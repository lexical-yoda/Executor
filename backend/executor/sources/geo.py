"""City-level IP geolocation from the free DB-IP Lite database.

The database is downloaded once a month into the web container's data folder
and every lookup happens locally, so client addresses are never sent anywhere.
DB-IP Lite is licensed CC BY 4.0: the page must credit "IP geolocation by
DB-IP" with a link to https://db-ip.com.

Accuracy is city level at best. Mobile networks often place users in their
carrier's hub city, and VPN users appear at the VPN's exit.
"""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import maxminddb

log = logging.getLogger("executor.geo")

URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
FILE = "dbip-city-lite.mmdb"


def _months(now: datetime) -> list[str]:
    """This month's release first, then last month's (a new month's file can lag)."""
    previous = now.replace(day=1) - timedelta(days=1)
    return [now.strftime("%Y-%m"), previous.strftime("%Y-%m")]


def public_ip(value: str | None) -> str | None:
    """The address if it is a valid public IP, else None."""
    try:
        address = ipaddress.ip_address((value or "").strip().split("%")[0])
    except ValueError:
        return None
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        return None
    return str(address)


def describe(record: dict | None) -> dict | None:
    if not record:
        return None
    location = record.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None:
        return None
    names = lambda block: ((block or {}).get("names") or {}).get("en")  # noqa: E731
    subdivisions = record.get("subdivisions") or [{}]
    country = record.get("country") or {}
    return {
        "city": names(record.get("city")),
        "region": names(subdivisions[0]),
        "country": names(country),
        "country_code": country.get("iso_code"),
        "lat": round(float(lat), 4),
        "lon": round(float(lon), 4),
    }


class GeoIP:
    def __init__(self, folder: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.folder = folder
        self.path = folder / FILE
        self.month_file = folder / "month"
        self._transport = transport
        self._reader: maxminddb.Reader | None = None
        self.month: str | None = None
        self.error: str | None = None
        self._open()

    def _open(self) -> None:
        if not self.path.exists():
            return
        try:
            reader = maxminddb.open_database(str(self.path), maxminddb.MODE_FILE)
        except (OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
            self.error = f"unreadable database: {exc.__class__.__name__}"
            return
        old, self._reader = self._reader, reader
        if old:
            old.close()
        self.month = self.month_file.read_text().strip() if self.month_file.exists() else None
        self.error = None

    @property
    def ready(self) -> bool:
        return self._reader is not None

    def lookup(self, ip: str | None) -> dict | None:
        address = public_ip(ip)
        if not address or not self._reader:
            return None
        try:
            return describe(self._reader.get(address))
        except (ValueError, maxminddb.InvalidDatabaseError):
            return None

    async def ensure(self, now: datetime | None = None) -> None:
        """Download this month's database if it is missing or older."""
        now = now or datetime.now(timezone.utc)
        months = _months(now)
        if self.month in months[:1] or (self.month == months[1] and now.day < 3):
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), transport=self._transport,
                                     follow_redirects=True) as client:
            for month in months:
                if self.month == month:
                    return
                partial = self.folder / f"{FILE}.gz.part"
                try:
                    async with client.stream("GET", URL.format(month=month)) as response:
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        with partial.open("wb") as handle:
                            async for chunk in response.aiter_bytes(1 << 20):
                                handle.write(chunk)
                    await asyncio.to_thread(self._install, partial, month)
                    log.info("geolocation database %s installed", month)
                    return
                except (httpx.HTTPError, OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
                    self.error = f"download failed: {exc.__class__.__name__}"
                    log.warning("geolocation download %s failed: %s", month, exc)
                    return
                finally:
                    partial.unlink(missing_ok=True)
        self.error = "no database published for this or last month"

    def _install(self, partial: Path, month: str) -> None:
        unpacked = self.folder / f"{FILE}.new"
        with gzip.open(partial, "rb") as source, unpacked.open("wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
        # Refuse a file that is not a readable database before replacing the old one.
        maxminddb.open_database(str(unpacked), maxminddb.MODE_FILE).close()
        os.replace(unpacked, self.path)
        self.month_file.write_text(month)
        self._open()
