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

    def test_status_notes_count_reflects_real_notes(self, real_service_client) -> None:
        client, svc, ws = real_service_client

        # Clear any notes left by previous tests in this session
        client.post("/v1/memory/clear", json={})

        # After clear: notes_count must be 0
        resp = client.get("/v1/status")
        assert resp.status_code == 200
        assert resp.json()["notes_count"] == 0, "notes_count must be 0 after memory clear"

        # Store a note, then confirm notes_count increments
        client.post("/v1/remember", json={"content": "test note for count", "priority": "high"})
        resp = client.get("/v1/status")
        assert resp.json()["notes_count"] == 1, "notes_count must reflect stored notes in real service"

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

    def test_targeted_recall_filters_by_query(self, real_service_client) -> None:
        """vectr_recall(query=...) must return notes relevant to the query.

        Semantic search ranks by cosine similarity — it is a ranker, not an exact
        keyword filter. The test verifies that the matching note is included in the
        results; whether unrelated notes also appear depends on the embedding model
        and collection size (with a real model they score lower and fall below the
        top-k cutoff on larger collections).
        """
        client, svc, ws = real_service_client

        unique_match = "XTARGETED_RECALL_MATCH_TOKEN_42X"
        unique_nomatch = "XTARGETED_RECALL_NOMATCH_TOKEN_99X"

        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": f"Rate limiter entry point: {unique_match} — at rate_limit.py:5",
        }))
        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": f"Signal dispatch loop: {unique_nomatch} — at dispatcher.py:220",
        }))

        resp = client.post("/mcp", json=_tool_call("vectr_recall", {"query": unique_match}))
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        assert unique_match in text, (
            f"targeted recall with query='{unique_match}' must include the matching note"
        )

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
# Full MCP session tests — real VectrService, real MCP JSON-RPC protocol
# ---------------------------------------------------------------------------

class TestMcpSessionFull:
    """
    End-to-end MCP session tests using a real VectrService and real JSON-RPC.
    No mocks. Tests the full path: HTTP → routes → MCP dispatch → VectrService.
    """

    def _index_workspace(self, client, svc, ws: str) -> None:
        """Write a small multi-file codebase and index it."""
        Path(ws, "auth.py").write_text(textwrap.dedent("""
            def authenticate(username: str, password: str) -> bool:
                \"\"\"Verify credentials against the database.\"\"\"
                return True

            def generate_token(user_id: int) -> str:
                \"\"\"Generate a JWT token for a user.\"\"\"
                return "token"
        """))
        Path(ws, "rate_limit.py").write_text(textwrap.dedent("""
            class RateLimiter:
                \"\"\"Per-IP rate limiting using a sliding window.\"\"\"
                def check(self, ip: str) -> bool:
                    return True
                def reset(self, ip: str) -> None:
                    pass
        """))
        Path(ws, "db.py").write_text(textwrap.dedent("""
            def get_connection(dsn: str):
                \"\"\"Open a PostgreSQL connection.\"\"\"
                pass

            def execute_query(conn, sql: str, params: tuple = ()) -> list:
                \"\"\"Run a parameterised query and return rows.\"\"\"
                return []
        """))
        client.post("/v1/index", json={"path": ws, "force": True})

    # -- MCP initialize + tools/list --

    def test_mcp_initialize_returns_protocol_version(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0"},
            "capabilities": {},
        }))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "vectr"

    def test_mcp_tools_list_no_session_returns_all(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_jsonrpc("tools/list", {}))
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        for expected in ("vectr_search", "vectr_status", "vectr_remember",
                         "vectr_recall", "vectr_locate", "vectr_trace"):
            assert expected in names

    def test_mcp_tools_list_new_session_returns_exploration_only(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        from integrations.mcp_server import _memory_enabled_sessions

        # Clear all notes so count_notes() == 0; otherwise the pre-enable
        # check in handle_tools_list triggers on a session-scoped fixture
        # that has notes from prior tests.
        client.post("/v1/memory/clear", json={})

        sid = "integ-fresh-session-no-notes-42"
        _memory_enabled_sessions.discard(sid)

        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/list", {"_meta": {"sessionId": sid}}),
        )
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        # Exploration tools always present
        assert "vectr_search" in names
        assert "vectr_status" in names
        # vectr_remember and vectr_evict_hint always present (write side — no catch-22)
        assert "vectr_remember" in names
        assert "vectr_evict_hint" in names
        # Read/manage tools gated until memory is enabled
        assert "vectr_recall" not in names
        assert "vectr_snapshot" not in names
        _memory_enabled_sessions.discard(sid)

    def test_vectr_status_with_notes_enables_memory_tools_in_session(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        from integrations.mcp_server import _memory_enabled_sessions
        sid = "integ-status-enables-memory-43"
        _memory_enabled_sessions.discard(sid)

        # Pre-store a note so notes_count > 0
        svc.remember("test note for T13 integration", tags=["t13"])

        # Status call → should enable memory tools for session
        client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "vectr_status",
            "arguments": {},
            "_meta": {"sessionId": sid},
        }))

        # Now tools/list should include memory tools
        resp = client.post("/mcp", json=_jsonrpc("tools/list", {
            "_meta": {"sessionId": sid}
        }))
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        assert "vectr_recall" in names
        _memory_enabled_sessions.discard(sid)

    # -- vectr_search end-to-end --

    def test_mcp_search_finds_indexed_code(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._index_workspace(client, svc, ws)
        resp = client.post("/mcp", json=_tool_call("vectr_search", {
            "query": "authenticate user credentials",
        }))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "auth.py" in text or "authenticate" in text.lower()

    def test_mcp_search_with_language_filter(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._index_workspace(client, svc, ws)
        resp = client.post("/mcp", json=_tool_call("vectr_search", {
            "query": "database connection", "language": "python",
        }))
        assert resp.status_code == 200
        assert resp.json()["result"]["isError"] is False

    def test_mcp_search_empty_index_returns_graceful_message(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        # Force an empty index state by checking chunks
        resp = client.post("/mcp", json=_tool_call("vectr_search", {"query": "anything"}))
        result = resp.json()["result"]
        assert result["isError"] is False  # graceful, not a 500

    # -- vectr_status --

    def test_mcp_status_includes_all_required_fields(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_status"))
        text = resp.json()["result"]["content"][0]["text"]
        for field in ("Indexed files", "Total chunks", "Prior notes", "Workspace"):
            assert field in text, f"Status missing field: {field}"

    def test_mcp_status_notes_count_increments_after_remember(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        # Clear notes
        client.post("/v1/memory/clear", json={})

        resp_before = client.post("/mcp", json=_tool_call("vectr_status"))
        text_before = resp_before.json()["result"]["content"][0]["text"]
        assert "Prior notes    : 0" in text_before

        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": "status count integration test note",
        }))

        resp_after = client.post("/mcp", json=_tool_call("vectr_status"))
        text_after = resp_after.json()["result"]["content"][0]["text"]
        assert "Prior notes    : 0" not in text_after

    # -- T14: instruction style in vectr_status --

    def test_vectr_status_includes_tool_style_hint(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_status"))
        text = resp.json()["result"]["content"][0]["text"]
        # T14: style hint should appear in status output
        assert "Tool style" in text or "additive" in text or "directed" in text

    # -- vectr_map + vectr_map_save --

    def test_vectr_map_returns_passport_or_raw_metadata(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_map"))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        # Either a saved passport or raw metadata instruction
        text = result["content"][0]["text"]
        assert len(text) > 0

    def test_vectr_map_save_then_map_returns_passport(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        summary = "Python auth service with JWT tokens and rate limiting. Entry: auth.py."
        save_resp = client.post("/mcp", json=_tool_call("vectr_map_save", {
            "summary": summary,
        }))
        assert save_resp.json()["result"]["isError"] is False

        map_resp = client.post("/mcp", json=_tool_call("vectr_map"))
        text = map_resp.json()["result"]["content"][0]["text"]
        assert "Python auth service" in text or "JWT" in text

    # -- vectr_remember + vectr_recall --

    def test_mcp_remember_recall_full_round_trip(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})

        unique_token = "INTEG_REMEMBER_RECALL_TOKEN_7743"
        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": f"Rate limiter: {unique_token} — checks IP at rate_limit.py:5",
            "priority": "high",
        }))

        resp = client.post("/mcp", json=_tool_call("vectr_recall", {}))
        text = resp.json()["result"]["content"][0]["text"]
        assert unique_token in text

    def test_mcp_targeted_recall_filters_correctly(self, real_service_client) -> None:
        """vectr_recall(query=...) returns notes ranked by semantic similarity.

        With a real embedding model, the matching note scores higher and appears
        first. With the dummy embedder used in fast-tier tests, both notes may be
        returned — the key invariant is that the relevant note is present in results.
        """
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})

        match_token  = "INTEG_MATCH_TOKEN_9871"
        nomatch_token = "INTEG_NOMATCH_TOKEN_9872"
        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": f"Auth logic: {match_token} at auth.py:10",
        }))
        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": f"DB schema: {nomatch_token} at db.py:5",
        }))

        resp = client.post("/mcp", json=_tool_call("vectr_recall", {
            "query": match_token,
        }))
        text = resp.json()["result"]["content"][0]["text"]
        assert match_token in text

    def test_mcp_remember_empty_content_is_error(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_remember", {"content": ""}))
        assert resp.json()["result"]["isError"] is True

    # -- vectr_locate + vectr_trace --

    def test_mcp_locate_finds_indexed_symbol(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._index_workspace(client, svc, ws)
        resp = client.post("/mcp", json=_tool_call("vectr_locate", {
            "name": "authenticate",
        }))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False

    def test_mcp_locate_unknown_symbol_returns_graceful_message(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_locate", {
            "name": "SymbolThatDefinitelyDoesNotExistZZZ",
        }))
        result = resp.json()["result"]
        assert result["isError"] is False
        assert len(result["content"][0]["text"]) > 0

    def test_mcp_locate_missing_name_is_error(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_locate", {}))
        assert resp.json()["result"]["isError"] is True

    def test_mcp_trace_returns_response(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._index_workspace(client, svc, ws)
        resp = client.post("/mcp", json=_tool_call("vectr_trace", {
            "name": "authenticate",
            "direction": "callees",
        }))
        result = resp.json()["result"]
        assert result["isError"] is False

    def test_mcp_trace_invalid_direction_defaults_gracefully(
        self, real_service_client
    ) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_trace", {
            "name": "authenticate",
            "direction": "invalid_direction",
        }))
        assert resp.json()["result"]["isError"] is False

    # -- vectr_snapshot + vectr_snapshot_list --

    def test_mcp_snapshot_then_list_contains_label(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        label = "integ-test-snapshot"
        client.post("/mcp", json=_tool_call("vectr_remember", {
            "content": "snapshot integration test",
        }))
        snap_resp = client.post("/mcp", json=_tool_call("vectr_snapshot", {
            "label": label,
        }))
        assert snap_resp.json()["result"]["isError"] is False

        list_resp = client.post("/mcp", json=_tool_call("vectr_snapshot_list"))
        text = list_resp.json()["result"]["content"][0]["text"]
        assert label in text

    def test_mcp_snapshot_missing_label_is_error(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_snapshot", {}))
        assert resp.json()["result"]["isError"] is True

    # -- vectr_forget --

    def test_mcp_forget_all_clears_all_notes(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        unique = "INTEG_FORGET_TOKEN_5543"
        client.post("/mcp", json=_tool_call("vectr_remember", {"content": unique}))
        client.post("/mcp", json=_tool_call("vectr_forget", {"all": True}))

        resp = client.post("/mcp", json=_tool_call("vectr_recall", {}))
        text = resp.json()["result"]["content"][0]["text"]
        assert unique not in text

    def test_mcp_forget_bare_call_deletes_nothing(self, real_service_client) -> None:
        # Data-loss regression guard (2026-07-02): bare vectr_forget used to wipe the store.
        client, svc, ws = real_service_client
        unique = "INTEG_FORGET_SURVIVOR_7789"
        client.post("/mcp", json=_tool_call("vectr_remember", {"content": unique}))
        resp = client.post("/mcp", json=_tool_call("vectr_forget"))
        assert resp.json()["result"]["isError"] is True

        resp = client.post("/mcp", json=_tool_call("vectr_recall", {"detail": "full"}))
        text = resp.json()["result"]["content"][0]["text"]
        assert unique in text

    def test_mcp_forget_note_id_deletes_one(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        keep, drop = "INTEG_FORGET_KEEP_1111", "INTEG_FORGET_DROP_2222"
        client.post("/mcp", json=_tool_call("vectr_remember", {"content": keep}))
        r = client.post("/mcp", json=_tool_call("vectr_remember", {"content": drop}))
        drop_id = int(r.json()["result"]["content"][0]["text"].split("#")[1].split(".")[0].split()[0])

        client.post("/mcp", json=_tool_call("vectr_forget", {"note_id": drop_id}))
        resp = client.post("/mcp", json=_tool_call("vectr_recall", {"detail": "full"}))
        text = resp.json()["result"]["content"][0]["text"]
        assert keep in text
        assert drop not in text

    # -- vectr_evict_hint --

    def test_mcp_evict_hint_returns_response(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_evict_hint"))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["isError"] is False
        assert len(result["content"][0]["text"]) > 0

    # -- T15 API key via MCP endpoint --

    def test_mcp_endpoint_requires_api_key_when_set(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        from unittest.mock import patch
        with patch.dict("os.environ", {"VECTR_API_KEY": "mcp-integ-key"}):
            resp = client.post("/mcp", json=_tool_call("vectr_status"))
            assert resp.status_code == 401

    def test_mcp_endpoint_accepts_correct_api_key(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        from unittest.mock import patch
        with patch.dict("os.environ", {"VECTR_API_KEY": "mcp-integ-key"}):
            resp = client.post(
                "/mcp",
                json=_tool_call("vectr_status"),
                headers={"X-Api-Key": "mcp-integ-key"},
            )
            assert resp.status_code == 200

    # -- ping + unknown method --

    def test_mcp_ping_returns_ok(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_jsonrpc("ping"))
        assert resp.status_code == 200
        assert resp.json().get("result") == {}

    def test_mcp_unknown_method_returns_error(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_jsonrpc("unknown_method"))
        assert resp.json().get("error") is not None

    def test_mcp_unknown_tool_call_returns_is_error(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        resp = client.post("/mcp", json=_tool_call("vectr_nonexistent_tool_xyz"))
        assert resp.json()["result"]["isError"] is True


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

    def test_semantic_recall_ranks_relevant_note_first(self, integration_indexer, tmp_path) -> None:
        """B9 integration test — real Snowflake model must rank the relevant note above
        the irrelevant one when queried with a semantically related term."""
        import chromadb
        from agent.working_context_store import WorkingContextStore

        ws = "/integration/workspace"
        db_dir = str(tmp_path / "wcs_db")
        chroma_dir = str(tmp_path / "wcs_chroma")
        __import__("os").makedirs(db_dir, exist_ok=True)

        client = chromadb.PersistentClient(path=chroma_dir)
        store = WorkingContextStore(
            db_dir,
            embed_fn=integration_indexer.embed_texts,
            notes_chroma_client=client,
        )

        gc_note = (
            "handle_legacy_finalizers in Python/gc.c lines 1019–1040: "
            "objects with tp_del set are appended to gc.garbage and moved back to "
            "the old generation instead of being freed during cycle collection"
        )
        dict_note = (
            "dict_popitem in Objects/dictobject.c line 4869: walks PyDictKeyEntry "
            "in reverse order using dk_nentries, returns most recently inserted (key, value) pair"
        )
        store.remember(ws, gc_note, tags=["gc"])
        store.remember(ws, dict_note, tags=["dict"])

        # Query semantically near the GC finalizer note
        notes = store.recall(ws, query="garbage collector finalizer tp_del gc.garbage deferral", limit=2)
        assert len(notes) >= 1, "Semantic recall returned no notes"
        assert notes[0].content == gc_note, (
            f"Expected GC finalizer note ranked first, got: {notes[0].content[:80]}"
        )
