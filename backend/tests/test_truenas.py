import asyncio
import json

import pytest

from executor.config import Config
from executor.events import Tracker
from executor.monitor import Monitor
from executor.sources.truenas import TrueNAS

TiB = 2**40

POOL = {
    "name": "tank", "status": "ONLINE", "healthy": True, "warning": False, "status_detail": None,
    "size": 10 * TiB, "allocated": 4 * TiB, "free": 6 * TiB, "fragmentation": "3",
    "scan": {"function": "SCRUB", "state": "FINISHED", "start_time": {"$date": 1_700_000_000_000},
             "end_time": {"$date": 1_700_003_600_000}, "errors": 0, "percentage": 100.0},
    "topology": {"data": [{"type": "MIRROR", "status": "ONLINE", "children": [
        {"disk": "sdb", "status": "ONLINE", "stats": {"read_errors": 0, "write_errors": 0, "checksum_errors": 2}},
        {"disk": "sda", "status": "ONLINE", "stats": {}}]}], "cache": [], "log": []},
}


def responder(forbidden=(), key_ok=True, pools=None):
    def answer(method, params):
        if method in forbidden:
            return {"error": {"message": "Not authorized", "data": {"errname": "EACCES"}}}
        results = {
            "auth.login_ex": {"response_type": "SUCCESS" if key_ok else "AUTH_ERR"},
            "auth.login_with_api_key": key_ok,
            "system.info": {"version": "25.10.3", "uptime_seconds": 3600, "hostname": "nas"},
            "pool.query": [POOL] if pools is None else pools,
            "disk.query": [{"name": "sda", "model": "HDD 4T", "type": "HDD", "size": 4 * 10**12, "rotationrate": 5400},
                           {"name": "sdb", "model": "HDD 4T", "type": "HDD", "size": 4 * 10**12, "rotationrate": 5400},
                           {"name": "nvme0n1", "model": "SSD", "type": "SSD", "size": 5 * 10**11, "rotationrate": None}],
            "disk.temperatures": {"sda": 36.0, "sdb": 52.0, "nvme0n1": 41.5},
            "disk.temperature_agg": {"sda": {"min": 34, "max": 38, "avg": 36.2}},
            "alert.list": [
                {"uuid": "u1", "level": "INFO", "klass": "HasUpdate", "formatted": "An <b>update</b> is available.",
                 "datetime": {"$date": 1_700_000_000_000}, "dismissed": False},
                {"uuid": "u2", "level": "WARNING", "klass": "SMART", "formatted": "Disk sdb is failing",
                 "datetime": {"$date": 1_700_000_000_000}, "dismissed": False},
                {"uuid": "u3", "level": "CRITICAL", "klass": "Old", "formatted": "gone", "dismissed": True},
            ],
            "pool.dataset.query": [
                {"name": "tank", "used": {"parsed": 3 * TiB}, "available": {"parsed": 3 * TiB}},
                {"name": "tank/media", "used": {"parsed": 3 * TiB}, "available": {"parsed": 6 * TiB}},
                {"name": "tank/ix-apps", "used": {"parsed": 1}},
                {"name": "tank/media/films", "used": {"parsed": 2 * TiB}},
            ],
        }
        return {"result": results[method]}
    return answer


class FakeSocket:
    def __init__(self, answer, log):
        self.answer, self.log, self.replies = answer, log, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        call = json.loads(text)
        self.log.append(call)
        # A notification first, as TrueNAS may send between replies.
        self.replies += [{"jsonrpc": "2.0", "method": "collection_update"},
                         {"jsonrpc": "2.0", "id": call["id"], **self.answer(call["method"], call["params"])}]

    async def recv(self):
        return json.dumps(self.replies.pop(0))


def client(answer, username="executor", log=None):
    log = [] if log is None else log
    return TrueNAS("wss://nas.example/api/current", "secret-key", username,
                   connector=lambda url, **kw: FakeSocket(answer, log)), log


def test_status_reads_pools_disks_alerts_and_datasets():
    nas, log = client(responder())
    health = asyncio.run(nas.status())
    assert log[0]["method"] == "auth.login_ex" and log[0]["params"][0]["api_key"] == "secret-key"
    pool = health["pools"][0]
    # Usable space from the root dataset, not the raw size that counts parity.
    assert (pool["size"], pool["free"], pool["pct"], pool["raw_size"]) == (6 * TiB, 3 * TiB, 50.0, 10 * TiB)
    assert (pool["fragmentation"], pool["scan"]["errors"], pool["scan"]["finished"]) == (3, 0, 1_700_003_600)
    assert pool["vdevs"] == [{"role": "data", "type": "MIRROR", "status": "ONLINE", "disks": ["sdb", "sda"],
                              "errors": 2}]
    assert [(d["name"], d["pool"], d["temp"]) for d in health["disks"]] == \
        [("sda", "tank", 36.0), ("sdb", "tank", 52.0), ("nvme0n1", None, 41.5)]
    assert health["disks"][0]["temp_max_7d"] == 38 and health["disks"][0]["temp_avg_7d"] == 36.2
    assert [a["klass"] for a in health["alerts"]] == ["SMART", "HasUpdate"]
    assert health["alerts"][1]["text"] == "An update is available."
    assert health["system"] == {"version": "25.10.3", "uptime_s": 3600, "update": True}
    assert health["datasets"] == [{"name": "tank/media", "pool": "tank", "used": 3 * TiB, "available": 6 * TiB}]


def test_missing_permissions_and_refused_keys():
    nas, _ = client(responder(forbidden=("alert.list", "disk.temperature_agg")))
    health = asyncio.run(nas.status())
    assert health["alerts"] is None and health["disks"][0]["temp_max_7d"] is None
    with pytest.raises(RuntimeError, match="cannot read pools"):
        asyncio.run(client(responder(forbidden=("pool.query",)))[0].status())
    with pytest.raises(RuntimeError, match="refused the API key"):
        asyncio.run(client(responder(key_ok=False))[0].status())
    legacy, log = client(responder(), username=None)
    asyncio.run(legacy.status())
    assert log[0]["method"] == "auth.login_with_api_key"
    with pytest.raises(ValueError, match="wss://"):
        TrueNAS("ws://nas.example/api/current", "k")
    with pytest.raises(ValueError):
        Config.model_validate({"security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["x"]},
                               "integrations": {"truenas": {"url": "https://nas.example"}},
                               "machines": [], "services": []})


def test_tracker_reports_pool_changes_scrubs_and_new_alerts():
    tracker = Tracker()
    pool = {"name": "tank", "status": "ONLINE", "healthy": True, "detail": None,
            "scan": {"function": "SCRUB", "state": "FINISHED", "finished": 100, "errors": 0}}
    alert = {"id": "u1", "level": "INFO", "klass": "HasUpdate", "text": "update"}
    tracker.storage({"pools": [pool], "alerts": [alert]}, now=1)
    assert tracker.recent() == []
    scrubbed = {**pool, "scan": {**pool["scan"], "finished": 200, "errors": 3}}
    degraded = {**scrubbed, "status": "DEGRADED", "healthy": False, "detail": "One or more devices faulted"}
    tracker.storage({"pools": [degraded], "alerts": [alert, {"id": "u2", "level": "CRITICAL", "klass": "SMART",
                                                             "text": "sdb failing"}]}, now=2)
    titles = [(e["level"], e["title"], e["detail"]) for e in tracker.recent()]
    assert titles == [("bad", "TrueNAS: SMART", "sdb failing"), ("bad", "Scrub of tank finished", "3 errors"),
                      ("bad", "Pool tank is degraded", "One or more devices faulted")]


def test_monitor_section():
    config = Config.model_validate({
        "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
        "integrations": {"truenas": {"url": "wss://nas.example/api/current"}}, "machines": [], "services": []})
    monitor = Monitor(config, None, truenas=client(responder())[0])
    assert monitor.snapshot()["truenas"]["ok"] is False
    asyncio.run(monitor.poll_truenas())
    section = monitor.snapshot()["truenas"]
    assert section["ok"] and section["pools"][0]["name"] == "tank" and section["configured"]
    assert Monitor(config, None).snapshot()["truenas"]["error"] == "TRUENAS_API_KEY not set"
