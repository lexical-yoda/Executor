"""City-level IP geolocation, done locally.

Two free databases are downloaded into the web container's data folder, and
every lookup happens there, so client addresses are never sent anywhere:

- MaxMind GeoLite2 City (needs a free MaxMind account; updated twice a week,
  refreshed here weekly). It also estimates how far off it may be
  (``accuracy_radius``). Licence: GeoLite2 End User License Agreement; the page
  credits "GeoLite2 data created by MaxMind".
- DB-IP Lite City (no account; monthly). Licence CC BY 4.0; the page credits
  "IP geolocation by DB-IP".

GeoLite2 answers first when it is available; DB-IP is the fallback and a
second opinion. Corrections from the config (known networks, devices or
users) override both, because no database knows that a TV is in your living
room. Accuracy is city level at best: mobile networks and some ISPs route
through hub cities, and VPN users appear at the VPN's exit.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
import os
import shutil
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import maxminddb

log = logging.getLogger("executor.geo")


def public_ip(value: str | None) -> str | None:
    """The address if it is a valid public IP, else None."""
    try:
        address = ipaddress.ip_address((value or "").strip().split("%")[0])
    except ValueError:
        return None
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        return None
    return str(address)


def describe(record: dict | None, source: str = "dbip") -> dict | None:
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
        "radius_km": location.get("accuracy_radius"),
        "source": source,
    }


class MmdbSource:
    """A MaxMind-format database file, kept up to date by ``ensure``."""

    name = "mmdb"
    file = "db.mmdb"

    def __init__(self, folder: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.folder = folder
        self.path = folder / self.file
        self.version_file = folder / f"{self.file}.version"
        self._transport = transport
        self._reader: maxminddb.Reader | None = None
        self.version: str | None = None
        self.fetched: float | None = None
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
        if self.version_file.exists():
            self.version = self.version_file.read_text().strip()
            self.fetched = self.version_file.stat().st_mtime
        self.error = None

    @property
    def ready(self) -> bool:
        return self._reader is not None

    def lookup(self, ip: str | None) -> dict | None:
        address = public_ip(ip)
        if not address or not self._reader:
            return None
        try:
            return describe(self._reader.get(address), self.name)
        except (ValueError, maxminddb.InvalidDatabaseError):
            return None

    def _client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(30, read=180), transport=self._transport,
                                 follow_redirects=True, **kwargs)

    def _install(self, unpacked: Path, version: str) -> None:
        # Refuse a file that is not a readable database before replacing the old one.
        maxminddb.open_database(str(unpacked), maxminddb.MODE_FILE).close()
        os.replace(unpacked, self.path)
        self.version_file.write_text(version)
        self._open()

    async def ensure(self, now: datetime | None = None) -> None:
        raise NotImplementedError

    def status(self) -> dict:
        return {"name": self.name, "ready": self.ready, "version": self.version, "error": self.error}


class DbIpLite(MmdbSource):
    name = "dbip"
    file = "dbip-city-lite.mmdb"
    url = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"

    async def ensure(self, now: datetime | None = None) -> None:
        """Download this month's release if it is missing or older."""
        now = now or datetime.now(timezone.utc)
        previous = now.replace(day=1) - timedelta(days=1)
        months = [now.strftime("%Y-%m"), previous.strftime("%Y-%m")]
        if self.version == months[0] or (self.version == months[1] and now.day < 3):
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        async with self._client() as client:
            for month in months:
                if self.version == month:
                    return
                partial = self.folder / f"{self.file}.gz.part"
                try:
                    async with client.stream("GET", self.url.format(month=month)) as response:
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        with partial.open("wb") as handle:
                            async for chunk in response.aiter_bytes(1 << 20):
                                handle.write(chunk)
                    await asyncio.to_thread(self._unpack, partial, month)
                    log.info("DB-IP %s installed", month)
                    return
                except (httpx.HTTPError, OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
                    self.error = f"download failed: {exc.__class__.__name__}"
                    log.warning("DB-IP download %s failed: %s", month, exc)
                    return
                finally:
                    partial.unlink(missing_ok=True)
        self.error = "no database published for this or last month"

    def _unpack(self, partial: Path, month: str) -> None:
        unpacked = self.folder / f"{self.file}.new"
        with gzip.open(partial, "rb") as source, unpacked.open("wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
        self._install(unpacked, month)


class GeoLite2(MmdbSource):
    name = "geolite2"
    file = "GeoLite2-City.mmdb"
    url = "https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz"
    refresh = 7 * 86400

    def __init__(self, folder: Path, account_id: str, license_key: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._auth = (account_id, license_key)
        super().__init__(folder, transport)

    async def ensure(self, now: datetime | None = None) -> None:
        """Download the current release if there is none or it is a week old."""
        moment = (now or datetime.now(timezone.utc)).timestamp()
        if self.ready and self.fetched and moment - self.fetched < self.refresh:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.folder / f"{self.file}.tar.gz.part"
        try:
            async with self._client(auth=self._auth) as client:
                async with client.stream("GET", self.url) as response:
                    if response.status_code in (401, 403):
                        self.error = "MaxMind refused the account ID or licence key"
                        return
                    response.raise_for_status()
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes(1 << 20):
                            handle.write(chunk)
            await asyncio.to_thread(self._unpack, partial)
            log.info("GeoLite2 %s installed", self.version)
        except (httpx.HTTPError, OSError, ValueError, tarfile.TarError, maxminddb.InvalidDatabaseError) as exc:
            self.error = f"download failed: {exc.__class__.__name__}"
            log.warning("GeoLite2 download failed: %s", exc)
        finally:
            partial.unlink(missing_ok=True)

    def _unpack(self, partial: Path) -> None:
        unpacked = self.folder / f"{self.file}.new"
        with tarfile.open(partial, "r:gz") as archive:
            member = next((m for m in archive.getmembers()
                           if m.isfile() and m.name.endswith("/" + self.file)), None)
            if member is None:
                raise ValueError("no GeoLite2-City.mmdb in the archive")
            source = archive.extractfile(member)
            assert source is not None
            with unpacked.open("wb") as target:
                shutil.copyfileobj(source, target, 1 << 20)
        # The archive's folder is named after the release, e.g. GeoLite2-City_20261007.
        release = member.name.split("/")[0].rsplit("_", 1)[-1]
        self._install(unpacked, release)
        # Count the week from now, so a quiet release schedule does not mean daily downloads.
        os.utime(self.version_file, (time.time(), time.time()))
        self._open()


class Corrections:
    """Known places that beat any database: by network, by device, or by user."""

    def __init__(self, rules: list) -> None:
        self.rules = []
        for rule in rules:
            place = {"city": rule.city, "region": rule.region, "country": rule.country,
                     "country_code": rule.country_code, "lat": rule.lat, "lon": rule.lon,
                     "radius_km": None, "source": "correction"}
            self.rules.append((
                place,
                [ipaddress.ip_network(n, strict=False) for n in rule.networks],
                {d.casefold() for d in rule.devices},
                {u.casefold() for u in rule.users},
            ))
        self.signature = hashlib.sha256(json.dumps([r.model_dump() for r in rules], sort_keys=True,
                                                   default=str).encode()).hexdigest()[:12]

    def match(self, ip: str | None, user: str | None, device: str | None) -> dict | None:
        try:
            address = ipaddress.ip_address(ip) if ip else None
        except ValueError:
            address = None
        # Most specific first: a device, then a network, then a user who is always in one place.
        for place, _, devices, _ in self.rules:
            if device and device.casefold() in devices:
                return dict(place)
        for place, networks, _, _ in self.rules:
            if address and any(address in n for n in networks):
                return dict(place)
        for place, _, _, users in self.rules:
            if user and user.casefold() in users:
                return dict(place)
        return None


class Locator:
    def __init__(self, sources: list[MmdbSource], corrections: Corrections | None = None,
                 home: dict | None = None) -> None:
        self.sources = sources  # in order of preference
        self.corrections = corrections or Corrections([])
        # The server's own public address and where it is (the configured origin).
        self.home = home
        self.home_ip: str | None = None

    @property
    def ready(self) -> bool:
        return any(s.ready for s in self.sources)

    @property
    def signature(self) -> str:
        """Changes whenever a different answer could come out, so history can be relocated."""
        parts = [f"{s.name}:{s.version if s.ready else '-'}" for s in self.sources]
        return "|".join(parts + [self.corrections.signature])

    async def ensure(self) -> None:
        for source in self.sources:
            await source.ensure()

    def locate(self, ip: str | None, user: str | None = None, device: str | None = None) -> dict | None:
        address = (ip or "").strip().split("%")[0] or None
        if self.home and address and address == self.home_ip:
            return dict(self.home)
        fixed = self.corrections.match(address, user, device)
        if fixed:
            return fixed
        for source in self.sources:
            if source.ready:
                place = source.lookup(ip)
                if place:
                    return place
        return None

    def second_opinion(self, ip: str | None, primary: dict | None) -> dict | None:
        """Another database's answer, when it names a different city."""
        if not primary:
            return None
        for source in self.sources:
            if source.ready and source.name != primary.get("source"):
                other = source.lookup(ip)
                if other and other.get("city") != primary.get("city"):
                    return other
                return None
        return None

    def status(self) -> list[dict]:
        return [s.status() for s in self.sources]
