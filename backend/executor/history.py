"""Who is watching now, and where media users have connected from.

Samples Jellyfin's sessions every half minute and imports its activity log
(which records the address of every session start, kept by Jellyfin for about
a month), so location history starts with what Jellyfin already knows.
"""

from __future__ import annotations

import logging
import time

import httpx

from .sources.geo import Locator, public_ip
from .sources.jellyfin import Jellyfin, clean_device, parse_activity, parse_playback, parse_time, summarize_sessions
from .store import Store

log = logging.getLogger("executor.history")

# A session counts as present if Jellyfin saw it within this many seconds.
ACTIVE = 300
# Plays are kept for the yearly recap, longer than sightings.
PLAYS_DAYS = 400


class MediaHistory:
    def __init__(self, jellyfin: Jellyfin, store: Store | None, geo: Locator | None, keep_days: int,
                 home_ip_url: str | None = None) -> None:
        self.home_ip_url = home_ip_url
        self._home_checked = 0.0
        self.jellyfin = jellyfin
        self.store = store
        self.geo = geo
        self.keep_days = keep_days
        self.user_names: dict[str, str] = {}
        self.error: str | None = None
        self._users_loaded = 0.0
        self._purged = 0.0

    def locate(self, ip: str | None, user: str | None = None, device: str | None = None) -> dict | None:
        return self.geo.locate(ip, user, device) if self.geo else None

    async def sample(self, now: float | None = None) -> list[dict]:
        """Streams playing now, with their location; records every active session."""
        now = now or time.time()
        sessions = await self.jellyfin.sessions()
        watching = summarize_sessions(sessions)
        for stream in watching:
            stream["location"] = self.locate(stream["ip"], stream["user"], stream["device"])
            stream["location_alt"] = self.geo.second_opinion(stream["ip"], stream["location"]) if self.geo else None
        if self.store:
            for session in sessions:
                ip = public_ip(session.get("RemoteEndPoint"))
                user_id = (session.get("UserId") or "").replace("-", "")
                seen = parse_time(session.get("LastActivityDate"))
                if not ip or not user_id or seen is None or now - seen > ACTIVE:
                    continue
                item = session.get("NowPlayingItem")
                name = session.get("UserName") or "Unknown"
                device = clean_device(session.get("DeviceName"))
                self.store.record(
                    user_id=user_id, user_name=name, ip=ip, when=seen, source="session", device=device,
                    client=session.get("Client"), item=summarize_sessions([session])[0]["title"] if item else None,
                    geo=self.locate(ip, name, device))
        return watching

    async def maintain(self, now: float | None = None) -> None:
        """Slow upkeep: database, user names, activity log import, purge."""
        now = now or time.time()
        if self.geo:
            await self.geo.ensure()
        await self._check_home(now)
        if not self.store:
            return
        # A new database release or changed corrections can move places: relocate all history.
        if self.geo and self.geo.ready and self.store.get_state("geo_signature") != self.geo.signature:
            targets = self.store.targets()
            for ip, user_name, device in targets:
                self.store.relocate(ip, user_name, device, self.geo.locate(ip, user_name, device))
            self.store.set_state("geo_signature", self.geo.signature)
            log.info("relocated %d address and device pairs", len(targets))
        if now - self._users_loaded > 3600 or not self.user_names:
            self.user_names = await self.jellyfin.users()
            self._users_loaded = now
        last_id = int(self.store.get_state("activity_last_id") or 0)
        last_play = int(self.store.get_state("plays_last_id") or 0)
        raw = await self.jellyfin.entries_since(min(last_id, last_play))
        entries = [e for e in (parse_activity(i) for i in raw if (i.get("Id") or 0) > last_id) if e]
        cutoff = now - self.keep_days * 86400
        for entry in entries:
            ip = public_ip(entry["ip"])
            if ip and entry["when"] >= cutoff:
                name = self.user_names.get(entry["user_id"], "Unknown")
                self.store.record(user_id=entry["user_id"], user_name=name, ip=ip, when=entry["when"],
                                  source="log", device=entry["device"], geo=self.locate(ip, name, entry["device"]))
        if raw:
            newest = str(raw[-1].get("Id") or 0)
            self.store.set_state("activity_last_id", newest)
            if entries:
                log.info("imported %d activity log entries", len(entries))
        await self._import_plays(raw, last_play, now)
        if now - self._purged > 86400:
            removed = self.store.purge(self.keep_days, now)
            self._purged = now
            if removed:
                log.info("purged %d sightings older than %d days", removed, self.keep_days)

    async def _import_plays(self, raw: list[dict], last_play: int, now: float) -> None:
        """What was played and for how long, from the log's playback starts and stops."""
        assert self.store is not None
        plays = [p for p in (parse_playback(i) for i in raw if (i.get("Id") or 0) > last_play) if p]
        cutoff = now - PLAYS_DAYS * 86400
        for play in plays:
            if play["when"] < cutoff:
                continue
            if play["event"] == "start":
                self.store.start_play(play_id=play["id"], user_id=play["user_id"],
                                      user_name=self.user_names.get(play["user_id"], "Unknown"),
                                      item_id=play["item_id"], label=play["label"], device=play["device"],
                                      when=play["when"])
            else:
                self.store.stop_play(user_id=play["user_id"], item_id=play["item_id"], device=play["device"],
                                     when=play["when"])
        if raw:
            self.store.set_state("plays_last_id", str(raw[-1].get("Id") or 0))
        if plays:
            log.info("imported %d playback log entries", len(plays))
        unknown = self.store.unknown_items()
        if unknown:
            found = await self.jellyfin.items(unknown)
            known = {item["id"] for item in found}
            # Deleted items stay unknown; remember them so they are not asked for again.
            found += [{"id": i, "title": ""} for i in unknown if i not in known]
            self.store.save_items(found)

    async def _check_home(self, now: float) -> None:
        """Learn the server's public address, so sessions from home are placed at home."""
        if not (self.geo and self.geo.home and self.home_ip_url) or now - self._home_checked < 3600:
            return
        self._home_checked = now
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(self.home_ip_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("home address check failed: %s", exc)
            return
        address = public_ip(response.text.strip())
        if not address or address == self.geo.home_ip:
            return
        first = self.geo.home_ip is None
        self.geo.home_ip = address
        if self.store:
            # Sessions from this address were at home before we learned it: on start, look back a
            # month (home addresses tend to last); after a change, only a day, since the old
            # address may already belong to someone else.
            moved = self.store.mark_home(address, now - (30 if first else 1) * 86400, self.geo.home)
            log.info("home address changed; %d recent sightings placed at home", moved)

    def status(self) -> dict:
        return {
            "enabled": self.store is not None,
            "keep_days": self.keep_days,
            "geo_ready": bool(self.geo and self.geo.ready),
            "geo_sources": self.geo.status() if self.geo else [],
            "corrections": len(self.geo.corrections.rules) if self.geo else 0,
            "home_detected": bool(self.geo and self.geo.home_ip),
            "sightings": self.store.count() if self.store else 0,
            "error": self.error,
        }
