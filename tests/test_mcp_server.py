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

from app.service import VectrService
from tests._seam import assert_seam_call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_service():
    from agent.searcher import SearchResult
    from agent.working_context_store import WorkingNote

    svc = MagicMock()
    svc.total_chunks = 100

    result = SearchResult(
        file_path="auth.py", lines="1-10", symbol_name="verify_token",
        language="python", score=0.9, content="def verify_token(): ...",
    )
    svc.search.return_value = ([result], 10)
    # UPG-QUERYTYPE-REROUTE: additive symbol-graph hint — empty by default so
    # the common-case response has no exact-identifier-match section.
    svc.identifier_hint_symbols.return_value = []
    # UPG-NEARMISS-SYMBOL-NAMES: additive near-miss hint — empty by default so
    # the common-case response has no near-miss section either.
    svc.identifier_hint_nearmiss.return_value = []
    svc.status.return_value = {
        "indexed_files": 3,
        "total_chunks": 100,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "Snowflake/snowflake-arctic-embed-m-v1.5",
        "workspace_root": "/repo",
        "symbol_count": 25,
        "notes_count": 4,
        # UPG-8.2: retrieval weights + strategy fields are always present in
        # the real service.status() output (config defaults before the first
        # fingerprint, fingerprint-derived after) — mock the real shape.
        "semantic_weight": 0.70,
        "bm25_weight": 0.30,
        "graph_first": False,
        "strategy_rationale": "default weights — no workspace fingerprint yet, index the workspace to compute one",
        # UPG-WATCHER-PRESSURE-GOVERNOR: watcher backlog fields are always
        # present in the real service.status() output — mock the real shape.
        "watcher_burst_mode": False,
        "watcher_pending_files": 0,
        "watcher_batch_running": False,
        "watcher_last_batch_duration_ms": 0,
        # UPG-HOOK-INJECT-OBSERVABILITY: hook injection counters are always
        # present in the real service.status() output — mock the real shape.
        "hook_injection_counts": {},
    }
    svc.get_map.return_value = "# Passport\nA FastAPI service."
    # UPG-6.2: save_map returns a shaped result, not None — real shape.
    svc.save_map.return_value = {"saved": True, "existing_summary": None}
    svc.locate_with_snippets.return_value = []
    svc.format_locate.return_value = "No symbols found."
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.remember.return_value = 42
    # UPG-SCOPE-SURFACE-BACK: the MCP remember confirmation looks up the
    # resolved note via get_note() to surface its scope — a real WorkingNote
    # (not a bare MagicMock stand-in) so that lookup renders a real scope
    # value instead of a MagicMock repr in every confirmation text below.
    svc.get_note.return_value = WorkingNote(
        note_id=42, workspace="/repo", content="stub", tags=[], priority="medium",
        created_at=0.0, last_accessed=0.0, kind="finding", scope="workspace",
    )
    svc.recall.return_value = "# Notes\n[1] [HIGH] some note\n"
    svc.eviction_hint.return_value = ""
    svc.auto_eviction_hint.return_value = ""   # UPG-7.1: gated per-response footer
    svc.should_evict.return_value = False
    svc.snapshot_session.return_value = "snap_xyz"
    svc.list_snapshots.return_value = []
    svc.memory_only = False
    svc.search_only = False
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

    def test_n_results_description_guides_low_values(self) -> None:
        # UPG-NRESULTS-GUIDANCE (B5): the n_results param description must steer
        # the caller to 1-2 for specific lookups (token economy) and widen only
        # for exploratory queries — description text only, no query-side logic.
        tool = next(t for t in MCP_TOOLS if t["name"] == "vectr_search")
        n_desc = tool["inputSchema"]["properties"]["n_results"]["description"].lower()
        assert "1" in n_desc and "2" in n_desc, "n_results guidance must name the low-value case (1-2)"
        assert "exploratory" in n_desc or "survey" in n_desc, (
            "n_results guidance must say when to widen (exploratory/survey queries)"
        )

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
                         "vectr_map", "vectr_locate", "vectr_trace", "vectr_snapshot",
                         "vectr_fetch"):
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
        svc.search_only = False

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
        call_kwargs = svc.search.call_args[1]
        assert call_kwargs["n_results"] == 5, (
            "default n_results must be 5 to limit token accumulation per search call"
        )

    def test_explicit_n_results_overrides_default(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "foo", "n_results": 10}, svc)
        call_kwargs = svc.search.call_args[1]
        assert call_kwargs["n_results"] == 10

    def test_search_schema_default_matches_handler_upg83(self) -> None:
        # UPG-8.3: the advertised schema default must match what the handler
        # actually uses (was schema=10 but handler=5 → agents got fewer results
        # than the contract promised).
        from integrations.mcp_server import _EXPLORATION_TOOLS
        search_tool = next(t for t in _EXPLORATION_TOOLS if t["name"] == "vectr_search")
        assert search_tool["inputSchema"]["properties"]["n_results"]["default"] == 5
        assert "default: 5" in search_tool["inputSchema"]["properties"]["n_results"]["description"]

    def test_locate_and_trace_descriptions_use_keyword_example_upg_trace_empty_hint(self) -> None:
        # UPG-TRACE-EMPTY-HINT (F40-class): both `name` schema params are required
        # keyword args, but the description's "Example:" line called them
        # positionally (`vectr_locate('X')`) — trained a live failed tool call.
        from integrations.mcp_server import _EXPLORATION_TOOLS
        locate_tool = next(t for t in _EXPLORATION_TOOLS if t["name"] == "vectr_locate")
        trace_tool = next(t for t in _EXPLORATION_TOOLS if t["name"] == "vectr_trace")
        assert "vectr_locate(name=" in locate_tool["description"]
        assert "vectr_locate('" not in locate_tool["description"]
        assert "vectr_trace(name=" in trace_tool["description"]
        assert "vectr_trace('" not in trace_tool["description"]

    def test_n_results_capped_at_50(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "foo", "n_results": 999}, svc)
        call_kwargs = svc.search.call_args[1]
        assert call_kwargs["n_results"] <= 50

    def test_language_filter_passed_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_search", {"query": "fn", "language": "python"}, svc)
        call_kwargs = svc.search.call_args[1]
        assert call_kwargs.get("language") == "python"

    def test_eviction_hint_appended_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.auto_eviction_hint.return_value = "Drop these chunks: auth.py"  # UPG-7.1 gated path
        result = handle_tools_call("vectr_search", {"query": "auth"}, svc)
        assert "Drop these chunks" in result["content"][0]["text"]

    def test_no_routing_footnote_in_output(self) -> None:
        # UPG-QUERYTYPE-REROUTE: the regex query-classification layer (and its
        # "─── Routing: ... ───" footnote) is deleted outright — vectr_search
        # always runs hybrid semantic retrieval at the fingerprint-derived
        # weight, with no per-query classification artifact in the output.
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "Routing:" not in result["content"][0]["text"]

    # -----------------------------------------------------------------
    # UPG-NOTFOUND-FLOOR (F46) — low-confidence banner
    # -----------------------------------------------------------------

    def test_low_confidence_banner_leads_response(self) -> None:
        """When CodeSearcher flags the pool low_confidence, the MCP response
        must LEAD with a banner naming the config-driven message, isError stays
        False, and results are still shown in full below it (never suppressed)."""
        from agent.searcher import SearchResult, SearchResultList
        from agent.config import NOTFOUND_FLOOR_BANNER

        svc = _mock_service()
        weak = SearchResult(
            file_path="unrelated/module.py", lines="1-3", symbol_name="unrelated_fn",
            language="python", score=0.81, content="def unrelated_fn():\n    pass",
        )
        flagged = SearchResultList([weak])
        flagged.low_confidence = True
        svc.search.return_value = (flagged, 10)

        result = handle_tools_call("vectr_search", {"query": "CORS handling implementation"}, svc)
        text = result["content"][0]["text"]

        assert result["isError"] is False
        assert NOTFOUND_FLOOR_BANNER in text
        # banner leads the response, before the actual results section
        assert text.index(NOTFOUND_FLOOR_BANNER) < text.index("unrelated_fn")

    def test_no_banner_when_not_low_confidence(self) -> None:
        """The default mock's plain-list search result (no low_confidence
        attribute) must not emit the banner — the common/strong-match case."""
        from agent.config import NOTFOUND_FLOOR_BANNER
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert NOTFOUND_FLOOR_BANNER not in result["content"][0]["text"]

    # -----------------------------------------------------------------
    # UPG-QUERYTYPE-REROUTE — additive symbol-graph identifier hint
    # -----------------------------------------------------------------

    def test_identifier_hint_section_appended_below_results(self) -> None:
        """When service.identifier_hint_symbols() resolves at least one exact
        match, the MCP dispatch layer appends a hint section BELOW the L3
        results — never prepended, reordered, or reweighted."""
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        svc.identifier_hint_symbols.return_value = [
            Symbol(
                symbol_id=1, workspace="/repo", name="WorkspaceLock", kind="class",
                file_path="resolver.py", start_line=214, end_line=240,
            ),
        ]
        result = handle_tools_call("vectr_search", {"query": "WorkspaceLock acquisition"}, svc)
        text = result["content"][0]["text"]

        assert result["isError"] is False
        assert "Symbol graph (exact matches for query identifiers)" in text
        assert "[class] WorkspaceLock  resolver.py:214" in text
        # appended BELOW the L3 result section, not prepended above it
        assert text.index("auth.py") < text.index("Symbol graph (exact matches")

    def test_no_identifier_hint_section_when_nothing_resolves(self) -> None:
        """The common case — no identifier-shaped token, or none resolves —
        must not emit the section at all."""
        svc = _mock_service()  # identifier_hint_symbols() defaults to []
        result = handle_tools_call("vectr_search", {"query": "what are the dependencies here"}, svc)
        assert "Symbol graph (exact matches" not in result["content"][0]["text"]

    # -----------------------------------------------------------------
    # UPG-LOWCONF-SLIM-DEDUPE (B3) — pointer mode drops the symbol-graph
    # dup and the re-fetchable footer
    # -----------------------------------------------------------------

    def test_identifier_hint_suppressed_in_pointer_mode_when_fully_duplicate(self) -> None:
        """A low_confidence response whose only hint symbol is already shown
        as a pointer-list entry (same file_path + start_line) must not repeat
        it in a separate symbol-graph section."""
        from agent.searcher import SearchResult, SearchResultList
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        r = SearchResult(
            file_path="resolver.py", lines="214-240", symbol_name="WorkspaceLock",
            language="python", score=0.02, content="class WorkspaceLock: ...",
            symbol_start_line=214, symbol_end_line=240,
        )
        rl = SearchResultList([r])
        rl.low_confidence = True
        svc.search.return_value = (rl, 10)
        svc.identifier_hint_symbols.return_value = [
            Symbol(
                symbol_id=1, workspace="/repo", name="WorkspaceLock", kind="class",
                file_path="resolver.py", start_line=214, end_line=240,
            ),
        ]
        result = handle_tools_call("vectr_search", {"query": "WorkspaceLock acquisition"}, svc)
        text = result["content"][0]["text"]

        assert "resolver.py" in text          # still shown once, as a pointer
        assert "Symbol graph (exact matches" not in text  # no duplicate section

    def test_identifier_hint_partial_dedupe_in_pointer_mode(self) -> None:
        """When only SOME hint symbols duplicate the pointer list, the
        duplicate is dropped but a genuinely new symbol still gets its own
        line — this is a dedupe, not a blanket suppression."""
        from agent.searcher import SearchResult, SearchResultList
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        r = SearchResult(
            file_path="resolver.py", lines="214-240", symbol_name="WorkspaceLock",
            language="python", score=0.02, content="class WorkspaceLock: ...",
            symbol_start_line=214, symbol_end_line=240,
        )
        rl = SearchResultList([r])
        rl.low_confidence = True
        svc.search.return_value = (rl, 10)
        svc.identifier_hint_symbols.return_value = [
            Symbol(
                symbol_id=1, workspace="/repo", name="WorkspaceLock", kind="class",
                file_path="resolver.py", start_line=214, end_line=240,
            ),
            Symbol(
                symbol_id=2, workspace="/repo", name="OtherLock", kind="class",
                file_path="other.py", start_line=5, end_line=20,
            ),
        ]
        result = handle_tools_call("vectr_search", {"query": "WorkspaceLock vs OtherLock"}, svc)
        text = result["content"][0]["text"]

        assert "Symbol graph (exact matches for query identifiers)" in text
        assert "OtherLock  other.py:5" in text
        # the duplicate's own hint line (kind-prefixed) must not appear
        assert "[class] WorkspaceLock  resolver.py:214" not in text

    def test_identifier_hint_not_deduped_outside_pointer_mode(self) -> None:
        """Full (non-low-confidence) mode is out of scope for B3 — the hint
        section still appears even when it names the same location as a
        result, unchanged prior behaviour."""
        from agent.symbol_graph import Symbol

        svc = _mock_service()  # default search result is NOT low_confidence
        svc.identifier_hint_symbols.return_value = [
            Symbol(
                symbol_id=1, workspace="/repo", name="verify_token", kind="function",
                file_path="auth.py", start_line=1, end_line=10,
            ),
        ]
        result = handle_tools_call("vectr_search", {"query": "verify_token"}, svc)
        text = result["content"][0]["text"]
        assert "Symbol graph (exact matches for query identifiers)" in text

    def test_refetchable_footer_suppressed_in_pointer_mode(self) -> None:
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(file_path="auth.py", lines="10-20", symbol_name="login",
                         language="python", score=0.01, content="BODY",
                         score_source="reranker")
        rl = SearchResultList([r])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "re-fetchable anytime" not in text

    def test_refetchable_footer_present_outside_pointer_mode(self) -> None:
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(file_path="auth.py", lines="10-20", symbol_name="login",
                         language="python", score=0.9, content="BODY",
                         score_source="reranker")
        rl = SearchResultList([r])  # low_confidence defaults False
        text = _format_search_results(rl, "login", 5, 100)
        assert "re-fetchable anytime" in text

    # -----------------------------------------------------------------
    # UPG-NEARMISS-SYMBOL-NAMES — additive near-miss symbol-name hint
    # -----------------------------------------------------------------

    def test_nearmiss_section_appended_below_results_and_honestly_labeled(self) -> None:
        """When identifier_hint_nearmiss() returns candidates, the dispatch
        layer appends an inexact-labeled section BELOW the L3 results (and
        below the exact-match section, if any) — never as a match."""
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        svc.identifier_hint_nearmiss.return_value = [
            ("CacheControlHeader", [
                Symbol(
                    symbol_id=2, workspace="/repo", name="CacheControl", kind="class",
                    file_path="control.py", start_line=22, end_line=40,
                ),
            ]),
        ]
        result = handle_tools_call("vectr_search", {"query": "CacheControlHeader usage"}, svc)
        text = result["content"][0]["text"]

        assert result["isError"] is False
        assert "No exact match for 'CacheControlHeader'" in text
        assert "nearest symbol names" in text
        assert "CacheControl (control.py:22)" in text
        # appended below the L3 result section
        assert text.index("auth.py") < text.index("No exact match for")

    def test_nearmiss_section_below_exact_match_section_when_both_present(self) -> None:
        """A query with one exactly-resolved token and one near-miss token
        shows both sections, exact-match first, near-miss below it."""
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        svc.identifier_hint_symbols.return_value = [
            Symbol(
                symbol_id=1, workspace="/repo", name="WorkspaceLock", kind="class",
                file_path="resolver.py", start_line=214, end_line=240,
            ),
        ]
        svc.identifier_hint_nearmiss.return_value = [
            ("CacheControlHeader", [
                Symbol(
                    symbol_id=2, workspace="/repo", name="CacheControl", kind="class",
                    file_path="control.py", start_line=22, end_line=40,
                ),
            ]),
        ]
        result = handle_tools_call(
            "vectr_search", {"query": "WorkspaceLock vs CacheControlHeader"}, svc
        )
        text = result["content"][0]["text"]
        assert text.index("Symbol graph (exact matches") < text.index("No exact match for")

    def test_no_nearmiss_section_when_nothing_found(self) -> None:
        """The common case — no near-miss candidates at all — must not emit
        the section (matches identifier_hint_nearmiss() defaulting to [])."""
        svc = _mock_service()
        result = handle_tools_call("vectr_search", {"query": "XyzzyQwerty nonsense"}, svc)
        assert "No exact match for" not in result["content"][0]["text"]

    def test_nearmiss_section_caps_at_three_names(self) -> None:
        """Never more than 3 near-miss names total in the rendered section,
        even if a mocked service returned more than the configured cap."""
        from agent.symbol_graph import Symbol

        svc = _mock_service()
        svc.identifier_hint_nearmiss.return_value = [
            ("BigTokenName", [
                Symbol(
                    symbol_id=i, workspace="/repo", name=f"BigToken{i}", kind="class",
                    file_path="t.py", start_line=i, end_line=i + 1,
                )
                for i in range(3)
            ]),
        ]
        result = handle_tools_call("vectr_search", {"query": "BigTokenName lookup"}, svc)
        text = result["content"][0]["text"]
        assert text.count("(t.py:") == 3


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

    def test_strategy_always_shown_even_with_default_weights(self) -> None:
        # UPG-8.2: retrieval weights + strategy fields must always render —
        # previously the Retrieval line was omitted whenever the service
        # hadn't computed a fingerprint-derived strategy yet. service.status()
        # now always populates these (falling back to config defaults), so
        # the line must always be present, not conditionally shown.
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        assert "semantic=" in result["content"][0]["text"]

    def test_notes_count_shown_in_output(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        text = result["content"][0]["text"]
        assert "4" in text, "notes_count from status() must appear in vectr_status output"
        assert "vectr_recall" in text.lower(), (
            "when notes_count > 0, output must hint to call vectr_recall"
        )

    def test_watcher_line_absent_when_quiet(self) -> None:
        # UPG-WATCHER-PRESSURE-GOVERNOR: a quiet workspace's status stays terse.
        svc = _mock_service()
        result = handle_tools_call("vectr_status", {}, svc)
        assert "Watcher" not in result["content"][0]["text"]

    def test_watcher_line_shown_during_burst(self) -> None:
        svc = _mock_service()
        svc.status.return_value = {
            **svc.status.return_value,
            "watcher_burst_mode": True,
            "watcher_pending_files": 12,
            "watcher_batch_running": False,
            "watcher_last_batch_duration_ms": 340,
        }
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Watcher" in text
        assert "12" in text
        assert "burst mode" in text
        assert "340ms" in text

    def test_watcher_line_shown_while_batch_running(self) -> None:
        svc = _mock_service()
        svc.status.return_value = {
            **svc.status.return_value,
            "watcher_burst_mode": False,
            "watcher_pending_files": 3,
            "watcher_batch_running": True,
            "watcher_last_batch_duration_ms": 0,
        }
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Watcher" in text
        assert "batch running" in text

    def test_hook_injection_line_absent_when_no_injections(self) -> None:
        # UPG-HOOK-INJECT-OBSERVABILITY: a workspace with no hooks installed
        # (or whose hooks haven't fired yet) stays terse, same as Watcher.
        svc = _mock_service()
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Hook injections" not in text

    def test_hook_injection_line_shown_with_counts(self) -> None:
        svc = _mock_service()
        svc.status.return_value = {
            **svc.status.return_value,
            "hook_injection_counts": {"SessionStart": 3, "PreToolUse": 2},
        }
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Hook injections" in text
        assert "SessionStart 3" in text
        assert "PreToolUse 2" in text

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

    def test_tool_style_hint_shown_for_memory_first(self) -> None:
        svc = _mock_service()
        svc.suggest_instruction_style.return_value = "memory-first"
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Tool style" in text
        assert "[memory-first]" in text

    def test_tool_style_label_never_collides_with_mode_label(self) -> None:
        """UPG-TOOLSTYLE-LABEL-COLLISION: the CLAUDE.md authoring-style hint
        ("Tool style") and the operating-mode line ("Mode") render in the
        same vectr_status block — they must never share a literal value, or
        "Mode: full" next to "Tool style: [memory-only]" reads as a
        self-contradictory status (search enabled, yet "memory-only")."""
        svc = _mock_service()
        svc.status.return_value = {**svc.status.return_value, "mode": "full"}
        svc.suggest_instruction_style.return_value = "memory-first"
        text = handle_tools_call("vectr_status", {}, svc)["content"][0]["text"]
        assert "Mode           : full" in text
        assert "[memory-first]" in text
        assert "memory-only" not in text


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
        # UPG-6.2: overwrite defaults to False and is always forwarded explicitly.
        svc.save_map.assert_called_once_with("This is a FastAPI service.", overwrite=False)
        assert "saved" in result["content"][0]["text"].lower()

    def test_map_save_overwrite_true_forwarded(self) -> None:
        svc = _mock_service()
        handle_tools_call(
            "vectr_map_save", {"summary": "Updated summary.", "overwrite": True}, svc
        )
        svc.save_map.assert_called_once_with("Updated summary.", overwrite=True)

    def test_map_save_blocked_when_passport_exists_and_not_overwrite(self) -> None:
        svc = _mock_service()
        svc.save_map.return_value = {"saved": False, "existing_summary": "Old passport summary."}
        result = handle_tools_call("vectr_map_save", {"summary": "New summary."}, svc)
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "already exists" in text.lower()
        assert "Old passport summary." in text
        assert "overwrite" in text.lower()

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

    def test_locate_accepts_symbol_alias(self) -> None:
        # F40-class param ergonomics: an LLM guessing "symbol" from a
        # positional-looking tool-description example must still succeed.
        svc = _mock_service()
        result = handle_tools_call("vectr_locate", {"symbol": "verify_token"}, svc)
        assert result["isError"] is False
        svc.locate_with_snippets.assert_called_once_with("verify_token", limit=10, caller_file=None)

    def test_locate_accepts_symbol_name_alias(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_locate", {"symbol_name": "verify_token"}, svc)
        assert result["isError"] is False
        svc.locate_with_snippets.assert_called_once_with("verify_token", limit=10, caller_file=None)

    def test_locate_name_wins_over_alias_when_both_given(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_locate", {"name": "real", "symbol": "ignored"}, svc)
        svc.locate_with_snippets.assert_called_once_with("real", limit=10, caller_file=None)

    def test_locate_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.auto_eviction_hint.return_value = "Drop these: auth.py"  # UPG-7.1 gated path
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
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="both", limit=20, include_builtins=False)

    def test_trace_direction_passed_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch", "direction": "callers"}, svc)
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="callers", limit=20, include_builtins=False)

    def test_trace_invalid_direction_defaults_to_both(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch", "direction": "invalid_value"}, svc)
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="both", limit=20, include_builtins=False)

    def test_trace_include_builtins_passed_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_trace", {"name": "dispatch", "include_builtins": True}, svc)
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="both", limit=20, include_builtins=True)

    def test_trace_missing_name_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {}, svc)
        assert result["isError"] is True
        assert "name is required" in result["content"][0]["text"]

    def test_trace_accepts_symbol_alias(self) -> None:
        # F40-class param ergonomics reproduced on vectr_trace: "symbol"
        # (guessed from a positional-looking tool-description example) must
        # succeed instead of erroring "name is required".
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {"symbol": "dispatch"}, svc)
        assert result["isError"] is False
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="both", limit=20, include_builtins=False)

    def test_trace_accepts_symbol_name_alias(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {"symbol_name": "dispatch"}, svc)
        assert result["isError"] is False
        svc.trace_with_snippets.assert_called_once_with(
            "dispatch", direction="both", limit=20, include_builtins=False)

    def test_trace_returns_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert result["isError"] is False
        assert result["content"][0]["text"] == "No trace."

    def test_trace_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.auto_eviction_hint.return_value = "Drop these: bidder.py"  # UPG-7.1 gated path
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert "Drop these: bidder.py" in result["content"][0]["text"]

    def test_trace_no_eviction_hint_when_below_threshold(self) -> None:
        svc = _mock_service()
        svc.should_evict.return_value = False
        result = handle_tools_call("vectr_trace", {"name": "dispatch"}, svc)
        assert "Context management hint" not in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# vectr_fetch (UPG-CTX-EVICT)
# ---------------------------------------------------------------------------

class TestVectrFetch:
    def test_fetch_missing_ids_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_fetch", {}, svc)
        assert result["isError"] is True
        assert "ids is required" in result["content"][0]["text"]

    def test_fetch_empty_ids_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_fetch", {"ids": []}, svc)
        assert result["isError"] is True

    def test_fetch_calls_service_with_ids_in_order(self) -> None:
        svc = _mock_service()
        svc.fetch.return_value = [
            {"id": "a.py:1-5", "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 5, "symbol_name": "foo", "language": "python", "content": "def foo(): pass"},
        ]
        handle_tools_call("vectr_fetch", {"ids": ["a.py:1-5"]}, svc)
        svc.fetch.assert_called_once_with(["a.py:1-5"])

    def test_fetch_renders_found_chunk_with_id_and_content(self) -> None:
        svc = _mock_service()
        svc.fetch.return_value = [
            {"id": "a.py:1-5", "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 5, "symbol_name": "foo", "language": "python", "content": "def foo(): pass"},
        ]
        result = handle_tools_call("vectr_fetch", {"ids": ["a.py:1-5"]}, svc)
        text = result["content"][0]["text"]
        assert result["isError"] is False
        assert "a.py:1-5" in text
        assert "def foo(): pass" in text

    def test_fetch_renders_missing_id_as_not_found(self) -> None:
        svc = _mock_service()
        svc.fetch.return_value = [{"id": "gone.py:1-5", "found": False}]
        result = handle_tools_call("vectr_fetch", {"ids": ["gone.py:1-5"]}, svc)
        text = result["content"][0]["text"]
        assert "gone.py:1-5" in text
        assert "not found" in text

    def test_fetch_ids_echoed_in_request_order(self) -> None:
        svc = _mock_service()
        svc.fetch.return_value = [
            {"id": "b.py:1-2", "found": True, "file_path": "b.py", "start_line": 1,
             "end_line": 2, "symbol_name": "", "language": "python", "content": "b content"},
            {"id": "a.py:1-2", "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 2, "symbol_name": "", "language": "python", "content": "a content"},
        ]
        result = handle_tools_call("vectr_fetch", {"ids": ["b.py:1-2", "a.py:1-2"]}, svc)
        text = result["content"][0]["text"]
        assert text.index("b.py:1-2") < text.index("a.py:1-2")

    def test_fetch_exceeding_cap_returns_error(self) -> None:
        svc = _mock_service()
        svc.fetch.side_effect = ValueError("Too many ids requested")
        result = handle_tools_call("vectr_fetch", {"ids": ["x"] * 100}, svc)
        assert result["isError"] is True
        assert "Too many ids" in result["content"][0]["text"]

    def test_fetch_memory_only_returns_memory_only_message(self) -> None:
        from app.service import _MEMORY_ONLY_MSG
        svc = _mock_service()
        svc.memory_only = True
        result = handle_tools_call("vectr_fetch", {"ids": ["a.py:1-5"]}, svc)
        assert result["isError"] is False
        assert _MEMORY_ONLY_MSG in result["content"][0]["text"]
        svc.fetch.assert_not_called()

    def test_fetch_of_storage_capped_symbol_carries_truncation_warning(self) -> None:
        """UPG-FETCH-TRUNCATION-SILENT: a symbol chunk capped at index time
        (stored content shorter than the symbol's own recorded line span)
        must render the SAME truncation warning + Read() pointer that
        vectr_search already applies — a re-fetch of a large chunk must never
        silently look complete."""
        svc = _mock_service()
        # 375-line class; only 45 lines survived the storage cap.
        stored_content = "\n".join(f"    line {i}" for i in range(45))
        svc.fetch.return_value = [
            {"id": "eviction_advisor.py:55-429", "found": True,
             "file_path": "eviction_advisor.py", "start_line": 55, "end_line": 429,
             "symbol_name": "EvictionAdvisor", "language": "python",
             "content": stored_content},
        ]
        result = handle_tools_call("vectr_fetch", {"ids": ["eviction_advisor.py:55-429"]}, svc)
        text = result["content"][0]["text"]
        assert "more lines (content capped at ~2000 chars)" in text, (
            f"missing truncation warning. Got: {text[-300:]}"
        )
        assert "Read(" in text and "offset=54" in text and "limit=375" in text, (
            f"missing Read() fallback pointer. Got: {text[-300:]}"
        )

    def test_fetch_of_complete_small_chunk_carries_no_truncation_warning(self) -> None:
        """A chunk whose stored content fully covers its symbol's line span
        must render with no truncation warning at all."""
        svc = _mock_service()
        svc.fetch.return_value = [
            {"id": "a.py:1-5", "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 5, "symbol_name": "foo", "language": "python",
             "content": "def foo():\n    pass"},
        ]
        result = handle_tools_call("vectr_fetch", {"ids": ["a.py:1-5"]}, svc)
        text = result["content"][0]["text"]
        assert "content capped" not in text
        assert "Read(" not in text


class TestRelativePathRender:
    """UPG-RELATIVE-PATH-RENDER: MCP text output renders workspace-relative
    paths and prints the absolute root once per response header, instead of
    repeating the ~21-token absolute prefix on every result/hint/id line."""

    def _service_with_root(self, root: str):
        from agent.searcher import SearchResult
        svc = _mock_service()
        svc._workspace_root = root
        result = SearchResult(
            file_path=f"{root}/pkg/auth.py", lines="1-10", symbol_name="verify_token",
            language="python", score=0.9, content="def verify_token(): ...",
        )
        svc.search.return_value = ([result], 10)
        return svc

    def test_search_prints_workspace_header_once_and_relative_ids(self) -> None:
        svc = self._service_with_root("/ws/root")
        result = handle_tools_call("vectr_search", {"query": "auth"}, svc)
        text = result["content"][0]["text"]
        assert "workspace: /ws/root" in text
        assert "workspace: /ws/root" == [ln for ln in text.splitlines() if ln.startswith("workspace:")][0]
        # relative chunk id present; the absolute prefix does NOT ride the line
        assert "pkg/auth.py:1-10" in text
        assert "/ws/root/pkg/auth.py:1-10" not in text

    def test_search_symbol_graph_hint_renders_relative(self) -> None:
        from types import SimpleNamespace
        svc = self._service_with_root("/ws/root")
        svc.identifier_hint_symbols.return_value = [
            SimpleNamespace(kind="class", name="Widget", file_path="/ws/root/ui/widget.py", start_line=3),
        ]
        result = handle_tools_call("vectr_search", {"query": "Widget"}, svc)
        text = result["content"][0]["text"]
        assert "ui/widget.py:3" in text
        assert "/ws/root/ui/widget.py:3" not in text

    def test_fetch_renders_relative_id_from_absolute_path(self) -> None:
        svc = self._service_with_root("/ws/root")
        svc.fetch.return_value = [
            {"id": "/ws/root/a.py:1-5", "found": True, "file_path": "/ws/root/a.py",
             "start_line": 1, "end_line": 5, "symbol_name": "foo", "language": "python",
             "content": "def foo(): pass"},
        ]
        result = handle_tools_call("vectr_fetch", {"ids": ["a.py:1-5"]}, svc)
        text = result["content"][0]["text"]
        assert "workspace: /ws/root" in text
        assert "[a.py:1-5]" in text
        assert "/ws/root/a.py:1-5" not in text


# ---------------------------------------------------------------------------
# vectr_remember
# ---------------------------------------------------------------------------

# Default TRIGGER-ENGINE wave 1 params (bm2-design-skeleton.md §1/§2/§5) that
# every vectr_remember dispatch appends when the caller omits them — kept as
# one constant so existing assert_called_once_with() calls below stay
# readable as the additive surface grows. scope=None means "omitted" —
# UPG-TRIGGER-SCOPE-KIND-DEFAULTS resolves the kind's default scope at write
# time, downstream of this dispatch layer (see test_working_context_store.py
# for the write-time resolution itself).
_DEFAULT_TRIGGER_PARAMS = dict(
    triggers=None, provenance="agent", scope=None, anchors=None, supersedes=None, session_id=None,
)


class TestVectrRemember:
    def test_remember_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "Found auth bug"}, svc)
        svc.remember.assert_called_once_with(
            content="Found auth bug", tags=None, priority="medium", kind="finding", title="", agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_returns_note_id(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {"content": "Found auth bug"}, svc)
        assert result["isError"] is False
        assert "42" in result["content"][0]["text"]

    def test_remember_confirmation_surfaces_resolved_scope(self) -> None:
        """UPG-SCOPE-SURFACE-BACK: the confirmation names the RESOLVED scope
        (get_note()'s return, mocked as scope="workspace" — see
        _mock_service) rather than leaving scope resolution write-only."""
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {"content": "Found auth bug"}, svc)
        assert result["isError"] is False
        svc.get_note.assert_called_once_with(42)
        assert "scope=workspace" in result["content"][0]["text"]

    def test_remember_confirmation_echoes_title_and_first_line(self) -> None:
        """UPG-ADOPTION-V2-MINOR (b): the confirmation echoes the stored title +
        first content line so the caller can verify the write without a recall
        round-trip."""
        from agent.working_context_store import WorkingNote
        svc = _mock_service()
        svc.get_note.return_value = WorkingNote(
            note_id=42, workspace="/repo",
            content="lock_workspace() at resolver.rs:214 acquires a PID lock\nmore detail",
            tags=[], priority="medium", created_at=0.0, last_accessed=0.0,
            kind="finding", scope="workspace", title="workspace lock",
        )
        result = handle_tools_call("vectr_remember", {"content": "x"}, svc)
        text = result["content"][0]["text"]
        assert "title: workspace lock" in text
        assert "first line: lock_workspace() at resolver.rs:214 acquires a PID lock" in text

    def test_remember_confirmation_echo_bounds_long_first_line(self) -> None:
        from agent.working_context_store import WorkingNote
        svc = _mock_service()
        svc.get_note.return_value = WorkingNote(
            note_id=42, workspace="/repo", content="A" * 500, tags=[],
            priority="medium", created_at=0.0, last_accessed=0.0,
            kind="finding", scope="workspace", title="",
        )
        result = handle_tools_call("vectr_remember", {"content": "x"}, svc)
        text = result["content"][0]["text"]
        assert "first line: " + "A" * 117 + "..." in text

    def test_remember_confirmation_echo_dedupes_when_title_equals_first_line(self) -> None:
        """When no explicit title is given, the title is derived from the first
        content line, so title == first_line; the echo must not print the same
        text twice as `title: X · first line: X`."""
        from agent.working_context_store import WorkingNote
        svc = _mock_service()
        svc.get_note.return_value = WorkingNote(
            note_id=42, workspace="/repo",
            content="Factory.createProducer at Factory.java:2 returns a Producer\nmore",
            tags=[], priority="medium", created_at=0.0, last_accessed=0.0,
            kind="finding", scope="workspace",
            title="Factory.createProducer at Factory.java:2 returns a Producer",
        )
        result = handle_tools_call("vectr_remember", {"content": "x"}, svc)
        text = result["content"][0]["text"]
        assert "title: Factory.createProducer at Factory.java:2 returns a Producer" in text
        assert "first line:" not in text

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
            kind="finding",
            title="",
            agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_missing_content_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {}, svc)
        assert result["isError"] is True

    def test_remember_invalid_priority_clamps_to_medium(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "note", "priority": "urgent"}, svc)
        svc.remember.assert_called_once_with(
            content="note", tags=None, priority="medium", kind="finding", title="", agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_passes_kind_through(self) -> None:
        """UPG-9.3: an explicit kind reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "never push to main", "kind": "directive"}, svc)
        svc.remember.assert_called_once_with(
            content="never push to main", tags=None,
            priority="medium", kind="directive", title="", agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_passes_title_through(self) -> None:
        """UPG-RECALL-HIERARCHY: explicit title reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_remember", {
            "content": "def acquire_lock(): ...", "title": "workspace lock acquisition",
        }, svc)
        svc.remember.assert_called_once_with(
            content="def acquire_lock(): ...", tags=None, priority="medium",
            kind="finding", title="workspace lock acquisition", agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_passes_agent_through(self) -> None:
        """UPG-SUBAGENT-MEMORY: an explicit agent identifier reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_remember", {
            "content": "found the bug in the parser", "agent": "coder-2",
        }, svc)
        svc.remember.assert_called_once_with(
            content="found the bug in the parser", tags=None, priority="medium",
            kind="finding", title="", agent="coder-2",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    # -- TRIGGER-ENGINE wave 1 (bm2-design-skeleton.md §1/§2/§5) --------------

    def test_remember_passes_triggers_provenance_scope_anchors_through(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {
            "content": "a gotcha about auth.py",
            "kind": "gotcha",
            "triggers": [{"path": "src/auth.py", "event": "pre-edit"}],
            "provenance": "auto",
            "scope": "repo",
            "anchors": ["src/auth.py"],
        }, svc)
        svc.remember.assert_called_once_with(
            content="a gotcha about auth.py", tags=None, priority="medium",
            kind="gotcha", title="", agent="",
            triggers=[{"path": "src/auth.py", "event": "pre-edit"}],
            provenance="auto", scope="repo", anchors=["src/auth.py"], supersedes=None, session_id=None,
        )

    def test_remember_passes_supersedes_as_int(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_remember", {"content": "corrected finding", "supersedes": "7"}, svc)
        svc.remember.assert_called_once_with(
            content="corrected finding", tags=None, priority="medium",
            kind="finding", title="", agent="",
            triggers=None, provenance="agent", scope=None, anchors=None, supersedes=7, session_id=None,
        )

    def test_remember_non_integer_supersedes_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {"content": "note", "supersedes": "not-a-number"}, svc)
        assert result["isError"] is True
        svc.remember.assert_not_called()

    def test_remember_value_error_from_service_returns_mcp_error(self) -> None:
        """A malformed trigger, bad provenance/scope combo, or a supersedes
        target that doesn't exist all surface as `isError: True`, not a
        raised exception reaching the dispatch caller."""
        svc = _mock_service()
        svc.remember.side_effect = ValueError("provenance='auto' is not allowed on kind='directive'")
        result = handle_tools_call("vectr_remember", {
            "content": "an unreviewed standing rule", "kind": "directive", "provenance": "auto",
        }, svc)
        assert result["isError"] is True
        assert "provenance" in result["content"][0]["text"]

    def test_remember_provenance_human_rejected_at_mcp_layer(self) -> None:
        """The MCP tool is the AGENT's own surface (bm2-design-skeleton.md §5:
        "promotion is an explicit user act") -- same boundary
        test_promote_to_human_rejected_at_mcp_layer enforces for vectr_promote.
        An agent minting a note straight to provenance='human' here would be
        indistinguishable from a genuine user-reviewed directive (the unhedged
        imperative framing, auto-injected every session start) -- a one-call
        trust forgery. The store is never even called; nothing is stored."""
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {
            "content": "a standing rule", "kind": "directive", "provenance": "human",
        }, svc)
        assert result["isError"] is True
        text = result["content"][0]["text"].lower()
        assert "human" in text
        assert "user-side" in text or "person" in text
        svc.remember.assert_not_called()

    def test_remember_provenance_agent_still_works(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {
            "content": "a note", "provenance": "agent",
        }, svc)
        assert result["isError"] is False
        svc.remember.assert_called_once()

    def test_remember_provenance_auto_still_works(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {
            "content": "a note", "provenance": "auto",
        }, svc)
        assert result["isError"] is False
        svc.remember.assert_called_once()

    def test_remember_provenance_omitted_defaults_to_agent(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_remember", {"content": "a note"}, svc)
        assert result["isError"] is False
        svc.remember.assert_called_once_with(
            content="a note", tags=None, priority="medium",
            kind="finding", title="", agent="",
            **_DEFAULT_TRIGGER_PARAMS,
        )

    def test_remember_supersedes_human_note_rejected_by_store_surfaces_as_mcp_error(self) -> None:
        """Round-trip: the store-side write-boundary guard (an agent/auto
        write may not supersede a human-provenance note) surfaces through the
        MCP dispatch's existing ValueError-to-_mcp_error path, same as any
        other remember() validation error."""
        svc = _mock_service()
        svc.remember.side_effect = ValueError(
            "supersedes references note #7, which is provenance='human' -- "
            "a write whose own provenance is not 'human' may not supersede "
            "a human-provenance note"
        )
        result = handle_tools_call("vectr_remember", {
            "content": "an agent note", "supersedes": 7, "provenance": "agent",
        }, svc)
        assert result["isError"] is True
        assert "human" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# UPG-SCOPE-SURFACE-BACK — resolved scope end to end, through a REAL store
# ---------------------------------------------------------------------------

class TestVectrRememberResolvedScopeEndToEnd:
    """kind="task" defaults to scope="branch" (UPG-TRIGGER-SCOPE-KIND-DEFAULTS),
    resolved by the REAL store at write time — a real git branch in a git
    workspace, falling back to scope="workspace" in a non-git one (the
    silent-death guard: baking scope="branch" with no branch value would
    exclude the note from firing on every future branch, forever). Exercises
    the REAL VectrService + WorkingContextStore (not the MagicMock service
    used elsewhere in this file) so the scope shown in the MCP confirmation
    is the store's actual write-time decision, not a stubbed one."""

    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))
        return svc

    def _init_git_repo(self, tmp_path, branch: str = "main") -> None:
        import subprocess
        run = lambda *args: subprocess.run(args, cwd=str(tmp_path), check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        run("git", "config", "user.email", "test@test.com")
        run("git", "config", "user.name", "test")
        run("git", "commit", "--allow-empty", "-q", "-m", "init")

    def test_task_note_shows_resolved_branch_scope_in_git_workspace(self, tmp_path, monkeypatch) -> None:
        self._init_git_repo(tmp_path, branch="main")
        svc = self._make_service(tmp_path, monkeypatch)
        result = handle_tools_call(
            "vectr_remember", {"content": "current work on segment targeting", "kind": "task"}, svc,
        )
        assert result["isError"] is False
        assert "scope=branch (main)" in result["content"][0]["text"]

    def test_task_note_falls_back_to_workspace_scope_in_non_git_workspace(self, tmp_path, monkeypatch) -> None:
        # No git init here — a plain directory, no git work tree at all.
        svc = self._make_service(tmp_path, monkeypatch)
        result = handle_tools_call(
            "vectr_remember", {"content": "current work, no git here", "kind": "task"}, svc,
        )
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "scope=workspace" in text
        assert "scope=branch" not in text


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
    # UPG-TEST-SIGNATURE-DRIFT: these seam assertions name only the kwarg(s) the
    # test is actually about and validate them against the real
    # VectrService.recall signature (tests/_seam.assert_seam_call), rather than
    # re-listing every default kwarg. A new param added to the recall seam no
    # longer reddens all six at once; a renamed/removed param still fails
    # precisely, naming the stale key.
    def test_recall_calls_service(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_recall", {}, svc)
        assert svc.recall.call_count == 1
        assert_seam_call(
            svc.recall, VectrService.recall,
            query=None, limit=10, boot=False, detail="index", sort_by="relevance", note_id=None,
        )

    def test_recall_with_filters(self) -> None:
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"query": "auth", "tags": ["wip"], "priority": "high", "limit": 5}, svc)
        assert_seam_call(
            svc.recall, VectrService.recall,
            query="auth", tags=["wip"], priority="high", limit=5,
        )

    def test_recall_passes_kind_filter(self) -> None:
        """UPG-9.3: a kind filter reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"kind": "directive"}, svc)
        assert_seam_call(svc.recall, VectrService.recall, kind="directive")

    def test_recall_passes_detail_full(self) -> None:
        """UPG-RECALL-HIERARCHY: detail='full' reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"detail": "full"}, svc)
        assert_seam_call(svc.recall, VectrService.recall, detail="full")

    def test_recall_passes_note_id(self) -> None:
        """UPG-RECALL-HIERARCHY: note_id expand path reaches the service."""
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"note_id": 7}, svc)
        assert_seam_call(svc.recall, VectrService.recall, note_id=7)

    def test_recall_passes_sort_by_and_max_age(self) -> None:
        """UPG-RECALL-HIERARCHY: sort_by and max_age_days reach the service."""
        svc = _mock_service()
        handle_tools_call("vectr_recall", {"sort_by": "recency", "max_age_days": 7.0}, svc)
        assert_seam_call(svc.recall, VectrService.recall, sort_by="recency", max_age_days=7.0)

    def test_recall_returns_notes_text(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_recall", {}, svc)
        assert result["isError"] is False
        assert "Notes" in result["content"][0]["text"]

    def test_recall_appends_eviction_hint_when_should_evict(self) -> None:
        svc = _mock_service()
        svc.auto_eviction_hint.return_value = "Drop these: segment.py"  # UPG-7.1 gated path
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
    def test_forget_note_id_deletes_single_note(self) -> None:
        svc = _mock_service()
        svc.forget_note.return_value = True
        result = handle_tools_call("vectr_forget", {"note_id": 12}, svc)
        svc.forget_note.assert_called_once_with(12)
        svc.forget_all.assert_not_called()
        assert result["isError"] is False
        assert "#12" in result["content"][0]["text"]

    def test_forget_note_id_not_found(self) -> None:
        svc = _mock_service()
        svc.forget_note.return_value = False
        result = handle_tools_call("vectr_forget", {"note_id": 999}, svc)
        assert result["isError"] is False
        assert "not found" in result["content"][0]["text"]
        svc.forget_all.assert_not_called()

    def test_forget_note_id_non_integer_is_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_forget", {"note_id": "abc"}, svc)
        assert result["isError"] is True
        svc.forget_note.assert_not_called()
        svc.forget_all.assert_not_called()

    def test_forget_all_true_clears_workspace(self) -> None:
        svc = _mock_service()
        svc.forget_all.return_value = 5
        result = handle_tools_call("vectr_forget", {"all": True}, svc)
        svc.forget_all.assert_called_once()
        assert result["isError"] is False
        assert "5" in result["content"][0]["text"]

    def test_forget_no_arguments_deletes_nothing(self) -> None:
        # Data-loss regression (2026-07-02): the handler used to call forget_all()
        # unconditionally, wiping every note on ANY vectr_forget call.
        svc = _mock_service()
        result = handle_tools_call("vectr_forget", {}, svc)
        assert result["isError"] is True
        svc.forget_all.assert_not_called()
        svc.forget_note.assert_not_called()

    def test_forget_all_false_with_no_note_id_deletes_nothing(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_forget", {"all": False}, svc)
        assert result["isError"] is True
        svc.forget_all.assert_not_called()

    def test_forget_in_tools_list(self) -> None:
        names = {t["name"] for t in handle_tools_list()["tools"]}
        assert "vectr_forget" in names

    def test_all_tools_env_flag_exposes_full_surface(self, monkeypatch) -> None:
        # Hosted/registry inspectors connect with a fresh session and zero
        # notes; VECTR_MCP_ALL_TOOLS=1 must expose the complete tool surface
        # (memory read tools included) without any note existing.
        monkeypatch.setenv("VECTR_MCP_ALL_TOOLS", "1")
        tools = handle_tools_list(session_id="fresh-session-no-notes")["tools"]
        names = {t["name"] for t in tools}
        assert len(tools) == 15
        assert {"vectr_recall", "vectr_forget", "vectr_promote", "vectr_snapshot", "vectr_snapshot_list"} <= names

    def test_all_tools_env_flag_off_keeps_gating(self, monkeypatch) -> None:
        monkeypatch.delenv("VECTR_MCP_ALL_TOOLS", raising=False)
        tools = handle_tools_list(session_id="fresh-session-no-notes")["tools"]
        names = {t["name"] for t in tools}
        assert "vectr_recall" not in names


# ---------------------------------------------------------------------------
# vectr_promote (TRIGGER-ENGINE wave 1, bm2-design-skeleton.md §5)
# ---------------------------------------------------------------------------

class TestVectrPromote:
    def test_promote_calls_service(self) -> None:
        svc = _mock_service()
        svc.promote_note.return_value = True
        result = handle_tools_call("vectr_promote", {"note_id": 12, "to": "agent"}, svc)
        svc.promote_note.assert_called_once_with(12, "agent")
        assert result["isError"] is False
        assert "#12" in result["content"][0]["text"]
        assert "agent" in result["content"][0]["text"]

    def test_promote_to_human_rejected_at_mcp_layer(self) -> None:
        """The MCP tool is the AGENT's own surface (bm2-design-skeleton.md §5:
        "promotion is an explicit user act"). An agent must never raise its own
        note straight to provenance='human' -- that would let it decide, on its
        own, that a person endorsed something, reopening the trust-inversion
        hole §5 closes structurally. Only auto -> agent is reachable here; the
        store is never even called. (Full auto -> agent -> human semantics
        remain available on the user-side REST /v1/promote route.)"""
        svc = _mock_service()
        result = handle_tools_call("vectr_promote", {"note_id": 12, "to": "human"}, svc)
        assert result["isError"] is True
        text = result["content"][0]["text"].lower()
        assert "human" in text
        assert "user-side" in text or "person" in text
        svc.promote_note.assert_not_called()

    def test_promote_missing_note_id_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_promote", {"to": "agent"}, svc)
        assert result["isError"] is True
        svc.promote_note.assert_not_called()

    def test_promote_missing_to_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_promote", {"note_id": 12}, svc)
        assert result["isError"] is True
        svc.promote_note.assert_not_called()

    def test_promote_non_integer_note_id_returns_error(self) -> None:
        svc = _mock_service()
        result = handle_tools_call("vectr_promote", {"note_id": "abc", "to": "agent"}, svc)
        assert result["isError"] is True
        svc.promote_note.assert_not_called()

    def test_promote_not_found_returns_error(self) -> None:
        svc = _mock_service()
        svc.promote_note.return_value = False
        result = handle_tools_call("vectr_promote", {"note_id": 999, "to": "agent"}, svc)
        assert result["isError"] is True
        assert "not found" in result["content"][0]["text"].lower()

    def test_promote_value_error_from_service_returns_mcp_error(self) -> None:
        """An invalid single-step promotion (e.g. a note already at 'agent' or
        'human' targeted with 'agent' again) surfaces as isError: True, never
        an unhandled exception. Uses to='agent' since 'human' is rejected
        before the store is ever called (see test_promote_to_human_rejected_at_mcp_layer)."""
        svc = _mock_service()
        svc.promote_note.side_effect = ValueError("promote() only allows a single step up")
        result = handle_tools_call("vectr_promote", {"note_id": 12, "to": "agent"}, svc)
        assert result["isError"] is True
        assert "single step" in result["content"][0]["text"]

    def test_promote_in_tools_list(self) -> None:
        names = {t["name"] for t in handle_tools_list()["tools"]}
        assert "vectr_promote" in names

    def test_promote_gated_with_other_memory_read_tools(self) -> None:
        """vectr_promote is a pure SQLite write needing no embedder/indexer —
        same gating group as vectr_forget/vectr_recall (notes-exist gate),
        not the always-visible write-side group vectr_remember lives in."""
        assert "vectr_promote" in {t["name"] for t in _MEMORY_TOOLS}
        write_names = {t["name"] for t in _MEMORY_WRITE_TOOLS}
        assert "vectr_promote" not in write_names


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

    def test_score_source_rendered(self) -> None:
        # UPG-MCP-SCORE-SOURCE-RENDER: the displayed score's scale (reranker vs
        # dense) must be visible so the caller can read the number correctly.
        from agent.searcher import SearchResult
        rr = SearchResult(
            file_path="auth.py", lines="10-20", symbol_name="login",
            language="python", score=0.88, content="def login(): pass",
            score_source="reranker",
        )
        text = _format_search_results([rr], "login", 7, 50)
        assert "0.880 (reranker)" in text

        dn = SearchResult(
            file_path="auth.py", lines="10-20", symbol_name="login",
            language="python", score=0.42, content="def login(): pass",
            score_source="dense",
        )
        text = _format_search_results([dn], "login", 7, 50)
        assert "0.420 (dense)" in text

    def test_score_order_explain_annotates_large_divergence(self) -> None:
        # UPG-SCORE-ORDER-EXPLAIN (B6): a below-rank-1 result whose displayed
        # relevance clears the divergence margin (>=1.5x rank 1) is annotated
        # with the demoting prior's reason.
        from agent.searcher import SearchResult, SearchResultList
        rank1 = SearchResult(file_path="core.py", lines="1-40", symbol_name="select",
                             language="python", score=0.30, content="def select(): ...",
                             score_source="reranker")
        rank2 = SearchResult(file_path="lib.rs", lines="1-10", symbol_name="Layout",
                             language="rust", score=0.90, content="pub use foo::Bar;",
                             score_source="reranker", quality_reason="navigational chunk")
        text = _format_search_results(SearchResultList([rank1, rank2]), "q", 5, 100)
        assert "(ranked lower: navigational chunk)" in text

    def test_score_order_explain_skips_mild_inversion(self) -> None:
        from agent.searcher import SearchResult, SearchResultList
        rank1 = SearchResult(file_path="core.py", lines="1-40", symbol_name="a",
                             language="python", score=0.80, content="x",
                             score_source="reranker")
        rank2 = SearchResult(file_path="b.py", lines="1-10", symbol_name="b",
                             language="python", score=0.90, content="y",
                             score_source="reranker", quality_reason="test-file demotion")
        text = _format_search_results(SearchResultList([rank1, rank2]), "q", 5, 100)
        assert "ranked lower" not in text  # 0.90 < 1.5*0.80

    def test_low_confidence_renders_pointer_mode(self) -> None:
        # UPG-LOWCONF-OUTPUT-SLIM / UPG-FLOOR-SLIM-PAYLOAD (B4): the low-confidence
        # result set ships pointers, not full bodies.
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(file_path="auth.py", lines="10-20", symbol_name="login",
                         language="python", score=0.01, content="SECRET_BODY_TOKEN in here",
                         score_source="reranker")
        rl = SearchResultList([r])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "auth.py" in text          # pointer present
        assert "login" in text
        assert "SECRET_BODY_TOKEN" not in text   # body omitted
        assert "pointers only" in text

    def test_full_mode_still_shows_body(self) -> None:
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(file_path="auth.py", lines="10-20", symbol_name="login",
                         language="python", score=0.88, content="BODY_TOKEN here",
                         score_source="reranker")
        rl = SearchResultList([r])  # low_confidence defaults False
        text = _format_search_results(rl, "login", 5, 100)
        assert "BODY_TOKEN" in text

    # UPG-POINTER-MODE-UNIFORM-STRIP: a result whose own ce_relevance clears
    # the retention floor keeps a body/excerpt even when the SET is flagged
    # low confidence.
    def test_individually_strong_result_keeps_excerpt_in_low_confidence_set(self) -> None:
        from agent.searcher import SearchResult, SearchResultList
        strong = SearchResult(
            file_path="payments.py", lines="10-20", symbol_name="charge_card",
            language="python", score=0.531, content="STRONG_BODY_TOKEN here",
            score_source="reranker", ce_relevance=0.531,
        )
        weak = SearchResult(
            file_path="auth.py", lines="30-40", symbol_name="login",
            language="python", score=0.05, content="WEAK_BODY_TOKEN here",
            score_source="reranker", ce_relevance=0.05,
        )
        rl = SearchResultList([weak, strong])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "STRONG_BODY_TOKEN" in text, "an individually-strong result must keep its body"
        assert "WEAK_BODY_TOKEN" not in text, "a genuinely weak result stays pointer-only"
        assert "individually strong" in text, "the retained result must be labeled"
        assert "0.531" in text

    def test_all_weak_low_confidence_set_still_pointer_only(self) -> None:
        """No result clears the retention floor — every result stays a bare
        pointer, identical to pre-fix behaviour."""
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(
            file_path="auth.py", lines="10-20", symbol_name="login",
            language="python", score=0.05, content="SECRET_BODY_TOKEN in here",
            score_source="reranker", ce_relevance=0.05,
        )
        rl = SearchResultList([r])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "SECRET_BODY_TOKEN" not in text
        assert "individually strong" not in text

    def test_no_reranker_low_confidence_set_still_pointer_only(self) -> None:
        """Zero-DF-triggered low_confidence with no reranker run (ce_relevance
        None on every result) must not retain any body — there is no
        calibrated score to clear the floor with."""
        from agent.searcher import SearchResult, SearchResultList
        r = SearchResult(
            file_path="auth.py", lines="10-20", symbol_name="login",
            language="python", score=0.9, content="DENSE_ONLY_BODY_TOKEN",
        )  # ce_relevance defaults to None (no reranker)
        rl = SearchResultList([r])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "DENSE_ONLY_BODY_TOKEN" not in text

    def test_retention_floor_boundary(self) -> None:
        """ce_relevance exactly at the configured floor retains; just below
        does not (matches ranking.pointer_mode_retain.min_relevance = 0.30)."""
        from agent.config import POINTER_MODE_RETAIN_MIN_RELEVANCE
        from agent.searcher import SearchResult, SearchResultList

        at_floor = SearchResult(
            file_path="a.py", lines="1-5", symbol_name="a",
            language="python", score=POINTER_MODE_RETAIN_MIN_RELEVANCE,
            content="AT_FLOOR_BODY", score_source="reranker",
            ce_relevance=POINTER_MODE_RETAIN_MIN_RELEVANCE,
        )
        rl = SearchResultList([at_floor])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        assert "AT_FLOOR_BODY" in text, "a result exactly at the floor must retain its body"

        below_floor = SearchResult(
            file_path="b.py", lines="1-5", symbol_name="b",
            language="python", score=POINTER_MODE_RETAIN_MIN_RELEVANCE - 0.01,
            content="BELOW_FLOOR_BODY", score_source="reranker",
            ce_relevance=POINTER_MODE_RETAIN_MIN_RELEVANCE - 0.01,
        )
        rl2 = SearchResultList([below_floor])
        rl2.low_confidence = True
        text2 = _format_search_results(rl2, "unrelated", 5, 100)
        assert "BELOW_FLOOR_BODY" not in text2, "a result just below the floor must stay pointer-only"

    def test_retained_excerpt_bounded_and_refetchable(self) -> None:
        """A retained excerpt is bounded (not the full body verbatim beyond
        the configured excerpt length) and the vectr_fetch footer reappears
        because real content was shown."""
        from agent.config import POINTER_MODE_RETAIN_EXCERPT_LINES
        from agent.searcher import SearchResult, SearchResultList
        long_content = "\n".join(f"line {i}" for i in range(POINTER_MODE_RETAIN_EXCERPT_LINES + 40))
        r = SearchResult(
            file_path="big.py", lines="1-999", symbol_name="big_fn",
            language="python", score=0.9, content=long_content,
            score_source="reranker", ce_relevance=0.9,
        )
        rl = SearchResultList([r])
        rl.low_confidence = True
        text = _format_search_results(rl, "unrelated", 5, 100)
        shown_lines = [l for l in text.splitlines() if l.startswith("line ")]
        assert len(shown_lines) == POINTER_MODE_RETAIN_EXCERPT_LINES, (
            f"excerpt must be bounded to {POINTER_MODE_RETAIN_EXCERPT_LINES} lines, got {len(shown_lines)}"
        )
        assert "more lines" in text
        assert "vectr_fetch(ids=" in text, "footer must reappear once a body/excerpt was actually shown"

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

    def test_truncation_footer_includes_fetch_pointer(self) -> None:
        """UPG-CTX-EVICT: the truncation footer points at vectr_fetch(ids=[...])
        rather than a blind Read(file, offset=N) — vectr_fetch restores the full
        chunk deterministically and keeps the caller in the vectr tool family."""
        from agent.searcher import SearchResult
        long_content = "\n".join(f"line {i}" for i in range(120))
        result = SearchResult(
            file_path="big.py", lines="1-120", symbol_name="",
            language="python", score=0.9, content=long_content,
        )
        text = _format_search_results([result], "query", 10, 100)
        assert "vectr_fetch(ids=" in text, "footer must contain a vectr_fetch pointer for full context"
        assert "big.py:1-120" in text, "footer must reference the chunk's own id"

    def test_expand_hint_fires_when_content_capped_by_2000_char_limit(self) -> None:
        """UPG-11.4-b: when content was truncated by the 2000-char storage cap
        (stored content is significantly shorter than the full symbol line range),
        the formatter must emit an expand instruction so the caller can read the
        full definition without a blind whole-file read.

        E.g. Field.deconstruct is 100 lines; content[:2000] captures ~48 lines.
        The old 80-line guard never fires (48 < 80) so the hint was never shown.
        """
        from agent.searcher import SearchResult
        # Simulate a 100-line symbol where only 45 lines survived the 2000-char cap.
        stored_content = "\n".join(f"    line {i}" for i in range(45))
        result = SearchResult(
            file_path="django/db/models/fields/__init__.py",
            lines="606-705",
            symbol_name="Field.deconstruct",
            language="python",
            score=1.2,
            content=stored_content,
            symbol_start_line=606,
            symbol_end_line=705,
        )
        text = _format_search_results([result], "Field deconstruct", 10, 41447)
        # Must include expand hint because 45 lines << 100 lines (symbol_range=100, floor=5)
        assert "more lines" in text, (
            "UPG-11.4-b: expand hint must fire when stored content is shorter than symbol range. "
            f"Got: {text[-200:]}"
        )
        # UPG-CTX-EVICT review fix: a chunk capped by the 2000-char STORAGE
        # limit is stored capped — vectr_fetch would return the same truncated
        # content, so this branch must point at a file read, not vectr_fetch.
        assert "Read(" in text and "offset=605" in text and "limit=100" in text, (
            "storage-capped expand hint must point at a file read reaching the "
            f"missing tail. Got: {text[-260:]}"
        )
        assert "capped at ~2000 chars) — vectr_fetch" not in text, (
            "vectr_fetch must NOT be advertised for storage-capped chunks — "
            "the index holds only the capped content."
        )

    def test_expand_hint_not_fired_when_content_covers_full_symbol(self) -> None:
        """UPG-11.4-b: when the stored content fully covers the symbol line range,
        no expand hint should be emitted.
        """
        from agent.searcher import SearchResult
        # 30-line symbol, 28 lines stored — well within the 5-line tolerance
        stored_content = "\n".join(f"    line {i}" for i in range(28))
        result = SearchResult(
            file_path="auth.py",
            lines="10-39",
            symbol_name="login",
            language="python",
            score=0.88,
            content=stored_content,
            symbol_start_line=10,
            symbol_end_line=39,  # 30 lines total
        )
        text = _format_search_results([result], "login", 7, 100)
        # 28 stored lines vs 30 symbol lines: difference is 2, below tolerance of 5
        assert "more lines (content capped" not in text, (
            "UPG-11.4-b: expand hint must NOT fire when content covers the full symbol range. "
            f"Got: {text[-200:]}"
        )

    def test_expand_hint_references_chunk_id(self) -> None:
        """UPG-CTX-EVICT: the expand hint must reference the chunk's own id
        (file:start-end) so vectr_fetch(ids=[...]) can restore exactly this
        chunk — not an arbitrary Read() window/limit.
        """
        from agent.searcher import SearchResult
        stored_content = "\n".join(f"    line {i}" for i in range(45))
        result = SearchResult(
            file_path="fields.py",
            lines="1-100",
            symbol_name="MyField.deconstruct",
            language="python",
            score=1.0,
            content=stored_content,
            symbol_start_line=1,
            symbol_end_line=100,  # 100-line symbol
        )
        text = _format_search_results([result], "deconstruct", 5, 100)
        assert "vectr_fetch(ids=" in text and "fields.py:1-100" in text, (
            "UPG-CTX-EVICT: expand hint must reference the chunk's own id (fields.py:1-100). "
            f"Got: {text[-300:]}"
        )


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
        # UPG-EVICT-SESSION-SCOPE: the real VectrService now routes every
        # advisor read/write through `_advisor_for(session_id)` + the
        # `record_results`/`record_chunk` methods rather than the bare
        # `_eviction_advisor` attribute. These tests call handle_tools_call
        # with no session_id (the anonymous/shared-advisor path), so wiring
        # every one of those methods to the same real advisor keeps this an
        # honest integration test of handle_tools_call's wiring.
        from agent.eviction_advisor import EvictionAdvisor
        # remember_escalation_chunks=0 / remember_escalation_tokens=0 disable the
        # UPG-REMEMBER-BANNER-FATIGUE / UPG-EVICT-ESCALATION-GATE-TOO-LOW gates
        # by default — these are integration tests of the pre-existing
        # UPG-7.1/UPG-11.15 wiring, not of the remember-fatigue gates themselves.
        advisor_kwargs.setdefault("remember_escalation_chunks", 0)
        advisor_kwargs.setdefault("remember_escalation_tokens", 0)
        svc = _mock_service()
        real_advisor = EvictionAdvisor(**advisor_kwargs)
        svc._eviction_advisor = real_advisor
        svc._advisor_for.side_effect = lambda session_id=None: real_advisor
        svc.should_evict.side_effect = lambda session_id=None: real_advisor.should_evict()
        svc.eviction_hint.side_effect = lambda session_id=None: real_advisor.eviction_hint()
        svc.auto_eviction_hint.side_effect = lambda session_id=None: real_advisor.auto_eviction_hint()
        svc.record_results.side_effect = (
            lambda results, session_id=None: real_advisor.record_results(results)
        )
        svc.record_chunk.side_effect = (
            lambda file_path, lines, symbol_name, content, chunk_id="", session_id=None:
            real_advisor.record(
                file_path=file_path, lines=lines, symbol_name=symbol_name,
                content=content, chunk_id=chunk_id,
            )
        )
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
        # retrieved_token_gate=0 disables the UPG-11.15 token-accumulation gate
        # so this test focuses solely on the call-count trigger path.
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
            retrieved_token_gate=0,
        )
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "Context management hint" in result["content"][0]["text"], (
            "eviction hint must be injected into vectr_search response when threshold is exceeded"
        )

    def test_eviction_hint_contains_action_required(self) -> None:
        # Directive language is required — passive phrasing gets ignored by the LLM.
        # retrieved_token_gate=0 disables the UPG-11.15 token-accumulation gate
        # so this test focuses solely on directive language in the injected hint.
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
            retrieved_token_gate=0,
        )
        result = handle_tools_call("vectr_search", {"query": "verify"}, svc)
        assert "ACTION REQUIRED" in result["content"][0]["text"], (
            "injected eviction hint must contain 'ACTION REQUIRED' to prompt vectr_remember"
        )

    def test_no_hint_when_search_returns_empty_results(self) -> None:
        # should_evict() may be True but empty results → no chunks → hint must not fire.
        # retrieved_token_gate=0 disables the UPG-11.15 gate so suppression is due
        # to the empty-chunk path in eviction_hint(), not the token accumulation gate.
        svc, _ = self._service_with_real_advisor(
            retrieval_call_threshold=0,
            time_threshold_seconds=100_000,
            retrieved_token_gate=0,
        )
        svc.search.return_value = ([], 5)
        result = handle_tools_call("vectr_search", {"query": "nothing"}, svc)
        assert "Context management hint" not in result["content"][0]["text"]
