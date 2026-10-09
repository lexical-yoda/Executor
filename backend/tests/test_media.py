import asyncio

import httpx
from fastapi.testclient import TestClient

from executor.config import Config
from executor.monitor import Monitor
from executor.sources.media import (Arr, Jellyseerr, MediaSources, QBittorrent, parse_timeleft,
                                    summarize_queue_item)
from executor.web import create_web_app

PNG = b"\x89PNG\r\n\x1a\nfake"


def seerr_handler(request: httpx.Request) -> httpx.Response:
    if request.headers.get("x-api-key") != "key":
        return httpx.Response(401)
    path = request.url.path
    if path == "/api/v1/request/count":
        return httpx.Response(200, json={"total": 5, "pending": 1, "processing": 1, "available": 3, "declined": 0})
    if path == "/api/v1/request":
        flt = request.url.params["filter"]
        result = {"id": 7, "type": "movie", "createdAt": "2026-01-01T10:00:00.000Z", "is4k": False,
                  "media": {"tmdbId": 11, "mediaType": "movie"}, "seasons": [],
                  "requestedBy": {"displayName": "alice", "email": "alice@example.com"}}
        if flt == "processing":
            result = {**result, "id": 8, "type": "tv", "media": {"tmdbId": 22, "mediaType": "tv"},
                      "seasons": [{"seasonNumber": 2}, {"seasonNumber": 1}]}
        return httpx.Response(200, json={"results": [result]})
    if path == "/api/v1/movie/11":
        return httpx.Response(200, json={"title": "Film", "releaseDate": "1999-03-31", "posterPath": "/abc.jpg"})
    if path == "/api/v1/tv/22":
        return httpx.Response(200, json={"name": "Show", "firstAirDate": "2011-04-17",
                                         "posterPath": "/../../etc/passwd"})
    if path == "/imageproxy/tmdb/t/p/w300_and_h450_face/abc.jpg":
        # As Jellyseerr labels JPEGs.
        return httpx.Response(200, content=PNG, headers={"content-type": "image/jpg"})
    return httpx.Response(404)


def test_requests_with_titles_and_safe_posters():
    async def check():
        seerr = Jellyseerr("http://seerr.example", "key", transport=httpx.MockTransport(seerr_handler))
        data = await seerr.requests()
        assert data["counts"]["pending"] == 1
        film = data["pending"][0]
        assert film["title"] == "Film" and film["year"] == 1999 and film["requested_by"] == "alice"
        assert film["has_poster"] is True
        assert "alice@example.com" not in str(data)
        show = data["processing"][0]
        assert show["title"] == "Show" and show["seasons"] == [1, 2]
        # A poster path that is not a plain file name is never used.
        assert show["has_poster"] is False and seerr.poster_path("tv", 22) is None
        assert await seerr.poster("movie", 11) == (PNG, "image/jpeg")
        assert await seerr.poster("movie", 999) is None

    asyncio.run(check())


def test_queue_items():
    assert parse_timeleft("00:12:30") == 750 and parse_timeleft("1.02:00:00") == 93600
    episode = summarize_queue_item({
        "id": 1, "size": 1000, "sizeleft": 250, "timeleft": "00:05:00", "status": "downloading",
        "trackedDownloadState": "downloading", "trackedDownloadStatus": "ok", "downloadClient": "qBittorrent",
        "series": {"title": "Show"}, "episode": {"seasonNumber": 1, "episodeNumber": 2, "title": "Pilot"},
    }, "sonarr")
    assert episode["title"] == "Show S01E02" and episode["subtitle"] == "Pilot"
    assert episode["progress"] == 0.75 and episode["eta_s"] == 300
    movie = summarize_queue_item({
        "id": 2, "size": 0, "status": "warning", "trackedDownloadStatus": "warning",
        "statusMessages": [{"title": "x", "messages": ["No files found are eligible for import"]}],
        "movie": {"title": "Film", "year": 1999},
    }, "radarr")
    assert movie["title"] == "Film" and movie["subtitle"] == "1999" and movie["progress"] is None
    assert movie["health"] == "warning" and "eligible" in movie["message"]


def qbit_handler(modern: bool = False):
    state = {"logins": 0, "expire": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            state["logins"] += 1
            ok = b"password=pw" in request.content
            cookie = {"set-cookie": "SID=abc; path=/"} if ok else {}
            if modern:
                return httpx.Response(204 if ok else 401, headers=cookie)
            return httpx.Response(200, text="Ok." if ok else "Fails.", headers=cookie)
        if request.headers.get("cookie") != "SID=abc":
            return httpx.Response(403)
        if state["expire"]:
            state["expire"] = False
            return httpx.Response(403)
        if request.url.path == "/api/v2/transfer/info":
            return httpx.Response(200, json={"dl_info_speed": 5000, "up_info_speed": 100,
                                             "connection_status": "connected"})
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[{"state": "downloading"}, {"state": "stalledUP"},
                                             {"state": "stalledDL"}, {"state": "uploading"}])
        return httpx.Response(404)

    return state, handler


import pytest  # noqa: E402


@pytest.mark.parametrize("modern", [False, True])
def test_qbittorrent_counts_and_relogin(modern):
    state, handler = qbit_handler(modern)

    async def check():
        qbit = QBittorrent("http://qbit.example:8080", "admin", "pw", transport=httpx.MockTransport(handler))
        status = await qbit.status()
        assert state["logins"] == 2
        assert status["down_bps"] == 5000 and status["downloading"] == 1 and status["seeding"] == 2
        assert status["stalled"] == 1 and status["torrents"] == 4
        wrong = QBittorrent("http://qbit.example:8080", "admin", "nope", transport=httpx.MockTransport(handler))
        try:
            await wrong.status()
        except RuntimeError as exc:
            assert "refused" in str(exc)
        else:
            raise AssertionError("expected a refusal")

    asyncio.run(check())


CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"media": {"jellyseerr": {"url": "http://seerr.example"},
                               "sonarr": {"url": "http://sonarr.example"}}},
    "machines": [], "services": [],
})


def test_monitor_and_poster_endpoint():
    def sonarr(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    seerr = Jellyseerr("http://seerr.example", "key", transport=httpx.MockTransport(seerr_handler))
    media = MediaSources(jellyseerr=seerr,
                         sonarr=Arr("sonarr", "http://sonarr.example", "bad", transport=httpx.MockTransport(sonarr)))
    monitor = Monitor(CONFIG, None, media=media)
    asyncio.run(monitor.poll_media())
    snap = monitor.snapshot()["media"]
    assert snap["requests"]["ok"] and snap["requests"]["pending"][0]["title"] == "Film"
    assert snap["downloads"]["errors"] == {"sonarr": "Sonarr refused the API key"}
    assert snap["downloads"]["torrents"]["configured"] is False

    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, media=media)
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    # Titles the server has not seen yet are refused.
    assert c.get("/api/media/poster/movie/999").status_code == 404
    assert c.get("/api/media/poster/person/11").status_code == 404
    response = c.get("/api/media/poster/movie/11")
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/jpeg"
