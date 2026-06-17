"""Integration tests for FastAPI routes. Uses a fully mocked VectrService — no model loading."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import app
from agent.searcher import SearchResult
from agent.query_router import RoutingDecision, QueryType
from agent.symbol_graph import LocateResult, Symbol


def _make_service():
    svc = MagicMock()
    svc._embed_model = "BAAI/bge-base-en-v1.5"
    svc.total_chunks = 500

    _result = SearchResult(
        file_path="src/auth/middleware.py",
        lines="42-67",
        symbol_name="verify_jwt_token",
        language="python",
        score=0.94,
        content="def verify_jwt_token(token: str) -> dict:\n    ...",
    )
    _decision = RoutingDecision(
        query_type=QueryType.SEMANTIC,
        semantic_weight=0.70,
        also_run_symbol_lookup=False,
        also_run_trace=False,
        include_map_hint=False,
        rationale="semantic query — standard adaptive hybrid weights",
    )

    svc.search.return_value = ([_result], 18)
    svc.search_routed.return_value = ([_result], 18, _decision, [], [])
    svc.index.return_value = (12, 500, 240)
    svc.status.return_value = {
        "indexed_files": 12,
        "total_chunks": 500,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "BAAI/bge-base-en-v1.5",
        "workspace_root": "/repo",
        "symbol_count": 0,
        # UPG-3.3: per-language coverage + symbol availability (real shape)
        "languages": [
            {"language": "python", "files": 10, "chunks": 400, "symbols": True},
            {"language": "markdown", "files": 2, "chunks": 100, "symbols": False},
        ],
        # UPG-8.7: symbol-graph build trust signals (real shape)
        "symbol_graph_complete": True,
        "symbol_graph_failed_files": 0,
    }
    svc.get_map.return_value = "# Codebase Passport\nFastAPI service."
    # locate_with_snippets returns a LocateResult wrapper (not a bare list) —
    # the REST route must unwrap .symbols. Mock the real shape so the route
    # contract is actually exercised.
    svc.locate_with_snippets.return_value = LocateResult(
        symbols=[
            Symbol(
                symbol_id=1,
                workspace="/repo",
                name="PyDict_New",
                kind="function",
                file_path="Objects/dictobject.c",
                start_line=812,
                end_line=824,
                snippet="PyObject *\nPyDict_New(void)\n{",
            )
        ],
        resolution_strategy="exact",
        query="PyDict_New",
    )
    svc.format_locate.return_value = "PyDict_New  Objects/dictobject.c:812"
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.should_evict.return_value = False
    svc.eviction_hint.return_value = ""
    return svc


@pytest.fixture
def client():
    """
    Fast fixture: patches VectrService so no model is loaded during lifespan.
    All service calls go to a MagicMock with realistic return values.
    """
    svc = _make_service()
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            # Ensure mock is in app state regardless of lifespan behaviour
            app.state.service = svc
            yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client) -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "embed_model" in data


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_happy_path(client) -> None:
    resp = client.post("/v1/search", json={"query": "JWT token validation"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["file"] == "src/auth/middleware.py"
    assert data["results"][0]["score"] == 0.94
    assert "processing_ms" in data
    assert "chunks_searched" in data


def test_search_missing_query(client) -> None:
    resp = client.post("/v1/search", json={"n_results": 5})
    assert resp.status_code == 422


def test_search_invalid_n_results_too_high(client) -> None:
    resp = client.post("/v1/search", json={"query": "auth", "n_results": 999})
    assert resp.status_code == 422


def test_search_unindexed_language_no_422(client) -> None:
    # UPG-3.1: a real-but-unindexed language must NOT 422 (c/zig were the pain).
    # It is accepted; unindexed languages simply yield no matches (+ MCP hint).
    for lang in ("c", "zig", "cobol"):
        resp = client.post("/v1/search", json={"query": "auth", "language": lang})
        assert resp.status_code == 200, f"{lang} should be accepted, got {resp.status_code}"


def test_search_language_normalized(client) -> None:
    # mixed case + whitespace normalised to lower/stripped
    resp = client.post("/v1/search", json={"query": "auth", "language": "  Rust "})
    assert resp.status_code == 200


def test_search_with_language_filter(client) -> None:
    resp = client.post("/v1/search", json={"query": "auth", "language": "python"})
    assert resp.status_code == 200


def test_search_request_accepts_any_language() -> None:
    # UPG-3.1: model no longer rejects/normalises — it just carries the value.
    # Normalisation is shared in CodeSearcher.search (see test_indexer_searcher).
    from app.models import SearchRequest
    assert SearchRequest(query="q", language="c").language == "c"
    assert SearchRequest(query="q", language="zig").language == "zig"
    assert SearchRequest(query="q", language=None).language is None


# ---------------------------------------------------------------------------
# Locate (symbol graph) — route must unwrap LocateResult.symbols
# ---------------------------------------------------------------------------

def test_locate_happy_path(client) -> None:
    resp = client.post("/v1/locate", json={"name": "PyDict_New"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    sym = data["results"][0]
    assert sym["name"] == "PyDict_New"
    assert sym["file_path"] == "Objects/dictobject.c"
    assert sym["start_line"] == 812
    assert "formatted" in data
    assert "processing_ms" in data


def test_locate_empty(client) -> None:
    """A LocateResult with no symbols returns 200 + empty results, not a 500."""
    app.state.service.locate_with_snippets.return_value = LocateResult(
        symbols=[], resolution_strategy="none", query="nope"
    )
    resp = client.post("/v1/locate", json={"name": "nope"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_trace_happy_path(client) -> None:
    resp = client.post("/v1/trace", json={"name": "get_gc_state"})
    assert resp.status_code == 200
    data = resp.json()
    assert "formatted" in data
    assert "processing_ms" in data


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def test_index_happy_path(client) -> None:
    resp = client.post("/v1/index", json={"path": ".", "force": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["indexed_files"] == 12
    assert "processing_ms" in data


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def test_status(client) -> None:
    resp = client.get("/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["indexed_files"] == 12
    assert data["total_chunks"] == 500
    assert "workspace_root" in data
    assert "notes_count" in data, "/v1/status must include notes_count for agent recall decisions"
    assert isinstance(data["notes_count"], int)
    # UPG-8.7: symbol-graph build trust signals surface in /v1/status
    assert data["symbol_graph_complete"] is True
    assert data["symbol_graph_failed_files"] == 0
    # UPG-3.3: per-language coverage with symbol availability
    langs = {l["language"]: l for l in data["languages"]}
    assert langs["python"]["symbols"] is True
    assert langs["python"]["files"] == 10
    assert langs["markdown"]["symbols"] is False


# ---------------------------------------------------------------------------
# MCP protocol
# ---------------------------------------------------------------------------

def test_mcp_info(client) -> None:
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "vectr"


def test_mcp_tools_list(client) -> None:
    resp = client.post("/mcp/tools/list", json={})
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["tools"]}
    assert "vectr_search" in names
    assert "vectr_status" in names
    assert "vectr_map_save" in names


def test_mcp_tools_call_search(client) -> None:
    resp = client.post(
        "/mcp/tools/call",
        json={"name": "vectr_search", "arguments": {"query": "JWT token validation"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is False
    assert "verify_jwt_token" in data["content"][0]["text"]


def test_mcp_tools_call_map_save(client) -> None:
    resp = client.post(
        "/mcp/tools/call",
        json={"name": "vectr_map_save", "arguments": {"summary": "Python FastAPI service for auth."}},
    )
    assert resp.status_code == 200
    assert resp.json()["isError"] is False


def test_mcp_tools_call_missing_name(client) -> None:
    resp = client.post("/mcp/tools/call", json={"arguments": {}})
    assert resp.status_code == 400


def test_mcp_tools_call_status(client) -> None:
    resp = client.post("/mcp/tools/call", json={"name": "vectr_status", "arguments": {}})
    assert resp.status_code == 200
    assert resp.json()["isError"] is False


def test_mcp_status_renders_language_capability(client) -> None:
    """UPG-3.3: vectr_status text must tell the agent where locate/trace work."""
    resp = client.post("/mcp/tools/call", json={"name": "vectr_status", "arguments": {}})
    text = resp.json()["content"][0]["text"]
    assert "Languages" in text
    assert "python" in text and "locate/trace" in text
    assert "markdown" in text and "search-only" in text


def test_mcp_status_warns_when_primary_language_has_no_symbols(client) -> None:
    """If the dominant language is search-only, the agent should be told to prefer
    search over locate/trace — the adoption-critical routing hint."""
    app.state.service.status.return_value = {
        "indexed_files": 240, "total_chunks": 900,
        "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "x",
        "workspace_root": "/repo", "symbol_count": 0,
        "languages": [
            {"language": "markdown", "files": 240, "chunks": 800, "symbols": False},
            {"language": "python", "files": 5, "chunks": 100, "symbols": True},
        ],
    }
    resp = client.post("/mcp/tools/call", json={"name": "vectr_status", "arguments": {}})
    text = resp.json()["content"][0]["text"]
    assert "Primary language (markdown)" in text
    assert "prefer vectr_search" in text


# ---------------------------------------------------------------------------
# T15: VECTR_API_KEY enforcement
# ---------------------------------------------------------------------------

class TestApiKeyEnforcement:
    """T15: VECTR_API_KEY header enforcement.

    The middleware checks os.environ at request time (not at import time),
    so patch.dict works cleanly without module reloading.
    """

    def test_no_key_set_allows_all_requests(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": ""}):
            resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_health_endpoint_bypasses_key_check(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/health")
        assert resp.status_code == 200  # health always allowed

    def test_missing_key_returns_401(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/status")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/status", headers={"X-Api-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_correct_x_api_key_header_passes(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/status", headers={"X-Api-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_bearer_token_passes(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/status", headers={"Authorization": "Bearer test-secret-key"})
        assert resp.status_code == 200

    def test_401_response_includes_error_field(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": "test-secret-key"}):
            resp = client.get("/v1/status")
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert body["error"] == "unauthorized"

    def test_empty_key_env_var_allows_requests(self, client) -> None:
        with patch.dict("os.environ", {"VECTR_API_KEY": ""}):
            resp = client.get("/v1/status")
        assert resp.status_code == 200
