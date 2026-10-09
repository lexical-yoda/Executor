import asyncio
import time

from fastapi.testclient import TestClient

from executor import traffic
from executor.config import Config
from executor.monitor import Monitor
from executor.recap import build_recap
from executor.store import Store
from executor.web import create_web_app

NOW = time.time()
HOUR = int(NOW // 3600 * 3600)
HOME = "192.168.0.99"
PLACES = {"203.0.113.5": {"city": "Berlin", "country_code": "DE", "lat": 52.5, "lon": 13.4},
          "203.0.113.6": {"city": "Berlin", "country_code": "DE", "lat": 52.5, "lon": 13.4},
          "198.51.100.7": {"city": "Lagos", "country_code": "NG", "lat": 6.5, "lon": 3.4}}

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "integrations": {"edge": {"traffic_url": "http://edge.example:8099/traffic.json"}},
    "machines": [], "services": [],
})


def summary(hours=(HOUR - 3600, HOUR), ssh=5):
    sites, visitors, threats, ips, tags = [], [], [], [], []
    for h in hours:
        sites += [
            {"hour": h, "site": "media.example.com", "requests": 100, "monitor": 12, "s2": 90, "s3": 4, "s4": 4,
             "s5": 2, "bytes": 10_000, "rt": [50, 30, 10, 5, 3, 1, 1, 0, 0, 0, 0, 0], "cache_hit": 3,
             "cache_total": 4, "visitors": 3},
            {"hour": h, "site": "www.example.com", "requests": 10, "monitor": 0, "s2": 10, "s3": 0, "s4": 0, "s5": 0,
             "bytes": 500, "rt": [10] + [0] * 11, "cache_hit": 0, "cache_total": 0, "visitors": 1},
        ]
        visitors += [{"hour": h, "site": "media.example.com", "ip": HOME, "n": 40},
                     {"hour": h, "site": "media.example.com", "ip": "203.0.113.5", "n": 30},
                     {"hour": h, "site": "media.example.com", "ip": "203.0.113.6", "n": 30},
                     {"hour": h, "site": "www.example.com", "ip": "198.51.100.7", "n": 10}]
        threats.append({"hour": h, "ssh": ssh, "bans": 1, "fw": 0, "scans": 2})
        ips += [{"hour": h, "ip": "198.51.100.7", "ssh": ssh, "fw": 0, "scans": 0, "banned": True},
                {"hour": h, "ip": "203.0.113.5", "ssh": 0, "fw": 0, "scans": 2, "banned": False}]
        tags += [{"hour": h, "kind": "user", "tag": "root", "n": ssh}, {"hour": h, "kind": "port", "tag": "tcp/23", "n": 1},
                 {"hour": h, "kind": "nonsense", "tag": "x", "n": 1}]
    return {"version": 1, "generated": int(NOW), "rt_buckets_ms": traffic.RT_BUCKETS, "sites": sites,
            "visitor_ips": visitors, "recent_15m": {"media.example.com": {"requests": 40, "s5": 6}},
            "threat_hours": threats, "threat_ips": ips, "threat_tags": tags, "banned_now": 2}


def store_with(tmp_path, data=None):
    store = Store(tmp_path / "executor.db")
    rows = traffic.ingest(data or summary(), PLACES.get, HOME)
    store.save_edge(rows["sites"], rows["places"], rows["threats"], rows["ips"], rows["tags"])
    return store, rows


def test_ingest_keeps_visitors_as_places_and_counts_home_traffic(tmp_path):
    store, rows = store_with(tmp_path)
    media = next(s for s in rows["sites"] if s["site"] == "media.example.com")
    assert media["own"] == 40
    # Visitors become city counts; the home address is not placed.
    assert sorted(p[2:] for p in rows["places"] if p[0] == HOUR) == [
        ("DE", "Berlin", 52.5, 13.4, 60), ("NG", "Lagos", 6.5, 3.4, 10)]
    assert all(t["kind"] in ("user", "port") for t in rows["tags"])
    # Sending the same hours again replaces them.
    store.save_edge(rows["sites"], rows["places"], rows["threats"], rows["ips"], rows["tags"])
    assert store.edge_sites(HOUR - 7200)[0]["requests"] == 200


def test_overview_percentiles_sparklines_and_threats(tmp_path):
    store, rows = store_with(tmp_path)
    view = traffic.overview(store, NOW, rows["recent"], rows["banned_now"], rows["buckets"])
    media = view["sites"][0]
    assert media["site"] == "media.example.com" and media["requests"] == 200 and media["own"] == 80
    assert (media["p50_ms"], media["p95_ms"], media["errors_pct"], media["cache_pct"]) == (5, 50, 2.0, 75)
    assert len(media["spark"]) == 25 and sum(media["spark"]) == 200
    assert media["recent"] == {"requests": 40, "s5": 6}
    assert view["totals"]["requests"] == 220 and view["countries"][0] == {"country_code": "DE", "n": 120}
    t = view["threats"]
    assert (t["ssh"], t["bans"], t["scans"], t["banned_now"], t["attackers"]) == (10, 2, 4, 2, 2)
    assert t["countries"][0]["country_code"] == "NG" and t["users"][0] == {"tag": "root", "n": 10}
    assert traffic.percentile([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5], 0.5, traffic.RT_BUCKETS) is None


def test_spike_needs_a_week_of_history_and_three_times_the_average(tmp_path):
    week = [HOUR - 3600 * i for i in range(2, 60)]
    store, _ = store_with(tmp_path, summary(hours=week, ssh=4))
    assert traffic.spike(store, NOW) is None
    rows = traffic.ingest(summary(hours=[HOUR - 3600], ssh=80), None, None)
    store.save_edge([], [], rows["threats"], rows["ips"], rows["tags"])
    count, average = traffic.spike(store, NOW)
    assert count == 82 and round(average) == 6


def test_monitor_routes_and_recap(tmp_path):
    store = Store(tmp_path / "executor.db")
    monitor = Monitor(CONFIG, None, store=store)
    asyncio.run(monitor._ingest_traffic(summary()))
    edge = monitor.snapshot()["edge"]["traffic"]
    assert edge["ok"] and edge["sites"][0]["site"] == "media.example.com" and edge["generated"] == int(NOW)

    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False, store=store)
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    site = c.get("/api/edge/sites/media.example.com?range=7d").json()
    assert site["totals"]["requests"] == 200 and site["bucket_s"] == 6 * 3600
    # Without the geolocation databases (or a known home address) visitors are counted, not placed.
    assert site["places"] == [{"country_code": "", "city": "", "lat": None, "lon": None, "n": 200}]
    assert c.get("/api/edge/sites/media.example.com?range=1y").status_code == 400
    assert c.get("/api/edge/sites/..%2Fetc?range=24h").status_code in (400, 404)
    threats = c.get("/api/edge/threats?range=24h").json()
    assert threats["totals"]["ssh"] == 10 and threats["points"] == [] and threats["sources"][0]["ip"] == "198.51.100.7"
    recap = build_recap(store, now=NOW)["edge"]
    assert recap["requests"] == 220 and recap["busiest"]["site"] == "media.example.com" and recap["attacks"] == 14
