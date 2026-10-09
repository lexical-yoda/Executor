import asyncio
import os
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from executor.config import ActionsConfig, Config, SizedFolder
from executor.monitor import Monitor
from executor.runner import create_runner_app, measure_folder
from executor.sources.jellyfin import Jellyfin, summarize_latest
from executor.store import Store
from executor.web import create_web_app

MOVIES = "a" * 32
SHOWS = "b" * 32
FILM = "c" * 32
SERIES = "d" * 32
PNG = b"\x89PNG\r\n\x1a\nposter"
TOKEN = "t" * 40

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"jellyfin": {"url": "http://media.example:8096", "history_days": 0,
                                  "library_folders": {"Films": "films", "Shows": "shows"}}},
    "machines": [], "services": [],
})


def episode(n, series=SERIES, name="A Show"):
    return {"Type": "Episode", "Id": f"{n:032x}", "SeriesId": series, "SeriesName": name,
            "ParentIndexNumber": 1, "IndexNumber": n, "DateCreated": f"2026-01-0{n}T00:00:00Z"}


def test_recent_additions_group_episodes_by_show():
    items = [episode(3), {"Type": "Movie", "Id": FILM, "Name": "A Film", "ProductionYear": 2001,
                          "DateCreated": "2026-01-02T00:00:00Z"}, episode(2), episode(1),
             episode(1, series="e" * 32, name="Other")]
    latest = summarize_latest(items, limit=2)
    assert [i["title"] for i in latest] == ["A Show", "A Film"]
    assert latest[0]["detail"] == "3 new episodes" and latest[0]["id"] == SERIES
    single = summarize_latest([episode(4)])
    assert single[0]["detail"] == "S01E04"


def jellyfin_handler(request: httpx.Request) -> httpx.Response:
    path, params = request.url.path, request.url.params
    if path == "/Library/VirtualFolders":
        return httpx.Response(200, json=[
            {"Name": "Films", "CollectionType": "movies", "ItemId": MOVIES},
            {"Name": "Shows", "CollectionType": "tvshows", "ItemId": SHOWS},
            {"Name": "Home videos", "CollectionType": None, "ItemId": "f" * 32},
        ])
    if path == "/Items" and params.get("limit") == "0":
        assert params.get("isMissing") == "false"
        counts = {"Movie": 12, "Series": 3, "Episode": 40}
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": counts.get(params.get("includeItemTypes"), 5)})
    if path == "/Items":
        return httpx.Response(200, json={"Items": [
            {"Type": "Movie", "Id": FILM, "Name": "A Film", "DateCreated": "2026-01-02T00:00:00Z"}, episode(1)]})
    if path == f"/Items/{FILM}/Images/Primary":
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    return httpx.Response(404)


class FakeRunner:
    async def sizes(self):
        return {"films": {"id": "films", "ok": True, "bytes": 3000, "files": 12},
                "shows": {"id": "shows", "ok": False, "error": "No such file or directory",
                          "bytes": None, "files": None}}


def test_library_counts_sizes_posters_and_history(tmp_path):
    store = Store(tmp_path / "executor.db")
    store.set_daily((date.today() - timedelta(days=7)).isoformat(), "library_bytes", 1000)
    jellyfin = Jellyfin("http://media.example:8096", "key", transport=httpx.MockTransport(jellyfin_handler))
    monitor = Monitor(CONFIG, FakeRunner(), store=store, jellyfin=jellyfin)  # type: ignore[arg-type]
    asyncio.run(monitor.poll_library())
    library = monitor.snapshot()["library"]
    assert library["ok"]
    films, shows, home = library["libraries"]
    assert (films["movies"], films["bytes"], films["files"]) == (12, 3000, 12)
    assert (shows["series"], shows["episodes"], shows["bytes"]) == (3, 40, None)
    assert home["items"] == 5 and home["bytes"] is None
    assert library["totals"] == {"movies": 12, "series": 3, "episodes": 40, "collections": 0, "bytes": 3000}
    assert library["growth"]["d7"] == 2000
    assert [i["id"] for i in library["latest"]] == [FILM, SERIES]

    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, store=store, jellyfin=jellyfin)
    app.state.monitor.library = monitor.library
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    assert c.get(f"/api/media/library/poster/{FILM}").content == PNG
    # Only recent additions, and only well-formed ids.
    assert c.get(f"/api/media/library/poster/{'9' * 32}").status_code == 404
    assert c.get("/api/media/library/poster/..%2F..%2Fetc").status_code == 404
    days = c.get("/api/media/library/history").json()["days"]
    assert [d["bytes"] for d in days] == [1000, 3000] and days[-1]["movies"] == 12


def test_folder_sizes_skip_symlinks_and_report_only_numbers(tmp_path):
    root = tmp_path / "library"
    (root / "Show" / "Season 1").mkdir(parents=True)
    (root / "Show" / "Season 1" / "e1.mkv").write_bytes(b"x" * 100)
    (root / "film.mkv").write_bytes(b"y" * 50)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"z" * 1000)
    os.symlink(outside, root / "link")
    assert measure_folder(SizedFolder(id="lib", path=str(root))) == \
        {"id": "lib", "ok": True, "error": None, "bytes": 150, "files": 2}
    missing = measure_folder(SizedFolder(id="gone", path=str(tmp_path / "nope")))
    assert missing["ok"] is False and missing["bytes"] is None

    class Docker:
        async def close(self):
            pass

    actions = ActionsConfig.model_validate({"sizes": [{"id": "lib", "path": str(root)}]})
    with TestClient(create_runner_app(actions, TOKEN, Docker(), None)) as c:  # type: ignore[arg-type]
        assert c.get("/sizes").status_code == 401
        body = c.get("/sizes", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert body == [{"id": "lib", "ok": True, "error": None, "bytes": 150, "files": 2}]
