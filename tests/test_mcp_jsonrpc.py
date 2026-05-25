"""
Tests for the MCP JSON-RPC protocol — POST /mcp.

Two fixture layers:
  client          — mocked VectrService, tests protocol correctness
  client_real_memory — real WorkingContextStore, tests remember→recall round-trip
    (the exact sequence Claude Code runs during a two-phase session)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def jsonrpc(method: str, params: dict | None = None, id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def tool_call(name: str, arguments: dict | None = None, id: int = 1) -> dict:
    return jsonrpc("tools/call", {"name": name, "arguments": arguments or {}}, id=id)


# ---------------------------------------------------------------------------
# Protocol handshake
# ---------------------------------------------------------------------------

class TestMcpHandshake:
    def test_initialize_returns_protocol_version(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-code", "version": "1.0"},
        }))
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["result"]["protocolVersion"] == "2024-11-05"
        assert data["result"]["serverInfo"]["name"] == "vectr"

    def test_notifications_initialized_returns_empty(self, client) -> None:
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_ping_returns_ok(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("ping"))
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert "error" not in data

    def test_unknown_method_returns_method_not_found(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("nonexistent/method"))
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------

class TestMcpToolsList:
    def test_returns_all_tools(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("tools/list"))
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"vectr_search", "vectr_status", "vectr_map", "vectr_map_save",
                "vectr_locate", "vectr_trace", "vectr_remember", "vectr_recall",
                "vectr_evict_hint", "vectr_snapshot", "vectr_snapshot_list"} <= names

    def test_each_tool_has_input_schema(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("tools/list"))
        for tool in resp.json()["result"]["tools"]:
            assert "inputSchema" in tool, f"{tool['name']} missing inputSchema"
            assert "description" in tool, f"{tool['name']} missing description"


# ---------------------------------------------------------------------------
# tools/call — mocked service
# ---------------------------------------------------------------------------

class TestMcpToolsCall:
    def test_status_returns_text(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_status"))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "workspace" in text.lower() or "indexed" in text.lower()

    def test_map_save_called_with_summary(self, client) -> None:
        resp = client.post("/mcp", json=tool_call(
            "vectr_map_save", {"summary": "Python FastAPI service for rate limiting."}
        ))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_map_save_missing_summary_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_map_save", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_search_happy_path(self, client) -> None:
        resp = client.post("/mcp", json=tool_call(
            "vectr_search", {"query": "rate limit middleware", "n_results": 5}
        ))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        assert "verify_token" in result["content"][0]["text"]  # from mock

    def test_search_missing_query_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_search", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_locate_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_locate", {"name": "Signal"}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_locate_missing_name_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_locate", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_trace_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_trace", {"name": "send", "direction": "callers"}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_remember_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call(
            "vectr_remember", {"content": "Signal.send at dispatcher.py:220", "priority": "high"}
        ))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        assert "note" in result["content"][0]["text"].lower()

    def test_remember_missing_content_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_remember", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_recall_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_recall", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_evict_hint_returns_text(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_evict_hint"))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_snapshot_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_snapshot", {"label": "phase1-complete"}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_snapshot_missing_label_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_snapshot", {}))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_snapshot_list_dispatches_to_service(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_snapshot_list"))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_unknown_tool_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=tool_call("vectr_does_not_exist"))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is True

    def test_missing_tool_name_returns_error(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("tools/call", {"arguments": {}}))
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    def test_jsonrpc_id_echoed(self, client) -> None:
        resp = client.post("/mcp", json=jsonrpc("ping", id=42))
        assert resp.json()["id"] == 42


# ---------------------------------------------------------------------------
# remember → recall round-trip with real WorkingContextStore
# ---------------------------------------------------------------------------

class TestMcpMemoryRoundTrip:
    """
    Tests that notes stored via vectr_remember in one request are retrievable
    via vectr_recall in a subsequent request. Uses client_real_memory fixture
    which wires a real WorkingContextStore but keeps search mocked.

    This is exactly the two-phase POC scenario:
      Request 1 (Phase 1 simulation): vectr_remember
      Request 2 (Phase 2 simulation): vectr_recall → must return stored content
    """

    def test_remember_then_recall_returns_content(self, client_real_memory) -> None:
        client = client_real_memory
        # Phase 1: store a finding
        resp = client.post("/mcp", json=tool_call("vectr_remember", {
            "content": "Field.contribute_to_class at django/db/models/fields/__init__.py:770",
            "tags": ["field", "lifecycle"],
            "priority": "high",
        }))
        assert resp.json()["result"]["isError"] is False

        # Phase 2: recall in the "next session"
        resp = client.post("/mcp", json=tool_call("vectr_recall", {}))
        text = resp.json()["result"]["content"][0]["text"]
        assert "contribute_to_class" in text
        assert "django/db/models/fields" in text

    def test_multiple_remember_then_recall_all_returned(self, client_real_memory) -> None:
        client = client_real_memory
        findings = [
            "BaseHandler.load_middleware() at django/core/handlers/base.py builds middleware stack",
            "Middleware must set async_capable = True to run in ASGI mode",
            "HTTP 429 response class: HttpResponseTooManyRequests at django/http/response.py",
        ]
        for content in findings:
            client.post("/mcp", json=tool_call("vectr_remember", {"content": content}))

        resp = client.post("/mcp", json=tool_call("vectr_recall", {}))
        recalled = resp.json()["result"]["content"][0]["text"]
        for finding in findings:
            # Each stored finding should appear in the recall output
            keyword = finding.split()[0]  # first distinctive word
            assert keyword in recalled, f"Keyword '{keyword}' missing from recall"

    def test_recall_tag_filter_via_mcp(self, client_real_memory) -> None:
        client = client_real_memory
        client.post("/mcp", json=tool_call("vectr_remember", {
            "content": "signal dispatch internals", "tags": ["signal"]
        }))
        client.post("/mcp", json=tool_call("vectr_remember", {
            "content": "middleware loading code", "tags": ["middleware"]
        }))

        resp = client.post("/mcp", json=tool_call("vectr_recall", {"tags": ["signal"]}))
        recalled = resp.json()["result"]["content"][0]["text"]
        assert "signal dispatch" in recalled
        assert "middleware loading" not in recalled

    def test_snapshot_then_recall_via_mcp(self, client_real_memory) -> None:
        client = client_real_memory
        client.post("/mcp", json=tool_call("vectr_remember", {
            "content": "key finding for snapshot test"
        }))
        snap_resp = client.post("/mcp", json=tool_call("vectr_snapshot", {"label": "phase1-complete"}))
        assert snap_resp.json()["result"]["isError"] is False

        # Notes still accessible after snapshot
        resp = client.post("/mcp", json=tool_call("vectr_recall", {}))
        assert "key finding for snapshot test" in resp.json()["result"]["content"][0]["text"]

    def test_empty_recall_before_any_remember(self, client_real_memory) -> None:
        resp = client_real_memory.post("/mcp", json=tool_call("vectr_recall", {}))
        text = resp.json()["result"]["content"][0]["text"]
        assert "No working notes found" in text
