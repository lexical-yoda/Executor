import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from executor.config import Config
from executor.events import Tracker, uptime_seconds
from executor.history import MediaHistory
from executor.recap import build_recap
from executor.sources.jellyfin import Jellyfin, describe_library_item, parse_playback
from executor.store import Store
from executor.web import create_web_app

ALICE = "a" * 32

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "machines": [{"id": "nas", "name": "NAS", "role": "Storage"}],
    "services": [{"id": "web", "name": "Web", "group": "Apps"}],
})


def client(app):
    return TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))


def service(status, error=None):
    return {"id": "web", "name": "Web", "status": status, "error": error, "containers": []}


def test_service_changes_need_two_checks_and_the_first_sight_is_silent():
    tracker = Tracker()
    tracker.services([service("up")], now=0)
    tracker.services([service("down", "timed out")], now=20)
    assert tracker.recent() == []  # one bad check is not an outage
    tracker.services([service("down", "timed out")], now=40)
    tracker.services([service("up")], now=100)
    tracker.services([service("up")], now=120)
    titles = [e["title"] for e in tracker.recent()]
    assert titles == ["Web is back up", "Web is down"]
    assert tracker.recent()[0]["detail"] == "after 1m"


def test_container_restarts_and_stops():
    tracker = Tracker()
    watched = {"app", "db"}
    tracker.containers({"app": {"state": "running", "status": "Up 3 hours"},
                        "db": {"state": "running", "status": "Up 3 hours (healthy)", "health": "healthy"},
                        "other": {"state": "running", "status": "Up 1 hour"}}, watched)
    tracker.containers({"app": {"state": "running", "status": "Up 5 seconds"},
                        "db": {"state": "exited", "status": "Exited (1) 2 seconds ago"}}, watched)
    titles = {e["title"] for e in tracker.recent()}
    assert titles == {"app restarted", "db stopped"}
    assert uptime_seconds("Up About an hour (healthy)") == 3600
    assert uptime_seconds("Exited (0) 5 minutes ago") is None


def test_downloads_group_a_season_and_ignore_new_baselines():
    tracker = Tracker()
    tracker.downloads([])
    tracker.downloads([{"id": f"s-{i}", "title": "Show", "subtitle": f"S01E0{i}", "size": 2**30, "progress": 0.1}
                       for i in range(1, 4)])
    tracker.downloads([{"id": "s-1", "title": "Show", "subtitle": "S01E01", "size": 2**30, "progress": 1.0}])
    tracker.downloads([])
    events = tracker.recent()
    assert events[0]["title"] == "Downloaded Show S01E01"
    assert events[1]["title"] == "Grabbed Show" and events[1]["detail"] == "3 items · 3.0 GiB"


def test_backups_runs_streams_and_requests():
    tracker = Tracker()
    job = {"id": "1", "name": "Glacier", "status": "ok", "last_finished": 100, "last_result": "Success",
           "last_duration_s": 600, "history": [{"added_bytes": 2**20}]}
    tracker.backups([job], [])
    tracker.backups([{**job, "last_finished": 200}], [])
    tracker.runs([])
    tracker.runs([{"id": "r1", "title": "Reset", "status": "running"}])
    tracker.runs([{"id": "r1", "title": "Reset", "status": "failed", "error": "step 2: boom"}])
    tracker.streams([])
    tracker.streams([{"user": "alice", "user_id": ALICE, "device": "TV", "title": "Film",
                      "location": {"city": "Lisbon", "country_code": "PT"}}])
    tracker.requests([], [])
    tracker.requests([{"id": 7, "title": "Dune", "year": 2021, "kind": "movie", "requested_by": "bob"}], [])
    events = tracker.recent()
    by_title = {e["title"]: e for e in events}
    assert by_title["Backup 'Glacier' finished"]["detail"] == "+1.0 MiB · took 10m"
    assert by_title["Action 'Reset' failed"]["detail"] == "step 2: boom"
    assert by_title["started watching Film"]["actor"] == "alice"
    assert by_title["started watching Film"]["detail"] == "TV · Lisbon, PT"
    assert by_title["requested Dune (2021)"]["actor"] == "bob"


def test_store_checks_machine_hours_and_events(tmp_path):
    store = Store(tmp_path / "x.db")
    now = 1_800_000_000.0
    store.record_checks([("web", "up", 12.0)], when=now)
    store.record_checks([("web", "down", None)], when=now + 30)
    store.record_checks([("web", "up", 8.0)], when=now + 400)
    history = store.check_history("web", now - 3600, 300)
    assert [(b["up"], b["down"]) for b in history] == [(1, 1), (1, 0)]
    assert history[0]["ms"] == 12.0
    assert round(store.uptime(now - 3600, now + 3600)["web"], 1) == 66.7

    store.add_machine_sample("nas", {"cpu": 10, "pools": {"tank": 50}}, when=now)
    store.add_machine_sample("nas", {"cpu": 30, "pools": {"tank": 52}}, when=now + 60)
    assert store.backfill_machine("nas", [(now, {"cpu": 99}), (now - 7200, {"cpu": 40})]) == 1
    series = store.machine_series("nas", now - 86400, 3600)
    assert series["cpu"][-1] == 20 and series["pools"]["tank"][-1] == 51
    assert series["cpu"][0] == 40 and series["gpu"] is None

    store.add_event(kind="service", level="bad", title="Web is down", when=now)
    assert store.event_counts(now - 1, now + 1) == {"service": {"bad": 1}}
    assert store.events(10)[0]["title"] == "Web is down"


def test_playback_log_and_recap(tmp_path):
    start = {"Id": 10, "Type": "VideoPlayback", "UserId": ALICE, "ItemId": "i1",
             "Name": "alice is playing The Office - Test the Store on Living room TV",
             "Date": "2026-10-09T10:00:00.0000000Z"}
    stop = {**start, "Id": 11, "Type": "VideoPlaybackStopped",
            "Name": "alice has finished playing The Office - Test the Store on Living room TV",
            "Date": "2026-10-09T10:45:00.0000000Z"}
    parsed = parse_playback(start)
    assert parsed["label"] == "The Office - Test the Store" and parsed["device"] == "Living room TV"
    assert parse_playback({"Type": "SessionStarted", "UserId": ALICE}) is None
    item = describe_library_item({"Id": "i1", "Type": "Episode", "SeriesName": "The Office", "Name": "Test the Store",
                                  "ParentIndexNumber": 7, "IndexNumber": 8, "RunTimeTicks": 13_200_000_000})
    assert item == {"id": "i1", "title": "The Office", "episode": "S07E08 · Test the Store", "kind": "Episode",
                    "year": None, "runtime_s": 1320}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": ALICE, "Name": "alice"}])
        if path == "/System/ActivityLog/Entries":
            return httpx.Response(200, json={"Items": [stop, start]})
        if path == "/Items":
            return httpx.Response(200, json={"Items": [{"Id": "i1", "Type": "Episode", "SeriesName": "The Office",
                                                        "Name": "Test the Store"}]})
        return httpx.Response(404)

    store = Store(tmp_path / "x.db")
    store.record(user_id=ALICE, user_name="alice", ip="8.8.8.8", source="log", when=parsed["when"] - 60,
                 geo={"city": "Lisbon", "country_code": "PT", "lat": 38.7, "lon": -9.1})
    jf = Jellyfin("http://media.example", "k", transport=httpx.MockTransport(handler))
    history = MediaHistory(jf, store, None, 90)
    now = parsed["when"] + 3600
    asyncio.run(history.maintain(now))
    plays = store.plays(now - 86400, now, now)
    assert len(plays) == 1 and plays[0]["seconds"] == 2700 and plays[0]["title"] == "The Office"
    assert plays[0]["city"] == "Lisbon"
    asyncio.run(history.maintain(now))
    assert len(store.plays(now - 86400, now, now)) == 1  # nothing imported twice

    store.add_daily(time.strftime("%Y-%m-%d", time.localtime(now)), "download_bytes", 5e9)
    recap = build_recap(store, now=now, days=7)
    assert recap["media"]["plays"] == 1 and recap["media"]["hours"] == 0.8
    assert recap["media"]["top_titles"][0] == {"title": "The Office", "kind": "Episode", "hours": 0.8, "plays": 1,
                                               "viewers": 1}
    assert recap["media"]["top_places"][0]["city"] == "Lisbon"
    assert recap["downloads"]["bytes"] == 5e9
    assert recap["media"]["longest"]["name"] == "alice"


def test_ledger_routes_and_tiles(tmp_path):
    store = Store(tmp_path / "x.db")
    store.record_checks([("web", "up", 5.0)])
    store.add_event(kind="system", level="info", title="Hello")
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    (tiles / "world.pmtiles").write_bytes(bytes(range(256)) * 4)
    (tiles / "india.pmtiles").write_bytes(b"x" * 10)
    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, store=store, tiles_dir=tiles)
    c = client(app)
    assert c.get("/api/events").json()["events"][0]["title"] == "Hello"
    body = c.get("/api/services/web/history?hours=24").json()
    assert body["uptime"] == 100.0 and body["bucket_s"] == 300
    assert c.get("/api/services/nope/history").status_code == 404
    assert c.get("/api/recap").json()["media"]["plays"] == 0
    sets = c.get("/api/map/tilesets").json()["tilesets"]
    assert [t["name"] for t in sets] == ["world", "india"]
    part = c.get("/tiles/world.pmtiles", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == bytes([2, 3, 4, 5])
    assert c.get("/tiles/..%2Fx.pmtiles").status_code == 404
    assert c.get("/tiles/missing.pmtiles").status_code == 404
    assert c.get("/api/machines/nas/history?range=1y").json()["t"] == []

    bare = client(create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False))
    assert bare.get("/api/recap").status_code == 404
    assert bare.get("/api/events").json()["events"] == []  # kept in memory without a data folder
    assert bare.get("/api/map/tilesets").json()["tilesets"] == []
