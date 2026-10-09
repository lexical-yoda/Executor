"""Executor's own history, in one SQLite file in the web data folder.

- Sightings: where media users connect from. One row is a user seen from one
  address on one device over a stretch of time. A new sighting starts when the
  address or device changes or after a gap of more than two hours. Each row
  carries the city-level location looked up when it was first seen.
- Events: a log of things that changed (a service went down, a backup
  finished, someone started watching).
- Checks: service probe results in five-minute buckets, for uptime bars.
- Machine hours: hourly averages of machine stats, so charts reach back a year
  even though the stats source keeps about a month.
- Plays: what was played, by whom and for how long, from Jellyfin's activity
  log, for the weekly recap.
- Daily: per-day counters (bytes downloaded).
- Overrides: a service's name, group, link or visibility as set from the
  page, which win over config.yaml and compose labels.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

GAP = 2 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    ip TEXT NOT NULL,
    device TEXT,
    client TEXT,
    item TEXT,
    source TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    city TEXT,
    region TEXT,
    country TEXT,
    country_code TEXT,
    lat REAL,
    lon REAL,
    radius_km REAL,
    geo_source TEXT
);
CREATE INDEX IF NOT EXISTS sightings_user ON sightings (user_id, last_seen);
CREATE INDEX IF NOT EXISTS sightings_time ON sightings (last_seen);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    actor TEXT,
    ref TEXT
);
CREATE INDEX IF NOT EXISTS events_time ON events (ts);
CREATE TABLE IF NOT EXISTS checks (
    service TEXT NOT NULL,
    bucket INTEGER NOT NULL,
    up INTEGER NOT NULL DEFAULT 0,
    degraded INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0,
    ms_sum REAL NOT NULL DEFAULT 0,
    ms_n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (service, bucket)
);
CREATE TABLE IF NOT EXISTS machine_hours (
    machine TEXT NOT NULL,
    hour INTEGER NOT NULL,
    n INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (machine, hour)
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    item_id TEXT,
    label TEXT NOT NULL,
    device TEXT,
    started REAL NOT NULL,
    ended REAL,
    city TEXT,
    country_code TEXT
);
CREATE INDEX IF NOT EXISTS plays_time ON plays (started);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    episode TEXT,
    kind TEXT,
    year INTEGER,
    runtime_s INTEGER
);
CREATE TABLE IF NOT EXISTS overrides (
    service TEXT PRIMARY KEY,
    name TEXT,
    grp TEXT,
    url TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS daily (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (day, key)
);
CREATE TABLE IF NOT EXISTS edge_hours (
    hour INTEGER NOT NULL,
    site TEXT NOT NULL,
    requests INTEGER NOT NULL,
    monitor INTEGER NOT NULL,
    own INTEGER NOT NULL,
    s2 INTEGER NOT NULL, s3 INTEGER NOT NULL, s4 INTEGER NOT NULL, s5 INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    visitors INTEGER NOT NULL,
    rt TEXT NOT NULL,
    cache_hit INTEGER NOT NULL,
    cache_total INTEGER NOT NULL,
    PRIMARY KEY (hour, site)
);
CREATE TABLE IF NOT EXISTS edge_places (
    hour INTEGER NOT NULL,
    site TEXT NOT NULL,
    country_code TEXT NOT NULL,
    city TEXT NOT NULL,
    lat REAL, lon REAL,
    n INTEGER NOT NULL,
    PRIMARY KEY (hour, site, country_code, city)
);
CREATE TABLE IF NOT EXISTS threat_hours (
    hour INTEGER PRIMARY KEY,
    ssh INTEGER NOT NULL, bans INTEGER NOT NULL, fw INTEGER NOT NULL, scans INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS threat_ips (
    hour INTEGER NOT NULL,
    ip TEXT NOT NULL,
    ssh INTEGER NOT NULL, fw INTEGER NOT NULL, scans INTEGER NOT NULL,
    banned INTEGER NOT NULL,
    country_code TEXT, city TEXT, lat REAL, lon REAL,
    PRIMARY KEY (hour, ip)
);
CREATE TABLE IF NOT EXISTS threat_tags (
    hour INTEGER NOT NULL,
    kind TEXT NOT NULL,
    tag TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (hour, kind, tag)
);
"""

CHECK_BUCKET = 300
# Machine stats kept as hourly averages.
SERIES_KEYS = ("cpu", "mem", "disk", "net_tx", "net_rx", "cpu_temp", "gpu", "gpu_temp")
# Longest a play counts for, so a session left open overnight does not swamp the totals.
MAX_PLAY = 6 * 3600

COLUMNS = ("id", "user_id", "user_name", "ip", "device", "client", "item", "source", "first_seen",
           "last_seen", "city", "region", "country", "country_code", "lat", "lon", "radius_km", "geo_source")
GEO = ("city", "region", "country", "country_code", "lat", "lon", "radius_km", "geo_source")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(sightings)")}
        for column, kind in (("radius_km", "REAL"), ("geo_source", "TEXT")):
            if column not in existing:
                self._db.execute(f"ALTER TABLE sightings ADD COLUMN {column} {kind}")
        # Early rows kept form-encoded device names ("Sam's+phone").
        self._db.execute("UPDATE sightings SET device = REPLACE(device, '+', ' ') "
                         "WHERE device LIKE '%+%' AND device NOT LIKE '% %'")
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    # --- state ---------------------------------------------------------------
    def get_state(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                             "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))

    # --- sightings -----------------------------------------------------------
    def record(self, *, user_id: str, user_name: str, ip: str, when: float, source: str,
               device: str | None = None, client: str | None = None, item: str | None = None,
               geo: dict | None = None) -> None:
        """Extend the matching recent sighting, or start a new one."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, first_seen, last_seen FROM sightings "
                "WHERE user_id = ? AND ip = ? AND IFNULL(device, '') = IFNULL(?, '') "
                "AND last_seen >= ? AND first_seen <= ? ORDER BY last_seen DESC LIMIT 1",
                (user_id, ip, device, when - GAP, when + GAP)).fetchone()
            if row:
                self._db.execute(
                    "UPDATE sightings SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?), "
                    "user_name = ?, client = COALESCE(?, client), item = COALESCE(?, item) WHERE id = ?",
                    (when, when, user_name, client, item, row[0]))
                return
            geo = geo or {}
            self._db.execute(
                "INSERT INTO sightings (user_id, user_name, ip, device, client, item, source, first_seen, "
                "last_seen, city, region, country, country_code, lat, lon, radius_km, geo_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, user_name, ip, device, client, item, source, when, when, geo.get("city"),
                 geo.get("region"), geo.get("country"), geo.get("country_code"), geo.get("lat"), geo.get("lon"),
                 geo.get("radius_km"), geo.get("source")))

    def purge(self, keep_days: float, now: float | None = None) -> int:
        cutoff = (now or time.time()) - keep_days * 86400
        with self._lock:
            return self._db.execute("DELETE FROM sightings WHERE last_seen < ?", (cutoff,)).rowcount

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        return [dict(zip(COLUMNS, row)) for row in self._db.execute(sql, args).fetchall()]

    def trail(self, user_id: str, since: float) -> list[dict]:
        return self._rows(f"SELECT {', '.join(COLUMNS)} FROM sightings WHERE user_id = ? AND last_seen >= ? "
                          "ORDER BY first_seen", (user_id, since))

    def targets(self) -> list[tuple[str, str, str | None]]:
        """Every distinct (address, user name, device) seen, for relocating history."""
        return [tuple(r) for r in self._db.execute(
            "SELECT DISTINCT ip, user_name, device FROM sightings").fetchall()]

    def relocate(self, ip: str, user_name: str, device: str | None, geo: dict | None) -> None:
        geo = geo or {}
        values = [geo.get(k if k != "geo_source" else "source") for k in GEO]
        with self._lock:
            self._db.execute(
                f"UPDATE sightings SET {', '.join(f'{k} = ?' for k in GEO)} "
                "WHERE ip = ? AND user_name = ? AND IFNULL(device, '') = IFNULL(?, '') "
                # Sessions placed at home stay there: the address may belong to someone else later.
                "AND IFNULL(geo_source, '') != 'home'",
                (*values, ip, user_name, device))

    def mark_home(self, ip: str, since: float, geo: dict) -> int:
        """Place recent sessions from the server's own address at home."""
        values = [geo.get(k if k != "geo_source" else "source") for k in GEO]
        with self._lock:
            return self._db.execute(
                f"UPDATE sightings SET {', '.join(f'{k} = ?' for k in GEO)} WHERE ip = ? AND last_seen >= ?",
                (*values, ip, since)).rowcount

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]

    def users(self, since: float) -> list[dict]:
        rows = self._db.execute(
            "SELECT user_id, MAX(user_name), COUNT(*), MAX(last_seen), "
            "COUNT(DISTINCT IFNULL(city, '') || '|' || IFNULL(country_code, '')) "
            "FROM sightings WHERE last_seen >= ? GROUP BY user_id ORDER BY MAX(last_seen) DESC", (since,)).fetchall()
        return [{"id": r[0], "name": r[1], "sightings": r[2], "last_seen": r[3], "places": r[4]} for r in rows]

    def places(self, since: float, user_id: str | None = None) -> dict:
        """Sightings grouped by place, with who was seen there."""
        where, args = "last_seen >= ?", [since]
        if user_id:
            where += " AND user_id = ?"
            args.append(user_id)
        rows = self._db.execute(
            f"SELECT lat, lon, city, region, country, country_code, user_id, MAX(user_name), COUNT(*), "
            f"MIN(first_seen), MAX(last_seen), MAX(radius_km), MAX(geo_source = 'correction') "
            f"FROM sightings WHERE {where} AND lat IS NOT NULL GROUP BY lat, lon, user_id", tuple(args)).fetchall()
        unlocated = self._db.execute(f"SELECT COUNT(*) FROM sightings WHERE {where} AND lat IS NULL",
                                     tuple(args)).fetchone()[0]
        places: dict[tuple, dict] = {}
        for lat, lon, city, region, country, code, uid, name, count, first, last, radius, fixed in rows:
            place = places.setdefault((lat, lon), {
                "lat": lat, "lon": lon, "city": city, "region": region, "country": country,
                "country_code": code, "count": 0, "first_seen": first, "last_seen": last, "users": [],
                "radius_km": radius, "corrected": bool(fixed)})
            place["count"] += count
            place["corrected"] = place["corrected"] or bool(fixed)
            if radius is not None:
                place["radius_km"] = max(place["radius_km"] or 0, radius)
            place["first_seen"] = min(place["first_seen"], first)
            place["last_seen"] = max(place["last_seen"], last)
            place["users"].append({"id": uid, "name": name, "count": count, "last_seen": last})
        result = sorted(places.values(), key=lambda p: p["last_seen"], reverse=True)
        for place in result:
            place["users"].sort(key=lambda u: u["last_seen"], reverse=True)
        return {"places": result, "unlocated": unlocated}

    # --- events --------------------------------------------------------------
    def add_event(self, *, kind: str, level: str, title: str, detail: str | None = None,
                  actor: str | None = None, ref: str | None = None, when: float | None = None) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (ts, kind, level, title, detail, actor, ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (when or time.time(), kind, level, title, detail, actor, ref))
            return int(cursor.lastrowid or 0)

    def events(self, limit: int = 50, before: int | None = None, since: float | None = None) -> list[dict]:
        where, args = [], []
        if before:
            where.append("id < ?")
            args.append(before)
        if since:
            where.append("ts >= ?")
            args.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(
            f"SELECT id, ts, kind, level, title, detail, actor, ref FROM events {clause} ORDER BY id DESC LIMIT ?",
            (*args, max(1, min(limit, 200)))).fetchall()
        keys = ("id", "ts", "kind", "level", "title", "detail", "actor", "ref")
        return [dict(zip(keys, row)) for row in rows]

    def event_counts(self, since: float, until: float) -> dict[str, dict[str, int]]:
        """Events in a window, counted by kind and level."""
        counts: dict[str, dict[str, int]] = {}
        for kind, level, n in self._db.execute(
                "SELECT kind, level, COUNT(*) FROM events WHERE ts >= ? AND ts < ? GROUP BY kind, level",
                (since, until)):
            counts.setdefault(kind, {})[level] = n
        return counts

    # --- service checks ------------------------------------------------------
    def record_checks(self, results: list[tuple[str, str, float | None]], when: float | None = None) -> None:
        """One probe round: (service id, status, latency in ms)."""
        bucket = int((when or time.time()) // CHECK_BUCKET * CHECK_BUCKET)
        with self._lock:
            for service, status, ms in results:
                if status not in ("up", "degraded", "down"):
                    continue
                self._db.execute(
                    f"INSERT INTO checks (service, bucket, {status}, ms_sum, ms_n) VALUES (?, ?, 1, ?, ?) "
                    f"ON CONFLICT (service, bucket) DO UPDATE SET {status} = {status} + 1, "
                    "ms_sum = ms_sum + excluded.ms_sum, ms_n = ms_n + excluded.ms_n",
                    (service, bucket, ms or 0.0, 1 if ms is not None else 0))

    def check_history(self, service: str, since: float, bucket_s: int) -> list[dict]:
        """Probe results grouped into buckets of ``bucket_s`` seconds, oldest first."""
        rows = self._db.execute(
            "SELECT (bucket / ?) * ?, SUM(up), SUM(degraded), SUM(down), SUM(ms_sum), SUM(ms_n) "
            "FROM checks WHERE service = ? AND bucket >= ? GROUP BY 1 ORDER BY 1",
            (bucket_s, bucket_s, service, int(since))).fetchall()
        return [{"t": t, "up": up, "degraded": deg, "down": down,
                 "ms": round(ms_sum / ms_n, 1) if ms_n else None} for t, up, deg, down, ms_sum, ms_n in rows]

    def uptime(self, since: float, until: float | None = None) -> dict[str, float]:
        """Share of checks that were up (or degraded, which still serves) per service."""
        rows = self._db.execute(
            "SELECT service, SUM(up) + SUM(degraded), SUM(up) + SUM(degraded) + SUM(down) FROM checks "
            "WHERE bucket >= ? AND bucket < ? GROUP BY service", (int(since), int(until or time.time() + 1)))
        return {service: round(ok / total * 100, 3) for service, ok, total in rows if total}

    # --- machine hours -------------------------------------------------------
    def add_machine_sample(self, machine: str, values: dict, when: float | None = None) -> None:
        """Fold one stats reading into its hour's running average."""
        hour = int((when or time.time()) // 3600 * 3600)
        with self._lock:
            row = self._db.execute("SELECT n, data FROM machine_hours WHERE machine = ? AND hour = ?",
                                   (machine, hour)).fetchone()
            n, data = (row[0], json.loads(row[1])) if row else (0, {})
            merged = _fold(data, values, n)
            self._db.execute(
                "INSERT INTO machine_hours (machine, hour, n, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (machine, hour) DO UPDATE SET n = excluded.n, data = excluded.data",
                (machine, hour, n + 1, json.dumps(merged)))

    def backfill_machine(self, machine: str, points: list[tuple[float, dict]]) -> int:
        """Older readings from the stats source, for hours not yet recorded."""
        added = 0
        with self._lock:
            for when, values in points:
                hour = int(when // 3600 * 3600)
                added += self._db.execute(
                    "INSERT OR IGNORE INTO machine_hours (machine, hour, n, data) VALUES (?, ?, 1, ?)",
                    (machine, hour, json.dumps(values))).rowcount
        return added

    def machine_series(self, machine: str, since: float, bucket_s: int) -> dict:
        """Hourly averages regrouped into buckets, in the same shape as a live chart series."""
        rows = self._db.execute("SELECT hour, n, data FROM machine_hours WHERE machine = ? AND hour >= ? "
                                "ORDER BY hour", (machine, int(since))).fetchall()
        buckets: dict[int, tuple[int, dict]] = {}
        for hour, n, data in rows:
            key = hour // bucket_s * bucket_s
            count, acc = buckets.get(key, (0, {}))
            buckets[key] = (count + 1, _fold(acc, json.loads(data), count))
        out: dict = {"t": [], "pools": {}, **{k: [] for k in SERIES_KEYS}}
        names = sorted({name for _, data in buckets.values() for name in (data.get("pools") or {})})
        for name in names:
            out["pools"][name] = []
        for key in sorted(buckets):
            data = buckets[key][1]
            out["t"].append(key)
            for k in SERIES_KEYS:
                value = data.get(k)
                out[k].append(round(value, 2) if isinstance(value, (int, float)) else None)
            for name in names:
                value = (data.get("pools") or {}).get(name)
                out["pools"][name].append(round(value, 2) if value is not None else None)
        for k in ("gpu", "gpu_temp", "cpu_temp", "net_tx", "net_rx"):
            if all(v is None for v in out[k]):
                out[k] = None
        return out

    def machine_averages(self, since: float, until: float) -> dict[str, dict]:
        """Average and peak CPU, memory and CPU temperature per machine over a window."""
        result: dict[str, dict] = {}
        for machine, data in self._db.execute(
                "SELECT machine, data FROM machine_hours WHERE hour >= ? AND hour < ?", (int(since), int(until))):
            values = json.loads(data)
            entry = result.setdefault(machine, {"cpu": [], "mem": [], "cpu_temp": []})
            for key in entry:
                if isinstance(values.get(key), (int, float)):
                    entry[key].append(values[key])
        return {m: {f"{k}_avg": round(sum(v) / len(v), 1) if v else None for k, v in e.items()}
                | {f"{k}_max": round(max(v), 1) if v else None for k, v in e.items()} for m, e in result.items()}

    # --- plays ---------------------------------------------------------------
    def start_play(self, *, play_id: int, user_id: str, user_name: str, item_id: str | None, label: str,
                   device: str | None, when: float) -> None:
        place = self._db.execute(
            "SELECT city, country_code FROM sightings WHERE user_id = ? AND first_seen - 1800 <= ? "
            "AND last_seen + 1800 >= ? AND lat IS NOT NULL ORDER BY ABS(first_seen - ?) LIMIT 1",
            (user_id, when, when, when)).fetchone() or (None, None)
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO plays (id, user_id, user_name, item_id, label, device, started, city, "
                "country_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (play_id, user_id, user_name, item_id, label, device, when, place[0], place[1]))

    def stop_play(self, *, user_id: str, item_id: str | None, device: str | None, when: float) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM plays WHERE user_id = ? AND IFNULL(item_id, '') = IFNULL(?, '') "
                "AND IFNULL(device, '') = IFNULL(?, '') AND ended IS NULL AND started <= ? AND started >= ? "
                "ORDER BY started DESC LIMIT 1", (user_id, item_id, device, when, when - MAX_PLAY)).fetchone()
            if not row:
                return False
            self._db.execute("UPDATE plays SET ended = ? WHERE id = ?", (when, row[0]))
            return True

    def unknown_items(self, limit: int = 200) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT item_id FROM plays WHERE item_id IS NOT NULL "
            "AND item_id NOT IN (SELECT id FROM items) LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    def save_items(self, items: list[dict]) -> None:
        with self._lock:
            for item in items:
                self._db.execute(
                    "INSERT INTO items (id, title, episode, kind, year, runtime_s) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET title = excluded.title, episode = excluded.episode, "
                    "kind = excluded.kind, year = excluded.year, runtime_s = excluded.runtime_s",
                    (item["id"], item["title"], item.get("episode"), item.get("kind"), item.get("year"),
                     item.get("runtime_s")))

    def plays(self, since: float, until: float, now: float | None = None) -> list[dict]:
        """Plays started in a window, with a duration estimate for ones never seen to stop."""
        now = now or time.time()
        rows = self._db.execute(
            "SELECT p.id, p.user_id, p.user_name, p.label, p.device, p.started, p.ended, p.city, p.country_code, "
            "i.title, i.episode, i.kind, i.runtime_s FROM plays p LEFT JOIN items i ON i.id = p.item_id "
            "WHERE p.started >= ? AND p.started < ? ORDER BY p.started", (since, until)).fetchall()
        result = []
        for pid, uid, name, label, device, started, ended, city, code, title, episode, kind, runtime in rows:
            if ended is not None:
                seconds = ended - started
            elif runtime:
                seconds = min(runtime, now - started)
            else:
                seconds = 0
            result.append({"id": pid, "user_id": uid, "user_name": name, "title": title or label,
                           "episode": episode, "kind": kind, "device": device, "started": started,
                           "seconds": max(0, min(seconds, MAX_PLAY)), "city": city, "country_code": code})
        return result

    # --- service overrides ---------------------------------------------------
    def overrides(self) -> dict[str, dict]:
        rows = self._db.execute("SELECT service, name, grp, url, hidden FROM overrides").fetchall()
        return {r[0]: {"name": r[1], "group": r[2], "url": r[3], "hidden": bool(r[4])} for r in rows}

    def set_override(self, service: str, *, name: str | None, group: str | None, url: str | None,
                     hidden: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO overrides (service, name, grp, url, hidden, updated) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (service) DO UPDATE SET name = excluded.name, grp = excluded.grp, url = excluded.url, "
                "hidden = excluded.hidden, updated = excluded.updated",
                (service, name, group, url, int(hidden), time.time()))

    def clear_override(self, service: str) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM overrides WHERE service = ?", (service,)).rowcount > 0

    # --- daily counters ------------------------------------------------------
    def add_daily(self, day: str, key: str, value: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO daily (day, key, value) VALUES (?, ?, ?) "
                             "ON CONFLICT (day, key) DO UPDATE SET value = value + excluded.value", (day, key, value))

    def set_daily(self, day: str, key: str, value: float) -> None:
        """Keep the day's latest reading (a level, such as a library size, rather than a count)."""
        with self._lock:
            self._db.execute("INSERT INTO daily (day, key, value) VALUES (?, ?, ?) "
                             "ON CONFLICT (day, key) DO UPDATE SET value = excluded.value", (day, key, value))

    def daily_sum(self, key: str, first_day: str, last_day: str) -> float:
        row = self._db.execute("SELECT SUM(value) FROM daily WHERE key = ? AND day >= ? AND day <= ?",
                               (key, first_day, last_day)).fetchone()
        return row[0] or 0.0

    def daily_series(self, key: str, first_day: str) -> list[tuple[str, float]]:
        return [(r[0], r[1]) for r in self._db.execute(
            "SELECT day, value FROM daily WHERE key = ? AND day >= ? ORDER BY day", (key, first_day))]

    # --- edge traffic and attacks --------------------------------------------
    def save_edge(self, sites: list[dict], places: list[tuple], threats: list[dict], ips: list[tuple],
                  tags: list[dict]) -> None:
        """Replace the hours the edge server reported (it resends the last two days each time)."""
        site_hours = sorted({s["hour"] for s in sites})
        threat_hours = sorted({t["hour"] for t in threats})
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._write_edge(site_hours, threat_hours, sites, places, threats, ips, tags)
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _write_edge(self, site_hours, threat_hours, sites, places, threats, ips, tags) -> None:
        for hour in site_hours:
            self._db.execute("DELETE FROM edge_hours WHERE hour = ?", (hour,))
            self._db.execute("DELETE FROM edge_places WHERE hour = ?", (hour,))
        self._db.executemany(
            "INSERT INTO edge_hours VALUES (:hour, :site, :requests, :monitor, :own, :s2, :s3, :s4, :s5, "
            ":bytes, :visitors, :rt, :cache_hit, :cache_total)", sites)
        self._db.executemany("INSERT OR REPLACE INTO edge_places VALUES (?, ?, ?, ?, ?, ?, ?)", places)
        for hour in threat_hours:
            for table in ("threat_hours", "threat_ips", "threat_tags"):
                self._db.execute(f"DELETE FROM {table} WHERE hour = ?", (hour,))
        self._db.executemany("INSERT INTO threat_hours VALUES (:hour, :ssh, :bans, :fw, :scans)", threats)
        self._db.executemany("INSERT INTO threat_ips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ips)
        self._db.executemany("INSERT OR REPLACE INTO threat_tags VALUES (:hour, :kind, :tag, :n)", tags)

    def _dicts(self, sql: str, args: tuple) -> list[dict]:
        cursor = self._db.execute(sql, args)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def edge_sites(self, since: float, until: float | None = None) -> list[dict]:
        """Totals per site between two times, with the request time buckets added up."""
        rows = self._dicts("SELECT * FROM edge_hours WHERE hour >= ? AND hour < ? ORDER BY hour",
                          (since, until or 2**62))
        sites: dict[str, dict] = {}
        for r in rows:
            s = sites.setdefault(r["site"], {"site": r["site"], "requests": 0, "monitor": 0, "own": 0, "s2": 0,
                                             "s3": 0, "s4": 0, "s5": 0, "bytes": 0, "visitors": 0, "rt": [],
                                             "cache_hit": 0, "cache_total": 0})
            for k in ("requests", "monitor", "own", "s2", "s3", "s4", "s5", "bytes", "visitors", "cache_hit",
                      "cache_total"):
                s[k] += r[k]
            rt = json.loads(r["rt"] or "[]")
            s["rt"] = [a + b for a, b in zip(s["rt"], rt)] if s["rt"] else rt
        return list(sites.values())

    def edge_series(self, since: float, bucket_s: int, site: str | None = None) -> list[dict]:
        where, args = "hour >= ?", [since]
        if site:
            where, args = where + " AND site = ?", args + [site]
        return self._dicts(
            f"SELECT CAST(hour / ? AS INTEGER) * ? AS t, SUM(requests) AS requests, SUM(own) AS own, "
            f"SUM(s4) AS s4, SUM(s5) AS s5, SUM(monitor) AS monitor FROM edge_hours WHERE {where} "
            f"GROUP BY t ORDER BY t", tuple([bucket_s, bucket_s] + args))

    def edge_place_sum(self, since: float, site: str | None = None, limit: int = 300) -> list[dict]:
        where, args = "hour >= ?", [since]
        if site:
            where, args = where + " AND site = ?", args + [site]
        return self._dicts(
            f"SELECT country_code, city, AVG(lat) AS lat, AVG(lon) AS lon, SUM(n) AS n FROM edge_places "
            f"WHERE {where} GROUP BY country_code, city ORDER BY n DESC LIMIT ?", tuple(args + [limit]))

    def threat_series(self, since: float, bucket_s: int) -> list[dict]:
        return self._dicts(
            "SELECT CAST(hour / ? AS INTEGER) * ? AS t, SUM(ssh) AS ssh, SUM(bans) AS bans, SUM(fw) AS fw, "
            "SUM(scans) AS scans FROM threat_hours WHERE hour >= ? GROUP BY t ORDER BY t", (bucket_s, bucket_s, since))

    def threat_totals(self, since: float, until: float | None = None) -> dict:
        row = self._db.execute("SELECT SUM(ssh), SUM(bans), SUM(fw), SUM(scans) FROM threat_hours "
                               "WHERE hour >= ? AND hour < ?", (since, until or 2**62)).fetchone()
        return {k: int(v or 0) for k, v in zip(("ssh", "bans", "fw", "scans"), row)}

    def threat_sources(self, since: float, limit: int = 400) -> list[dict]:
        return self._dicts(
            "SELECT ip, SUM(ssh) AS ssh, SUM(fw) AS fw, SUM(scans) AS scans, MAX(banned) AS banned, "
            "MAX(country_code) AS country_code, MAX(city) AS city, MAX(lat) AS lat, MAX(lon) AS lon "
            "FROM threat_ips WHERE hour >= ? GROUP BY ip ORDER BY SUM(ssh) + SUM(fw) + SUM(scans) DESC LIMIT ?",
            (since, limit))

    def threat_tag_sum(self, since: float, kind: str, limit: int = 10) -> list[dict]:
        return self._dicts("SELECT tag, SUM(n) AS n FROM threat_tags WHERE hour >= ? AND kind = ? "
                          "GROUP BY tag ORDER BY n DESC LIMIT ?", (since, kind, limit))

    # --- upkeep --------------------------------------------------------------
    def purge_ledger(self, now: float | None = None) -> None:
        """Drop old rows from everything but sightings (which follow the history setting)."""
        now = now or time.time()
        day = 86400
        with self._lock:
            self._db.execute("DELETE FROM events WHERE ts < ?", (now - 180 * day,))
            self._db.execute("DELETE FROM checks WHERE bucket < ?", (now - 35 * day,))
            self._db.execute("DELETE FROM machine_hours WHERE hour < ?", (now - 400 * day,))
            self._db.execute("DELETE FROM plays WHERE started < ?", (now - 400 * day,))
            self._db.execute("DELETE FROM daily WHERE day < ?",
                             (time.strftime("%Y-%m-%d", time.gmtime(now - 400 * day)),))
            self._db.execute("DELETE FROM edge_hours WHERE hour < ?", (now - 400 * day,))
            self._db.execute("DELETE FROM edge_places WHERE hour < ?", (now - 35 * day,))
            self._db.execute("DELETE FROM threat_hours WHERE hour < ?", (now - 400 * day,))
            # Attacker addresses are kept a week.
            self._db.execute("DELETE FROM threat_ips WHERE hour < ?", (now - 7 * day,))
            self._db.execute("DELETE FROM threat_tags WHERE hour < ?", (now - 35 * day,))


def _fold(average: dict, values: dict, n: int) -> dict:
    """Running average of a reading into an average of ``n`` readings (pools nested)."""
    out = dict(average)
    for key, value in values.items():
        if key == "pools":
            out["pools"] = _fold(average.get("pools") or {}, value or {}, n)
            continue
        if not isinstance(value, (int, float)):
            continue
        previous = average.get(key)
        out[key] = value if not isinstance(previous, (int, float)) or n <= 0 else previous + (value - previous) / (n + 1)
    return out
