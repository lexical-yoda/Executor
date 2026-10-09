"""Executor's own history: where media users connect from, kept for a set number of days.

One row is a "sighting": a user seen from one address on one device over a
stretch of time. A new sighting starts when the address or device changes or
after a gap of more than two hours. Each row carries the city-level location
looked up when it was first seen.
"""

from __future__ import annotations

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
    lon REAL
);
CREATE INDEX IF NOT EXISTS sightings_user ON sightings (user_id, last_seen);
CREATE INDEX IF NOT EXISTS sightings_time ON sightings (last_seen);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

COLUMNS = ("id", "user_id", "user_name", "ip", "device", "client", "item", "source", "first_seen",
           "last_seen", "city", "region", "country", "country_code", "lat", "lon")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)
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
                "last_seen, city, region, country, country_code, lat, lon) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, user_name, ip, device, client, item, source, when, when, geo.get("city"),
                 geo.get("region"), geo.get("country"), geo.get("country_code"), geo.get("lat"), geo.get("lon")))

    def purge(self, keep_days: float, now: float | None = None) -> int:
        cutoff = (now or time.time()) - keep_days * 86400
        with self._lock:
            return self._db.execute("DELETE FROM sightings WHERE last_seen < ?", (cutoff,)).rowcount

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        return [dict(zip(COLUMNS, row)) for row in self._db.execute(sql, args).fetchall()]

    def trail(self, user_id: str, since: float) -> list[dict]:
        return self._rows(f"SELECT {', '.join(COLUMNS)} FROM sightings WHERE user_id = ? AND last_seen >= ? "
                          "ORDER BY first_seen", (user_id, since))

    def unlocated_ips(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT DISTINCT ip FROM sightings WHERE lat IS NULL").fetchall()]

    def set_location(self, ip: str, geo: dict) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sightings SET city = ?, region = ?, country = ?, country_code = ?, lat = ?, lon = ? "
                "WHERE ip = ? AND lat IS NULL",
                (geo.get("city"), geo.get("region"), geo.get("country"), geo.get("country_code"),
                 geo.get("lat"), geo.get("lon"), ip))

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
            f"MIN(first_seen), MAX(last_seen) FROM sightings WHERE {where} AND lat IS NOT NULL "
            f"GROUP BY lat, lon, user_id", tuple(args)).fetchall()
        unlocated = self._db.execute(f"SELECT COUNT(*) FROM sightings WHERE {where} AND lat IS NULL",
                                     tuple(args)).fetchone()[0]
        places: dict[tuple, dict] = {}
        for lat, lon, city, region, country, code, uid, name, count, first, last in rows:
            place = places.setdefault((lat, lon), {
                "lat": lat, "lon": lon, "city": city, "region": region, "country": country,
                "country_code": code, "count": 0, "first_seen": first, "last_seen": last, "users": []})
            place["count"] += count
            place["first_seen"] = min(place["first_seen"], first)
            place["last_seen"] = max(place["last_seen"], last)
            place["users"].append({"id": uid, "name": name, "count": count, "last_seen": last})
        result = sorted(places.values(), key=lambda p: p["last_seen"], reverse=True)
        for place in result:
            place["users"].sort(key=lambda u: u["last_seen"], reverse=True)
        return {"places": result, "unlocated": unlocated}
