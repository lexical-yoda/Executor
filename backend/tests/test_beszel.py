import asyncio
import json

import httpx
import pytest

from executor.sources.beszel import Beszel, BeszelError, summarize, to_series

SYSTEM = {"id": "abc123", "name": "NAS", "status": "up",
          "info": {"cpu": 2.0, "mp": 40.0, "dp": 5.0, "u": 3600, "la": [0.5, 0.4, 0.3], "t": 12, "dt": 50}}

STATS = {
    "cpu": 2.0, "m": 31.2, "mu": 13.6, "mp": 43.6, "mz": 11.5, "d": 818.0, "du": 33.0, "dp": 4.0,
    "t": {"k10temp_tctl": 48.0, "drivetemp": 44.0, "drivetemp_2": 38.0, "nvme_composite": 40.0, "GPU X": 49.0},
    "g": {"0": {"n": "GPU X", "u": 12.0, "mu": 100.0, "mt": 4000.0, "p": 5.0}},
    "ni": {"eth0": [1000, 2000, 0, 0], "wg1": [900, 100, 0, 0]},
    "z": {"tank": {"d": 1000.0, "du": 820.0, "h": "ONLINE"}, "apps": {"d": 900.0, "du": 90.0, "h": "ONLINE"}},
}


def test_summarize_maps_fields_and_ignores_tunnels():
    s = summarize(SYSTEM, STATS)
    assert s["cpu_pct"] == 2.0 and s["mem_pct"] == 43.6 and s["threads"] == 12
    assert s["cpu_temp"] == 48.0
    assert s["gpu_temp"] == 49.0 and s["gpus"][0]["util_pct"] == 12.0
    assert s["drive_temp_max"] == 44.0
    # WireGuard traffic also crosses the physical NIC, so it is not added twice.
    assert (s["net_tx_bps"], s["net_rx_bps"]) == (1000, 2000)
    assert [p["name"] for p in s["pools"]] == ["apps", "tank"]
    assert s["pools"][1]["pct"] == 82.0 and s["pools"][1]["health"] == "ONLINE"


def test_summarize_without_stats_uses_system_info():
    s = summarize(SYSTEM, None)
    assert s["cpu_pct"] == 2.0 and s["mem_pct"] == 40.0 and s["pools"] == [] and s["net_tx_bps"] is None


def test_to_series_builds_columns():
    items = [{"created": "2026-10-09 07:00:00.000Z", "stats": STATS},
             {"created": "2026-10-09 07:01:00.000Z", "stats": {**STATS, "cpu": 5.0}}]
    series = to_series(items)
    assert series["t"][1] - series["t"][0] == 60
    assert series["cpu"] == [2.0, 5.0]
    assert series["pools"]["tank"] == [82.0, 82.0]
    assert series["gpu"] == [12.0, 12.0]


def test_to_series_drops_empty_columns():
    series = to_series([{"created": "2026-10-09 07:00:00Z", "stats": {"cpu": 1.0}}])
    assert series["gpu"] is None and series["net_tx"] is None and series["pools"] == {}


class FakeHub:
    def __init__(self):
        self.logins = 0
        self.expire_next = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth-with-password"):
            body = json.loads(request.content)
            if body["password"] != "secret":
                return httpx.Response(400, json={"message": "bad"})
            self.logins += 1
            return httpx.Response(200, json={"token": f"tok{self.logins}"})
        if not request.headers.get("authorization", "").startswith("tok"):
            return httpx.Response(401)
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401)
        if request.url.path.endswith("/systems/records"):
            return httpx.Response(200, json={"items": [SYSTEM]})
        if request.url.path.endswith("/system_stats/records"):
            return httpx.Response(200, json={"items": [
                {"system": "abc123", "created": "2026-10-09 07:01:00Z", "stats": STATS},
                {"system": "abc123", "created": "2026-10-09 07:00:00Z", "stats": {"cpu": 1.0}},
            ]})
        return httpx.Response(404)


def run(coro):
    return asyncio.run(coro)


def test_client_logs_in_and_reads():
    hub = FakeHub()
    client = Beszel("http://hub", "ro@example.com", "secret", transport=httpx.MockTransport(hub.handler))

    async def go():
        systems = await client.systems()
        latest = await client.latest()
        await client.close()
        return systems, latest

    systems, latest = run(go())
    assert list(systems) == ["NAS"]
    assert latest["abc123"]["created"] == "2026-10-09 07:01:00Z"
    assert hub.logins == 1


def test_client_relogs_in_once_when_token_expires():
    hub = FakeHub()
    client = Beszel("http://hub", "ro@example.com", "secret", transport=httpx.MockTransport(hub.handler))

    async def go():
        await client.systems()
        hub.expire_next = True
        await client.systems()
        await client.close()

    run(go())
    assert hub.logins == 2


def test_bad_password_raises():
    hub = FakeHub()
    client = Beszel("http://hub", "ro@example.com", "wrong", transport=httpx.MockTransport(hub.handler))
    with pytest.raises(BeszelError):
        run(client.systems())


def test_system_id_is_validated():
    client = Beszel("http://hub", "a", "b", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(BeszelError):
        run(client.records("x' || 1=1", "1m", 60))
