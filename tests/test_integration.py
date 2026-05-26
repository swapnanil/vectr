"""
End-to-end integration tests.

Two tiers:

  Fast (no marker) — real VectrService + dummy embedder.  These run in CI with
    every push and exercise the full HTTP → routes → indexer → ChromaDB →
    searcher pipeline without any model download.

  @pytest.mark.integration — real Snowflake/snowflake-arctic-embed-m-v1.5 model (~440 MB,
    downloaded once then cached).  Verifies that semantic ranking actually works
    with production embeddings.  Run with: pytest -m integration
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.conftest import make_py


def _jsonrpc(method: str, params: dict | None = None, id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _tool_call(name: str, arguments: dict | None = None, id: int = 1) -> dict:
    return _jsonrpc("tools/call", {"name": name, "arguments": arguments or {}}, id=id)


# ---------------------------------------------------------------------------
# Fast tier — real VectrService, dummy embedder
# ---------------------------------------------------------------------------

class TestFullPipelineFast:
    """
    Exercises the complete production stack (routes → VectrService → indexer →
    ChromaDB → BM25) with the deterministic dummy embedder.

    These replace the MagicMock-based tests for scenarios where we care about
    real routing, real indexing, and real search — not just HTTP shape.
    """

    def test_index_then_search_returns_result(self, real_service_client, tmp_path) -> None:
        client, svc, ws = real_service_client
        # Write a file into the workspace so the indexer has something to find
        py = Path(ws) / "auth.py"
        py.write_text(textwrap.dedent("""
            def verify_token(token: str) -> dict:
                \"\"\"Verify a JWT and return claims.\"\"\"
                return {}

            def refresh_token(token: str) -> str:
                return token
        """))
        index_resp = client.post("/v1/index", json={"path": ws, "force": True})
        assert index_resp.status_code == 200
        assert index_resp.json()["indexed_files"] >= 1

        search_resp = client.post("/v1/search", json={"query": "verify token"})
        assert search_resp.status_code == 200
        results = search_resp.json()["results"]
        assert len(results) >= 1
        # Real pipeline — result must come from the real indexer, not a hardcoded mock
        assert any("auth.py" in r["file"] for r in results)

    def test_mcp_search_real_pipeline(self, real_service_client, tmp_path) -> None:
        client, svc, ws = real_service_client
        py = Path(ws) / "rate_limit.py"
        py.write_text(textwrap.dedent("""
            class RateLimiter:
                def check(self, ip: str) -> bool:
                    return True
        """))
        client.post("/v1/index", json={"path": ws, "force": True})

        resp = client.post("/mcp", json=_tool_call("vectr_search", {
            "query": "rate limiter check",
        }))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        # Result text must come from the real indexer (not a mock return value)
        assert "rate_limit.py" in result["content"][0]["text"] or "RateLimiter" in result["content"][0]["text"]

    def test_mcp_status_reflects_real_index(self, real_service_client, tmp_path) -> None:
        client, svc, ws = real_service_client
        py = Path(ws) / "models.py"
        py.write_text("class User:\n    pass\n")
        client.post("/v1/index", json={"path": ws, "force": True})

        resp = client.post("/mcp", json=_tool_call("vectr_status"))
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        # Status must reflect the real indexed state, not a mock
        assert any(kw in text.lower() for kw in ("indexed", "chunks", "workspace"))

    def test_mcp_remember_recall_through_real_service(self, real_service_client) -> None:
        client, svc, ws = real_service_client

        store_resp = client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": "RateLimiter.check at rate_limit.py:5",
            "priority": "high",
        }))
        assert store_resp.json()["result"]["isError"] is False

        recall_resp = client.post("/mcp", json=_tool_call("vectr_recall", {}))
        text = recall_resp.json()["result"]["content"][0]["text"]
        assert "RateLimiter" in text

    def test_rest_remember_recall_through_real_service(self, real_service_client) -> None:
        client, svc, ws = real_service_client

        client.post("/v1/remember", json={"content": "Signal.send at dispatcher.py:220", "priority": "high"})
        resp = client.post("/v1/recall", json={})
        assert resp.status_code == 200
        assert "Signal.send" in resp.json()["notes"]

    def test_evict_hint_after_search(self, real_service_client, tmp_path) -> None:
        client, svc, ws = real_service_client
        py = Path(ws) / "signals.py"
        py.write_text("def send(sender, **kwargs): pass\n")
        client.post("/v1/index", json={"path": ws, "force": True})
        client.post("/v1/search", json={"query": "send signal"})

        resp = client.get("/v1/evict-hint")
        assert resp.status_code == 200
        data = resp.json()
        assert "hint" in data
        assert "should_evict" in data

    def test_snapshot_through_real_service(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/remember", json={"content": "integration snapshot test"})
        snap_resp = client.post("/v1/snapshot", json={"label": "integration-test"})
        assert snap_resp.status_code == 200
        snap_id = snap_resp.json()["snapshot_id"]
        assert snap_id is not None and len(snap_id) > 0

    def test_bm25_ranking_works_in_real_pipeline(self, real_service_client, tmp_path) -> None:
        """BM25 + code tokenizer finds identifiers in real indexed code."""
        client, svc, ws = real_service_client
        (Path(ws) / "dispatcher.py").write_text(textwrap.dedent("""
            def dispatch_signal(sender, dispatch_uid=None, **kwargs):
                pass

            def unrelated_helper():
                pass
        """))
        client.post("/v1/index", json={"path": ws, "force": True})
        resp = client.post("/v1/search", json={"query": "dispatch_uid", "n_results": 5})
        results = resp.json()["results"]
        assert len(results) >= 1
        assert any("dispatcher.py" in r["file"] for r in results)

    def test_unknown_tool_returns_error_through_real_service(self, real_service_client) -> None:
        client, _, _ = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_does_not_exist"))
        assert resp.json()["result"]["isError"] is True

    def test_search_missing_query_returns_422(self, real_service_client) -> None:
        client, _, _ = real_service_client
        resp = client.post("/v1/search", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration tier — real nomic-embed-code model
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSemanticRanking:
    """
    Verifies that the production Snowflake/snowflake-arctic-embed-m-v1.5 embedding model
    produces meaningful semantic rankings — i.e. "authenticate user with JWT"
    ranks auth code above rate-limiting code.

    These are the tests that would catch a silent degradation in embedding
    quality (wrong model, wrong preprocessing, dimension mismatch).
    """

    def _make_searcher(self, integration_indexer, tmp_path):
        from tests.conftest import make_py
        from agent.searcher import CodeSearcher

        make_py(tmp_path, "auth.py", """
            def authenticate_user(username: str, password: str) -> dict:
                \"\"\"Authenticate a user and return a JWT token payload.\"\"\"
                # verify credentials against the user database
                claims = {"sub": username, "role": "user"}
                return claims

            def refresh_jwt_token(token: str) -> str:
                \"\"\"Refresh an expiring JWT token.\"\"\"
                return token
        """)
        make_py(tmp_path, "rate_limit.py", """
            def check_rate_limit(ip: str, limit: int = 100) -> bool:
                \"\"\"Check if an IP has exceeded the rate limit.\"\"\"
                return True

            def reset_rate_limit(ip: str) -> None:
                \"\"\"Reset the rate limit counter for an IP.\"\"\"
                pass
        """)
        make_py(tmp_path, "db.py", """
            def get_connection(dsn: str):
                \"\"\"Open a database connection.\"\"\"
                pass

            def execute_query(conn, sql: str, params: tuple = ()) -> list:
                \"\"\"Execute a parameterised SQL query.\"\"\"
                return []
        """)
        integration_indexer.workspace_root = tmp_path
        integration_indexer.index_workspace()
        s = CodeSearcher(integration_indexer)
        s.refresh_bm25()
        return s

    def test_auth_query_ranks_auth_code_first(self, integration_indexer, tmp_path) -> None:
        s = self._make_searcher(integration_indexer, tmp_path)
        results, _ = s.search("authenticate user JWT token", n_results=3, rerank=False)
        assert len(results) >= 1
        assert results[0].file_path.endswith("auth.py"), (
            f"Expected auth.py first, got {results[0].file_path} "
            f"(score={results[0].score})"
        )

    def test_rate_limit_query_ranks_rate_limit_code_first(self, integration_indexer, tmp_path) -> None:
        s = self._make_searcher(integration_indexer, tmp_path)
        results, _ = s.search("check if IP exceeded rate limit", n_results=3, rerank=False)
        assert results[0].file_path.endswith("rate_limit.py"), (
            f"Expected rate_limit.py first, got {results[0].file_path}"
        )

    def test_db_query_ranks_db_code_first(self, integration_indexer, tmp_path) -> None:
        s = self._make_searcher(integration_indexer, tmp_path)
        results, _ = s.search("open database connection execute SQL", n_results=3, rerank=False)
        assert results[0].file_path.endswith("db.py"), (
            f"Expected db.py first, got {results[0].file_path}"
        )

    def test_embedding_dimension_matches_collection(self, integration_indexer, tmp_path) -> None:
        from tests.conftest import make_py
        path = make_py(tmp_path, "check_dim.py", "def foo(): pass")
        integration_indexer.index_file(path)
        vec = integration_indexer.embed_query("test query")
        # nomic-embed-code outputs 768-dim embeddings
        assert len(vec) == 768
        assert all(isinstance(v, float) for v in vec)

    def test_query_embedding_differs_for_different_queries(self, integration_indexer, tmp_path) -> None:
        v1 = integration_indexer.embed_query("authenticate user")
        v2 = integration_indexer.embed_query("database connection")
        # Different semantics → different vectors (cosine distance > 0)
        import numpy as np
        cosine = float(np.dot(v1, v2))
        assert cosine < 0.99, "Embeddings too similar for unrelated queries"
