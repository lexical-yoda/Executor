import asyncio
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from executor.config import Config
from executor.events import Tracker
from executor.monitor import Monitor, grown_since
from executor.sources.immich import Immich, parse_version, queue_label
from executor.store import Store
from executor.web import create_web_app

GiB = 2**30

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"immich": {"url": "http://photos.example:2283"}},
    "machines": [], "services": [],
})


def library(photos=100, videos=10, usage=5 * GiB):
    return {"photos": photos, "videos": videos, "usage": usage, "usagePhotos": usage // 2,
            "usageVideos": usage - usage // 2,
            "usageByUser": [{"userId": "u1", "userName": "Sam", "photos": photos, "videos": videos,
                             "usage": usage, "usagePhotos": 0, "usageVideos": 0, "quotaSizeInBytes": None},
                            {"userId": "u2", "userName": "Ana", "photos": 0, "videos": 0, "usage": 0,
                             "usagePhotos": 0, "usageVideos": 0, "quotaSizeInBytes": 10 * GiB}]}


def handler_for(state: dict, forbidden: tuple[str, ...] = ()):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-api-key") != "key":
            return httpx.Response(401, json={"message": "Invalid API key"})
        path = request.url.path.removeprefix("/api")
        if path in forbidden:
            return httpx.Response(403, json={"message": "Missing required permission"})
        if path == "/server/statistics":
            return httpx.Response(200, json=library(**state.get("library", {})))
        if path == "/server/storage":
            return httpx.Response(200, json={"diskSizeRaw": 100 * GiB, "diskUseRaw": 6 * GiB,
                                             "diskAvailableRaw": 94 * GiB, "diskUsagePercentage": 6.0})
        if path == "/server/version":
            return httpx.Response(200, json={"major": 3, "minor": 2, "patch": 4, "prerelease": None})
        if path == "/server/version-check":
            return httpx.Response(200, json={"checkedAt": "2026-01-01T00:00:00Z",
                                             "releaseVersion": state.get("release", "v3.3.1")})
        if path == "/queues":
            return httpx.Response(200, json=[
                {"name": "thumbnailGeneration", "isPaused": False,
                 "statistics": {"active": 1, "waiting": 40, "delayed": 0, "failed": state.get("failed", 0),
                                "completed": 0, "paused": 0}},
                {"name": "smartSearch", "isPaused": True,
                 "statistics": {"active": 0, "waiting": 0, "delayed": 0, "failed": 0, "completed": 0, "paused": 0}},
                {"name": "newQueueKind", "isPaused": False, "statistics": {}},
            ])
        if path == "/users/me/calendar-heatmap":
            first = date.fromisoformat(request.url.params["from"])
            last = date.fromisoformat(request.url.params["to"])
            step = 2 if request.url.params["type"] == "Taken" else 1
            days = (last - first).days + 1
            return httpx.Response(200, json={"from": str(first), "to": str(last), "totalCount": days * step,
                                             "series": [{"date": str(first + timedelta(days=i)), "count": step}
                                                        for i in range(days)]})
        return httpx.Response(404)
    return handler


def client_for(state=None, forbidden=(), key="key"):
    return Immich("http://photos.example:2283", key,
                  transport=httpx.MockTransport(handler_for(state or {}, forbidden)))


def test_status_reads_size_users_jobs_and_updates():
    async def check():
        status = await client_for().status()
        assert (status["photos"], status["videos"], status["bytes"]) == (100, 10, 5 * GiB)
        assert [u["name"] for u in status["users"]] == ["Sam", "Ana"] and status["users"][1]["quota"] == 10 * GiB
        assert status["disk"] == {"size": 100 * GiB, "used": 6 * GiB, "free": 94 * GiB, "pct": 6.0}
        assert status["version"] == "3.2.4" and status["latest"] == "3.3.1" and status["update"] is True
        thumbs = status["jobs"][0]
        assert thumbs["label"] == "Thumbnails" and thumbs["waiting"] == 40
        assert status["backlog"] == 41 and status["failed"] == 0
        assert next(j for j in status["jobs"] if j["name"] == "smartSearch")["paused"] is True
        assert next(j for j in status["jobs"] if j["name"] == "newQueueKind")["label"] == "New queue kind"
        # User ids stay on the server.
        assert "u1" not in str(status)
    asyncio.run(check())


def test_missing_permissions_leave_figures_out():
    async def check():
        status = await client_for(forbidden=("/server/storage", "/queues", "/server/version-check")).status()
        assert status["photos"] == 100
        assert status["disk"] is None and status["jobs"] is None and status["backlog"] is None
        assert status["latest"] is None and status["update"] is False
        assert await client_for(forbidden=("/users/me/calendar-heatmap",)).activity("Upload", date(2026, 1, 10)) \
            is None
        with pytest.raises(RuntimeError, match="server statistics"):
            await client_for(forbidden=("/server/statistics",)).status()
        with pytest.raises(RuntimeError, match="refused the API key"):
            await client_for(key="wrong").status()
    asyncio.run(check())


def test_activity_and_versions():
    async def check():
        series = await client_for().activity("Taken", date(2026, 1, 10), days=10)
        assert len(series) == 10 and series[0] == {"date": "2026-01-01", "count": 2}
        assert series[-1]["date"] == "2026-01-10"
    asyncio.run(check())
    assert parse_version("v3.10.0") > parse_version("3.9.9")
    assert parse_version("nightly") is None
    assert queue_label("faceDetection") == "Face detection"


def status(photos=100, videos=10, size=5 * GiB, latest=None, failed=0):
    return {"photos": photos, "videos": videos, "bytes": size, "version": "3.2.4", "latest": latest,
            "update": latest is not None, "failed": failed,
            "jobs": [{"label": "Face detection", "failed": failed}]}


def test_tracker_waits_for_an_upload_burst_to_settle():
    tracker = Tracker()
    tracker.photos(status(), now=1)
    assert tracker.recent() == []
    tracker.photos(status(photos=103, size=5 * GiB + 30 * 2**20), now=2)
    tracker.photos(status(photos=105, videos=11, size=5 * GiB + 90 * 2**20), now=3)
    assert tracker.recent() == []
    tracker.photos(status(photos=105, videos=11, size=5 * GiB + 90 * 2**20), now=4)
    event = tracker.recent()[0]
    assert event["title"] == "Added 5 photos and 1 video to Immich"
    assert event["detail"] == "+90 MiB · 5.1 GiB in total" and event["ref"] == "photos"
    tracker.photos(status(photos=104, videos=11), now=5)
    tracker.photos(status(photos=104, videos=11), now=6)
    assert tracker.recent()[0]["title"] == "Removed 1 photo from Immich"


def test_tracker_reports_a_new_release_once_and_failed_jobs():
    tracker = Tracker()
    tracker.photos(status(latest="3.3.1"), now=1)
    tracker.photos(status(latest="3.3.1"), now=2)
    assert tracker.recent() == []
    tracker.photos(status(latest="3.4.0", failed=2), now=3)
    titles = [e["title"] for e in tracker.recent()]
    assert titles == ["2 Immich jobs failed", "Immich 3.4.0 is available"]
    assert tracker.recent()[0]["detail"] == "Face detection"


def test_grown_since_needs_a_reading_far_enough_back():
    series = [{"date": "2026-01-01", "bytes": 100}, {"date": "2026-01-05", "bytes": 160},
              {"date": "2026-01-08", "bytes": 200}]
    assert grown_since(series, date(2026, 1, 8), 7) == 100
    assert grown_since(series, date(2026, 1, 8), 3) == 40
    assert grown_since(series, date(2026, 1, 8), 30) is None


def test_monitor_snapshot_history_and_recap(tmp_path):
    store = Store(tmp_path / "executor.db")
    today = date.today()
    store.set_daily((today - timedelta(days=7)).isoformat(), "immich_bytes", 4 * GiB)
    immich = client_for()
    monitor = Monitor(CONFIG, None, store=store, immich=immich)
    asyncio.run(monitor.poll_immich())
    snap = monitor.snapshot()["photos"]
    assert snap["ok"] and snap["library"]["photos"] == 100
    assert len(snap["recent"]) == 30 and snap["added_7d"] == 7 and snap["added_30d"] == 30
    assert snap["growth"]["d7"] == GiB and snap["growth"]["d30"] is None
    recap = monitor.photos_recap(7)
    assert recap["added"] == 7 and recap["added_before"] == 7 and recap["bytes_growth"] == GiB

    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, store=store, immich=immich)
    app.state.monitor.photos, app.state.monitor.photo_activity = monitor.photos, monitor.photo_activity
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    body = c.get("/api/photos/history").json()
    assert len(body["upload"]) == 365 and body["taken"][0]["count"] == 2
    assert [p["bytes"] for p in body["size"]] == [4 * GiB, 5 * GiB]
    assert c.get("/api/recap").json()["photos"]["added"] == 7


def test_unconfigured_and_keyless():
    bare = Config.model_validate({"security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
                                  "machines": [], "services": []})
    assert Monitor(bare, None).snapshot()["photos"] is None
    keyless = Monitor(CONFIG, None).snapshot()["photos"]
    assert keyless["configured"] is False and keyless["error"] == "IMMICH_API_KEY not set"
