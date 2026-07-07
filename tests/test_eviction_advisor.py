"""
Tests for agent/eviction_advisor.py — EvictionAdvisor.

Verifies:
  - record() tracks unique chunks (no duplicates by file:lines key)
  - record_results() batch-records SearchResult objects
  - total_tokens_in_session() sums estimated_tokens across chunks
  - should_evict() returns True when token count >= threshold
  - eviction_hint() returns empty string when no chunks
  - eviction_hint() formats chunks grouped by file with symbol names
  - clear_session() resets all state
  - as_chunk_dicts() serialises correctly
  - RetrievedChunk.estimated_tokens is at least 1
"""
from __future__ import annotations

import pytest

from agent.eviction_advisor import EvictionAdvisor, RetrievedChunk


# ---------------------------------------------------------------------------
# RetrievedChunk
# ---------------------------------------------------------------------------

class TestRetrievedChunk:
    def test_estimated_tokens_short_content(self) -> None:
        c = RetrievedChunk(file_path="f.py", lines="1-5", symbol_name="fn", content="x")
        assert c.estimated_tokens >= 1

    def test_estimated_tokens_approximate(self) -> None:
        content = "a" * 400
        c = RetrievedChunk(file_path="f.py", lines="1-5", symbol_name="fn", content=content)
        assert c.estimated_tokens == 100

    def test_estimated_tokens_scales_with_content(self) -> None:
        short = RetrievedChunk(file_path="f.py", lines="1-5", symbol_name="fn", content="ab" * 10)
        long_ = RetrievedChunk(file_path="f.py", lines="1-5", symbol_name="fn", content="ab" * 100)
        assert long_.estimated_tokens > short.estimated_tokens

    def test_retrieved_at_is_float(self) -> None:
        c = RetrievedChunk(file_path="f.py", lines="1-5", symbol_name="fn", content="x")
        assert isinstance(c.retrieved_at, float)


# ---------------------------------------------------------------------------
# EvictionAdvisor — record / deduplication
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_adds_chunk(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        assert adv.total_tokens_in_session() > 0

    def test_record_deduplicates_same_file_lines(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        # Only one unique key, so should still be 1 chunk
        assert len(adv._chunks) == 1

    def test_record_different_lines_are_distinct(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "fn_a", "def fn_a(): ...")
        adv.record("auth.py", "20-30", "fn_b", "def fn_b(): ...")
        assert len(adv._chunks) == 2

    def test_record_different_files_are_distinct(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "fn", "def fn(): ...")
        adv.record("models.py", "1-10", "fn", "def fn(): ...")
        assert len(adv._chunks) == 2


# ---------------------------------------------------------------------------
# EvictionAdvisor — record_results
# ---------------------------------------------------------------------------

class TestRecordResults:
    def test_record_results_from_search_results(self) -> None:
        from agent.searcher import SearchResult
        results = [
            SearchResult(file_path="a.py", lines="1-5", symbol_name="f1",
                         language="python", score=0.9, content="def f1(): pass"),
            SearchResult(file_path="b.py", lines="1-5", symbol_name="f2",
                         language="python", score=0.8, content="def f2(): pass"),
        ]
        adv = EvictionAdvisor()
        adv.record_results(results)
        assert len(adv._chunks) == 2

    def test_record_results_deduplicates(self) -> None:
        from agent.searcher import SearchResult
        result = SearchResult(file_path="a.py", lines="1-5", symbol_name="fn",
                              language="python", score=0.9, content="def fn(): pass")
        adv = EvictionAdvisor()
        adv.record_results([result])
        adv.record_results([result])
        assert len(adv._chunks) == 1


# ---------------------------------------------------------------------------
# EvictionAdvisor — should_evict
# ---------------------------------------------------------------------------

class TestShouldEvict:
    def test_below_threshold_returns_false(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=10000)
        adv.record("f.py", "1-5", "fn", "short content")
        assert adv.should_evict() is False

    def test_at_threshold_returns_true(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=1)
        adv.record("f.py", "1-5", "fn", "x" * 100)
        assert adv.should_evict() is True

    def test_empty_session_returns_false(self) -> None:
        adv = EvictionAdvisor()
        assert adv.should_evict() is False

    def test_threshold_configurable(self) -> None:
        adv_low = EvictionAdvisor(eviction_threshold_tokens=5)
        adv_high = EvictionAdvisor(eviction_threshold_tokens=50000)
        content = "x" * 100
        adv_low.record("f.py", "1-5", "fn", content)
        adv_high.record("f.py", "1-5", "fn", content)
        assert adv_low.should_evict() is True
        assert adv_high.should_evict() is False

    def test_default_threshold_is_40k(self) -> None:
        # Default threshold must be 40,000 tokens (research-backed: arXiv:2310.08560).
        # 4K was too low and caused premature eviction hints on small sessions.
        adv = EvictionAdvisor()
        assert adv._threshold == 40_000

    def test_40k_chars_triggers_eviction(self) -> None:
        adv = EvictionAdvisor()
        # 40K chars ÷ 4 = 10K tokens — below threshold → no eviction
        adv.record("f.py", "1-100", "fn", "x" * 40_000)
        assert adv.should_evict() is False
        # 160K chars ÷ 4 = 40K tokens — at threshold → eviction fires
        adv.record("g.py", "1-100", "fn2", "y" * 120_000)
        assert adv.should_evict() is True


# ---------------------------------------------------------------------------
# EvictionAdvisor — total_tokens_in_session
# ---------------------------------------------------------------------------

class TestTotalTokens:
    def test_empty_session_is_zero(self) -> None:
        adv = EvictionAdvisor()
        assert adv.total_tokens_in_session() == 0

    def test_tokens_sum_across_chunks(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fn", "a" * 40)   # ~10 tokens
        adv.record("b.py", "1-5", "fn", "b" * 40)   # ~10 tokens
        assert adv.total_tokens_in_session() == 20


# ---------------------------------------------------------------------------
# EvictionAdvisor — eviction_hint
# ---------------------------------------------------------------------------

class TestEvictionHint:
    def test_empty_session_returns_empty_string(self) -> None:
        adv = EvictionAdvisor()
        assert adv.eviction_hint() == ""

    def test_empty_chunks_but_time_trigger_returns_generic_nudge(self) -> None:
        # No vectr_search calls, but time threshold already elapsed → still nudge
        adv = EvictionAdvisor(time_threshold_seconds=0)
        hint = adv.eviction_hint()
        assert hint != "", "should return a nudge when time trigger fired even with no tracked chunks"
        assert "vectr_remember" in hint
        assert "ACTION REQUIRED" in hint

    def test_hint_mentions_file_path(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        hint = adv.eviction_hint()
        assert "auth.py" in hint

    def test_hint_mentions_symbol_name(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        hint = adv.eviction_hint()
        assert "verify_token" in hint

    def test_hint_mentions_token_count(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "fn", "x" * 400)  # 100 tokens
        hint = adv.eviction_hint()
        assert "100" in hint

    def test_hint_mentions_chunk_count(self) -> None:
        adv = EvictionAdvisor()
        adv.record("a.py", "1-5", "fa", "content_a" * 10)
        adv.record("b.py", "1-5", "fb", "content_b" * 10)
        hint = adv.eviction_hint()
        assert "2" in hint

    def test_hint_groups_by_file(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "fn_a", "content a" * 5)
        adv.record("auth.py", "20-30", "fn_b", "content b" * 5)
        adv.record("models.py", "1-5", "fn_c", "content c" * 5)
        hint = adv.eviction_hint()
        # Both chunks from auth.py should be listed under auth.py
        auth_idx = hint.index("auth.py")
        assert "fn_a" in hint[auth_idx:auth_idx + 200] or "fn_a" in hint

    def test_hint_distinguishes_chunk_retrieval_from_note_retrieval(self) -> None:
        # Raw codebase chunks → re-retrievable via vectr_fetch (UPG-CTX-EVICT
        # deterministic re-fetch surface superseded the earlier "re-run
        # vectr_search/vectr_locate" wording — UPG-EVICT-SESSION-SCOPE).
        # Synthesized analysis (saved via vectr_remember) → retrievable via vectr_recall
        # Both paths must appear so the LLM understands the full protocol.
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 10)
        hint = adv.eviction_hint()
        assert "vectr_fetch" in hint, "hint must tell LLM how to re-retrieve raw codebase chunks"
        assert "vectr_recall" in hint, "hint must tell LLM that saved notes are retrieved via vectr_recall, not vectr_search"

    def test_hint_contains_directive_action_required(self) -> None:
        # Hint must use imperative language so the LLM calls vectr_remember.
        # Passive/conditional phrasing ("if you have findings...") gets ignored.
        adv = EvictionAdvisor()
        adv.record("gc.c", "100-120", "gc_collect", "static int gc_collect() {...}")
        hint = adv.eviction_hint()
        assert "ACTION REQUIRED" in hint, (
            "eviction hint must use directive 'ACTION REQUIRED' language to prompt vectr_remember"
        )

    def test_hint_contains_vectr_remember_call(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 10)
        hint = adv.eviction_hint()
        assert "vectr_remember" in hint, "hint must tell the LLM to call vectr_remember"


# ---------------------------------------------------------------------------
# EvictionAdvisor — clear_session
# ---------------------------------------------------------------------------

class TestClearSession:
    def test_clear_resets_chunks(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content")
        assert len(adv._chunks) == 1
        adv.clear_session()
        assert len(adv._chunks) == 0

    def test_clear_resets_token_count(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "x" * 400)
        adv.clear_session()
        assert adv.total_tokens_in_session() == 0

    def test_clear_means_no_eviction(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=1)
        adv.record("f.py", "1-5", "fn", "x" * 100)
        assert adv.should_evict() is True
        adv.clear_session()
        assert adv.should_evict() is False


# ---------------------------------------------------------------------------
# EvictionAdvisor — as_chunk_dicts
# ---------------------------------------------------------------------------

class TestAsChunkDicts:
    def test_returns_list_of_dicts(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "verify_token", "def verify_token(): ...")
        dicts = adv.as_chunk_dicts()
        assert isinstance(dicts, list)
        assert len(dicts) == 1

    def test_dict_has_expected_keys(self) -> None:
        adv = EvictionAdvisor()
        adv.record("auth.py", "1-10", "fn", "content")
        d = adv.as_chunk_dicts()[0]
        assert "file" in d
        assert "lines" in d
        assert "symbol" in d
        assert "content" in d

    def test_dict_values_match_recorded(self) -> None:
        adv = EvictionAdvisor()
        adv.record("models.py", "5-15", "UserModel", "class UserModel: pass")
        d = adv.as_chunk_dicts()[0]
        assert d["file"] == "models.py"
        assert d["lines"] == "5-15"
        assert d["symbol"] == "UserModel"
        assert d["content"] == "class UserModel: pass"

    def test_empty_session_returns_empty_list(self) -> None:
        adv = EvictionAdvisor()
        assert adv.as_chunk_dicts() == []


# ---------------------------------------------------------------------------
# EvictionAdvisor — tool_call_count secondary trigger
# ---------------------------------------------------------------------------

class TestToolCallCountTrigger:
    def test_evicts_after_tool_call_threshold(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=5)
        assert adv.should_evict() is False
        for _ in range(5):
            adv.increment_tool_call()
        assert adv.should_evict() is False  # exactly at threshold, not over
        adv.increment_tool_call()
        assert adv.should_evict() is True   # > threshold

    def test_token_threshold_still_works_independently(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=1, tool_call_threshold=1000)
        adv.record("f.py", "1-5", "fn", "x" * 100)
        assert adv.should_evict() is True   # token threshold fired, not tool-call

    def test_clear_session_resets_tool_call_count(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=3)
        for _ in range(5):
            adv.increment_tool_call()
        assert adv.should_evict() is True
        adv.clear_session()
        assert adv.should_evict() is False
        assert adv._tool_call_count == 0

    def test_default_tool_call_threshold_is_10(self) -> None:
        adv = EvictionAdvisor()
        assert adv._tool_call_threshold == 10

    def test_21_tool_calls_triggers_eviction(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=20)
        for _ in range(21):
            adv.increment_tool_call()
        assert adv.should_evict() is True

    def test_10_tool_calls_no_eviction(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=10,
                              retrieval_call_threshold=1000, time_threshold_seconds=1000)
        for _ in range(10):
            adv.increment_tool_call()
        assert adv.should_evict() is False  # exactly 10, not > 10


# ---------------------------------------------------------------------------
# EvictionAdvisor — retrieval call count secondary trigger
# ---------------------------------------------------------------------------

class TestRetrievalCallCountTrigger:
    def test_fires_after_retrieval_threshold(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=1000,
                              retrieval_call_threshold=1, time_threshold_seconds=1000)
        assert adv.should_evict() is False
        adv.increment_retrieval_call()
        assert adv.should_evict() is False  # exactly at threshold, not over
        adv.increment_retrieval_call()
        assert adv.should_evict() is True   # > threshold

    def test_default_retrieval_call_threshold_is_1(self) -> None:
        adv = EvictionAdvisor()
        assert adv._retrieval_call_threshold == 1

    def test_fires_independently_of_token_and_tool_count(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=1000,
                              retrieval_call_threshold=1, time_threshold_seconds=1000)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.should_evict() is True

    def test_clear_session_resets_retrieval_count(self) -> None:
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=1000,
                              retrieval_call_threshold=1, time_threshold_seconds=1000)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.should_evict() is True
        adv.clear_session()
        assert adv.should_evict() is False
        assert adv._retrieval_call_count == 0


# ---------------------------------------------------------------------------
# EvictionAdvisor — wall-clock time trigger
# ---------------------------------------------------------------------------

class TestTimeBasedTrigger:
    def test_fires_after_time_threshold(self) -> None:
        import time as _time
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=1000,
                              retrieval_call_threshold=1000, time_threshold_seconds=0.05)
        assert adv.should_evict() is False
        _time.sleep(0.1)
        assert adv.should_evict() is True

    def test_default_time_threshold_is_180s(self) -> None:
        adv = EvictionAdvisor()
        assert adv._time_threshold_seconds == 180.0

    def test_clear_session_resets_timer(self) -> None:
        import time as _time
        adv = EvictionAdvisor(eviction_threshold_tokens=100_000, tool_call_threshold=1000,
                              retrieval_call_threshold=1000, time_threshold_seconds=0.05)
        _time.sleep(0.1)
        assert adv.should_evict() is True
        adv.clear_session()
        assert adv.should_evict() is False

    def test_session_started_at_is_float(self) -> None:
        adv = EvictionAdvisor()
        assert isinstance(adv._session_started_at, float)


# ---------------------------------------------------------------------------
# UPG-7.1 — auto_eviction_hint gating (footer fires on escalation, not every response)
# ---------------------------------------------------------------------------

class TestAutoEvictionHintGatingUPG71:
    def _adv_with_chunk(self, **kw):
        # defaults that disable every trigger except the one a test exercises.
        # retrieved_token_gate=0 disables the UPG-11.15 token-accumulation gate so
        # these tests can focus exclusively on UPG-7.1 fresh-escalation semantics.
        kw.setdefault("eviction_threshold_tokens", 100_000)
        kw.setdefault("tool_call_threshold", 1000)
        kw.setdefault("time_threshold_seconds", 100_000)
        kw.setdefault("retrieved_token_gate", 0)
        adv = EvictionAdvisor(**kw)
        adv.record("auth.py", "1-10", "verify", "x" * 400)  # ~100 tracked tokens
        return adv

    def test_default_rearm_is_4(self) -> None:
        assert EvictionAdvisor()._rearm_retrieval_calls == 4

    def test_fires_once_then_suppressed(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=1)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()         # count > 1 → should_evict True
        assert adv.should_evict() is True
        assert "ACTION REQUIRED" in adv.auto_eviction_hint()   # first fire
        assert adv.auto_eviction_hint() == ""                  # immediately suppressed
        assert adv.auto_eviction_hint() == ""                  # still suppressed

    def test_rearms_after_more_retrieval_calls(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=1, rearm_retrieval_calls=3)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""                  # fire at retr=2
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() == ""                  # +2 since fire (<3) → still gated
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""                  # +3 since fire → re-arms

    def test_rearms_on_token_escalation(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=1, eviction_threshold_tokens=50)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""                  # first fire
        assert adv.auto_eviction_hint() == ""                  # no new pressure → gated
        adv.record("models.py", "1-50", "Model", "y" * 400)    # +~100 tokens ≥ threshold 50
        assert adv.auto_eviction_hint() != ""                  # token escalation re-arms

    def test_explicit_eviction_hint_stays_ungated(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=1)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""                  # auto fires once
        assert adv.auto_eviction_hint() == ""                  # then auto-suppressed
        # the explicit vectr_evict_hint / REST path must always answer
        assert "ACTION REQUIRED" in adv.eviction_hint()

    def test_empty_when_not_should_evict(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=100)  # never trips
        assert adv.should_evict() is False
        assert adv.auto_eviction_hint() == ""

    def test_clear_session_re_arms(self) -> None:
        adv = self._adv_with_chunk(retrieval_call_threshold=1)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""
        assert adv.auto_eviction_hint() == ""
        adv.clear_session()
        assert adv._last_emit is None
        adv.record("new.py", "1-5", "f", "z" * 400)
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""                  # fresh fire after reset


# ---------------------------------------------------------------------------
# UPG-11.15 — token-accumulation gate on auto_eviction_hint
# ---------------------------------------------------------------------------

class TestAutoEvictionHintTokenGateUPG1115:
    """auto_eviction_hint() must not emit when retrieved tokens since the
    last hint (or session start) are below the configured gate threshold,
    even if should_evict() is True due to retrieval call count."""

    def _adv(self, gate: int = 4000, **kw) -> EvictionAdvisor:
        """Build an advisor where only the retrieval-call trigger is active
        (tokens/tools/time all set high) and the token gate is explicit."""
        kw.setdefault("eviction_threshold_tokens", 100_000)
        kw.setdefault("tool_call_threshold", 1000)
        kw.setdefault("time_threshold_seconds", 100_000)
        kw.setdefault("retrieval_call_threshold", 1)
        return EvictionAdvisor(retrieved_token_gate=gate, **kw)

    # -----------------------------------------------------------------------
    # Suppression direction: small results → no hint
    # -----------------------------------------------------------------------

    def test_burst_of_small_searches_does_not_emit(self) -> None:
        """Three tiny search results (each ~50 tokens) must not trigger the hint.

        This mirrors the adoption-reviewer observation: three vectr_search calls
        returning 7-15 line methods triggered the full ACTION REQUIRED block
        even though total context pressure was negligible.
        """
        adv = self._adv(gate=4000)
        # Three calls, each returning a ~15-line method ≈ 50 tokens (200 chars)
        for i in range(3):
            adv.record(f"file_{i}.py", "1-15", f"fn_{i}", "x" * 200)
            adv.increment_retrieval_call()
        # retrieval_call_count = 3 → should_evict() is True via call-count trigger
        assert adv.should_evict() is True
        # but total tokens ≈ 150, far below gate=4000 → hint must be suppressed
        assert adv.auto_eviction_hint() == "", (
            "auto_eviction_hint() must not emit when accumulated tokens < gate"
        )

    def test_single_call_below_gate_does_not_emit(self) -> None:
        """A single search returning a short method must not emit."""
        adv = self._adv(gate=4000, retrieval_call_threshold=0)
        adv.record("a.py", "1-10", "short_fn", "x" * 100)  # 25 tokens
        adv.increment_retrieval_call()
        assert adv.should_evict() is True
        assert adv.auto_eviction_hint() == ""

    def test_gate_boundary_one_below_suppresses(self) -> None:
        """One token below gate → still suppressed."""
        gate = 4000
        adv = self._adv(gate=gate, retrieval_call_threshold=0)
        # (gate - 1) tokens = (gate - 1) * 4 chars
        adv.record("a.py", "1-100", "fn", "x" * ((gate - 1) * 4))
        adv.increment_retrieval_call()
        assert adv.total_tokens_in_session() == gate - 1
        assert adv.auto_eviction_hint() == "", (
            "hint must be suppressed when accumulated tokens < gate"
        )

    # -----------------------------------------------------------------------
    # Emission direction: sufficient tokens → hint fires
    # -----------------------------------------------------------------------

    def test_hint_fires_when_tokens_cross_gate(self) -> None:
        """Accumulated tokens at or above the gate must trigger the hint."""
        gate = 4000
        adv = self._adv(gate=gate, retrieval_call_threshold=0)
        # gate tokens worth of content
        adv.record("big.py", "1-200", "big_fn", "x" * (gate * 4))
        adv.increment_retrieval_call()
        assert adv.total_tokens_in_session() >= gate
        assert adv.should_evict() is True
        assert "ACTION REQUIRED" in adv.auto_eviction_hint(), (
            "hint must emit when accumulated tokens >= gate"
        )

    def test_accumulated_tokens_span_multiple_calls(self) -> None:
        """Multiple calls each below the gate can accumulate to cross it."""
        gate = 1000  # lower gate to make test concise
        adv = self._adv(gate=gate, retrieval_call_threshold=0)
        # 5 calls × 300 tokens each = 1500 tokens > gate=1000
        for i in range(5):
            adv.record(f"f{i}.py", "1-50", f"fn{i}", "x" * 1200)  # 300 tokens each
            adv.increment_retrieval_call()
        assert adv.total_tokens_in_session() == 1500
        assert adv.auto_eviction_hint() != "", (
            "accumulated tokens (1500) > gate (1000) must emit the hint"
        )

    # -----------------------------------------------------------------------
    # Token delta resets after each emit
    # -----------------------------------------------------------------------

    def test_gate_resets_after_emit(self) -> None:
        """After the hint fires, the gate applies to tokens accumulated SINCE
        that emit — not cumulative session tokens."""
        gate = 1000
        adv = self._adv(gate=gate, retrieval_call_threshold=0)
        # First burst: 1200 tokens → crosses gate → hint fires
        adv.record("a.py", "1-100", "fn_a", "x" * 4800)  # 1200 tokens
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""              # first fire
        # Second burst: only 200 more tokens → delta since last emit < gate
        adv.record("b.py", "1-20", "fn_b", "x" * 800)    # 200 tokens
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() == "", (
            "after an emit, gate must apply to tokens since that emit, not total"
        )

    def test_gate_rearms_when_delta_crosses_threshold_again(self) -> None:
        """After an emit, once delta tokens since that emit cross the gate again,
        the hint can re-fire (subject also to _fresh_escalation)."""
        gate = 500
        # small eviction_threshold so _fresh_escalation re-arms on token delta
        adv = self._adv(gate=gate, retrieval_call_threshold=0, eviction_threshold_tokens=200)
        # First emit: 600 tokens > gate=500
        adv.record("a.py", "1-100", "fn_a", "x" * 2400)  # 600 tokens
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != ""              # first fire
        # Add 600 more tokens → delta since emit = 600 > gate=500 → re-fires
        adv.record("b.py", "1-100", "fn_b", "x" * 2400)  # 600 tokens
        adv.increment_retrieval_call()
        assert adv.auto_eviction_hint() != "", (
            "delta since last emit (600) > gate (500) must allow re-fire"
        )

    # -----------------------------------------------------------------------
    # Explicit hint path remains ungated
    # -----------------------------------------------------------------------

    def test_explicit_eviction_hint_bypasses_token_gate(self) -> None:
        """The explicit vectr_evict_hint / /v1/evict path uses eviction_hint()
        directly and must bypass the token gate."""
        gate = 100_000  # gate too high for any test to naturally cross
        adv = self._adv(gate=gate, retrieval_call_threshold=0)
        adv.record("tiny.py", "1-5", "fn", "x" * 40)     # 10 tokens
        adv.increment_retrieval_call()
        assert adv.should_evict() is True
        assert adv.auto_eviction_hint() == "", "auto path gated by token gate"
        # explicit path always answers
        assert "ACTION REQUIRED" in adv.eviction_hint(), (
            "explicit eviction_hint() must bypass the token gate"
        )

    # -----------------------------------------------------------------------
    # Configuration
    # -----------------------------------------------------------------------

    def test_default_retrieved_token_gate_is_4000(self) -> None:
        """Default gate must be 4000 tokens as specified in config.yaml."""
        adv = EvictionAdvisor()
        assert adv._retrieved_token_gate == 4000

    def test_retrieved_token_gate_zero_disables_gate(self) -> None:
        """Gate=0 means every non-empty emit is allowed (used in UPG-7.1 tests)."""
        adv = self._adv(gate=0, retrieval_call_threshold=0)
        adv.record("a.py", "1-5", "fn", "x" * 4)         # 1 token — well below any real gate
        adv.increment_retrieval_call()
        assert adv.should_evict() is True
        # gate=0 means 1 >= 0 → allowed
        assert adv.auto_eviction_hint() != "", "gate=0 must not suppress any non-zero token emit"


# ---------------------------------------------------------------------------
# UPG-11.15 — config loader: new key raises KeyError if missing, not silent
# ---------------------------------------------------------------------------

class TestEvictionConfigLoaderUPG1115:
    def test_eviction_retrieved_token_gate_constant_exists(self) -> None:
        """EVICTION_RETRIEVED_TOKEN_GATE must be exported from agent.config."""
        from agent.config import EVICTION_RETRIEVED_TOKEN_GATE
        assert isinstance(EVICTION_RETRIEVED_TOKEN_GATE, int)
        assert EVICTION_RETRIEVED_TOKEN_GATE == 4000

    def test_eviction_retrieved_token_gate_is_positive(self) -> None:
        """Gate must be > 0 — a zero or negative gate would disable suppression."""
        from agent.config import EVICTION_RETRIEVED_TOKEN_GATE
        assert EVICTION_RETRIEVED_TOKEN_GATE > 0

    def test_missing_behavior_eviction_key_raises(self) -> None:
        """Loading config without behavior.eviction.retrieved_token_gate must raise
        KeyError — not silently default to a magic value (per UPG-12.1 pattern)."""
        import yaml

        broken_yaml = """
behavior:
  remember_nudge:
    threshold: 10
    cooldown: 5
"""
        cfg = yaml.safe_load(broken_yaml)
        with pytest.raises(KeyError):
            # Direct subscript access must raise on missing key
            _ = cfg["behavior"]["eviction"]["retrieved_token_gate"]
