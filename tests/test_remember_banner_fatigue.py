"""Tests for UPG-REMEMBER-BANNER-FATIGUE and UPG-EVICT-ESCALATION-GATE-TOO-LOW.

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
  e) UPG-EVICT-ESCALATION-GATE-TOO-LOW: a companion tokens-since-remember gate
     (EVICTION_REMEMBER_ESCALATION_TOKENS) required in ADDITION to the chunk
     gate, so a single large search cannot trip both gates in one burst.
  f) The first eligible re-fire after a vectr_remember renders the softer,
     non-escalated wording; only a second-or-later eligible re-fire without
     an intervening vectr_remember escalates to "ACTION REQUIRED".
  g) The machine-readable `needs_remember: true` line appears only on the
     escalated form.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.config import (
    EVICTION_REMEMBER_ESCALATION_CHUNKS,
    EVICTION_REMEMBER_ESCALATION_TOKENS,
)
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
    on the remember-escalation gate. remember_escalation_tokens defaults to 0
    (disabled) so tests exercising only the chunk-count half of the gate
    (remember_escalation_chunks) aren't also blocked by the companion
    token-count gate (UPG-EVICT-ESCALATION-GATE-TOO-LOW); tests of the token
    gate itself override it explicitly."""
    kw.setdefault("eviction_threshold_tokens", 100_000)
    kw.setdefault("tool_call_threshold", 1000)
    kw.setdefault("time_threshold_seconds", 100_000)
    kw.setdefault("retrieval_call_threshold", 0)
    kw.setdefault("retrieved_token_gate", 0)
    kw.setdefault("remember_escalation_tokens", 0)
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
        # UPG-EVICT-ESCALATION-GATE-TOO-LOW: the eligibility gate (enough
        # chunks/tokens since the last vectr_remember) only decides WHETHER
        # auto_eviction_hint() fires at all. Once eligible, the FIRST re-fire
        # after a vectr_remember renders the softer, non-escalated wording;
        # only a SECOND eligible re-fire without an intervening vectr_remember
        # escalates to "ACTION REQUIRED" — see TestSoftThenEscalateProgression
        # for the dedicated soft/escalate test.
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
        first_refire = adv.auto_eviction_hint()
        assert first_refire != "" and "ACTION REQUIRED" not in first_refire, (
            "the first eligible re-fire after vectr_remember must use the softer, "
            "non-escalated wording"
        )
        adv.increment_retrieval_call()  # re-arm _fresh_escalation (rearm=1)
        assert "ACTION REQUIRED" in adv.auto_eviction_hint(), (
            "a second eligible re-fire without an intervening vectr_remember must escalate"
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

    def test_escalation_tokens_constant_exists_and_is_positive(self) -> None:
        """UPG-EVICT-ESCALATION-GATE-TOO-LOW: companion token gate."""
        assert isinstance(EVICTION_REMEMBER_ESCALATION_TOKENS, int)
        assert EVICTION_REMEMBER_ESCALATION_TOKENS > 0

    def test_default_escalation_tokens_exceeds_a_single_search_burst(self) -> None:
        """The whole point of the gate: a single search returning n_results=5
        chunks of the size describing the live-reproduced defect (~800+ tokens
        each, per the retrieved_token_gate=4000 comment) must not alone clear
        the gate. Concretely: 5 chunks whose combined tokens equal exactly
        retrieved_token_gate's own worked example (4000 tokens) must be
        strictly below EVICTION_REMEMBER_ESCALATION_TOKENS."""
        from agent.config import EVICTION_RETRIEVED_TOKEN_GATE
        assert EVICTION_RETRIEVED_TOKEN_GATE < EVICTION_REMEMBER_ESCALATION_TOKENS

    def test_missing_remember_escalation_tokens_key_raises(self) -> None:
        """Direct subscript access must raise KeyError on a missing key —
        never silently default (per UPG-12.1 pattern)."""
        import yaml

        broken_yaml = """
behavior:
  eviction:
    remember_escalation_chunks: 3
"""
        cfg = yaml.safe_load(broken_yaml)
        with pytest.raises(KeyError):
            _ = cfg["behavior"]["eviction"]["remember_escalation_tokens"]


# ---------------------------------------------------------------------------
# UPG-EVICT-ESCALATION-GATE-TOO-LOW
#
# Live-reproduced defect: the escalated banner re-fired on the very next
# search after the caller had just complied with vectr_remember, because
# remember_escalation_chunks (3) is smaller than a single search's default
# n_results (5) — one large-chunk search can cross both the chunk-count gate
# and the pre-existing token gate in a single burst.
# ---------------------------------------------------------------------------

class TestEscalationGateTooLow:
    def _big_chunk_content(self, tokens: int) -> str:
        return "x" * (tokens * 4)

    def test_defect_reproduction_one_remember_then_one_large_search(self) -> None:
        """Reproduces the live defect: one vectr_remember, then a single search
        burst of 5 large chunks (>4000 tokens total, mirroring a real
        large-chunk-corpus search response) must NOT render the escalated
        ACTION REQUIRED banner immediately afterward."""
        adv = _adv(
            remember_escalation_chunks=EVICTION_REMEMBER_ESCALATION_CHUNKS,
            remember_escalation_tokens=EVICTION_REMEMBER_ESCALATION_TOKENS,
        )
        # Prime and comply once, mirroring "caller just complied with vectr_remember".
        adv.record("prior.py", "1-10", "fn", self._big_chunk_content(500))
        adv.increment_retrieval_call()
        adv.note_remembered()

        # A single vectr_search call, default n_results=5, each chunk ~900
        # tokens on a large-chunk corpus -> ~4500 tokens in one burst. Before
        # the fix this alone crossed both remember_escalation_chunks (3) and
        # the pre-existing retrieved_token_gate (4000).
        for i in range(5):
            adv.record(f"big{i}.py", "1-50", f"fn_{i}", self._big_chunk_content(900))
        adv.increment_retrieval_call()

        hint = adv.auto_eviction_hint()
        assert "ACTION REQUIRED" not in hint, (
            "the escalated banner must not re-fire on the very next search "
            "burst right after the caller complied with vectr_remember"
        )
        assert "needs_remember: true" not in hint

    def test_huge_single_burst_crossing_both_gates_still_renders_soft_first(self) -> None:
        """Defense-in-depth: even if a single search burst is large enough to
        cross BOTH the chunk gate and the new token gate in one call, the
        FIRST eligible re-fire after vectr_remember still renders the softer
        wording, not ACTION REQUIRED — the soft/escalate split (not just the
        token gate) is what prevents immediate re-escalation on corpora with
        chunks large enough to blow past any fixed token threshold in one call."""
        adv = _adv(
            remember_escalation_chunks=EVICTION_REMEMBER_ESCALATION_CHUNKS,
            remember_escalation_tokens=EVICTION_REMEMBER_ESCALATION_TOKENS,
        )
        adv.record("prior.py", "1-10", "fn", self._big_chunk_content(500))
        adv.increment_retrieval_call()
        adv.note_remembered()

        # One huge search burst crossing both gates in a single call.
        for i in range(5):
            adv.record(f"huge{i}.py", "1-200", f"fn_{i}", self._big_chunk_content(2000))
        adv.increment_retrieval_call()

        hint = adv.auto_eviction_hint()
        assert hint != "", "sanity: gate should be crossed"
        assert "ACTION REQUIRED" not in hint, (
            "first eligible re-fire after vectr_remember must be soft even when "
            "a single burst crosses both gates at once"
        )

    def test_soft_then_escalated_progression(self) -> None:
        """First eligible re-fire after vectr_remember -> soft wording.
        Second eligible re-fire, no intervening vectr_remember -> escalated."""
        adv = _adv(remember_escalation_chunks=1, remember_escalation_tokens=1,
                   rearm_retrieval_calls=1)
        adv.record("a.py", "1-10", "fn", "x" * 40)
        adv.increment_retrieval_call()
        assert "ACTION REQUIRED" in adv.auto_eviction_hint(), "first-ever fire escalates"

        adv.note_remembered()
        adv.record("b.py", "1-10", "fn", "x" * 40)
        adv.increment_retrieval_call()
        soft_hint = adv.auto_eviction_hint()
        assert soft_hint != "" and "ACTION REQUIRED" not in soft_hint, (
            "first eligible re-fire after vectr_remember must be soft"
        )
        assert "vectr_remember" in soft_hint

        adv.record("c.py", "1-10", "fn", "x" * 40)
        adv.increment_retrieval_call()
        escalated_hint = adv.auto_eviction_hint()
        assert "ACTION REQUIRED" in escalated_hint, (
            "second eligible re-fire without an intervening vectr_remember must escalate"
        )

    def test_needs_remember_line_present_only_on_escalated_form(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 20)
        escalated = adv.eviction_hint(escalated=True)
        soft = adv.eviction_hint(escalated=False)
        assert "needs_remember: true" in escalated
        assert "needs_remember" not in soft

    def test_needs_remember_line_present_only_on_escalated_no_chunks_form(self) -> None:
        """The no-chunks time-triggered fallback must also gate the token on
        escalated vs. soft."""
        adv = EvictionAdvisor(time_threshold_seconds=0)
        escalated = adv.eviction_hint(escalated=True)
        soft = adv.eviction_hint(escalated=False)
        assert "needs_remember: true" in escalated
        assert "needs_remember" not in soft

    def test_soft_form_omits_action_required_but_keeps_vectr_remember_call(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 20)
        soft = adv.eviction_hint(escalated=False)
        assert "ACTION REQUIRED" not in soft
        assert "vectr_remember" in soft

    def test_explicit_eviction_hint_default_stays_escalated(self) -> None:
        """The explicit vectr_evict_hint tool / /v1/evict endpoint call
        eviction_hint() with no arguments — must stay ungated AND escalated."""
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 20)
        assert "ACTION REQUIRED" in adv.eviction_hint()
        assert "needs_remember: true" in adv.eviction_hint()


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
        advisor._remember_escalation_tokens = 0

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
