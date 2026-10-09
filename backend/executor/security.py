"""Request guard for the web process.

There is no login: access is limited to a few WireGuard peers. Because the
browser on one of those peers could still be tricked by another website into
sending requests here, the guard also checks the Host header (DNS rebinding)
and requires a custom header plus a same-origin check on anything that is not
a plain read (cross-site request forgery).
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("executor.security")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ACTION_HEADER = b"x-executor"

SECURITY_HEADERS = [
    (b"content-security-policy",
     b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
     b"connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class Guard:
    def __init__(self, app: ASGIApp, allowed_clients: list[str], allowed_hosts: list[str],
                 denied_clients: list[str] | None = None) -> None:
        self.app = app
        self.networks = [ipaddress.ip_network(c, strict=False) for c in allowed_clients]
        self.denied = [ipaddress.ip_network(c, strict=False) for c in denied_clients or []]
        self.hosts = {h.lower() for h in allowed_hosts}

    def _client_allowed(self, ip: str | None) -> bool:
        if not ip:
            return False
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if any(address in network for network in self.denied):
            return False
        return any(address in network for network in self.networks)

    @staticmethod
    def _is_loopback(ip: str | None) -> bool:
        try:
            return bool(ip) and ipaddress.ip_address(ip).is_loopback
        except ValueError:
            return False

    async def _deny(self, send: Send, status: int, reason: str) -> None:
        body = reason.encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS],
        })
        await send({"type": "http.response.body", "body": body})

    def check(self, scope: Scope) -> tuple[int, str] | None:
        """Return (status, reason) to refuse the request, or None to allow it."""
        client = scope.get("client")
        ip = client[0] if client else None
        path = scope.get("path", "")
        headers = {k.lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        # The container healthcheck calls in over loopback.
        if path == "/healthz" and self._is_loopback(ip):
            return None

        if not self._client_allowed(ip):
            return 403, f"Client {ip} is not allowed."

        host = _host_only(headers.get(b"host", ""))
        if host not in self.hosts:
            return 400, "Unexpected Host header."

        method = scope.get("method", "GET").upper()
        if method in SAFE_METHODS:
            return None

        if headers.get(ACTION_HEADER) != "1":
            return 403, "Missing request header."
        if not headers.get(b"content-type", "").startswith("application/json"):
            return 415, "JSON only."
        origin = headers.get(b"origin")
        if origin and _host_only(urlsplit(origin).netloc) not in self.hosts:
            return 403, "Cross-origin request refused."
        fetch_site = headers.get(b"sec-fetch-site")
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return 403, "Cross-site request refused."
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        refusal = self.check(scope)
        if refusal:
            client = scope.get("client")
            log.warning("refused %s %s from %s: %s", scope.get("method"), scope.get("path"),
                        client[0] if client else "?", refusal[1])
            await self._deny(send, *refusal)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)
