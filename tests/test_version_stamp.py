"""Tests for agent/version_stamp.py (UPG-CLI-DAEMON-VERSION-SKEW).

compute_version_stamp() is the single function both the daemon
(VectrService, stamped once at startup) and the CLI (recomputed per
invocation) call to produce a directly-comparable version stamp.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from agent.version_stamp import _git_short_sha, compute_version_stamp


def _fake_run(returncode: int = 0, stdout: str = "a1b2c3d\n"):
    def _run(*args, **kwargs):
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        return result
    return _run


class TestGitShortSha:
    def test_returns_sha_when_git_succeeds(self, tmp_path) -> None:
        sha = _git_short_sha(tmp_path, _run_git=_fake_run(0, "a1b2c3d\n"))
        assert sha == "a1b2c3d"

    def test_returns_none_when_git_binary_missing(self, tmp_path) -> None:
        def _raise(*a, **k):
            raise FileNotFoundError("git not found")
        assert _git_short_sha(tmp_path, _run_git=_raise) is None

    def test_returns_none_when_not_a_git_checkout(self, tmp_path) -> None:
        sha = _git_short_sha(tmp_path, _run_git=_fake_run(returncode=128, stdout=""))
        assert sha is None

    def test_returns_none_on_timeout(self, tmp_path) -> None:
        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git", timeout=2)
        assert _git_short_sha(tmp_path, _run_git=_timeout) is None

    def test_returns_none_when_stdout_is_blank(self, tmp_path) -> None:
        sha = _git_short_sha(tmp_path, _run_git=_fake_run(0, "   \n"))
        assert sha is None


class TestComputeVersionStamp:
    def test_includes_short_sha_when_git_available(self) -> None:
        stamp = compute_version_stamp(_run_git=_fake_run(0, "deadbee\n"))
        assert stamp.endswith("+deadbee")

    def test_falls_back_to_bare_package_version_when_git_unavailable(self) -> None:
        def _raise(*a, **k):
            raise FileNotFoundError("no git")
        stamp = compute_version_stamp(_run_git=_raise)
        assert "+" not in stamp
        assert stamp  # non-empty — bare package version (or 0.0.0 fallback)

    def test_stamp_is_deterministic_for_same_inputs(self) -> None:
        run = _fake_run(0, "abc1234\n")
        assert compute_version_stamp(_run_git=run) == compute_version_stamp(_run_git=run)
