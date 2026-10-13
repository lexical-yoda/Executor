"""City-level IP geolocation, done locally.

Free databases are downloaded into the web container's data folder, and every
lookup happens there, so client addresses are never sent anywhere:

- IP2Location LITE DB11 (needs a free IP2Location account's download token;
  published monthly, fetched once a month on a day in the first week, as
  IP2Location asks). IPv4 only here. Free with attribution and no
  redistribution; the page credits it in IP2Location's own words.
- MaxMind GeoLite2 City (needs a free MaxMind account; updated twice a week,
  refreshed here weekly). It also estimates how far off it may be
  (``accuracy_radius``). Licence: GeoLite2 End User License Agreement; the page
  credits "GeoLite2 data created by MaxMind".
- DB-IP Lite City (no account; monthly), used only when neither account is
  set up. Licence CC BY 4.0; the page credits "IP geolocation by DB-IP".

The first configured database answers; the next is a second opinion.
Corrections from the config (known networks, devices or users) override them
all, because no database knows that a TV is in your living room. Accuracy is
city level at best: mobile networks and some ISPs route through hub cities,
and VPN users appear at the VPN's exit.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
import math
import os
import shutil
import struct
import tarfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import IP2Location
import maxminddb

if TYPE_CHECKING:
    from ..config import Household

log = logging.getLogger("executor.geo")


class _DropTokenRequests(logging.Filter):
    """httpx logs every request's full address at INFO, and IP2Location's
    download address carries the account's token: never let it reach a log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "token=" not in record.getMessage()


logging.getLogger("httpx").addFilter(_DropTokenRequests())

# Bump when the way answers are chosen changes, so history is located again.
RULES = "3"
# A GeoLite2 answer this vague (km) is an area, not a city: a city another
# database names inside that area is the better guess.
VAGUE_KM = 200


def distance_km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


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


def _ip2location_text(value: Any) -> str | None:
    """A field from an IP2Location record, or None for its placeholders."""
    text = str(value).strip() if value is not None else ""
    if not text or text == "-" or text.startswith("This parameter is unavailable") or text == "INVALID IP ADDRESS":
        return None
    return text


def describe_ip2location(record: Any, source: str = "ip2location") -> dict | None:
    if record is None:
        return None
    try:
        lat, lon = float(record.latitude), float(record.longitude)
    except (TypeError, ValueError):
        return None
    code = _ip2location_text(record.country_short)
    # Addresses it has no place for come back as "-" at 0, 0.
    if not code or (lat == 0 and lon == 0):
        return None
    return {
        "city": _ip2location_text(record.city),
        "region": _ip2location_text(record.region),
        "country": _ip2location_text(record.country_long),
        "country_code": code,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "radius_km": None,  # IP2Location gives no accuracy estimate
        "source": source,
    }


class MmdbSource:
    """A downloaded database file (MaxMind format unless a subclass reads
    another), kept up to date by ``ensure``."""

    name = "mmdb"
    file = "db.mmdb"

    def __init__(self, folder: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.folder = folder
        self.path = folder / self.file
        self.version_file = folder / f"{self.file}.version"
        self._transport = transport
        self._reader: Any = None
        self.version: str | None = None
        self.fetched: float | None = None
        self.error: str | None = None
        self._open()

    def _load(self, path: Path) -> Any:
        """Open a reader for the file, raising if it is not a usable database."""
        return maxminddb.open_database(str(path), maxminddb.MODE_FILE)

    def _open(self) -> None:
        if not self.path.exists():
            return
        try:
            reader = self._load(self.path)
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
        self._load(unpacked).close()
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


class IP2LocationLite(MmdbSource):
    """IP2Location LITE DB11 (IPv4), in IP2Location's own BIN format."""

    name = "ip2location"
    file = "IP2LOCATION-LITE-DB11.BIN"
    package = "DB11LITEBIN"
    url = "https://www.ip2location.com/download/"
    # Gap between tries that did not bring this month's release (failed, refused,
    # or still last month's), so a problem never turns into many downloads.
    retry = 12 * 3600

    def __init__(self, folder: Path, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._tried: float | None = None
        super().__init__(folder, transport)

    def _load(self, path: Path) -> Any:
        try:
            reader = IP2Location.IP2Location(str(path))
        except Exception as exc:  # its parser, fed the wrong file, can fail in many ways
            raise ValueError(f"not an IP2Location database ({exc.__class__.__name__})") from exc
        # The right file places a well-known address; a country-only edition or junk does not.
        try:
            usable = describe_ip2location(reader.get_all("8.8.8.8")) is not None
        except Exception:
            usable = False
        if not usable:
            reader.close()
            raise ValueError("not an IP2Location city database")
        return reader

    def lookup(self, ip: str | None) -> dict | None:
        address = public_ip(ip)
        # The IPv4 edition: an IPv6 address is left to the next database.
        if not address or ":" in address or not self._reader:
            return None
        try:
            return describe_ip2location(self._reader.get_all(address), self.name)
        except (OSError, ValueError, IndexError, struct.error):
            return None

    def download_day(self, month: str) -> int:
        """IP2Location asks for one download a month, on a random day of its first
        week. This picks that day per month, the same across restarts."""
        return hashlib.sha256(f"{month}:{self._token}".encode()).digest()[0] % 7 + 1

    async def ensure(self, now: datetime | None = None) -> None:
        """Fetch a release at once when there is none, then once a month."""
        now = now or datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")
        if self.ready and (self.version or "").startswith(month):
            return
        if self.ready and now.day < self.download_day(month):
            return
        if self._tried is not None and now.timestamp() - self._tried < self.retry:
            return
        self._tried = now.timestamp()
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.folder / f"{self.file}.zip.part"
        try:
            async with self._client() as client:
                query = {"token": self._token, "file": self.package}
                async with client.stream("GET", self.url, params=query) as response:
                    if response.status_code != 200:
                        # Report the status only: the request's address carries the token.
                        self.error = f"download failed: HTTP {response.status_code}"
                        log.warning("IP2Location download failed: HTTP %s", response.status_code)
                        return
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes(1 << 20):
                            handle.write(chunk)
            if not zipfile.is_zipfile(partial):
                # A refusal comes back as a short line of text, such as "NO PERMISSION".
                with partial.open("rb") as handle:
                    reason = handle.read(120).decode("ascii", "replace").replace(self._token, "…").strip()
                self.error = f"IP2Location refused the download: {reason or 'empty reply'}"
                log.warning("%s", self.error)
                return
            await asyncio.to_thread(self._unpack, partial)
            log.info("IP2Location %s installed", self.version)
        except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as exc:
            self.error = f"download failed: {exc.__class__.__name__}"
            log.warning("IP2Location download failed: %s", exc.__class__.__name__)
        finally:
            partial.unlink(missing_ok=True)

    def _unpack(self, partial: Path) -> None:
        unpacked = self.folder / f"{self.file}.new"
        with zipfile.ZipFile(partial) as archive:
            member = next((m for m in archive.infolist()
                           if not m.is_dir() and m.filename.rsplit("/", 1)[-1].upper() == self.file), None)
            if member is None:
                raise ValueError(f"no {self.file} in the archive")
            with archive.open(member) as source, unpacked.open("wb") as target:
                shutil.copyfileobj(source, target, 1 << 20)
        try:
            # The file's header carries its release date.
            reader = self._load(unpacked)
            year, month, day = (getattr(reader, f"_db{part}", 0) for part in ("year", "month", "day"))
            reader.close()
            if year and month:
                version = f"20{year:02d}-{month:02d}-{day:02d}"
            else:
                version = datetime.now(timezone.utc).strftime("%Y-%m")
            self._install(unpacked, version)
        finally:
            unpacked.unlink(missing_ok=True)


class Corrections:
    """Known places that beat any database: by network, by device, or by user."""

    def __init__(self, rules: list) -> None:
        self.rules = []
        for rule in rules:
            place = {"home": True} if rule.home else {
                "city": rule.city, "region": rule.region, "country": rule.country,
                "country_code": rule.country_code, "lat": rule.lat, "lon": rule.lon,
                "radius_km": None, "source": "correction"}
            self.rules.append((
                place,
                [ipaddress.ip_network(n, strict=False) for n in rule.networks],
                {d.casefold() for d in rule.devices},
                {u.casefold() for u in rule.users},
                {(d.user.casefold(), d.device.casefold()) for d in rule.user_devices},
            ))
        self.signature = hashlib.sha256(json.dumps([r.model_dump() for r in rules], sort_keys=True,
                                                   default=str).encode()).hexdigest()[:12]

    def match_device(self, user: str | None, device: str | None) -> dict | None:
        """One user's device, then a device name: these beat even the home address."""
        if device:
            pair = ((user or "").casefold(), device.casefold())
            for place, _, _, _, pairs in self.rules:
                if pair in pairs:
                    return dict(place)
            for place, _, devices, _, _ in self.rules:
                if device.casefold() in devices:
                    return dict(place)
        return None

    def match(self, ip: str | None, user: str | None, device: str | None) -> dict | None:
        try:
            address = ipaddress.ip_address(ip) if ip else None
        except ValueError:
            address = None
        # Most specific first: a device, then a network, then a user who is always in one place.
        found = self.match_device(user, device)
        if found:
            return found
        for place, networks, _, _, _ in self.rules:
            if address and any(address in n for n in networks):
                return dict(place)
        for place, _, _, users, _ in self.rules:
            if user and user.casefold() in users:
                return dict(place)
        return None


class Locator:
    def __init__(self, sources: list[MmdbSource], corrections: Corrections | None = None,
                 home: dict | None = None, household: Household | None = None) -> None:
        self.sources = sources  # in order of preference
        self.corrections = corrections or Corrections([])
        # The server's own public address and where it is (the configured origin).
        self.home = home
        self.home_ip: str | None = None
        self.household = household
        self._household_users = {u.casefold() for u in household.users} if household else set()

    @property
    def ready(self) -> bool:
        return any(s.ready for s in self.sources)

    @property
    def signature(self) -> str:
        """Changes whenever a different answer could come out, so history can be relocated."""
        parts = [f"{s.name}:{s.version if s.ready else '-'}" for s in self.sources]
        household = f"household:{sorted(self._household_users)}/{self.household.prefix_v4}" if self.household else ""
        return "|".join(parts + [self.corrections.signature, household, RULES])

    async def ensure(self) -> None:
        for source in self.sources:
            await source.ensure()

    def locate(self, ip: str | None, user: str | None = None, device: str | None = None) -> dict | None:
        address = (ip or "").strip().split("%")[0] or None
        # A device pinned to a place wins over everything, even the home address.
        fixed = self.corrections.match_device(user, device)
        if fixed is None:
            if self.home and address and address == self.home_ip:
                return dict(self.home)
            fixed = self.corrections.match(address, user, device)
        if fixed and fixed.get("home"):
            return dict(self.home) if self.home else None
        if fixed:
            return fixed
        if self.in_household_block(address, user):
            return dict(self.home)
        answers = [a for a in (s.lookup(ip) for s in self.sources if s.ready) if a]
        if not answers:
            return None
        best = answers[0]
        radius = best.get("radius_km")
        if radius and radius >= VAGUE_KM:
            # The first database only knows the area; take a city another one places inside it.
            for other in answers[1:]:
                if other.get("city") and distance_km(best, other) <= radius:
                    return {**other, "radius_km": None, "within": {"source": best["source"], "km": radius}}
        return best

    def in_household_block(self, address: str | None, user: str | None) -> bool:
        """A household user on the same provider block as the server's own address."""
        if not (self.home and self.household and self.home_ip and address and user):
            return False
        if user.casefold() not in self._household_users:
            return False
        try:
            here = ipaddress.ip_address(address)
            home = ipaddress.ip_address(self.home_ip)
        except ValueError:
            return False
        if here.version != 4 or home.version != 4:
            return False
        block = ipaddress.ip_network(f"{home}/{self.household.prefix_v4}", strict=False)
        return here in block

    def second_opinion(self, ip: str | None, primary: dict | None) -> dict | None:
        """Another database's answer, when it names a different city."""
        if not primary or primary.get("source") in ("home", "correction"):
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
