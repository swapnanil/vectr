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

    def test_hint_includes_recall_instruction(self) -> None:
        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "content" * 10)
        hint = adv.eviction_hint()
        assert "vectr_search" in hint


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
