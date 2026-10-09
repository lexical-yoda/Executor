"""Background polling and the status snapshot served to the page."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from .checks import Probe, http_probe, local_stats, ping, tcp_probe
from .config import Config, Machine, Service
from .history import MediaHistory
from .sources.aws import CloudWatchS3
from .sources.backups import evaluate_files
from .sources.beszel import Beszel, summarize, to_series
from .sources.duplicati import Duplicati
from .sources.edge import check_certificate, fetch_bandwidth
from .sources.media import MediaSources

log = logging.getLogger("executor.monitor")


class RunnerClient:
    """Talks to the runner over the internal network."""

    def __init__(self, base_url: str, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return await self._client.request(method, path, **kwargs)

    async def containers(self) -> list[dict]:
        response = await self._client.get("/containers")
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise RuntimeError(detail or f"runner answered HTTP {response.status_code}")
        return response.json()

    async def files(self) -> list[dict]:
        response = await self._client.get("/files")
        response.raise_for_status()
        return response.json()


def service_status(service: Service, probe: Probe | None,
                   containers: dict[str, dict] | None) -> tuple[str, list[dict]]:
    """Combine the HTTP/TCP probe with container states into one status."""
    states: list[dict] = []
    if containers is not None:
        for name in service.containers:
            states.append(containers.get(name) or {"name": name, "state": "missing",
                                                   "health": None, "status": "not found"})

    if probe is None and not states:
        return "unknown", states

    down = probe is not None and not probe.ok
    if states and states[0]["state"] != "running":
        down = True
    if down:
        return "down", states

    degraded = any(s["state"] != "running" or s.get("health") in {"unhealthy", "starting"}
                   for s in states)
    return ("degraded" if degraded else "up"), states


class Monitor:
    def __init__(self, config: Config, runner: RunnerClient | None,
                 beszel: Beszel | None = None, duplicati: Duplicati | None = None,
                 media: MediaSources | None = None, cloudwatch: CloudWatchS3 | None = None,
                 history: MediaHistory | None = None) -> None:
        self.config = config
        self.history = history
        self.watching: list[dict] = []
        self.watching_error: str | None = None
        self.watching_checked: float | None = None
        self.cloudwatch = cloudwatch
        self.storage: dict[str, dict] = {}
        self.storage_errors: dict[str, str] = {}
        self.media = media
        self.media_settings = config.integrations.media
        self.requests: dict | None = None
        self.requests_error: str | None = None
        self._requests_checked = float("-inf")  # so the first poll always fetches
        self.queue: list[dict] = []
        self.queue_errors: dict[str, str] = {}
        self.torrents: dict | None = None
        self.torrents_error: str | None = None
        self.runner = runner
        self.beszel = beszel
        self.duplicati = duplicati
        self.backup_settings = config.integrations.backups
        self.duplicati_status: dict | None = None
        self.duplicati_error: str | None = None if duplicati else "not configured"
        self.backup_files: list[dict] = []
        self.backups_checked: float | None = None
        # Beszel: system name -> system record, machine id -> summary / sparkline.
        self.beszel_systems: dict[str, dict] = {}
        self.machine_stats: dict[str, dict] = {}
        self.sparklines: dict[str, dict] = {}
        self.beszel_error: str | None = None if beszel else "not configured"
        self.edge_settings = config.integrations.edge
        self.bandwidth: dict | None = None
        self.bandwidth_error: str | None = None
        self.certificates: list[dict] = []
        self._certs_checked = 0.0
        self.probes: dict[str, Probe] = {}
        self.pings: dict[str, Probe] = {}
        self.last_seen: dict[str, str] = {}
        self.containers: dict[str, dict] | None = None
        self.runner_error: str | None = "not polled yet"
        self._tasks: list[asyncio.Task] = []
        self._http = httpx.AsyncClient(follow_redirects=False)
        self._http_insecure = httpx.AsyncClient(follow_redirects=False, verify=False)

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        settings = self.config.settings
        self._tasks = [
            asyncio.create_task(self._loop(self.check_services, settings.check_interval)),
            asyncio.create_task(self._loop(self.check_machines, settings.ping_interval)),
            asyncio.create_task(self._loop(self.poll_containers, settings.container_interval)),
        ]
        if self.edge_settings:
            self._tasks.append(asyncio.create_task(self._loop(self.poll_edge, self.edge_settings.interval)))
        if self.backup_settings:
            self._tasks.append(asyncio.create_task(self._loop(self.poll_backups, self.backup_settings.interval)))
            if self.cloudwatch and self.backup_settings.storage:
                self._tasks.append(asyncio.create_task(
                    self._loop(self.poll_storage, self.backup_settings.storage_interval)))
        if self.media and self.media_settings:
            self._tasks.append(asyncio.create_task(self._loop(self.poll_media, self.media_settings.interval)))
        if self.history:
            self._tasks += [
                asyncio.create_task(self._loop(self.poll_watching, 30)),
                asyncio.create_task(self._loop(self.poll_history, 300)),
            ]
        if self.beszel:
            self._tasks += [
                asyncio.create_task(self._loop(self.poll_beszel, settings.stats_interval)),
                asyncio.create_task(self._loop(self.poll_sparklines, 60)),
            ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._http.aclose()
        await self._http_insecure.aclose()
        if self.beszel:
            await self.beszel.close()
        if self.duplicati:
            await self.duplicati.close()
        if self.media:
            await self.media.close()
        if self.cloudwatch:
            await self.cloudwatch.close()

    @staticmethod
    async def _loop(func, interval: float) -> None:
        while True:
            try:
                await func()
            except Exception:  # noqa: BLE001 - a broken probe must not stop polling
                log.exception("poll failed: %s", func.__name__)
            await asyncio.sleep(interval)

    # --- polling -----------------------------------------------------------
    async def _probe(self, service: Service) -> None:
        check = service.check
        if check is None:
            return
        if check.type == "tcp":
            self.probes[service.id] = await tcp_probe(check)
        else:
            client = self._http if check.verify_tls else self._http_insecure
            self.probes[service.id] = await http_probe(check, client)

    async def check_services(self) -> None:
        await asyncio.gather(*(self._probe(s) for s in self.config.services))

    async def _ping(self, machine: Machine) -> None:
        if machine.local or not machine.address:
            return
        probe = await ping(machine.address)
        self.pings[machine.id] = probe
        if probe.ok:
            self.last_seen[machine.id] = probe.checked_at

    async def check_machines(self) -> None:
        await asyncio.gather(*(self._ping(m) for m in self.config.machines))

    async def poll_containers(self) -> None:
        if self.runner is None:
            self.runner_error = "runner not configured"
            return
        try:
            items = await self.runner.containers()
        except Exception as exc:  # noqa: BLE001
            self.containers = None
            self.runner_error = str(exc)[:160] or type(exc).__name__
            return
        self.containers = {c["name"]: c for c in items}
        self.runner_error = None

    async def poll_edge(self) -> None:
        settings = self.edge_settings
        assert settings is not None
        if settings.bandwidth_url:
            try:
                self.bandwidth = await fetch_bandwidth(settings.bandwidth_url, self._http)
                self.bandwidth_error = None
            except Exception as exc:  # noqa: BLE001
                self.bandwidth_error = str(exc)[:160] or type(exc).__name__
        # Certificates change rarely; check them hourly.
        if settings.certificates and time.monotonic() - self._certs_checked > 3600:
            self.certificates = list(await asyncio.gather(*(check_certificate(h) for h in settings.certificates)))
            self._certs_checked = time.monotonic()

    async def poll_backups(self) -> None:
        settings = self.backup_settings
        assert settings is not None
        if self.duplicati:
            try:
                self.duplicati_status = await self.duplicati.status()
                self.duplicati_error = None
            except Exception as exc:  # noqa: BLE001
                log.warning("duplicati status failed: %s", exc)
                self.duplicati_error = (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)[:160]
        if settings.files:
            folders: dict[str, dict] = {}
            if self.runner:
                try:
                    folders = {f["id"]: f for f in await self.runner.files()}
                except Exception as exc:  # noqa: BLE001
                    log.warning("runner folder report failed: %s", exc)
            self.backup_files = [evaluate_files(item, folders.get(item.folder)) for item in settings.files]
        self.backups_checked = time.time()

    @staticmethod
    def _reason(exc: Exception) -> str:
        return (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)[:160]

    async def poll_media(self) -> None:
        media, settings = self.media, self.media_settings
        assert media is not None and settings is not None
        if media.jellyseerr and time.monotonic() - self._requests_checked >= settings.requests_interval:
            try:
                self.requests = await media.jellyseerr.requests()
                self.requests_error = None
            except Exception as exc:  # noqa: BLE001
                log.warning("jellyseerr failed: %s", exc)
                self.requests_error = self._reason(exc)
            self._requests_checked = time.monotonic()
        queue: list[dict] = []
        errors: dict[str, str] = {}
        for arr in media.arrs:
            try:
                queue += await arr.queue()
            except Exception as exc:  # noqa: BLE001
                log.warning("%s queue failed: %s", arr.source, exc)
                errors[arr.source] = self._reason(exc)
        # Active downloads first, then by how far along they are.
        queue.sort(key=lambda q: (q["status"] != "downloading", -(q["progress"] or 0)))
        self.queue, self.queue_errors = queue, errors
        if media.qbittorrent:
            try:
                self.torrents = await media.qbittorrent.status()
                self.torrents_error = None
            except Exception as exc:  # noqa: BLE001
                log.warning("qbittorrent failed: %s", exc)
                self.torrents_error = self._reason(exc)

    def _media(self) -> dict | None:
        if not (self.media and self.media_settings):
            return None
        media = self.media
        return {
            "requests": {"configured": media.jellyseerr is not None, "ok": self.requests_error is None,
                         "error": self.requests_error, **(self.requests or {"counts": {}, "pending": [],
                                                                            "processing": []})},
            "downloads": {
                "sources": [a.source for a in media.arrs],
                "queue": self.queue,
                "errors": self.queue_errors,
                "torrents": {"configured": media.qbittorrent is not None, "ok": self.torrents_error is None,
                             "error": self.torrents_error, **(self.torrents or {})},
            },
        }

    async def poll_watching(self) -> None:
        assert self.history is not None
        try:
            self.watching = await self.history.sample()
            self.watching_error = None
        except Exception as exc:  # noqa: BLE001
            log.warning("jellyfin sessions failed: %s", exc)
            self.watching_error = self._reason(exc)
        self.watching_checked = time.time()

    async def poll_history(self) -> None:
        assert self.history is not None
        try:
            await self.history.maintain()
            self.history.error = None
        except Exception as exc:  # noqa: BLE001
            log.warning("history upkeep failed: %s", exc)
            self.history.error = self._reason(exc)

    def _jellyfin(self) -> dict | None:
        if not self.history:
            return None
        settings = self.config.integrations.jellyfin
        hub = settings.hub if settings else None
        origin = settings.origin if settings else None
        return {
            "ok": self.watching_error is None,
            "error": self.watching_error,
            "checked_at": self.watching_checked,
            "watching": self.watching,
            "history": self.history.status(),
            "hub": hub.model_dump() if hub else None,
            "origin": origin.model_dump() if origin else None,
        }

    async def poll_storage(self) -> None:
        assert self.cloudwatch is not None and self.backup_settings is not None
        for item in self.backup_settings.storage:
            try:
                self.storage[item.name] = await self.cloudwatch.bucket(
                    item.bucket, item.region, item.storage_types, item.price_per_gib_month)
                self.storage_errors.pop(item.name, None)
            except Exception as exc:  # noqa: BLE001
                log.warning("storage metrics for %s failed: %s", item.name, exc)
                self.storage_errors[item.name] = self._reason(exc)

    def _storage(self) -> list[dict]:
        assert self.backup_settings is not None
        jobs = {j["name"]: j for j in (self.duplicati_status or {}).get("jobs", [])}
        result = []
        for item in self.backup_settings.storage:
            job = jobs.get(item.duplicati_job or "")
            duplicati_bytes = job["target_bytes"] if job else None
            aws = self.storage.get(item.name)
            fallback_cost = (round(duplicati_bytes / 2**30 * item.price_per_gib_month, 2)
                             if duplicati_bytes is not None and item.price_per_gib_month is not None else None)
            result.append({
                "name": item.name,
                "bucket": item.bucket,
                "configured": self.cloudwatch is not None,
                "error": None if self.cloudwatch is None else self.storage_errors.get(item.name),
                "aws": aws,
                "duplicati_bytes": duplicati_bytes,
                "duplicati_versions": job["versions"] if job else None,
                "fallback_cost": fallback_cost,
            })
        return result

    def _backups(self) -> dict | None:
        if not self.backup_settings:
            return None
        return {
            "storage": self._storage(),
            "checked_at": self.backups_checked,
            "duplicati": {"configured": self.duplicati is not None, "ok": self.duplicati_error is None,
                          "error": self.duplicati_error, **(self.duplicati_status or {"jobs": [], "paused": False})},
            "files": self.backup_files,
        }

    def _edge(self) -> dict | None:
        if not self.edge_settings:
            return None
        return {"bandwidth": self.bandwidth, "bandwidth_error": self.bandwidth_error,
                "certificates": self.certificates}

    def _beszel_machines(self) -> list[tuple[Machine, dict]]:
        """Configured machines that exist in Beszel, with their system record."""
        return [(m, self.beszel_systems[m.beszel]) for m in self.config.machines
                if m.beszel and m.beszel in self.beszel_systems]

    async def poll_beszel(self) -> None:
        assert self.beszel is not None
        try:
            self.beszel_systems = await self.beszel.systems()
            latest = await self.beszel.latest()
        except Exception as exc:  # noqa: BLE001
            self.beszel_error = str(exc)[:160] or type(exc).__name__
            return
        self.beszel_error = None
        stats: dict[str, dict] = {}
        for machine, system in self._beszel_machines():
            record = latest.get(system["id"])
            stats[machine.id] = {**summarize(system, record["stats"] if record else None),
                                 "updated": record["created"] if record else None}
        self.machine_stats = stats

    async def poll_sparklines(self) -> None:
        assert self.beszel is not None
        if not self.beszel_systems:
            # Both loops start together; make sure the system list exists first.
            self.beszel_systems = await self.beszel.systems()
        lines: dict[str, dict] = {}
        for machine, system in self._beszel_machines():
            try:
                series = to_series(await self.beszel.records(system["id"], "1m", 3600))
            except Exception as exc:  # noqa: BLE001
                log.warning("sparkline for %s failed: %s", machine.id, exc)
                continue
            lines[machine.id] = {"cpu": series["cpu"], "mem": series["mem"]}
        self.sparklines = lines

    async def history(self, machine_id: str, range_name: str) -> dict | None:
        machine = next((m for m in self.config.machines if m.id == machine_id), None)
        if not self.beszel or not machine or not machine.beszel:
            return None
        system = self.beszel_systems.get(machine.beszel)
        if not system:
            return None
        return await self.beszel.series(system["id"], range_name)

    # --- snapshot ----------------------------------------------------------
    def _machine(self, machine: Machine) -> dict:
        base = {"id": machine.id, "name": machine.name, "role": machine.role,
                "address": machine.address, "icon": machine.icon, "details": None,
                "monitored": bool(machine.beszel and self.beszel),
                "stats": self.machine_stats.get(machine.id),
                "spark": self.sparklines.get(machine.id)}
        if machine.local:
            return {**base, "status": "up", "latency_ms": None, "error": None,
                    "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "details": local_stats()}
        probe = self.pings.get(machine.id)
        if probe is None:
            return {**base, "status": "unknown", "latency_ms": None, "error": None,
                    "last_seen": None}
        return {**base, "status": "up" if probe.ok else "down", "latency_ms": probe.latency_ms,
                "error": probe.error, "last_seen": self.last_seen.get(machine.id)}

    def _service(self, service: Service) -> dict:
        probe = self.probes.get(service.id)
        status, states = service_status(service, probe, self.containers)
        return {
            "id": service.id,
            "name": service.name,
            "group": service.group,
            "url": service.url,
            "description": service.description,
            "status": status,
            "latency_ms": probe.latency_ms if probe else None,
            "http_status": probe.http_status if probe else None,
            "error": probe.error if probe else None,
            "checked_at": probe.checked_at if probe else None,
            "containers": states,
        }

    def snapshot(self) -> dict:
        machines = [self._machine(m) for m in self.config.machines]
        services = [self._service(s) for s in self.config.services]
        containers = list(self.containers.values()) if self.containers is not None else []
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "site": self.config.site.model_dump(),
            "groups": self.config.ordered_groups,
            "machines": machines,
            "services": services,
            "summary": {
                "services_up": sum(s["status"] == "up" for s in services),
                "services_total": len(services),
                "machines_up": sum(m["status"] == "up" for m in machines),
                "machines_total": len(machines),
                "containers_running": sum(c["state"] == "running" for c in containers),
                "containers_total": len(containers),
                "containers_unhealthy": sum(c.get("health") == "unhealthy" for c in containers),
                "containers_known": self.containers is not None,
            },
            "runner": {"ok": self.runner_error is None, "error": self.runner_error},
            "beszel": {"configured": self.beszel is not None, "ok": self.beszel_error is None,
                       "error": self.beszel_error},
            "edge": self._edge(),
            "backups": self._backups(),
            "media": self._media(),
            "jellyfin": self._jellyfin(),
        }
