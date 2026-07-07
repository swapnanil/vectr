"""Tests for UPG-MCP-SESSION-ID-HANDSHAKE.

The per-session EvictionAdvisor (VectrService._advisor_for) only isolates
sessions when the /mcp transport resolves a session_id. Standard MCP
streamable-HTTP clients never send vectr's invented `_meta.sessionId` or
`X-Session-ID` — they use the server-assigned `Mcp-Session-Id` header,
handed back on `initialize` and echoed by the client on every later request.
These tests confirm:

  1. `initialize` responses carry a fresh `Mcp-Session-Id` response header.
  2. Two /mcp callers using distinct `Mcp-Session-Id` headers land on
     distinct EvictionAdvisor instances (real service, real dispatch).
  3. The legacy `X-Session-ID` fallback still works for callers that don't
     send the handshake header.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import make_py, _DummyEmbedProvider, _RealVectrService


def tool_call(name: str, arguments: dict | None = None, id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def _make_real_service(tmp_path, monkeypatch, num_files: int = 1):
    from agent import indexer as idx_module
    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    for i in range(num_files):
        make_py(
            tmp_path, f"mod{i}.py",
            f"def handler_{i}():\n    \"\"\"Handles request type {i}.\"\"\"\n    return {i}\n",
        )
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        svc = _RealVectrService(workspace_root=str(tmp_path))
    svc.index(str(tmp_path))
    return svc


@pytest.fixture
def real_mcp_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import app

    svc = _make_real_service(tmp_path, monkeypatch)
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            yield c, svc


class TestInitializeAssignsSessionId:
    def test_initialize_response_carries_mcp_session_id_header(self, client) -> None:
        resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test-client", "version": "1.0"}},
        })
        assert resp.status_code == 200
        assert "Mcp-Session-Id" in resp.headers
        assert len(resp.headers["Mcp-Session-Id"]) > 0

    def test_two_initialize_calls_get_distinct_session_ids(self, client) -> None:
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        first = client.post("/mcp", json=body).headers["Mcp-Session-Id"]
        second = client.post("/mcp", json=body).headers["Mcp-Session-Id"]
        assert first != second


class TestMcpSessionIdHeaderDrivesIsolation:
    def test_distinct_mcp_session_id_headers_hit_distinct_advisors(self, real_mcp_client) -> None:
        client, svc = real_mcp_client

        client.post("/mcp", json=tool_call("vectr_search", {"query": "handler"}),
                    headers={"Mcp-Session-Id": "session-A"})

        hint_a = client.post("/mcp", json=tool_call("vectr_evict_hint"),
                              headers={"Mcp-Session-Id": "session-A"})
        hint_b = client.post("/mcp", json=tool_call("vectr_evict_hint"),
                              headers={"Mcp-Session-Id": "session-B"})

        text_a = hint_a.json()["result"]["content"][0]["text"]
        text_b = hint_b.json()["result"]["content"][0]["text"]
        assert "No retrieved chunks to evict" not in text_a
        assert "No retrieved chunks to evict" in text_b
        assert "session-A" in svc._session_advisors
        assert "session-B" in svc._session_advisors
        assert svc._advisor_for("session-A") is not svc._advisor_for("session-B")

    def test_mcp_session_id_header_takes_priority_over_meta_session_id(self, real_mcp_client) -> None:
        client, svc = real_mcp_client
        body = tool_call("vectr_search", {"query": "handler"})
        body["params"]["_meta"] = {"sessionId": "meta-session"}
        client.post("/mcp", json=body, headers={"Mcp-Session-Id": "header-session"})

        assert "header-session" in svc._session_advisors
        assert "meta-session" not in svc._session_advisors


class TestLegacyFallbacksStillWork:
    def test_x_session_id_header_still_isolates_when_no_mcp_session_id_sent(self, real_mcp_client) -> None:
        client, svc = real_mcp_client

        client.post("/mcp", json=tool_call("vectr_search", {"query": "handler"}),
                    headers={"X-Session-ID": "legacy-session"})

        hint = client.post("/mcp", json=tool_call("vectr_evict_hint"),
                            headers={"X-Session-ID": "legacy-session"})
        text = hint.json()["result"]["content"][0]["text"]
        assert "No retrieved chunks to evict" not in text
        assert "legacy-session" in svc._session_advisors

    def test_meta_session_id_still_works_when_no_headers_sent(self, real_mcp_client) -> None:
        client, svc = real_mcp_client
        body = tool_call("vectr_search", {"query": "handler"})
        body["params"]["_meta"] = {"sessionId": "meta-only-session"}
        client.post("/mcp", json=body)

        assert "meta-only-session" in svc._session_advisors
