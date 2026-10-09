"""Jellyfin: who is streaming what right now.

Read with an API key created in Jellyfin's dashboard. Used to warn before an
action that would interrupt playback.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

IP_IN_OVERVIEW = re.compile(r"IP address:\s*(\S+)")
ONLINE_FROM = re.compile(r" is online from (.+)$")


def describe_item(item: dict) -> str:
    name = item.get("Name") or "Unknown"
    if item.get("Type") == "Episode" and item.get("SeriesName"):
        season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f" S{season:02d}E{episode:02d}" if isinstance(season, int) and isinstance(episode, int) else ""
        return f"{item['SeriesName']}{code} · {name}"
    if item.get("Type") == "Movie" and item.get("ProductionYear"):
        return f"{name} ({item['ProductionYear']})"
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
            "device": session.get("DeviceName"),
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
            "when": when, "device": device.group(1) if device else None}


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

    async def activity_since(self, last_id: int, page: int = 500, max_pages: int = 40) -> list[dict]:
        """Sign-in events newer than ``last_id``, oldest first."""
        found: list[dict] = []
        for index in range(max_pages):
            data = await self._get("/System/ActivityLog/Entries", startIndex=index * page, limit=page,
                                   hasUserId="true")
            items = data.get("Items") or []
            newer = [i for i in items if (i.get("Id") or 0) > last_id]
            found += [e for e in (parse_activity(i) for i in newer) if e]
            if len(newer) < len(items) or len(items) < page:
                break
        found.sort(key=lambda e: e["id"])
        return found
