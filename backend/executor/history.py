"""Who is watching now, and where media users have connected from.

Samples Jellyfin's sessions every half minute and imports its activity log
(which records the address of every session start, kept by Jellyfin for about
a month), so location history starts with what Jellyfin already knows.
"""

from __future__ import annotations

import logging
import time

from .sources.geo import GeoIP, public_ip
from .sources.jellyfin import Jellyfin, clean_device, parse_time, summarize_sessions
from .store import Store

log = logging.getLogger("executor.history")

# A session counts as present if Jellyfin saw it within this many seconds.
ACTIVE = 300


class MediaHistory:
    def __init__(self, jellyfin: Jellyfin, store: Store | None, geo: GeoIP | None, keep_days: int) -> None:
        self.jellyfin = jellyfin
        self.store = store
        self.geo = geo
        self.keep_days = keep_days
        self.user_names: dict[str, str] = {}
        self.error: str | None = None
        self._users_loaded = 0.0
        self._purged = 0.0

    def locate(self, ip: str | None) -> dict | None:
        return self.geo.lookup(ip) if self.geo else None

    async def sample(self, now: float | None = None) -> list[dict]:
        """Streams playing now, with their location; records every active session."""
        now = now or time.time()
        sessions = await self.jellyfin.sessions()
        watching = summarize_sessions(sessions)
        for stream in watching:
            stream["location"] = self.locate(stream["ip"])
        if self.store:
            for session in sessions:
                ip = public_ip(session.get("RemoteEndPoint"))
                user_id = (session.get("UserId") or "").replace("-", "")
                seen = parse_time(session.get("LastActivityDate"))
                if not ip or not user_id or seen is None or now - seen > ACTIVE:
                    continue
                item = session.get("NowPlayingItem")
                self.store.record(
                    user_id=user_id, user_name=session.get("UserName") or "Unknown", ip=ip, when=seen,
                    source="session", device=clean_device(session.get("DeviceName")), client=session.get("Client"),
                    item=summarize_sessions([session])[0]["title"] if item else None, geo=self.locate(ip))
        return watching

    async def maintain(self, now: float | None = None) -> None:
        """Slow upkeep: database, user names, activity log import, purge."""
        now = now or time.time()
        if self.geo:
            await self.geo.ensure()
        if not self.store:
            return
        if self.geo and self.geo.ready:
            for ip in self.store.unlocated_ips():
                place = self.geo.lookup(ip)
                if place:
                    self.store.set_location(ip, place)
        if now - self._users_loaded > 3600 or not self.user_names:
            self.user_names = await self.jellyfin.users()
            self._users_loaded = now
        last_id = int(self.store.get_state("activity_last_id") or 0)
        entries = await self.jellyfin.activity_since(last_id)
        cutoff = now - self.keep_days * 86400
        for entry in entries:
            ip = public_ip(entry["ip"])
            if ip and entry["when"] >= cutoff:
                self.store.record(user_id=entry["user_id"],
                                  user_name=self.user_names.get(entry["user_id"], "Unknown"), ip=ip,
                                  when=entry["when"], source="log", device=entry["device"], geo=self.locate(ip))
        if entries:
            self.store.set_state("activity_last_id", str(entries[-1]["id"]))
            log.info("imported %d activity log entries", len(entries))
        if now - self._purged > 86400:
            removed = self.store.purge(self.keep_days, now)
            self._purged = now
            if removed:
                log.info("purged %d sightings older than %d days", removed, self.keep_days)

    def status(self) -> dict:
        return {
            "enabled": self.store is not None,
            "keep_days": self.keep_days,
            "geo_ready": bool(self.geo and self.geo.ready),
            "geo_month": self.geo.month if self.geo else None,
            "geo_error": self.geo.error if self.geo else None,
            "sightings": self.store.count() if self.store else 0,
            "error": self.error,
        }
