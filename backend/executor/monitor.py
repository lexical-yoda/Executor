"""Background polling and the status snapshot served to the page."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from .checks import Probe, http_probe, local_stats, ping, tcp_probe
from .config import Config, Machine, Service
from .discovery import Plan, plan, remember
from .events import Tracker
from .history import MediaHistory
from .sources.aws import CloudWatchS3
from .sources.backups import evaluate_files
from .sources.beszel import Beszel, summarize, to_series
from .sources.duplicati import Duplicati
from .sources.edge import check_certificate, fetch_bandwidth
from .sources.media import MediaSources
from .store import Store

log = logging.getLogger("executor.monitor")

# Chart ranges longer than the stats source keeps, served from Executor's own
# hourly averages: range -> (seconds, bucket seconds).
LONG_RANGES: dict[str, tuple[int, int]] = {"90d": (90 * 86400, 6 * 3600), "1y": (365 * 86400, 86400)}


def hourly_sample(stats: dict) -> dict:
    """The figures from a machine summary that hourly history keeps."""
    gpu = (stats.get("gpus") or [{}])[0]
    values = {"cpu": stats.get("cpu_pct"), "mem": stats.get("mem_pct"), "disk": stats.get("disk_pct"),
              "net_tx": stats.get("net_tx_bps"), "net_rx": stats.get("net_rx_bps"), "cpu_temp": stats.get("cpu_temp"),
              "gpu": gpu.get("util_pct"), "gpu_temp": stats.get("gpu_temp"),
              "pools": {p["name"]: p["pct"] for p in stats.get("pools") or [] if p.get("pct") is not None}}
    return {k: v for k, v in values.items() if v is not None}


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

    async def stacks(self) -> list[str] | None:
        """Stack folder names, or None when the runner has no stacks folder (or cannot read it)."""
        response = await self._client.get("/stacks")
        response.raise_for_status()
        body = response.json()
        return body["stacks"] if body.get("configured") and body.get("ok") else None


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
                 history: MediaHistory | None = None, store: Store | None = None) -> None:
        self.config = config
        self.history = history
        self.store = store
        self.tracker = Tracker(store)
        self._download_bytes: float | None = None
        self._backfilled: set[str] = set()
        # Stack discovery: folder names from the runner, each container's stack
        # (remembered, since a removed container no longer says) and the plan.
        self.stack_dirs: list[str] | None = None
        self._stacks_checked = float("-inf")
        self.stack_memory: dict[str, list[str]] = {}
        if store:
            try:
                self.stack_memory = json.loads(store.get_state("container_stacks") or "{}")
            except ValueError:
                self.stack_memory = {}
        self.plan: Plan = plan(config.services, None, None, self.stack_memory, config.discovery)
        self._plan_seen: set[str] | None = None
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
        if self.runner:
            self._tasks.append(asyncio.create_task(self._loop(self.poll_runs, 10)))
        if self.store:
            self._tasks.append(asyncio.create_task(self._loop(self.upkeep, 6 * 3600)))
            self.tracker.emit("system", "info", "Executor came online")

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

    # --- services: configured plus discovered ---------------------------------
    def services(self) -> list[Service]:
        return [s for s, _ in self.plan.configured] + [s for s, _, _ in self.plan.discovered]

    def service_ids(self) -> set[str]:
        return {s.id for s in self.services()}

    def _stack_info(self, service: Service) -> tuple[bool, str | None, bool]:
        """(discovered, stack name, stopped) for one service in the current plan."""
        for s, stack, stopped in self.plan.discovered:
            if s.id == service.id:
                return True, stack, stopped
        for s, state in self.plan.configured:
            if s.id == service.id:
                return False, None, state == "stopped"
        return False, None, False

    def _replan(self) -> None:
        """Recompute which services show, and log stacks that appear or go."""
        previous = {s.id: s.name for s in self.services()} | {i: i for i in self.plan.removed}
        self.plan = plan(self.config.services, self.containers, self.stack_dirs, self.stack_memory,
                         self.config.discovery)
        if self.plan.learned and self.store:
            self._safely(self.store.set_state, "container_stacks", json.dumps(self.stack_memory))
        current = {s.id: s.name for s in self.services()}
        if self._plan_seen is None:
            # The first full picture is the baseline.
            self._plan_seen = set(current)
            return
        names = {s.id: s.name for s in self.config.services}
        for service_id in sorted(set(current) - self._plan_seen):
            discovered = any(s.id == service_id for s, _, _ in self.plan.discovered)
            if discovered:
                self.tracker.emit("service", "info", f"New stack found: {current[service_id]}",
                                  "Discovered from Docker; add it to config.yaml to name, group or check it",
                                  ref=f"service:{service_id}")
        for service_id in sorted(self._plan_seen - set(current)):
            name = names.get(service_id) or previous.get(service_id) or service_id
            self.tracker.emit("service", "info", f"{name} was removed",
                              "Its containers and stack folder are gone", ref=None)
        self._plan_seen = set(current)

    async def check_services(self) -> None:
        current = self.services()
        await asyncio.gather(*(self._probe(s) for s in current))
        services = [self._service(s) for s in current]
        self.tracker.services(services)
        if self.store:
            self._safely(self.store.record_checks, [(s["id"], s["status"], s["latency_ms"]) for s in services])

    def _safely(self, func, *args) -> None:
        """Write to the store without letting a database problem stop a poll."""
        try:
            func(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("store write failed (%s): %s", getattr(func, "__name__", "?"), exc)

    async def upkeep(self) -> None:
        assert self.store is not None
        self._safely(self.store.purge_ledger)

    async def poll_runs(self) -> None:
        assert self.runner is not None
        try:
            response = await self.runner.request("GET", "/runs")
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - the containers poll already reports an unreachable runner
            return
        self.tracker.runs(response.json().get("runs", []))

    async def _ping(self, machine: Machine) -> None:
        if machine.local or not machine.address:
            return
        probe = await ping(machine.address)
        self.pings[machine.id] = probe
        if probe.ok:
            self.last_seen[machine.id] = probe.checked_at

    async def check_machines(self) -> None:
        await asyncio.gather(*(self._ping(m) for m in self.config.machines))
        self.tracker.machines([self._machine(m) for m in self.config.machines])

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
        if remember(self.stack_memory, self.containers) and self.store:
            self._safely(self.store.set_state, "container_stacks", json.dumps(self.stack_memory))
        if self.config.discovery and time.monotonic() - self._stacks_checked > 60:
            self._stacks_checked = time.monotonic()
            try:
                self.stack_dirs = await self.runner.stacks()
            except Exception as exc:  # noqa: BLE001 - discovery is a nicety; keep the last answer
                log.warning("runner stacks report failed: %s", exc)
        self._replan()
        self.tracker.containers(self.containers, {name for s in self.services() for name in s.containers})

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
            self.tracker.certificates(self.certificates)

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
        if self.duplicati_error is None:
            self.tracker.backups((self.duplicati_status or {}).get("jobs", []), self.backup_files)

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
                self.tracker.requests(self.requests.get("pending", []), self.requests.get("processing", []))
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
        if not errors:
            # With a source down its items vanish from the queue; that is not a finished download.
            self.tracker.downloads(queue)
        if media.qbittorrent:
            try:
                self.torrents = await media.qbittorrent.status()
                self.torrents_error = None
                self._count_download(self.torrents.get("down_session_bytes"))
            except Exception as exc:  # noqa: BLE001
                log.warning("qbittorrent failed: %s", exc)
                self.torrents_error = self._reason(exc)

    def _count_download(self, session_bytes: float | None) -> None:
        """Add what qBittorrent downloaded since the last poll to today's total."""
        if session_bytes is None or not self.store:
            return
        previous, self._download_bytes = self._download_bytes, session_bytes
        if previous is None:
            return
        # A smaller figure means qBittorrent restarted and counts from zero again.
        grown = session_bytes - previous if session_bytes >= previous else session_bytes
        if grown > 0:
            self._safely(self.store.add_daily, time.strftime("%Y-%m-%d"), "download_bytes", grown)

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
            self.tracker.streams(self.watching)
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
            if record and self.store:
                self._safely(self.store.add_machine_sample, machine.id, hourly_sample(stats[machine.id]))
                if machine.id not in self._backfilled:
                    self._backfilled.add(machine.id)
                    await self._backfill(machine.id, system["id"])
        self.machine_stats = stats

    async def _backfill(self, machine_id: str, system_id: str) -> None:
        """Seed the hourly history from what the stats source still keeps (about a month)."""
        assert self.beszel is not None and self.store is not None
        if self.store.get_state(f"backfilled:{machine_id}"):
            return
        points: list[tuple[float, dict]] = []
        try:
            for record_type, seconds in (("120m", 7 * 86400), ("480m", 30 * 86400)):
                series = to_series(await self.beszel.records(system_id, record_type, seconds))
                for i, t in enumerate(series["t"]):
                    values = {k: (series[k] or [None] * len(series["t"]))[i]
                              for k in ("cpu", "mem", "disk", "net_tx", "net_rx", "cpu_temp", "gpu", "gpu_temp")}
                    values["pools"] = {name: v[i] for name, v in series["pools"].items() if v[i] is not None}
                    points.append((t, {k: v for k, v in values.items() if v is not None}))
        except Exception as exc:  # noqa: BLE001
            log.warning("history backfill for %s failed: %s", machine_id, exc)
            self._backfilled.discard(machine_id)
            return
        added = self.store.backfill_machine(machine_id, points)
        self.store.set_state(f"backfilled:{machine_id}", "1")
        log.info("seeded %d hours of history for %s", added, machine_id)

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

    async def machine_history(self, machine_id: str, range_name: str) -> dict | None:
        machine = next((m for m in self.config.machines if m.id == machine_id), None)
        if range_name in LONG_RANGES:
            if not machine or not self.store:
                return None
            seconds, bucket = LONG_RANGES[range_name]
            return self.store.machine_series(machine_id, time.time() - seconds, bucket)
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
        discovered, stack, stopped = self._stack_info(service)
        error = probe.error if probe else None
        if stopped:
            # Deliberately down: the stack's containers are gone but its folder is still there.
            status, error = "unknown", "Stack stopped"
        return {
            "id": service.id,
            "name": service.name,
            "group": service.group,
            "url": service.url,
            "description": service.description,
            "status": status,
            "latency_ms": probe.latency_ms if probe else None,
            "http_status": probe.http_status if probe else None,
            "error": error,
            "checked_at": probe.checked_at if probe else None,
            "containers": states,
            "discovered": discovered,
            "stack": stack,
        }

    def snapshot(self) -> dict:
        machines = [self._machine(m) for m in self.config.machines]
        current = self.services()
        services = [self._service(s) for s in current]
        containers = list(self.containers.values()) if self.containers is not None else []
        groups = list(self.config.ordered_groups)
        for service in current:
            if service.group not in groups:
                groups.append(service.group)
        if self.config.discovery and self.config.discovery.group in groups:
            # Stacks nobody has placed yet come last.
            groups.remove(self.config.discovery.group)
            groups.append(self.config.discovery.group)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "site": self.config.site.model_dump(),
            "groups": groups,
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
            "ledger": self.store is not None,
            "edge": self._edge(),
            "backups": self._backups(),
            "media": self._media(),
            "jellyfin": self._jellyfin(),
        }
