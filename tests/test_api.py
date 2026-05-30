"""Integration tests for FastAPI routes. Uses a fully mocked VectrService — no model loading."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import app
from agent.searcher import SearchResult
from agent.query_router import RoutingDecision, QueryType


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
    }
    svc.get_map.return_value = "# Codebase Passport\nFastAPI service."
    svc.locate_with_snippets.return_value = []
    svc.format_locate.return_value = "No results."
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


def test_search_invalid_language(client) -> None:
    resp = client.post("/v1/search", json={"query": "auth", "language": "cobol"})
    assert resp.status_code == 422


def test_search_with_language_filter(client) -> None:
    resp = client.post("/v1/search", json={"query": "auth", "language": "python"})
    assert resp.status_code == 200


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
