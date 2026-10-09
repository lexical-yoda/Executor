import asyncio

import httpx
from fastapi.testclient import TestClient

from executor.config import Config
from executor.sources.jellyfin import Jellyfin, summarize_sessions
from executor.web import create_web_app

SESSIONS = [
    {"UserName": "alice", "Client": "Jellyfin Web", "DeviceName": "Firefox",
     "LastActivityDate": "2026-01-01T10:00:00Z",
     "NowPlayingItem": {"Type": "Episode", "Name": "Pilot", "SeriesName": "Show",
                        "ParentIndexNumber": 1, "IndexNumber": 2},
     "PlayState": {"IsPaused": False, "PlayMethod": "Transcode"}},
    {"UserName": "bob", "Client": "Android TV", "DeviceName": "TV",
     "LastActivityDate": "2026-01-01T11:00:00Z",
     "NowPlayingItem": {"Type": "Movie", "Name": "Film", "ProductionYear": 1999},
     "PlayState": {"IsPaused": True, "PlayMethod": "DirectPlay"}},
    {"UserName": "carol", "Client": "Jellyfin Web", "LastActivityDate": "2026-01-01T12:00:00Z"},
]


def test_only_playing_sessions_are_streams():
    streams = summarize_sessions(SESSIONS)
    assert [s["user"] for s in streams] == ["bob", "alice"]
    assert streams[0]["title"] == "Film (1999)" and streams[0]["paused"] is True
    assert streams[1]["title"] == "Show S01E02 · Pilot" and streams[1]["transcoding"] is True


def test_streams_send_the_key_and_report_refusals():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != 'MediaBrowser Token="good"':
            return httpx.Response(401)
        assert request.url.params["activeWithinSeconds"] == "960"
        return httpx.Response(200, json=SESSIONS)

    async def check():
        good = Jellyfin("http://media.example:8096/", "good", transport=httpx.MockTransport(handler))
        assert len(await good.streams()) == 2
        bad = Jellyfin("http://media.example:8096", "bad", transport=httpx.MockTransport(handler))
        try:
            await bad.streams()
        except RuntimeError as exc:
            assert "refused" in str(exc)
        else:
            raise AssertionError("expected a refusal")

    asyncio.run(check())


CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "machines": [], "services": [],
})


def test_streams_endpoint_when_not_configured_and_configured():
    plain = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False)
    c = TestClient(plain, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    assert c.get("/api/streams").json() == {"configured": False, "ok": False, "error": None, "streams": []}

    media = Jellyfin("http://media.example", "k",
                     transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SESSIONS)))
    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, jellyfin=media)
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    body = c.get("/api/streams").json()
    assert body["ok"] and len(body["streams"]) == 2
