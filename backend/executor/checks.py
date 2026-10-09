"""Probes: HTTP and TCP service checks, ICMP pings, local host stats."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from icmplib import async_ping

from .config import Check


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Probe:
    ok: bool
    latency_ms: float | None = None
    http_status: int | None = None
    error: str | None = None
    checked_at: str = ""


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectTimeout | httpx.ReadTimeout | asyncio.TimeoutError):
        return "timed out"
    if isinstance(exc, httpx.ConnectError | ConnectionRefusedError):
        return "connection refused" if "refused" in str(exc).lower() else "cannot connect"
    text = str(exc) or type(exc).__name__
    return text[:160]


async def http_probe(check: Check, client: httpx.AsyncClient) -> Probe:
    started = time.perf_counter()
    try:
        response = await client.get(check.url or "", timeout=check.timeout)
    except Exception as exc:  # noqa: BLE001
        return Probe(ok=False, error=_describe(exc), checked_at=now())
    latency = round((time.perf_counter() - started) * 1000, 1)
    status = response.status_code
    ok = status in check.expect if check.expect else status < 500
    return Probe(ok=ok, latency_ms=latency, http_status=status,
                 error=None if ok else f"HTTP {status}", checked_at=now())


async def tcp_probe(check: Check) -> Probe:
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(check.host, check.port), timeout=check.timeout)
        writer.close()
        await writer.wait_closed()
    except Exception as exc:  # noqa: BLE001
        return Probe(ok=False, error=_describe(exc), checked_at=now())
    return Probe(ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 1), checked_at=now())


async def ping(address: str) -> Probe:
    try:
        host = await async_ping(address, count=2, interval=0.2, timeout=1.5, privileged=False)
    except Exception as exc:  # noqa: BLE001
        return Probe(ok=False, error=_describe(exc), checked_at=now())
    if not host.is_alive:
        return Probe(ok=False, error="no reply", checked_at=now())
    return Probe(ok=True, latency_ms=round(host.avg_rtt, 1), checked_at=now())


def local_stats() -> dict | None:
    """Host stats read from /proc, which a container shares with its host."""
    try:
        with open("/proc/loadavg", encoding="ascii") as handle:
            load = [float(x) for x in handle.read().split()[:3]]
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.split()[0])
        with open("/proc/uptime", encoding="ascii") as handle:
            uptime = float(handle.read().split()[0])
    except (OSError, ValueError, KeyError):
        return None
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", 0)
    return {
        "load": load,
        "cpus": os.cpu_count(),
        "mem_total_gb": round(total / 1024 / 1024, 1) if total else None,
        "mem_used_pct": round((total - available) / total * 100, 1) if total else None,
        "uptime_s": int(uptime),
    }
