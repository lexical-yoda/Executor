"""AWS: S3 bucket size and growth from CloudWatch's daily storage metrics.

S3 publishes each bucket's size per storage class (``BucketSizeBytes``) and its
object count (``NumberOfObjects``) to CloudWatch once a day, in the bucket's
region, and CloudWatch keeps those daily points for 15 months. Reading them
needs only ``cloudwatch:GetMetricStatistics``, which cannot touch the data.

Requests are signed with Signature Version 4 using only the standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

NS = {"cw": "http://monitoring.amazonaws.com/doc/2010-08-01/"}
DAY = 86400
HISTORY_DAYS = 400


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def sign_get(host: str, region: str, service: str, query: dict[str, str], key_id: str, secret: str,
             now: datetime, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    """Headers that authenticate a GET to ``https://{host}/?{query}`` (SigV4)."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    headers = {"host": host, "x-amz-date": amz_date, **{k.lower(): v for k, v in (extra_headers or {}).items()}}
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
    canonical_query = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
    canonical_request = "\n".join(["GET", "/", canonical_query, canonical_headers, signed,
                                   hashlib.sha256(b"").hexdigest()])
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}".encode("utf-8"), date), region), service), "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    result = {k: v for k, v in headers.items() if k != "host"}
    result["authorization"] = (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
                               f"SignedHeaders={signed}, Signature={signature}")
    return result


def parse_datapoints(body: str) -> list[tuple[float, float]]:
    root = ET.fromstring(body)
    points = []
    for member in root.iterfind(".//cw:Datapoints/cw:member", NS):
        stamp = member.findtext("cw:Timestamp", namespaces=NS)
        value = member.findtext("cw:Average", namespaces=NS)
        if stamp and value:
            moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            points.append((moment, float(value)))
    return sorted(points)


def parse_error(body: str) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return body[:120]
    code = root.findtext(".//{*}Code") or "Error"
    message = root.findtext(".//{*}Message") or ""
    return f"{code}: {message}"[:200]


def _day(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%d")


def summarize_bucket(sizes: dict[str, list[tuple[float, float]]], objects: list[tuple[float, float]],
                     price_per_gib_month: float | None) -> dict:
    """Combine per-storage-class daily sizes into one daily total."""
    by_day: dict[str, dict[str, float]] = {}
    for storage_type, points in sizes.items():
        for moment, value in points:
            by_day.setdefault(_day(moment), {})[storage_type] = value
    days = sorted(by_day)
    series = [{"date": d, "bytes": round(sum(by_day[d].values()))} for d in days]
    latest = series[-1] if series else None

    def growth(days_back: int) -> float | None:
        if not latest:
            return None
        cutoff = (datetime.strptime(latest["date"], "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
        earlier = [s for s in series if s["date"] <= cutoff]
        return latest["bytes"] - earlier[-1]["bytes"] if earlier else None

    total = latest["bytes"] if latest else None
    return {
        "as_of": latest["date"] if latest else None,
        "bytes": total,
        "by_type": {k: round(v) for k, v in by_day[days[-1]].items()} if days else {},
        "objects": round(objects[-1][1]) if objects else None,
        "growth_7d": growth(7),
        "growth_30d": growth(30),
        "growth_90d": growth(90),
        # Until there are 30 days of history.
        "growth_total": latest["bytes"] - series[0]["bytes"] if latest else None,
        "first_date": series[0]["date"] if series else None,
        "monthly_cost": round(total / 2**30 * price_per_gib_month, 2)
        if total is not None and price_per_gib_month is not None else None,
        "series": series,
    }


class CloudWatchS3:
    def __init__(self, key_id: str, secret: str, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key_id = key_id
        self._secret = secret
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _statistics(self, region: str, metric: str, bucket: str, storage_type: str,
                          now: datetime) -> list[tuple[float, float]]:
        host = f"monitoring.{region}.amazonaws.com"
        query = {
            "Action": "GetMetricStatistics", "Version": "2010-08-01",
            "Namespace": "AWS/S3", "MetricName": metric,
            "Dimensions.member.1.Name": "BucketName", "Dimensions.member.1.Value": bucket,
            "Dimensions.member.2.Name": "StorageType", "Dimensions.member.2.Value": storage_type,
            "StartTime": (now - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%dT00:00:00Z"),
            "EndTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Period": str(DAY), "Statistics.member.1": "Average",
        }
        headers = sign_get(host, region, "monitoring", query, self._key_id, self._secret, now)
        url = f"https://{host}/?" + "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
        response = await self._client.get(url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"CloudWatch refused: {parse_error(response.text)}")
        return parse_datapoints(response.text)

    async def bucket(self, bucket: str, region: str, storage_types: list[str],
                     price_per_gib_month: float | None, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        sizes = {}
        for storage_type in storage_types:
            points = await self._statistics(region, "BucketSizeBytes", bucket, storage_type, now)
            if points:
                sizes[storage_type] = points
        objects = await self._statistics(region, "NumberOfObjects", bucket, "AllStorageTypes", now)
        return {"fetched_at": time.time(), **summarize_bucket(sizes, objects, price_per_gib_month)}
