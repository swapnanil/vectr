"""DaemonInjectionProvider tests (UPG-PRO-16): the proxy's daemon client.

Drives a stub daemon in-process — never a real daemon. Fail-open is the key
behaviour: any daemon error becomes "inject nothing".
"""
from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent.proactive.provider import DaemonInjectionProvider
from agent.proactive.types import ProactiveWindow


def _stub_daemon(response_json, status=200, capture=None):
    async def _proactive(request):
        if capture is not None:
            capture["body"] = await request.json()
            capture["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return JSONResponse(status_code=status, content=response_json)

    return Starlette(routes=[Route("/v1/proactive", _proactive, methods=["POST"])])


def _provider(app, **kw):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://daemon")
    return DaemonInjectionProvider("http://daemon", client=client, **kw)


async def test_provider_returns_injection_result():
    capture = {}
    app = _stub_daemon(
        {"context": "CTX-BODY", "item_count": 2, "anchor_ids": ["note:1", "note:2"], "scores": [1.0, 0.8]},
        capture=capture,
    )
    prov = _provider(app, api_key="k1")
    w = ProactiveWindow(text="lock", file_paths=["/x/a.py"], symbols=["Foo"])
    result = await prov.inject(w, session_id="s1", channel="proxy")
    assert result.context == "CTX-BODY"
    assert result.item_count == 2
    assert result.anchor_ids == ("note:1", "note:2")
    # Window + attribution threaded to the daemon.
    assert capture["body"]["text"] == "lock"
    assert capture["body"]["file_paths"] == ["/x/a.py"]
    assert capture["body"]["session_id"] == "s1"
    assert capture["headers"].get("x-api-key") == "k1"
    await prov.aclose()


async def test_provider_empty_context_is_empty_result():
    app = _stub_daemon({"context": "", "item_count": 0, "anchor_ids": [], "scores": []})
    prov = _provider(app)
    result = await prov.inject(ProactiveWindow(text="x"), session_id="", channel="proxy")
    assert result.is_empty()
    await prov.aclose()


async def test_provider_daemon_error_is_fail_open():
    app = _stub_daemon({"error": "boom"}, status=500)
    prov = _provider(app)
    result = await prov.inject(ProactiveWindow(text="x"), session_id="", channel="proxy")
    assert result.is_empty()  # a 500 -> inject nothing, never raise
    await prov.aclose()


async def test_provider_unreachable_is_fail_open():
    class _Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("down", request=request)

    client = httpx.AsyncClient(transport=_Down(), base_url="http://daemon")
    prov = DaemonInjectionProvider("http://daemon", client=client)
    result = await prov.inject(ProactiveWindow(text="x"), session_id="", channel="proxy")
    assert result.is_empty()
    await prov.aclose()
