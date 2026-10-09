"""Jellyfin: who is streaming what right now.

Read with an API key created in Jellyfin's dashboard. Used to warn before an
action that would interrupt playback.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote_plus

import httpx

IP_IN_OVERVIEW = re.compile(r"IP address:\s*(\S+)")
ONLINE_FROM = re.compile(r" is online from (.+)$")
# "<user> is playing <item> on <device>" and "<user> has finished playing <item> on <device>".
PLAYING = re.compile(r" (is playing|has finished playing) (.+)$")
PLAY_TYPES = {"VideoPlayback": "start", "AudioPlayback": "start",
              "VideoPlaybackStopped": "stop", "AudioPlaybackStopped": "stop"}


def describe_item(item: dict) -> str:
    name = item.get("Name") or "Unknown"
    if item.get("Type") == "Episode" and item.get("SeriesName"):
        season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f" S{season:02d}E{episode:02d}" if isinstance(season, int) and isinstance(episode, int) else ""
        return f"{item['SeriesName']}{code} · {name}"
    if item.get("Type") == "Movie" and item.get("ProductionYear"):
        return f"{name} ({item['ProductionYear']})"
    return name


def clean_device(name: str | None) -> str | None:
    """Device names sometimes arrive form-encoded ("Sam%27s+phone")."""
    if name and " " not in name and ("+" in name or "%" in name):
        return unquote_plus(name)
    return name


def parse_time(value: str | None) -> float | None:
    """Jellyfin's ISO times, which can carry seven fractional digits."""
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def summarize_sessions(sessions: list[dict]) -> list[dict]:
    """Only sessions that are playing something, newest activity first."""
    streams = []
    for session in sessions:
        item = session.get("NowPlayingItem")
        if not item:
            continue
        play = session.get("PlayState") or {}
        runtime, position = item.get("RunTimeTicks"), play.get("PositionTicks")
        streams.append({
            "user": session.get("UserName") or "Unknown",
            "user_id": session.get("UserId"),
            "title": describe_item(item),
            "kind": item.get("Type"),
            "client": session.get("Client"),
            "device": clean_device(session.get("DeviceName")),
            "ip": session.get("RemoteEndPoint"),
            "paused": bool(play.get("IsPaused")),
            "transcoding": play.get("PlayMethod") == "Transcode",
            "progress": round(position / runtime, 4) if runtime and position is not None else None,
            "runtime_s": round(runtime / 10_000_000) if runtime else None,
            "last_activity": session.get("LastActivityDate"),
        })
    streams.sort(key=lambda s: s["last_activity"] or "", reverse=True)
    return streams


def parse_activity(entry: dict) -> dict | None:
    """A sign-in or session start from the activity log, with its address."""
    if entry.get("Type") not in ("SessionStarted", "AuthenticationSucceeded") or not entry.get("UserId"):
        return None
    ip = IP_IN_OVERVIEW.search(entry.get("ShortOverview") or "")
    when = parse_time(entry.get("Date"))
    if not ip or when is None:
        return None
    device = ONLINE_FROM.search(entry.get("Name") or "")
    return {"id": entry.get("Id"), "user_id": entry["UserId"].replace("-", ""), "ip": ip.group(1),
            "when": when, "device": clean_device(device.group(1)) if device else None}


def parse_playback(entry: dict) -> dict | None:
    """A playback start or stop from the activity log."""
    event = PLAY_TYPES.get(entry.get("Type") or "")
    when = parse_time(entry.get("Date"))
    if not event or not entry.get("UserId") or when is None:
        return None
    match = PLAYING.search(entry.get("Name") or "")
    label, device = None, None
    if match:
        label, _, device = match.group(2).rpartition(" on ")
        if not label:
            label, device = match.group(2), None
    return {"id": entry.get("Id"), "event": event, "user_id": entry["UserId"].replace("-", ""),
            "item_id": (entry.get("ItemId") or "").replace("-", "") or None, "label": label or "Unknown",
            "device": clean_device(device), "when": when}


def describe_library_item(item: dict) -> dict:
    """Title (series name for episodes), episode label, kind, year and runtime."""
    kind = item.get("Type")
    runtime = item.get("RunTimeTicks")
    episode = None
    if kind == "Episode":
        season, number = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f"S{season:02d}E{number:02d} · " if isinstance(season, int) and isinstance(number, int) else ""
        episode = f"{code}{item.get('Name') or ''}".strip(" ·") or None
    return {"id": (item.get("Id") or "").replace("-", ""),
            "title": (item.get("SeriesName") if kind == "Episode" else None) or item.get("Name") or "Unknown",
            "episode": episode, "kind": kind, "year": item.get("ProductionYear"),
            "runtime_s": round(runtime / 10_000_000) if runtime else None}


class Jellyfin:
    def __init__(self, url: str, api_key: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f'MediaBrowser Token="{api_key}"', "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params):
        response = await self._client.get(path, params=params or None)
        if response.status_code in (401, 403):
            raise RuntimeError("Jellyfin refused the API key")
        response.raise_for_status()
        return response.json()

    async def sessions(self) -> list[dict]:
        """Sessions active in the last 16 minutes, as Jellyfin returns them."""
        return await self._get("/Sessions", activeWithinSeconds=960)

    async def streams(self) -> list[dict]:
        return summarize_sessions(await self.sessions())

    async def users(self) -> dict[str, str]:
        return {u["Id"].replace("-", ""): u.get("Name") or "Unknown" for u in await self._get("/Users")}

    async def entries_since(self, last_id: int, page: int = 500, max_pages: int = 40) -> list[dict]:
        """Raw activity log entries with a user, newer than ``last_id``, oldest first."""
        found: list[dict] = []
        for index in range(max_pages):
            data = await self._get("/System/ActivityLog/Entries", startIndex=index * page, limit=page,
                                   hasUserId="true")
            items = data.get("Items") or []
            newer = [i for i in items if (i.get("Id") or 0) > last_id]
            found += newer
            if len(newer) < len(items) or len(items) < page:
                break
        found.sort(key=lambda e: e.get("Id") or 0)
        return found

    async def activity_since(self, last_id: int, page: int = 500, max_pages: int = 40) -> list[dict]:
        """Sign-in events newer than ``last_id``, oldest first."""
        entries = await self.entries_since(last_id, page, max_pages)
        return [e for e in (parse_activity(i) for i in entries) if e]

    async def items(self, ids: list[str]) -> list[dict]:
        """Library details for item ids (deleted items are simply missing)."""
        found: list[dict] = []
        for start in range(0, len(ids), 50):
            data = await self._get("/Items", ids=",".join(ids[start:start + 50]), enableImages="false",
                                   enableUserData="false", recursive="true")
            found += [describe_library_item(i) for i in data.get("Items") or []]
        return found
