"""Tests for agent/outcome.py (memoization-l1-capture-design §2.4).

The outcome-derivation cascade: content markers over tool OUTPUT (never
prompt content — R5-sanctioned) beat the exit code, which beats the weak
is_error/interrupted fallback, which beats "unknown". Markers are primary
because the exit code lies in the common case (T2 research finding: 0
observed BUILD FAILURE exit codes across 25 real maven invocations)."""
from __future__ import annotations

from agent.outcome import derive_outcome, derive_termination, match_markers


class TestMarkersBeatExitCode:
    def test_failure_marker_with_rc_zero_is_soft_failure(self):
        result = derive_outcome(
            rc=0, is_error=False, interrupted=False,
            stdout_digest="[INFO] BUILD FAILURE", stderr_digest="",
        )
        assert result["outcome"] == "soft_failure"
        assert "maven.build_failure" in result["markers_matched"]

    def test_failure_marker_with_no_rc_is_soft_failure(self):
        result = derive_outcome(
            rc=None, is_error=False, interrupted=False,
            stdout_digest="BUILD FAILURE", stderr_digest="",
        )
        assert result["outcome"] == "soft_failure"

    def test_failure_marker_with_nonzero_rc_is_failure(self):
        result = derive_outcome(
            rc=1, is_error=False, interrupted=False,
            stdout_digest="BUILD FAILURE", stderr_digest="",
        )
        assert result["outcome"] == "failure"

    def test_success_marker_with_no_rc_is_success(self):
        result = derive_outcome(
            rc=None, is_error=False, interrupted=False,
            stdout_digest="5 passed in 1.2s", stderr_digest="",
        )
        assert result["outcome"] == "success"


class TestExitCodeFallback:
    def test_rc_zero_no_markers_is_success(self):
        result = derive_outcome(
            rc=0, is_error=False, interrupted=False,
            stdout_digest="ok", stderr_digest="",
        )
        assert result["outcome"] == "success"

    def test_rc_nonzero_no_markers_is_failure(self):
        result = derive_outcome(
            rc=1, is_error=False, interrupted=False,
            stdout_digest="", stderr_digest="",
        )
        assert result["outcome"] == "failure"


class TestSignalTermination:
    def test_rc_137_is_interrupted_and_signal(self):
        result = derive_outcome(
            rc=137, is_error=False, interrupted=False,
            stdout_digest="", stderr_digest="",
        )
        assert result["outcome"] == "interrupted"
        assert result["termination"] == "signal"


class TestWeakFallbacks:
    def test_is_error_flag_no_rc_no_markers(self):
        result = derive_outcome(
            rc=None, is_error=True, interrupted=False,
            stdout_digest="", stderr_digest="",
        )
        assert result["outcome"] == "failure"

    def test_interrupted_flag_no_rc_no_markers(self):
        result = derive_outcome(
            rc=None, is_error=False, interrupted=True,
            stdout_digest="", stderr_digest="",
        )
        assert result["outcome"] == "interrupted"
        assert result["termination"] == "cancelled"

    def test_nothing_present_is_unknown(self):
        result = derive_outcome(
            rc=None, is_error=False, interrupted=False,
            stdout_digest="", stderr_digest="",
        )
        assert result["outcome"] == "unknown"
        assert result["termination"] == "unknown"


class TestTerminationDerivation:
    def test_normal_exit(self):
        assert derive_termination(0, False) == "normal"

    def test_no_rc_no_interrupted(self):
        assert derive_termination(None, False) == "unknown"

    def test_interrupted_wins_over_rc(self):
        assert derive_termination(0, True) == "cancelled"

    def test_signal_range_rc(self):
        assert derive_termination(137, False) == "signal"


class TestMarkerMatchingOverTextOnly:
    def test_matches_in_stderr_too(self):
        matched = match_markers("", "error: could not compile")
        assert any(kind == "failure" for _, kind in matched)

    def test_no_match_returns_empty(self):
        assert match_markers("nothing interesting here", "") == []
