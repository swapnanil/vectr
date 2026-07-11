"""Injection provider that backs the proxy with the local vectr daemon.

The proxy runs as its own process; its intelligence comes from the workspace's
already-running daemon over localhost — the single source of truth for the notes
store and index, so there is no second store to keep in sync. Every failure mode
(daemon down, slow, error) resolves to "inject nothing", which the proxy then
forwards unmodified (fail-open).
"""
from __future__ import annotations

import httpx

from agent.proactive.types import InjectionResult, ProactiveWindow


class DaemonInjectionProvider:
    """Calls `POST /v1/proactive` on the local daemon to get packed context."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 0.5,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._api_key = api_key
        # An injected client (tests) is used as-is; otherwise one is built lazily.
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def inject(
        self, window: ProactiveWindow, *, session_id: str, channel: str
    ) -> InjectionResult:
        try:
            client = await self._get_client()
            headers = {"content-type": "application/json"}
            if self._api_key:
                headers["X-Api-Key"] = self._api_key
            resp = await client.post(
                f"{self._base_url}/v1/proactive",
                json={
                    "text": window.text,
                    "file_paths": window.file_paths,
                    "symbols": window.symbols,
                    "session_id": session_id,
                    "channel": channel,
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return InjectionResult.empty()
        context = data.get("context") or ""
        if not context:
            return InjectionResult.empty()
        return InjectionResult(
            context=context,
            item_count=int(data.get("item_count") or 0),
            anchor_ids=tuple(data.get("anchor_ids") or ()),
            scores=tuple(data.get("scores") or ()),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
