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

import json

import pytest

import run_acceptance
from run_acceptance import (
    _symbol_leaf,
    main,
    run_case,
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

    # --- UPG-ACCEPTANCE-HARNESS-F35-CRASH: file-only spec (no 'symbol' key) ---

    def test_file_only_spec_absent_passes(self) -> None:
        """F35's real recorded shape: {"k": 5, "file": "..."} with no
        'symbol' key at all. Must not crash and must correctly report the
        file as absent when no result's file matches."""
        results = [_r("SomeClass.method", 0.9)]
        assert top_k_absent(results, k=5, file="django/views/templates/i18n_catalog.js") is True

    def test_file_only_spec_absent_fires(self) -> None:
        results = [{"symbol": "", "file": "django/views/templates/i18n_catalog.js",
                    "score": 0.6, "content": "x"}]
        assert top_k_absent(results, k=5, file="django/views/templates/i18n_catalog.js") is False

    def test_file_only_spec_outside_k_ignored(self) -> None:
        results = [
            _r("SomeClass.method", 0.9),
            {"symbol": "", "file": "django/views/templates/i18n_catalog.js",
             "score": 0.5, "content": "x"},
        ]
        assert top_k_absent(results, k=1, file="django/views/templates/i18n_catalog.js") is True

    def test_neither_symbol_nor_file_is_vacuously_true(self) -> None:
        """Matches a few pre-existing corpus entries (F19/F50/F52) that pair
        a real top_k_contains assertion with a no-op top_k_absent(symbol=None)
        — preserved as an always-True vacuous check, not an error, so those
        cases keep evaluating exactly as before."""
        assert top_k_absent([_r("x")], k=3) is True
        assert top_k_absent([], k=3) is True


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


# ---------------------------------------------------------------------------
# run_case — manual bucket (UPG-ACCEPTANCE-HARNESS-F35-CRASH)
#
# A case whose 'expect' dict has no key this harness evaluates (a free-text
# 'notes'-only entry, or an unimplemented assertion primitive) must be
# reported as a distinct "manual" result, never silently counted as a pass.
# ---------------------------------------------------------------------------

class TestRunCaseManualBucket:
    def test_notes_only_case_is_manual_not_pass(self, monkeypatch) -> None:
        monkeypatch.setattr(run_acceptance, "_post", lambda base, path, body: {"results": []})
        case = {"id": "x", "query": "q", "expect": {"notes": "free text only"}}
        ok, messages = run_case(case, "http://localhost:0")
        assert ok is None
        assert any("MANUAL" in m for m in messages)

    def test_unimplemented_primitive_only_is_manual_not_pass(self, monkeypatch) -> None:
        """e.g. F56's 'top_k_contains_any_of' — a real corpus key this
        harness does not (yet) implement an evaluator for."""
        monkeypatch.setattr(run_acceptance, "_post", lambda base, path, body: {"results": []})
        case = {"id": "x", "query": "q",
                "expect": {"top_k_contains_any_of": [{"symbol": "a"}, {"symbol": "b"}]}}
        ok, messages = run_case(case, "http://localhost:0")
        assert ok is None

    def test_recognized_assertion_still_returns_bool(self, monkeypatch) -> None:
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [{"symbol": "Field.deconstruct", "file": "/p/f.py"}]},
        )
        case = {"id": "x", "query": "q",
                "expect": {"top_k_contains": {"k": 3, "symbol": "Field.deconstruct"}}}
        ok, _ = run_case(case, "http://localhost:0")
        assert ok is True


# ---------------------------------------------------------------------------
# main() — a malformed corpus entry must never truncate the rest of the run;
# the summary must count pass/fail/error/manual separately
# (UPG-ACCEPTANCE-HARNESS-F35-CRASH).
#
# HTTP is mocked at the _get/_post module-function level — zero daemon calls.
# ---------------------------------------------------------------------------

def _fake_get(base: str, path: str) -> dict:
    assert path == "/v1/status"
    return {"indexed_files": 1, "total_chunks": 1, "languages": []}


def _fake_post(base: str, path: str, body: dict) -> dict:
    assert path == "/v1/search"
    return {
        "results": [
            {"file": "/p/fields/__init__.py", "symbol": "Field.deconstruct",
             "score": 1.0, "symbol_start_line": 10, "symbol_end_line": 20},
        ]
    }


class TestMainErrorHandlingAndBuckets:
    def test_malformed_case_reported_as_error_and_run_continues(
        self, tmp_path, monkeypatch, capsys,
    ) -> None:
        cases = [
            {"id": "good-case", "query": "q1",
             "expect": {"top_k_contains": {"k": 3, "symbol": "Field.deconstruct"}}},
            # Malformed: top_k_absent with no 'k' key at all -> KeyError deep
            # inside run_case, which main() must catch rather than crash on.
            {"id": "malformed-case", "query": "q2",
             "expect": {"top_k_absent": {"symbol": "read"}}},
            # F35's real recorded shape (file-only, no 'symbol' key at all) —
            # must evaluate cleanly, not crash, now that top_k_absent
            # accepts file= as an independent criterion.
            {"id": "f35-style-case", "query": "q3",
             "expect": {"top_k_absent": {"k": 5, "file": "some/other/file.js"}}},
            # No recognized assertion key -> MANUAL, not silently PASS.
            {"id": "manual-case", "query": "q4",
             "expect": {"notes": "free text only, nothing to check"}},
        ]
        cases_path = tmp_path / "mini_cases.jsonl"
        with open(cases_path, "w") as fh:
            for c in cases:
                fh.write(json.dumps(c) + "\n")

        monkeypatch.setattr(run_acceptance, "_CASES_PATH", cases_path)
        monkeypatch.setattr(run_acceptance, "_get", _fake_get)
        monkeypatch.setattr(run_acceptance, "_post", _fake_post)

        exit_code = main(["--port", "9999"])
        out = capsys.readouterr().out

        assert "[ERROR] malformed-case" in out
        assert "KeyError" in out
        # the run must continue past the malformed case to evaluate the rest...
        assert "f35-style-case" in out
        assert "[MANUAL] manual-case" in out
        assert "good-case" in out
        assert "Results: 2 pass / 0 fail / 1 error / 1 manual / 0 skip  (4 total)" in out
        # an error must fail the gate (non-zero exit), not be swallowed
        assert exit_code == 1
