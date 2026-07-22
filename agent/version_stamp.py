"""Shared source-version stamp for the daemon and the CLI (UPG-CLI-DAEMON-VERSION-SKEW).

Most vectr output is rendered daemon-side, and the CLI shim always imports
the working tree fresh at invocation time — so after a source upgrade a
long-running daemon can silently keep serving old rendering while the CLI
looks current. `compute_version_stamp()` gives both sides the exact same
value (package version, plus a short git SHA when running from a git
checkout) computed the exact same way, so a CLI invocation can detect that
the daemon it's talking to predates the code it was just built from.
"""
from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_NAME = "vectr"


def _git_short_sha(
    repo_root: Path,
    *,
    _run_git: Callable[..., "subprocess.CompletedProcess"] | None = None,
) -> str | None:
    """Short git SHA for `repo_root`, or None when git isn't available, the
    directory isn't a git checkout, or the call fails/times out for any
    other reason. All failure is swallowed here — the caller degrades to
    the bare package version rather than raising."""
    run = _run_git or subprocess.run
    try:
        # `git rev-parse` walks UP from cwd, so an installed copy (whose
        # repo_root is a site-packages parent) would otherwise pick up any
        # enclosing checkout's HEAD — e.g. a package manager's own prefix
        # repo — and stamp an unrelated SHA. Only a repo_root that is itself
        # the top of a checkout (`.git` dir, or file for linked worktrees)
        # counts as running from source.
        if not (repo_root / ".git").exists():
            return None
        result = run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def compute_version_stamp(*, _run_git: Callable[..., "subprocess.CompletedProcess"] | None = None) -> str:
    """`<package-version>` or `<package-version>+<short-sha>` when run from a
    git checkout. Called identically by `VectrService` (stamped once at
    daemon startup) and the CLI (recomputed on every invocation) so the two
    stamps are directly comparable."""
    try:
        pkg_version = version(_PACKAGE_NAME)
    except PackageNotFoundError:
        pkg_version = "0.0.0"
    sha = _git_short_sha(_REPO_ROOT, _run_git=_run_git)
    return f"{pkg_version}+{sha}" if sha else pkg_version
