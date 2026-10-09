import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from executor.config import Config
from executor.monitor import Monitor
from executor.sources.aws import CloudWatchS3, parse_datapoints, parse_error, sign_get, summarize_bucket


def test_signature_matches_aws_example():
    # The worked example from AWS's Signature Version 4 documentation.
    headers = sign_get("iam.amazonaws.com", "us-east-1", "iam", {"Action": "ListUsers", "Version": "2010-05-08"},
                       "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                       datetime(2015, 8, 30, 12, 36, tzinfo=timezone.utc),
                       {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-date, "
        "Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7")


def stats_xml(points):
    members = "".join(f"<member><Timestamp>{t}</Timestamp><Average>{v}</Average><Unit>Bytes</Unit></member>"
                      for t, v in points)
    return ('<GetMetricStatisticsResponse xmlns="http://monitoring.amazonaws.com/doc/2010-08-01/">'
            f"<GetMetricStatisticsResult><Datapoints>{members}</Datapoints><Label>x</Label>"
            "</GetMetricStatisticsResult></GetMetricStatisticsResponse>")


def test_parsing():
    points = parse_datapoints(stats_xml([("2026-01-02T00:00:00Z", 2.0), ("2026-01-01T00:00:00Z", 1.0)]))
    assert [v for _, v in points] == [1.0, 2.0]
    error = ('<ErrorResponse xmlns="http://monitoring.amazonaws.com/doc/2010-08-01/"><Error><Type>Sender</Type>'
             "<Code>AccessDenied</Code><Message>not allowed</Message></Error></ErrorResponse>")
    assert parse_error(error) == "AccessDenied: not allowed"


def day(n):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=n)).timestamp()


def test_summary_growth_and_cost():
    gib = 2**30
    sizes = {"GlacierInstantRetrievalStorage": [(day(n), (50 + n * 0.5) * gib) for n in range(0, 100)],
             "StandardStorage": [(day(99), 1 * gib)]}
    summary = summarize_bucket(sizes, [(day(99), 3546.0)], 0.004)
    assert summary["as_of"] == "2026-04-10"
    assert summary["bytes"] == round((50 + 99 * 0.5 + 1) * gib)
    assert summary["by_type"]["StandardStorage"] == gib
    assert summary["objects"] == 3546
    # 30 days earlier, Standard had no data point, so only Glacier counts there.
    assert summary["growth_30d"] == round((99 - 69) * 0.5 * gib + gib)
    assert summary["monthly_cost"] == round((50 + 49.5 + 1) * 0.004, 2)
    assert len(summary["series"]) == 100
    assert summarize_bucket({}, [], 0.004)["bytes"] is None


def test_client_signs_and_queries_the_bucket_region():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "monitoring.us-east-1.amazonaws.com"
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/")
        params = request.url.params
        assert params["Namespace"] == "AWS/S3" and params["Dimensions.member.1.Value"] == "backups"
        if params["MetricName"] == "NumberOfObjects":
            return httpx.Response(200, text=stats_xml([("2026-01-02T00:00:00Z", 10)]))
        if params["Dimensions.member.2.Value"] == "GlacierInstantRetrievalStorage":
            return httpx.Response(200, text=stats_xml([("2026-01-02T00:00:00Z", 2**30)]))
        return httpx.Response(200, text=stats_xml([]))

    async def check():
        client = CloudWatchS3("AKIDTEST", "secret", transport=httpx.MockTransport(handler))
        result = await client.bucket("backups", "us-east-1",
                                     ["StandardStorage", "GlacierInstantRetrievalStorage"], 0.004)
        assert result["bytes"] == 2**30 and result["objects"] == 10
        assert list(result["by_type"]) == ["GlacierInstantRetrievalStorage"]

        def refuse(request):
            return httpx.Response(403, text="<ErrorResponse><Error><Code>AccessDenied</Code>"
                                            "<Message>nope</Message></Error></ErrorResponse>")
        bad = CloudWatchS3("AKIDTEST", "secret", transport=httpx.MockTransport(refuse))
        try:
            await bad.bucket("backups", "us-east-1", ["StandardStorage"], None)
        except RuntimeError as exc:
            assert "AccessDenied" in str(exc)
        else:
            raise AssertionError("expected a refusal")

    asyncio.run(check())
    assert len(seen) == 3
    # The secret never appears in a request.
    assert all("secret" not in str(r.url) and "secret" not in str(r.headers) for r in seen)


def test_falls_back_to_duplicati_without_aws_keys():
    config = Config.model_validate({
        "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
        "integrations": {"backups": {"storage": [{"name": "Archive", "bucket": "backups", "region": "us-east-1",
                                                  "price_per_gib_month": 0.004, "duplicati_job": "Weekly"}]}},
        "machines": [], "services": [],
    })
    monitor = Monitor(config, None)
    monitor.duplicati_status = {"jobs": [{"name": "Weekly", "target_bytes": 100 * 2**30, "versions": 4}],
                                "paused": False}
    storage = monitor.snapshot()["backups"]["storage"][0]
    assert storage["configured"] is False and storage["aws"] is None
    assert storage["duplicati_bytes"] == 100 * 2**30 and storage["fallback_cost"] == 0.4
