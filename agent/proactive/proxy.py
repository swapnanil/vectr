"""Localhost Anthropic-shaped API proxy (UPG-PRO-14/15/16).

The literal "vectr in the middle" of the wire. Point the agent harness at it
with `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`; it forwards `/v1/messages`
(and everything else) to the real Anthropic API, transparently, and — when
injection is enabled — appends deterministic proactive context AFTER the last
prompt-cache breakpoint so cached prefixes keep hitting.

Hard properties:
  * Transparent pass-through of streaming SSE and non-streaming responses,
    tool_use/tool_result, and every header the proxy does not own. The upstream
    API key passes through UNTOUCHED and is never stored, never logged.
  * Fail-open. If the intelligence layer errors or exceeds its tight time
    budget, the request is forwarded unmodified. If upstream is unreachable, an
    honest upstream-shaped error is returned. The proxy being down is never
    mysterious — unset ANTHROPIC_BASE_URL to bypass it entirely.
  * Cache-append discipline. Injected content is appended as the newest content;
    earlier messages are never mutated or reordered (see request_window).
  * Localhost-only, off by default, own opt-in.

No real API call is ever made in tests: the upstream client and the injection
provider are both injectable, so tests drive a local mock upstream.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from agent.proactive.cache import ResponseCache
from agent.proactive.request_window import append_context_block, assemble_window
from agent.proactive.settings import ProactiveSettings
from agent.proactive.types import InjectionResult, ProactiveWindow

# Headers vectr must not forward verbatim: hop-by-hop and length/encoding fields
# it recomputes. The Authorization / x-api-key headers are NOT here — they pass
# through untouched (and are never logged).
_HOP_BY_HOP = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
_RESPONSE_HOP_BY_HOP = frozenset(
    {"content-length", "transfer-encoding", "connection", "keep-alive"}
)


class InjectionProvider(Protocol):
    """Produces packed proactive context for one proxied request. The proxy
    treats any error or timeout as "inject nothing" (fail-open)."""

    async def inject(
        self, window: ProactiveWindow, *, session_id: str, channel: str
    ) -> InjectionResult:
        ...


@dataclass
class ProxyMetrics:
    requests: int = 0
    injected: int = 0
    inject_skipped: int = 0
    inject_bypassed_error: int = 0
    upstream_errors: int = 0
    response_cache_hits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def bump(self, field_name: str) -> None:
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + 1)

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "requests": self.requests,
                "injected": self.injected,
                "inject_skipped": self.inject_skipped,
                "inject_bypassed_error": self.inject_bypassed_error,
                "upstream_errors": self.upstream_errors,
                "response_cache_hits": self.response_cache_hits,
            }


def _forward_request_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in _HOP_BY_HOP:
            continue
        out[name] = value
    return out


def _forward_response_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in _RESPONSE_HOP_BY_HOP:
            continue
        out[name] = value
    return out


def _session_id(body: dict) -> str:
    """Deterministic per-conversation id for dedup/cooldown: a hash of the first
    user message's text (stable across the turns of one conversation). Structural
    identity, not content classification. "" when none can be derived."""
    try:
        for msg in body.get("messages") or []:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    seed = content
                elif isinstance(content, list):
                    seed = " ".join(
                        str(b.get("text") or "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    seed = ""
                if seed.strip():
                    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
                break
    except Exception:
        return ""
    return ""


def _response_cache_key(path: str, header_sig: str, forward_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(path.encode("utf-8"))
    h.update(b"\x00")
    h.update(header_sig.encode("utf-8"))
    h.update(b"\x00")
    h.update(forward_bytes)
    return h.hexdigest()


def _anthropic_error(status_code: int, message: str, err_type: str = "api_error") -> JSONResponse:
    """An Anthropic-error-shaped JSON body. Never carries the API key or any
    request header — honesty without leaking secrets."""
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


class VectrProxy:
    """Holds proxy state and serves the ASGI handler."""

    def __init__(
        self,
        settings: ProactiveSettings,
        *,
        injection_provider: InjectionProvider | None = None,
        response_cache: ResponseCache | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = injection_provider
        self._response_cache = response_cache
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self.metrics = ProxyMetrics()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    timeout = httpx.Timeout(
                        self._settings.proxy_read_timeout_s,
                        connect=self._settings.proxy_connect_timeout_s,
                    )
                    self._client = httpx.AsyncClient(
                        base_url=self._settings.proxy_upstream_base_url, timeout=timeout
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- injection -------------------------------------------------------

    async def _maybe_inject(self, forward_bytes: bytes) -> tuple[bytes, bool]:
        """Return (forward_bytes, injected). Fail-open at every step: a parse
        error, a provider error, or a budget overrun forwards the original bytes
        unmodified."""
        if not (self._settings.proxy_inject and self._provider is not None):
            self.metrics.bump("inject_skipped")
            return forward_bytes, False
        try:
            body = json.loads(forward_bytes)
        except Exception:
            self.metrics.bump("inject_skipped")
            return forward_bytes, False
        if not isinstance(body, dict) or not body.get("messages"):
            self.metrics.bump("inject_skipped")
            return forward_bytes, False

        window = assemble_window(body)
        if window.is_empty():
            self.metrics.bump("inject_skipped")
            return forward_bytes, False

        session = _session_id(body)
        budget_s = max(self._settings.proxy_inject_budget_ms, 1) / 1000.0
        try:
            result = await asyncio.wait_for(
                self._provider.inject(window, session_id=session, channel="proxy"),
                timeout=budget_s,
            )
        except (asyncio.TimeoutError, Exception):
            self.metrics.bump("inject_bypassed_error")
            return forward_bytes, False

        if result is None or result.is_empty():
            self.metrics.bump("inject_skipped")
            return forward_bytes, False

        new_body, ok = append_context_block(body, result.context)
        if not ok:
            self.metrics.bump("inject_skipped")
            return forward_bytes, False
        self.metrics.bump("injected")
        return json.dumps(new_body).encode("utf-8"), True

    # -- request handling ------------------------------------------------

    async def handle(self, request: Request) -> Response:
        self.metrics.bump("requests")
        raw = await request.body()
        path = request.url.path
        method = request.method
        is_messages = method == "POST" and path.rstrip("/").endswith("/v1/messages")

        forward_bytes = raw
        if is_messages:
            forward_bytes, _injected = await self._maybe_inject(raw)

        fwd_headers = _forward_request_headers(request.headers)

        # Exact-match response cache (opt-in). Keyed on the exact bytes upstream
        # will see, so a hit is provably a valid response for that request.
        cache = self._response_cache
        cache_key = ""
        if cache is not None and self._settings.response_cache_enabled and is_messages:
            header_sig = "|".join(
                f"{k}={fwd_headers[k]}"
                for k in sorted(fwd_headers)
                if k.lower() in ("anthropic-version", "anthropic-beta", "content-type")
            )
            cache_key = _response_cache_key(path, header_sig, forward_bytes)
            found, cached = cache.get(cache_key)
            if found:
                self.metrics.bump("response_cache_hits")
                return self._replay(cached)

        return await self._forward(
            request, method, path, forward_bytes, fwd_headers, cache, cache_key
        )

    def _replay(self, cached: dict) -> Response:
        chunks: list[bytes] = cached.get("chunks", [])
        status = cached.get("status", 200)
        headers = cached.get("headers", {})

        async def _iter():
            for ch in chunks:
                yield ch

        return StreamingResponse(_iter(), status_code=status, headers=headers)

    async def _forward(
        self,
        request: Request,
        method: str,
        path: str,
        forward_bytes: bytes,
        fwd_headers: dict[str, str],
        cache: ResponseCache | None,
        cache_key: str,
    ) -> Response:
        client = await self._get_client()
        url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))
        try:
            upstream_req = client.build_request(
                method, url, headers=fwd_headers, content=forward_bytes
            )
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            self.metrics.bump("upstream_errors")
            # Honest, key-free upstream-shaped error. 502: proxy reached but
            # could not complete the upstream call.
            return _anthropic_error(502, f"upstream request failed: {type(exc).__name__}")

        resp_headers = _forward_response_headers(upstream.headers)
        status = upstream.status_code
        cacheable = (
            cache is not None
            and self._settings.response_cache_enabled
            and cache_key
            and status == 200
        )
        captured: list[bytes] = []

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    if cacheable:
                        captured.append(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                if cacheable:
                    cache.put(
                        cache_key,
                        {"status": status, "headers": dict(resp_headers), "chunks": captured},
                    )

        return StreamingResponse(body_iter(), status_code=status, headers=resp_headers)


def build_proxy_app(
    settings: ProactiveSettings,
    *,
    injection_provider: InjectionProvider | None = None,
    response_cache: ResponseCache | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> Starlette:
    """Build the proxy ASGI app. Injection and upstream client are injectable so
    tests never touch the real API."""
    proxy = VectrProxy(
        settings,
        injection_provider=injection_provider,
        response_cache=response_cache,
        client_factory=client_factory,
    )

    async def _health(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "proxy": True, "metrics": proxy.metrics.as_dict()})

    async def _catch_all(request: Request) -> Response:
        return await proxy.handle(request)

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        try:
            yield
        finally:
            await proxy.aclose()

    app = Starlette(
        routes=[
            Route("/__vectr_proxy/health", _health, methods=["GET"]),
            Route("/{path:path}", _catch_all, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ],
        lifespan=_lifespan,
    )
    app.state.proxy = proxy
    return app
