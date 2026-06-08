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
    _EXPLORATION_TOOLS,
    _MEMORY_WRITE_TOOLS,
    _MEMORY_TOOLS,
    handle_tools_call,
    handle_tools_list,
    enable_memory_for_session,
    is_memory_enabled,
    _memory_enabled_sessions,
    _mcp_error,
    _format_search_results,
    _session_calls_since_save,
    _REMEMBER_NUDGE_THRESHOLD,
    _REMEMBER_NUDGE_COOLDOWN,
    _should_nudge_remember,
    _reset_calls_since_save,
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
        "notes_count": 4,
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

    def test_recall_is_conditional_on_notes_existing(self) -> None:
        desc = self._desc("vectr_recall").lower()
        assert "vectr_status" in desc, "vectr_recall must reference vectr_status as the existence check"
        assert "notes_count" in desc, "vectr_recall must mention notes_count so agent knows when to skip it"
        assert "earlier in this session" in desc or "prior session" in desc or "prior work" in desc, (
            "vectr_recall must be framed as conditional (within-session or cross-session notes), not unconditional"
        )
        assert "do not" in desc or "not call" in desc or "skip" in desc, (
            "vectr_recall must include a negative trigger — when NOT to call it"
        )

    def test_remember_says_not_for_obvious_facts(self) -> None:
        desc = self._desc("vectr_remember").lower()
        assert "not" in desc or "do not" in desc, (
            "vectr_remember must include a negative trigger — when NOT to store a note"
        )
        assert "re-deriv" in desc or "obvious" in desc or "easily" in desc, (
            "vectr_remember must warn against storing easily re-derivable facts"
        )

    def test_remember_content_schema_encourages_code_not_prose(self) -> None:
        from integrations.mcp_server import MCP_TOOLS
        tool = next(t for t in MCP_TOOLS if t["name"] == "vectr_remember")
        content_desc = tool["inputSchema"]["properties"]["content"]["description"].lower()
        assert "paste" in content_desc or "signature" in content_desc or "code" in content_desc, (
            "vectr_remember content description must encourage storing actual code, not prose"
        )
        assert "1-3 sentences" not in content_desc, (
            "vectr_remember content description must not constrain to 1-3 sentences"
        )

    def test_map_says_not_substitute_for_recall(self) -> None:
        desc = self._desc("vectr_map").lower()
        assert "vectr_status" in desc or "vectr_recall" in desc, (
            "vectr_map must clarify it is not a substitute for vectr_recall / vectr_status"
        )

    def test_snapshot_describes_milestone_grouping(self) -> None:
        desc = self._desc("vectr_snapshot").lower()
        assert "milestone" in desc or "checkpoint" in desc or "return" in desc, (
            "vectr_snapshot must frame itself as a milestone/checkpoint to return to, not as session-ending vocab"
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

    def test_status_surfaces_notes_count_and_recall_guidance(self) -> None:
        desc = self._desc("vectr_status").lower()
        assert "notes_count" in desc, (
            "vectr_status must mention notes_count so agent can decide whether to call recall"
        )
        assert "vectr_recall" in desc, (
            "vectr_status must reference vectr_recall to guide the agent's next action"
        )


# ---------------------------------------------------------------------------
# handle_tools_list
# ---------------------------------------------------------------------------

class TestHandleToolsList:
    def test_returns_all_tools_no_session(self) -> None:
        # No session_id → full list (backwards compat)
        result = handle_tools_list()
        assert "tools" in result
        assert isinstance(result["tools"], list)
        assert len(result["tools"]) == len(MCP_TOOLS)

    def test_each_tool_has_name_and_schema(self) -> None:
        for tool in handle_tools_list()["tools"]:
            assert "name" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_required_tools_present_no_session(self) -> None:
        names = {t["name"] for t in handle_tools_list()["tools"]}
        for expected in ("vectr_search", "vectr_status", "vectr_remember", "vectr_recall",
                         "vectr_map", "vectr_locate", "vectr_trace", "vectr_snapshot"):
            assert expected in names


# ---------------------------------------------------------------------------
# T13: Adaptive tool registration
# ---------------------------------------------------------------------------

class TestAdaptiveToolRegistration:
    def setup_method(self) -> None:
        # Isolate session state between tests
        _memory_enabled_sessions.clear()

    def teardown_method(self) -> None:
        _memory_enabled_sessions.clear()

    def test_new_session_gets_exploration_and_write_tools(self) -> None:
        result = handle_tools_list(session_id="sess-new-001")
        names = {t["name"] for t in result["tools"]}
        # exploration tools always present
        for t in _EXPLORATION_TOOLS:
            assert t["name"] in names
        # vectr_remember and vectr_evict_hint always present (write side)
        assert "vectr_remember" in names
        assert "vectr_evict_hint" in names
        # read/manage tools gated until memory enabled
        for t in _MEMORY_TOOLS:
            assert t["name"] not in names

    def test_no_session_id_returns_full_list(self) -> None:
        result = handle_tools_list(session_id=None)
        assert len(result["tools"]) == len(MCP_TOOLS)

    def test_enable_memory_adds_read_tools(self) -> None:
        sid = "sess-enable-002"
        assert not is_memory_enabled(sid)
        enable_memory_for_session(sid)
        assert is_memory_enabled(sid)
        result = handle_tools_list(session_id=sid)
        assert len(result["tools"]) == len(MCP_TOOLS)

    def test_vectr_status_with_notes_enables_memory_tools(self) -> None:
        from unittest.mock import MagicMock
        sid = "sess-status-003"
        svc = MagicMock()
        svc.status.return_value = {
            "notes_count": 3, "indexed_files": 10, "total_chunks": 100,
            "symbol_count": 50, "last_indexed": "1s ago",
            "embed_model": "test", "workspace_root": "/tmp",
            "semantic_weight": None,
        }
        svc.should_evict.return_value = False

        # Before status call — only exploration tools
        assert not is_memory_enabled(sid)

        # Call vectr_status
        handle_tools_call("vectr_status", {}, svc, session_id=sid)

        # After status call with notes_count > 0 — memory enabled
        assert is_memory_enabled(sid)
        result = handle_tools_list(session_id=sid)
        assert len(result["tools"]) == len(MCP_TOOLS)

    def test_vectr_status_no_notes_does_not_enable_memory(self) -> None:
        from unittest.mock import MagicMock
        sid = "sess-status-004"
        svc = MagicMock()
        svc.status.return_value = {
            "notes_count": 0, "indexed_files": 10, "total_chunks": 100,
            "symbol_count": 50, "last_indexed": "1s ago",
            "embed_model": "test", "workspace_root": "/tmp",
            "semantic_weight": None,
        }
        handle_tools_call("vectr_status", {}, svc, session_id=sid)
        assert not is_memory_enabled(sid)

    def test_vectr_remember_enables_memory_tools(self) -> None:
        from unittest.mock import MagicMock
        sid = "sess-remember-005"
        svc = MagicMock()
        svc.remember.return_value = 1

        assert not is_memory_enabled(sid)
        handle_tools_call("vectr_remember", {"content": "stub code here"}, svc, session_id=sid)
        assert is_memory_enabled(sid)

    def test_exploration_tools_always_include_core_set(self) -> None:
        exploration_names = {t["name"] for t in _EXPLORATION_TOOLS}
        for expected in ("vectr_search", "vectr_status", "vectr_map", "vectr_locate", "vectr_trace"):
            assert expected in exploration_names

    def test_memory_write_tools_always_visible(self) -> None:
        write_names = {t["name"] for t in _MEMORY_WRITE_TOOLS}
        assert "vectr_remember" in write_names
        assert "vectr_evict_hint" in write_names

    def test_memory_read_tools_gated(self) -> None:
        memory_names = {t["name"] for t in _MEMORY_TOOLS}
        for expected in ("vectr_recall", "vectr_snapshot", "vectr_forget"):
            assert expected in memory_names
        # remember and evict_hint live in _MEMORY_WRITE_TOOLS, not here
        assert "vectr_remember" not in memory_names
        assert "vectr_evict_hint" not in memory_names

    def test_session_state_is_independent_between_sessions(self) -> None:
        enable_memory_for_session("sess-A")
        assert is_memory_enabled("sess-A")
        assert not is_memory_enabled("sess-B")


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

    def test_n_results_defaults_to_5(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "foo"}, svc)
        call_kwargs = svc.search_routed.call_args[1]
        assert call_kwargs["n_results"] == 5, (
            "default n_results must be 5 to limit token accumulation per search call"
        )

    def test_explicit_n_results_overrides_default(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "foo", "n_results": 10}, svc)
        call_kwargs = svc.search_routed.call_args[1]
        assert call_kwargs["n_results"] == 10

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

    def test_notes_count_shown_in_output(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        text = result["content"][0]["text"]
        assert "4" in text, "notes_count from status() must appear in vectr_status output"
        assert "vectr_recall" in text.lower(), (
            "when notes_count > 0, output must hint to call vectr_recall"
        )

    def test_notes_count_zero_shows_skip_hint(self) -> None:
        svc = _mock_service()
        svc.status.return_value = {**svc.status.return_value, "notes_count": 0}
        result = handle_tools_call("vectr_status", {}, svc)
        text = result["content"][0]["text"].lower()
        assert "skip" in text or "no prior" in text, (
            "when notes_count == 0, output must tell agent to skip vectr_recall"
        )
        assert "vectr_recall" not in text.replace("skip vectr_recall", "").replace("no prior", ""), \
            "when notes_count == 0, must not prompt agent to call recall"


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
        svc.locate_with_snippets.assert_called_once_with("verify_token", limit=10, caller_file=None)

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
        svc.locate_with_snippets.assert_called_once_with("foo", limit=5, caller_file=None)

    def test_locate_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = True
        svc.eviction_hint.return_value = "Drop these: auth.py"
        result = handle_tools_call("vectr_locate", {"name": "verify_token"}, svc)
        assert "Drop these: auth.py" in result["content"][0]["text"]

    def test_locate_no_eviction_hint_when_below_threshold(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = False
        result = handle_tools_call("vectr_locate", {"name": "verify_token"}, svc)
        assert "Context management hint" not in result["content"][0]["text"]


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

    def test_trace_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = True
        svc.eviction_hint.return_value = "Drop these: bidder.py"
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert "Drop these: bidder.py" in result["content"][0]["text"]

    def test_trace_no_eviction_hint_when_below_threshold(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = False
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert "Context management hint" not in result["content"][0]["text"]


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
# Turn-count vectr_remember nudge (E10)
# ---------------------------------------------------------------------------

class TestRememberNudge:
    """Verify the MCP-level turn-count reminder fires correctly.

    Each test uses a unique session_id to avoid cross-test state leakage.
    The counter is also explicitly reset after each test via teardown.
    """

    _session_prefix = "nudge_test_"
    _counter = 0

    def _sid(self) -> str:
        TestRememberNudge._counter += 1
        return f"{self._session_prefix}{TestRememberNudge._counter}"

    def teardown_method(self, _method):
        # Clean up any session state written by this test
        keys = [k for k in list(_session_calls_since_save.keys()) if k.startswith(self._session_prefix)]
        for k in keys:
            _session_calls_since_save.pop(k, None)

    def _make_search_calls(self, svc, session_id: str, n: int) -> dict:
        result = {}
        for _ in range(n):
            result = handle_tools_call("vectr_search", {"query": "test"}, svc, session_id=session_id)
        return result

    def test_nudge_absent_below_threshold(self) -> None:
        svc = _mock_service()
        sid = self._sid()
        result = self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD - 1)
        assert "vectr_remember reminder" not in result["content"][0]["text"]

    def test_nudge_fires_at_threshold(self) -> None:
        svc = _mock_service()
        sid = self._sid()
        result = self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD)
        assert "vectr_remember reminder" in result["content"][0]["text"]

    def test_nudge_text_is_imperative_and_cites_compact(self) -> None:
        svc = _mock_service()
        sid = self._sid()
        result = self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD)
        text = result["content"][0]["text"]
        assert "vectr_remember" in text
        assert "/compact" in text
        assert "future session" in text

    def test_nudge_resets_on_vectr_remember(self) -> None:
        svc = _mock_service()
        sid = self._sid()
        # Hit the threshold
        self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD)
        # Save a note — should reset counter
        handle_tools_call("vectr_remember", {"content": "key finding"}, svc, session_id=sid)
        # One call below threshold — nudge must NOT appear
        result = handle_tools_call("vectr_search", {"query": "test"}, svc, session_id=sid)
        assert "vectr_remember reminder" not in result["content"][0]["text"]

    def test_nudge_fires_again_after_cooldown(self) -> None:
        svc = _mock_service()
        sid = self._sid()
        # Hit threshold, then save to reset
        self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD)
        handle_tools_call("vectr_remember", {"content": "key finding"}, svc, session_id=sid)
        # Hit threshold again + cooldown
        result = self._make_search_calls(svc, sid, _REMEMBER_NUDGE_THRESHOLD + _REMEMBER_NUDGE_COOLDOWN)
        assert "vectr_remember reminder" in result["content"][0]["text"]

    def test_nudge_absent_without_session_id(self) -> None:
        svc = _mock_service()
        # No session_id → nudge must never fire regardless of call count
        result = self._make_search_calls(svc, None, _REMEMBER_NUDGE_THRESHOLD + 5)
        # _make_search_calls passes session_id to handle_tools_call as None
        assert "vectr_remember reminder" not in result["content"][0]["text"]

    def test_nudge_only_in_discovery_tools_not_in_recall_or_status(self) -> None:
        svc = _mock_service()
        # Part A: status and recall never show nudge, even when counter exceeds threshold
        sid_a = self._sid()
        for _ in range(_REMEMBER_NUDGE_THRESHOLD + 5):
            handle_tools_call("vectr_status", {}, svc, session_id=sid_a)
        assert "vectr_remember reminder" not in \
            handle_tools_call("vectr_status", {}, svc, session_id=sid_a)["content"][0]["text"]
        assert "vectr_remember reminder" not in \
            handle_tools_call("vectr_recall", {}, svc, session_id=sid_a)["content"][0]["text"]
        # Part B: search DOES show nudge when the search call is exactly the threshold call
        sid_b = self._sid()
        result = self._make_search_calls(svc, sid_b, _REMEMBER_NUDGE_THRESHOLD)
        assert "vectr_remember reminder" in result["content"][0]["text"]


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

    def test_recall_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = True
        svc.eviction_hint.return_value = "Drop these: segment.py"
        result = handle_tools_call("vectr_recall", {}, svc)
        assert "Drop these: segment.py" in result["content"][0]["text"]

    def test_recall_no_eviction_hint_when_below_threshold(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = False
        result = handle_tools_call("vectr_recall", {}, svc)
        assert "Context management hint" not in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# vectr_evict_hint
# ---------------------------------------------------------------------------

class TestVectrEvictHint:
    def test_returns_hint_when_set(self) -> None:
        svc = _mock_service()
        svc.eviction_hint.return_value = "Vectr can re-retrieve auth.py in <50ms."
        result = handle_tools_call("vectr_evict_hint", {}, svc)
        assert result["isError"] is False
        assert "auth.py" in result["content"][0]["text"]  # proxy for hint content passing through

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

    def test_content_over_80_lines_is_truncated(self) -> None:
        from agent.searcher import SearchResult
        long_content = "\n".join(f"line {i}" for i in range(120))
        result = SearchResult(
            file_path="big.py", lines="1-120", symbol_name="big_fn",
            language="python", score=0.9, content=long_content,
        )
        text = _format_search_results([result], "query", 10, 100)
        shown_lines = [l for l in text.splitlines() if l.startswith("line ")]
        assert len(shown_lines) == 80, "exactly 80 content lines must be shown"
        assert "more lines" in text, "truncation footer must mention remaining line count"

    def test_content_at_or_under_80_lines_shown_in_full(self) -> None:
        from agent.searcher import SearchResult
        short_content = "\n".join(f"line {i}" for i in range(60))
        result = SearchResult(
            file_path="small.py", lines="1-60", symbol_name="",
            language="python", score=0.8, content=short_content,
        )
        text = _format_search_results([result], "query", 5, 100)
        assert "more lines" not in text
        assert "line 59" in text

    def test_truncation_footer_includes_read_pointer(self) -> None:
        from agent.searcher import SearchResult
        long_content = "\n".join(f"line {i}" for i in range(120))
        result = SearchResult(
            file_path="big.py", lines="1-120", symbol_name="",
            language="python", score=0.9, content=long_content,
        )
        text = _format_search_results([result], "query", 10, 100)
        assert "Read" in text, "footer must contain a Read pointer for full context"


# ---------------------------------------------------------------------------
# EvictionAdvisor integration — real advisor wired through handle_tools_call
#
# Mock-based tests (TestVectrSearch.test_eviction_hint_appended_when_should_evict)
# mock both should_evict() and eviction_hint() at the service level, so they cannot
# catch bugs like a missing record_results() call or passive hint text. These tests
# use a real EvictionAdvisor to catch wiring bugs in mcp_server.py.
# ---------------------------------------------------------------------------

class TestEvictionAdvisorIntegration:

    def _service_with_real_advisor(self, **advisor_kwargs):
        from agent.eviction_advisor import EvictionAdvisor
        svc = _mock_service()
        real_advisor = EvictionAdvisor(**advisor_kwargs)
        svc._eviction_advisor = real_advisor
        svc.should_evict.side_effect = real_advisor.should_evict
        svc.eviction_hint.side_effect = real_advisor.eviction_hint
        return svc, real_advisor

    def test_search_populates_chunks_in_eviction_advisor(self) -> None:
        # Verifies record_results() is called after vectr_search. Without it,
        # _chunks stays empty and eviction_hint() always returns "".
        svc, advisor = self._service_with_real_advisor()
        assert len(advisor._chunks) == 0
        handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert len(advisor._chunks) > 0, (
            "handle_tools_call must call record_results() after vectr_search so "
            "eviction_hint() can reference what was retrieved"
        )

    def test_eviction_hint_fires_in_search_response_after_threshold(self) -> None:
        # With threshold=0 the hint fires on the 1st retrieval call.
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
        )
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "Context management hint" in result["content"][0]["text"], (
            "eviction hint must be injected into vectr_search response when threshold is exceeded"
        )

    def test_eviction_hint_contains_action_required(self) -> None:
        # Directive language is required — passive phrasing gets ignored by the LLM.
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
        )
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "ACTION REQUIRED" in result["content"][0]["text"], (
            "injected eviction hint must contain 'ACTION REQUIRED' to prompt vectr_remember"
        )

    def test_no_hint_when_search_returns_empty_results(self) -> None:
        # should_evict() may be True but empty results → no chunks → hint must not fire.
        from agent.query_router import RoutingDecision, QueryType
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
        )
        empty_decision = RoutingDecision(
            query_type=QueryType.SEMANTIC, semantic_weight=0.7,
            also_run_symbol_lookup=False, also_run_trace=False,
            include_map_hint=False, rationale="semantic",
        )
        svc.search_routed.return_value = ([], 5, empty_decision, [], [])
        result = handle_tools_call("vectr_search", {"query": "nothing"}, svc)
        assert "Context management hint" not in result["content"][0]["text"]
