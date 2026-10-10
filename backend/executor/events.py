"""Turns changes between polls into a log of events.

Each poll hands the tracker what it saw. The first sight of anything only sets
a baseline, so a restart of Executor does not flood the log; after that, every
change becomes one event. Service changes must hold for two checks in a row,
so a single slow probe does not log a flap.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque

from .store import Store

log = logging.getLogger("executor.events")

# Docker's "Up 3 hours (healthy)" status text, for spotting restarts.
UPTIME = re.compile(r"^Up (\d+|an?|About an?|Less than a) (second|minute|hour|day|week|month|year)s?", re.I)
UNIT = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def uptime_seconds(status: str | None) -> int | None:
    match = UPTIME.match(status or "")
    if not match:
        return None
    amount = match.group(1).lower()
    number = int(amount) if amount.isdigit() else 1
    return number * UNIT[match.group(2).lower()]


def _size(value: float | None) -> str:
    if value is None:
        return ""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    n, i = float(value), 0
    while n >= 1024 and i < len(units) - 1:
        n, i = n / 1024, i + 1
    return f"{n:.1f} {units[i]}" if i and n < 10 else f"{n:.0f} {units[i]}"


def _span(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


class Tracker:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store
        self._memory: deque[dict] = deque(maxlen=300)
        self._next_id = 1
        self.services_seen: dict[str, tuple[str, float]] = {}
        self._pending: dict[str, tuple[str, int]] = {}
        self.machines_seen: dict[str, tuple[str, float]] = {}
        self.containers_seen: dict[str, dict] | None = None
        self.jobs_seen: dict[str, dict] | None = None
        self.files_seen: dict[str, dict] | None = None
        self.streams_seen: set[str] | None = None
        self.queue_seen: dict[str, dict] | None = None
        self.requests_seen: set[int] | None = None
        self.runs_seen: dict[str, str] | None = None
        self.certs_seen: dict[str, float] | None = None
        self.photos_seen: dict | None = None
        self.pools_seen: dict[str, dict] | None = None
        self.nas_alerts_seen: set[str] | None = None
        self.blocking_seen: str | None = None
        # Library counts when the current burst of uploads (or deletions) began.
        self._photo_burst: dict | None = None

    # --- output --------------------------------------------------------------
    def emit(self, kind: str, level: str, title: str, detail: str | None = None, actor: str | None = None,
             ref: str | None = None, when: float | None = None) -> None:
        when = when or time.time()
        if self.store:
            try:
                self.store.add_event(kind=kind, level=level, title=title, detail=detail, actor=actor, ref=ref,
                                     when=when)
                return
            except Exception as exc:  # noqa: BLE001 - the log must never break polling
                log.warning("cannot record event: %s", exc)
        self._memory.appendleft({"id": self._next_id, "ts": when, "kind": kind, "level": level, "title": title,
                                 "detail": detail, "actor": actor, "ref": ref})
        self._next_id += 1

    def recent(self, limit: int = 50, before: int | None = None) -> list[dict]:
        if self.store:
            return self.store.events(limit, before)
        items = [e for e in self._memory if before is None or e["id"] < before]
        return items[:limit]

    # --- services and machines ----------------------------------------------
    def services(self, services: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        for s in services:
            status = s["status"]
            if status == "unknown":
                continue
            seen = self.services_seen.get(s["id"])
            if seen is None:
                self.services_seen[s["id"]] = (status, now)
                continue
            if status == seen[0]:
                self._pending.pop(s["id"], None)
                continue
            pending = self._pending.get(s["id"])
            count = pending[1] + 1 if pending and pending[0] == status else 1
            if count < 2:
                self._pending[s["id"]] = (status, count)
                continue
            self._pending.pop(s["id"], None)
            self.services_seen[s["id"]] = (status, now)
            ref = f"service:{s['id']}"
            after = f"after {_span(now - seen[1])}"
            if status == "down":
                self.emit("service", "bad", f"{s['name']} is down", s.get("error") or None, ref=ref, when=now)
            elif status == "degraded":
                self.emit("service", "warn", f"{s['name']} is degraded", _degraded_reason(s), ref=ref, when=now)
            elif seen[0] == "down":
                self.emit("service", "good", f"{s['name']} is back up", after, ref=ref, when=now)
            else:
                self.emit("service", "good", f"{s['name']} is healthy again", after, ref=ref, when=now)

    def machines(self, machines: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        for m in machines:
            # Devices that come and go all day (laptops, phones) are not worth an event.
            if m.get("roaming") or m["status"] == "unknown":
                continue
            seen = self.machines_seen.get(m["id"])
            self.machines_seen[m["id"]] = (m["status"], now) if not seen or seen[0] != m["status"] else seen
            if not seen or seen[0] == m["status"]:
                continue
            ref = f"machine:{m['id']}"
            if m["status"] == "down":
                self.emit("machine", "bad", f"{m['name']} went offline", m.get("error"), ref=ref, when=now)
            else:
                self.emit("machine", "good", f"{m['name']} is back online", f"after {_span(now - seen[1])}",
                          ref=ref, when=now)

    def containers(self, containers: dict[str, dict] | None, watched: set[str], now: float | None = None) -> None:
        """Changes to the containers that services list (others come and go with maintenance)."""
        if containers is None:
            return
        now = now or time.time()
        current = {name: c for name, c in containers.items() if name in watched}
        before = self.containers_seen
        self.containers_seen = current
        if before is None:
            return
        for name, c in current.items():
            old = before.get(name)
            ref = f"container:{name}"
            running = c.get("state") == "running"
            if old is None:
                if running:
                    self.emit("container", "info", f"{name} was created", c.get("status"), ref=ref, when=now)
                continue
            was_running = old.get("state") == "running"
            if was_running and not running:
                self.emit("container", "bad", f"{name} stopped", c.get("status"), ref=ref, when=now)
            elif running and not was_running:
                self.emit("container", "good", f"{name} started", c.get("status"), ref=ref, when=now)
            elif running:
                new_up, old_up = uptime_seconds(c.get("status")), uptime_seconds(old.get("status"))
                if new_up is not None and old_up is not None and new_up < old_up and new_up < 600:
                    self.emit("container", "info", f"{name} restarted", None, ref=ref, when=now)
                elif c.get("health") == "unhealthy" and old.get("health") != "unhealthy":
                    self.emit("container", "warn", f"{name} is unhealthy", None, ref=ref, when=now)
                elif c.get("health") == "healthy" and old.get("health") == "unhealthy":
                    self.emit("container", "good", f"{name} is healthy again", None, ref=ref, when=now)
        for name in before.keys() - current.keys():
            self.emit("container", "warn", f"{name} disappeared", "No longer listed by Docker",
                      ref=f"container:{name}", when=now)

    # --- backups -------------------------------------------------------------
    def backups(self, jobs: list[dict], files: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        current_jobs = {j["id"]: j for j in jobs}
        if self.jobs_seen is not None:
            for job_id, job in current_jobs.items():
                old = self.jobs_seen.get(job_id)
                ref = f"backup:{job_id}"
                if old and job["status"] == "running" and old["status"] != "running":
                    self.emit("backup", "info", f"Backup '{job['name']}' started", None, ref=ref, when=now)
                if old and job.get("last_finished") and job.get("last_finished") != old.get("last_finished"):
                    result = job.get("last_result") or "finished"
                    level = {"Success": "good", "Warning": "warn"}.get(result, "bad")
                    latest = (job.get("history") or [{}])[-1]
                    parts = [f"+{_size(latest.get('added_bytes'))}" if latest.get("added_bytes") is not None else "",
                             f"took {_span(job.get('last_duration_s'))}" if job.get("last_duration_s") else ""]
                    word = {"Success": "finished", "Warning": "finished with warnings"}.get(result, "failed")
                    self.emit("backup", level, f"Backup '{job['name']}' {word}",
                              " · ".join(p for p in parts if p) or job.get("last_error"), ref=ref, when=now)
        self.jobs_seen = current_jobs
        current_files = {f["name"]: f for f in files}
        if self.files_seen is not None:
            for name, item in current_files.items():
                old = self.files_seen.get(name)
                ref = f"files:{name}"
                if old and item.get("last") and item.get("last") != old.get("last") and item["status"] == "ok":
                    self.emit("backup", "good", f"'{name}' written", item.get("schedule") or None, ref=ref, when=now)
                elif old and item["status"] in ("failed", "stale", "missing") and old["status"] == "ok":
                    word = {"failed": "failed", "stale": "is overdue", "missing": "is missing files"}[item["status"]]
                    self.emit("backup", "bad", f"'{name}' {word}", item.get("error"), ref=ref, when=now)
        self.files_seen = current_files

    # --- media ---------------------------------------------------------------
    def streams(self, watching: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        keys: dict[str, dict] = {}
        for s in watching:
            keys[f"{s.get('user_id') or s['user']}|{s.get('device') or ''}|{s['title']}"] = s
        before = self.streams_seen
        self.streams_seen = set(keys)
        if before is None:
            return
        for key in keys.keys() - before:
            s = keys[key]
            place = s.get("location") or {}
            where = ", ".join(p for p in (place.get("city"), place.get("country_code")) if p)
            detail = " · ".join(p for p in (s.get("device"), where) if p) or None
            user_id = (s.get("user_id") or "").replace("-", "")
            self.emit("stream", "info", f"started watching {s['title']}", detail, actor=s["user"],
                      ref=f"user:{user_id}" if user_id else None, when=now)

    def downloads(self, queue: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        current = {q["id"]: q for q in queue}
        before = self.queue_seen
        self.queue_seen = current
        if before is None:
            return
        finished: list[dict] = []
        for qid, old in before.items():
            if qid in current:
                new = current[qid]
                if new.get("health") == "error" and old.get("health") != "error":
                    self.emit("download", "bad", f"Download failed: {_label(new)}", new.get("message"),
                              ref=f"download:{qid}", when=now)
                continue
            if (old.get("progress") or 0) >= 0.99 or old.get("status") in ("completed", "importing"):
                finished.append(old)
        # A season grabbed at once arrives as many queue items: one event per show.
        for title, items in _by_title(finished).items():
            self.emit("download", "good", f"Downloaded {_what(title, items)}",
                      _batch(items), ref=f"download:{items[0]['id']}", when=now)
        added = [current[qid] for qid in current.keys() - before.keys()]
        for title, items in _by_title(added).items():
            self.emit("download", "info", f"Grabbed {_what(title, items)}",
                      _batch(items), ref=f"download:{items[0]['id']}", when=now)

    def requests(self, pending: list[dict], processing: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        items = {r["id"]: r for r in [*pending, *processing]}
        before = self.requests_seen
        self.requests_seen = set(items)
        if before is None:
            return
        for rid in items.keys() - before:
            r = items[rid]
            title = f"{r['title']} ({r['year']})" if r.get("year") else r["title"]
            self.emit("request", "info", f"requested {title}", "TV" if r.get("kind") == "tv" else "Movie",
                      actor=r.get("requested_by"), ref=f"request:{rid}", when=now)

    def dns(self, status: dict, now: float | None = None) -> None:
        """Pi-hole's blocking switched off or back on."""
        now = now or time.time()
        blocking = status.get("blocking")
        before, self.blocking_seen = self.blocking_seen, blocking
        if before is None or blocking is None or blocking == before:
            return
        if blocking == "disabled":
            timer = status.get("blocking_timer")
            self.emit("dns", "warn", "Pi-hole stopped blocking ads",
                      f"Back on in {_span(timer)}" if timer else None, ref="dns", when=now)
        elif blocking == "enabled":
            self.emit("dns", "good", "Pi-hole is blocking ads again", None, ref="dns", when=now)

    def storage(self, health: dict, now: float | None = None) -> None:
        """Pool state changes, finished scrubs and new TrueNAS alerts."""
        now = now or time.time()
        pools = {p["name"]: p for p in health.get("pools") or []}
        before = self.pools_seen
        self.pools_seen = pools
        if before is not None:
            for name, pool in pools.items():
                old = before.get(name)
                ref = f"pool:{name}"
                if not old:
                    continue
                if pool["status"] != old["status"]:
                    good = pool["status"] == "ONLINE" and pool["healthy"]
                    self.emit("storage", "good" if good else "bad", f"Pool {name} is {pool['status'].lower()}",
                              pool.get("detail"), ref=ref, when=now)
                scan, old_scan = pool.get("scan") or {}, old.get("scan") or {}
                if scan.get("state") == "FINISHED" and scan.get("finished") and \
                        scan.get("finished") != old_scan.get("finished"):
                    errors = scan.get("errors") or 0
                    word = (scan.get("function") or "scrub").lower()
                    self.emit("storage", "bad" if errors else "good",
                              f"{word.capitalize()} of {name} finished",
                              f"{errors} errors" if errors else "No errors", ref=ref, when=now)
        alerts = health.get("alerts")
        if alerts is None:
            return
        current = {a["id"]: a for a in alerts if a.get("id")}
        seen = self.nas_alerts_seen
        self.nas_alerts_seen = set(current)
        if seen is None:
            return
        for alert_id in current.keys() - seen:
            a = current[alert_id]
            level = {"INFO": "info", "NOTICE": "info", "WARNING": "warn"}.get(a.get("level") or "", "bad")
            self.emit("storage", level, f"TrueNAS: {a.get('klass') or 'alert'}", a.get("text") or None,
                      ref="nas", when=now)

    def photos(self, status: dict, now: float | None = None) -> None:
        """Photo library changes, a release to update to, and jobs that failed."""
        now = now or time.time()
        current = {k: status[k] for k in ("photos", "videos", "bytes")}
        before = self.photos_seen
        self.photos_seen = {**current, "latest": status.get("latest") if status.get("update") else None,
                            "failed": status.get("failed")}
        if before is None:
            return
        # A phone backing up sends a burst over several polls: one event once the counts settle.
        if any(current[k] != before[k] for k in ("photos", "videos")):
            if self._photo_burst is None:
                self._photo_burst = {k: before[k] for k in current}
        elif self._photo_burst is not None:
            start, self._photo_burst = self._photo_burst, None
            self._library_change(start, current, now)
        latest = self.photos_seen["latest"]
        if latest and latest != before.get("latest"):
            self.emit("photos", "info", f"Immich {latest} is available", f"Running {status.get('version')}",
                      ref="photos", when=now)
        failed, failed_before = status.get("failed"), before.get("failed")
        if failed is not None and failed_before is not None and failed > failed_before:
            queues = [j["label"] for j in status.get("jobs") or [] if j["failed"]]
            count = failed - failed_before
            self.emit("photos", "warn", f"{count} Immich {'job' if count == 1 else 'jobs'} failed",
                      ", ".join(queues) or None, ref="photos", when=now)

    def _library_change(self, start: dict, end: dict, now: float) -> None:
        photos, videos = end["photos"] - start["photos"], end["videos"] - start["videos"]
        parts = [f"{abs(n)} {word if abs(n) == 1 else word + 's'}"
                 for n, word in ((photos, "photo"), (videos, "video")) if n]
        if not parts:
            return
        if photos >= 0 and videos >= 0:
            title = f"Added {' and '.join(parts)} to Immich"
        elif photos <= 0 and videos <= 0:
            title = f"Removed {' and '.join(parts)} from Immich"
        else:
            title = "Immich library changed: " + ", ".join(
                f"{'+' if n > 0 else '−'}{p}" for n, p in zip([n for n in (photos, videos) if n], parts))
        grown = end["bytes"] - start["bytes"]
        detail = f"{'+' if grown >= 0 else '−'}{_size(abs(grown))} · {_size(end['bytes'])} in total"
        self.emit("photos", "info", title, detail, ref="photos", when=now)

    # --- actions and certificates -------------------------------------------
    def runs(self, runs: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        current = {r["id"]: r for r in runs}
        before = self.runs_seen
        self.runs_seen = {rid: r["status"] for rid, r in current.items()}
        if before is None:
            return
        for rid, r in current.items():
            old = before.get(rid)
            ref = f"run:{rid}"
            if old is None and r["status"] == "running":
                self.emit("action", "info", f"Action '{r['title']}' started", None, ref=ref, when=now)
            if r["status"] != "running" and old != r["status"]:
                good = r["status"] == "succeeded"
                self.emit("action", "good" if good else "bad",
                          f"Action '{r['title']}' {'succeeded' if good else 'failed'}",
                          None if good else r.get("error"), ref=ref, when=now)

    def certificates(self, certs: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        current = {c["host"]: c["days_left"] for c in certs if c.get("days_left") is not None}
        before = self.certs_seen
        self.certs_seen = current
        if before is None:
            return
        for host, days in current.items():
            if host in before and days > before[host] + 1:
                self.emit("certificate", "good", f"Certificate for {host} renewed", f"{int(days)} days left",
                          ref=f"certificate:{host}", when=now)


def _label(item: dict) -> str:
    return f"{item['title']} {item['subtitle']}" if item.get("subtitle") else item["title"]


def _by_title(items: list[dict]) -> dict[str, list[dict]]:
    """Episodes of one show together (by series), everything else by title."""
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item.get("series") or item["title"], []).append(item)
    return groups


def _what(title: str, items: list[dict]) -> str:
    if len(items) == 1:
        return _label(items[0])
    if items[0].get("series"):
        return f"{len(items)} episodes of {title}"
    return title


def _batch(items: list[dict]) -> str | None:
    size = _size(sum(i.get("size") or 0 for i in items)) if any(i.get("size") for i in items) else ""
    codes = sorted(i["episode"] for i in items if i.get("episode"))
    span = (f"{codes[0]} to {codes[-1]}" if len(codes) > 1 else "") if len(codes) == len(items) else ""
    count = f"{len(items)} items" if len(items) > 1 and not span and not items[0].get("series") else ""
    return " · ".join(p for p in (span or count, size) if p) or None


def _degraded_reason(service: dict) -> str | None:
    bad = [c["name"] for c in service.get("containers") or []
           if c.get("state") != "running" or c.get("health") in ("unhealthy", "starting")]
    return f"Check: {', '.join(bad)}" if bad else None
