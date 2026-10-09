"""Media panels: Jellyseerr requests, Sonarr and Radarr queues, qBittorrent speeds.

All read-only. Each client takes its key or password from the web container's
environment and keeps only the fields the page shows.
"""

from __future__ import annotations

import re
import time

import httpx

PENDING = 1  # Jellyseerr request status: pending approval
POSTER = "/imageproxy/tmdb/t/p/w300_and_h450_face{path}"
POSTER_PATH = re.compile(r"^/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)$")
TIMELEFT = re.compile(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)")


def _year(value: str | None) -> int | None:
    return int(value[:4]) if value and value[:4].isdigit() else None


class Jellyseerr:
    def __init__(self, url: str, api_key: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"X-Api-Key": api_key, "Accept": "application/json"})
        # (media type, tmdb id) -> (fetched at, title, year, poster path)
        self._titles: dict[tuple[str, int], tuple[float, str, int | None, str | None]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> dict:
        response = await self._client.get(path, params=params or None)
        if response.status_code in (401, 403):
            raise RuntimeError("Jellyseerr refused the API key")
        response.raise_for_status()
        return response.json()

    async def _title(self, kind: str, tmdb_id: int) -> tuple[str, int | None, str | None]:
        cached = self._titles.get((kind, tmdb_id))
        if cached and time.time() - cached[0] < 86400:
            return cached[1:]
        try:
            data = await self._get(f"/api/v1/{kind}/{tmdb_id}")
        except (httpx.HTTPError, RuntimeError, ValueError):
            return (f"TMDB {tmdb_id}", None, None)
        title = data.get("title") or data.get("name") or f"TMDB {tmdb_id}"
        year = _year(data.get("releaseDate") or data.get("firstAirDate"))
        poster = data.get("posterPath")
        poster = poster if poster and POSTER_PATH.match(poster) else None
        self._titles[(kind, tmdb_id)] = (time.time(), title, year, poster)
        return title, year, poster

    async def _list(self, flt: str, take: int) -> list[dict]:
        data = await self._get("/api/v1/request", take=take, skip=0, filter=flt, sort="added")
        items = []
        for r in data.get("results") or []:
            media = r.get("media") or {}
            kind = "tv" if r.get("type") == "tv" else "movie"
            tmdb_id = media.get("tmdbId")
            if not isinstance(tmdb_id, int):
                continue
            title, year, poster = await self._title(kind, tmdb_id)
            user = r.get("requestedBy") or {}
            items.append({
                "id": r.get("id"),
                "kind": kind,
                "tmdb_id": tmdb_id,
                "title": title,
                "year": year,
                "has_poster": poster is not None,
                "seasons": sorted(s.get("seasonNumber") for s in r.get("seasons") or []
                                  if isinstance(s.get("seasonNumber"), int)),
                "requested_by": user.get("displayName") or user.get("jellyfinUsername") or "Unknown",
                "requested_at": r.get("createdAt"),
                "is_4k": bool(r.get("is4k")),
            })
        return items

    async def requests(self) -> dict:
        counts = await self._get("/api/v1/request/count")
        return {
            "counts": {k: counts.get(k) for k in ("total", "pending", "processing", "available", "declined")},
            "pending": await self._list("pending", 20),
            "processing": await self._list("processing", 6),
        }

    def poster_path(self, kind: str, tmdb_id: int) -> str | None:
        """Only posters of titles this client has already looked up."""
        cached = self._titles.get((kind, tmdb_id))
        return cached[3] if cached else None

    async def poster(self, kind: str, tmdb_id: int) -> tuple[bytes, str] | None:
        path = self.poster_path(kind, tmdb_id)
        if not path:
            return None
        response = await self._client.get(POSTER.format(path=path))
        # Jellyseerr's image proxy labels JPEGs "image/jpg"; serve the standard type.
        kind_header = response.headers.get("content-type", "").split(";")[0].strip().lower()
        kind_header = {"image/jpg": "image/jpeg"}.get(kind_header, kind_header)
        if response.status_code != 200 or kind_header not in ("image/jpeg", "image/png", "image/webp"):
            return None
        return response.content, kind_header


def parse_timeleft(value: str | None) -> int | None:
    match = TIMELEFT.fullmatch(value or "")
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def summarize_queue_item(item: dict, source: str) -> dict:
    size = item.get("size") or 0
    left = item.get("sizeleft") or 0
    if source == "sonarr":
        series = (item.get("series") or {}).get("title") or "Unknown series"
        episode = item.get("episode") or {}
        season, number = episode.get("seasonNumber", item.get("seasonNumber")), episode.get("episodeNumber")
        code = f" S{season:02d}E{number:02d}" if isinstance(season, int) and isinstance(number, int) else ""
        title = f"{series}{code}"
        subtitle = episode.get("title")
    else:
        movie = item.get("movie") or {}
        title = movie.get("title") or item.get("title") or "Unknown movie"
        subtitle = str(movie["year"]) if movie.get("year") else None
    messages = [m for s in item.get("statusMessages") or [] for m in s.get("messages") or []]
    return {
        "id": f"{source}-{item.get('id')}",
        "source": source,
        "title": title,
        "subtitle": subtitle,
        "size": size,
        "progress": round(1 - left / size, 4) if size else None,
        "eta_s": parse_timeleft(item.get("timeleft")),
        "status": item.get("status"),
        "state": item.get("trackedDownloadState"),
        "health": item.get("trackedDownloadStatus") or "ok",
        "message": (item.get("errorMessage") or (messages[0] if messages else None) or "")[:200] or None,
        "client": item.get("downloadClient"),
    }


class Arr:
    """Sonarr or Radarr (API v3)."""

    def __init__(self, source: str, url: str, api_key: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.source = source
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"X-Api-Key": api_key, "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def queue(self) -> list[dict]:
        include = {"includeSeries": "true", "includeEpisode": "true"} if self.source == "sonarr" \
            else {"includeMovie": "true"}
        response = await self._client.get("/api/v3/queue", params={"pageSize": 50, **include})
        if response.status_code in (401, 403):
            raise RuntimeError(f"{self.source.title()} refused the API key")
        response.raise_for_status()
        return [summarize_queue_item(r, self.source) for r in response.json().get("records") or []]


DOWNLOADING = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "checkingDL", "allocating"}
SEEDING = {"uploading", "forcedUP", "stalledUP", "queuedUP", "checkingUP"}
STALLED = {"stalledDL"}
PAUSED = {"pausedDL", "stoppedDL", "queuedDL"}


class QBittorrent:
    def __init__(self, url: str, username: str, password: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._username = username
        self._password = password
        self._logged_in = False
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        response = await self._client.post("/api/v2/auth/login",
                                           data={"username": self._username, "password": self._password})
        if response.status_code == 403:
            raise RuntimeError("qBittorrent has banned this address after failed logins")
        # Older versions answer 200 "Ok.", newer ones 204 with no body.
        ok = response.status_code == 204 or (response.status_code == 200 and response.text.strip() == "Ok.")
        if not ok:
            raise RuntimeError("qBittorrent refused the login")
        self._logged_in = True

    async def _get(self, path: str, **params):
        if not self._logged_in:
            await self._login()
        response = await self._client.get(path, params=params or None)
        if response.status_code == 403:
            # Session expired: log in once more.
            await self._login()
            response = await self._client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    async def status(self) -> dict:
        transfer = await self._get("/api/v2/transfer/info")
        torrents = await self._get("/api/v2/torrents/info")
        states = [t.get("state") for t in torrents]
        return {
            "down_bps": transfer.get("dl_info_speed"),
            "up_bps": transfer.get("up_info_speed"),
            "connection": transfer.get("connection_status"),
            "torrents": len(torrents),
            "downloading": sum(s in DOWNLOADING for s in states),
            "seeding": sum(s in SEEDING for s in states),
            "stalled": sum(s in STALLED for s in states),
            "paused": sum(s in PAUSED for s in states),
            "errored": sum(s in ("error", "missingFiles") for s in states),
        }


class MediaSources:
    """The configured media clients, any of which may be missing."""

    def __init__(self, jellyseerr: Jellyseerr | None = None, sonarr: Arr | None = None,
                 radarr: Arr | None = None, qbittorrent: QBittorrent | None = None) -> None:
        self.jellyseerr = jellyseerr
        self.arrs = [a for a in (sonarr, radarr) if a]
        self.qbittorrent = qbittorrent

    async def close(self) -> None:
        for client in (self.jellyseerr, *self.arrs, self.qbittorrent):
            if client:
                await client.close()
