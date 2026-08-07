"""Outcome derivation cascade.

Deterministic, first-hit-wins over five tiers: (1) content markers over tool
OUTPUT — a versioned, protocol-level table (`agent/markers.yaml`, loaded
here); (2) the exit code, when present; (3) the editor's own `is_error`
flag, when populated (a first-order truth claim about this specific call);
(4) `harness_success` — the editor's own routing verdict (which hook event
fired for this tool call), a weaker second-order inference, when the caller
knows it; (5) `unknown`.

Content markers are primary rather than the exit code because the exit code
lies in the common case: a research finding across 25 real build-tool
invocations in 8 sessions observed 0 exit codes that actually surfaced a
build failure — every failure was only visible in the tool's own printed
output. Marker regexes run over stdout/stderr digests only — R5-sanctioned
(tool OUTPUT classification, the same category as an exit code), never over
prompt/task content.

`harness_success` closes a real gap in the exit-code tier: on a real editor
integration (Claude Code), a successfully-routed tool call structurally
NEVER carries an exit-code field at all (rc is absent, not merely 0 — the
G0 live-capture finding), so tier (2) never fires for the large majority of
everyday commands that also don't match any build/test marker (`jq`, `git
status`, `ps`, `gh pr view`, ...) — they fell through every tier straight to
`unknown` even though the tool call itself completed without error. The
caller already knows this because it is the one deciding, from the SAME
discrete hook-event name the harness used to route the call (`PostToolUse`
vs `PostToolUseFailure`), which branch of its own payload-building code ran
— that decision is tool-call structure, not content, the identical category
already sanctioned for `rc`/`termination`. `harness_success=True/False` is
only ever passed when the caller is certain which event fired;
`harness_success=None` (the default) means "this integration doesn't know",
and the cascade behaves exactly as before. Markers are still checked first,
so a piped/quieted build tool that structurally "succeeded" at the harness
level but printed a failure marker (the T2 exit-code-lying finding) is
unaffected — `harness_success` only fills the tier below `is_error`.

`is_error` deliberately sits ABOVE `harness_success`, not below it, even
though both are "weak" signals relative to markers/rc: `harness_success` is
only as trustworthy as the assumption that the editor's event vocabulary
cleanly separates success from failure by event name. That assumption is
verified for Claude Code (G0) but not guaranteed for every integration —
e.g. one whose hook surface documents no distinct failure-path event at all
(a real, already-shipped case: see `main._write_codex_hooks`'s own
docstring) would fire the SAME "success-shaped" event name for both
outcomes, making `harness_success=True` an unreliable inference there. Never
fixed by guessing which editor sent the event (no such field exists in the
payload) — instead, whenever the editor's own `is_error` flag IS populated
for a given call, it is a direct claim about that call and wins outright.
This is a no-op for Claude Code (G0: `is_error` is structurally always
`False` on the verified success path, so this tier never fires there and
`harness_success` decides exactly as before) and only changes behavior for
an integration where `is_error` disagrees with the routing inference.
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
    harness_success: bool | None = None,
) -> dict:
    """Run the full outcome-derivation cascade. Returns:
        {"outcome": str, "termination": str, "markers_matched": list[str]}
    `outcome` is always one of OUTCOME_VALUES; `termination` always one of
    TERMINATION_VALUES; `markers_matched` is the marker ids that fired
    (possibly empty), stored verbatim in the episode row's `markers_json`.

    `harness_success` is tri-state and optional (module docstring): pass
    True/False only when the caller is certain which discrete hook event
    routed this tool call, else leave it None. It sits below `is_error` and
    above `unknown` — weaker than `is_error` (module docstring: a routing
    inference, not a direct per-call truth claim), so an editor-populated
    `is_error=True` always wins over it when both are present and disagree.
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
        # `is_error` wins over `harness_success` when both are present and
        # disagree: it is a first-order truth claim about THIS call (when an
        # integration populates it), while `harness_success` is a
        # second-order inference from routing that only holds under an
        # assumed event vocabulary (module docstring). Provably a no-op for
        # the one integration this was verified against live (G0: the
        # success-path tool_response structurally never carries an is_error-
        # shaped field for Bash, so `is_error` is always False there) — this
        # ordering only changes behavior for an integration whose event name
        # doesn't cleanly separate success from failure but does populate
        # its own error flag.
        outcome = "failure"
    elif harness_success is not None:
        outcome = "success" if harness_success else "failure"
    else:
        outcome = "unknown"

    return {"outcome": outcome, "termination": termination, "markers_matched": marker_ids}
