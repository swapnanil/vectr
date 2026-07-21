#!/usr/bin/env python3
"""Tier-2: two-arm seeded-bugfix benchmark on the camel corpus -- vectr as
shipped vs. bash-native, one real model in the loop per (task, arm) session.

Design: .claude/bench-vectr-vs-bash/plan.md, "Tier 2" section. Unlike T1c
(exploration, read-only, sentinel-scored on prose quality), T2 measures
end-to-end bug fixing against an objective, pre-registered gate: 4 seeded-bug
tasks (tasks_t2_camel.jsonl), each derived by reverse-applying a real upstream
fix commit's NON-TEST hunks (`git show <fix_sha> -- ':!*src/test*'`) to plant
a bug whose own commit test (kept at its fixed version) is the pass/fail gate.
THIS DRIVER COMPUTES NO SCORE beyond recording gate results (`gate_pre`,
`gate_post` per arm) -- sentinel reviews the recorded diffs.

Both arms share ONE fixture tree (tmp/poc-camel, live-indexed by the vectr
daemon on --port, default 8800) and run sequentially per task, vectr arm
first (fixed order -- documented caveat: the bash arm always runs against a
tree the vectr arm has already reset, never the other way around):

  - vectr arm: SHIPPED configuration, not bare MCP -- T1c's headline finding
    (README.md, "T1c results") was that bare `--mcp-config` alone produces
    near-zero tool usage (2/6 sessions). This driver reproduces what `vectr
    init --hooks` actually leaves on disk: an AGENTS.md-appended guidance
    block (CLAUDE.md is a symlink to it in this fixture), `.claude/
    settings.json` hook wiring (SessionStart/UserPromptSubmit/PreToolUse/
    PreCompact -> `vectr hook <event>`), and `.mcp.json` declaring the vectr
    server -- written before the session, removed after (AGENTS.md restored
    to its exact original bytes; the other two, being vectr-owned files that
    should never pre-exist upstream, are deleted). The `--mcp-config` flag
    passed on the command line points at the same daemon `.mcp.json` names,
    plus `--strict-mcp-config` so vectr is the only MCP server visible.
  - bash arm: pure tree, zero vectr artifacts, an empty `{"mcpServers": {}}`
    --mcp-config + --strict-mcp-config so no MCP tools exist at all.

`.vectrignore` is never touched in either arm (daemon-indexing infrastructure
the fixture already carries, orthogonal to agent-facing guidance).

Each task's seed patch is materialized under this run's own results
directory (results/vectr-vs-bash/camel/<vectr-sha>/t2/<task_id>_seed.patch)
-- gitignored, outside the fixture tree entirely, and never on a path a
session could read. This intentionally diverges from
~/.cache/vectr/bench/maven-settings.xml (a fixed, shared, non-per-run
location used for the Maven settings file, which every task/arm reads but
never writes) -- the seed patch is per-run output, not shared toolchain
config, so it belongs with this run's other recorded artifacts.

THIS SCRIPT NEVER SPAWNS `claude -p` UNLESS EXPLICITLY RUN WITHOUT --dry-run.
Automation (subagents, CI, hooks) must only ever call this with --dry-run;
live sessions burn the user's Claude Code quota AND mutate the shared
fixture tree -- both are sentinel's call to make (see README.md).

Honesty rules (mirrors run_t1b.py / run_t1c.py / plan.md):
  - Tasks in tasks_t2_camel.jsonl are pre-registered and never edited by this
    driver or by a run to make a gate outcome look better.
  - No query-content branching anywhere in this driver: the preamble, the
    `claude -p` flags, the artifact templates, and the gate command are
    identical for every task and both arms regardless of what a task's
    prompt says (.claude/HEURISTIC-DIRECTIVE.md R5). The only per-task
    variation is the prompt text, fix_sha, gate_modules, and gate_test --
    all task-authoring data, never a runtime classification.
  - The gate command, its settings.xml, and the seed commit's own test are
    NEVER modified to make anything pass.
  - This driver computes NO score beyond gate pass/fail -- sentinel reviews
    the recorded tree_changes diffs.

Usage:
    # Compose + print every claude -p invocation, run ONLY read-only
    # preflights (daemon health/status, fixture exists+git-clean, JDK21
    # resolvable, maven settings exists, each seed reverse-applies, `vectr`
    # on PATH, task ids unique); spawn nothing, mutate nothing in the
    # fixture. Safe for automation.
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t2.py --dry-run

    # Real run (sentinel-gated, burns quota AND mutates tmp/poc-camel) --
    # one task, one arm at a time:
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t2.py --tasks T2-01 --arms vectr
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent                  # benchmarks/vs_bash/tier1
_VS_BASH_DIR = _HERE.parent                     # benchmarks/vs_bash
_REPO_ROOT = _VS_BASH_DIR.parent.parent

# T2 is currently a single, pre-registered corpus (camel), same convention as
# run_t1c.py's _CORPUS -- not a CLI flag until a second corpus is registered.
_CORPUS = "camel"

# run_t1b.py resolves the claude CLI binary and spawns/parses a single-prompt
# stream-json session; run_t1c.py builds the two-arm MCP configs, the usage-
# token extractor, and the per-(task, arm) summary shape. Reused unchanged
# (not re-implemented) so T2 runs through the identical spawn/parse/summarize
# machinery T1b/T1c already exercise.
sys.path.insert(0, str(_HERE))
from run_t1b import CLAUDE_BIN, run_claude_session, load_jsonl  # noqa: E402
from run_t1c import (  # noqa: E402
    build_mcp_config_vectr,
    build_mcp_config_bash,
    summarize_task_result as _t1c_summarize_task_result,
    check_fixture_root_exists,
    preflight_vectr,
)

# agent.prompt_templates.load_template is a lightweight, stdlib-only,
# @cache-decorated file reader (agent/prompt_templates.py) -- safe to import
# directly, unlike importing main.py itself (which inspects sys.argv at
# module scope and pulls in the full CLI's dependency surface).
sys.path.insert(0, str(_REPO_ROOT))
from agent.prompt_templates import load_template  # noqa: E402

# ---------------------------------------------------------------------------
# Arm-independent constants
# ---------------------------------------------------------------------------

# Fixed, task-content-independent, arm-independent bugfix preamble. Never
# names a specific tool surface (the bash arm has zero MCP tools) -- each
# session discovers its own real tool list from the CLI itself, same design
# as run_t1c.py's _PREAMBLE.
_PREAMBLE = (
    "You are working in the codebase at your current working directory. Use whichever "
    "tool or tools available to you in this session that you judge best -- this may "
    "include MCP tools and/or your normal shell and file tools (Bash, Grep, Read, Glob, "
    "Edit, Write), depending on what this session actually has available. There is a "
    "real, reproducible bug in this codebase, described below. Find the root cause and "
    "fix it directly in the implementation source so the project's existing test suite "
    "passes. Do not weaken, skip, delete, or otherwise modify any test file to make it "
    "pass -- the fix must be a genuine correction to the implementation. Make your "
    "changes directly in the files of this working tree.\n\n"
)

# Session-command flags kept out of reach of tools this benchmark should
# never exercise (network research is not part of fixing a seeded local bug;
# both arms have the exact same restriction).
_DISALLOWED_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")

# Design requirement (module docstring): vectr arm always runs before bash
# within a task -- the bash arm must always run against a tree the vectr arm
# has already reset, never the reverse. `--arms` is honored as a SET of
# which arms to run, never as an ORDER; this is the single source of truth
# for the actual run order, applied both at the CLI layer (main) and inside
# run_task itself (so the invariant holds for any caller, not just the CLI).
_ARM_ORDER: tuple[str, ...] = ("vectr", "bash")


def _normalize_arm_order(arms: list[str]) -> list[str]:
    return [a for a in _ARM_ORDER if a in arms]

# ---------------------------------------------------------------------------
# vectr's on-disk artifact templates -- rendered from the SAME template files
# main.py's writers read (agent/templates/*), not re-authored copy, so this
# driver's rendering stays in lockstep with whatever CLAUDE.md/AGENTS.md body
# vectr actually ships. The splice/block-marker mechanics below are copied
# from main.py's own `_splice`/`_render_claude_md`/`_make_vectr_block`
# (verified against main.py, not guessed) rather than imported from it,
# because main.py is the CLI entry point (it inspects sys.argv at module
# scope and pulls in the full server dependency surface) -- unsuitable for
# library-style import from a benchmark driver.
# ---------------------------------------------------------------------------

_VECTR_BLOCK_START = "<!-- vectr-start -->"
_VECTR_BLOCK_END = "<!-- vectr-end -->"
_VECTR_BLOCK_RE = re.compile(
    r"\n*<!-- vectr-start -->.*?<!-- vectr-end -->\n?", re.DOTALL,
)

_CLAUDE_MD_TEMPLATE = load_template("claude_md.md")
_SESSION_START_GUIDANCE_DEFAULT = load_template("session_start_guidance_default.txt")
_MCP_JSON_TEMPLATE = load_template("mcp.json.template")


def _splice(template: str, placeholder: str, text: str) -> str:
    """Same behavior as main.py's `_splice`: replace `placeholder` with
    `text`; an empty splice also collapses the blank lines that framed the
    placeholder so no gap is left behind."""
    if text:
        return template.replace(placeholder, text.rstrip("\n"))
    return template.replace(f"\n{placeholder}\n", "")


def render_agents_md_block() -> str:
    """Render vectr's guidance block exactly as `vectr init --hooks` leaves
    it in AGENTS.md for THIS fixture. Traced against main.py's
    `_write_workspace_config` (not assumed): CLAUDE.md is written FIRST in
    that function (hooks_installed=True once hooks were just installed in
    the same run, tool_loading=True -- CLAUDE.md-only guidance), then
    AGENTS.md is written SECOND via the `_IDE_CONFIG_APPEND_ONLY` loop with
    `hooks_installed=codex_hooks_installed` (False here -- this driver never
    writes .codex/hooks.json) and `tool_loading` defaulting False. Because
    the camel fixture's CLAUDE.md is a symlink TO AGENTS.md, both writes hit
    the same underlying file and the second (AGENTS.md-variant) write is the
    one that survives -- so hooks_installed=False, search_only=False,
    tool_loading=False is the correct, verified end state, not a
    simplification of the literal spec wording."""
    rendered = _CLAUDE_MD_TEMPLATE.replace(
        "__SESSION_START_GUIDANCE__", _SESSION_START_GUIDANCE_DEFAULT
    )
    rendered = _splice(rendered, "__TOOL_LOADING_GUIDANCE__", "")
    return f"{_VECTR_BLOCK_START}\n{rendered.rstrip()}\n{_VECTR_BLOCK_END}\n"


def render_claude_settings() -> dict:
    """.claude/settings.json exactly as `vectr init --hooks` writes it:
    the four hook groups main.py's `_write_claude_hooks` installs (each an
    `_install_hook_group` call with the literal event/matcher/command below,
    verified against main.py lines ~830-845) plus
    `enableAllProjectMcpServers: true` (`_ensure_enable_all_project_mcp_
    servers`). Deliberately carries NO `mcpServers` key -- main.py never
    writes one into settings.json; the MCP server declaration lives only in
    the separate .mcp.json file (render_fixture_mcp_json below). This is a
    documented deviation from an earlier, less precise reading of the task
    brief ("plus mcpServers.vectr = ...") -- main.py's actual write is the
    resolving authority per its own "copy the exact structure vectr
    generates" instruction."""
    return {
        "hooks": {
            "SessionStart": [{
                "matcher": "startup|resume|clear|compact",
                "hooks": [{"type": "command", "command": "vectr hook session-start"}],
            }],
            "UserPromptSubmit": [{
                "hooks": [{"type": "command", "command": "vectr hook user-prompt-submit"}],
            }],
            "PreToolUse": [{
                "matcher": "Edit|Write|Read",
                "hooks": [{"type": "command", "command": "vectr hook pre-tool-use"}],
            }],
            "PreCompact": [{
                "matcher": "manual|auto",
                "hooks": [{"type": "command", "command": "vectr hook pre-compact"}],
            }],
        },
        "enableAllProjectMcpServers": True,
    }


def render_fixture_mcp_json(port: int) -> str:
    """.mcp.json exactly as main.py's `_write_workspace_config` writes it
    (same `agent/templates/mcp.json.template`, no auth headers -- the bench
    daemon runs keyless)."""
    return _MCP_JSON_TEMPLATE.format(port=port)


def write_vectr_artifacts(fixture_root: Path, port: int) -> bytes:
    """Write all three vectr-shipped artifacts for the vectr arm. Returns
    AGENTS.md's original bytes (the only pre-existing file touched) for an
    exact restore afterward. `.claude/settings.json` and `.mcp.json` are
    vectr-owned files this driver deletes on cleanup rather than restoring
    from a snapshot -- upstream camel never ships either (same assumption
    run_t1c.py's `_VECTR_OWNED_PATHS` purity check relies on) and step 1's
    fixture-clean check already guarantees no untracked copy is lying
    around; a surprise pre-existing one is treated as a hard error rather
    than silently destroyed."""
    agents_path = fixture_root / "AGENTS.md"
    settings_path = fixture_root / ".claude" / "settings.json"
    mcp_path = fixture_root / ".mcp.json"
    if settings_path.exists():
        raise RuntimeError(f"refusing to overwrite pre-existing {settings_path}")
    if mcp_path.exists():
        raise RuntimeError(f"refusing to overwrite pre-existing {mcp_path}")

    original = agents_path.read_bytes()
    existing = original.decode("utf-8")
    block = render_agents_md_block()
    stripped = (_VECTR_BLOCK_RE.sub("", existing) if _VECTR_BLOCK_START in existing else existing).rstrip()
    new_agents = f"{stripped}\n\n{block}" if stripped else block
    agents_path.write_text(new_agents, encoding="utf-8")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(render_claude_settings(), indent=2) + "\n", encoding="utf-8")
    mcp_path.write_text(render_fixture_mcp_json(port), encoding="utf-8")
    return original


def remove_vectr_artifacts(fixture_root: Path, agents_md_original: bytes) -> None:
    """Restore AGENTS.md to its exact original bytes; delete the other two
    vectr-owned artifacts. Called BEFORE capture_tree_changes (see run_task)
    so the recorded diff never includes vectr's own coaching injection."""
    (fixture_root / "AGENTS.md").write_bytes(agents_md_original)
    settings_path = fixture_root / ".claude" / "settings.json"
    if settings_path.exists():
        settings_path.unlink()
    claude_dir = settings_path.parent
    try:
        if claude_dir.exists() and not any(claude_dir.iterdir()):
            claude_dir.rmdir()
    except OSError:
        pass
    mcp_path = fixture_root / ".mcp.json"
    if mcp_path.exists():
        mcp_path.unlink()


# ---------------------------------------------------------------------------
# Fixture git-clean / seed-patch mechanics
# ---------------------------------------------------------------------------

class FixtureNotCleanError(RuntimeError):
    """Raised when the shared fixture is not in the expected clean state --
    the whole run aborts loudly rather than silently building on top of a
    previous run's leftover state."""


def verify_fixture_clean(fixture_root: Path) -> None:
    """Step 1 of the per-task sequence: `git status --porcelain` must show
    only `?? .vectrignore` (the one file this driver, and every arm, never
    touches). Anything else means a previous run's cleanup failed or the
    fixture was hand-edited."""
    result = subprocess.run(
        ["git", "-C", str(fixture_root), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise FixtureNotCleanError(
            f"git status failed in {fixture_root} (rc={result.returncode}): {result.stderr.strip()}"
        )
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    unexpected = [ln for ln in lines if ln.strip() != "?? .vectrignore"]
    if unexpected:
        raise FixtureNotCleanError(
            f"fixture not clean: {fixture_root} has unexpected changes: {unexpected}"
        )


def materialize_seed_patch(fixture_root: Path, fix_sha: str, out_path: Path) -> Path:
    """`git show <fix_sha> -- ':!*src/test*' > out_path` -- the fix commit's
    non-test hunks only. The commit's own test (kept at its fixed version)
    is the pass/fail gate, so it must never be part of the seeded bug."""
    result = subprocess.run(
        ["git", "-C", str(fixture_root), "show", fix_sha, "--", ":!*src/test*"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show {fix_sha} failed: {result.stderr.strip()}")
    out_path.write_text(result.stdout)
    return out_path


def apply_seed_reverse(fixture_root: Path, patch_path: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(fixture_root), "apply", "-R", str(patch_path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git apply -R {patch_path} failed: {result.stderr.strip()}")


def hide_git(fixture_root: Path) -> Path:
    """Move .git out of the fixture so a session cannot discover the gold
    answer via `git log`/`git diff` against the fix commit. Sibling
    location (not inside the fixture, so it is never visible as a workspace
    file) -- same convention run_t1c.py's fixture layout uses."""
    git_dir = fixture_root / ".git"
    stash_path = fixture_root.parent / f"{fixture_root.name}.git-stash"
    if stash_path.exists():
        raise RuntimeError(
            f"stale git-stash already present: {stash_path} -- a previous run's "
            f"cleanup failed; investigate before continuing"
        )
    shutil.move(str(git_dir), str(stash_path))
    return stash_path


def restore_git(fixture_root: Path, stash_path: Path) -> None:
    git_dir = fixture_root / ".git"
    if git_dir.exists():
        raise RuntimeError(f"{git_dir} unexpectedly already present before restore")
    shutil.move(str(stash_path), str(git_dir))


def reset_fixture(fixture_root: Path) -> None:
    """`git checkout -- . && git clean -fd -e .vectrignore` -- full reset to
    HEAD, preserving .vectrignore (never touched in either arm). Requires
    `.git` to be present (call only after restore_git)."""
    subprocess.run(
        ["git", "-C", str(fixture_root), "checkout", "--", "."],
        check=True, capture_output=True, text=True, timeout=60,
    )
    subprocess.run(
        ["git", "-C", str(fixture_root), "clean", "-fd", "-e", ".vectrignore"],
        check=True, capture_output=True, text=True, timeout=60,
    )


def capture_tree_changes(fixture_root: Path, *, cap_bytes: int = 200_000) -> dict:
    """`git status --porcelain` + `git diff`, called AFTER .git is restored
    AND (for the vectr arm) after vectr's own artifacts have already been
    removed -- so the diff reflects only the seed reversal + the agent's
    actual edits, never vectr's own AGENTS.md/settings.json/.mcp.json
    injection. Capped at cap_bytes; truncation is flagged, never silent."""
    status = subprocess.run(
        ["git", "-C", str(fixture_root), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    diff = subprocess.run(
        ["git", "-C", str(fixture_root), "diff"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    return {
        "status_porcelain": status,
        "diff": diff[:cap_bytes],
        "diff_truncated": len(diff) > cap_bytes,
    }


def daemon_settle(base: str, *, max_wait_s: int = 300, poll_gap_s: int = 10) -> tuple[bool, str]:
    """Poll GET /v1/status until two successive polls `poll_gap_s` apart
    report identical `total_chunks` AND `last_indexed` -- the daemon has
    stopped re-indexing the seed's file-tree mutation. Read-only; NEVER
    starts, stops, or restarts the daemon.

    A poll miss -- unreachable daemon (URLError/ConnectionError/TimeoutError/
    OSError) OR a truncated/invalid JSON body from `json.load` (a
    json.JSONDecodeError, which is a ValueError -- plausible mid-reindex,
    when the daemon's status endpoint is momentarily mid-write) -- is
    treated as transient, not fatal: keep polling rather than aborting the
    task on the very first hiccup. Gives up only after `max_wait_s`, at
    which point it proceeds with a logged warning -- a settle timeout (or a
    persistent poll miss) is a data-quality caveat on the run, not a reason
    to abort it."""
    start = time.time()
    prev = None
    last_poll_error: str | None = None
    while True:
        try:
            with urllib.request.urlopen(f"{base}/v1/status", timeout=10) as r:
                status = json.load(r)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as exc:
            last_poll_error = f"{type(exc).__name__}: {exc}"
            if time.time() - start >= max_wait_s:
                return False, f"settle timed out after {max_wait_s}s -- last poll error: {last_poll_error}"
            time.sleep(poll_gap_s)
            continue
        cur = (status.get("total_chunks"), status.get("last_indexed"))
        if prev is not None and cur == prev:
            return True, f"settled at total_chunks={cur[0]} last_indexed={cur[1]!r} after {time.time()-start:.1f}s"
        if time.time() - start >= max_wait_s:
            return False, (
                f"settle timed out after {max_wait_s}s -- proceeding anyway "
                f"(last observed total_chunks={cur[0]} last_indexed={cur[1]!r})"
            )
        prev = cur
        time.sleep(poll_gap_s)


# ---------------------------------------------------------------------------
# Maven/JDK gate
# ---------------------------------------------------------------------------

def resolve_java_home() -> str:
    result = subprocess.run(
        ["/usr/libexec/java_home", "-v", "21"], capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"JDK 21 not resolvable via `/usr/libexec/java_home -v 21`: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def maven_settings_path() -> Path:
    return Path.home() / ".cache" / "vectr" / "bench" / "maven-settings.xml"


# Maven emits one of a small, stable set of literal strings when a `test`
# goal genuinely reaches and runs tests that then fail (as opposed to the
# build never reaching the test phase at all -- a compile error, a missing
# module, an OOM, etc). Used ONLY to label `gate_error` below for REPORTING;
# it never changes the gate's pass/fail outcome (the exit code remains the
# sole pass/fail signal, per the module docstring's honesty rules) or any
# control flow -- classifying tool OUTPUT after the fact, not task-prompt
# content (HEURISTIC-DIRECTIVE R5 is about query-content branching, not this).
_MAVEN_TEST_FAILURE_MARKERS: tuple[str, ...] = ("There are test failures", "Tests run:")


def _looks_like_genuine_test_failure(maven_output: str) -> bool:
    return any(marker in maven_output for marker in _MAVEN_TEST_FAILURE_MARKERS)


def run_gate(fixture_root: Path, java_home: str, gate_modules: str, gate_test: str,
             *, timeout_s: int = 600) -> dict:
    """`<fixture>/mvnw -q -s <maven-settings> -pl <gate_modules> test
    -Dtest=<gate_test>`, cwd=fixture, JAVA_HOME pinned to the resolved JDK 21.
    Exit 0 = pass. NEVER modifies the gate command, settings, or tests to
    make anything pass.

    `gate_error` (additive, reporting-only) distinguishes an infrastructure/
    compile failure from a genuine red test: True when the gate did not pass
    AND the combined maven output carries none of `_MAVEN_TEST_FAILURE_
    MARKERS` (or the gate timed out outright, which by definition is not a
    completed, genuinely-red test run). This changes no pass/fail logic --
    `passed` is untouched and remains `returncode == 0`."""
    settings = maven_settings_path()
    cmd = [
        str(fixture_root / "mvnw"), "-q", "-s", str(settings),
        "-pl", gate_modules, "test", f"-Dtest={gate_test}",
    ]
    env = dict(os.environ)
    env["JAVA_HOME"] = java_home
    start = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=str(fixture_root), env=env, capture_output=True, text=True, timeout=timeout_s,
        )
        passed = result.returncode == 0
        return {
            "passed": passed, "returncode": result.returncode,
            "wall_s": round(time.time() - start, 2),
            "stdout_tail": result.stdout[-4000:], "stderr_tail": result.stderr[-4000:],
            "timed_out": False,
            "gate_error": (not passed) and not _looks_like_genuine_test_failure(result.stdout + result.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "passed": False, "returncode": None, "wall_s": round(time.time() - start, 2),
            "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:],
            "timed_out": True,
            "gate_error": True,
        }


# ---------------------------------------------------------------------------
# Session env (JAVA_HOME + MAVEN_ARGS) -- additive to run_t1b.py, not a
# modification of it: run_claude_session's `_spawn_env()` builds the child's
# env from this process's `os.environ` at spawn time, so these are set on
# THIS process's environment only for the duration of one session spawn,
# then restored. Keeps run_t1b.py's signature untouched.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _extra_session_env(java_home: str, maven_settings: Path):
    extra = {"JAVA_HOME": java_home, "MAVEN_ARGS": f"-s {maven_settings}"}
    saved = {k: os.environ.get(k) for k in extra}
    os.environ.update(extra)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Command composition -- identical shape for both arms; the only inputs
# that vary per arm are the mcp_config_path and the session cwd (both
# already fixed to the one shared fixture in T2).
# ---------------------------------------------------------------------------

def compose_command(prompt: str, mcp_config_path: Path, model: str, max_turns: int,
                     disallowed_tools: tuple[str, ...]) -> list[str]:
    full_prompt = _PREAMBLE + prompt
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
        "--disallowedTools", ",".join(disallowed_tools),
    ]


# ---------------------------------------------------------------------------
# Preflights (read-only only -- never starts/stops/restarts anything, never
# mutates the fixture)
# ---------------------------------------------------------------------------

def check_fixture_clean_preflight(fixture_root: Path) -> tuple[bool, str]:
    try:
        verify_fixture_clean(fixture_root)
        return True, f"{fixture_root} is git-clean (only .vectrignore untracked)"
    except FixtureNotCleanError as exc:
        return False, str(exc)


def check_jdk21_preflight() -> tuple[bool, str]:
    try:
        home = resolve_java_home()
        return True, f"JDK 21 resolved: {home}"
    except RuntimeError as exc:
        return False, str(exc)


def check_maven_settings_preflight() -> tuple[bool, str]:
    p = maven_settings_path()
    if p.exists():
        return True, f"{p} exists"
    return False, f"maven settings missing: {p}"


def check_vectr_on_path_preflight() -> tuple[bool, str]:
    p = shutil.which("vectr")
    if p:
        return True, f"vectr on PATH: {p}"
    return False, "vectr not found on PATH"


def check_seed_reverse_applies_preflight(fixture_root: Path, fix_sha: str) -> tuple[bool, str]:
    """Read-only: pipes `git show <fix_sha> -- ':!*src/test*'` directly into
    `git apply -R --check` over stdin -- never writes a patch file to disk
    (that only happens in materialize_seed_patch during a live run), keeping
    this dry-run preflight purely read-only."""
    show = subprocess.run(
        ["git", "-C", str(fixture_root), "show", fix_sha, "--", ":!*src/test*"],
        capture_output=True, text=True, timeout=60,
    )
    if show.returncode != 0:
        return False, f"git show {fix_sha} failed: {show.stderr.strip()}"
    check = subprocess.run(
        ["git", "-C", str(fixture_root), "apply", "-R", "--check"],
        input=show.stdout, capture_output=True, text=True, timeout=30,
    )
    if check.returncode == 0:
        return True, f"{fix_sha[:12]} reverse-applies cleanly"
    return False, f"{fix_sha[:12]} does NOT reverse-apply: {check.stderr.strip()}"


def check_task_ids_unique(tasks_path: Path) -> tuple[bool, str]:
    """load_jsonl (reused from run_t1b) returns a dict keyed by id, which
    would silently overwrite duplicates rather than reveal them -- this
    reads the raw lines instead, specifically to catch that case."""
    ids: list[str] = []
    with open(tasks_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            ids.append(json.loads(line)["id"])
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        return False, f"duplicate task ids in {tasks_path.name}: {dupes}"
    return True, f"{len(ids)} unique task ids in {tasks_path.name}"


# ---------------------------------------------------------------------------
# Per-task live sequence
# ---------------------------------------------------------------------------

def _best_effort_restore(fixture_root: Path, state: dict) -> None:
    """Rail: on ANY exception mid-task (or a seed_invalid early-return),
    restore everything the task has changed so far, swallowing further
    errors (best effort) so a broken cleanup step never hides the original
    exception -- the fixture must never be left dirty or git-less."""
    if state.get("vectr_artifacts_active"):
        try:
            remove_vectr_artifacts(fixture_root, state["agents_md_original"])
        except Exception:
            pass
        state["vectr_artifacts_active"] = False
    if state.get("git_hidden"):
        try:
            restore_git(fixture_root, state["git_stash_path"])
        except Exception:
            pass
        state["git_hidden"] = False
    if state.get("seed_applied"):
        try:
            reset_fixture(fixture_root)
        except Exception:
            pass
        state["seed_applied"] = False


def run_task(
    task: dict, fixture_root: Path, base: str, out_dir: Path, *,
    model: str, max_turns: int, session_timeout_s: int, gate_timeout_s: int,
    java_home: str, arms: list[str], disallowed_tools: tuple[str, ...],
    mcp_config_path_by_arm: dict[str, Path], daemon_port: int,
) -> dict:
    """Run the full live sequence (module docstring / plan.md "Tier 2"
    section) for one task: verify-clean -> seed -> hide-git -> settle ->
    pre-gate (must fail) -> [per requested arm: write artifacts if vectr ->
    session -> post-gate (git still hidden) -> restore git -> remove
    artifacts if vectr -> capture tree_changes -> reset -> reseed+rehide
    unless last arm]. Returns one row: {"id", "seed_invalid", "gate_pre",
    "arms": {arm: summary}}. NEVER computes a pass/fail score beyond
    recording gate results -- sentinel reviews the recorded diffs."""
    tid = task["id"]
    state: dict = {
        "git_hidden": False, "git_stash_path": None,
        "seed_applied": False,
        "vectr_artifacts_active": False, "agents_md_original": None,
    }
    row: dict = {"id": tid, "seed_invalid": False, "arms": {}}
    # Tracked so a mid-arm-loop exception can be tagged with which arm was in
    # flight ("arm reached if known" -- see main()'s per-task error handling).
    current_arm: str | None = None

    try:
        # Step 1: verify fixture is git-clean before touching anything.
        verify_fixture_clean(fixture_root)

        # Step 2: materialize + reverse-apply the seed patch. seed_applied is
        # flipped True BEFORE the (atomic) apply call itself -- strictly
        # safer against the restore-skip class: if anything between the flag
        # flip and the call were ever to change, the restore rail still
        # attempts reset_fixture, which is itself a safe no-op when nothing
        # was actually seeded.
        seed_patch_path = out_dir / f"{tid}_seed.patch"
        materialize_seed_patch(fixture_root, task["fix_sha"], seed_patch_path)
        state["seed_applied"] = True
        apply_seed_reverse(fixture_root, seed_patch_path)
        row["seed_patch_path"] = str(seed_patch_path.relative_to(_REPO_ROOT))

        # Step 3: hide .git so a session cannot discover the gold answer.
        state["git_stash_path"] = hide_git(fixture_root)
        state["git_hidden"] = True

        # Step 4: daemon settle (read-only poll, never starts/stops it).
        settled, settle_msg = daemon_settle(base)
        row["settle_pre"] = {"settled": settled, "msg": settle_msg}
        print(f"  [{tid}] settle (pre-gate): {settle_msg}")

        # Step 5: PRE-GATE -- must fail, or the seed is invalid.
        gate_pre = run_gate(fixture_root, java_home, task["gate_modules"], task["gate_test"],
                             timeout_s=gate_timeout_s)
        row["gate_pre"] = {"passed": gate_pre["passed"], "wall_s": gate_pre["wall_s"]}
        if gate_pre["passed"]:
            print(f"  [{tid}] PRE-GATE PASSED -- seed is invalid, aborting task")
            row["seed_invalid"] = True
            _best_effort_restore(fixture_root, state)
            return row
        print(f"  [{tid}] pre-gate correctly FAILS ({gate_pre['wall_s']}s) -- seed valid")

        # Design requirement (module docstring): vectr arm always runs before
        # bash within a task, regardless of the order the caller passed
        # `arms` in -- normalized here (not only at the CLI layer, see
        # main()) so the invariant holds for every caller of run_task.
        arms = _normalize_arm_order(arms)

        for i, arm in enumerate(arms):
            current_arm = arm
            mcp_config_path = mcp_config_path_by_arm[arm]

            # Step 6 (vectr arm only): write shipped artifacts before the session.
            if arm == "vectr":
                state["agents_md_original"] = write_vectr_artifacts(fixture_root, daemon_port)
                state["vectr_artifacts_active"] = True

            cmd = compose_command(task["prompt"], mcp_config_path, model, max_turns, disallowed_tools)
            print(f"  [{tid}/{arm}] cmd: {shlex.join(cmd)}")

            # NEVER reached by automation -- real spawn, burns Claude Code quota.
            with _extra_session_env(java_home, maven_settings_path()):
                session = run_claude_session(cmd, cwd=str(fixture_root), timeout_s=session_timeout_s)
            if session.get("timed_out"):
                print(f"  [{tid}/{arm}] [WARN] session timed out after {session_timeout_s}s")

            stamp = time.strftime("%Y%m%dT%H%M%S")
            transcript_path = out_dir / f"{tid}_{arm}_{stamp}.jsonl"
            with open(transcript_path, "w") as fh:
                for ev in session["events"]:
                    fh.write(json.dumps(ev) + "\n")

            summary = _t1c_summarize_task_result(task, arm, session["events"], session["wall_s"])
            # Deliverable 3: when the event stream carries no terminal
            # `result` event, force is_error/error explicitly -- WITHOUT
            # modifying run_t1b.parse_transcript itself. A layered check on
            # top of its already-parsed output (parse_transcript's `final`
            # dict defaults is_error to False when no result event exists,
            # which would otherwise silently look like a clean success).
            if not any(ev.get("type") == "result" for ev in session["events"]):
                summary["is_error"] = True
                summary["error"] = "no result event"
            summary["timed_out"] = bool(session.get("timed_out", False))
            summary["transcript_path"] = str(transcript_path.relative_to(_REPO_ROOT))

            # Step 9: POST-GATE, .git still hidden, immediately after the session.
            gate_post = run_gate(fixture_root, java_home, task["gate_modules"], task["gate_test"],
                                  timeout_s=gate_timeout_s)
            summary["gate_pre"] = row["gate_pre"]
            summary["gate_post"] = {"passed": gate_post["passed"], "wall_s": gate_post["wall_s"]}
            print(f"  [{tid}/{arm}] post-gate passed={gate_post['passed']} ({gate_post['wall_s']}s)")

            # Restore .git, THEN (vectr arm only) remove vectr's own
            # artifacts, THEN capture tree_changes -- order matters: capturing
            # before removing the vectr block would show AGENTS.md as
            # "changed" because of our own coaching injection, not because
            # of the agent's actual fix.
            restore_git(fixture_root, state["git_stash_path"])
            state["git_hidden"] = False
            if arm == "vectr":
                remove_vectr_artifacts(fixture_root, state["agents_md_original"])
                state["vectr_artifacts_active"] = False

            summary["tree_changes"] = capture_tree_changes(fixture_root)
            reset_fixture(fixture_root)
            state["seed_applied"] = False

            row["arms"][arm] = summary

            if i < len(arms) - 1:
                # Step 7: between arms -- re-seed, re-hide, re-settle.
                # (seed_applied flipped before the apply call -- see the
                # step-2 comment above for the same rationale.)
                state["seed_applied"] = True
                apply_seed_reverse(fixture_root, seed_patch_path)
                state["git_stash_path"] = hide_git(fixture_root)
                state["git_hidden"] = True
                settled, settle_msg = daemon_settle(base)
                row.setdefault("settle_between_arms", []).append(
                    {"after_arm": arm, "settled": settled, "msg": settle_msg}
                )
                print(f"  [{tid}] settle (between arms, after {arm}): {settle_msg}")

        return row

    except Exception as exc:
        # Tag which arm (if any) was in flight when this Exception fired, so
        # main()'s per-task error row (MEDIUM-2b) can report it -- the
        # restore itself happens unconditionally in `finally` below, not
        # here, so this tagging never has to run for a BaseException
        # (KeyboardInterrupt/SystemExit) to still be restored.
        exc.t2_task_id = tid  # type: ignore[attr-defined]
        exc.t2_arm_reached = current_arm  # type: ignore[attr-defined]
        raise

    finally:
        # Step 11 (HIGH-1 safety rail): restore whatever is still active,
        # REGARDLESS of how this function exits -- normal return, a plain
        # Exception, or a BaseException such as KeyboardInterrupt/SystemExit.
        # A Ctrl-C during a 900s session, a settle sleep, or a 600s gate must
        # never unwind through only the `except Exception` above (which does
        # not match BaseException) and leave the fixture git-less + seeded.
        # _best_effort_restore is idempotent against its own state flags, so
        # on the normal-return path (every flag already cleared) this is a
        # no-op. Interrupts still propagate after the restore completes --
        # `finally` never swallows the exception it runs alongside.
        _best_effort_restore(fixture_root, state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tier-2 two-arm seeded-bugfix benchmark")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--arms", default="vectr,bash",
                         help="Comma-separated arms to run: vectr, bash, or both (default)")
    parser.add_argument("--tasks", default=None, help="Comma-separated task ids to run; default = all")
    parser.add_argument("--fixture-root", default=None,
                         help="Override the shared fixture root (default tmp/poc-camel)")
    parser.add_argument("--model", default="sonnet",
                         help="Model alias/id passed to --model (cheaper-models rule: sonnet by default)")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=900, help="Per-session wall-clock timeout, seconds")
    parser.add_argument("--gate-timeout", type=int, default=600, help="Per-gate-run wall-clock timeout, seconds")
    parser.add_argument("--smoke", action="store_true", help="Tag the results file as a smoke test")
    parser.add_argument("--dry-run", action="store_true",
                         help="Parse tasks, compose + print every claude -p invocation, run ONLY "
                              "read-only preflights; spawn nothing, mutate nothing in the fixture. "
                              "The only mode automation may use.")
    args = parser.parse_args(argv)

    valid_arms = {"vectr", "bash"}
    requested_arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in requested_arms if a not in valid_arms]
    if bad or not requested_arms:
        print(f"ERROR: --arms must be a comma-separated subset of {sorted(valid_arms)}, got {args.arms!r}",
              file=sys.stderr)
        return 2
    # `--arms` is a SET of which arms to run, never an ORDER -- vectr always
    # runs before bash when both are selected (see _ARM_ORDER). Normalized
    # here too so the printed plan and the dry-run preview already reflect
    # the order that will actually execute.
    requested_arms = _normalize_arm_order(requested_arms)

    base = f"http://{args.host}:{args.port}"
    tasks_path = _HERE / f"tasks_t2_{_CORPUS}.jsonl"
    fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"poc-{_CORPUS}"

    if not tasks_path.exists():
        print(f"ERROR: no task file: {tasks_path}", file=sys.stderr)
        return 1

    ok, msg = check_task_ids_unique(tasks_path)
    print(f"[preflight:task-ids-unique] {'OK' if ok else 'FAIL'}: {msg}")
    if not ok:
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    tasks = load_jsonl(tasks_path)
    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks else sorted(tasks.keys())
    unknown = [t for t in task_ids if t not in tasks]
    if unknown or not task_ids:
        print(f"ERROR: unknown/empty task id(s) {unknown or args.tasks!r} -- "
              f"valid ids in {tasks_path.name}: {sorted(tasks.keys())}", file=sys.stderr)
        return 2

    print("=" * 88)
    print(f"Tier-2 two-arm seeded-bugfix -- corpus={_CORPUS}  arms={requested_arms}")
    print(f"fixture: {fixture_root}  daemon: {base}")
    print(f"model={args.model}  max_turns={args.max_turns}  session_timeout={args.timeout}s  "
          f"gate_timeout={args.gate_timeout}s  dry_run={args.dry_run}")
    print(f"Running {len(task_ids)} task(s) x {len(requested_arms)} arm(s): {task_ids}")
    print("=" * 88)

    # ------------------------------------------------------------------
    # Preflights -- ALL read-only, no fixture mutation. A failure is a
    # loudly-printed, non-fatal warning in --dry-run (its job is to compose
    # + print regardless); fatal in live mode.
    # ------------------------------------------------------------------
    checks: list[tuple[str, bool, str]] = [
        ("fixture-exists", *check_fixture_root_exists(fixture_root)),
        ("fixture-clean", *check_fixture_clean_preflight(fixture_root)),
        ("vectr-daemon", *preflight_vectr(base)),
        ("jdk21", *check_jdk21_preflight()),
        ("maven-settings", *check_maven_settings_preflight()),
        ("vectr-on-path", *check_vectr_on_path_preflight()),
    ]
    for tid in task_ids:
        checks.append((
            f"seed-reverse-applies:{tid}",
            *check_seed_reverse_applies_preflight(fixture_root, tasks[tid]["fix_sha"]),
        ))

    fatal = False
    for label, ok, msg in checks:
        status_tag = "OK" if ok else "FAIL"
        print(f"[preflight:{label}] {status_tag}: {msg}")
        if not ok:
            if args.dry_run:
                print(f"  [dry-run] WARNING: {label} check failed (not fatal for --dry-run)")
            else:
                fatal = True
    if fatal and not args.dry_run:
        print("ERROR: one or more fatal preflight checks failed; aborting.", file=sys.stderr)
        return 1

    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, text=True,
    ).strip()
    out_dir = _REPO_ROOT / "results" / "vectr-vs-bash" / _CORPUS / sha / "t2"
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

    if args.dry_run:
        for tid in task_ids:
            task = tasks[tid]
            for arm in requested_arms:
                cmd = compose_command(task["prompt"], mcp_config_path_by_arm[arm], args.model,
                                       args.max_turns, _DISALLOWED_TOOLS)
                print(f"\n[{tid}/{arm}] gate_modules={task['gate_modules']}  gate_test={task['gate_test']}")
                print(f"  cwd: {fixture_root}")
                print(f"  cmd: {shlex.join(cmd)}")
                print("  [dry-run] not spawned")
        print("\n" + "=" * 88)
        print(f"[dry-run] preflights done, {len(task_ids)} task(s) x {len(requested_arms)} arm(s) "
              f"composed, 0 sessions spawned, 0 fixture mutations.")
        print("=" * 88)
        return 0

    # ------------------------------------------------------------------
    # Live mode -- everything below mutates the shared fixture tree.
    # ------------------------------------------------------------------
    java_home = resolve_java_home()
    results: list[dict] = []
    stamp = time.strftime("%Y%m%dT%H%M%S")
    tag = "smoke" if args.smoke else "run"
    out_path = out_dir / f"t2_{tag}_{stamp}.json"

    def _write_aggregate() -> dict:
        # Rewritten after EVERY task (not just once at the end) so a failure
        # on a later task never discards the results already recorded for
        # earlier ones -- also called unconditionally in the `finally` below
        # so a run that raises out of the loop entirely (e.g. Ctrl-C) still
        # leaves whatever was completed on disk.
        agg = {
            "corpus": _CORPUS,
            "vectr_sha": sha,
            "smoke_test": bool(args.smoke),
            "port": args.port,
            "model": args.model,
            "max_turns": args.max_turns,
            "arms": requested_arms,
            "task_ids": task_ids,
            "results": results,
            "tasks_seed_invalid": sum(1 for r in results if r.get("seed_invalid")),
            "tasks_errored": sum(1 for r in results if "error" in r),
            "tasks_scored": sum(
                1 for r in results if not r.get("seed_invalid") and "error" not in r
            ),
        }
        out_path.write_text(json.dumps(agg, indent=2))
        return agg

    try:
        for tid in task_ids:
            task = tasks[tid]
            print(f"\n{'=' * 88}\n[{tid}] starting\n{'=' * 88}")
            try:
                row = run_task(
                    task, fixture_root, base, out_dir,
                    model=args.model, max_turns=args.max_turns,
                    session_timeout_s=args.timeout, gate_timeout_s=args.gate_timeout,
                    java_home=java_home, arms=requested_arms,
                    disallowed_tools=_DISALLOWED_TOOLS,
                    mcp_config_path_by_arm=mcp_config_path_by_arm, daemon_port=args.port,
                )
            except Exception as exc:
                # run_task's own try/finally has already restored the
                # fixture (HIGH-1) before this propagates -- a failure on
                # one task must not discard the aggregate for tasks already
                # completed, nor abort the remaining tasks. A
                # KeyboardInterrupt/SystemExit (BaseException, not caught
                # here) still propagates straight out of this loop, after
                # run_task's own restore -- Ctrl-C stops the whole run.
                arm_reached = getattr(exc, "t2_arm_reached", None)
                print(f"[{tid}] ERROR (arm_reached={arm_reached!r}): {exc!r} -- "
                      f"recording error row, continuing to next task")
                row = {
                    "id": tid, "seed_invalid": False, "arms": {},
                    "error": repr(exc), "arm_reached": arm_reached,
                }
            results.append(row)
            _write_aggregate()
            if row.get("seed_invalid"):
                print(f"[{tid}] SEED INVALID -- pre-gate passed, skipped")
                continue
            if "error" in row:
                continue
            for arm, summary in row["arms"].items():
                print(f"[{tid}/{arm}] gate_pre_passed={summary['gate_pre']['passed']} "
                      f"gate_post_passed={summary['gate_post']['passed']} "
                      f"is_error={summary['is_error']} timed_out={summary['timed_out']}")
    finally:
        aggregate = _write_aggregate()

    print("\n" + "=" * 88)
    print(f"tasks_seed_invalid={aggregate['tasks_seed_invalid']}  "
          f"tasks_errored={aggregate['tasks_errored']}  tasks_scored={aggregate['tasks_scored']}")
    print(f"Results written: {out_path}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
