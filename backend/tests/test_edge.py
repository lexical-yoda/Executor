import asyncio
from datetime import datetime, timezone

import httpx

from executor.sources.edge import describe_certificate, fetch_bandwidth, parse_not_after, summarize_bandwidth

START, END = 1_790_812_800, 1_793_491_200  # 1 Oct to 1 Nov 2026 (UTC)
SAMPLE = {
    "fetched_at": START + 600_000,
    "errors": {},
    "instance": {"label": "vps", "plan": "p", "region": "r"},
    "account": {
        "current_month_to_date": {"gb_in": 432, "gb_out": 400, "instance_bandwidth_credits": 650,
                                  "free_bandwidth_credits": 2048, "purchased_bandwidth_credits": 0,
                                  "overage": 0, "overage_cost": 0, "timestamp_start": str(START)},
        "current_month_projected": {"gb_out": 400, "instance_bandwidth_credits": 2318,
                                    "free_bandwidth_credits": 2048, "purchased_bandwidth_credits": 0,
                                    "timestamp_start": str(START), "timestamp_end": str(END)},
        "previous_month": {"gb_in": 904, "gb_out": 848, "instance_bandwidth_credits": 2048,
                           "free_bandwidth_credits": 2048, "purchased_bandwidth_credits": 0},
    },
    "daily": {"2026-10-08": {"incoming_bytes": 30_000_000_000, "outgoing_bytes": 28_000_000_000},
              "2026-10-07": {"incoming_bytes": 36_000_000, "outgoing_bytes": 20_000_000}},
}


def test_bandwidth_projection_and_allowance():
    halfway = START + (END - START) / 2
    s = summarize_bandwidth(SAMPLE, now=halfway)
    assert s["out_gb"] == 400 and s["in_gb"] == 432
    assert s["allowance_now_gb"] == 2698 and s["allowance_month_gb"] == 4366
    assert s["elapsed_pct"] == 50.0
    assert s["projected_out_gb"] == 800.0          # linear: twice the halfway figure
    assert s["projected_pct"] == round(800 / 4366 * 100, 1)
    assert s["previous"]["out_gb"] == 848
    assert [d["date"] for d in s["daily"]] == ["2026-10-07", "2026-10-08"]
    assert s["daily"][1]["out_gb"] == 28.0


def test_bandwidth_with_missing_account_block():
    s = summarize_bandwidth({"fetched_at": 1, "errors": {"account": "HTTP 403"}, "daily": {}}, now=100)
    assert s["out_gb"] is None and s["projected_out_gb"] is None and s["errors"]["account"] == "HTTP 403"


def test_fetch_bandwidth_over_http():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE))

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_bandwidth("http://vps:8099/bandwidth.json", client)

    assert asyncio.run(go())["out_gb"] == 400


def test_certificate_description():
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    cert = {"notAfter": "Dec 10 12:00:00 2026 GMT",
            "issuer": ((("countryName", "US"),), (("organizationName", "Let's Encrypt"),), (("commonName", "E6"),))}
    d = describe_certificate("media.example.com", cert, now=now)
    assert d["days_left"] == 10.5 and d["issuer"] == "Let's Encrypt"
    assert parse_not_after("Dec 10 12:00:00 2026 GMT").year == 2026
