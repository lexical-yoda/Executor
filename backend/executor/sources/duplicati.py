"""Duplicati: last result, timing, size and schedule of every backup job.

Duplicati has no read-only login, so this uses the UI password. Only the
fields shown on the page are kept; destinations (which can hold storage
credentials) are never read into the result.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import httpx

HISTORY = 10
BAD_RESULTS = {"Error", "Fatal"}
UNITS = {"s": 1, "m": 60, "h": 3600, "D": 86400, "W": 7 * 86400, "M": 30 * 86400, "Y": 365 * 86400}


def parse_time(value: str | None) -> float | None:
    """Duplicati's compact ``20261008T214314Z`` or an ISO 8601 time."""
    if not value or value.startswith("0001-"):
        return None
    try:
        if re.fullmatch(r"\d{8}T\d{6}Z", value):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_duration(value: str | None) -> float | None:
    """``00:13:14.1852476`` or ``1.02:03:04`` (days.hours:minutes:seconds)."""
    match = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)", value or "")
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_repeat(value: str | None) -> float | None:
    """A schedule's repeat interval, such as ``1D`` or ``1W``, in seconds."""
    match = re.fullmatch(r"(\d+)([smhDWMY])", value or "")
    return int(match.group(1)) * UNITS[match.group(2)] if match else None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_results(logs: list[dict]) -> list[dict]:
    """Backup results from a job's log, newest first."""
    results = []
    for log in logs:
        if log.get("Type") != "Result":
            continue
        try:
            message = json.loads(log.get("Message") or "{}")
        except ValueError:
            continue
        if message.get("MainOperation") not in (None, "Backup"):
            continue
        results.append({
            "result": message.get("ParsedResult") or "Unknown",
            "finished": parse_time(message.get("EndTime")) or log.get("Timestamp"),
            "duration_s": parse_duration(message.get("Duration")),
            "warnings": _int(message.get("WarningsActualLength")) or 0,
            "errors": _int(message.get("ErrorsActualLength")) or 0,
            "added_bytes": _int(message.get("SizeOfAddedFiles")),
        })
    results.sort(key=lambda r: r["finished"] or 0, reverse=True)
    return results[:HISTORY]


def summarize_job(item: dict, logs: list[dict], active: dict | None, now: float | None = None) -> dict:
    now = now or time.time()
    backup = item.get("Backup") or {}
    meta = backup.get("Metadata") or {}
    schedule = item.get("Schedule") or {}
    results = parse_results(logs)
    latest = results[0] if results else None
    finished = parse_time(meta.get("LastBackupFinished")) or (latest or {}).get("finished")
    repeat = parse_repeat(schedule.get("Repeat"))
    stale_after = repeat + max(7200, repeat * 0.15) if repeat else None

    if active is not None:
        status = "running"
    elif latest and latest["result"] in BAD_RESULTS:
        status = "failed"
    elif stale_after and (finished is None or now - finished > stale_after):
        status = "stale"
    elif latest and latest["result"] == "Warning":
        status = "warning"
    elif finished:
        status = "ok"
    else:
        status = "unknown"

    return {
        "id": str(backup.get("ID")),
        "name": backup.get("Name") or f"Backup {backup.get('ID')}",
        "status": status,
        "last_finished": finished,
        "last_duration_s": parse_duration(meta.get("LastBackupDuration")) or (latest or {}).get("duration_s"),
        "last_result": (latest or {}).get("result"),
        "last_error": (meta.get("LastErrorMessage") or "")[:300] or None,
        "source_bytes": _int(meta.get("SourceFilesSize")),
        "target_bytes": _int(meta.get("TargetFilesSize")),
        "versions": _int(meta.get("BackupListCount")),
        "next_run": parse_time(schedule.get("Time")),
        "repeat": schedule.get("Repeat"),
        "stale_after_s": stale_after,
        "progress": active,
        # Oldest first, for a left-to-right strip.
        "history": [{"result": r["result"], "finished": r["finished"], "warnings": r["warnings"],
                     "errors": r["errors"], "added_bytes": r["added_bytes"]} for r in reversed(results)],
    }


def active_backup(state: dict) -> str | None:
    task = state.get("ActiveTask")
    if isinstance(task, dict):
        value = task.get("Item2") or task.get("BackupID")
        return str(value) if value is not None else None
    if isinstance(task, list) and len(task) > 1:
        return str(task[1])
    return None


class Duplicati:
    def __init__(self, url: str, password: str, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._password = password
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        response = await self._client.post("/api/v1/auth/login",
                                           json={"Password": self._password, "RememberMe": False})
        if response.status_code == 401:
            raise RuntimeError("Duplicati refused the password")
        if response.status_code in (400, 403):
            # For example "Invalid hostname": Duplicati only answers host names
            # on its allowlist, so use an IP address in the URL.
            try:
                detail = response.json().get("Error")
            except ValueError:
                detail = None
            raise RuntimeError(f"Duplicati refused the login: {detail or response.status_code}")
        response.raise_for_status()
        self._token = response.json().get("AccessToken")
        if not self._token:
            raise RuntimeError("Duplicati returned no access token")

    async def _get(self, path: str, **params):
        if not self._token:
            await self._login()
        for attempt in range(2):
            response = await self._client.get(path, params=params or None,
                                              headers={"Authorization": f"Bearer {self._token}"})
            if response.status_code == 401 and attempt == 0:
                await self._login()
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Duplicati refused the session")

    async def status(self, now: float | None = None) -> dict:
        backups = await self._get("/api/v1/backups")
        state = await self._get("/api/v1/serverstate")
        running = active_backup(state)
        progress = None
        if running:
            try:
                data = await self._get("/api/v1/progressstate")
                progress = {"phase": data.get("Phase"), "fraction": data.get("OverallProgress")}
            except (httpx.HTTPError, ValueError):
                progress = {"phase": None, "fraction": None}
        jobs = []
        for item in backups:
            job_id = str((item.get("Backup") or {}).get("ID"))
            logs = await self._get(f"/api/v1/backup/{job_id}/log", pagesize=HISTORY)
            jobs.append(summarize_job(item, logs, progress if running == job_id else None, now))
        return {"jobs": jobs, "paused": state.get("ProgramState") == "Paused"}
