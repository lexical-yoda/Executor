"""TrueNAS storage health: pools, scrubs, disks and their temperatures, alerts.

Read-only, through TrueNAS's JSON-RPC API over a secure websocket, with an API
key of a user that has the Read-only Administrator role (TRUENAS_API_KEY).
TrueNAS revokes an API key that is ever sent over plain HTTP, so the URL must
be wss://. Anything the key may not read is left out rather than failing the
whole panel.
"""

from __future__ import annotations

import itertools
import json
import re
import ssl

from websockets.asyncio.client import connect

TAG = re.compile(r"<[^>]+>")
LEVELS = {"CRITICAL": 5, "ALERT": 5, "EMERGENCY": 5, "ERROR": 4, "WARNING": 3, "NOTICE": 2, "INFO": 1}
DATASET_PROPS = ["used", "available"]


class Forbidden(Exception):
    pass


def _when(value) -> float | None:
    """TrueNAS dates come as {"$date": milliseconds}."""
    if isinstance(value, dict) and isinstance(value.get("$date"), (int, float)):
        return value["$date"] / 1000
    return None


def _parsed(prop) -> int | None:
    return prop.get("parsed") if isinstance(prop, dict) and isinstance(prop.get("parsed"), (int, float)) else None


def _vdev_disks(vdev: dict) -> list[dict]:
    children = vdev.get("children") or []
    return [c for c in children if c.get("disk")] or ([vdev] if vdev.get("disk") else [])


def summarize(info: dict | None, pools: list[dict] | None, disks: list[dict] | None, temps: dict | None,
              temp_week: dict | None, alerts: list[dict] | None, datasets: list[dict] | None) -> dict:
    pool_of: dict[str, str] = {}
    # A pool's own figures count parity; its root dataset shows the usable space.
    roots = {d.get("name"): d for d in datasets or [] if "/" not in (d.get("name") or "")}
    out_pools = []
    for p in pools or []:
        vdevs = []
        for kind, items in (p.get("topology") or {}).items():
            for vdev in items or []:
                members = _vdev_disks(vdev)
                for member in members:
                    pool_of[member["disk"]] = p["name"]
                errors = sum(int((m.get("stats") or {}).get(k) or 0) for m in members
                             for k in ("read_errors", "write_errors", "checksum_errors"))
                vdevs.append({"role": kind, "type": vdev.get("type"), "status": vdev.get("status"),
                              "disks": [m["disk"] for m in members], "errors": errors})
        scan = p.get("scan") or {}
        size, allocated, free = p.get("size"), p.get("allocated"), p.get("free")
        root = roots.get(p.get("name")) or {}
        used, available = _parsed(root.get("used")), _parsed(root.get("available"))
        if used is not None and available is not None:
            size, allocated, free = used + available, used, available
        out_pools.append({
            "name": p.get("name"),
            "status": p.get("status"),
            "healthy": bool(p.get("healthy")),
            "warning": bool(p.get("warning")),
            "detail": p.get("status_detail"),
            "size": size,
            "allocated": allocated,
            "free": free,
            "raw_size": p.get("size"),
            "pct": round(allocated / size * 100, 1) if size and allocated is not None else None,
            "fragmentation": _int(p.get("fragmentation")),
            "scan": {
                "function": scan.get("function"),
                "state": scan.get("state"),
                "started": _when(scan.get("start_time")),
                "finished": _when(scan.get("end_time")),
                "errors": scan.get("errors"),
                "pct": round(scan["percentage"], 1) if isinstance(scan.get("percentage"), (int, float)) else None,
            } if scan.get("function") else None,
            "vdevs": vdevs,
        })
    out_disks = []
    for d in disks or []:
        name = d.get("name")
        week = (temp_week or {}).get(name) or {}
        out_disks.append({
            "name": name,
            "model": d.get("model"),
            "type": d.get("type"),
            "size": d.get("size"),
            "rpm": d.get("rotationrate"),
            "pool": pool_of.get(name),
            "temp": (temps or {}).get(name),
            "temp_max_7d": week.get("max"),
            "temp_avg_7d": round(week["avg"], 1) if isinstance(week.get("avg"), (int, float)) else None,
        })
    order = [p["name"] for p in out_pools]
    out_disks.sort(key=lambda d: (order.index(d["pool"]) if d["pool"] in order else len(order), d["name"] or ""))
    out_alerts = sorted(({
        "id": a.get("uuid"),
        "level": a.get("level"),
        "klass": a.get("klass"),
        "text": TAG.sub("", a.get("formatted") or a.get("text") or "").strip()[:400],
        "since": _when(a.get("datetime")),
    } for a in alerts or [] if not a.get("dismissed")), key=lambda a: -LEVELS.get(a["level"] or "", 0))
    out_datasets = [{
        "name": d.get("name"),
        "pool": (d.get("name") or "").split("/")[0],
        "used": _parsed(d.get("used")),
        "available": _parsed(d.get("available")),
    } for d in datasets or [] if (d.get("name") or "").count("/") == 1 and not (d.get("name") or "").split("/")[1].startswith(("ix-", "."))]
    out_datasets.sort(key=lambda d: -(d["used"] or 0))
    return {
        "system": {
            "version": (info or {}).get("version"),
            "uptime_s": (info or {}).get("uptime_seconds"),
            "update": any(a["klass"] == "HasUpdate" for a in out_alerts),
        } if info else None,
        "pools": out_pools,
        "disks": out_disks if disks is not None else None,
        "alerts": out_alerts if alerts is not None else None,
        "datasets": out_datasets if datasets is not None else None,
    }


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TrueNAS:
    def __init__(self, url: str, api_key: str, username: str | None = None, verify_tls: bool = False,
                 timeout: float = 15.0, connector=connect) -> None:
        if not url.startswith("wss://"):
            raise ValueError("TrueNAS revokes API keys sent over plain HTTP: the url must start with wss://")
        self.url = url
        self._key = api_key
        self._username = username
        self._timeout = timeout
        self._connect = connector
        self._ssl = ssl.create_default_context()
        if not verify_tls:
            self._ssl.check_hostname = False
            self._ssl.verify_mode = ssl.CERT_NONE
        self._ids = itertools.count(1)

    async def close(self) -> None:
        pass

    async def _call(self, ws, method: str, *params):
        call_id = next(self._ids)
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": call_id, "method": method, "params": list(params)}))
        while True:
            reply = json.loads(await ws.recv())
            if reply.get("id") != call_id:
                continue  # a notification or a stale reply
            if "error" in reply:
                error = reply["error"] or {}
                text = json.dumps(error)
                if "EACCES" in text or "Not authorized" in text or "Not authenticated" in text:
                    raise Forbidden(method)
                raise RuntimeError(f"TrueNAS {method} failed: {error.get('message') or 'error'}"[:160])
            return reply.get("result")

    async def _login(self, ws) -> None:
        if self._username:
            result = await self._call(ws, "auth.login_ex", {"mechanism": "API_KEY_PLAIN",
                                                             "username": self._username, "api_key": self._key})
            ok = isinstance(result, dict) and result.get("response_type") == "SUCCESS"
        else:
            ok = await self._call(ws, "auth.login_with_api_key", self._key) is True
        if not ok:
            raise RuntimeError("TrueNAS refused the API key")

    async def _optional(self, ws, method: str, *params):
        try:
            return await self._call(ws, method, *params)
        except Forbidden:
            return None

    async def status(self) -> dict:
        async with self._connect(self.url, ssl=self._ssl, open_timeout=self._timeout, close_timeout=2,
                                 max_size=2**24) as ws:
            await self._login(ws)
            info = await self._optional(ws, "system.info")
            pools = await self._optional(ws, "pool.query")
            disks = await self._optional(ws, "disk.query", [], {"select": ["name", "model", "type", "size",
                                                                           "rotationrate"]})
            names = [d["name"] for d in disks or [] if d.get("name")]
            temps = await self._optional(ws, "disk.temperatures") if names else None
            week = await self._optional(ws, "disk.temperature_agg", names, 7) if names else None
            alerts = await self._optional(ws, "alert.list")
            datasets = await self._optional(ws, "pool.dataset.query", [],
                                            {"extra": {"properties": DATASET_PROPS, "flat": True}})
        if pools is None:
            raise RuntimeError("The TrueNAS key cannot read pools (give its user the Read-only Administrator role)")
        return summarize(info, pools, disks, temps, week, alerts, datasets)
