"""Outcome derivation cascade.

Deterministic, first-hit-wins over four tiers: (1) content markers over tool
OUTPUT — a versioned, protocol-level table (`agent/markers.yaml`, loaded
here); (2) the exit code, when present; (3) the editor's own `is_error`/
`interrupted` flags, as a weak fallback; (4) `unknown`.

Content markers are primary rather than the exit code because the exit code
lies in the common case: a research finding across 25 real build-tool
invocations in 8 sessions observed 0 exit codes that actually surfaced a
build failure — every failure was only visible in the tool's own printed
output. Marker regexes run over stdout/stderr digests only — R5-sanctioned
(tool OUTPUT classification, the same category as an exit code), never over
prompt/task content.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import yaml

_MARKERS_PATH = Path(__file__).resolve().parent / "markers.yaml"

OUTCOME_VALUES = ("success", "failure", "soft_failure", "interrupted", "unknown")
TERMINATION_VALUES = ("normal", "signal", "timeout", "cancelled", "unknown")


@cache
def _load_markers() -> list[tuple[str, re.Pattern, str]]:
    """[(marker_id, compiled_pattern, kind)], loaded once from
    agent/markers.yaml. Cached — the table is static packaged data, never
    per-request state."""
    direct = _MARKERS_PATH
    if direct.is_file():
        raw = direct.read_text(encoding="utf-8")
    else:
        import importlib.resources as _ilr
        raw = _ilr.files("agent").joinpath("markers.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return [
        (entry["id"], re.compile(entry["pattern"], re.MULTILINE), entry["kind"])
        for entry in data.get("markers", [])
    ]


def match_markers(stdout_digest: str, stderr_digest: str) -> list[tuple[str, str]]:
    """[(marker_id, kind), ...] for every marker whose pattern matches
    anywhere in stdout_digest or stderr_digest. Never raises; empty input
    yields an empty match list."""
    combined = f"{stdout_digest}\n{stderr_digest}"
    return [
        (marker_id, kind)
        for marker_id, pattern, kind in _load_markers()
        if pattern.search(combined)
    ]


def derive_termination(rc: int | None, interrupted: bool) -> str:
    """One of TERMINATION_VALUES. `interrupted` (the editor's own flag) wins
    first as the most direct signal available; absent that, a POSIX
    128+signum (or a raw negative signal number some APIs report) exit code
    indicates the process was killed by a signal rather than exiting
    normally — general Unix convention, not tool-specific."""
    if interrupted:
        return "cancelled"
    if rc is None:
        return "unknown"
    if rc < 0 or rc > 128:
        return "signal"
    return "normal"


def derive_outcome(
    *,
    rc: int | None,
    is_error: bool,
    interrupted: bool,
    stdout_digest: str,
    stderr_digest: str,
) -> dict:
    """Run the full outcome-derivation cascade. Returns:
        {"outcome": str, "termination": str, "markers_matched": list[str]}
    `outcome` is always one of OUTCOME_VALUES; `termination` always one of
    TERMINATION_VALUES; `markers_matched` is the marker ids that fired
    (possibly empty), stored verbatim in the episode row's `markers_json`.
    """
    termination = derive_termination(rc, interrupted)
    matched = match_markers(stdout_digest, stderr_digest)
    marker_ids = [marker_id for marker_id, _ in matched]

    failure_hit = any(kind == "failure" for _, kind in matched)
    success_hit = any(kind == "success" for _, kind in matched)

    if interrupted or termination in ("signal", "cancelled"):
        # A user- or signal-terminated run never becomes an arc endpoint
        # (spec trap (d)): the run didn't complete, so neither the exit
        # code nor content markers get a vote — a Ctrl-C mid-test can
        # print failure-looking output (partial "N failed" summary lines,
        # tracebacks) that would otherwise misclassify this as a real
        # failure/soft_failure and feed a false arc.
        outcome = "interrupted"
    elif failure_hit:
        outcome = "soft_failure" if (rc is None or rc == 0) else "failure"
    elif success_hit:
        outcome = "success"
    elif rc is not None:
        outcome = "success" if rc == 0 else "failure"
    elif is_error:
        outcome = "failure"
    else:
        outcome = "unknown"

    return {"outcome": outcome, "termination": termination, "markers_matched": marker_ids}
