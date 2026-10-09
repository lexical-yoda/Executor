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
    lon REAL,
    radius_km REAL,
    geo_source TEXT
);
CREATE INDEX IF NOT EXISTS sightings_user ON sightings (user_id, last_seen);
CREATE INDEX IF NOT EXISTS sightings_time ON sightings (last_seen);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

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
