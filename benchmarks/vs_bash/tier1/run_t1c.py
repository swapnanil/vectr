#!/usr/bin/env python3
"""Tier-1c: two-arm exploration benchmark -- vectr's MCP surface vs the
bash-native toolset (Bash/Grep/Read/Glob), one real model in the loop per
session.

Design: .claude/bench-vectr-vs-bash/plan.md, "Tier 1" section (T1c). Unlike
T1b (single arm, observational tool-choice), T1c is a genuine two-arm A/B:
for each multi-step "explain how <subsystem> works" exploration task, a
`claude -p` session runs TWICE -- once with vectr's MCP tools available
(vectr arm), once with only its native shell/file tools (bash arm, via
`--strict-mcp-config` and an empty `mcpServers` config so no MCP tools exist
at all). The only difference between the two composed invocations is which
tools exist and the session's working directory; the prompt text is byte-
identical (a single shared `_PREAMBLE` + `_PREAMBLE`-following task prompt,
never rewritten per arm) -- honesty design, and per `.claude/HEURISTIC-
DIRECTIVE.md` R5 there is no query-content branching anywhere in this
driver: no arm gets a different preamble, flag set, or parsing path based on
what a task's prompt says.

"Which tools exist" is enforced on disk, not assumed: both arms' fixture
checkouts must pass a guidance-purity preflight (see check_guidance_purity)
proving no vectr-owned config files, no vectr-appended blocks in agent-
instruction files the corpus may ship its own copy of, no vectr hooks in
project `.claude/settings.json` (--strict-mcp-config does NOT disable
hooks), and no vectr git hooks. The vectr arm receives vectr exclusively
through the --mcp-config flag this driver composes.

THIS SCRIPT NEVER SPAWNS `claude -p` UNLESS EXPLICITLY RUN WITHOUT --dry-run.
Automation (subagents, CI, hooks) must only ever call this with --dry-run;
live sessions burn the user's Claude Code quota and are sentinel's call to
make (see README.md in this directory).

Honesty rules (mirrors run_t1b.py / run_tier0.py / plan.md):
  - Tasks in tasks_t1c_camel.jsonl are pre-registered (sentinel-authored)
    and never edited by this driver or by a run to make an outcome look
    better.
  - The preamble never names a specific tool surface (it says "whichever
    tool or tools available to you in this session"), because naming
    vectr's MCP tools would be a lie to the bash arm, which genuinely has
    none available (`--strict-mcp-config` + `{"mcpServers": {}}`). Each
    session discovers its own real tool list from the CLI itself.
  - Tool-choice / token / timing metrics all come from the stream-json
    event stream the `claude` CLI itself emits -- nothing is inferred from
    a task's prompt text.
  - This driver computes NO score or verdict. T1c is scored by sentinel
    review of the recorded final-answer text; the driver only measures and
    records.
  - tiktoken is a proxy tokenizer (same cl100k_base encoder Tier 0 / T1b
    use) -- report deltas, not absolute-token claims.

Usage:
    # Compose + print all 12 (6 tasks x 2 arms) claude -p invocations, run
    # both arms' preflights, spawn nothing. Safe for automation.
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t1c.py --dry-run

    # Real run (sentinel-gated, burns quota) -- one task, one arm at a time:
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t1c.py --tasks C01 --arms vectr
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).parent                  # benchmarks/vs_bash/tier1
_VS_BASH_DIR = _HERE.parent                     # benchmarks/vs_bash
_REPO_ROOT = _VS_BASH_DIR.parent.parent

# T1c is currently a single, pre-registered corpus (camel -- Java, large,
# unfamiliar; see plan.md's T1c description). Not a CLI flag: the task file
# name and gold-answer scope are fixed until a second corpus is registered.
_CORPUS = "camel"

# run_t1b.py already resolves the claude CLI binary and spawns/parses a
# single-prompt stream-json session correctly for this driver family; reuse
# those exact code paths (not a re-implementation) so both T1c arms run
# through the identical spawn/parse machinery T1b already exercises.
sys.path.insert(0, str(_HERE))
from run_t1b import (  # noqa: E402
    CLAUDE_BIN,
    run_claude_session,
    parse_transcript,
    load_jsonl,
    count_tokens,
)

# ---------------------------------------------------------------------------
# Arm-independent constants
# ---------------------------------------------------------------------------

_MCP_SERVER_NAME = "vectr"
_VECTR_TOOL_PREFIX = "mcp__vectr__"
_BASH_NATIVE_TOOLS = ("Bash", "Grep", "Read", "Glob")

# Fixed, arm-independent, task-content-independent preamble. Deliberately
# never names a specific tool surface: the bash arm has zero MCP tools
# (--strict-mcp-config + an empty mcpServers config), so a preamble that
# said "you have vectr's tools" would be untrue for that arm. Each session
# discovers its own real tool list from the CLI itself. This is the ONE
# string object used to compose every invocation, for both arms -- there is
# no per-arm variant.
_PREAMBLE = (
    "You are working in the codebase at your current working directory. Use whichever "
    "tool or tools available to you in this session that you judge best to answer the "
    "question below -- this may include MCP tools and/or your normal shell and file tools "
    "(Bash, Grep, Read, Glob), depending on what this session actually has available. This "
    "is a read-only exploration task -- do not modify any files. The question may require "
    "several tool calls across multiple files to build a complete picture; take as many "
    "steps as you need, then answer once you are confident you understand the full flow.\n\n"
)

# Preflight expectation only -- reported, and a WARNING (never fatal, in
# either mode) if the live daemon's chunk count is below this floor. The
# camel corpus indexes to ~176k chunks; this floor just flags an obviously
# wrong/under-indexed daemon before spending quota against it.
_EXPECTED_MIN_CHUNKS = 100_000

# Fixture guidance-purity model. A session picks up vectr three ways beyond
# the --mcp-config flag this driver controls: (1) files vectr itself CREATES
# in a workspace it has touched, (2) a fenced block vectr APPENDS to agent-
# instruction files the corpus may legitimately ship its own copy of, and
# (3) project `.claude/settings.json` hooks -- `--strict-mcp-config`
# neutralizes a project's MCP *servers* but does NOT disable its *hooks*, so
# a fixture whose settings run `vectr hook ...` injects vectr into a session
# regardless of MCP config. Both arms' fixtures must be clean on all three:
# the bash arm so "no vectr" is true, the vectr arm so the only vectr
# guidance it gets is the MCP toolset itself (arms differ ONLY in available
# tools + cwd -- pre-registered design).

# (1) Files only vectr writes; upstream corpora never ship these, so bare
# presence is a purity failure. Bare `.cursor`/`.vscode`/`.codex` DIRS are
# deliberately NOT checked -- upstream corpora may ship them (camel ships
# `.vscode/`); only the vectr-owned files inside them are checked.
_VECTR_OWNED_PATHS = (
    ".mcp.json",
    ".vectrignore",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
    ".cursor/rules/vectr.mdc",
    ".codex/config.toml",
)

# (2) Files vectr appends a fenced block to. Presence alone proves nothing
# (camel ships its own AGENTS.md plus a CLAUDE.md symlink to it); only
# vectr's literal block marker does. Marker strings match vectr's own
# append-block constants in main.py.
_VECTR_APPENDED_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules")
_VECTR_MD_MARKER = "<!-- vectr-start -->"
_VECTR_COMMENT_MARKER = "# vectr-start"


def _path_present(p: Path) -> bool:
    """True if something exists at `p`, including a (possibly broken)
    symlink -- Path.exists() alone follows symlinks and misses a dangling
    one, which would otherwise hide a purity violation."""
    return p.exists() or p.is_symlink()


def _read_if_present(p: Path) -> str | None:
    """Text of `p` (symlinks followed), None if nothing exists there, "" if
    present but unreadable (e.g. a dangling symlink -- which cannot steer an
    agent, so unreadable == no guidance)."""
    if not _path_present(p):
        return None
    try:
        return p.resolve().read_text(errors="ignore")
    except OSError:
        return ""


def _settings_mentions_vectr(text: str) -> str | None:
    """Violation description if a .claude settings JSON wires vectr in via
    hooks or a project MCP server entry, else None. Falls back to a raw
    substring scan when the JSON does not parse -- an unparseable settings
    file that mentions vectr cannot be proven clean."""
    try:
        data = json.loads(text)
    except ValueError:
        return "unparseable JSON mentioning vectr" if "vectr" in text else None
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries or []:
            for hook in entry.get("hooks") or []:
                if "vectr" in str(hook.get("command", "")):
                    return f"hook command references vectr ({event})"
    if "vectr" in (data.get("mcpServers") or {}):
        return "mcpServers contains a vectr entry"
    return None


def check_guidance_purity(fixture_root: Path, allow: frozenset[str] = frozenset()) -> tuple[bool, str]:
    """All three purity axes above, for either arm's fixture. `allow` names
    _VECTR_OWNED_PATHS entries exempted for a specific arm (the vectr arm
    keeps its `.vectrignore`: gitignore-style indexing patterns the live
    daemon's view of the workspace depends on -- infrastructure with no
    agent-facing guidance, and removing it could churn the built index)."""
    violations = []
    for rel in _VECTR_OWNED_PATHS:
        if rel in allow:
            continue
        if _path_present(fixture_root / rel):
            violations.append(f"vectr-owned file present: {rel}")
    for name in _VECTR_APPENDED_NAMES:
        text = _read_if_present(fixture_root / name)
        if text and _VECTR_MD_MARKER in text:
            violations.append(f"vectr block marker in {name}")
    for rel in (".claude/settings.json", ".claude/settings.local.json"):
        text = _read_if_present(fixture_root / rel)
        if text:
            bad = _settings_mentions_vectr(text)
            if bad:
                violations.append(f"{rel}: {bad}")
    hook_text = _read_if_present(fixture_root / ".git" / "hooks" / "post-commit")
    if hook_text and _VECTR_COMMENT_MARKER in hook_text:
        violations.append("vectr block marker in .git/hooks/post-commit")
    if violations:
        return False, f"purity failure -- {'; '.join(violations)} ({fixture_root})"
    return True, "no vectr-owned files, appended blocks, settings hooks, or git hooks"


# ---------------------------------------------------------------------------
# MCP config generation
# ---------------------------------------------------------------------------

def build_mcp_config_vectr(base_url: str) -> dict:
    """Streamable-http MCP config pointing at the live daemon, same schema
    run_t1b.py's build_mcp_config emits. Combined with --strict-mcp-config
    on the composed command, this is the ONLY MCP server the vectr arm's
    session can see."""
    return {"mcpServers": {_MCP_SERVER_NAME: {"type": "http", "url": f"{base_url}/mcp"}}}


def build_mcp_config_bash() -> dict:
    """Empty MCP config for the bash arm. Combined with --strict-mcp-config,
    this guarantees zero MCP tools exist in that session -- not even ones a
    fixture's own .mcp.json or the user's global config would otherwise
    contribute."""
    return {"mcpServers": {}}


# ---------------------------------------------------------------------------
# Usage-token extraction (additive to run_t1b.parse_transcript, not a
# modification of it -- both arms go through the exact same parser above;
# this only pulls one more field, the terminal result event's token usage,
# out of the same raw event list).
#
# ASSUMPTION (flagged for sentinel verification before any live run, same
# caveat run_t1b.py's parse_transcript docstring carries): mirrors the
# `usage` shape benchmarks/harness/eval_v2.py's usage_from_events already
# parses successfully from persistent multi-turn stream-json sessions
# (`iterations` list of {input_tokens, cache_creation_input_tokens,
# cache_read_input_tokens, output_tokens}, or a flat usage dict without
# `iterations` for a single-shot session). This driver's single-prompt `-p`
# invocation has not itself been live-verified against a live CLI.
# ---------------------------------------------------------------------------

_USAGE_KEYS = (
    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    "output_tokens",
)


def usage_from_events(events: list[dict]) -> dict:
    """Terminal result event's token usage, reported in three input tiers
    (base / cache-creation / cache-read) plus their total. The tiers stay
    separate in the record because the two arms cache differently (the vectr
    arm's MCP tool schemas are cache-heavy) -- a single lumped number would
    distort the arm comparison. When no result event exists, or one exists
    but its usage dict is empty/reshaped, every value is None and
    `usage_unparsed` is True -- zeros are never reported as if measured."""
    result = None
    for ev in events:
        if ev.get("type") == "result":
            result = ev
    unparsed = {k: None for k in _USAGE_KEYS}
    unparsed.update({"total_input_tokens": None, "usage_unparsed": True})
    if result is None:
        return unparsed
    usage = result.get("usage") or {}
    iters = usage.get("iterations", [usage])
    if not any(k in it for it in iters for k in _USAGE_KEYS):
        return unparsed
    vals = {k: sum(it.get(k, 0) for it in iters) for k in _USAGE_KEYS}
    vals["total_input_tokens"] = (
        vals["input_tokens"] + vals["cache_creation_input_tokens"]
        + vals["cache_read_input_tokens"]
    )
    vals["usage_unparsed"] = False
    return vals


# ---------------------------------------------------------------------------
# Per-session summary (per task x arm)
# ---------------------------------------------------------------------------

def summarize_task_result(task: dict, arm: str, events: list[dict], wall_s: float) -> dict:
    """Per-(task, arm) row: full tool-call sequence, counts, vectr_search
    n_results values (explicitly flagged when omitted/defaulted -- the T1a
    n-distribution metric folded into T1c per plan.md), Bash-native tool
    counts, usage tokens in/out, wall time, turn count, and the final answer
    text. Reads only the CLI's own event data -- never re-derives anything
    from the task's prompt text. Computes no score/verdict (T1c is scored by
    sentinel review of `final_answer`)."""
    parsed = parse_transcript(events)
    calls = parsed["tool_calls"]
    sequence = [c["tool"] for c in calls]
    counts = Counter(sequence)

    search_calls = []
    for c in calls:
        if c["tool"] != f"{_VECTR_TOOL_PREFIX}vectr_search":
            continue
        n_results = c["args"].get("n_results")
        search_calls.append({
            "n_results": n_results,
            "n_results_defaulted": n_results is None,
            "query": c["args"].get("query"),
        })

    bash_native_counts = {t: counts.get(t, 0) for t in _BASH_NATIVE_TOOLS}
    usage = usage_from_events(events)
    used_vectr = any(t.startswith(_VECTR_TOOL_PREFIX) for t in sequence)
    used_bash_native = any(t in _BASH_NATIVE_TOOLS for t in sequence)

    return {
        "id": task["id"],
        "arm": arm,
        "category": task.get("category"),
        "subsystem_hint": task.get("subsystem_hint"),
        "prompt": task["prompt"],
        "wall_s": round(wall_s, 2),
        "num_turns": parsed["final"]["num_turns"],
        "cost_usd": parsed["final"]["cost_usd"],
        "reported_duration_ms": parsed["final"]["duration_ms"],
        "is_error": parsed["final"]["is_error"],
        "error": parsed["final"]["error"],
        "tool_sequence": sequence,
        "tool_call_counts": dict(counts),
        "bash_native_counts": bash_native_counts,
        "search_calls": search_calls,
        "usage_input_tokens": usage["total_input_tokens"],
        "usage_input_base_tokens": usage["input_tokens"],
        "usage_cache_creation_tokens": usage["cache_creation_input_tokens"],
        "usage_cache_read_tokens": usage["cache_read_input_tokens"],
        "usage_output_tokens": usage["output_tokens"],
        "usage_unparsed": usage["usage_unparsed"],
        "used_vectr": used_vectr,
        "used_bash_native": used_bash_native,
        "final_answer": parsed["final"]["answer"],
        "final_answer_tokens": count_tokens(parsed["final"]["answer"]),
    }


# ---------------------------------------------------------------------------
# Command composition -- identical shape for both arms; the only inputs
# that vary per arm are the mcp_config_path (which server(s), if any, that
# config declares) and the session cwd (passed separately to
# run_claude_session, not part of the command line).
# ---------------------------------------------------------------------------

def compose_command(task: dict, mcp_config_path: Path, model: str, max_turns: int) -> list[str]:
    full_prompt = _PREAMBLE + task["prompt"]
    return [
        CLAUDE_BIN,
        "-p", full_prompt,
        "--model", model,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        "--mcp-config", str(mcp_config_path),
        "--strict-mcp-config",
        "--max-turns", str(max_turns),
    ]


# ---------------------------------------------------------------------------
# Preflights (read-only only -- never starts/stops/restarts anything)
# ---------------------------------------------------------------------------

def check_fixture_root_exists(fixture_root: Path) -> tuple[bool, str]:
    """Generic sanity check (mirrors run_t1b.py's un-named fixture-root
    check in main()): the fixture only needs to exist as a session cwd on a
    real spawn, so this is deliberately separate from preflight_vectr's
    daemon-only checks -- a live daemon says nothing about whether the local
    checkout used as cwd is actually present."""
    if _path_present(fixture_root):
        return True, f"{fixture_root} exists"
    return False, f"fixture root does not exist: {fixture_root}"


def preflight_vectr(base: str) -> tuple[bool, str]:
    """GET /v1/health and /v1/status against the live daemon. Read-only."""
    try:
        with urllib.request.urlopen(f"{base}/v1/health", timeout=10) as r:
            health = json.load(r)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return False, f"cannot reach {base}/v1/health: {exc}"
    if health.get("status") != "ok":
        return False, f"{base}/v1/health reports status={health.get('status')!r}"
    try:
        with urllib.request.urlopen(f"{base}/v1/status", timeout=10) as r:
            status = json.load(r)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return False, f"cannot reach {base}/v1/status: {exc}"
    indexed_files = status.get("indexed_files")
    total_chunks = int(status.get("total_chunks") or 0)
    msg = f"{base} healthy -- indexed_files={indexed_files} total_chunks={total_chunks}"
    if total_chunks < _EXPECTED_MIN_CHUNKS:
        msg += (f" (WARNING: below the expected floor of {_EXPECTED_MIN_CHUNKS} for the "
                f"{_CORPUS} corpus -- not fatal, just report the count as instructed)")
    return True, msg


def preflight_bash(fixture_root: Path) -> tuple[bool, str]:
    """Fixture root exists, is a git repo, and passes all guidance-purity
    axes (vectr-owned files, appended block markers, settings hooks, git
    hooks). Read-only -- filesystem stat/reads + a read-only git rev-parse,
    nothing that mutates the fixture."""
    if not _path_present(fixture_root):
        return False, f"fixture root does not exist: {fixture_root}"
    try:
        subprocess.run(
            ["git", "-C", str(fixture_root), "rev-parse", "--is-inside-work-tree"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"fixture root is not a git repo: {fixture_root} ({exc})"
    ok, msg = check_guidance_purity(fixture_root)
    if not ok:
        return False, msg
    return True, f"{fixture_root} OK -- git repo, {msg}"


def corpus_sha(fixture_root: Path) -> str | None:
    """The fixture checkout's HEAD sha (read-only), None if unresolvable.
    Recorded per arm and asserted equal across arms -- two fixtures at
    different corpus commits would silently compare different code."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(fixture_root), "rev-parse", "HEAD"],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tier-1c two-arm exploration benchmark")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--arms", default="vectr,bash",
                         help="Comma-separated arms to run: vectr, bash, or both (default)")
    parser.add_argument("--tasks", default=None, help="Comma-separated task ids to run; default = all")
    parser.add_argument("--fixture-root", default=None,
                         help="Override the vectr-arm corpus checkout root (default tmp/poc-camel)")
    parser.add_argument("--bash-fixture-root", default=None,
                         help="Override the bash-arm corpus checkout root "
                              "(default ~/.cache/vectr/bench/poc-camel-bash)")
    parser.add_argument("--model", default="sonnet", help="Model alias/id passed to --model (cheaper-models rule: sonnet by default)")
    parser.add_argument("--max-turns", type=int, default=30,
                         help="Exploration tasks are multi-step. 12 proved insufficient in the "
                              "C01 smoke: both arms hit the ceiling mid-exploration and produced "
                              "empty final answers (is_error=True, ~200 output tokens).")
    parser.add_argument("--timeout", type=int, default=600, help="Per-session wall-clock timeout, seconds")
    parser.add_argument("--smoke", action="store_true", help="Tag the results file as a smoke test")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compose + print every claude -p invocation and run both arms' "
                              "preflights; spawn nothing. The only mode automation may use.")
    args = parser.parse_args(argv)

    valid_arms = {"vectr", "bash"}
    requested_arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in requested_arms if a not in valid_arms]
    if bad or not requested_arms:
        print(f"ERROR: --arms must be a comma-separated subset of {sorted(valid_arms)}, got {args.arms!r}",
              file=sys.stderr)
        return 2

    base = f"http://{args.host}:{args.port}"
    tasks_path = _HERE / f"tasks_t1c_{_CORPUS}.jsonl"
    vectr_fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"poc-{_CORPUS}"
    bash_fixture_root = (
        Path(args.bash_fixture_root) if args.bash_fixture_root
        else Path.home() / ".cache" / "vectr" / "bench" / f"poc-{_CORPUS}-bash"
    )
    fixture_root_by_arm = {"vectr": vectr_fixture_root, "bash": bash_fixture_root}

    if not tasks_path.exists():
        print(f"ERROR: no task file: {tasks_path}", file=sys.stderr)
        return 1
    tasks = load_jsonl(tasks_path)
    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks else sorted(tasks.keys())
    unknown = [t for t in task_ids if t not in tasks]
    if unknown or not task_ids:
        print(f"ERROR: unknown/empty task id(s) {unknown or args.tasks!r} -- "
              f"valid ids in {tasks_path.name}: {sorted(tasks.keys())}", file=sys.stderr)
        return 2

    print("=" * 88)
    print(f"Tier-1c two-arm exploration -- corpus={_CORPUS}  arms={requested_arms}")
    print(f"vectr daemon: {base}  vectr-arm fixture: {vectr_fixture_root}")
    print(f"bash-arm fixture: {bash_fixture_root}")
    print(f"model={args.model}  max_turns={args.max_turns}  timeout={args.timeout}s  dry_run={args.dry_run}")
    print(f"Running {len(task_ids)} task(s) x {len(requested_arms)} arm(s): {task_ids}")
    print("=" * 88)

    # ------------------------------------------------------------------
    # Preflights -- one or more checks per requested arm. A failure is a
    # loudly-printed, non-fatal warning in --dry-run (dry-run's job is to
    # compose + print every invocation regardless); it is fatal in live
    # mode. vectr arm gets three checks (fixture exists as a session cwd,
    # daemon healthy, guidance purity -- its `.vectrignore` exempted as
    # daemon indexing infrastructure); bash arm gets one combined check
    # (fixture exists, is a git repo, full guidance purity -- preflight_bash).
    # ------------------------------------------------------------------
    checks_by_arm: dict[str, list[tuple[str, bool, str]]] = {a: [] for a in requested_arms}
    if "vectr" in requested_arms:
        checks_by_arm["vectr"].append(("vectr-fixture", *check_fixture_root_exists(vectr_fixture_root)))
        checks_by_arm["vectr"].append(("vectr-daemon", *preflight_vectr(base)))
        checks_by_arm["vectr"].append(
            ("vectr-purity", *check_guidance_purity(vectr_fixture_root, allow=frozenset({".vectrignore"}))))
    if "bash" in requested_arms:
        checks_by_arm["bash"].append(("bash-fixture", *preflight_bash(bash_fixture_root)))

    for arm in requested_arms:
        for label, ok, msg in checks_by_arm[arm]:
            status_tag = "OK" if ok else "FAIL"
            print(f"[preflight:{label}] {status_tag}: {msg}")
            if not ok:
                if args.dry_run:
                    print(f"  [dry-run] WARNING: {label} check failed (not fatal for --dry-run "
                          f"-- only needed for a real spawn)")
                else:
                    print(f"ERROR: {label} check failed: {msg}", file=sys.stderr)
                    return 1

    # Corpus commit per arm: recorded in the aggregate and asserted equal
    # across arms -- fixtures at different corpus commits would silently
    # compare different code. Fatal in live mode, warning in --dry-run.
    corpus_sha_by_arm = {a: corpus_sha(fixture_root_by_arm[a]) for a in requested_arms}
    print(f"corpus sha by arm: {corpus_sha_by_arm}")
    resolved_shas = {s for s in corpus_sha_by_arm.values() if s}
    if len(resolved_shas) > 1:
        msg = f"corpus sha mismatch across arms: {corpus_sha_by_arm}"
        if args.dry_run:
            print(f"  [dry-run] WARNING: {msg}")
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 1

    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, text=True,
    ).strip()
    out_dir = _REPO_ROOT / "results" / "vectr-vs-bash" / _CORPUS / sha / "t1c"
    out_dir.mkdir(parents=True, exist_ok=True)

    mcp_config_path_by_arm: dict[str, Path] = {}
    if "vectr" in requested_arms:
        p = out_dir / "mcp_config_vectr.json"
        p.write_text(json.dumps(build_mcp_config_vectr(base), indent=2) + "\n")
        mcp_config_path_by_arm["vectr"] = p
        print(f"MCP config (vectr arm) written: {p}")
    if "bash" in requested_arms:
        p = out_dir / "mcp_config_bash.json"
        p.write_text(json.dumps(build_mcp_config_bash(), indent=2) + "\n")
        mcp_config_path_by_arm["bash"] = p
        print(f"MCP config (bash arm, empty) written: {p}")

    results = []
    n_composed = 0
    for arm in requested_arms:
        fixture_root = fixture_root_by_arm[arm]
        mcp_config_path = mcp_config_path_by_arm[arm]
        for tid in task_ids:
            task = tasks[tid]
            cmd = compose_command(task, mcp_config_path, args.model, args.max_turns)
            n_composed += 1

            print(f"\n[{tid}/{arm}] category={task.get('category')}  "
                  f"subsystem_hint={task.get('subsystem_hint')!r}")
            print(f"  cwd: {fixture_root}")
            print(f"  cmd: {shlex.join(cmd)}")

            if args.dry_run:
                print(f"  [dry-run] not spawned")
                continue

            # NEVER reached by automation -- real spawn, burns Claude Code quota.
            session = run_claude_session(cmd, cwd=str(fixture_root), timeout_s=args.timeout)
            if session.get("timed_out"):
                print(f"  [WARN] session timed out after {args.timeout}s")
            if session["returncode"] not in (0, None):
                print(f"  [WARN] claude exited {session['returncode']}: "
                      f"{session['stderr_tail'][-300:]}")
            if not session["events"]:
                print(f"  [WARN] session produced ZERO events -- spawn or auth failure, "
                      f"not agent behavior. stderr: {session['stderr_tail'][-300:]!r}")

            stamp = time.strftime("%Y%m%dT%H%M%S")
            transcript_path = out_dir / f"{tid}_{arm}_{stamp}.jsonl"
            with open(transcript_path, "w") as fh:
                for ev in session["events"]:
                    fh.write(json.dumps(ev) + "\n")
            print(f"  transcript: {transcript_path}")

            summary = summarize_task_result(task, arm, session["events"], session["wall_s"])
            summary["transcript_path"] = str(transcript_path.relative_to(_REPO_ROOT))
            results.append(summary)

            print(f"  tools: {summary['tool_sequence']}")
            print(f"  used_vectr={summary['used_vectr']}  used_bash_native={summary['used_bash_native']}  "
                  f"usage_in={summary['usage_input_tokens']}  usage_out={summary['usage_output_tokens']}  "
                  f"turns={summary['num_turns']}  wall_s={summary['wall_s']}")

    if args.dry_run:
        print("\n" + "=" * 88)
        print(f"[dry-run] preflights done, {n_composed} command(s) composed "
              f"({len(task_ids)} task(s) x {len(requested_arms)} arm(s)), 0 sessions spawned.")
        print("=" * 88)
        return 0

    # ------------------------------------------------------------------
    # Aggregate + write SHA-stamped results
    # ------------------------------------------------------------------
    tool_choice_totals_by_arm: dict[str, dict[str, int]] = {a: {} for a in requested_arms}
    tasks_scored_by_arm: dict[str, int] = {a: 0 for a in requested_arms}
    tasks_used_vectr_by_arm: dict[str, int] = {a: 0 for a in requested_arms}
    tasks_used_bash_native_by_arm: dict[str, int] = {a: 0 for a in requested_arms}
    for r in results:
        a = r["arm"]
        tasks_scored_by_arm[a] += 1
        if r["used_vectr"]:
            tasks_used_vectr_by_arm[a] += 1
        if r["used_bash_native"]:
            tasks_used_bash_native_by_arm[a] += 1
        for tool, n in r["tool_call_counts"].items():
            tool_choice_totals_by_arm[a][tool] = tool_choice_totals_by_arm[a].get(tool, 0) + n

    aggregate = {
        "corpus": _CORPUS,
        "corpus_sha_by_arm": corpus_sha_by_arm,
        "vectr_sha": sha,
        "smoke_test": bool(args.smoke),
        "port": args.port,
        "model": args.model,
        "max_turns": args.max_turns,
        "arms": requested_arms,
        "task_ids": task_ids,
        "results": results,
        "tool_choice_totals_by_arm": tool_choice_totals_by_arm,
        "tasks_scored_by_arm": tasks_scored_by_arm,
        "tasks_used_vectr_by_arm": tasks_used_vectr_by_arm,
        "tasks_used_bash_native_by_arm": tasks_used_bash_native_by_arm,
    }
    stamp = time.strftime("%Y%m%dT%H%M%S")
    tag = "smoke" if args.smoke else "run"
    out_path = out_dir / f"t1c_{tag}_{stamp}.json"
    out_path.write_text(json.dumps(aggregate, indent=2))

    print("\n" + "=" * 88)
    print(f"tasks_scored_by_arm: {tasks_scored_by_arm}")
    print(f"tool_choice_totals_by_arm: {tool_choice_totals_by_arm}")
    print(f"Results written: {out_path}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
