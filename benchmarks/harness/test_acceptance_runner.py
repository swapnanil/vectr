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
import subprocess
from pathlib import Path

import pytest

import run_acceptance
from run_acceptance import (
    _symbol_leaf,
    classify_revision_stamp,
    comparable_revision,
    describe_served_revision,
    main,
    resolve_served_revision,
    run_case,
    top_k_absent,
    top_k_contains,
    sorted_by_score,
    scores_in_unit_interval,
    uniform_score_source,
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
# scores_in_unit_interval / uniform_score_source
# (UPG-CORPUS-RESTAMP-SCORE-CASES — the current displayed-score contract:
# bounded [0, 1] and one uniform scale per result set; monotonicity with
# rank order is explicitly NOT required.)
# ---------------------------------------------------------------------------

def _rs(symbol: str, score: float, source: str = "dense") -> dict:
    return {"symbol": symbol, "file": "/p/file.py", "score": score,
            "score_source": source, "content": "x"}


class TestScoresInUnitInterval:
    def test_all_in_range_passes(self) -> None:
        results = [_rs("a", 0.0), _rs("b", 0.5), _rs("c", 1.0)]
        assert scores_in_unit_interval(results) is True

    def test_score_above_one_fails(self) -> None:
        # the historical F12 defect: base_rank * quality + sym_boost, unbounded
        results = [_rs("a", 1.2), _rs("b", 0.9)]
        assert scores_in_unit_interval(results) is False

    def test_negative_score_fails(self) -> None:
        results = [_rs("a", -0.1)]
        assert scores_in_unit_interval(results) is False

    def test_non_monotonic_but_bounded_passes(self) -> None:
        # monotonicity is explicitly not part of this contract
        results = [_rs("a", 0.6), _rs("b", 0.9), _rs("c", 0.7)]
        assert scores_in_unit_interval(results) is True

    def test_empty(self) -> None:
        assert scores_in_unit_interval([]) is True


class TestUniformScoreSource:
    def test_all_reranker_passes(self) -> None:
        results = [_rs("a", 0.9, "reranker"), _rs("b", 0.8, "reranker")]
        assert uniform_score_source(results) is True

    def test_all_dense_passes(self) -> None:
        results = [_rs("a", 0.9, "dense"), _rs("b", 0.8, "dense")]
        assert uniform_score_source(results) is True

    def test_mixed_sources_fails(self) -> None:
        results = [_rs("a", 0.9, "reranker"), _rs("b", 0.8, "dense")]
        assert uniform_score_source(results) is False

    def test_missing_field_defaults_to_dense(self) -> None:
        results = [{"symbol": "a", "file": "/p/f.py", "score": 0.9, "content": "x"},
                   _rs("b", 0.8, "dense")]
        assert uniform_score_source(results) is True

    def test_single_result(self) -> None:
        assert uniform_score_source([_rs("a", 0.9, "reranker")]) is True

    def test_empty(self) -> None:
        assert uniform_score_source([]) is True


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

    def test_top_k_contains_any_of_passes_when_one_candidate_matches(self, monkeypatch) -> None:
        """UPG-HARNESS-TOPK-ANY-OF-EVALUATOR: F56's 'top_k_contains_any_of' is now
        machine-evaluated — passes when the top-k holds AT LEAST ONE candidate."""
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [
                {"symbol": "check_password", "file": "/p/django/contrib/auth/hashers.py"},
            ]},
        )
        case = {"id": "F56", "query": "q", "expect": {"top_k_contains_any_of": {
            "k": 5, "candidates": [
                {"file": "django/contrib/auth/hashers.py", "symbol": "check_password"},
                {"file": "django/contrib/auth/backends.py", "symbol": "ModelBackend.authenticate"},
            ]}}}
        ok, messages = run_case(case, "http://localhost:0")
        assert ok is True
        assert any("top_k_contains_any_of" in m for m in messages)

    def test_top_k_contains_any_of_fails_when_no_candidate_matches(self, monkeypatch) -> None:
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [
                {"symbol": "unrelated", "file": "/p/other.py"},
            ]},
        )
        case = {"id": "F56", "query": "q", "expect": {"top_k_contains_any_of": {
            "k": 5, "candidates": [
                {"file": "django/contrib/auth/hashers.py", "symbol": "check_password"},
                {"file": "django/contrib/auth/backends.py", "symbol": "ModelBackend.authenticate"},
            ]}}}
        ok, _ = run_case(case, "http://localhost:0")
        assert ok is False

    def test_scores_in_unit_interval_and_uniform_score_source_wired(self, monkeypatch) -> None:
        """The restamped F1c/F12 expect shape end-to-end through run_case."""
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [
                {"symbol": "Field.deconstruct", "file": "/p/f.py", "score": 0.95,
                 "score_source": "reranker"},
                {"symbol": "BloomIndex.deconstruct", "file": "/p/f2.py", "score": 0.80,
                 "score_source": "reranker"},
            ]},
        )
        case = {"id": "F1c", "query": "q",
                "expect": {"scores_in_unit_interval": True, "uniform_score_source": True}}
        ok, messages = run_case(case, "http://localhost:0")
        assert ok is True
        assert any("scores_in_unit_interval" in m for m in messages)
        assert any("uniform_score_source" in m for m in messages)

    def test_scores_in_unit_interval_fires_on_unbounded_score(self, monkeypatch) -> None:
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [
                {"symbol": "Field.deconstruct", "file": "/p/f.py", "score": 1.2,
                 "score_source": "dense"},
            ]},
        )
        case = {"id": "x", "query": "q", "expect": {"scores_in_unit_interval": True}}
        ok, _ = run_case(case, "http://localhost:0")
        assert ok is False

    def test_uniform_score_source_fires_on_mixed_set(self, monkeypatch) -> None:
        monkeypatch.setattr(
            run_acceptance, "_post",
            lambda base, path, body: {"results": [
                {"symbol": "a", "file": "/p/f.py", "score": 0.9, "score_source": "reranker"},
                {"symbol": "b", "file": "/p/f.py", "score": 0.5, "score_source": "dense"},
            ]},
        )
        case = {"id": "x", "query": "q", "expect": {"uniform_score_source": True}}
        ok, _ = run_case(case, "http://localhost:0")
        assert ok is False

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


# ---------------------------------------------------------------------------
# corpus_revision_stamp — served-revision resolution, stamp classification and
# main() wiring (UPG-CORPUS-REVISION-STAMP).
#
# The stamp records which witness revision a case's 'expect' was verified
# against so a passing->failing flip is attributable to corpus drift vs
# product regression. Contract pinned here:
#   - only a git SHA (>=7 hex chars) is comparable; "in-repo"/"unknown" are
#     provenance sentinels, never diffed against a workspace;
#   - a dirty working tree is reported DISTINCTLY from clean match/mismatch,
#     because HEAD over a dirty tree does not describe the indexed bytes;
#   - non-git roots, missing dirs and unavailable git degrade to explicit
#     unknown states — never a crash, never a fabricated revision;
#   - every notice is print-only: mismatches must NOT change the exit code.
# ---------------------------------------------------------------------------

# A plausible full SHA that is guaranteed different from any scratch repo's
# HEAD (all-zero-prefix SHAs cannot be committed).
_OTHER_SHA = "0e5a1b2c3d4e5f60718293a4b5c6d7e8f9012345"


def _git_in(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git inside a scratch repo with a hermetic identity/config."""
    return subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=vectr-harness-test",
         "-c", "user.email=harness-test@example.com",
         "-c", "commit.gpgsign=false",
         *args],
        capture_output=True, text=True, check=False,
    )


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a real minimal git repo with one commit; return (path, full sha)."""
    repo = tmp_path / "witness"
    repo.mkdir()
    init = _git_in(repo, "init", "-q")
    assert init.returncode == 0, init.stderr
    (repo / "tracked.txt").write_text("line one\n")
    add = _git_in(repo, "add", ".")
    assert add.returncode == 0, add.stderr
    commit = _git_in(repo, "commit", "-q", "-m", "init")
    assert commit.returncode == 0, commit.stderr
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    return repo, head.stdout.strip()


class TestComparableRevision:
    def test_full_sha_is_comparable(self) -> None:
        sha = "0f1e2d3c4b5a69788796a5b4c3d2e1f001234567"
        assert comparable_revision(sha.upper()) == sha

    def test_abbreviated_sha_is_comparable(self) -> None:
        # The abbreviated GATE-v4 django witness SHA from the lane brief.
        assert comparable_revision("957d0cee71") == "957d0cee71"

    def test_in_repo_sentinel_is_not_comparable(self) -> None:
        assert comparable_revision("in-repo") is None

    def test_unknown_sentinel_is_not_comparable(self) -> None:
        assert comparable_revision("unknown") is None

    def test_missing_none_and_blank_are_not_comparable(self) -> None:
        assert comparable_revision(None) is None
        assert comparable_revision("") is None
        assert comparable_revision("   ") is None

    def test_six_char_hex_is_not_comparable(self) -> None:
        # Below 7 chars an abbreviation is too ambiguous to diff against.
        assert comparable_revision("957d0c") is None

    def test_non_hex_value_is_not_comparable(self) -> None:
        # Historical records cite vectr-trunk revisions like '56be786' in
        # prose; anything that is not pure hex must never be diffed.
        assert comparable_revision("trunk-56be786") is None


class TestResolveServedRevision:
    def test_clean_repo_reports_head_sha(self, tmp_path) -> None:
        repo, sha = _init_repo(tmp_path)
        result = resolve_served_revision(str(repo))
        assert result == {"state": "clean", "revision": sha, "detail": None}

    def test_modified_tracked_file_is_dirty(self, tmp_path) -> None:
        repo, sha = _init_repo(tmp_path)
        (repo / "tracked.txt").write_text("line one\nline two\n")
        result = resolve_served_revision(str(repo))
        assert result["state"] == "dirty"
        assert result["revision"] == sha

    def test_untracked_file_alone_is_dirty(self, tmp_path) -> None:
        # Pins the brief's requirement that UNTRACKED changes also count: a
        # new untracked file can enter the index without any tracked diff.
        repo, sha = _init_repo(tmp_path)
        (repo / "untracked.txt").write_text("stray\n")
        result = resolve_served_revision(str(repo))
        assert result["state"] == "dirty"
        assert result["revision"] == sha

    def test_plain_directory_is_not_a_git_repo(self, tmp_path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        result = resolve_served_revision(str(plain))
        assert result["state"] == "not-a-git-repo"
        assert result["revision"] is None

    def test_missing_directory_is_unknown_not_crash(self, tmp_path) -> None:
        result = resolve_served_revision(str(tmp_path / "does-not-exist"))
        assert result["state"] == "unknown"
        assert result["revision"] is None
        assert "does-not-exist" in result["detail"]

    def test_none_root_is_unknown(self) -> None:
        result = resolve_served_revision(None)
        assert result["state"] == "unknown"
        assert result["revision"] is None

    def test_blank_root_is_unknown(self) -> None:
        assert resolve_served_revision("")["state"] == "unknown"
        assert resolve_served_revision("   ")["state"] == "unknown"

    def test_non_string_root_is_unknown(self) -> None:
        # A daemon sending a malformed workspace_root must degrade, not crash.
        assert resolve_served_revision(12345)["state"] == "unknown"

    def test_no_git_binary_degrades_to_reported_unknown(self, tmp_path, monkeypatch) -> None:
        repo, _sha = _init_repo(tmp_path)
        monkeypatch.setenv("PATH", "")
        result = resolve_served_revision(str(repo))
        assert result["state"] == "no-git-binary"
        assert result["revision"] is None

    def test_status_timeout_reports_cleanliness_unknown(self, tmp_path, monkeypatch) -> None:
        repo, sha = _init_repo(tmp_path)
        real_git = run_acceptance._git

        def hung_status(args, cwd, timeout=15):
            if args and args[0] == "status":
                raise subprocess.TimeoutExpired(cmd="git status", timeout=timeout)
            return real_git(args, cwd, timeout=timeout)

        monkeypatch.setattr(run_acceptance, "_git", hung_status)
        result = resolve_served_revision(str(repo))
        assert result["state"] == "cleanliness-unknown"
        assert result["revision"] == sha

    def test_status_failure_reports_cleanliness_unknown(self, tmp_path, monkeypatch) -> None:
        repo, sha = _init_repo(tmp_path)
        real_git = run_acceptance._git

        def failing_status(args, cwd, timeout=15):
            if args and args[0] == "status":
                return subprocess.CompletedProcess(
                    args=["git"], returncode=128, stdout="",
                    stderr="fatal: unable to read tree")
            return real_git(args, cwd, timeout=timeout)

        monkeypatch.setattr(run_acceptance, "_git", failing_status)
        result = resolve_served_revision(str(repo))
        assert result["state"] == "cleanliness-unknown"
        assert result["revision"] == sha


class TestDescribeServedRevision:
    def test_clean_names_the_sha(self) -> None:
        line = describe_served_revision(
            {"state": "clean", "revision": _OTHER_SHA, "detail": None})
        assert _OTHER_SHA in line
        assert "clean" in line
        assert "DIRTY" not in line

    def test_dirty_is_loud_and_explains_why(self) -> None:
        line = describe_served_revision(
            {"state": "dirty", "revision": _OTHER_SHA, "detail": None})
        assert _OTHER_SHA in line
        assert "DIRTY" in line
        assert "does not describe the indexed bytes" in line

    def test_non_git_states_report_unknown(self) -> None:
        for state in ("not-a-git-repo", "no-git-binary"):
            line = describe_served_revision({"state": state, "revision": None,
                                             "detail": None})
            assert line.startswith("unknown"), state

    def test_generic_unknown_carries_detail(self) -> None:
        line = describe_served_revision(
            {"state": "unknown", "revision": None, "detail": "boom"})
        assert "boom" in line


class TestClassifyRevisionStamp:
    _served_clean = {"state": "clean", "revision": _OTHER_SHA, "detail": None}
    _served_dirty = {"state": "dirty", "revision": _OTHER_SHA, "detail": None}

    def test_clean_exact_match_is_match(self) -> None:
        verdict = classify_revision_stamp(_OTHER_SHA, self._served_clean)
        assert verdict == "match"

    def test_clean_abbreviated_stamp_matches_full_served(self) -> None:
        verdict = classify_revision_stamp(_OTHER_SHA[:10], self._served_clean)
        assert verdict == "match"

    def test_uppercase_stamp_normalizes(self) -> None:
        verdict = classify_revision_stamp(_OTHER_SHA.upper(), self._served_clean)
        assert verdict == "match"

    def test_clean_different_revision_is_mismatch(self) -> None:
        other = "1234567890abcdef1234567890abcdef12345678"
        assert classify_revision_stamp(other, self._served_clean) == "mismatch"

    def test_dirty_same_revision_is_dirty_match_not_match(self) -> None:
        # Matching SHA + dirty tree must NOT count as a verified match: the
        # indexed bytes may differ from the labelled ones.
        assert classify_revision_stamp(_OTHER_SHA, self._served_dirty) == "dirty-match"

    def test_dirty_different_revision_is_mismatch_dirty(self) -> None:
        other = "1234567890abcdef1234567890abcdef12345678"
        assert classify_revision_stamp(other, self._served_dirty) == "mismatch-dirty"

    def test_cleanliness_unknown_same_revision_is_dirty_match(self) -> None:
        served = {"state": "cleanliness-unknown", "revision": _OTHER_SHA,
                  "detail": "git status failed"}
        assert classify_revision_stamp(_OTHER_SHA, served) == "dirty-match"

    def test_comparable_stamp_with_unresolved_served_is_unchecked(self) -> None:
        for served in ({"state": "not-a-git-repo", "revision": None, "detail": None},
                       {"state": "no-git-binary", "revision": None, "detail": None},
                       {"state": "unknown", "revision": None, "detail": None}):
            assert classify_revision_stamp(_OTHER_SHA, served) == "served-unresolved"

    def test_sentinels_and_absence_are_unstamped(self) -> None:
        for stamp in ("in-repo", "unknown", None, "", "trunk-56be786"):
            assert classify_revision_stamp(stamp, self._served_clean) == "unstamped"


class TestMainRevisionStampWiring:
    """End-to-end through main(): HTTP mocked, workspace is a REAL scratch git
    repo at a known SHA. Pins the severity contract: a revision mismatch is
    printed but never changes the exit code."""

    def _write_cases(self, path: Path) -> None:
        cases = [
            # All three share one trivially-passing expect; the variation under
            # test is the corpus_revision_stamp value, not the assertions.
            {"id": "F-rev-mismatch", "query": "q1", "corpus": "django",
             "expect": {"top_k_contains": {"k": 3, "symbol": "Field.deconstruct"}},
             "corpus_revision_stamp": "1234567890abcdef1234567890abcdef12345678"},
            {"id": "F-rev-inrepo", "query": "q2", "corpus": "zig-fixture",
             "expect": {"top_k_contains": {"k": 3, "symbol": "Field.deconstruct"}},
             "corpus_revision_stamp": "in-repo"},
            {"id": "F-rev-unknown", "query": "q3", "corpus": "django",
             "expect": {"top_k_contains": {"k": 3, "symbol": "Field.deconstruct"}},
             "corpus_revision_stamp": "unknown"},
        ]
        with open(path, "w") as fh:
            for c in cases:
                fh.write(json.dumps(c) + "\n")

    def test_mismatch_printed_exit_code_untouched(self, tmp_path, monkeypatch, capsys) -> None:
        repo, sha = _init_repo(tmp_path)
        cases_path = tmp_path / "rev_cases.jsonl"
        self._write_cases(cases_path)

        def fake_get(base: str, path: str) -> dict:
            assert path == "/v1/status"
            return {"indexed_files": 1, "total_chunks": 1, "languages": [],
                    "workspace_root": str(repo)}

        def fake_post(base: str, path: str, body: dict) -> dict:
            assert path == "/v1/search"
            return {"results": [{"file": "/p/f.py", "symbol": "Field.deconstruct",
                                 "score": 0.9}]}

        monkeypatch.setattr(run_acceptance, "_CASES_PATH", cases_path)
        monkeypatch.setattr(run_acceptance, "_get", fake_get)
        monkeypatch.setattr(run_acceptance, "_post", fake_post)

        exit_code = main(["--port", "9999"])
        out = capsys.readouterr().out

        # Header always reports the served workspace state...
        assert f"Served workspace: {repo}" in out
        assert f"Served revision: {sha} (working tree clean)" in out
        # ...the stamped-but-different case gets a per-case notice...
        assert "[REVISION MISMATCH] F-rev-mismatch" in out
        assert "needs re-verification" in out
        # ...and the summary accounts for every non-comparable stamp.
        assert (
            "2 case(s) carry no comparable corpus_revision_stamp" in out
        )
        # Severity contract: informational only — passing run still exits 0.
        assert exit_code == 0
        assert "Results: 3 pass / 0 fail / 0 error / 0 manual / 0 skip  (3 total)" in out
