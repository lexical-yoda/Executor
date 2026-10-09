import asyncio

import httpx
import pytest

from executor.config import Config
from executor.events import Tracker
from executor.monitor import Monitor
from executor.sources.pihole import PiHole

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"pihole": {"url": "http://dns.example:9090"}},
    "machines": [], "services": [],
})


def handler_for(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth" and request.method == "POST":
            state["logins"] = state.get("logins", 0) + 1
            if request.read() != b'{"password":"app-pass"}':
                return httpx.Response(401, json={"session": {"valid": False, "sid": None}})
            return httpx.Response(200, json={"session": {"valid": True, "sid": f"sid{state['logins']}"}})
        if path == "/api/auth" and request.method == "DELETE":
            state["logged_out"] = request.headers.get("x-ftl-sid")
            return httpx.Response(204)
        if request.headers.get("x-ftl-sid") != f"sid{state.get('logins')}" or state.pop("expire", False):
            return httpx.Response(401, json={"error": {"key": "unauthorized"}})
        if path == "/api/stats/summary":
            return httpx.Response(200, json={
                "queries": {"total": 1000, "blocked": 250, "percent_blocked": 25.04, "cached": 300, "forwarded": 450,
                            "unique_domains": 120},
                "clients": {"active": 7, "total": 9},
                "gravity": {"domains_being_blocked": 150000, "last_update": 1_700_000_000}})
        if path == "/api/history":
            return httpx.Response(200, json={"history": [{"timestamp": 1_700_000_000, "total": 10, "blocked": 3,
                                                          "cached": 1, "forwarded": 6}]})
        if path == "/api/stats/top_domains":
            assert request.url.params["blocked"] == "true"
            return httpx.Response(200, json={"domains": [{"domain": "ads.example", "count": 90}]})
        if path == "/api/stats/top_clients":
            return httpx.Response(200, json={"clients": [{"ip": "192.168.0.5", "name": "phone.lan", "count": 400},
                                                         {"ip": "192.168.0.6", "name": "", "count": 10}]})
        if path == "/api/dns/blocking":
            return httpx.Response(200, json={"blocking": state.get("blocking", "enabled"), "timer": None})
        if path == "/api/info/version":
            return httpx.Response(403)
        return httpx.Response(404)
    return handler


def client(state, password="app-pass"):
    return PiHole("http://dns.example:9090", password, transport=httpx.MockTransport(handler_for(state)))


def test_status_logs_in_once_and_relogs_when_the_session_expires():
    state: dict = {}

    async def check():
        pihole = client(state)
        first = await pihole.status()
        assert (first["queries"], first["blocked"], first["pct_blocked"], first["clients_active"]) == (1000, 250, 25.0, 7)
        assert first["history"] == [{"t": 1_700_000_000, "total": 10, "blocked": 3}]
        assert first["top_blocked"] == [{"domain": "ads.example", "count": 90}]
        assert [c["name"] for c in first["top_clients"]] == ["phone.lan", "192.168.0.6"]
        assert first["blocking"] == "enabled" and first["version"] is None
        await pihole.status()
        assert state["logins"] == 1
        state["expire"] = True
        await pihole.status()
        assert state["logins"] == 2
        await pihole.close()
        assert state["logged_out"] == "sid2"
    asyncio.run(check())


def test_wrong_password_is_reported():
    with pytest.raises(RuntimeError, match="refused the password"):
        asyncio.run(client({}, password="wrong").status())


def test_blocking_events_and_monitor_section():
    tracker = Tracker()
    tracker.dns({"blocking": "enabled"}, now=1)
    tracker.dns({"blocking": "disabled", "blocking_timer": 300}, now=2)
    tracker.dns({"blocking": "enabled"}, now=3)
    assert [(e["title"], e["detail"]) for e in tracker.recent()] == [
        ("Pi-hole is blocking ads again", None), ("Pi-hole stopped blocking ads", "Back on in 5m")]

    state: dict = {}
    monitor = Monitor(CONFIG, None, pihole=client(state))
    asyncio.run(monitor.poll_pihole())
    section = monitor.snapshot()["pihole"]
    assert section["ok"] and section["queries"] == 1000 and section["configured"]
    assert Monitor(CONFIG, None).snapshot()["pihole"]["error"] == "PIHOLE_PASSWORD not set"
