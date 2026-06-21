"""Unit tests for run_acceptance.py harness logic (zero daemon calls).

Focus: the top_k_absent leaf-equality check.

The bug: old substring containment ('absent_sym in r["symbol"]') produced
false-positive "failures" when the absent symbol is a prefix/substring of a
valid result's symbol.

Real example (F8):
    case asserts absent_sym="read" from top-k.
    vectr correctly returns rank1 = "HttpRequest.readlines" (leaf "readlines").
    Substring check: "read" in "HttpRequest.readlines" -> True -> fires absent
    check incorrectly -> false regression.
    Leaf check: leaf("HttpRequest.readlines") = "readlines" != "read" -> absent
    check passes -> correct.

Same trap: "all" in "recall", "get" in "getter", "run" in "running".
"""
from __future__ import annotations

import pytest

from run_acceptance import (
    _symbol_leaf,
    top_k_absent,
    top_k_contains,
    sorted_by_score,
    affordance_expand_to_symbol,
)


# ---------------------------------------------------------------------------
# _symbol_leaf
# ---------------------------------------------------------------------------

class TestSymbolLeaf:
    def test_bare_name(self) -> None:
        assert _symbol_leaf("read") == "read"

    def test_qualified_dot(self) -> None:
        assert _symbol_leaf("HttpRequest.readlines") == "readlines"

    def test_qualified_double_colon(self) -> None:
        assert _symbol_leaf("Buffer::read") == "read"

    def test_deep_qualified(self) -> None:
        assert _symbol_leaf("A.B.C.method") == "method"

    def test_empty_string(self) -> None:
        assert _symbol_leaf("") == ""


# ---------------------------------------------------------------------------
# top_k_absent — core F8 regression guard
# ---------------------------------------------------------------------------

def _r(symbol: str, score: float = 0.8) -> dict:
    return {"symbol": symbol, "file": "/p/file.py", "score": score, "content": "x"}


class TestTopKAbsent:
    """Guards the harness leaf-equality fix (UPG-12.2).

    Each test pairs the BEFORE (substring) expectation with the AFTER (leaf)
    behaviour so it's clear what changed.
    """

    # --- F8: read absent vs HttpRequest.readlines present ---

    def test_f8_readlines_not_read_passes(self) -> None:
        """rank1=HttpRequest.readlines, absent_sym='read' -> PASS.

        Leaf('HttpRequest.readlines') = 'readlines' != 'read'.
        Old substring check ('read' in 'HttpRequest.readlines') would have
        fired falsely, producing a false regression. Leaf check correctly
        sees leaf='readlines' != 'read' and passes.

        This mirrors the actual F8 outcome: UPG-11.8 added 'read' to
        prog_stopwords; the real HttpRequest.read (rank7) is outside the k=3
        window, and rank1 HttpRequest.readlines is a different symbol.
        """
        results = [
            _r("HttpRequest.readlines", 1.0),
            _r("FileWrapper.readline", 0.9),
            _r("BinaryFile.readall", 0.8),
        ]
        # All three have leaves readlines/readline/readall — none is 'read'
        assert top_k_absent(results, k=3, symbol="read") is True

    def test_f8_bare_read_fires(self) -> None:
        """If a result has exact symbol leaf 'read', the absent check fires."""
        results = [_r("HttpRequest.read", 1.0)]
        assert top_k_absent(results, k=3, symbol="read") is False

    def test_f8_bare_read_outside_k_ignored(self) -> None:
        """A 'read'-leaf at rank 4 doesn't fire a k=3 absent check."""
        results = [
            _r("HttpRequest.readlines", 1.0),
            _r("FileWrapper.readline", 0.9),
            _r("BinaryFile.readall", 0.8),
            _r("HttpRequest.read", 0.7),   # rank 4 — outside k=3
        ]
        assert top_k_absent(results, k=3, symbol="read") is True

    # --- Other substring-trap cases ---

    def test_all_absent_vs_recall_present(self) -> None:
        """'all' is a substring of 'recall' but must not fire the absent check.

        F6 asserts leaf='all' absent. If rank1 returns 'Memory.recall',
        old substring check would fire falsely (since 'all' in 'recall').
        """
        results = [_r("Memory.recall", 1.0), _r("MigrationLoader.load_all", 0.8)]
        # 'recall' leaf != 'all', 'load_all' leaf == 'load_all' != 'all'
        assert top_k_absent(results, k=5, symbol="all") is True

    def test_get_absent_vs_getter_present(self) -> None:
        """'get' absent, result has symbol leaf 'getter' -> absent check passes."""
        results = [_r("Config.getter", 1.0)]
        assert top_k_absent(results, k=3, symbol="get") is True

    def test_run_absent_vs_running_present(self) -> None:
        """'run' absent, result has symbol 'Process.running' -> absent check passes."""
        results = [_r("Process.running", 0.9)]
        assert top_k_absent(results, k=3, symbol="run") is True

    # --- Correct fires (should still detect real violations) ---

    def test_qualified_absent_symbol_fires_on_exact_leaf(self) -> None:
        """Absent symbol 'RemoveField.deconstruct' fires when leaf is 'deconstruct'
        and the full qualified name is exactly 'RemoveField.deconstruct'.

        The check must still catch the real absent target when it appears.
        """
        results = [_r("RemoveField.deconstruct", 1.0)]
        assert top_k_absent(results, k=3, symbol="RemoveField.deconstruct") is False

    def test_bare_leaf_exact_match_fires(self) -> None:
        """If the bare symbol 'deconstruct' equals absent_sym 'deconstruct', fire."""
        results = [_r("deconstruct", 0.9)]
        assert top_k_absent(results, k=3, symbol="deconstruct") is False

    def test_empty_results_always_absent(self) -> None:
        assert top_k_absent([], k=5, symbol="read") is True

    def test_no_symbol_field_is_skipped(self) -> None:
        """Results without a 'symbol' key must not crash or fire."""
        results = [{"file": "/p/f.py", "score": 0.8, "content": "x", "symbol": None}]
        assert top_k_absent(results, k=3, symbol="read") is True

    def test_rust_double_colon_leaf(self) -> None:
        """Rust-style 'Buffer::read' has leaf 'read' -> absent check fires."""
        results = [_r("Buffer::read", 0.9)]
        assert top_k_absent(results, k=3, symbol="read") is False

    def test_rust_double_colon_readlines_does_not_fire(self) -> None:
        """Rust 'Buffer::readlines' has leaf 'readlines' != 'read' -> no fire."""
        results = [_r("Buffer::readlines", 0.9)]
        assert top_k_absent(results, k=3, symbol="read") is True


# ---------------------------------------------------------------------------
# top_k_contains
# ---------------------------------------------------------------------------

class TestTopKContains:
    def test_file_and_symbol_match(self) -> None:
        results = [{"symbol": "Field.deconstruct", "file": "/p/fields/__init__.py",
                    "score": 1.0, "content": "x"}]
        assert top_k_contains(results, 3, file="fields/__init__.py",
                               symbol="Field.deconstruct") is True

    def test_leaf_match_for_symbol(self) -> None:
        results = [{"symbol": "Field.deconstruct", "file": "/p/f.py",
                    "score": 1.0, "content": "x"}]
        # match by leaf 'deconstruct'
        assert top_k_contains(results, 3, symbol="deconstruct") is True

    def test_qualified_suffix_match(self) -> None:
        results = [{"symbol": "JSONField.from_db_value", "file": "/p/json.py",
                    "score": 1.0, "content": "x"}]
        assert top_k_contains(results, 3, symbol="from_db_value") is True

    def test_file_mismatch(self) -> None:
        results = [{"symbol": "Field.deconstruct", "file": "/p/other.py",
                    "score": 1.0, "content": "x"}]
        assert top_k_contains(results, 3, file="fields/__init__.py",
                               symbol="Field.deconstruct") is False

    def test_outside_k(self) -> None:
        results = [
            {"symbol": "X.y", "file": "/p/a.py", "score": 1.0, "content": "x"},
            {"symbol": "X.y", "file": "/p/a.py", "score": 0.9, "content": "x"},
            {"symbol": "Field.deconstruct", "file": "/p/fields/__init__.py",
             "score": 0.8, "content": "x"},  # rank 3 — outside k=2
        ]
        assert top_k_contains(results, 2, file="fields/__init__.py") is False
        assert top_k_contains(results, 3, file="fields/__init__.py") is True


# ---------------------------------------------------------------------------
# sorted_by_score
# ---------------------------------------------------------------------------

class TestSortedByScore:
    def test_monotonic_passes(self) -> None:
        results = [_r("a", s) for s in [1.0, 0.9, 0.8]]
        assert sorted_by_score(results) is True

    def test_non_monotonic_fails(self) -> None:
        results = [_r("a", s) for s in [0.8, 1.0, 0.9]]
        assert sorted_by_score(results) is False

    def test_equal_scores_pass(self) -> None:
        results = [_r("a", 0.8), _r("b", 0.8)]
        assert sorted_by_score(results) is True

    def test_single_result(self) -> None:
        assert sorted_by_score([_r("a", 0.9)]) is True

    def test_empty(self) -> None:
        assert sorted_by_score([]) is True


# ---------------------------------------------------------------------------
# affordance_expand_to_symbol
# ---------------------------------------------------------------------------

class TestAffordanceExpandToSymbol:
    def test_symbol_start_line_present(self) -> None:
        results = [{"symbol": "Field", "file": "/p/f.py", "score": 0.9,
                    "content": "x", "symbol_start_line": 10, "symbol_end_line": 50}]
        assert affordance_expand_to_symbol(results) is True

    def test_symbol_start_line_zero_fails(self) -> None:
        results = [{"symbol": "Field", "file": "/p/f.py", "score": 0.9,
                    "content": "x", "symbol_start_line": 0}]
        assert affordance_expand_to_symbol(results) is False

    def test_missing_field_fails(self) -> None:
        results = [{"symbol": "Field", "file": "/p/f.py", "score": 0.9, "content": "x"}]
        assert affordance_expand_to_symbol(results) is False
