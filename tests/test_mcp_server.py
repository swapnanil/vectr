"""
Tests for integrations/mcp_server.py — handle_tools_call() dispatch.

All tool dispatches are exercised against a MagicMock service.
Validates:
  - Correct service methods called with correct arguments
  - Correct isError flag and content structure in every branch
  - Argument validation (missing required params → isError)
  - Edge-case paths (empty index, unknown tool, priority clamp, direction clamp)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from integrations.mcp_server import (
    MCP_TOOLS,
    handle_tools_call,
    handle_tools_list,
    _mcp_error,
    _format_search_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_service():
    from agent.searcher import SearchResult
    from agent.query_router import RoutingDecision, QueryType

    svc = MagicMock()
    svc.total_chunks = 100

    result = SearchResult(
        file_path="auth.py", lines="1-10", symbol_name="verify_token",
        language="python", score=0.9, content="def verify_token(): ...",
    )
    decision = RoutingDecision(
        query_type=QueryType.SEMANTIC,
        semantic_weight=0.7,
        also_run_symbol_lookup=False,
        also_run_trace=False,
        include_map_hint=False,
        rationale="semantic",
    )
    svc.search_routed.return_value = ([result], 10, decision, [], [])
    svc.status.return_value = {
        "indexed_files": 3,
        "total_chunks": 100,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "Snowflake/snowflake-arctic-embed-m-v1.5",
        "workspace_root": "/repo",
        "symbol_count": 25,
    }
    svc.get_map.return_value = "# Passport\nA FastAPI service."
    svc.save_map.return_value = None
    svc.locate_with_snippets.return_value = []
    svc.format_locate.return_value = "No symbols found."
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.remember.return_value = 42
    svc.recall.return_value = "# Notes\n[1] [HIGH] some note\n"
    svc.eviction_hint.return_value = ""
    svc.should_evict.return_value = False
    svc.snapshot_session.return_value = "snap_xyz"
    svc.list_snapshots.return_value = []
    return svc


# ---------------------------------------------------------------------------
# Tool description quality — LLM decision-rule language
# ---------------------------------------------------------------------------

class TestToolDescriptions:
    """Verify that tool descriptions contain decision-rule language, not just prose."""

    def _desc(self, name: str) -> str:
        for tool in MCP_TOOLS:
            if tool["name"] == name:
                return tool["description"]
        raise KeyError(name)

    def test_all_tools_have_non_empty_description(self) -> None:
        for tool in MCP_TOOLS:
            assert tool.get("description", "").strip(), f"{tool['name']} has empty description"

    def test_search_says_not_when_name_known(self) -> None:
        desc = self._desc("vectr_search")
        assert "vectr_locate" in desc, "vectr_search must tell model to use vectr_locate when name is known"

    def test_search_says_not_when_trace_needed(self) -> None:
        desc = self._desc("vectr_search")
        assert "vectr_trace" in desc, "vectr_search must tell model to use vectr_trace for call relationships"

    def test_locate_says_not_when_concept_search(self) -> None:
        desc = self._desc("vectr_locate")
        assert "vectr_search" in desc, "vectr_locate must direct to vectr_search for concept queries"

    def test_locate_says_not_when_trace_needed(self) -> None:
        desc = self._desc("vectr_locate")
        assert "vectr_trace" in desc, "vectr_locate must direct to vectr_trace for call relationships"

    def test_trace_says_not_when_name_unknown(self) -> None:
        desc = self._desc("vectr_trace")
        assert "vectr_search" in desc or "vectr_locate" in desc, (
            "vectr_trace must direct to vectr_search/vectr_locate when name is not yet known"
        )

    def test_trace_says_not_when_just_want_definition(self) -> None:
        desc = self._desc("vectr_trace")
        assert "vectr_locate" in desc, "vectr_trace must direct to vectr_locate for definition-only queries"

    def test_recall_says_call_at_session_start(self) -> None:
        desc = self._desc("vectr_recall").lower()
        assert "start" in desc, "vectr_recall must instruct the model to call it at session start"

    def test_snapshot_says_call_before_ending_session(self) -> None:
        desc = self._desc("vectr_snapshot").lower()
        assert "end" in desc or "ending" in desc or "before" in desc, (
            "vectr_snapshot must tell the model to call it before ending a session"
        )

    def test_evict_hint_mentions_bidirectional_protocol(self) -> None:
        desc = self._desc("vectr_evict_hint").lower()
        assert "vectr_remember" in self._desc("vectr_evict_hint"), (
            "vectr_evict_hint description should reference vectr_remember to explain the bidirectional protocol"
        )

    def test_map_save_says_only_when_raw_metadata(self) -> None:
        desc = self._desc("vectr_map_save").lower()
        assert "only" in desc or "not when" in desc or "not " in desc, (
            "vectr_map_save must clarify it is only needed after raw metadata, not every session"
        )

    def test_status_says_not_for_normal_exploration(self) -> None:
        desc = self._desc("vectr_status").lower()
        assert "debug" in desc or "not needed" in desc or "not " in desc, (
            "vectr_status must clarify it is for debugging, not normal exploration"
        )


# ---------------------------------------------------------------------------
# handle_tools_list
# ---------------------------------------------------------------------------

class TestHandleToolsList:
    def test_returns_all_tools(self) -> None:
        result = handle_tools_list()
        assert "tools" in result
        assert isinstance(result["tools"], list)
        assert len(result["tools"]) == len(MCP_TOOLS)

    def test_each_tool_has_name_and_schema(self) -> None:
        for tool in handle_tools_list()["tools"]:
            assert "name" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_required_tools_present(self) -> None:
        names = {t["name"] for t in handle_tools_list()["tools"]}
        for expected in ("vectr_search", "vectr_status", "vectr_remember", "vectr_recall",
                         "vectr_map", "vectr_locate", "vectr_trace", "vectr_snapshot"):
            assert expected in names


# ---------------------------------------------------------------------------
# vectr_search
# ---------------------------------------------------------------------------

class TestVectrSearch:
    def test_basic_search_returns_result(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": "verify token"}, svc)
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "auth.py" in text or "verify_token" in text

    def test_missing_query_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {}, svc)
        assert result["isError"] is True
        assert "query is required" in result["content"][0]["text"]

    def test_empty_query_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": ""}, svc)
        assert result["isError"] is True

    def test_empty_index_returns_indexing_message(self) -> None:
        svc = _mock_service()
        svc.total_chunks = 0
        result = handle_tools_call("vectr_search", {"query": "anything"}, svc)
        assert result["isError"] is False
        assert "indexing" in result["content"][0]["text"].lower()

    def test_n_results_capped_at_50(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "foo", "n_results": 999}, svc)
        call_kwargs = svc.search_routed.call_args[1]
        assert call_kwargs["n_results"] <= 50

    def test_language_filter_passed_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "fn", "language": "python"}, svc)
        call_kwargs = svc.search_routed.call_args[1]
        assert call_kwargs.get("language") == "python"

    def test_eviction_hint_appended_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = True
        svc.eviction_hint.return_value = "Drop these chunks: auth.py"
        result = handle_tools_call("vectr_search", {"query": "auth"}, svc)
        assert "Drop these chunks" in result["content"][0]["text"]

    def test_map_hint_appended_when_include_map_hint(self) -> None:
        from agent.query_router import RoutingDecision, QueryType
        svc = _mock_service()
        from agent.searcher import SearchResult
        decision = RoutingDecision(
            query_type=QueryType.STRUCTURAL,
            semantic_weight=0.3,
            also_run_symbol_lookup=False,
            also_run_trace=False,
            include_map_hint=True,
            rationale="structural",
        )
        svc.search_routed.return_value = ([], 5, decision, [], [])
        result = handle_tools_call("vectr_search", {"query": "overview"}, svc)
        assert result["isError"] is False
        # get_map called for the hint
        svc.get_map.assert_called()

    def test_routing_footnote_in_output(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "Routing:" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# vectr_status
# ---------------------------------------------------------------------------

class TestVectrStatus:
    def test_returns_status_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "Vectr status" in text
        assert "snowflake-arctic-embed" in text
        assert "3" in text  # indexed_files

    def test_calls_service_status(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_status", {}, svc)
        svc.status.assert_called_once()

    def test_symbol_count_in_output(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        assert "25" in result["content"][0]["text"]

    def test_strategy_shown_when_available(self) -> None:
        svc = _mock_service()
        svc.status.return_value = {
            **svc.status.return_value,
            "semantic_weight": 0.75,
            "bm25_weight": 0.25,
            "graph_first": False,
            "strategy_rationale": "large codebase — semantic weighted higher",
        }
        result = handle_tools_call("vectr_status", {}, svc)
        text = result["content"][0]["text"]
        assert "semantic=75%" in text
        assert "bm25=25%" in text
        assert "large codebase" in text

    def test_strategy_omitted_when_not_set(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        assert "semantic=" not in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# vectr_map / vectr_map_save
# ---------------------------------------------------------------------------

class TestVectrMap:
    def test_map_returns_passport(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_map", {}, svc)
        assert result["isError"] is False
        assert "Passport" in result["content"][0]["text"]

    def test_map_save_stores_summary(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_map_save", {"summary": "This is a FastAPI service."}, svc)
        assert result["isError"] is False
        svc.save_map.assert_called_once_with("This is a FastAPI service.")
        assert "saved" in result["content"][0]["text"].lower()

    def test_map_save_missing_summary_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_map_save", {}, svc)
        assert result["isError"] is True
        assert "summary is required" in result["content"][0]["text"]

    def test_map_save_empty_summary_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_map_save", {"summary": "   "}, svc)
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# vectr_locate
# ---------------------------------------------------------------------------

class TestVectrLocate:
    def test_locate_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_locate", {"name": "verify_token"}, svc)
        svc.locate_with_snippets.assert_called_once_with("verify_token", limit=10)

    def test_locate_returns_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_locate", {"name": "verify_token"}, svc)
        assert result["isError"] is False
        assert result["content"][0]["text"] == "No symbols found."

    def test_locate_missing_name_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_locate", {}, svc)
        assert result["isError"] is True
        assert "name is required" in result["content"][0]["text"]

    def test_locate_custom_limit(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_locate", {"name": "foo", "limit": 5}, svc)
        svc.locate_with_snippets.assert_called_once_with("foo", limit=5)


# ---------------------------------------------------------------------------
# vectr_trace
# ---------------------------------------------------------------------------

class TestVectrTrace:
    def test_trace_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        svc.trace_with_snippets.assert_called_once_with("dispatch", direction="both", limit=20)

    def test_trace_direction_passed_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch", "direction": "callers"}, svc)
        svc.trace_with_snippets.assert_called_once_with("dispatch", direction="callers", limit=20)

    def test_trace_invalid_direction_defaults_to_both(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch", "direction": "invalid_value"}, svc)
        svc.trace_with_snippets.assert_called_once_with("dispatch", direction="both", limit=20)

    def test_trace_missing_name_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {}, svc)
        assert result["isError"] is True
        assert "name is required" in result["content"][0]["text"]

    def test_trace_returns_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert result["isError"] is False
        assert result["content"][0]["text"] == "No trace."


# ---------------------------------------------------------------------------
# vectr_remember
# ---------------------------------------------------------------------------

class TestVectrRemember:
    def test_remember_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "Found auth bug"}, svc)
        svc.remember.assert_called_once_with(content="Found auth bug", tags=None, priority="medium")

    def test_remember_returns_note_id(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {"content": "Found auth bug"}, svc)
        assert result["isError"] is False
        assert "42" in result["content"][0]["text"]

    def test_remember_with_tags_and_priority(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {
            "content": "Fix rate limiter",
            "tags": ["wip", "rate-limit"],
            "priority": "high",
        }, svc)
        svc.remember.assert_called_once_with(
            content="Fix rate limiter",
            tags=["wip", "rate-limit"],
            priority="high",
        )

    def test_remember_missing_content_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {}, svc)
        assert result["isError"] is True

    def test_remember_invalid_priority_clamps_to_medium(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "note", "priority": "urgent"}, svc)
        svc.remember.assert_called_once_with(content="note", tags=None, priority="medium")


# ---------------------------------------------------------------------------
# vectr_recall
# ---------------------------------------------------------------------------

class TestVectrRecall:
    def test_recall_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_recall", {}, svc)
        svc.recall.assert_called_once_with(query=None, tags=None, priority=None, limit=10)

    def test_recall_with_filters(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"query": "auth", "tags": ["wip"], "priority": "high", "limit": 5}, svc)
        svc.recall.assert_called_once_with(query="auth", tags=["wip"], priority="high", limit=5)

    def test_recall_returns_notes_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_recall", {}, svc)
        assert result["isError"] is False
        assert "Notes" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# vectr_evict_hint
# ---------------------------------------------------------------------------

class TestVectrEvictHint:
    def test_returns_hint_when_set(self) -> None:
        svc = _mock_service()
        svc.eviction_hint.return_value = "Drop auth.py from context."
        result = handle_tools_call("vectr_evict_hint", {}, svc)
        assert result["isError"] is False
        assert "auth.py" in result["content"][0]["text"]

    def test_returns_clean_message_when_no_hint(self) -> None:
        svc = _mock_service()
        svc.eviction_hint.return_value = ""
        result = handle_tools_call("vectr_evict_hint", {}, svc)
        assert result["isError"] is False
        assert "clean" in result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# vectr_snapshot / vectr_snapshot_list
# ---------------------------------------------------------------------------

class TestVectrSnapshot:
    def test_snapshot_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_snapshot", {"label": "my-session"}, svc)
        svc.snapshot_session.assert_called_once_with(label="my-session", session_id=None)

    def test_snapshot_returns_id(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_snapshot", {"label": "my-session"}, svc)
        assert result["isError"] is False
        assert "snap_xyz" in result["content"][0]["text"]

    def test_snapshot_missing_label_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_snapshot", {}, svc)
        assert result["isError"] is True
        assert "label is required" in result["content"][0]["text"]

    def test_snapshot_list_empty(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_snapshot_list", {}, svc)
        assert result["isError"] is False
        assert "No snapshots" in result["content"][0]["text"]

    def test_snapshot_list_with_entries(self) -> None:
        svc = _mock_service()
        svc.list_snapshots.return_value = [
            {"snapshot_id": "snap_abc", "label": "auth-wip", "created_at": 1700000000.0},
        ]
        result = handle_tools_call("vectr_snapshot_list", {}, svc)
        text = result["content"][0]["text"]
        assert "snap_abc" in text
        assert "auth-wip" in text

    def test_snapshot_with_session_id(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_snapshot", {"label": "sess", "session_id": "s123"}, svc)
        svc.snapshot_session.assert_called_once_with(label="sess", session_id="s123")


# ---------------------------------------------------------------------------
# vectr_forget
# ---------------------------------------------------------------------------

class TestVectrForget:
    def test_forget_calls_forget_all(self) -> None:
        svc = _mock_service()
        svc.forget_all.return_value = 5
        result = handle_tools_call("vectr_forget", {}, svc)
        svc.forget_all.assert_called_once()
        assert result["isError"] is False
        assert "5" in result["content"][0]["text"]

    def test_forget_zero_notes(self) -> None:
        svc = _mock_service()
        svc.forget_all.return_value = 0
        result = handle_tools_call("vectr_forget", {}, svc)
        assert result["isError"] is False
        assert "0" in result["content"][0]["text"]

    def test_forget_in_tools_list(self) -> None:
        names = {t["name"] for t in handle_tools_list()["tools"]}
        assert "vectr_forget" in names


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    def test_unknown_tool_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_does_not_exist", {}, svc)
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]

    def test_error_mentions_tool_name(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_nonexistent_xyz", {}, svc)
        assert "vectr_nonexistent_xyz" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# _format_search_results
# ---------------------------------------------------------------------------

class TestFormatSearchResults:
    def test_empty_results_returns_no_results_message(self) -> None:
        text = _format_search_results([], "my query", 5, 100)
        assert "No results" in text
        assert "my query" in text

    def test_results_formatted_with_file_path(self) -> None:
        from agent.searcher import SearchResult
        result = SearchResult(
            file_path="auth.py", lines="10-20", symbol_name="login",
            language="python", score=0.88, content="def login(): pass",
        )
        text = _format_search_results([result], "login", 7, 50)
        assert "auth.py" in text
        assert "login" in text
        assert "0.880" in text

    def test_result_count_shown(self) -> None:
        from agent.searcher import SearchResult
        results = [
            SearchResult(file_path=f"f{i}.py", lines="1-5", symbol_name="",
                         language="python", score=0.5, content="x")
            for i in range(3)
        ]
        text = _format_search_results(results, "query", 10, 100)
        assert "3 results" in text
