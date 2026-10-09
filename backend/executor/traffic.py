"""Traffic and attacks at the edge server: its summary in, Executor's records and views out.

The edge server sends counts by hour for the last two days (see README, "Traffic
and shields"). Visitor addresses are placed on the map here and kept only as
city counts; attacker addresses are kept a week with their place.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Callable

RT_BUCKETS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
RANGES = {"24h": (86400, 3600), "7d": (7 * 86400, 6 * 3600), "30d": (30 * 86400, 86400)}

Locate = Callable[[str], dict | None]


def _place(geo: dict | None) -> tuple[str, str, float | None, float | None]:
    if not geo:
        return "", "", None, None
    return geo.get("country_code") or "", geo.get("city") or "", geo.get("lat"), geo.get("lon")


def ingest(data: dict, locate: Locate | None, home_ip: str | None) -> dict:
    """Rows for the store from one summary, plus the live figures that are not kept."""
    if data.get("version") != 1:
        raise RuntimeError("the edge traffic summary has an unknown format")
    cache: dict[str, tuple] = {}

    def where(ip: str) -> tuple:
        if ip not in cache:
            cache[ip] = _place(locate(ip) if locate else None)
        return cache[ip]

    own: dict[tuple[int, str], int] = defaultdict(int)
    places: dict[tuple, list] = {}
    for v in data.get("visitor_ips") or []:
        hour, site, ip, n = int(v["hour"]), str(v["site"]), str(v["ip"]), int(v["n"])
        if home_ip and ip == home_ip:
            own[(hour, site)] += n
            continue
        cc, city, lat, lon = where(ip)
        key = (hour, site, cc, city)
        if key in places:
            places[key][-1] += n
        else:
            places[key] = [hour, site, cc, city, lat, lon, n]
    sites = [{
        "hour": int(s["hour"]), "site": str(s["site"])[:100],
        **{k: int(s.get(k) or 0) for k in ("requests", "monitor", "s2", "s3", "s4", "s5", "bytes", "visitors",
                                           "cache_hit", "cache_total")},
        "own": own.get((int(s["hour"]), str(s["site"])), 0),
        "rt": json.dumps([int(x) for x in s.get("rt") or []]),
    } for s in data.get("sites") or []]
    threats = [{"hour": int(t["hour"]), **{k: int(t.get(k) or 0) for k in ("ssh", "bans", "fw", "scans")}}
               for t in data.get("threat_hours") or []]
    ips = [(int(t["hour"]), str(t["ip"]), int(t.get("ssh") or 0), int(t.get("fw") or 0), int(t.get("scans") or 0),
            int(bool(t.get("banned"))), *where(str(t["ip"]))) for t in data.get("threat_ips") or []]
    tags = [{"hour": int(t["hour"]), "kind": str(t["kind"]), "tag": str(t["tag"])[:40], "n": int(t["n"])}
            for t in data.get("threat_tags") or [] if t.get("kind") in ("user", "port")]
    return {"sites": sites, "places": [tuple(p) for p in places.values()], "threats": threats, "ips": ips,
            "tags": tags, "recent": data.get("recent_15m") or {}, "banned_now": data.get("banned_now"),
            "generated": data.get("generated"), "buckets": data.get("rt_buckets_ms") or RT_BUCKETS}


def percentile(hist: list[int], share: float, buckets: list[int]) -> int | None:
    """The bucket edge (ms) below which ``share`` of requests finished; None for the open last bucket."""
    total = sum(hist)
    if not total:
        return None
    running = 0
    for i, n in enumerate(hist):
        running += n
        if running >= share * total:
            return buckets[i] if i < len(buckets) else None
    return None


def site_view(row: dict, buckets: list[int]) -> dict:
    requests = row["requests"]
    return {
        "site": row["site"],
        "requests": requests,
        "own": row["own"],
        "monitor": row["monitor"],
        "s4": row["s4"],
        "s5": row["s5"],
        "errors_pct": round(row["s5"] / requests * 100, 2) if requests else 0.0,
        "p50_ms": percentile(row["rt"], 0.5, buckets),
        "p95_ms": percentile(row["rt"], 0.95, buckets),
        "slow": bool(row["rt"]) and percentile(row["rt"], 0.95, buckets) is None,
        "bytes": row["bytes"],
        "visitors": row["visitors"],
        "cache_pct": round(row["cache_hit"] / row["cache_total"] * 100) if row["cache_total"] else None,
    }


def countries(rows: list[dict], weight: Callable[[dict], int] = lambda r: int(r.get("n") or 0),
              limit: int = 8) -> list[dict]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[r.get("country_code") or ""] += weight(r)
    ranked = sorted(((cc, n) for cc, n in by.items() if cc), key=lambda kv: -kv[1])[:limit]
    return [{"country_code": cc, "n": n} for cc, n in ranked]


def points(rows: list[dict], weight: Callable[[dict], int]) -> list[dict]:
    """Rows with a place, merged by city, for the map."""
    by: dict[tuple, dict] = {}
    for r in rows:
        if r.get("lat") is None or r.get("lon") is None:
            continue
        key = (r.get("country_code") or "", r.get("city") or "")
        p = by.setdefault(key, {"key": f"{r['lat']:.3f},{r['lon']:.3f}", "lat": r["lat"], "lon": r["lon"],
                                "label": ", ".join(x for x in (r.get("city"), r.get("country_code")) if x),
                                "count": 0})
        p["count"] += weight(r)
    return sorted(by.values(), key=lambda p: -p["count"])


def threat_total(t: dict) -> int:
    return int(t.get("ssh") or 0) + int(t.get("fw") or 0) + int(t.get("scans") or 0)


def overview(store, now: float, recent: dict, banned_now: int | None, buckets: list[int]) -> dict:
    """The last day at the edge, for the snapshot (computed when a summary arrives, not per request)."""
    since = now - 86400
    rows = store.edge_sites(since)
    sites = sorted((site_view(r, buckets) for r in rows), key=lambda s: -s["requests"])
    hourly: dict[str, dict[int, int]] = defaultdict(dict)
    first_hour = int(since // 3600 * 3600)
    for site in sites:
        series = store.edge_series(first_hour, 3600, site["site"])
        hourly[site["site"]] = {int(p["t"]): int(p["requests"] or 0) for p in series}
    hours = [first_hour + i * 3600 for i in range(25)]
    for site in sites:
        site["spark"] = [hourly[site["site"]].get(h, 0) for h in hours]
        r = recent.get(site["site"]) or {}
        site["recent"] = {"requests": int(r.get("requests") or 0), "s5": int(r.get("s5") or 0)}
    totals = {k: sum(s[k] for s in sites) for k in ("requests", "own", "monitor", "s5", "bytes")}
    threat_rows = store.threat_series(first_hour, 3600)
    by_hour = {int(t["t"]): threat_total(t) for t in threat_rows}
    sources = store.threat_sources(since)
    return {
        "sites": sites,
        "totals": totals,
        "countries": countries(store.edge_place_sum(since)),
        "threats": {
            **store.threat_totals(since),
            "banned_now": banned_now,
            "attackers": len(sources),
            "countries": countries(sources, threat_total),
            "users": store.threat_tag_sum(since, "user", 6),
            "ports": store.threat_tag_sum(since, "port", 6),
            "spark": [by_hour.get(h, 0) for h in hours],
        },
    }


def spike(store, now: float) -> tuple[int, float] | None:
    """The last full hour's attacks against the week's hourly average, when it is over three times that."""
    last = int(now // 3600 * 3600) - 3600
    hour = store.threat_series(last, 3600)
    if not hour or int(hour[0]["t"]) != last:
        return None
    count = threat_total(hour[0])
    week = [threat_total(t) for t in store.threat_series(last - 7 * 86400, 3600) if int(t["t"]) < last]
    if len(week) < 24:
        return None
    average = sum(week) / len(week)
    return (count, average) if count >= 30 and count > 3 * average else None


def site_detail(store, site: str, range_name: str, now: float | None = None, buckets: list[int] | None = None) -> dict:
    now = now or time.time()
    seconds, bucket = RANGES[range_name]
    since = now - seconds
    rows = [r for r in store.edge_sites(since) if r["site"] == site]
    return {
        "site": site,
        "range": range_name,
        "series": store.edge_series(int(since // bucket * bucket), bucket, site),
        "bucket_s": bucket,
        "totals": site_view(rows[0], buckets or RT_BUCKETS) if rows else None,
        "places": store.edge_place_sum(since, site, 15),
        "countries": countries(store.edge_place_sum(since, site)),
    }


def threat_detail(store, range_name: str, now: float | None = None) -> dict:
    now = now or time.time()
    seconds, bucket = RANGES[range_name]
    since = now - seconds
    # Attacker addresses are kept a week.
    sources = store.threat_sources(max(since, now - 7 * 86400))
    return {
        "range": range_name,
        "series": store.threat_series(int(since // bucket * bucket), bucket),
        "bucket_s": bucket,
        "totals": store.threat_totals(since),
        "points": points(sources, threat_total)[:300],
        "sources": sources[:20],
        "countries": countries(sources, threat_total),
        "users": store.threat_tag_sum(since, "user", 12),
        "ports": store.threat_tag_sum(since, "port", 12),
    }
