"""Pi-hole (v6): DNS queries, what was blocked, clients and blocking state.

Read with an app password (Pi-hole: Settings > Web interface / API > app
password) from PIHOLE_PASSWORD. Pi-hole has no read-only login, so the client
only ever reads, and logs out when Executor stops so it does not hold one of
Pi-hole's limited sessions.
"""

from __future__ import annotations

import httpx


def summarize(summary: dict, history: dict | None, blocked: dict | None, clients: dict | None,
              blocking: dict | None, version: dict | None) -> dict:
    queries = summary.get("queries") or {}
    gravity = summary.get("gravity") or {}
    slots = [{"t": h.get("timestamp"), "total": int(h.get("total") or 0), "blocked": int(h.get("blocked") or 0)}
             for h in (history or {}).get("history") or [] if isinstance(h.get("timestamp"), (int, float))]
    core = (((version or {}).get("version") or {}).get("core") or {}).get("local") or {}
    total = int(queries.get("total") or 0)
    return {
        "queries": total,
        "blocked": int(queries.get("blocked") or 0),
        "pct_blocked": round(float(queries.get("percent_blocked") or 0), 1),
        "cached": int(queries.get("cached") or 0),
        "forwarded": int(queries.get("forwarded") or 0),
        "unique_domains": queries.get("unique_domains"),
        "clients_active": (summary.get("clients") or {}).get("active"),
        "blocklist_domains": gravity.get("domains_being_blocked"),
        "blocklist_updated": gravity.get("last_update"),
        "blocking": (blocking or {}).get("blocking"),
        "blocking_timer": (blocking or {}).get("timer"),
        "history": slots,
        "top_blocked": [{"domain": d.get("domain"), "count": d.get("count")}
                        for d in (blocked or {}).get("domains") or []][:10],
        "top_clients": [{"name": c.get("name") or c.get("ip"), "ip": c.get("ip"), "count": c.get("count")}
                        for c in (clients or {}).get("clients") or []][:10],
        "version": core.get("version"),
    }


class PiHole:
    def __init__(self, url: str, password: str, timeout: float = 8.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._password = password
        self._sid: str | None = None
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"Accept": "application/json"})

    async def close(self) -> None:
        if self._sid:
            try:
                await self._client.delete("/api/auth", headers={"X-FTL-SID": self._sid})
            except httpx.HTTPError:
                pass
        await self._client.aclose()

    async def _login(self) -> None:
        response = await self._client.post("/api/auth", json={"password": self._password})
        session = (response.json() if response.content else {}).get("session") or {}
        if response.status_code == 429:
            raise RuntimeError("Pi-hole has no free API sessions left")
        if not session.get("valid") or not session.get("sid"):
            raise RuntimeError("Pi-hole refused the password")
        self._sid = session["sid"]

    async def _get(self, path: str, **params):
        if not self._sid:
            await self._login()
        response = await self._client.get(path, params=params or None, headers={"X-FTL-SID": self._sid or ""})
        if response.status_code == 401:
            # The session expired: log in once more.
            await self._login()
            response = await self._client.get(path, params=params or None, headers={"X-FTL-SID": self._sid or ""})
        response.raise_for_status()
        return response.json()

    async def _optional(self, path: str, **params):
        try:
            return await self._get(path, **params)
        except httpx.HTTPStatusError:
            return None

    async def status(self) -> dict:
        summary = await self._get("/api/stats/summary")
        return summarize(
            summary,
            await self._optional("/api/history"),
            await self._optional("/api/stats/top_domains", blocked="true", count=10),
            await self._optional("/api/stats/top_clients", count=10),
            await self._optional("/api/dns/blocking"),
            await self._optional("/api/info/version"),
        )
