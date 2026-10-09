"""Minimal async client for the Docker Engine API over its unix socket."""

from __future__ import annotations

import httpx


def _health(status: str) -> str | None:
    if "(healthy)" in status:
        return "healthy"
    if "(unhealthy)" in status:
        return "unhealthy"
    if "(health: starting)" in status:
        return "starting"
    return None


class DockerAPI:
    def __init__(self, socket: str = "/var/run/docker.sock") -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket),
            base_url="http://docker",
            timeout=10,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def containers(self) -> list[dict]:
        response = await self._client.get("/containers/json", params={"all": "true"})
        response.raise_for_status()
        result = []
        for item in response.json():
            names = item.get("Names") or []
            name = names[0].lstrip("/") if names else item["Id"][:12]
            status = item.get("Status", "")
            result.append(
                {
                    "name": name,
                    "state": item.get("State", "unknown"),
                    "health": _health(status),
                    "status": status,
                    "image": item.get("Image", ""),
                }
            )
        return sorted(result, key=lambda c: c["name"].lower())

    async def health(self, name: str) -> str | None:
        """Health status of one container, or its plain state if it has no healthcheck."""
        response = await self._client.get(f"/containers/{name}/json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        state = response.json().get("State", {})
        health = state.get("Health") or {}
        return health.get("Status") or state.get("Status")
