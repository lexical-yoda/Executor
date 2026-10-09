"""Configuration models.

Two files, deliberately separate:

- ``config.yaml`` is read by the web process: machines, services, security.
- ``actions.yaml`` is read only by the runner. The web process never sees the
  commands, so a compromised web container cannot add or change what runs.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]


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


class Integrations(BaseModel):
    beszel: BeszelIntegration | None = None


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


class Step(BaseModel):
    name: str
    run: list[str] | None = None
    cwd: str | None = None
    wait_healthy: str | None = None
    timeout: float = 300

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        if bool(self.run) == bool(self.wait_healthy):
            raise ValueError(f"step '{self.name}' needs exactly one of run or wait_healthy")
        return self


class Action(BaseModel):
    id: Slug
    title: str
    description: str = ""
    confirm: str
    danger: Literal["low", "medium", "high"] = "medium"
    steps: list[Step] = Field(min_length=1)


class ActionsConfig(BaseModel):
    actions: list[Action] = []

    @model_validator(mode="after")
    def _unique(self) -> ActionsConfig:
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action id")
        return self

    def get(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)


def _read(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(path: str | Path) -> Config:
    return Config.model_validate(_read(path))


def load_actions(path: str | Path) -> ActionsConfig:
    return ActionsConfig.model_validate(_read(path))
