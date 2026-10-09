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
    # Hostnames whose TLS certificate expiry is shown.
    certificates: list[str] = []
    interval: float = 300


class GlobePoint(BaseModel):
    label: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class JellyfinIntegration(BaseModel):
    # Server URL as seen from the web container. The API key comes from the
    # environment (JELLYFIN_API_KEY), never from this file.
    url: str
    timeout: float = 8.0
    # Days of location history to keep in the web data folder. 0 turns
    # history (and the geolocation download) off.
    history_days: int = Field(default=90, ge=0, le=400)
    # Where streams are served from, drawn as the end of each arc on the map.
    hub: GlobePoint | None = None


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


class Integrations(BaseModel):
    beszel: BeszelIntegration | None = None
    edge: EdgeIntegration | None = None
    jellyfin: JellyfinIntegration | None = None
    backups: BackupsIntegration | None = None
    media: MediaIntegration | None = None


class Config(BaseModel):
    security: Security
    site: Site = Site()
    settings: Settings = Settings()
    integrations: Integrations = Integrations()
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
    steps: list[Step] = Field(min_length=1)


class WatchedFolder(BaseModel):
    # A folder whose file names, sizes and times the runner may report, for
    # backups the web container cannot read itself (root-only folders).
    id: Slug
    path: str
    # Plain file names in the folder whose last lines may be reported.
    tail: list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+$")]] = []


class ActionsConfig(BaseModel):
    ssh: dict[str, SshTarget] = {}
    files: list[WatchedFolder] = []
    actions: list[Action] = []

    @model_validator(mode="after")
    def _unique(self) -> ActionsConfig:
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action id")
        folders = [f.id for f in self.files]
        if len(folders) != len(set(folders)):
            raise ValueError("duplicate watched folder id")
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
