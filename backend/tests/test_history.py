import asyncio
import gzip
import io
import json
import tarfile
import time
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from executor.config import Config, LocationCorrection
from executor.history import MediaHistory
from executor.sources.geo import Corrections, DbIpLite, GeoLite2, Locator, describe, public_ip
from executor.sources.jellyfin import Jellyfin, parse_activity, parse_time
from executor.store import Store
from executor.web import create_web_app

ALICE = "a" * 32
BOB = "b" * 32
PLACES = {"203.0.113.10": {"city": "Porto", "region": "Kerala", "country": "India", "country_code": "IN",
                           "lat": 9.93, "lon": 76.26},
          "198.51.100.7": {"city": "Dubai", "region": "Dubai", "country": "United Arab Emirates",
                           "country_code": "AE", "lat": 25.2, "lon": 55.27}}


class FakeSource:
    """Stands in for a database: a fixed table of answers."""

    name = "dbip"
    ready = True
    version = "2026-10"
    error = None

    def __init__(self, table=None, name="dbip"):
        self.table = table if table is not None else PLACES
        self.name = name
        self.ensured = 0

    async def ensure(self):
        self.ensured += 1

    def lookup(self, ip):
        place = self.table.get(ip)
        return {**place, "radius_km": 50, "source": self.name} if place else None

    def status(self):
        return {"name": self.name, "ready": True, "version": self.version, "error": None}


def FakeGeo(corrections=()):
    return Locator([FakeSource()], Corrections(list(corrections)))


def test_public_ip_and_describe():
    assert public_ip("203.0.113.10") is None  # documentation range is reserved
    assert public_ip("8.8.8.8") == "8.8.8.8"
    assert public_ip("192.168.0.4") is None and public_ip("10.0.0.1") is None and public_ip("nope") is None
    record = {"city": {"names": {"en": "Porto"}}, "subdivisions": [{"names": {"en": "Kerala"}}],
              "country": {"iso_code": "IN", "names": {"en": "India"}},
              "location": {"latitude": 9.931233, "longitude": 76.267304}}
    assert describe(record, "geolite2") == {"city": "Porto", "region": "Kerala", "country": "India",
                                            "country_code": "IN", "lat": 9.9312, "lon": 76.2673,
                                            "radius_km": None, "source": "geolite2"}
    assert describe({"country": {"iso_code": "IN"}}) is None


def test_geo_download_refuses_a_bad_file(tmp_path):
    def handler(request):
        if "2026-10" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=gzip.compress(b"not a database"))

    geo = DbIpLite(tmp_path / "geo", transport=httpx.MockTransport(handler))
    asyncio.run(geo.ensure(datetime(2026, 10, 9, tzinfo=timezone.utc)))
    assert not geo.ready and "download failed" in geo.error
    assert not (tmp_path / "geo" / "dbip-city-lite.mmdb").exists()
    assert geo.lookup("8.8.8.8") is None


def test_geolite2_download_sends_the_account_and_checks_the_archive(tmp_path):
    seen = {}

    def tarball(member, content):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo(member)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return buffer.getvalue()

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        if seen.get("refuse"):
            return httpx.Response(401)
        return httpx.Response(200, content=tarball("GeoLite2-City_20261007/GeoLite2-City.mmdb", b"not a database"))

    geo = GeoLite2(tmp_path / "geo", "12345", "secret", transport=httpx.MockTransport(handler))
    asyncio.run(geo.ensure())
    assert seen["auth"].startswith("Basic ")
    assert not geo.ready and "download failed" in geo.error
    assert not (tmp_path / "geo" / "GeoLite2-City.mmdb").exists()
    seen["refuse"] = True
    asyncio.run(geo.ensure())
    assert geo.error == "MaxMind refused the account ID or licence key"


def test_corrections_beat_databases_and_prefer_devices():
    home = LocationCorrection(city="Home town", lat=1.0, lon=2.0, networks=["203.0.113.0/24"])
    tv = LocationCorrection(city="Cabin", lat=3.0, lon=4.0, devices=["Living Room TV"])
    always = LocationCorrection(city="Village", lat=5.0, lon=6.0, users=["Grandma"])
    geo = FakeGeo([home, tv, always])
    assert geo.locate("203.0.113.10", "alice", "Phone")["city"] == "Home town"
    assert geo.locate("203.0.113.10", "alice", "living room tv")["city"] == "Cabin"
    assert geo.locate("198.51.100.7", "grandma", "Phone")["city"] == "Village"
    plain = geo.locate("198.51.100.7", "bob", "Phone")
    assert plain["city"] == "Dubai" and plain["source"] == "dbip"
    assert geo.locate("203.0.113.10")["source"] == "correction"


def test_second_opinion_only_when_databases_disagree():
    other = {"203.0.113.10": {**PLACES["203.0.113.10"], "city": "Elsewhere"}, "198.51.100.7": PLACES["198.51.100.7"]}
    geo = Locator([FakeSource(name="geolite2"), FakeSource(other, name="dbip")])
    first = geo.locate("203.0.113.10")
    assert first["source"] == "geolite2"
    assert geo.second_opinion("203.0.113.10", first)["city"] == "Elsewhere"
    assert geo.second_opinion("198.51.100.7", geo.locate("198.51.100.7")) is None


def test_store_merges_sightings_and_purges(tmp_path):
    store = Store(tmp_path / "x.db")
    now = time.time()
    base = dict(user_id=ALICE, user_name="alice", ip="203.0.113.10", source="session", device="TV",
                geo=PLACES["203.0.113.10"])
    store.record(**base, when=now - 3600)
    store.record(**base, when=now - 1800, item="Film")       # extends
    store.record(**base, when=now + 3 * 3600)                 # after a gap: new row
    store.record(**{**base, "device": "Phone"}, when=now)     # other device: new row
    store.record(user_id=BOB, user_name="bob", ip="198.51.100.7", source="log", when=now - 40 * 86400,
                 geo=PLACES["198.51.100.7"])
    store.record(user_id=BOB, user_name="bob", ip="192.0.2.1", source="log", when=now)
    trail = store.trail(ALICE, now - 86400)
    assert len(trail) == 3
    assert trail[0]["item"] == "Film" and trail[0]["last_seen"] == now - 1800
    places = store.places(now - 90 * 86400)
    assert {p["city"] for p in places["places"]} == {"Porto", "Dubai"}
    assert places["unlocated"] == 1
    porto = next(p for p in places["places"] if p["city"] == "Porto")
    assert porto["count"] == 3 and porto["users"][0]["name"] == "alice"
    assert [u["name"] for u in store.users(now - 90 * 86400)] == ["alice", "bob"]
    assert store.places(now - 90 * 86400, BOB)["places"][0]["city"] == "Dubai"
    store.relocate("192.0.2.1", "bob", None, {**PLACES["203.0.113.10"], "source": "correction"})
    assert store.places(now - 90 * 86400)["unlocated"] == 0
    assert any(p["corrected"] for p in store.places(now - 90 * 86400)["places"])
    assert store.purge(30, now) == 1
    assert [p["city"] for p in store.places(now - 90 * 86400, BOB)["places"]] == ["Porto"]


def test_activity_parsing():
    assert parse_time("2026-10-07T19:41:53.9238851Z") == datetime(2026, 10, 7, 19, 41, 53, 923885,
                                                                  tzinfo=timezone.utc).timestamp()
    entry = {"Id": 9, "Type": "SessionStarted", "UserId": ALICE, "Date": "2026-10-07T19:41:53.9238851Z",
             "Name": "alice is online from Android TV", "ShortOverview": "IP address: 203.0.113.10"}
    assert parse_activity(entry) == {"id": 9, "user_id": ALICE, "ip": "203.0.113.10",
                                     "when": parse_time(entry["Date"]), "device": "Android TV"}
    assert parse_activity({**entry, "Name": "alice is online from Alice%27s+phone"})["device"] == "Alice's phone"
    assert parse_activity({**entry, "Type": "VideoPlayback"}) is None
    assert parse_activity({**entry, "ShortOverview": "IP address: "}) is None


def jellyfin_handler(now: float):
    stamp = datetime.fromtimestamp(now - 60, timezone.utc).isoformat().replace("+00:00", "Z")
    old = datetime.fromtimestamp(now - 7200, timezone.utc).isoformat().replace("+00:00", "Z")
    log = [
        {"Id": 3, "Type": "SessionStarted", "UserId": BOB, "Date": old, "Name": "bob is online from Chrome",
         "ShortOverview": "IP address: 198.51.100.7"},
        {"Id": 2, "Type": "VideoPlayback", "UserId": BOB, "Date": old, "Name": "bob is playing X"},
        {"Id": 1, "Type": "SessionStarted", "UserId": ALICE, "Date": old, "Name": "alice is online from TV",
         "ShortOverview": "IP address: 192.168.0.20"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Sessions":
            return httpx.Response(200, json=[
                {"UserId": ALICE, "UserName": "alice", "RemoteEndPoint": "203.0.113.10", "DeviceName": "TV",
                 "Client": "Android TV", "LastActivityDate": stamp,
                 "NowPlayingItem": {"Name": "Film", "Type": "Movie", "ProductionYear": 1999,
                                    "RunTimeTicks": 72_000_000_000},
                 "PlayState": {"PositionTicks": 36_000_000_000, "PlayMethod": "Transcode"}},
                {"UserId": BOB, "UserName": "bob", "RemoteEndPoint": "10.0.0.5", "LastActivityDate": stamp},
            ])
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": ALICE, "Name": "alice"}, {"Id": BOB, "Name": "bob"}])
        if path == "/System/ActivityLog/Entries":
            return httpx.Response(200, json={"Items": log, "TotalRecordCount": len(log)})
        return httpx.Response(404)

    return handler


def test_sampling_and_log_import(tmp_path, monkeypatch):
    now = time.time()
    jf = Jellyfin("http://media.example", "k", transport=httpx.MockTransport(jellyfin_handler(now)))
    store = Store(tmp_path / "x.db")
    source = FakeSource()
    geo = Locator([source])
    history = MediaHistory(jf, store, geo, 90)

    # The documentation addresses used here count as non-public, so treat them as public for this test.
    import executor.history as module
    monkeypatch.setattr(module, "public_ip",
                        lambda ip: ip if ip and not ip.startswith(("10.", "192.168.")) else None)

    watching = asyncio.run(history.sample(now))
    assert len(watching) == 1
    assert watching[0]["location"]["city"] == "Porto" and watching[0]["progress"] == 0.5
    assert watching[0]["transcoding"] is True
    asyncio.run(history.maintain(now))
    assert source.ensured == 1
    assert store.get_state("geo_signature") == geo.signature
    assert store.get_state("activity_last_id") == "3"
    users = {u["name"]: u for u in store.users(now - 86400)}
    assert set(users) == {"alice", "bob"}  # bob from the log; his private-address session is skipped
    assert store.trail(ALICE, now - 86400)[0]["item"] == "Film (1999)"
    asyncio.run(history.maintain(now))
    assert store.count() == 2  # nothing imported twice
    assert history.status()["sightings"] == 2


CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"jellyfin": {"url": "http://media.example", "hub": {"label": "Hub", "lat": 12.9, "lon": 77.6},
                                 "origin": {"label": "Home", "lat": 9.9, "lon": 76.3}}},
    "machines": [], "services": [],
})


def test_history_endpoints(tmp_path):
    store = Store(tmp_path / "x.db")
    now = time.time()
    store.record(user_id=ALICE, user_name="alice", ip="203.0.113.10", source="log", when=now,
                 geo=PLACES["203.0.113.10"])
    jf = Jellyfin("http://media.example", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    history = MediaHistory(jf, store, FakeGeo(), 90)
    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, history=history)
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    assert c.get("/api/media/users").json()["users"][0]["name"] == "alice"
    places = c.get(f"/api/media/places?user={ALICE}&days=30").json()
    assert places["places"][0]["city"] == "Porto"
    trail = c.get(f"/api/media/trail?user={ALICE}").json()
    assert trail["sightings"][0]["ip"] == "203.0.113.10"
    assert c.get("/api/media/trail?user=../../etc").status_code == 400
    snapshot = c.get("/api/status").json()["jellyfin"]
    assert snapshot["hub"]["label"] == "Hub" and snapshot["origin"]["label"] == "Home"
    assert snapshot["history"]["enabled"] is True

    plain = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False)
    c = TestClient(plain, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    assert c.get("/api/media/users").status_code == 404
    assert c.get("/api/status").json()["jellyfin"] is None


def test_sessions_from_the_servers_own_address_are_home(tmp_path, monkeypatch):
    now = time.time()
    store = Store(tmp_path / "x.db")
    store.record(user_id=ALICE, user_name="alice", ip="8.8.4.4", source="session", when=now - 600,
                 geo={**PLACES["198.51.100.7"], "source": "dbip"})
    home = {"city": "Home", "lat": 1.0, "lon": 2.0, "source": "home"}
    geo = Locator([FakeSource()], Corrections([]), home)
    jf = Jellyfin("http://media.example", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    history = MediaHistory(jf, store, geo, 90, "https://whoami.example/")

    class Answer:
        # Documentation addresses count as non-public, so use a real public one.
        text = "8.8.4.4\n"

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            assert url == "https://whoami.example/"
            return Answer()

    import executor.history as module
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    asyncio.run(history._check_home(now))
    assert geo.home_ip == "8.8.4.4"
    assert geo.locate("8.8.4.4")["city"] == "Home"
    trail = store.trail(ALICE, now - 86400)
    assert trail[0]["city"] == "Home" and trail[0]["geo_source"] == "home"
    # Relocating history leaves home sightings alone.
    store.relocate("8.8.4.4", "alice", None, {**PLACES["198.51.100.7"], "source": "dbip"})
    assert store.trail(ALICE, now - 86400)[0]["city"] == "Home"
