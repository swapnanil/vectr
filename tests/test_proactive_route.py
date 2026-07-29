"""Dedicated REST route test for POST /v1/proactive (UPG-PRO-7).

Uses a mocked VectrService (real return shape) — the route contract only, no
model loading. Every new REST route gets its own test (test-mock-fidelity rule).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import app


def _client(svc):
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            yield c


@pytest.fixture
def svc():
    s = MagicMock()
    s.search_only = False
    s.proactive_context.return_value = {
        "context": "vectr proactive context (deterministic, local; verify before relying):\n"
                   "note #12 (gotcha, anchored to resolver.py): drops on scope exit",
        "item_count": 1,
        "anchor_ids": ["note:12"],
        "scores": [1.0],
    }
    return s


@pytest.fixture
def client(svc):
    yield from _client(svc)


def test_proactive_route_returns_packed_context(client, svc):
    resp = client.post("/v1/proactive", json={
        "text": "how does the workspace lock work",
        "file_paths": ["/x/resolver.py"],
        "symbols": [],
        "session_id": "s1",
        "channel": "proxy",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["item_count"] == 1
    assert data["anchor_ids"] == ["note:12"]
    assert "resolver.py" in data["context"]
    assert "processing_ms" in data
    # Real args threaded to the service.
    _args, kwargs = svc.proactive_context.call_args
    assert kwargs["text"] == "how does the workspace lock work"
    assert kwargs["file_paths"] == ["/x/resolver.py"]
    assert kwargs["channel"] == "proxy"


def test_proactive_route_empty_when_service_returns_nothing(client, svc):
    svc.proactive_context.return_value = {
        "context": "", "item_count": 0, "anchor_ids": [], "scores": []
    }
    resp = client.post("/v1/proactive", json={"text": "off-topic"})
    assert resp.status_code == 200
    assert resp.json()["context"] == ""
    assert resp.json()["item_count"] == 0


def test_proactive_route_search_only_returns_empty():
    s = MagicMock()
    s.search_only = True
    for c in _client(s):
        resp = c.post("/v1/proactive", json={"text": "x"})
        assert resp.status_code == 200
        assert resp.json()["item_count"] == 0
        s.proactive_context.assert_not_called()


def test_proactive_route_service_error_never_500(client, svc):
    svc.proactive_context.side_effect = RuntimeError("boom")
    resp = client.post("/v1/proactive", json={"text": "x"})
    assert resp.status_code == 200  # hook/proxy-facing: never errors the caller
    assert resp.json()["item_count"] == 0


def test_proactive_route_threads_edited_file_paths_to_service(client, svc):
    """UPG-PROXY-INJECT-SINGLE-TURN: `edited_file_paths` is a new, optional
    request field -- round-trips to the service call unmodified when
    present, and defaults to the model's own default (empty list) when
    omitted, never a validation error."""
    resp = client.post("/v1/proactive", json={
        "text": "how does the workspace lock work",
        "file_paths": ["/x/resolver.py"],
        "edited_file_paths": ["/x/resolver.py"],
        "session_id": "s1",
        "channel": "proxy",
    })
    assert resp.status_code == 200
    _args, kwargs = svc.proactive_context.call_args
    assert kwargs["edited_file_paths"] == ["/x/resolver.py"]

    resp_omitted = client.post("/v1/proactive", json={"text": "x", "file_paths": ["/x/resolver.py"]})
    assert resp_omitted.status_code == 200
    _args, kwargs_omitted = svc.proactive_context.call_args
    assert kwargs_omitted["edited_file_paths"] == []


def test_proactive_route_backward_compat_session_id_omitted_or_legacy_shaped(client, svc):
    """Backward compat (UPG-PROXY-COOLDOWN-SESSION-IDENTITY). `session_id`
    stays an opaque string on the wire — this route never validates its
    shape. An older proxy client sending a legacy sha256-hex-looking
    session_id, or a caller omitting session_id entirely, both keep getting
    a well-formed 200 response, never a validation error, never a crash."""
    resp_omitted = client.post("/v1/proactive", json={"text": "how does the workspace lock work"})
    assert resp_omitted.status_code == 200
    data_omitted = resp_omitted.json()
    assert set(data_omitted) >= {"context", "item_count", "anchor_ids", "scores", "processing_ms"}
    _args, kwargs_omitted = svc.proactive_context.call_args
    assert kwargs_omitted["session_id"] == ""  # ProactiveRequest.session_id default

    legacy_sid = "a3f5c9e21b7d4f68"  # shaped like the old sha256(...)[:16] session id
    resp_legacy = client.post("/v1/proactive", json={
        "text": "how does the workspace lock work", "session_id": legacy_sid,
    })
    assert resp_legacy.status_code == 200
    data_legacy = resp_legacy.json()
    assert set(data_legacy) >= {"context", "item_count", "anchor_ids", "scores", "processing_ms"}
    _args, kwargs_legacy = svc.proactive_context.call_args
    assert kwargs_legacy["session_id"] == legacy_sid
