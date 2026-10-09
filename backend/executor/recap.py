"""The weekly recap: a few headline numbers for the last days, against the days before."""

from __future__ import annotations

import time
from collections import Counter, defaultdict

from .store import Store

DAY = 86400


def _day(moment: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(moment))


def summarize_plays(plays: list[dict]) -> dict:
    titles: dict[str, dict] = {}
    viewers: dict[str, dict] = {}
    places: dict[tuple, dict] = {}
    hours_of_day: Counter[int] = Counter()
    for p in plays:
        hours = p["seconds"] / 3600
        title = titles.setdefault(p["title"], {"title": p["title"], "kind": p["kind"], "hours": 0.0, "plays": 0,
                                               "viewers": set()})
        title["hours"] += hours
        title["plays"] += 1
        title["viewers"].add(p["user_id"])
        if p["kind"] and not title["kind"]:
            title["kind"] = p["kind"]
        viewer = viewers.setdefault(p["user_id"], {"id": p["user_id"], "name": p["user_name"], "hours": 0.0,
                                                   "plays": 0})
        viewer["hours"] += hours
        viewer["plays"] += 1
        if p["city"]:
            place = places.setdefault((p["city"], p["country_code"]), {"city": p["city"],
                                                                        "country_code": p["country_code"],
                                                                        "plays": 0, "viewers": set()})
            place["plays"] += 1
            place["viewers"].add(p["user_id"])
        hours_of_day[time.localtime(p["started"]).tm_hour] += 1

    def top(items, key, n=5):
        ranked = sorted(items, key=key, reverse=True)[:n]
        return [{**i, **({"viewers": len(i["viewers"])} if "viewers" in i else {}),
                 **({"hours": round(i["hours"], 1)} if "hours" in i else {})} for i in ranked]

    longest = max(plays, key=lambda p: p["seconds"], default=None)
    return {
        "plays": len(plays),
        "hours": round(sum(p["seconds"] for p in plays) / 3600, 1),
        "viewers": len(viewers),
        "titles": len(titles),
        "countries": len({p["country_code"] for p in plays if p["country_code"]}),
        "top_titles": top(titles.values(), lambda t: (t["hours"], t["plays"])),
        "top_viewers": top(viewers.values(), lambda v: (v["hours"], v["plays"])),
        "top_places": top(places.values(), lambda c: (c["plays"], len(c["viewers"]))),
        "prime_hour": hours_of_day.most_common(1)[0][0] if hours_of_day else None,
        "longest": {"user_id": longest["user_id"], "name": longest["user_name"], "title": longest["title"],
                    "episode": longest["episode"], "hours": round(longest["seconds"] / 3600, 1)}
        if longest and longest["seconds"] else None,
    }


def _edge(store: Store, start: float, previous: float, now: float) -> dict | None:
    """Requests to the public sites (without health checks) and attacks, against the period before."""
    sites = store.edge_sites(start, now)
    if not sites:
        return None
    before = store.edge_sites(previous, start)
    busiest = max(sites, key=lambda s: s["requests"])
    attacks = store.threat_totals(start, now)
    attacks_before = store.threat_totals(previous, start)
    return {
        "requests": sum(s["requests"] for s in sites),
        "requests_before": sum(s["requests"] for s in before),
        "busiest": {"site": busiest["site"], "requests": busiest["requests"]},
        "attacks": attacks["ssh"] + attacks["fw"] + attacks["scans"],
        "attacks_before": attacks_before["ssh"] + attacks_before["fw"] + attacks_before["scans"],
        "bans": attacks["bans"],
    }


def build_recap(store: Store, now: float | None = None, days: int = 7, storage: list[dict] | None = None,
                photos: dict | None = None) -> dict:
    now = now or time.time()
    start, previous = now - days * DAY, now - 2 * days * DAY
    plays = summarize_plays(store.plays(start, now, now))
    before = summarize_plays(store.plays(previous, start, now))
    events = store.event_counts(start, now)
    uptime = store.uptime(start, now)
    worst = min(uptime.items(), key=lambda kv: kv[1], default=None)
    counted: dict[str, dict[str, int]] = defaultdict(dict, events)

    def count(kind: str, *levels: str) -> int:
        return sum(counted[kind].get(level, 0) for level in levels) if counted.get(kind) else 0

    downloaded = store.daily_sum("download_bytes", _day(start), _day(now))
    downloaded_before = store.daily_sum("download_bytes", _day(previous), _day(start - 1))
    return {
        "days": days,
        "from": start,
        "to": now,
        "media": plays,
        "previous": {k: before[k] for k in ("plays", "hours", "viewers", "titles")},
        "downloads": {
            "bytes": downloaded,
            "bytes_before": downloaded_before,
            "completed": count("download", "good"),
            "failed": count("download", "bad"),
            "requests": count("request", "info"),
        },
        "reliability": {
            "uptime": round(sum(uptime.values()) / len(uptime), 2) if uptime else None,
            "worst": {"service": worst[0], "uptime": worst[1]} if worst else None,
            "incidents": count("service", "bad"),
            "machine_outages": count("machine", "bad"),
            "restarts": count("container", "info"),
        },
        "backups": {"succeeded": count("backup", "good"), "failed": count("backup", "bad"),
                    "warnings": count("backup", "warn"),
                    "glacier_growth": next((s["aws"]["growth_7d"] for s in storage or []
                                            if s.get("aws") and s["aws"].get("growth_7d") is not None), None)},
        "actions": {"succeeded": count("action", "good"), "failed": count("action", "bad")},
        "edge": _edge(store, start, previous, now),
        # Uploads to the photo library and its growth, when Immich is configured.
        "photos": photos,
        "machines": store.machine_averages(start, now),
    }
