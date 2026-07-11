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
