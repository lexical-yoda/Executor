"""The web process: the page, the status API, and a narrow proxy to the runner."""

from __future__ import annotations

import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from .config import Config
from .history import MediaHistory
from .monitor import LONG_RANGES, Monitor, RunnerClient
from .recap import build_recap
from .security import Guard
from .sources.aws import CloudWatchS3
from .sources.beszel import RANGES, Beszel
from .sources.duplicati import Duplicati
from .sources.immich import Immich
from .sources.pihole import PiHole
from .sources.truenas import TrueNAS
from .sources.jellyfin import Jellyfin
from .sources.media import MediaSources
from .store import Store

log = logging.getLogger("executor.web")


class Changes(BaseModel):
    """Some fields of a service's placement; fields left out stay as they are."""
    name: str | None = Field(default=None, max_length=60)
    group: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=300)
    hidden: bool | None = None

    model_config = {"extra": "forbid"}

    @field_validator("name", "group", "url", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        if isinstance(value, str):
            value = " ".join(value.split())
            return value or None
        return value

    @field_validator("url")
    @classmethod
    def _web_link(cls, value: str | None) -> str | None:
        if value is not None and not value.lower().startswith(("http://", "https://")):
            raise ValueError("the link must start with http:// or https://")
        return value


class Bulk(BaseModel):
    """The same change, or a reset, for several services at once."""
    ids: list[str] = Field(min_length=1, max_length=200)
    changes: Changes | None = None
    reset: bool = False

    model_config = {"extra": "forbid"}


class GroupOrder(BaseModel):
    order: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = Field(max_length=100)

    model_config = {"extra": "forbid"}


class GroupRename(BaseModel):
    old: str = Field(min_length=1, max_length=40)
    new: str = Field(min_length=1, max_length=40)

    model_config = {"extra": "forbid"}

    @field_validator("new", mode="before")
    @classmethod
    def _tidy(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value


class Placement(BaseModel):
    """A service's name, group, link and visibility as set from the page.
    Empty fields fall back to the config or Docker."""
    name: str | None = Field(default=None, max_length=60)
    group: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=300)
    hidden: bool = False

    model_config = {"extra": "forbid"}

    @field_validator("name", "group", "url", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        if isinstance(value, str):
            value = " ".join(value.split())
            return value or None
        return value

    @field_validator("url")
    @classmethod
    def _web_link(cls, value: str | None) -> str | None:
        # Only web links: the page opens it in a new tab.
        if value is not None and not value.lower().startswith(("http://", "https://")):
            raise ValueError("the link must start with http:// or https://")
        return value

# The map's worker is an ES module and its fonts are protobuf; older Pythons know neither.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/x-protobuf", ".pbf")


def create_web_app(config: Config, runner: RunnerClient | None, static_dir: Path | None,
                   start_monitor: bool = True, beszel: Beszel | None = None,
                   jellyfin: Jellyfin | None = None, duplicati: Duplicati | None = None,
                   media: MediaSources | None = None, cloudwatch: CloudWatchS3 | None = None,
                   history: MediaHistory | None = None, store: Store | None = None,
                   tiles_dir: Path | None = None, immich: Immich | None = None,
                   truenas: TrueNAS | None = None, pihole: PiHole | None = None) -> FastAPI:
    store = store or (history.store if history else None)
    monitor = Monitor(config, runner, beszel, duplicati, media, cloudwatch, history, store, immich, jellyfin,
                      truenas, pihole)
    tile_name = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
    recap_cache: dict[int, tuple[float, dict]] = {}
    user_id_pattern = re.compile(r"^[0-9a-f]{32}$")
    posters: dict[tuple[str, int], tuple[bytes, str]] = {}
    history_cache: dict[tuple[str, str], tuple[float, dict]] = {}
    streams_cache: list = []  # [(monotonic time, body)]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_monitor:
            monitor.start()
        yield
        await monitor.stop()
        if runner:
            await runner.close()
        if jellyfin:
            await jellyfin.close()
        if store:
            store.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.monitor = monitor

    async def relay(method: str, path: str, **kwargs) -> JSONResponse:
        if runner is None:
            raise HTTPException(503, "Runner not configured.")
        try:
            response = await runner.request(method, path, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("runner unreachable: %s", exc)
            raise HTTPException(503, "Runner unreachable.") from None
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:200]}
        return JSONResponse(body, status_code=response.status_code)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    async def status() -> dict:
        return monitor.snapshot()

    @app.get("/api/machines/{machine_id}/history")
    async def machine_history(machine_id: str, range: str = "24h") -> dict:  # noqa: A002
        if range not in RANGES and range not in LONG_RANGES:
            raise HTTPException(400, "Unknown range.")
        key = (machine_id, range)
        cached = history_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < 60:
            return cached[1]
        try:
            series = await monitor.machine_history(machine_id, range)
        except Exception as exc:  # noqa: BLE001
            log.warning("history for %s failed: %s", machine_id, exc)
            raise HTTPException(502, "Stats source unavailable.") from None
        if series is None:
            raise HTTPException(404, "No stats for this machine.")
        body = {"machine": machine_id, "range": range, **series}
        history_cache[key] = (now, body)
        return body

    def ledger() -> Store:
        if store is None:
            raise HTTPException(404, "Executor has no data folder, so it keeps no history.")
        return store

    @app.get("/api/events")
    async def events(limit: int = 50, before: int | None = None) -> dict:
        return {"events": monitor.tracker.recent(max(1, min(limit, 200)), before)}

    @app.get("/api/services/{service_id}/history")
    async def service_history(service_id: str, hours: int = 24) -> dict:
        if service_id not in monitor.service_ids():
            raise HTTPException(404, "No such service.")
        hours = max(1, min(hours, 720))
        bucket = 300 if hours <= 24 else 3600 if hours <= 168 else 6 * 3600
        since = time.time() - hours * 3600
        buckets = ledger().check_history(service_id, since, bucket)
        ok = sum(b["up"] + b["degraded"] for b in buckets)
        total = ok + sum(b["down"] for b in buckets)
        return {"service": service_id, "hours": hours, "bucket_s": bucket, "buckets": buckets,
                "uptime": round(ok / total * 100, 3) if total else None}

    @app.put("/api/services/{service_id}/override")
    async def place_service(service_id: str, body: Placement) -> dict:
        ledger()
        if service_id not in monitor.service_ids():
            raise HTTPException(404, "No such service.")
        monitor.set_override(service_id, name=body.name, group=body.group, url=body.url, hidden=body.hidden)
        return {"ok": True}

    @app.delete("/api/services/{service_id}/override")
    async def reset_service(service_id: str) -> dict:
        ledger()
        monitor.clear_override(service_id)
        return {"ok": True}

    @app.get("/api/settings/services")
    async def settings_services() -> dict:
        ledger()
        groups = monitor.groups()
        # Config groups with no service in them yet are still offered as places to move to.
        groups += [g for g in config.ordered_groups if g not in groups]
        return {"services": monitor.settings_services(), "groups": groups,
                "default_group": config.discovery.group if config.discovery else None}

    @app.post("/api/settings/services")
    async def bulk_services(body: Bulk) -> dict:
        ledger()
        known = monitor.service_ids()
        unknown = [i for i in body.ids if i not in known]
        if unknown:
            raise HTTPException(404, f"No such service: {unknown[0]}.")
        changes = body.changes.model_dump(exclude_unset=True) if body.changes else {}
        for service_id in dict.fromkeys(body.ids):
            if body.reset:
                monitor.clear_override(service_id)
            elif changes:
                monitor.update_override(service_id, changes)
        return {"ok": True, "count": len(set(body.ids))}

    @app.put("/api/settings/groups")
    async def group_order(body: GroupOrder) -> dict:
        ledger()
        monitor.set_group_order(body.order)
        return {"ok": True, "groups": monitor.groups()}

    @app.post("/api/settings/groups/rename")
    async def group_rename(body: GroupRename) -> dict:
        ledger()
        moved = monitor.rename_group(body.old, body.new)
        if not moved:
            raise HTTPException(404, "No service is in that group.")
        return {"ok": True, "moved": moved, "groups": monitor.groups()}

    @app.get("/api/recap")
    async def recap(days: int = 7) -> dict:
        days = max(1, min(days, 90))
        cached = recap_cache.get(days)
        now = time.monotonic()
        if cached and now - cached[0] < 120:
            return cached[1]
        backups = monitor.snapshot().get("backups") or {}
        body = build_recap(ledger(), days=days, storage=backups.get("storage"), photos=monitor.photos_recap(days))
        recap_cache[days] = (now, body)
        return body

    @app.get("/api/photos/history")
    async def photo_history() -> dict:
        """A year of photo library activity and its size over time."""
        if not monitor.immich_settings:
            raise HTTPException(404, "Immich is not configured.")
        return monitor.photo_history()

    @app.get("/api/map/tilesets")
    async def tilesets() -> dict:
        """Map tile archives present, the world one first."""
        found = []
        if tiles_dir and tiles_dir.is_dir():
            for file in sorted(tiles_dir.glob("*.pmtiles")):
                if tile_name.match(file.stem) and file.is_file():
                    found.append({"name": file.stem, "url": f"/tiles/{file.name}", "bytes": file.stat().st_size})
        found.sort(key=lambda t: (t["name"] != "world", t["name"]))
        return {"tilesets": found}

    @app.api_route("/tiles/{name}.pmtiles", methods=["GET", "HEAD"], include_in_schema=False)
    async def tiles(name: str) -> FileResponse:
        """One map archive; the map reads it piece by piece with range requests."""
        if not tiles_dir or not tile_name.match(name):
            raise HTTPException(404)
        file = tiles_dir / f"{name}.pmtiles"
        if not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, media_type="application/octet-stream",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/streams")
    async def streams() -> dict:
        """Active media streams, for the confirm dialog of disruptive actions."""
        if jellyfin is None:
            return {"configured": False, "ok": False, "error": None, "streams": []}
        now = time.monotonic()
        if streams_cache and now - streams_cache[0][0] < 10:
            return streams_cache[0][1]
        try:
            body = {"configured": True, "ok": True, "error": None, "streams": await jellyfin.streams()}
        except Exception as exc:  # noqa: BLE001
            log.warning("jellyfin sessions failed: %s", exc)
            message = str(exc) if isinstance(exc, RuntimeError) else exc.__class__.__name__
            body = {"configured": True, "ok": False, "error": message, "streams": []}
        streams_cache[:] = [(now, body)]
        return body

    def history_store():
        if history is None or history.store is None:
            raise HTTPException(404, "Location history is not enabled.")
        return history.store

    def since(days: int) -> float:
        keep = history.keep_days if history else 90
        return time.time() - max(1, min(days, keep)) * 86400

    def user_param(user: str | None) -> str | None:
        if user is None or user == "":
            return None
        if not user_id_pattern.match(user):
            raise HTTPException(400, "Unknown user id.")
        return user

    @app.get("/api/media/users")
    async def media_users(days: int = 90) -> dict:
        return {"users": history_store().users(since(days))}

    @app.get("/api/media/places")
    async def media_places(days: int = 90, user: str | None = None) -> dict:
        return history_store().places(since(days), user_param(user))

    @app.get("/api/media/trail")
    async def media_trail(user: str, days: int = 90) -> dict:
        user_id = user_param(user)
        assert user_id is not None
        return {"user": user_id, "sightings": history_store().trail(user_id, since(days))}

    @app.get("/api/media/poster/{kind}/{tmdb_id}")
    async def poster(kind: str, tmdb_id: int) -> Response:
        """Posters of requested titles, proxied because the page loads nothing
        from other origins. Only titles the server has already seen in its own
        request list are served, so the page cannot make it fetch anything else."""
        if kind not in ("movie", "tv") or media is None or media.jellyseerr is None:
            raise HTTPException(404)
        key = (kind, tmdb_id)
        if key not in posters:
            try:
                image = await media.jellyseerr.poster(kind, tmdb_id)
            except Exception:  # noqa: BLE001
                image = None
            if image is None:
                raise HTTPException(404)
            if len(posters) >= 64:
                posters.pop(next(iter(posters)))
            posters[key] = image
        content, content_type = posters[key]
        return Response(content, media_type=content_type,
                        headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/media/library/history")
    async def library_history() -> dict:
        if not monitor.jellyfin:
            raise HTTPException(404, "Jellyfin is not configured.")
        return monitor.library_history()

    library_posters: dict[str, tuple[bytes, str]] = {}

    @app.get("/api/media/library/poster/{item_id}")
    async def library_poster(item_id: str) -> Response:
        """Posters of recent additions, proxied like request posters: only items
        in the server's own list of recent additions are served."""
        if not user_id_pattern.match(item_id) or not monitor.jellyfin or item_id not in monitor.library_poster_ids():
            raise HTTPException(404)
        if item_id not in library_posters:
            try:
                image = await monitor.jellyfin.poster(item_id)
            except Exception:  # noqa: BLE001
                image = None
            if image is None:
                raise HTTPException(404)
            if len(library_posters) >= 64:
                library_posters.pop(next(iter(library_posters)))
            library_posters[item_id] = image
        content, content_type = library_posters[item_id]
        return Response(content, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/actions")
    async def actions() -> JSONResponse:
        return await relay("GET", "/actions")

    @app.post("/api/actions/{action_id}/run")
    async def run_action(action_id: str, request: Request) -> JSONResponse:
        client = request.client.host if request.client else "unknown"
        return await relay("POST", "/runs", json={"action": action_id, "requested_by": client})

    @app.get("/api/runs")
    async def runs() -> JSONResponse:
        return await relay("GET", "/runs")

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str, offset: int = 0) -> JSONResponse:
        return await relay("GET", f"/runs/{run_id}", params={"offset": offset})

    if static_dir and static_dir.is_dir():
        index = static_dir / "index.html"
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def page(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate, headers={"Cache-Control": "private, max-age=86400"}
                                    if path.startswith("map/") else None)
            # Map fonts and icons: a missing one must not come back as the page.
            if path.startswith(("map/", "tiles/")):
                raise HTTPException(404)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.add_middleware(Guard, allowed_clients=config.security.allowed_clients,
                       allowed_hosts=config.security.allowed_hosts,
                       denied_clients=config.security.denied_clients)
    return app
