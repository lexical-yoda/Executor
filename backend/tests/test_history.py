import asyncio
import gzip
import io
import json
import logging
import struct
import tarfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from executor.config import Config, LocationCorrection
from executor.history import MediaHistory
from executor.sources.geo import (Corrections, DbIpLite, GeoLite2, IP2LocationLite, Locator, describe,
                                  describe_ip2location, public_ip)
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


def ip2location_bin(rows, year=26, month=10, day=1):
    """A tiny IP2Location DB11 (IPv4) file: rows of (first address, country code,
    country, region, city, lat, lon), sorted, each running to the next one."""
    columns = 8  # ip_from, country, region, city, latitude, longitude, zip code, time zone
    start = 64
    rows = [*rows, ("255.255.255.255", "-", "-", "-", "-", 0, 0)]
    strings = bytearray()
    text_at = start + (len(rows) + 1) * columns * 4  # after the rows and one blank row

    def text(*values):
        at = text_at + len(strings)
        for value in values:
            strings.extend(bytes([len(value)]) + value.encode())
        return at

    body = bytearray()
    for first, code, country, region, city, lat, lon in rows:
        number = int.from_bytes(bytes(int(x) for x in first.split(".")), "big")
        body += struct.pack("<IIIIffII", number, text(code, country), text(region), text(city), lat, lon,
                            text("-"), text("-"))
    body += bytes(columns * 4)
    header = struct.pack("<BBBBBIIIIIIBBB", 11, columns, year, month, day, len(rows) - 1, start + 1, 0, 0, 0, 0,
                         1, 0, 0)
    return header.ljust(start, b"\0") + bytes(body) + bytes(strings)


IP2_ROWS = [("0.0.0.0", "-", "-", "-", "-", 0, 0),
            ("8.8.8.0", "US", "United States of America", "California", "Mountain View", 37.406, -122.0785),
            ("8.8.9.0", "-", "-", "-", "-", 0, 0),
            ("9.9.9.0", "IN", "India", "Kerala", "Porto", 9.9312, 76.2673),
            ("9.9.10.0", "-", "-", "-", "-", 0, 0)]


def zipped(name, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
        archive.writestr("LICENSE_LITE.TXT", "licence")
    return buffer.getvalue()


def test_ip2location_installs_once_a_month_and_never_logs_the_token(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    requests = []

    def handler(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, content=zipped("IP2LOCATION-LITE-DB11.BIN", ip2location_bin(IP2_ROWS)))

    geo = IP2LocationLite(tmp_path / "geo", "tok-secret", transport=httpx.MockTransport(handler))
    asyncio.run(geo.ensure(datetime(2026, 10, 13, tzinfo=timezone.utc)))
    assert requests == [{"token": "tok-secret", "file": "DB11LITEBIN"}]
    assert geo.ready and geo.version == "2026-10-01" and geo.error is None
    assert geo.lookup("9.9.9.9") == {"city": "Porto", "region": "Kerala", "country": "India", "country_code": "IN",
                                     "lat": 9.9312, "lon": 76.2673, "radius_km": None, "source": "ip2location"}
    assert geo.lookup("8.8.8.8")["city"] == "Mountain View"
    assert geo.lookup("8.8.9.1") is None  # no place for it
    assert geo.lookup("2001:4860:4860::8888") is None  # IPv4 edition: left to the next database
    assert geo.lookup("192.168.0.5") is None
    assert "tok-secret" not in caplog.text

    # This month's release is in: no more downloads until next month's download day.
    asyncio.run(geo.ensure(datetime(2026, 10, 30, tzinfo=timezone.utc)))
    day = geo.download_day("2026-11")
    assert 1 <= day <= 7
    if day > 1:
        asyncio.run(geo.ensure(datetime(2026, 11, day - 1, 12, tzinfo=timezone.utc)))
    assert len(requests) == 1
    asyncio.run(geo.ensure(datetime(2026, 11, day, 12, tzinfo=timezone.utc)))
    assert len(requests) == 2

    # A reopened folder finds the installed file and its release.
    again = IP2LocationLite(tmp_path / "geo", "tok-secret")
    assert again.ready and again.version == "2026-10-01"


def test_ip2location_refusals_wait_before_trying_again(tmp_path):
    calls = []
    replies = [httpx.Response(200, text="NO PERMISSION"),
               httpx.Response(200, content=zipped("IP2LOCATION-LITE-DB11.BIN", b"not a database")),
               httpx.Response(403)]

    def handler(request):
        calls.append(1)
        return replies[len(calls) - 1]

    geo = IP2LocationLite(tmp_path / "geo", "tok-secret", transport=httpx.MockTransport(handler))
    start = datetime(2026, 10, 13, tzinfo=timezone.utc)
    asyncio.run(geo.ensure(start))
    assert geo.error == "IP2Location refused the download: NO PERMISSION" and not geo.ready
    asyncio.run(geo.ensure(start + timedelta(hours=1)))
    assert len(calls) == 1  # waits before asking again
    asyncio.run(geo.ensure(start + timedelta(hours=13)))
    assert len(calls) == 2 and "download failed" in geo.error and not geo.ready
    assert sorted(p.name for p in (tmp_path / "geo").iterdir()) == []
    asyncio.run(geo.ensure(start + timedelta(hours=26)))
    assert geo.error == "download failed: HTTP 403" and "tok-secret" not in geo.error


def test_describe_ip2location_drops_placeholders():
    record = SimpleNamespace(country_short="IN", country_long="India", region="-", city="Porto",
                             latitude="9.931230", longitude="76.267300")
    assert describe_ip2location(record) == {"city": "Porto", "region": None, "country": "India",
                                            "country_code": "IN", "lat": 9.9312, "lon": 76.2673,
                                            "radius_km": None, "source": "ip2location"}
    assert describe_ip2location(SimpleNamespace(country_short="-", country_long="-", region="-", city="-",
                                                latitude="0.000000", longitude="0.000000")) is None
    assert describe_ip2location(SimpleNamespace(country_short="INVALID IP ADDRESS", country_long="", region="",
                                                city="", latitude="INVALID IP ADDRESS",
                                                longitude="INVALID IP ADDRESS")) is None
    assert describe_ip2location(None) is None


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
    # Machine charts still answer when media history is on (the two once shared a name).
    assert c.get("/api/machines/nas/history?range=24h").status_code == 404

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


def test_a_vague_answer_gives_way_to_a_city_inside_it():
    near = {"203.0.113.10": {"city": "Kochi", "lat": 9.93, "lon": 76.27}}
    far = {"203.0.113.10": {"city": "Delhi", "lat": 28.6, "lon": 77.2}}

    class Vague(FakeSource):
        def lookup(self, ip):
            return {"city": "Bengaluru", "lat": 12.97, "lon": 77.59, "radius_km": 500, "source": "geolite2"}

    inside = Locator([Vague(name="geolite2"), FakeSource(near, name="dbip")]).locate("203.0.113.10")
    assert inside["city"] == "Kochi" and inside["within"] == {"source": "geolite2", "km": 500}
    outside = Locator([Vague(name="geolite2"), FakeSource(far, name="dbip")]).locate("203.0.113.10")
    assert outside["city"] == "Bengaluru"


def test_household_block_pinned_devices_and_home_corrections():
    from executor.config import Household

    home = {"city": "Home", "lat": 1.0, "lon": 2.0, "source": "home"}
    pinned = LocationCorrection(city="Parents", lat=7.0, lon=8.0, user_devices=[{"user": "alice", "device": "TV box"}])
    desk = LocationCorrection(home=True, user_devices=[{"user": "alice", "device": "Desktop"}])
    geo = Locator([FakeSource()], Corrections([pinned, desk]), home, Household(users=["Alice"], prefix_v4=20))
    geo.home_ip = "203.0.113.10"
    # The server's own address is home for anyone.
    assert geo.locate("203.0.113.10", "bob", "Phone")["city"] == "Home"
    # A household user elsewhere in the same /20 block is home; someone else there is not.
    assert geo.locate("203.0.120.5", "alice", "Phone")["city"] == "Home"
    assert geo.locate("203.0.120.5", "bob", "Phone") is None  # not home; the fake databases do not know it
    assert geo.locate("198.51.100.7", "alice", "Phone")["city"] == "Dubai"
    # Pinned devices win, even from the home address; only that user's device matches.
    assert geo.locate("203.0.113.10", "alice", "tv box")["city"] == "Parents"
    assert geo.locate("198.51.100.7", "bob", "TV box")["city"] == "Dubai"
    assert geo.locate("198.51.100.7", "alice", "Desktop")["city"] == "Home"
    # A home correction needs no place of its own; any other correction does.
    import pytest

    with pytest.raises(ValueError):
        LocationCorrection(users=["alice"])
    assert "household" in geo.signature
