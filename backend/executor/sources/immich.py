"""Immich photo library: size, counts, background jobs and upload activity.

Read-only, with an API key created in Immich under Account Settings > API Keys
(IMMICH_API_KEY). The key needs the permissions server.statistics,
server.storage, server.versionCheck, queue.read and user.read; statistics and
job queues are admin-only, so the key must belong to an admin. Anything the
key may not read is left out rather than failing the whole panel.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

import httpx

# Immich's job queues, by the names its API uses.
QUEUES = {
    "thumbnailGeneration": "Thumbnails",
    "metadataExtraction": "Metadata",
    "videoConversion": "Video conversion",
    "faceDetection": "Face detection",
    "facialRecognition": "Face recognition",
    "smartSearch": "Smart search",
    "duplicateDetection": "Duplicate detection",
    "backgroundTask": "Background tasks",
    "storageTemplateMigration": "Storage template",
    "migration": "File migration",
    "search": "Search",
    "sidecar": "Sidecar files",
    "library": "External libraries",
    "notifications": "Notifications",
    "backupDatabase": "Database backup",
    "ocr": "Text recognition",
    "workflow": "Workflows",
    "editor": "Editor",
}
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def queue_label(name: str) -> str:
    return QUEUES.get(name) or re.sub(r"(?<!^)(?=[A-Z])", " ", name).capitalize()


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    match = VERSION.match(value or "")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def summarize_queues(queues: list[dict]) -> list[dict]:
    jobs = []
    for q in queues:
        stats = q.get("statistics") or {}
        jobs.append({
            "name": q.get("name"),
            "label": queue_label(str(q.get("name") or "")),
            "paused": bool(q.get("isPaused")),
            **{k: int(stats.get(k) or 0) for k in ("active", "waiting", "delayed", "failed")},
        })
    # Busiest first, so the card shows what is working.
    jobs.sort(key=lambda j: (-(j["active"] + j["waiting"] + j["delayed"]), j["label"]))
    return jobs


def summarize(stats: dict, storage: dict | None, current: dict | None, release: dict | None,
              queues: list[dict] | None) -> dict:
    version = f"{current['major']}.{current['minor']}.{current['patch']}" if current else None
    latest = (release or {}).get("releaseVersion")
    released, running = parse_version(latest), parse_version(version)
    jobs = summarize_queues(queues) if queues is not None else None
    return {
        "photos": int(stats.get("photos") or 0),
        "videos": int(stats.get("videos") or 0),
        "bytes": int(stats.get("usage") or 0),
        "photo_bytes": int(stats.get("usagePhotos") or 0),
        "video_bytes": int(stats.get("usageVideos") or 0),
        "users": sorted(({
            "name": u.get("userName") or "Unknown",
            "photos": int(u.get("photos") or 0),
            "videos": int(u.get("videos") or 0),
            "bytes": int(u.get("usage") or 0),
            "quota": u.get("quotaSizeInBytes"),
        } for u in stats.get("usageByUser") or []), key=lambda u: -u["bytes"]),
        "disk": {
            "size": storage.get("diskSizeRaw"),
            "used": storage.get("diskUseRaw"),
            "free": storage.get("diskAvailableRaw"),
            "pct": storage.get("diskUsagePercentage"),
        } if storage else None,
        "version": version,
        "latest": latest.lstrip("v") if latest else None,
        "update": bool(released and running and released > running),
        "jobs": jobs,
        "backlog": sum(j["active"] + j["waiting"] + j["delayed"] for j in jobs) if jobs is not None else None,
        "failed": sum(j["failed"] for j in jobs) if jobs is not None else None,
    }


class Immich:
    def __init__(self, url: str, api_key: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"x-api-key": api_key, "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params):
        response = await self._client.get(f"/api{path}", params=params or None)
        if response.status_code == 401:
            raise RuntimeError("Immich refused the API key")
        if response.status_code == 403:
            raise PermissionError(path)
        response.raise_for_status()
        return response.json()

    async def _optional(self, path: str, **params):
        """A figure the key may lack the permission for: None instead of an error."""
        try:
            return await self._get(path, **params)
        except (PermissionError, httpx.HTTPStatusError):
            return None

    async def status(self) -> dict:
        try:
            stats = await self._get("/server/statistics")
        except PermissionError:
            raise RuntimeError("The Immich key cannot read server statistics (admin and server.statistics)") \
                from None
        storage, current, release, queues = await asyncio.gather(
            self._optional("/server/storage"), self._optional("/server/version"),
            self._optional("/server/version-check"), self._optional("/queues"))
        return summarize(stats, storage, current, release, queues)

    async def activity(self, kind: str, today: date, days: int = 365) -> list[dict] | None:
        """Assets per day for the key's own account: "Upload" (added) or "Taken" (by photo date)."""
        body = await self._optional("/users/me/calendar-heatmap", type=kind,
                                    **{"from": (today - timedelta(days=days - 1)).isoformat(),
                                       "to": today.isoformat()})
        if not isinstance(body, dict):
            return None
        return [{"date": p["date"], "count": int(p.get("count") or 0)}
                for p in body.get("series") or [] if isinstance(p.get("date"), str)]
