"""Backups judged by the files they leave behind, from the runner's folder report."""

from __future__ import annotations

import fnmatch
import time

from ..config import FileBackup

RANK = {"ok": 0, "unknown": 1, "stale": 2, "missing": 3, "failed": 4}


def evaluate_files(item: FileBackup, folder: dict | None, now: float | None = None) -> dict:
    now = now or time.time()
    result = {"name": item.name, "schedule": item.schedule, "status": "unknown", "last": None,
              "files": [], "kept": None, "log_line": None, "error": None}
    if folder is None:
        result["error"] = "folder report unavailable"
        return result
    if not folder.get("ok"):
        result["error"] = folder.get("error") or "folder unreadable"
        return result

    max_age = item.max_age_hours * 3600
    entries = sorted(folder.get("entries") or [], key=lambda e: e["mtime"], reverse=True)
    by_name = {e["name"]: e for e in entries}
    tails = folder.get("tails") or {}

    def judge(entry: dict | None) -> str:
        if entry is None:
            return "missing"
        if entry["size"] == 0:
            return "failed"
        return "stale" if now - entry["mtime"] > max_age else "ok"

    if item.expect:
        for name in item.expect:
            entry = by_name.get(name)
            result["files"].append({"name": name, "size": entry["size"] if entry else None,
                                    "mtime": entry["mtime"] if entry else None, "state": judge(entry)})
        present = [f["mtime"] for f in result["files"] if f["mtime"]]
        result["last"] = max(present) if present else None
    else:
        matches = [e for e in entries if fnmatch.fnmatch(e["name"], item.pattern or "")]
        newest = matches[0] if matches else None
        result["kept"] = len(matches)
        result["last"] = newest["mtime"] if newest else None
        result["files"].append({"name": newest["name"] if newest else item.pattern,
                                "size": newest["size"] if newest else None,
                                "mtime": newest["mtime"] if newest else None, "state": judge(newest)})

    status = max((f["state"] for f in result["files"]), key=RANK.__getitem__, default="unknown")
    if item.error_file:
        error = by_name.get(item.error_file)
        if error and error["size"] > 0:
            status = "failed"
            lines = tails.get(item.error_file) or []
            result["error"] = lines[-1] if lines else f"{item.error_file} is not empty"
    if item.log_file:
        lines = tails.get(item.log_file) or []
        result["log_line"] = lines[-1] if lines else None
    result["status"] = status
    return result
