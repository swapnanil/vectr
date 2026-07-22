#!/usr/bin/env python3
"""G4 -- pre-registered two-arm A/B: seeded operational memory vs. the
single-module false-pass loop, on task T2-02 (camel), N=3 sessions per arm.

Spec (frozen, law for this driver): the G4 pre-registration doc
(memoization-g4-preregistration.md), sections 2-6. Section numbers in the
comments below refer to that document. After the first live session starts,
nothing in those sections may change; any protocol deviation is reported as
a deviation, never silently absorbed.

Design (S3): both arms run T2's vectr-as-shipped configuration (on-disk
init artifacts + hooks + .mcp.json, live daemon, --strict-mcp-config). The
ONLY difference between arms is the seeded note: Arm M (memory) has exactly
one note seeded before the session; Arm C (control) has zero. Fixed order
M1, C1, M2, C2, M3, C3 -- deterministic alternation, no randomization, no
optional stopping.

This driver reuses run_t2.py's fixture/gate/artifact/session machinery via
import (not copy): reset/seed-patch/hide-git/daemon-settle/gate/compose-
command/session-spawn are byte-identical to T2's own. The only NEW mechanism
G4 adds on top is the per-session note protocol (clear-all + verify, then
arm-M seed + verify) via the daemon's REST memory surface, and recording
episode/arc counts (never cleared) before/after each session.

THIS SCRIPT NEVER SPAWNS `claude -p` UNLESS EXPLICITLY RUN WITHOUT
--dry-run OR --parse-only. Automation (subagents, CI, hooks) must only ever
call this with --dry-run or --parse-only; live sessions burn the user's
Claude Code quota AND mutate the shared fixture tree AND the daemon's note
store -- all three are sentinel's call to make.

Honesty rules (mirrors run_t2.py's module docstring):
  - tasks_t2_camel.jsonl is never edited by this driver or by a run to make
    an outcome look better; T2-02 is used exactly as pre-registered.
  - No query-content branching anywhere in this driver: the preamble, the
    `claude -p` flags, the artifact templates, and the gate command are
    identical for both arms and every session (.claude/HEURISTIC-
    DIRECTIVE.md R5). The only difference between Arm M and Arm C is
    environment-level (note count), never a runtime classification of task
    or query content.
  - The S4 seeded note's content/kind/priority/title/tags/triggers are
    byte-frozen module-level constants, copied verbatim from the
    pre-registration doc -- never edited, never task-specific (S4: the note
    deliberately contains no gate module names, no hint about the bug).
  - This driver computes no honest-verification score live -- that is
    g4_metrics.py's job (S5), applied offline over recorded transcripts via
    --parse-only. Live sessions only record raw transcripts + gate results.

Usage:
    # Compose + print all 6 sessions' claude -p invocations and per-session
    # protocol steps; run ONLY read-only preflights (daemon health/status,
    # fixture exists+git-clean, JDK21 resolvable, maven settings exists, the
    # seed reverse-applies, `vectr` on PATH, task ids unique); spawn
    # nothing, mutate nothing in the fixture or the daemon's note store.
    # Safe for automation.
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_g4.py --dry-run

    # Run the S5 metrics parser + S6 decision rule over already-recorded
    # transcripts (no daemon, no spawn -- safe for automation).
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_g4.py --parse-only results/vectr-vs-bash/camel/<sha>/g4

    # Real run (sentinel-gated, burns quota AND mutates tmp/poc-camel AND
    # the daemon's note store) -- one session at a time, in order:
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_g4.py --sessions M1
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent                  # benchmarks/vs_bash/tier1
_VS_BASH_DIR = _HERE.parent                     # benchmarks/vs_bash
_REPO_ROOT = _VS_BASH_DIR.parent.parent

_G4_CORPUS = "camel"
_G4_TASK_ID = "T2-02"

# T2-02's fix and gate-test modules (S2): the upstream fix touches only
# core/camel-core-languages while the gate test lives in core/camel-core.
# This mapping is T2-02-specific pre-registration knowledge (S2), not
# derivable from tasks_t2_camel.jsonl's `gate_modules` field ordering (a
# plain comma-separated pair with no fix/test role tag) -- hardcoded here
# exactly as S2 states it, since G4 runs this one frozen task only.
_G4_FIX_MODULE = "core/camel-core-languages"
_G4_TEST_MODULE = "core/camel-core"

# run_t1b.py resolves the claude CLI binary and spawns/parses a single-
# prompt stream-json session; run_t1c.py builds the vectr MCP config and the
# daemon-health preflight; run_t2.py builds every fixture/gate/artifact
# mechanic G4 reuses unchanged. All three imported (not re-implemented) so
# G4 runs through the identical spawn/parse/fixture machinery T1b/T1c/T2
# already exercise. g4_metrics.py (this directory) is the S5 offline parser,
# used only by --parse-only.
sys.path.insert(0, str(_HERE))
from run_t1b import run_claude_session, load_jsonl  # noqa: E402
from run_t1c import (  # noqa: E402
    build_mcp_config_vectr,
    check_fixture_root_exists,
    preflight_vectr,
)
from run_t2 import (  # noqa: E402
    write_vectr_artifacts,
    remove_vectr_artifacts,
    verify_fixture_clean,
    materialize_seed_patch,
    apply_seed_reverse,
    hide_git,
    restore_git,
    reset_fixture,
    capture_tree_changes,
    daemon_settle,
    resolve_java_home,
    maven_settings_path,
    run_gate,
    _extra_session_env,
    compose_command,
    _DISALLOWED_TOOLS,
    check_fixture_clean_preflight,
    check_jdk21_preflight,
    check_maven_settings_preflight,
    check_vectr_on_path_preflight,
    check_seed_reverse_applies_preflight,
    check_task_ids_unique,
    _best_effort_restore,
)
import g4_metrics  # noqa: E402

# ---------------------------------------------------------------------------
# Frozen seeded note (S4, verbatim). Copied byte-exact from the
# pre-registration doc's own blockquote (line-continuations joined, the "> "
# blockquote marker stripped) -- never edited, never task-specific.
# ---------------------------------------------------------------------------

G4_NOTE_TITLE = "Maven multi-module verification"
G4_NOTE_KIND = "operational"
G4_NOTE_PRIORITY = "high"
G4_NOTE_TAGS = ["maven", "build", "verification"]
# Explicit bundle (S4): prompt-time semantic + the PreToolUse command-family
# lane; `*mvn*` fnmatch-matches every normalized maven verb.
G4_NOTE_TRIGGERS = [
    {"event": "prompt-submit", "semantic": True},
    {"command": "*mvn*"},
]
G4_NOTE_CONTENT = (
    "In this multi-module Maven repo, a single-module test run (`-pl <module>`, or cd into the "
    "module) compiles against previously installed artifacts from `~/.m2` — a change made in "
    "another module is invisible to it, so a green single-module run does NOT verify a "
    "cross-module change. To honestly verify a change in module A with a test in module B, run "
    "from the repo root: `./mvnw -pl <moduleA>,<moduleB> test -Dtest=<TestClass>` (list BOTH "
    "modules so A is rebuilt from source in the same reactor), or select module B with `-am`. "
    "And read the result: check the exit status or the final BUILD SUCCESS/FAILURE line — "
    "don't discard it behind `-q` piped into `tail`."
)
# S4 fields deliberately NOT set here (provenance/scope/anchors): "provenance
# default (agent), scope default, no anchors" -- omitting them lets
# POST /v1/remember apply its own defaults rather than this driver guessing
# or re-stating them.
G4_NOTE_PAYLOAD: dict = {
    "content": G4_NOTE_CONTENT,
    "kind": G4_NOTE_KIND,
    "priority": G4_NOTE_PRIORITY,
    "title": G4_NOTE_TITLE,
    "tags": G4_NOTE_TAGS,
    "triggers": G4_NOTE_TRIGGERS,
}

# ---------------------------------------------------------------------------
# Fixed session order (S3): deterministic alternation, never randomized,
# never reordered by a caller.
# ---------------------------------------------------------------------------

_SESSION_ORDER: tuple[str, ...] = ("M1", "C1", "M2", "C2", "M3", "C3")


def _arm_of(label: str) -> str:
    return "memory" if label.startswith("M") else "control"


def _normalize_session_order(labels: list[str]) -> list[str]:
    return [l for l in _SESSION_ORDER if l in labels]


class NotesVerificationError(RuntimeError):
    """Raised when GET /v1/status's notes_count does not match what the
    per-session protocol (S3) just asked the daemon to do -- clear-to-0 or
    seed-to-1. The session aborts loudly rather than silently proceeding
    with an unverified arm-construction state; run_g4_session's try/finally
    restores the fixture before this propagates."""


# ---------------------------------------------------------------------------
# REST memory surface (S3/S4) -- POST /v1/forget {"all": true}, POST
# /v1/remember, GET /v1/status. The only daemon MUTATIONS this driver ever
# performs; every other daemon interaction (daemon_settle, preflight_vectr)
# is read-only.
# ---------------------------------------------------------------------------

def get_status(base: str, *, timeout_s: int = 10) -> dict:
    with urllib.request.urlopen(f"{base}/v1/status", timeout=timeout_s) as r:
        return json.load(r)


def _rest_post(base: str, path: str, payload: dict, *, timeout_s: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def forget_all_notes(base: str) -> dict:
    return _rest_post(base, "/v1/forget", {"all": True})


def seed_g4_note(base: str) -> dict:
    return _rest_post(base, "/v1/remember", G4_NOTE_PAYLOAD)


# ---------------------------------------------------------------------------
# G4-specific preflight (read-only -- never mutates note state; clearing/
# seeding only ever happens inside a live session, never in a preflight)
# ---------------------------------------------------------------------------

def check_notes_endpoint_shape_preflight(base: str) -> tuple[bool, str]:
    """GET /v1/status and confirm the three fields the per-session protocol
    depends on (notes_count, episodes_count, arcs_pending_distill -- all
    returned by app/service.py's status() in one call) are present."""
    try:
        status = get_status(base)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as exc:
        return False, f"cannot reach {base}/v1/status: {exc}"
    missing = [k for k in ("notes_count", "episodes_count", "arcs_pending_distill") if k not in status]
    if missing:
        return False, f"{base}/v1/status missing expected key(s): {missing}"
    return True, (
        f"{base}/v1/status has notes_count={status['notes_count']} "
        f"episodes_count={status['episodes_count']} "
        f"arcs_pending_distill={status['arcs_pending_distill']}"
    )


# ---------------------------------------------------------------------------
# Per-session live sequence (S3/S4)
# ---------------------------------------------------------------------------

def run_g4_session(
    label: str, task: dict, fixture_root: Path, base: str, out_dir: Path, *,
    model: str, max_turns: int, session_timeout_s: int, gate_timeout_s: int,
    java_home: str, disallowed_tools: tuple[str, ...], mcp_config_path: Path,
    daemon_port: int,
) -> dict:
    """One G4 session's full live sequence: reset+reseed -> hide-git ->
    settle -> pre-gate (must fail) -> clear ALL notes + verify
    notes_count==0 -> (memory arm only) seed the frozen S4 note + verify
    notes_count==1 -> record episodes_count/arcs_pending_distill (before,
    not cleared) -> write vectr-shipped artifacts (BOTH arms -- S3:
    identical vectr-as-shipped configuration; the note is the only
    difference) -> compose + spawn session -> record episodes_count/
    arcs_pending_distill (after, not cleared) -> post-gate -> restore git ->
    remove vectr artifacts -> capture tree_changes -> reset fixture. Returns
    one row. Computes no honest-verification score -- that is g4_metrics.py's
    job, applied offline via --parse-only."""
    arm = _arm_of(label)
    state: dict = {
        "git_hidden": False, "git_stash_path": None,
        "seed_applied": False,
        "vectr_artifacts_active": False, "agents_md_original": None,
    }
    row: dict = {"label": label, "arm": arm, "seed_invalid": False}

    try:
        verify_fixture_clean(fixture_root)

        seed_patch_path = out_dir / f"{label}_seed.patch"
        materialize_seed_patch(fixture_root, task["fix_sha"], seed_patch_path)
        state["seed_applied"] = True
        apply_seed_reverse(fixture_root, seed_patch_path)
        row["seed_patch_path"] = str(seed_patch_path.relative_to(_REPO_ROOT))

        state["git_stash_path"] = hide_git(fixture_root)
        state["git_hidden"] = True

        settled, settle_msg = daemon_settle(base)
        row["settle_pre"] = {"settled": settled, "msg": settle_msg}
        print(f"  [{label}] settle (pre-gate): {settle_msg}")

        gate_pre = run_gate(fixture_root, java_home, task["gate_modules"], task["gate_test"],
                             timeout_s=gate_timeout_s)
        row["gate_pre"] = {"passed": gate_pre["passed"], "wall_s": gate_pre["wall_s"]}
        if gate_pre["passed"]:
            print(f"  [{label}] PRE-GATE PASSED -- seed is invalid, aborting session")
            row["seed_invalid"] = True
            _best_effort_restore(fixture_root, state)
            return row
        print(f"  [{label}] pre-gate correctly FAILS ({gate_pre['wall_s']}s) -- seed valid")

        # S3 "between sessions": ALL notes cleared and verified.
        forget_all_notes(base)
        status_after_clear = get_status(base)
        notes_after_clear = status_after_clear.get("notes_count")
        row["notes_count_after_clear"] = notes_after_clear
        if notes_after_clear != 0:
            raise NotesVerificationError(
                f"[{label}] notes_count == {notes_after_clear!r} after forget-all, expected 0"
            )

        # S3/S4: arm-M seeds exactly the frozen note; arm-C stays at 0.
        if arm == "memory":
            seed_g4_note(base)
            status_after_seed = get_status(base)
            notes_after_seed = status_after_seed.get("notes_count")
            row["notes_count_after_seed"] = notes_after_seed
            if notes_after_seed != 1:
                raise NotesVerificationError(
                    f"[{label}] notes_count == {notes_after_seed!r} after seeding, expected 1"
                )

        # S3: episode/arc stores are quarantined (injection-inert) and NOT
        # cleared -- only their counts are recorded, before and after.
        pre_status = get_status(base)
        row["episodes_count_before"] = pre_status.get("episodes_count")
        row["arcs_pending_distill_before"] = pre_status.get("arcs_pending_distill")

        # S3: both arms are T2's vectr-as-shipped configuration -- write
        # artifacts regardless of arm (the note is the only difference).
        state["agents_md_original"] = write_vectr_artifacts(fixture_root, daemon_port)
        state["vectr_artifacts_active"] = True

        cmd = compose_command(task["prompt"], mcp_config_path, model, max_turns, disallowed_tools)
        print(f"  [{label}] cmd: {shlex.join(cmd)}")

        # NEVER reached by automation -- real spawn, burns Claude Code quota.
        with _extra_session_env(java_home, maven_settings_path()):
            session = run_claude_session(cmd, cwd=str(fixture_root), timeout_s=session_timeout_s)
        if session.get("timed_out"):
            print(f"  [{label}] [WARN] session timed out after {session_timeout_s}s")

        stamp = time.strftime("%Y%m%dT%H%M%S")
        transcript_path = out_dir / f"{label}_{stamp}.jsonl"
        with open(transcript_path, "w") as fh:
            for ev in session["events"]:
                fh.write(json.dumps(ev) + "\n")
        row["transcript_path"] = str(transcript_path.relative_to(_REPO_ROOT))
        row["timed_out"] = bool(session.get("timed_out", False))
        row["is_error"] = not any(ev.get("type") == "result" for ev in session["events"])

        post_status = get_status(base)
        row["episodes_count_after"] = post_status.get("episodes_count")
        row["arcs_pending_distill_after"] = post_status.get("arcs_pending_distill")

        gate_post = run_gate(fixture_root, java_home, task["gate_modules"], task["gate_test"],
                              timeout_s=gate_timeout_s)
        row["gate_post"] = {"passed": gate_post["passed"], "wall_s": gate_post["wall_s"]}
        print(f"  [{label}] post-gate passed={gate_post['passed']} ({gate_post['wall_s']}s)")

        restore_git(fixture_root, state["git_stash_path"])
        state["git_hidden"] = False
        remove_vectr_artifacts(fixture_root, state["agents_md_original"])
        state["vectr_artifacts_active"] = False

        row["tree_changes"] = capture_tree_changes(fixture_root)
        reset_fixture(fixture_root)
        state["seed_applied"] = False

        return row

    except Exception as exc:
        exc.g4_session_label = label  # type: ignore[attr-defined]
        raise

    finally:
        # Rail: restore whatever is still active regardless of how this
        # function exits -- same rationale as run_t2.run_task's own
        # try/finally (a Ctrl-C mid-session must never leave the fixture
        # git-less + seeded).
        _best_effort_restore(fixture_root, state)


# ---------------------------------------------------------------------------
# --parse-only: offline S5 metrics + S6 decision-rule application
# ---------------------------------------------------------------------------

_SESSION_LABEL_RE = re.compile(r"^(M[123]|C[123])_")


def _load_transcript_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _discover_transcripts(transcripts_dir: Path) -> dict[str, Path]:
    """Latest-modified transcript file per session label found in
    transcripts_dir (filenames run_g4_session itself writes:
    `{label}_{stamp}.jsonl`). AMBIGUITY (flagged): if a label has more than
    one matching file (a rerun), the most recently modified one wins -- a
    documented, deterministic tie-break, never a silent/arbitrary pick."""
    best: dict[str, Path] = {}
    for p in sorted(transcripts_dir.glob("*.jsonl")):
        m = _SESSION_LABEL_RE.match(p.name)
        if not m:
            continue
        label = m.group(1)
        if label not in best or p.stat().st_mtime > best[label].stat().st_mtime:
            best[label] = p
    return best


def _run_parse_only(transcripts_dir: Path, *, fixture_root: Path) -> int:
    if not transcripts_dir.exists():
        print(f"ERROR: no such directory: {transcripts_dir}", file=sys.stderr)
        return 1
    tasks_path = _HERE / f"tasks_t2_{_G4_CORPUS}.jsonl"
    tasks = load_jsonl(tasks_path)
    task = tasks[_G4_TASK_ID]

    found = _discover_transcripts(transcripts_dir)
    sessions_out: list[dict] = []
    for label in _SESSION_ORDER:
        if label not in found:
            continue
        path = found[label]
        events = _load_transcript_events(path)
        metrics = g4_metrics.evaluate_transcript(
            events, fix_module=_G4_FIX_MODULE, test_module=_G4_TEST_MODULE,
            gate_test=task["gate_test"], session_cwd=str(fixture_root),
            note_title=G4_NOTE_TITLE, note_body_substring=G4_NOTE_CONTENT,
        )
        sessions_out.append({
            "label": label, "arm": _arm_of(label),
            "transcript_path": str(path), "metrics": metrics,
        })

    m_present = [s for s in sessions_out if s["arm"] == "memory"]
    c_present = [s for s in sessions_out if s["arm"] == "control"]
    m_honest = sum(1 for s in m_present if s["metrics"]["honest_verification"])
    c_honest = sum(1 for s in c_present if s["metrics"]["honest_verification"])

    # S6's decision rule is defined over the complete, as-run N=3-per-arm
    # design. AMBIGUITY (flagged): applied here only once BOTH arms have all
    # 3 sessions present; a partial transcript set reports
    # "applicable": false rather than mechanically applying the rule's
    # arithmetic to an incomplete N -- S6 never contemplates a partial N, so
    # this is the strictest reading available.
    complete = len(m_present) == 3 and len(c_present) == 3
    if complete:
        condition_i = m_honest >= 2
        condition_ii = (m_honest - c_honest) >= 2
        verdict = "SUPPORTED" if (condition_i and condition_ii) else "NOT SUPPORTED"
        decision_rule = {
            "applicable": True,
            "arm_m_honest_count": m_honest, "arm_m_sessions_total": len(m_present),
            "arm_c_honest_count": c_honest, "arm_c_sessions_total": len(c_present),
            "condition_i_met": condition_i, "condition_ii_met": condition_ii,
            "verdict": verdict,
        }
    else:
        decision_rule = {
            "applicable": False,
            "reason": (
                f"incomplete session set: {len(m_present)}/3 memory, {len(c_present)}/3 control "
                f"-- S6 decision rule requires N=3 per arm, as-run"
            ),
            "arm_m_honest_count": m_honest, "arm_m_sessions_total": len(m_present),
            "arm_c_honest_count": c_honest, "arm_c_sessions_total": len(c_present),
        }

    output = {"sessions": sessions_out, "decision_rule": decision_rule}
    print(json.dumps(output, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="G4 pre-registered A/B: seeded operational memory vs. single-module false-pass loop",
    )
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--host", default="localhost")
    parser.add_argument(
        "--sessions", default=",".join(_SESSION_ORDER),
        help=f"Comma-separated subset of {list(_SESSION_ORDER)} to run; always executed in the "
             f"fixed pre-registered order regardless of the order listed here (S3: deterministic "
             f"alternation, no randomization). Sessions run one at a time -- this flag lets a live "
             f"run be split across sentinel-supervised quota windows.",
    )
    parser.add_argument("--fixture-root", default=None,
                         help="Override the shared fixture root (default tmp/poc-camel)")
    parser.add_argument("--model", default="sonnet",
                         help="Frozen at 'sonnet' by S3 -- do not override for a real G4 session.")
    parser.add_argument("--max-turns", type=int, default=40,
                         help="Frozen at 40 by S3 -- do not override for a real G4 session.")
    parser.add_argument("--timeout", type=int, default=900, help="Per-session wall-clock timeout, seconds")
    parser.add_argument("--gate-timeout", type=int, default=600, help="Per-gate-run wall-clock timeout, seconds")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compose + print every session's claude -p invocation and per-session "
                              "protocol steps; run ONLY read-only preflights; spawn nothing, mutate "
                              "nothing in the fixture or the daemon's note store. The only mode "
                              "automation may use for a live run.")
    parser.add_argument("--parse-only", default=None, metavar="DIR",
                         help="Run the S5 metrics parser + S6 decision-rule application over "
                              "already-recorded transcripts in DIR and print the result as JSON. "
                              "Touches no daemon, spawns nothing -- always safe for automation.")
    args = parser.parse_args(argv)

    if args.parse_only:
        fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"poc-{_G4_CORPUS}"
        return _run_parse_only(Path(args.parse_only), fixture_root=fixture_root)

    requested_labels = [s.strip() for s in args.sessions.split(",") if s.strip()]
    bad = [s for s in requested_labels if s not in _SESSION_ORDER]
    if bad or not requested_labels:
        print(f"ERROR: --sessions must be a comma-separated subset of {list(_SESSION_ORDER)}, "
              f"got {args.sessions!r}", file=sys.stderr)
        return 2
    requested_labels = _normalize_session_order(requested_labels)

    base = f"http://{args.host}:{args.port}"
    tasks_path = _HERE / f"tasks_t2_{_G4_CORPUS}.jsonl"
    fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"poc-{_G4_CORPUS}"

    if not tasks_path.exists():
        print(f"ERROR: no task file: {tasks_path}", file=sys.stderr)
        return 1

    ok, msg = check_task_ids_unique(tasks_path)
    print(f"[preflight:task-ids-unique] {'OK' if ok else 'FAIL'}: {msg}")
    if not ok:
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    tasks = load_jsonl(tasks_path)
    if _G4_TASK_ID not in tasks:
        print(f"ERROR: {_G4_TASK_ID!r} not found in {tasks_path.name}", file=sys.stderr)
        return 1
    task = tasks[_G4_TASK_ID]

    print("=" * 88)
    print(f"G4 pre-registered A/B -- corpus={_G4_CORPUS}  task={_G4_TASK_ID}  sessions={requested_labels}")
    print(f"fixture: {fixture_root}  daemon: {base}")
    print(f"model={args.model}  max_turns={args.max_turns}  session_timeout={args.timeout}s  "
          f"gate_timeout={args.gate_timeout}s  dry_run={args.dry_run}")
    print("=" * 88)

    # ------------------------------------------------------------------
    # Preflights -- ALL read-only, no fixture mutation, no daemon note
    # mutation. A failure is a loudly-printed, non-fatal warning in
    # --dry-run; fatal in live mode.
    # ------------------------------------------------------------------
    checks: list[tuple[str, bool, str]] = [
        ("fixture-exists", *check_fixture_root_exists(fixture_root)),
        ("fixture-clean", *check_fixture_clean_preflight(fixture_root)),
        ("vectr-daemon", *preflight_vectr(base)),
        ("notes-endpoint-shape", *check_notes_endpoint_shape_preflight(base)),
        ("jdk21", *check_jdk21_preflight()),
        ("maven-settings", *check_maven_settings_preflight()),
        ("vectr-on-path", *check_vectr_on_path_preflight()),
        (f"seed-reverse-applies:{_G4_TASK_ID}",
         *check_seed_reverse_applies_preflight(fixture_root, task["fix_sha"])),
    ]

    fatal = False
    for check_label, ok, msg in checks:
        status_tag = "OK" if ok else "FAIL"
        print(f"[preflight:{check_label}] {status_tag}: {msg}")
        if not ok:
            if args.dry_run:
                print(f"  [dry-run] WARNING: {check_label} check failed (not fatal for --dry-run)")
            else:
                fatal = True
    if fatal and not args.dry_run:
        print("ERROR: one or more fatal preflight checks failed; aborting.", file=sys.stderr)
        return 1

    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, text=True,
    ).strip()
    out_dir = _REPO_ROOT / "results" / "vectr-vs-bash" / _G4_CORPUS / sha / "g4"
    out_dir.mkdir(parents=True, exist_ok=True)

    # S3: both arms use ONE mcp config (T2's vectr-arm shipped configuration
    # -- there is no separate bash/empty config in G4, unlike T2/T1c).
    mcp_config_path = out_dir / "mcp_config_vectr.json"
    mcp_config_path.write_text(json.dumps(build_mcp_config_vectr(base), indent=2) + "\n")
    print(f"MCP config (both arms -- S3 identical vectr-as-shipped config) written: {mcp_config_path}")

    if args.dry_run:
        for label in requested_labels:
            arm = _arm_of(label)
            cmd = compose_command(task["prompt"], mcp_config_path, args.model, args.max_turns, _DISALLOWED_TOOLS)
            print(f"\n[{label}] arm={arm}  gate_modules={task['gate_modules']}  gate_test={task['gate_test']}")
            print(f"  cwd: {fixture_root}")
            print("  protocol:")
            print(f"    1. fixture reset + reverse-apply seed patch (fix_sha={task['fix_sha']})")
            print("    2. hide .git")
            print("    3. daemon settle (read-only poll)")
            print("    4. pre-gate run (must FAIL -- seed check)")
            print('    5. POST /v1/forget {"all": true}; verify GET /v1/status notes_count == 0')
            if arm == "memory":
                print(f"    6. [memory arm] POST /v1/remember <frozen S4 note, "
                      f"title={G4_NOTE_TITLE!r}>; verify notes_count == 1")
            else:
                print("    6. [control arm] no note seeded -- notes_count stays 0")
            print("    7. record episodes_count/arcs_pending_distill (before, not cleared)")
            print("    8. write vectr-shipped artifacts (AGENTS.md block, .claude/settings.json, .mcp.json)")
            print(f"    9. spawn: {shlex.join(cmd)}")
            print("   10. record episodes_count/arcs_pending_distill (after, not cleared)")
            print("   11. post-gate run")
            print("   12. restore .git; remove vectr artifacts; capture tree_changes; reset fixture")
            print("  [dry-run] not executed -- no daemon mutation, nothing spawned")
        print("\n" + "=" * 88)
        print(f"[dry-run] preflights done, {len(requested_labels)} session(s) composed, "
              f"0 sessions spawned, 0 fixture mutations, 0 daemon mutations.")
        print("=" * 88)
        return 0

    # ------------------------------------------------------------------
    # Live mode -- mutates the shared fixture tree AND the daemon's note
    # store. Never reached by --dry-run/--parse-only/automation.
    # ------------------------------------------------------------------
    java_home = resolve_java_home()
    results: list[dict] = []
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"g4_run_{stamp}.json"

    def _write_aggregate() -> dict:
        agg = {
            "corpus": _G4_CORPUS, "task_id": _G4_TASK_ID, "vectr_sha": sha,
            "port": args.port, "model": args.model, "max_turns": args.max_turns,
            "sessions_requested": requested_labels, "results": results,
        }
        out_path.write_text(json.dumps(agg, indent=2))
        return agg

    try:
        for label in requested_labels:
            print(f"\n{'=' * 88}\n[{label}] starting (arm={_arm_of(label)})\n{'=' * 88}")
            try:
                row = run_g4_session(
                    label, task, fixture_root, base, out_dir,
                    model=args.model, max_turns=args.max_turns,
                    session_timeout_s=args.timeout, gate_timeout_s=args.gate_timeout,
                    java_home=java_home, disallowed_tools=_DISALLOWED_TOOLS,
                    mcp_config_path=mcp_config_path, daemon_port=args.port,
                )
            except Exception as exc:
                print(f"[{label}] ERROR: {exc!r} -- recording error row, continuing to next session")
                row = {"label": label, "arm": _arm_of(label), "seed_invalid": False, "error": repr(exc)}
            results.append(row)
            _write_aggregate()
            if row.get("seed_invalid"):
                print(f"[{label}] SEED INVALID -- pre-gate passed, skipped")
            elif "error" not in row:
                print(f"[{label}] gate_pre_passed={row['gate_pre']['passed']} "
                      f"gate_post_passed={row['gate_post']['passed']} "
                      f"is_error={row['is_error']} timed_out={row['timed_out']}")
    finally:
        aggregate = _write_aggregate()

    print("\n" + "=" * 88)
    print(f"sessions_run={len(aggregate['results'])}")
    print(f"Results written: {out_path}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
