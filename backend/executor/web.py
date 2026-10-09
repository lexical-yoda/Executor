"""The web process: the page, the status API, and a narrow proxy to the runner."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Config
from .monitor import Monitor, RunnerClient
from .security import Guard
from .sources.beszel import RANGES, Beszel
from .sources.duplicati import Duplicati
from .sources.jellyfin import Jellyfin
from .sources.media import MediaSources

log = logging.getLogger("executor.web")


def create_web_app(config: Config, runner: RunnerClient | None, static_dir: Path | None,
                   start_monitor: bool = True, beszel: Beszel | None = None,
                   jellyfin: Jellyfin | None = None, duplicati: Duplicati | None = None,
                   media: MediaSources | None = None) -> FastAPI:
    monitor = Monitor(config, runner, beszel, duplicati, media)
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
        if range not in RANGES:
            raise HTTPException(400, "Unknown range.")
        key = (machine_id, range)
        cached = history_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < 60:
            return cached[1]
        try:
            series = await monitor.history(machine_id, range)
        except Exception as exc:  # noqa: BLE001
            log.warning("history for %s failed: %s", machine_id, exc)
            raise HTTPException(502, "Stats source unavailable.") from None
        if series is None:
            raise HTTPException(404, "No stats for this machine.")
        body = {"machine": machine_id, "range": range, **series}
        history_cache[key] = (now, body)
        return body

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
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.add_middleware(Guard, allowed_clients=config.security.allowed_clients,
                       allowed_hosts=config.security.allowed_hosts,
                       denied_clients=config.security.denied_clients)
    return app
