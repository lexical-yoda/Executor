"""Configuration models.

Two files, deliberately separate:

- ``config.yaml`` is read by the web process: machines, services, security.
- ``actions.yaml`` is read only by the runner. The web process never sees the
  commands, so a compromised web container cannot add or change what runs.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
# ${NAME} references in http steps.
ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class Check(BaseModel):
    type: Literal["http", "tcp"] = "http"
    url: str | None = None
    host: str | None = None
    port: int | None = None
    # HTTP statuses that count as up. None means any response below 500,
    # because a 401 or a redirect to a login page still proves the app is alive.
    expect: list[int] | None = None
    verify_tls: bool = True
    timeout: float = 5.0

    @model_validator(mode="after")
    def _shape(self) -> Check:
        if self.type == "http" and not self.url:
            raise ValueError("an http check needs a url")
        if self.type == "tcp" and not (self.host and self.port):
            raise ValueError("a tcp check needs a host and a port")
        return self


class Service(BaseModel):
    id: Slug
    name: str
    group: str
    url: str | None = None
    description: str | None = None
    check: Check | None = None
    # First container is the primary one: if it is not running, the service is down.
    containers: list[str] = []


MachineIcon = Literal["server", "cloud", "laptop", "phone", "desktop", "gamepad", "router"]


class Machine(BaseModel):
    id: Slug
    name: str
    role: str
    address: str | None = None
    icon: MachineIcon = "server"
    # The machine Executor itself runs on: always reachable, reports host stats.
    local: bool = False
    # System name in Beszel, for live resource stats and history.
    beszel: str | None = None


class Security(BaseModel):
    # Client addresses or networks allowed to use the dashboard at all.
    allowed_clients: list[str]
    # Addresses or networks always refused, even if inside an allowed network.
    # Use it for the WireGuard hub and the home LAN.
    denied_clients: list[str] = []
    # Host header values accepted (without port). Blocks DNS rebinding.
    allowed_hosts: list[str]

    @field_validator("allowed_clients", "denied_clients")
    @classmethod
    def _networks(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value


class Site(BaseModel):
    title: str = "Executor"
    subtitle: str = "Command deck"


class Settings(BaseModel):
    check_interval: float = 20
    ping_interval: float = 15
    container_interval: float = 10
    stats_interval: float = 15


class BeszelIntegration(BaseModel):
    # Hub URL as seen from the web container. Credentials come from the
    # environment (BESZEL_EMAIL, BESZEL_PASSWORD), never from this file.
    url: str
    timeout: float = 8.0


class EdgeIntegration(BaseModel):
    # JSON written by the VPS's bandwidth fetcher (see README, "Edge panel").
    bandwidth_url: str | None = None
    # JSON written by the edge server's traffic summariser (see README, "Traffic
    # and shields"): requests per site and attacks, by hour.
    traffic_url: str | None = None
    # Hostnames whose TLS certificate expiry is shown.
    certificates: list[str] = []
    interval: float = 300


class MapPoint(BaseModel):
    label: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    # The machine at this point, whose health the map shows on the marker.
    machine: Slug | None = None


class UserDevice(BaseModel):
    user: str
    device: str


class LocationCorrection(BaseModel):
    # A known place that beats the geolocation databases. Matches, most
    # specific first: one user's device, a Jellyfin device name, an address
    # in one of the networks, or a Jellyfin user who is always in this place.
    # With `home: true` the place is the configured origin (Home) and needs
    # no city or coordinates of its own.
    home: bool = False
    city: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    networks: list[str] = []
    user_devices: list[UserDevice] = []
    devices: list[str] = []
    users: list[str] = []

    @field_validator("networks")
    @classmethod
    def _networks(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value

    @model_validator(mode="after")
    def _place(self) -> LocationCorrection:
        if not self.home and (self.city is None or self.lat is None or self.lon is None):
            raise ValueError("a correction needs city, lat and lon, or home: true")
        return self


class Household(BaseModel):
    # Providers behind carrier-grade NAT can give every device at home its own
    # public address from a shared block, so the server's own address misses
    # the rest of the house. These users' sessions from the same block as the
    # server's address (this many leading bits) count as home.
    users: list[str]
    prefix_v4: int = Field(default=20, ge=8, le=32)


class JellyfinIntegration(BaseModel):
    # Server URL as seen from the web container. The API key comes from the
    # environment (JELLYFIN_API_KEY), never from this file.
    url: str
    timeout: float = 8.0
    # Days of location history to keep in the web data folder. 0 turns
    # history (and the geolocation download) off.
    history_days: int = Field(default=90, ge=0, le=400)
    # On the map, streams flow from `origin` (where the media server is) to
    # `hub` (the public relay or reverse proxy, if any) and out to viewers.
    origin: MapPoint | None = None
    hub: MapPoint | None = None
    corrections: list[LocationCorrection] = []
    # A URL that answers with this server's public address as plain text (for
    # example https://api.ipify.org). Sessions from that address come from
    # the same home as the server, so they are placed at `origin`. Checked
    # hourly; off when unset.
    home_ip_url: str | None = None
    household: Household | None = None
    # Jellyfin library name -> id of a folder under `sizes:` in actions.yaml,
    # whose size the runner measures (Jellyfin knows few file sizes itself).
    library_folders: dict[str, Slug] = {}


class DuplicatiIntegration(BaseModel):
    # Server URL as seen from the web container. The UI password comes from
    # the environment (DUPLICATI_PASSWORD), never from this file.
    url: str
    timeout: float = 10.0


class FileBackup(BaseModel):
    # A backup judged by the files it leaves in a folder the runner watches.
    folder: Slug
    name: str
    schedule: str = ""
    max_age_hours: float = 26
    # Files that must all exist, be non-empty and be fresh...
    expect: list[str] = []
    # ...or a glob whose newest match must be fresh.
    pattern: str | None = None
    # A file whose non-empty content means the last run failed.
    error_file: str | None = None
    # A log whose last line is shown.
    log_file: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> FileBackup:
        if bool(self.expect) == bool(self.pattern):
            raise ValueError(f"backup '{self.name}' needs exactly one of expect or pattern")
        return self


class StorageBucket(BaseModel):
    # An S3 bucket whose size and growth come from CloudWatch's daily storage
    # metrics. Key: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in the
    # environment, allowed only cloudwatch:GetMetricStatistics.
    name: str
    bucket: str
    region: str
    storage_types: list[str] = ["StandardStorage", "GlacierInstantRetrievalStorage",
                                "GlacierInstantRetrievalSizeOverhead"]
    # For the cost estimate, in USD per GiB-month.
    price_per_gib_month: float | None = None
    # A Duplicati job writing to this bucket: its stored size is shown beside
    # AWS's figure, and used alone when AWS is unavailable.
    duplicati_job: str | None = None


class BackupsIntegration(BaseModel):
    duplicati: DuplicatiIntegration | None = None
    files: list[FileBackup] = []
    storage: list[StorageBucket] = []
    interval: float = 60
    # S3 storage metrics change once a day.
    storage_interval: float = 6 * 3600


class Upstream(BaseModel):
    # URL as seen from the web container. Keys and passwords come from the
    # environment, never from this file.
    url: str
    timeout: float = 8.0


class MediaIntegration(BaseModel):
    # Requests (JELLYSEERR_API_KEY), queues (SONARR_API_KEY, RADARR_API_KEY)
    # and torrent client speeds (QBITTORRENT_USERNAME, QBITTORRENT_PASSWORD).
    jellyseerr: Upstream | None = None
    sonarr: Upstream | None = None
    radarr: Upstream | None = None
    qbittorrent: Upstream | None = None
    interval: float = 15
    requests_interval: float = 60


class ImmichIntegration(BaseModel):
    # Server URL as seen from the web container. The API key comes from the
    # environment (IMMICH_API_KEY), never from this file.
    url: str
    timeout: float = 8.0
    interval: float = 300


class PiHoleIntegration(BaseModel):
    # Pi-hole v6 web server as seen from the web container. The app password
    # comes from the environment (PIHOLE_PASSWORD), never from this file.
    url: str
    timeout: float = 8.0
    interval: float = 60


class TrueNASIntegration(BaseModel):
    # JSON-RPC websocket, as seen from the web container. Must be wss://:
    # TrueNAS revokes an API key that is ever sent over plain HTTP. The key
    # comes from the environment (TRUENAS_API_KEY), never from this file.
    url: str
    # The key's user (for the newer login), if known.
    username: str | None = None
    # TrueNAS usually serves a self-signed certificate.
    verify_tls: bool = False
    timeout: float = 15.0
    interval: float = 300

    @field_validator("url")
    @classmethod
    def _secure(cls, value: str) -> str:
        if not value.startswith("wss://"):
            raise ValueError("the TrueNAS url must start with wss:// (TrueNAS revokes keys sent over plain HTTP)")
        return value


class Discovery(BaseModel):
    # Stacks found from Docker itself (compose labels) and the runner's stacks
    # folder. A stack none of whose containers a configured service lists
    # appears on its own; a configured service whose containers and stack
    # folder are both gone is dropped.
    group: str = "Discovered"
    # Host for links to a discovered stack's first published port, as seen
    # from the browser (for example the server's address). No link when unset.
    link_host: str | None = None
    # Host the web container reaches published ports on, for a TCP check.
    probe_host: str | None = None
    # Stacks (compose project names) never shown.
    ignore: list[str] = []
    # Display names and groups by stack; compose labels executor.name,
    # executor.group, executor.url and executor.hide do the same per stack.
    names: dict[str, str] = {}
    groups: dict[str, str] = {}


class Integrations(BaseModel):
    beszel: BeszelIntegration | None = None
    edge: EdgeIntegration | None = None
    jellyfin: JellyfinIntegration | None = None
    backups: BackupsIntegration | None = None
    media: MediaIntegration | None = None
    immich: ImmichIntegration | None = None
    truenas: TrueNASIntegration | None = None
    pihole: PiHoleIntegration | None = None


class Config(BaseModel):
    security: Security
    site: Site = Site()
    settings: Settings = Settings()
    integrations: Integrations = Integrations()
    discovery: Discovery | None = None
    groups: list[str] = []
    machines: list[Machine]
    services: list[Service]

    @model_validator(mode="after")
    def _unique(self) -> Config:
        for kind, items in (("machine", self.machines), ("service", self.services)):
            seen: set[str] = set()
            for item in items:
                if item.id in seen:
                    raise ValueError(f"duplicate {kind} id: {item.id}")
                seen.add(item.id)
        return self

    @property
    def ordered_groups(self) -> list[str]:
        """Configured group order first, then any group only used by a service."""
        order = list(self.groups)
        for service in self.services:
            if service.group not in order:
                order.append(service.group)
        return order


class SshTarget(BaseModel):
    # A host whose SSH login runs a fixed dispatcher (a forced command), so a
    # step can only name one of the dispatcher's commands.
    host: str
    user: str
    port: int = 22
    key: str = "/ssh/id_ed25519"
    known_hosts: str = "/ssh/known_hosts"


class SshCall(BaseModel):
    target: Slug
    command: Slug


class HttpCall(BaseModel):
    method: Literal["GET", "POST", "PUT", "DELETE"] = "POST"
    # ${NAME} in the url and header values is replaced from the runner's
    # environment. Those values are never logged.
    url: str
    headers: dict[str, str] = {}
    json_body: dict | list | None = Field(default=None, alias="json")
    # Statuses that count as success. None means any 2xx.
    expect: list[int] | None = None
    verify_tls: bool = True

    model_config = {"populate_by_name": True}


class Step(BaseModel):
    name: str
    run: list[str] | None = None
    cwd: str | None = None
    wait_healthy: str | None = None
    ssh: SshCall | None = None
    http: HttpCall | None = None
    timeout: float = 300
    # Retry a failing run, ssh or http step after this many seconds until it
    # succeeds or the step's timeout runs out. Use it to wait for something.
    retry_every: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        kinds = [bool(self.run), bool(self.wait_healthy), self.ssh is not None, self.http is not None]
        if sum(kinds) != 1:
            raise ValueError(f"step '{self.name}' needs exactly one of run, wait_healthy, ssh or http")
        if self.retry_every and self.wait_healthy:
            raise ValueError(f"step '{self.name}': wait_healthy already waits; drop retry_every")
        return self


class Action(BaseModel):
    id: Slug
    title: str
    description: str = ""
    confirm: str
    danger: Literal["low", "medium", "high"] = "medium"
    # Show the media server's active streams in the confirm dialog.
    show_streams: bool = False
    # Heading the action is listed under, usually the machine or stack it acts on.
    group: str | None = None
    # Where else the page offers the action: machine or service ids, or the
    # panels "downloads", "requests", "now-playing", "backups" and "edge".
    attach: list[Slug] = []
    steps: list[Step] = Field(min_length=1)


class WatchedFolder(BaseModel):
    # A folder whose file names, sizes and times the runner may report, for
    # backups the web container cannot read itself (root-only folders).
    id: Slug
    path: str
    # Plain file names in the folder whose last lines may be reported.
    tail: list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+$")]] = []


class SizedFolder(BaseModel):
    # A folder whose total size and file count the runner may report (for
    # example a media library), fixed here and never taken from a request.
    id: Slug
    path: str


class ActionsConfig(BaseModel):
    ssh: dict[str, SshTarget] = {}
    files: list[WatchedFolder] = []
    # A folder holding one subfolder per compose stack (as stack managers such
    # as Dockhand keep them). The runner reports the subfolder names only, so
    # the page can tell a stopped stack from a removed one.
    stacks_dir: str | None = None
    sizes: list[SizedFolder] = []
    actions: list[Action] = []

    @model_validator(mode="after")
    def _unique(self) -> ActionsConfig:
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action id")
        folders = [f.id for f in self.files]
        if len(folders) != len(set(folders)):
            raise ValueError("duplicate watched folder id")
        sized = [f.id for f in self.sizes]
        if len(sized) != len(set(sized)):
            raise ValueError("duplicate sized folder id")
        for action in self.actions:
            for step in action.steps:
                if step.ssh and step.ssh.target not in self.ssh:
                    raise ValueError(f"step '{step.name}' uses unknown ssh target '{step.ssh.target}'")
        return self

    def secret_names(self) -> set[str]:
        """Environment variables that http steps read, so they can be kept from child processes."""
        names: set[str] = set()
        for action in self.actions:
            for step in action.steps:
                if step.http:
                    for text in (step.http.url, *step.http.headers.values()):
                        names.update(ENV_REF.findall(text))
        return names

    def get(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)


def _read(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(path: str | Path) -> Config:
    return Config.model_validate(_read(path))


def load_actions(path: str | Path) -> ActionsConfig:
    return ActionsConfig.model_validate(_read(path))
