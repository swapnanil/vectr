"""Tests for UPG-REMEMBER-BANNER-FATIGUE.

Covers:
  a) EvictionAdvisor.note_remembered() resets the chunks-since-remember
     counter, so auto_eviction_hint()'s escalated ACTION REQUIRED directive
     doesn't immediately re-fire on the next retrieval right after the
     caller just called vectr_remember.
  b) The escalated directive only fires again once
     EVICTION_REMEMBER_ESCALATION_CHUNKS new chunks have been retrieved
     since that reset (or since session start, if vectr_remember was never
     called).
  c) The counter is per MCP session, mirroring the per-session advisor
     registry (UPG-EVICT-SESSION-SCOPE).
  d) MCP dispatch never stacks the eviction-hint banner and the turn-count
     soft nudge (_should_nudge_remember/_remember_nudge_text) in the same
     response.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.config import EVICTION_REMEMBER_ESCALATION_CHUNKS
from agent.eviction_advisor import EvictionAdvisor

from tests.conftest import make_py, _DummyEmbedProvider, _RealVectrService


def _make_service(tmp_path, monkeypatch, num_files: int = 1):
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


def _adv(**kw) -> EvictionAdvisor:
    """Advisor with every OTHER trigger disabled/maxed so tests focus solely
    on the remember-escalation gate."""
    kw.setdefault("eviction_threshold_tokens", 100_000)
    kw.setdefault("tool_call_threshold", 1000)
    kw.setdefault("time_threshold_seconds", 100_000)
    kw.setdefault("retrieval_call_threshold", 0)
    kw.setdefault("retrieved_token_gate", 0)
    return EvictionAdvisor(**kw)


# ---------------------------------------------------------------------------
# EvictionAdvisor unit-level: counter + gate semantics
# ---------------------------------------------------------------------------

class TestChunksSinceRememberCounter:
    def test_counter_starts_at_zero(self) -> None:
        adv = EvictionAdvisor()
        assert adv._chunks_since_remember == 0

    def test_record_increments_counter(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fn", "x" * 40)
        adv.record("b.py", "1-5", "fn2", "x" * 40)
        assert adv._chunks_since_remember == 2

    def test_duplicate_record_does_not_increment(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fn", "x" * 40)
        adv.record("a.py", "1-5", "fn", "x" * 40)  # duplicate file:lines
        assert adv._chunks_since_remember == 1

    def test_note_remembered_resets_counter(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fn", "x" * 40)
        adv.record("b.py", "1-5", "fn2", "x" * 40)
        adv.note_remembered()
        assert adv._chunks_since_remember == 0

    def test_clear_session_resets_counter(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fn", "x" * 40)
        adv.clear_session()
        assert adv._chunks_since_remember == 0


class TestRememberEscalationGate:
    def test_banner_suppressed_right_after_remember(self) -> None:
        threshold = EVICTION_REMEMBER_ESCALATION_CHUNKS
        adv = _adv(remember_escalation_chunks=threshold)
        for i in range(threshold):
            adv.record(f"a{i}.py", "1-10", "fn", "x" * 400)
            adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != "", "sanity: hint fires before any remember"
        adv.note_remembered()
        adv.record("b.py", "1-10", "fn2", "x" * 400)  # one new chunk, well below threshold
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() == "", (
            "escalated directive must not immediately re-fire right after "
            "vectr_remember — only one new chunk since the reset"
        )

    def test_escalates_again_once_threshold_chunks_retrieved(self) -> None:
        threshold = 3
        # rearm_retrieval_calls=1 disables the UPG-7.1 fresh-escalation re-arm
        # gate so this test focuses solely on the remember-escalation gate.
        adv = _adv(remember_escalation_chunks=threshold, rearm_retrieval_calls=1)
        for i in range(threshold):
            adv.record(f"a{i}.py", "1-10", "fn", "x" * 400)
            adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""
        adv.note_remembered()
        for i in range(threshold - 1):
            adv.record(f"f{i}.py", "1-10", "fn", "x" * 400)
            adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() == "", "still below threshold since remember"
        adv.record("last.py", "1-10", "fn", "x" * 400)  # crosses the threshold
        adv.increment_retrieval_call()
        assert "ACTION REQUIRED" in adv.auto_eviction_hint(), (
            "threshold chunks retrieved since remember must re-escalate"
        )

    def test_never_remembered_counts_from_session_start(self) -> None:
        """If vectr_remember was never called, the gate counts chunks since
        session start — the initial should-fire path still works."""
        adv = _adv(remember_escalation_chunks=1)
        adv.record("a.py", "1-10", "fn", "x" * 400)
        adv.increment_retrieval_call()
        assert "ACTION REQUIRED" in adv.auto_eviction_hint()

    def test_gate_zero_disables_remember_escalation_gate(self) -> None:
        adv = _adv(remember_escalation_chunks=0)
        adv.record("a.py", "1-10", "fn", "x" * 400)
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""

    def test_explicit_eviction_hint_bypasses_remember_gate(self) -> None:
        """vectr_evict_hint / /v1/evict is an explicit ask — always answers,
        regardless of the remember-fatigue gate."""
        adv = _adv(remember_escalation_chunks=1000)
        adv.record("a.py", "1-10", "fn", "x" * 400)
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() == "", "auto path gated"
        assert "ACTION REQUIRED" in adv.eviction_hint(), "explicit path ungated"

    def test_default_remember_escalation_chunks_matches_config(self) -> None:
        adv = EvictionAdvisor()
        assert adv._remember_escalation_chunks == EVICTION_REMEMBER_ESCALATION_CHUNKS


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------

class TestRememberEscalationConfig:
    def test_constant_exists_and_is_positive(self) -> None:
        assert isinstance(EVICTION_REMEMBER_ESCALATION_CHUNKS, int)
        assert EVICTION_REMEMBER_ESCALATION_CHUNKS > 0


# ---------------------------------------------------------------------------
# VectrService / MCP dispatch integration
# ---------------------------------------------------------------------------

class TestServiceNoteRemembered:
    def test_note_remembered_resets_the_calling_sessions_advisor_only(
        self, tmp_path, monkeypatch
    ) -> None:
        svc = _make_service(tmp_path, monkeypatch)
        results, _ = svc.search("handler", n_results=5)
        svc.record_results(results, session_id="session-a")
        svc.record_results(results, session_id="session-b")
        advisor_a = svc._advisor_for("session-a")
        advisor_b = svc._advisor_for("session-b")
        assert advisor_a._chunks_since_remember > 0
        assert advisor_b._chunks_since_remember > 0

        svc.note_remembered(session_id="session-a")

        assert advisor_a._chunks_since_remember == 0
        assert advisor_b._chunks_since_remember > 0, (
            "note_remembered() must not reset a different session's counter"
        )


class TestDispatchRememberResetsEscalation:
    def test_vectr_remember_resets_calling_sessions_escalation_gate(
        self, tmp_path, monkeypatch
    ) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        advisor = svc._advisor_for("session-a")
        advisor._retrieval_call_threshold = 0
        advisor._retrieved_token_gate = 0

        handle_tools_call("vectr_search", {"query": "handler"}, svc, session_id="session-a")
        handle_tools_call(
            "vectr_remember", {"content": "handler_0 does X"}, svc, session_id="session-a"
        )
        assert advisor._chunks_since_remember == 0

    def test_per_session_independence_through_dispatch(self, tmp_path, monkeypatch) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        for sid in ("session-a", "session-b"):
            adv = svc._advisor_for(sid)
            adv._retrieval_call_threshold = 0
            adv._retrieved_token_gate = 0

        handle_tools_call("vectr_search", {"query": "handler"}, svc, session_id="session-a")
        handle_tools_call("vectr_search", {"query": "handler"}, svc, session_id="session-b")
        handle_tools_call(
            "vectr_remember", {"content": "note"}, svc, session_id="session-a"
        )

        assert svc._advisor_for("session-a")._chunks_since_remember == 0
        assert svc._advisor_for("session-b")._chunks_since_remember > 0, (
            "session-b's escalation state must be untouched by session-a's remember"
        )


# ---------------------------------------------------------------------------
# No double banner stacking in a single tool response
# ---------------------------------------------------------------------------

class TestNoDoubleBannerStacking:
    def test_eviction_hint_and_soft_nudge_never_both_appear(self, tmp_path, monkeypatch) -> None:
        """When both the eviction advisor's escalation and the turn-count soft
        nudge would independently qualify to fire, only one banner appears."""
        from integrations.mcp_server import handle_tools_call
        from integrations.mcp_server._session import _session_calls_since_save
        from agent.config import BEHAVIOR_REMEMBER_NUDGE_THRESHOLD

        svc = _make_service(tmp_path, monkeypatch)
        advisor = svc._advisor_for("session-a")
        advisor._retrieval_call_threshold = 0
        advisor._retrieved_token_gate = 0
        advisor._remember_escalation_chunks = 0  # force the escalated banner to qualify

        # push the turn-count soft-nudge counter past its own threshold too
        _session_calls_since_save["session-a"] = BEHAVIOR_REMEMBER_NUDGE_THRESHOLD + 1

        result = handle_tools_call(
            "vectr_search", {"query": "handler"}, svc, session_id="session-a"
        )
        text = result["content"][0]["text"]
        assert "Context management hint" in text, "sanity: escalated banner must qualify"
        assert "vectr_remember reminder" not in text, (
            "the soft turn-count nudge must not stack alongside the eviction hint banner"
        )
