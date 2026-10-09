"""Edge panel sources: VPS bandwidth against the Vultr allowance, and TLS expiry.

Bandwidth comes from a small JSON file that the VPS writes from Vultr's API
(the API key is locked to the VPS's IP, so the VPS makes the call). Only
outbound transfer is billed; the allowance is the account's pooled credits:
the instance's own credits, which accrue hourly, plus the free monthly credits.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from datetime import datetime, timezone

import certifi
import httpx


def _gb(value) -> float | None:
    return round(float(value), 1) if value is not None else None


def summarize_bandwidth(data: dict, now: float | None = None) -> dict:
    now = now or time.time()
    account = data.get("account") or {}
    mtd = account.get("current_month_to_date") or {}
    projected = account.get("current_month_projected") or {}
    previous = account.get("previous_month") or {}

    def allowance(block: dict) -> float | None:
        parts = [block.get(k) for k in ("instance_bandwidth_credits", "free_bandwidth_credits",
                                        "purchased_bandwidth_credits")]
        return float(sum(p for p in parts if p is not None)) if any(p is not None for p in parts) else None

    start = float(mtd.get("timestamp_start") or projected.get("timestamp_start") or 0) or None
    end = float(projected.get("timestamp_end") or 0) or None
    out_gb = mtd.get("gb_out")
    elapsed = (now - start) / (end - start) if start and end and end > start else None
    projected_out = round(out_gb / elapsed, 1) if out_gb is not None and elapsed and elapsed > 0.01 else None
    month_allowance = allowance(projected)

    daily = [
        {"date": day, "out_gb": round(v.get("outgoing_bytes", 0) / 1e9, 2),
         "in_gb": round(v.get("incoming_bytes", 0) / 1e9, 2)}
        for day, v in sorted((data.get("daily") or {}).items())
    ]
    return {
        "fetched_at": data.get("fetched_at"),
        "age_s": int(now - data["fetched_at"]) if data.get("fetched_at") else None,
        "errors": data.get("errors") or {},
        "instance": data.get("instance"),
        "month_start": start,
        "month_end": end,
        "elapsed_pct": round(elapsed * 100, 1) if elapsed is not None else None,
        "out_gb": _gb(out_gb),
        "in_gb": _gb(mtd.get("gb_in")),
        "allowance_now_gb": _gb(allowance(mtd)),
        "allowance_month_gb": _gb(month_allowance),
        "projected_out_gb": projected_out,
        "projected_pct": round(projected_out / month_allowance * 100, 1)
        if projected_out is not None and month_allowance else None,
        "used_pct": round(out_gb / month_allowance * 100, 1) if out_gb is not None and month_allowance else None,
        "overage_gb": _gb(mtd.get("overage")),
        "overage_cost": mtd.get("overage_cost"),
        "previous": {"out_gb": _gb(previous.get("gb_out")), "in_gb": _gb(previous.get("gb_in")),
                     "allowance_gb": _gb(allowance(previous))} if previous else None,
        "daily": daily,
    }


async def fetch_bandwidth(url: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(url, timeout=10)
    response.raise_for_status()
    return summarize_bandwidth(response.json())


async def fetch_traffic(url: str, client: httpx.AsyncClient) -> dict:
    """The edge server's traffic and attack summary (written by its timer every 5 minutes)."""
    response = await client.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def parse_not_after(value: str) -> datetime:
    """Parse an OpenSSL date such as 'Dec 10 12:00:00 2026 GMT'."""
    return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def describe_certificate(host: str, cert: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    expires = parse_not_after(cert["notAfter"])
    issuer = dict(item for rdn in cert.get("issuer", ()) for item in rdn)
    return {
        "host": host,
        "expires": expires.isoformat(),
        "days_left": round((expires - now).total_seconds() / 86400, 1),
        "issuer": issuer.get("organizationName") or issuer.get("commonName"),
        "error": None,
    }


async def check_certificate(host: str, port: int = 443, timeout: float = 8) -> dict:
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"host": host, "expires": None, "days_left": None, "issuer": None,
                "error": str(exc)[:160] or type(exc).__name__}
    try:
        cert = writer.get_extra_info("ssl_object").getpeercert()
        return describe_certificate(host, cert)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
