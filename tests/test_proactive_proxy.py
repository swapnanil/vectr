"""Proxy integration tests (UPG-PRO-14/15/16).

Every upstream call goes to a local in-process mock — NEVER the real API. The
injection provider is faked. Covers transparent pass-through (streaming +
non-streaming), tool_use SSE byte-exactness, error passthrough, upstream-down
handling, cache-safe injection, fail-open, key hygiene, response caching, and
concurrency.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

from agent.proactive.cache import ResponseCache
from agent.proactive.proxy import build_proxy_app
from agent.proactive.settings import ProactiveSettings
from agent.proactive.types import InjectionResult
from tests._proactive_upstream import MockUpstream, full_sse_bytes, unreachable_client_factory

_BASE = dict(
    enabled=True, min_similarity=0.35, max_items_per_event=3, max_chars_per_event=800,
    cooldown_items=30, matcher_structural_note=True, matcher_semantic_note=True,
    matcher_code_search=False, proxy_enabled=True, proxy_host="127.0.0.1", proxy_port=19000,
    proxy_upstream_base_url="http://upstream", proxy_connect_timeout_s=10.0,
    proxy_read_timeout_s=600.0, proxy_inject=True, proxy_inject_budget_ms=40,
    proxy_inject_provider_timeout_fraction=0.8, proxy_inject_provider_timeout_max_s=2.0,
    cache_enabled=False, cache_max_entries=2048, cache_ttl_seconds=0.0,
    cache_similarity_threshold=1.0, response_cache_enabled=False,
    response_cache_ttl_seconds=60.0, response_cache_max_entries=256,
)


def _settings(**over):
    d = dict(_BASE)
    d.update(over)
    return ProactiveSettings(**d)


class _Provider:
    def __init__(self, context="PROACTIVE: note #1", delay=0.0, raise_exc=False):
        self.context = context
        self.delay = delay
        self.raise_exc = raise_exc
        self.seen = []

    async def inject(self, window, *, session_id, channel):
        self.seen.append((window, session_id, channel))
        if self.raise_exc:
            raise RuntimeError("provider boom")
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.context:
            return InjectionResult.empty()
        return InjectionResult(context=self.context, item_count=1,
                               anchor_ids=("note:1",), scores=(0.9,))


def _body():
    return {
        "model": "claude-x",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "explain the lock"}]},
        ],
    }


async def _drive(app, body=None, headers=None, method="POST", path="/v1/messages"):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        if method == "GET":
            return await client.get(path, headers=headers or {})
        return await client.post(path, content=json.dumps(body or _body()), headers=headers or {})


# -- transparent pass-through ----------------------------------------------

async def test_non_streaming_passthrough():
    up = MockUpstream()
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=up.client_factory())
    resp = await _drive(app)
    assert resp.status_code == 200
    assert resp.json() == up.response_json
    assert up.call_count == 1


async def test_streaming_sse_byte_exact_with_tool_use():
    up = MockUpstream()
    up.mode = "sse"
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=up.client_factory())
    resp = await _drive(app)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.content == full_sse_bytes()  # byte-exact relay incl. tool_use blocks


async def test_error_status_passthrough():
    up = MockUpstream()
    up.mode = "error"
    up.status = 429
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=up.client_factory())
    resp = await _drive(app)
    assert resp.status_code == 429
    assert resp.json()["type"] == "error"


async def test_upstream_unreachable_returns_shaped_error():
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=unreachable_client_factory())
    resp = await _drive(app)
    assert resp.status_code == 502
    body = resp.json()
    assert body["type"] == "error" and body["error"]["type"] == "api_error"


# -- injection --------------------------------------------------------------

async def test_injection_appends_context_cache_safe():
    up = MockUpstream()
    prov = _Provider(context="PROACTIVE: note #1")
    app = build_proxy_app(_settings(), injection_provider=prov, client_factory=up.client_factory())
    resp = await _drive(app)
    assert resp.status_code == 200
    forwarded = up.last_request["json"]
    last_content = forwarded["messages"][-1]["content"]
    # Injected block appended as newest content; original block untouched.
    assert last_content[0] == {"type": "text", "text": "explain the lock"}
    assert last_content[-1] == {"type": "text", "text": "PROACTIVE: note #1"}
    assert app.state.proxy.metrics.as_dict()["injected"] == 1


async def test_injection_empty_forwards_unmodified():
    up = MockUpstream()
    prov = _Provider(context="")  # nothing to inject
    app = build_proxy_app(_settings(), injection_provider=prov, client_factory=up.client_factory())
    await _drive(app)
    forwarded = up.last_request["json"]
    assert forwarded["messages"][-1]["content"] == [{"type": "text", "text": "explain the lock"}]


async def test_injection_provider_error_is_fail_open():
    up = MockUpstream()
    prov = _Provider(raise_exc=True)
    app = build_proxy_app(_settings(), injection_provider=prov, client_factory=up.client_factory())
    resp = await _drive(app)
    assert resp.status_code == 200  # request still went through
    forwarded = up.last_request["json"]
    assert forwarded["messages"][-1]["content"] == [{"type": "text", "text": "explain the lock"}]
    assert app.state.proxy.metrics.as_dict()["inject_bypassed_error"] == 1


async def test_injection_timeout_is_fail_open():
    up = MockUpstream()
    prov = _Provider(context="LATE", delay=0.5)  # 500ms > 20ms budget
    app = build_proxy_app(
        _settings(proxy_inject_budget_ms=20), injection_provider=prov,
        client_factory=up.client_factory(),
    )
    resp = await _drive(app)
    assert resp.status_code == 200
    forwarded = up.last_request["json"]
    assert forwarded["messages"][-1]["content"] == [{"type": "text", "text": "explain the lock"}]
    assert app.state.proxy.metrics.as_dict()["inject_bypassed_error"] == 1


# -- bypass diagnostics (UPG-PROXY-SILENT-BYPASS) ----------------------------


async def test_bypass_error_logs_one_warning_with_exception_and_elapsed_ms(caplog):
    import logging

    up = MockUpstream()
    prov = _Provider(raise_exc=True)
    app = build_proxy_app(_settings(), injection_provider=prov, client_factory=up.client_factory())
    with caplog.at_level(logging.WARNING, logger="agent.proactive.proxy"):
        resp = await _drive(app)
    assert resp.status_code == 200  # original bytes still forwarded (fail-open)
    forwarded = up.last_request["json"]
    assert forwarded["messages"][-1]["content"] == [{"type": "text", "text": "explain the lock"}]
    assert app.state.proxy.metrics.as_dict()["inject_bypassed_error"] == 1

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "RuntimeError" in msg          # exception class name
    assert "ms" in msg                    # elapsed-ms figure present
    # Metadata only — never the conversation content or the provider's context.
    assert "explain the lock" not in msg
    assert "PROACTIVE" not in msg


async def test_bypass_timeout_logs_one_warning_with_timeout_error_and_elapsed_ms(caplog):
    import logging

    up = MockUpstream()
    prov = _Provider(context="LATE", delay=0.5)  # 500ms > 20ms budget
    app = build_proxy_app(
        _settings(proxy_inject_budget_ms=20), injection_provider=prov,
        client_factory=up.client_factory(),
    )
    with caplog.at_level(logging.WARNING, logger="agent.proactive.proxy"):
        resp = await _drive(app)
    assert resp.status_code == 200
    assert app.state.proxy.metrics.as_dict()["inject_bypassed_error"] == 1

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "TimeoutError" in msg
    assert "ms" in msg


async def test_skip_branches_log_debug_reason_no_warning(caplog):
    import logging

    up = MockUpstream()
    prov = _Provider(context="")  # provider returns nothing to inject
    app = build_proxy_app(_settings(), injection_provider=prov, client_factory=up.client_factory())
    with caplog.at_level(logging.DEBUG, logger="agent.proactive.proxy"):
        await _drive(app)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("empty result" in m for m in debugs)


# -- key hygiene ------------------------------------------------------------

async def test_api_key_forwarded_untouched_never_leaked():
    up = MockUpstream()
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=up.client_factory())
    secret = "sk-ant-SECRETKEY123"
    resp = await _drive(app, headers={"x-api-key": secret, "anthropic-version": "2023-06-01"})
    # Forwarded verbatim to upstream.
    assert up.last_request["headers"].get("x-api-key") == secret
    # Never surfaced in the proxy's own health/metrics output.
    health = await _drive(app, method="GET", path="/__vectr_proxy/health")
    assert secret not in health.text


async def test_upstream_error_body_has_no_key():
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=unreachable_client_factory())
    resp = await _drive(app, headers={"x-api-key": "sk-ant-SECRET"})
    assert "sk-ant-SECRET" not in resp.text


# -- response cache ---------------------------------------------------------

async def test_response_cache_exact_match_serves_from_cache():
    up = MockUpstream()
    cache = ResponseCache(ttl_seconds=60.0)
    app = build_proxy_app(
        _settings(proxy_inject=False, response_cache_enabled=True),
        response_cache=cache, client_factory=up.client_factory(),
    )
    r1 = await _drive(app)
    r2 = await _drive(app)  # byte-identical request
    assert up.call_count == 1              # upstream hit once
    assert r2.content == r1.content        # byte-exact replay
    assert app.state.proxy.metrics.as_dict()["response_cache_hits"] == 1


# -- concurrency ------------------------------------------------------------

async def test_concurrent_requests_all_succeed():
    up = MockUpstream()
    app = build_proxy_app(_settings(proxy_inject=False), client_factory=up.client_factory())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        results = await asyncio.gather(*[
            client.post("/v1/messages", content=json.dumps(_body())) for _ in range(10)
        ])
    assert all(r.status_code == 200 for r in results)
    assert up.call_count == 10
