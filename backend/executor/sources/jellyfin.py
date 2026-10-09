"""Jellyfin: who is streaming what right now.

Read with an API key created in Jellyfin's dashboard. Used to warn before an
action that would interrupt playback.
"""

from __future__ import annotations

import httpx


def describe_item(item: dict) -> str:
    name = item.get("Name") or "Unknown"
    if item.get("Type") == "Episode" and item.get("SeriesName"):
        season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f" S{season:02d}E{episode:02d}" if isinstance(season, int) and isinstance(episode, int) else ""
        return f"{item['SeriesName']}{code} · {name}"
    if item.get("Type") == "Movie" and item.get("ProductionYear"):
        return f"{name} ({item['ProductionYear']})"
    return name


def summarize_sessions(sessions: list[dict]) -> list[dict]:
    """Only sessions that are playing something, newest activity first."""
    streams = []
    for session in sessions:
        item = session.get("NowPlayingItem")
        if not item:
            continue
        play = session.get("PlayState") or {}
        streams.append({
            "user": session.get("UserName") or "Unknown",
            "title": describe_item(item),
            "client": session.get("Client"),
            "device": session.get("DeviceName"),
            "paused": bool(play.get("IsPaused")),
            "transcoding": play.get("PlayMethod") == "Transcode",
            "last_activity": session.get("LastActivityDate"),
        })
    streams.sort(key=lambda s: s["last_activity"] or "", reverse=True)
    return streams


class Jellyfin:
    def __init__(self, url: str, api_key: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f'MediaBrowser Token="{api_key}"', "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def streams(self) -> list[dict]:
        response = await self._client.get("/Sessions", params={"activeWithinSeconds": 960})
        if response.status_code in (401, 403):
            raise RuntimeError("Jellyfin refused the API key")
        response.raise_for_status()
        return summarize_sessions(response.json())
