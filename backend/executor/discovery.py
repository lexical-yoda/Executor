"""Services found from the running stacks themselves.

Configured services (``services:`` in config.yaml) stay as they are, with their
names, groups, links and checks. Around them:

- A compose project (stack) none of whose containers a configured service
  lists appears on its own, named after the stack, with a link and a TCP
  check on its first published port when the settings allow.
- A stack folder with no containers at all appears as a stopped stack.
- A configured service whose containers are all gone is "stopped" while its
  stack folder still exists, and dropped once the folder is gone too, so
  removing a stack removes its tile. The stack a container belonged to is
  remembered from its compose labels, because a removed container no longer
  says.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from .config import Check, Discovery, Service

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
TRUE = {"1", "true", "yes", "on"}


def slug(text: str) -> str:
    return _NOT_SLUG.sub("-", text.lower()).strip("-") or "stack"


def pretty(name: str) -> str:
    """A stack's folder name as a title: "paperless-ngx" -> "Paperless ngx"."""
    words = re.sub(r"[-_]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else name


@dataclass
class Plan:
    # Configured services to show, with "active" or "stopped".
    configured: list[tuple[Service, str]] = field(default_factory=list)
    # Configured service ids dropped because their stack is gone.
    removed: list[str] = field(default_factory=list)
    # Services made from stacks no configured service covers, with their stack name.
    discovered: list[tuple[Service, str, bool]] = field(default_factory=list)


def _names(c: dict) -> set[str]:
    """The names a container's stack may go by: its compose project and its folder."""
    names = {c.get("project"), posixpath.basename((c.get("working_dir") or "").rstrip("/"))}
    return {n.lower() for n in names if n}


def remember(memory: dict[str, list[str]], containers: dict[str, dict]) -> bool:
    """Note each container's stack names; returns whether anything changed."""
    changed = False
    for name, c in containers.items():
        names = sorted(_names(c))
        if names and memory.get(name) != names:
            memory[name] = names
            changed = True
    return changed


def _first(containers: list[dict], label: str) -> str | None:
    for c in containers:
        value = (c.get("labels") or {}).get(label)
        if value:
            return value
    return None


def plan(services: list[Service], containers: dict[str, dict] | None, stack_dirs: list[str] | None,
         memory: dict[str, list[str]], settings: Discovery | None) -> Plan:
    result = Plan()
    folders = {s.lower() for s in stack_dirs} if stack_dirs is not None else None
    claimed = {name for s in services for name in s.containers}

    # Configured services: active, stopped (stack still there) or removed (stack gone).
    covered_stacks: set[str] = set()
    for service in services:
        stacks = {n for c in service.containers for n in memory.get(c, [])}
        if containers is not None:
            stacks |= {n for c in service.containers if c in containers for n in _names(containers[c])}
        covered_stacks |= stacks
        state = "active"
        gone = containers is not None and service.containers and not any(c in containers for c in service.containers)
        if gone and stacks and folders is not None:
            state = "stopped" if stacks & folders else "removed"
        if state == "removed":
            result.removed.append(service.id)
        else:
            result.configured.append((service, state))

    if settings is None or containers is None:
        return result

    # Stacks nobody configured: group the containers by compose project.
    by_stack: dict[str, list[dict]] = {}
    for name, c in containers.items():
        stack = c.get("project")
        if stack:
            by_stack.setdefault(stack, []).append({**c, "name": name})
    ignore = {s.lower() for s in settings.ignore}
    taken = {s.id for s in services}
    for stack, members in sorted(by_stack.items()):
        if _names(members[0]) & (ignore | covered_stacks):
            continue
        if any(m["name"] in claimed for m in members):
            continue
        if (_first(members, "executor.hide") or "").lower() in TRUE:
            continue
        # The container named after the stack (or its service) leads: its state decides up or down.
        members.sort(key=lambda m: (m["name"] != stack and m.get("service") != stack, m["name"]))
        # The lowest published port of the leading container that publishes any.
        port = next((min(m["ports"]) for m in members if m.get("ports")), None)
        url = _first(members, "executor.url") or (f"http://{settings.link_host}:{port}"
                                                     if settings.link_host and port else None)
        check = Check(type="tcp", host=settings.probe_host, port=port) if settings.probe_host and port else None
        service_id = f"stack-{slug(stack)}"
        if service_id in taken:
            continue
        taken.add(service_id)
        result.discovered.append((Service(
            id=service_id,
            name=_first(members, "executor.name") or settings.names.get(stack) or pretty(stack),
            group=_first(members, "executor.group") or settings.groups.get(stack) or settings.group,
            url=url,
            description=None,
            check=check,
            containers=[m["name"] for m in members],
        ), stack, False))

    # Stack folders with nothing running and no configured service.
    if folders is not None:
        running = {n for members in by_stack.values() for m in members for n in _names(m)}
        for name in sorted(stack_dirs or []):
            low = name.lower()
            if low in running or low in covered_stacks or low in ignore:
                continue
            service_id = f"stack-{slug(name)}"
            if service_id in taken:
                continue
            taken.add(service_id)
            result.discovered.append((Service(
                id=service_id, name=settings.names.get(name) or pretty(name),
                group=settings.groups.get(name) or settings.group, containers=[],
            ), name, True))
    return result
