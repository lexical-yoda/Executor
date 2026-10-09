"""Background polling and the status snapshot served to the page."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .checks import Probe, http_probe, local_stats, ping, tcp_probe
from .config import Config, Machine, Service
from .sources.beszel import Beszel, summarize, to_series

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
                 beszel: Beszel | None = None) -> None:
        self.config = config
        self.runner = runner
        self.beszel = beszel
        # Beszel: system name -> system record, machine id -> summary / sparkline.
        self.beszel_systems: dict[str, dict] = {}
        self.machine_stats: dict[str, dict] = {}
        self.sparklines: dict[str, dict] = {}
        self.beszel_error: str | None = None if beszel else "not configured"
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
        }
