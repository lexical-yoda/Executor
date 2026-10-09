"""Read-only client for a Beszel hub (PocketBase API).

Executor logs in as a Beszel user with the ``readonly`` role. Beszel already
keeps each machine's history, so Executor only reads it: current values from
the newest one-minute record, charts from the record type that matches the
requested range.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

# Range name -> (Beszel record type, seconds of history). Beszel keeps each
# type only for a limited time, roughly matching these windows.
RANGES: dict[str, tuple[str, int]] = {
    "1h": ("1m", 3600),
    "12h": ("10m", 12 * 3600),
    "24h": ("20m", 24 * 3600),
    "7d": ("120m", 7 * 86400),
    "30d": ("480m", 30 * 86400),
}

_ID = re.compile(r"^[a-z0-9]+$")
# Sensor names that usually carry the CPU package temperature, in order of preference.
_CPU_SENSORS = ("k10temp_tctl", "coretemp_package_id_0", "cpu_thermal", "k10temp_tccd1")


class BeszelError(Exception):
    pass


def _cutoff(seconds: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _epoch(created: str) -> int:
    return int(datetime.fromisoformat(created.replace(" ", "T").replace("Z", "+00:00")).timestamp())


def cpu_temperature(temps: dict[str, float] | None) -> float | None:
    if not temps:
        return None
    for name in _CPU_SENSORS:
        if name in temps:
            return temps[name]
    return None


def _physical_nics(stats: dict) -> dict[str, list]:
    """Network interfaces minus tunnels, whose traffic also crosses the physical NIC."""
    return {name: v for name, v in (stats.get("ni") or {}).items() if not name.startswith(("wg", "tun"))}


def summarize(system: dict, stats: dict | None) -> dict:
    """Current values for one machine card."""
    info = system.get("info") or {}
    s = stats or {}
    gpus = [
        {"name": g.get("n"), "util_pct": g.get("u"), "mem_used_mb": g.get("mu"),
         "mem_total_mb": g.get("mt"), "power_w": g.get("p")}
        for g in (s.get("g") or {}).values()
    ]
    pools = [
        {"name": name, "used_gib": round(p.get("du", 0), 1), "total_gib": round(p.get("d", 0), 1),
         "pct": round(p["du"] / p["d"] * 100, 1) if p.get("d") else None, "health": p.get("h")}
        for name, p in sorted((s.get("z") or {}).items())
    ]
    temps = s.get("t") or {}
    drive_temps = [v for k, v in temps.items() if k.startswith(("drivetemp", "nvme"))]
    nics = _physical_nics(s)
    return {
        "state": system.get("status"),
        "threads": info.get("t"),
        "cpu_pct": info.get("cpu"),
        "mem_pct": s.get("mp", info.get("mp")),
        "mem_used_gb": s.get("mu"),
        "mem_total_gb": s.get("m"),
        "arc_gb": s.get("mz") or None,
        "disk_pct": s.get("dp", info.get("dp")),
        "disk_used_gb": s.get("du"),
        "disk_total_gb": s.get("d"),
        "load": info.get("la"),
        "uptime_s": info.get("u"),
        "cpu_temp": cpu_temperature(temps) or info.get("dt") or None,
        "gpu_temp": temps.get(gpus[0]["name"]) if gpus and gpus[0]["name"] else None,
        "drive_temp_max": max(drive_temps) if drive_temps else None,
        "net_tx_bps": sum(v[0] for v in nics.values()) if nics else None,
        "net_rx_bps": sum(v[1] for v in nics.values()) if nics else None,
        "gpus": gpus,
        "pools": pools,
    }


def to_series(items: list[dict]) -> dict:
    """Column arrays for charts, one entry per record, oldest first."""
    out: dict = {"t": [], "cpu": [], "mem": [], "disk": [], "net_tx": [], "net_rx": [],
                 "cpu_temp": [], "gpu": [], "gpu_temp": [], "pools": {}}
    pool_names = sorted({name for item in items for name in (item.get("stats", {}).get("z") or {})})
    for name in pool_names:
        out["pools"][name] = []
    for item in items:
        s = item.get("stats") or {}
        out["t"].append(_epoch(item["created"]))
        out["cpu"].append(s.get("cpu"))
        out["mem"].append(s.get("mp"))
        out["disk"].append(s.get("dp"))
        nics = _physical_nics(s)
        out["net_tx"].append(sum(v[0] for v in nics.values()) if nics else None)
        out["net_rx"].append(sum(v[1] for v in nics.values()) if nics else None)
        temps = s.get("t") or {}
        out["cpu_temp"].append(cpu_temperature(temps))
        gpu = next(iter((s.get("g") or {}).values()), None)
        out["gpu"].append(gpu.get("u") if gpu else None)
        out["gpu_temp"].append(temps.get(gpu.get("n")) if gpu else None)
        pools = s.get("z") or {}
        for name in pool_names:
            p = pools.get(name)
            out["pools"][name].append(round(p["du"] / p["d"] * 100, 2) if p and p.get("d") else None)
    for key in ("gpu", "gpu_temp", "cpu_temp", "net_tx", "net_rx"):
        if all(v is None for v in out[key]):
            out[key] = None
    return out


class Beszel:
    def __init__(self, url: str, email: str, password: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport)
        self._email = email
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        async with self._lock:
            response = await self._client.post(
                "/api/collections/users/auth-with-password",
                json={"identity": self._email, "password": self._password})
            if response.status_code != 200:
                raise BeszelError(f"login failed (HTTP {response.status_code})")
            self._token = response.json()["token"]

    async def _get(self, path: str, params: dict) -> dict:
        for attempt in (1, 2):
            if self._token is None:
                await self._login()
            response = await self._client.get(path, params=params, headers={"Authorization": self._token or ""})
            if response.status_code in (401, 403) and attempt == 1:
                self._token = None
                continue
            if response.status_code != 200:
                raise BeszelError(f"HTTP {response.status_code} from {path}")
            return response.json()
        raise BeszelError("authentication keeps failing")

    async def systems(self) -> dict[str, dict]:
        """Systems keyed by their name in Beszel."""
        data = await self._get("/api/collections/systems/records", {"perPage": 200})
        return {item["name"]: item for item in data.get("items", [])}

    async def latest(self, window_s: int = 300) -> dict[str, dict]:
        """Newest one-minute record per system id."""
        data = await self._get("/api/collections/system_stats/records", {
            "perPage": 200, "sort": "-created", "fields": "system,stats,created",
            "filter": f"type='1m' && created>='{_cutoff(window_s)}'",
        })
        newest: dict[str, dict] = {}
        for item in data.get("items", []):
            newest.setdefault(item["system"], item)
        return newest

    async def records(self, system_id: str, record_type: str, seconds: int) -> list[dict]:
        if not _ID.match(system_id):
            raise BeszelError("invalid system id")
        data = await self._get("/api/collections/system_stats/records", {
            "perPage": 500, "sort": "created", "fields": "stats,created",
            "filter": f"system='{system_id}' && type='{record_type}' && created>='{_cutoff(seconds)}'",
        })
        return data.get("items", [])

    async def series(self, system_id: str, range_name: str) -> dict:
        record_type, seconds = RANGES[range_name]
        items = await self.records(system_id, record_type, seconds)
        if len(items) < 2 and record_type != "1m":
            # A young hub has no coarse records yet; fall back to the finest ones.
            items = await self.records(system_id, "1m", seconds)
        return to_series(items)
