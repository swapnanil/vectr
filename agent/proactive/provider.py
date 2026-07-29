"""Injection provider that backs the proxy with the local vectr daemon.

The proxy runs as its own process; its intelligence comes from the workspace's
already-running daemon over localhost — the single source of truth for the notes
store and index, so there is no second store to keep in sync. Every failure mode
(daemon down, slow, error) resolves to "inject nothing", which the proxy then
forwards unmodified (fail-open).
"""
from __future__ import annotations

import threading
from collections import deque

import httpx

from agent.proactive.types import InjectionResult, ProactiveWindow

# Ceiling on delivered-but-unconfirmed tokens queued for piggyback. Normally
# holds 0 or 1 (each is drained by the very next call); it only grows while the
# daemon is unreachable, and then the OLDEST is dropped — an unconfirmed
# delivery just leaves its note uncharged, so it may be injected again later.
# Noise, never loss.
_MAX_PENDING_CONFIRMS = 64


class DaemonInjectionProvider:
    """Calls `POST /v1/proactive` on the local daemon to get packed context.

    DELIVERY CONFIRMATION (UPG-PROXY-APPEND-BURNS-COOLDOWN). The daemon cannot
    see whether a retrieved block actually reached the model; the proxy can,
    but only after `append_context_block` has run. So this provider asks the
    daemon to select WITHOUT charging the cooldown ledger (`defer_charge`), and
    hands back the opaque `delivery_token` of anything the proxy confirms it
    delivered. The token rides along on the NEXT `/v1/proactive` request rather
    than a separate round trip: the daemon charges it before that request's own
    selection, so a confirmed note is already suppressed by the time the gate
    looks — no extra latency in the request path, and no window in which the
    same note can be selected twice by SEQUENTIAL requests.

    Against a daemon that predates deferred charging, the extra request fields
    are ignored and no `charge_deferred` comes back; this provider then queues
    nothing and confirms nothing, and the daemon charges at retrieval exactly
    as it always did.
    """

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
        # Confirmed-delivered tokens awaiting piggyback. Bounded; guarded by a
        # plain lock because `confirm_delivery` is called from the proxy's
        # request path while `inject` may be draining it concurrently.
        self._pending_confirms: deque[str] = deque(maxlen=_MAX_PENDING_CONFIRMS)
        self._pending_lock = threading.Lock()

    def confirm_delivery(self, token: str) -> None:
        """Record that `token`'s block was confirmed delivered to the model.

        Pure local bookkeeping — no I/O, so the proxy can call it inline on the
        request path. The token is transported on the next `inject()`.
        """
        if not token:
            return
        with self._pending_lock:
            self._pending_confirms.append(token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def inject(
        self, window: ProactiveWindow, *, session_id: str, channel: str
    ) -> InjectionResult:
        # Take (don't clear) the queued confirmations: they are only dropped
        # once the daemon has actually accepted them, so a failed call retries
        # them on the next request instead of silently losing the charge.
        with self._pending_lock:
            sending = list(self._pending_confirms)
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
                    "edited_file_paths": window.edited_file_paths,
                    "session_id": session_id,
                    "channel": channel,
                    "defer_charge": True,
                    # One token per request keeps the wire shape stable; the
                    # rest drain on subsequent requests.
                    "confirm_token": sending[0] if sending else "",
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return InjectionResult.empty()
        if sending:
            # Accepted by the daemon (2xx above): the token has been consumed
            # there, so stop carrying it. Removed by identity, never by
            # position, so a concurrent `confirm_delivery` append cannot be
            # dropped along with it.
            with self._pending_lock:
                try:
                    self._pending_confirms.remove(sending[0])
                except ValueError:
                    pass
        context = data.get("context") or ""
        if not context:
            return InjectionResult.empty()
        # `states` is deliberately left empty here and is NOT a claim that
        # every item is active. Per-item lifecycle state is daemon-side
        # metadata: it is folded, rendered and audited inside
        # `VectrService.proactive_context()` (UPG-PROXY-AUDIT-DURABLE), and
        # `/v1/proactive`'s `ProactiveResponse` intentionally does not carry
        # it — widening that response model would change a contract asserted
        # by exact-equality tests for no consumer that needs it. Nothing on
        # the proxy side reads `.states`; an empty tuple here means "not
        # transported", never "no revoked items".
        # Feature detection: a daemon that predates deferred charging has no
        # `charge_deferred` in its response model, so this reads False and the
        # token stays empty — the proxy then has nothing to confirm, and that
        # daemon's retrieval-time charge stands, exactly as before.
        delivery_token = ""
        if data.get("charge_deferred"):
            delivery_token = str(data.get("delivery_token") or "")
        return InjectionResult(
            context=context,
            item_count=int(data.get("item_count") or 0),
            anchor_ids=tuple(data.get("anchor_ids") or ()),
            scores=tuple(data.get("scores") or ()),
            states=(),
            delivery_token=delivery_token,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
