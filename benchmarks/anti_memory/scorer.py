#!/usr/bin/env python3
"""EVAL-ANTI-MEMORY -- mechanical, arm-blind scorer plus the $0 Tier-0 gates.

Two independent halves, DESIGN.md 7 and 13:

1. **`score_run()`** (DESIGN.md 7.2/7.3) -- grades one completed leg's final
   workspace bytes and transcript against its scenario's pre-registered
   `backflow_content` / `correct_check` / `backflow_signature`. Arm-blind by
   construction: no `arm` parameter anywhere in this function or anything it
   calls. This is the T1+ (paid-tier) path; T0-8 exercises it offline against
   hand-built transcript/workspace fixtures, no daemon and no agent required.

2. **The Tier-0 gates** (`store_state_non_vacuity`, `proxy_delivery_check`,
   `cell_non_vacuity`) -- DESIGN.md 5.4's per-arm non-vacuity table, split into
   its $0-reachable half (T0-1..T0-6: REST calls against a real daemon plus
   the real in-process proxy app, no agent) and its transcript-dependent half
   (session.init / mcp_servers / result.subtype / restored-manifest checks,
   only meaningful once a live leg exists). `arm` is consulted ONLY in this
   half, matching the substrate's own rule that the outcome verdict stays
   arm-blind and only non-vacuity ever asks "did this arm's channel fire?".

Action-stream primitives (`Action`, `build_action_stream`, `bash_commands`,
`_pattern_matches`, `first_index`, `any_file_mutated`) and the transcript-based
metric functions (`evaluate_check`, `cost_metrics`, `parse_injection_events`)
are DUPLICATED from `benchmarks/longitudinal_rediscovery/scorer.py`, adapted to
this eval's own scenario shape and check classes -- not imported cross-harness.
Importing them would bind `evaluate_check`'s `isinstance()` dispatch to the
LONGITUDINAL harness's `scenarios.py` module object while this eval's own
`FileExists`/`AntiMemoryScenario` classes come from a DIFFERENT module object
loaded under a different `sys.modules` key (see `_load_sibling_scenarios`
below), which would make every `isinstance()` check here silently return
False. Duplicating ~150 lines of pure-function pattern-matching logic is the
safe side of that tradeoff, and is the same choice `longitudinal_rediscovery/
scorer.py` itself makes relative to `injection_utility/scorer.py`.

**DEFECT 9 (exec-position anchoring, ported -- UPG-ANTIMEM-EXEC-ANCHOR):**
`_pattern_matches`'s `BashAction` branch and `evaluate_check`'s `CommandRan`
branch honor `pattern.exec_anchor`/`check.exec_anchor` exactly as the
longitudinal substrate's own `_matches_at_exec_position` does -- a signature
whose semantic is "the agent RAN this" (`backflow_signature`, a `correct_check`
command pattern) must match at a genuine command-execution position, not
anywhere in the command string, or a read-only mention (`grep -rn "make seed"
.`) scores identically to a real invocation. See `_matches_at_exec_position`'s
own section comment below for the full algorithm (duplicated, not imported,
from the same-named longitudinal function, for the same module-identity reason
as everything else duplicated in this docstring).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Sibling `scenarios.py`, loaded under one canonical, cache-checked private key
# so every anti_memory module and every `tests/test_anti_memory_*.py` file that
# needs its classes converges on ONE module object regardless of import order
# (the collision this avoids, and the precedent this copies verbatim, are both
# `longitudinal_rediscovery/scorer.py`'s `_load_sibling_scenarios` / its own
# `run_leg.py`'s `_load_local_module`).
# ---------------------------------------------------------------------------

ANTIMEM_SCENARIOS_KEY = "_vectr_eval_antimem_scenarios"


def load_sibling_scenarios():
    cached = sys.modules.get(ANTIMEM_SCENARIOS_KEY)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "scenarios.py"
    spec = importlib.util.spec_from_file_location(ANTIMEM_SCENARIOS_KEY, path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses (3.14) resolve a class's module via sys.modules[cls.__module__]
    # while building it, so the module must be registered before exec_module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_scenarios = load_sibling_scenarios()
AllOf = _scenarios.AllOf
CommandRan = _scenarios.CommandRan
FileExists = _scenarios.FileExists
FileMatchCountAtMost = _scenarios.FileMatchCountAtMost
FileMatches = _scenarios.FileMatches
FileUnchanged = _scenarios.FileUnchanged
VerifyCommand = _scenarios.VerifyCommand
FileMutated = _scenarios.FileMutated
sha256_file = _scenarios.sha256_file

ActionPattern = Any  # BashAction | PathAction | ContentAction | ToolAction | FileMutated (scenarios.py)

# Verbatim from `_ANTI_MEMORY_TEMPLATE` (DESIGN.md 2) -- the one substring
# guaranteed present on every rendering surface regardless of surface-specific
# clause reordering (DESIGN.md 2's own note: both phrases appear on both
# surfaces, only their order differs).
DETERRENT_PHRASE = "Previously believed ("


# ---------------------------------------------------------------------------
# Transcript reading
# ---------------------------------------------------------------------------


def load_transcript(path: Path) -> list[dict]:
    """Parse a stream-json transcript file (one JSON object per line).

    Unparseable lines are skipped: the CLI occasionally interleaves non-JSON
    diagnostics on stdout and a single bad line must not void a whole leg.
    """
    events: list[dict] = []
    if not path.exists():
        return events
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# The action stream (DESIGN.md 7.1, inherited from the substrate's 6.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    idx: int  # position within the action stream (0-based)
    event_index: int  # position within the FULL event list this action's event sits at
    name: str
    input: Mapping[str, Any]


def build_action_stream(events: Sequence[dict]) -> list[Action]:
    """Every `tool_use` block, in transcript order. Text blocks are NOT actions --
    prose is not evidence (DESIGN.md 7.1)."""
    actions: list[Action] = []
    for ev_i, ev in enumerate(events):
        if ev.get("type") != "assistant":
            continue
        message = ev.get("message")
        if not isinstance(message, dict):
            continue
        for blk in message.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                actions.append(
                    Action(
                        idx=len(actions),
                        event_index=ev_i,
                        name=str(blk.get("name") or ""),
                        input=blk.get("input") or {},
                    )
                )
    return actions


def bash_commands(events: Iterable[dict]) -> list[str]:
    """Every `command` string from Bash tool_use blocks, in order."""
    return [
        str(a.input.get("command"))
        for a in build_action_stream(list(events))
        if a.name == "Bash"
        and isinstance(a.input.get("command"), str)
        and a.input.get("command").strip()
    ]


_PATH_INPUT_KEYS = ("file_path", "path", "notebook_path")


def _path_values(input_: Mapping[str, Any]) -> list[str]:
    return [str(input_[k]) for k in _PATH_INPUT_KEYS if input_.get(k) is not None]


# ---------------------------------------------------------------------------
# Exec-position anchoring (DEFECT 9, ported for this harness --
# UPG-ANTIMEM-EXEC-ANCHOR). `_strip_exec_prefixes`/`_exec_positions`/
# `_matches_at_exec_position` are DUPLICATED, not imported, from
# `longitudinal_rediscovery/scorer.py` (the DEFECT 9 originals -- same
# algorithm, byte-identical regexes) for the same reason this module's own
# docstring already gives for every other duplicated primitive here:
# importing would bind these functions to the LONGITUDINAL harness's
# `scenarios.py` module object while this harness's own `BashAction`/
# `CommandRan` instances come from a different module object loaded under
# `ANTIMEM_SCENARIOS_KEY`. See the longitudinal original's section comment
# for the full semantic writeup this port reuses verbatim: a plain
# `re.search` finds a pattern ANYWHERE in a Bash `command` string, so a
# read-only mention (`cat deploy.sh`, `grep -n foo deploy.sh`) satisfies a
# signature written to detect an actual invocation (`./deploy.sh`) just as
# readily as the real thing. Anchoring instead requires the pattern to match
# starting at a genuine command-EXECUTION position: the start of the
# string, or immediately after a shell command separator (`;`, `&&`, `||`,
# `|`, `&`, or a newline), optionally through one or more interpreter/
# exec-wrapper tokens a real shell allows to precede the invoked program
# (`env`, one or more `VAR=value` assignments, `sh`/`bash`/`zsh`/`exec`,
# `timeout N`, `caffeinate -flags`).
# ---------------------------------------------------------------------------

_EXEC_PREFIX_TOKEN = re.compile(
    r"""
    \s*
    (?:
        [A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"]*"|\S*)\s+   # one VAR=value assignment
      | env\s+                                              # env (itself often followed
                                                              # by more VAR= tokens, looped)
      | (?:sh|bash|zsh|exec)\s+                              # interpreter / exec keyword
      | timeout\s+\S+\s+                                     # timeout N
      | caffeinate(?:\s+-\S+)*\s+                            # caffeinate [-flags]
    )?
    """,
    re.VERBOSE,
)

_EXEC_BOUNDARY = re.compile(r"\A|[;&|\n]")


def _strip_exec_prefixes(command: str) -> str:
    """Repeatedly strip leading whitespace and interpreter/exec-wrapper tokens off
    the FRONT of `command` so a pattern lands on the actually-invoked program, not
    its wrapper (`env FOO=bar timeout 30 bash deploy.sh`) -- loops until a pass
    makes no further progress rather than assuming one fixed order or count.
    """
    remainder = command
    while True:
        m = _EXEC_PREFIX_TOKEN.match(remainder)
        end = m.end() if m else 0
        if end == 0:
            return remainder
        remainder = remainder[end:]


def _exec_positions(command: str) -> list[int]:
    """Every index in `command` where a new command begins: 0, or immediately
    after `;`, `&&`, `||`, `|`, `&`, or a newline.
    """
    return [m.end() for m in _EXEC_BOUNDARY.finditer(command)]


def _matches_at_exec_position(pattern: str, command: str) -> bool:
    """True when `pattern` matches starting at some genuine command-execution
    position in `command`, after stripping any interpreter/exec-wrapper prefix at
    that position. Uses `re.match`, not `re.search`: the pattern must START at the
    candidate position, not merely occur somewhere after it.
    """
    for pos in _exec_positions(command):
        if re.match(pattern, _strip_exec_prefixes(command[pos:])):
            return True
    return False


def _pattern_matches(action: Action, pattern: ActionPattern) -> bool:
    """Dispatch one action-pattern primitive (BashAction, PathAction, ContentAction,
    ToolAction) against one action, by `type(pattern).__name__` rather than
    `isinstance` -- robust to `pattern` coming from a differently-loaded copy of
    `scenarios.py`'s longitudinal-reexported classes, unlike `evaluate_check`'s
    `isinstance` dispatch below, which is why this module always loads
    `scenarios.py` through `load_sibling_scenarios()`. `FileMutated` is
    deliberately not handled here -- it is end-of-leg file STATE, not a point in
    the action stream; see `any_file_mutated`.
    """
    kind = type(pattern).__name__
    if kind == "BashAction":
        if action.name != "Bash":
            return False
        command = str(action.input.get("command") or "")
        if getattr(pattern, "exec_anchor", False):
            return _matches_at_exec_position(pattern.pattern, command)
        return bool(re.search(pattern.pattern, command))
    if kind == "PathAction":
        if action.name not in pattern.tools:
            return False
        return any(re.search(pattern.pattern, v) for v in _path_values(action.input))
    if kind == "ContentAction":
        if action.name not in pattern.tools:
            return False
        if not any(re.search(pattern.path_pattern, v) for v in _path_values(action.input)):
            return False
        text = action.input.get("new_string")
        if text is None:
            text = action.input.get("content")
        return bool(re.search(pattern.text_pattern, str(text or "")))
    if kind == "ToolAction":
        return action.name in pattern.names
    raise TypeError(f"unknown action pattern type {kind!r}")


def _action_matches_any(action: Action, patterns: Sequence[ActionPattern]) -> bool:
    return any(_pattern_matches(action, p) for p in patterns if type(p).__name__ != "FileMutated")


def first_index(actions: Sequence[Action], patterns: Sequence[ActionPattern]) -> int | None:
    """Smallest action idx matching ANY pattern (OR semantics); `None` if none match.

    `FileMutated` entries within `patterns` are skipped here -- they have no action
    index to return (see `any_file_mutated` for that half of `backflow_shipped`).
    """
    action_patterns = [p for p in patterns if type(p).__name__ != "FileMutated"]
    if not action_patterns:
        return None
    for action in actions:
        if _action_matches_any(action, action_patterns):
            return action.idx
    return None


def any_file_mutated(
    patterns: Sequence[ActionPattern],
    *,
    workspace: Path,
    leg_start_baselines: Mapping[str, str],
) -> bool:
    """True if any `FileMutated` entry in `patterns` differs from its LEG-START
    baseline (not scenario-materialization baseline)."""
    for p in patterns:
        if type(p).__name__ != "FileMutated":
            continue
        target = workspace / p.path
        if not target.is_file():
            continue
        if sha256_file(target) != leg_start_baselines.get(p.path):
            return True
    return False


# ---------------------------------------------------------------------------
# Check evaluation -- own `isinstance()` bindings against THIS module's own
# `load_sibling_scenarios()`-loaded classes (see module docstring).
# ---------------------------------------------------------------------------


def _read(workspace: Path, rel: str) -> str | None:
    target = workspace / rel
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def evaluate_check(
    check: object,
    *,
    workspace: Path,
    baselines: Mapping[str, str],
    commands: Sequence[str],
    verify_dir: Path,
) -> dict[str, Any]:
    """Evaluate one declared check into {name, passed, detail}. `baselines` are
    always the current LEG's start-of-leg baselines (DESIGN.md 6.2's residue
    rule, inherited)."""
    if isinstance(check, FileExists):
        exists = (workspace / check.path).is_file()
        return {
            "name": check.name,
            "passed": exists == check.want,
            "detail": f"{check.path} {'exists' if exists else 'absent'} (want={check.want})",
        }

    if isinstance(check, FileMatches):
        content = _read(workspace, check.path)
        if content is None:
            return {"name": check.name, "passed": False, "detail": f"missing file {check.path}"}
        found = re.search(check.pattern, content) is not None
        return {
            "name": check.name,
            "passed": found == check.want,
            "detail": f"pattern {'found' if found else 'absent'} in {check.path} (want={check.want})",
        }

    if isinstance(check, FileMatchCountAtMost):
        content = _read(workspace, check.path)
        if content is None:
            return {"name": check.name, "passed": False, "detail": f"missing file {check.path}"}
        count = len(re.findall(check.pattern, content))
        return {
            "name": check.name,
            "passed": count <= check.limit,
            "detail": f"{count} occurrence(s) in {check.path}, limit {check.limit}",
        }

    if isinstance(check, FileUnchanged):
        target = workspace / check.path
        if not target.is_file():
            return {"name": check.name, "passed": False, "detail": f"missing file {check.path}"}
        actual = sha256_file(target)
        expected = baselines.get(check.path)
        return {
            "name": check.name,
            "passed": actual == expected,
            "detail": "sha256 matches leg-start baseline" if actual == expected else "file was modified this leg",
        }

    if isinstance(check, CommandRan):
        if getattr(check, "exec_anchor", False):
            matched = [c for c in commands if _matches_at_exec_position(check.pattern, c)]
        else:
            matched = [c for c in commands if re.search(check.pattern, c)]
        found = bool(matched)
        return {
            "name": check.name,
            "passed": found == check.want,
            "detail": (
                f"{len(matched)} matching command(s) (want={check.want})"
                + (f"; first: {matched[0][:160]}" if matched else "")
            ),
        }

    if isinstance(check, AllOf):
        subs = [
            evaluate_check(sub, workspace=workspace, baselines=baselines, commands=commands, verify_dir=verify_dir)
            for sub in check.of
        ]
        passed = all(s["passed"] for s in subs)
        return {
            "name": check.name,
            "passed": passed,
            "detail": "; ".join(f"{s['name']}={'PASS' if s['passed'] else 'FAIL'}" for s in subs),
            "sub_checks": subs,
        }

    if isinstance(check, VerifyCommand):
        argv = [a.replace("{verify_dir}", str(verify_dir)) for a in check.argv]
        try:
            proc = subprocess.run(argv, cwd=str(workspace), capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"name": check.name, "passed": False, "detail": f"verify command failed to run: {type(exc).__name__}"}
        passed = proc.returncode == check.expect_returncode
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        return {
            "name": check.name,
            "passed": passed,
            "detail": f"exit {proc.returncode} (want {check.expect_returncode})" + (f"; {tail[-1][:200]}" if tail else ""),
        }

    raise TypeError(f"unknown check type {type(check).__name__}")


def _check_backflow_content(
    items: Sequence[object],
    *,
    workspace: Path,
    baselines: Mapping[str, str],
    commands: Sequence[str],
    verify_dir: Path,
) -> tuple[bool, list[dict[str, Any]]]:
    """`spec.backflow_content` is an OR-list of positive evidence (DESIGN.md 7.2:
    `any(spec.backflow_content matched against final bytes)`). Each item is
    either a `FileMutated` (state, evaluated against leg-start baselines like
    `any_file_mutated`) or a read-only check (evaluated via `evaluate_check`).
    None of this eval's four scenarios put a `VerifyCommand` in `backflow_content`
    (verified in `scenarios.py`'s own module docstring); read-only evaluation
    order is therefore safe without the mutating-checks-last split `score_run`
    applies to `correct_check`.
    """
    results: list[dict[str, Any]] = []
    shipped = False
    for item in items:
        if type(item).__name__ == "FileMutated":
            target = workspace / item.path
            mutated = target.is_file() and sha256_file(target) != baselines.get(item.path)
            results.append({"name": f"FileMutated({item.path})", "passed": mutated, "detail": "sha256 differs from leg-start baseline" if mutated else "unchanged"})
            shipped = shipped or mutated
        else:
            r = evaluate_check(item, workspace=workspace, baselines=baselines, commands=commands, verify_dir=verify_dir)
            results.append(r)
            shipped = shipped or bool(r["passed"])
    return shipped, results


# ---------------------------------------------------------------------------
# The three-way outcome (DESIGN.md 7.2) and per-leg fields (DESIGN.md 7.3)
# ---------------------------------------------------------------------------


def recontamination_write(actions: Sequence[Action], scenario: Any) -> bool:
    """DESIGN.md 7.3 `recontamination_write`: any `mcp__vectr__vectr_remember`
    action whose `content` echoes F_old's own signature. Observed only, never
    manipulated (DESIGN.md 16.8). Reuses `backflow_signature`'s own
    `ContentAction` text patterns -- the same regex(es) already authored, once,
    at scenario-design time to detect F_old's belief in a workspace EDIT --
    checked here against a memory WRITE instead. Not a query-content heuristic:
    the pattern is fixed per scenario before any leg runs and is applied
    uniformly to whatever action stream comes in, never derived from a live
    query.
    """
    patterns = [p.text_pattern for p in scenario.backflow_signature if type(p).__name__ == "ContentAction"]
    if not patterns:
        return False
    for a in actions:
        if a.name != "mcp__vectr__vectr_remember":
            continue
        content = str(a.input.get("content") or "")
        if any(re.search(p, content) for p in patterns):
            return True
    return False


def score_run(
    scenario: Any,
    *,
    workspace: Path,
    leg_start_baselines: Mapping[str, str],
    transcript: Sequence[dict],
    verify_dir: Path,
) -> dict[str, Any]:
    """DESIGN.md 7.2/7.3. Arm-blind by construction -- no `arm` parameter
    anywhere in this function or in anything it calls; `arm` is consulted only
    in `cell_non_vacuity`/`store_state_non_vacuity` below, which answer a
    different question (did the channel fire?), never the outcome verdict.

    Evaluation order: `backflow_content` (always read-only for all four
    scenarios -- see `_check_backflow_content`'s docstring) before
    `correct_check`, which for A2/A4 IS a `VerifyCommand` that may mutate the
    workspace (its own verify script). Read-before-mutate, same discipline the
    substrate's `score_run` applies to `AllOf` vs `VerifyCommand` siblings.
    """
    actions = build_action_stream(transcript)
    commands = bash_commands(transcript)

    backflow_shipped, backflow_checks = _check_backflow_content(
        scenario.backflow_content, workspace=workspace, baselines=leg_start_baselines,
        commands=commands, verify_dir=verify_dir,
    )
    correct_check_result = evaluate_check(
        scenario.correct_check, workspace=workspace, baselines=leg_start_baselines,
        commands=commands, verify_dir=verify_dir,
    )
    correct = bool(correct_check_result["passed"]) and not backflow_shipped
    outcome = "BACKFLOW" if backflow_shipped else ("CORRECT" if correct else "NEITHER")

    backflow_attempt_idx = first_index(actions, scenario.backflow_signature)
    backflow_attempted = backflow_attempt_idx is not None
    self_corrected = backflow_attempted and not backflow_shipped

    stale_read_idx = first_index(actions, scenario.stale_artifact_read)
    truth_read_idx = first_index(actions, scenario.truth_source_read)

    # DESIGN.md 7.3 `verified_before_acting`: truth_read at a lower action index
    # than the first backflow (or correct) action. No `correct`-side action
    # pattern is declared anywhere in this design (only `backflow_signature`
    # is), so this is computed relative to the first backflow attempt, and is
    # `None` ("not applicable") when backflow was never attempted at all --
    # never guessed, matching the substrate's own null-not-imputed rule.
    verified_before_acting: bool | None = None
    if backflow_attempted and truth_read_idx is not None:
        verified_before_acting = truth_read_idx < backflow_attempt_idx

    recall_called = sum(1 for a in actions if a.name == "mcp__vectr__vectr_recall")

    return {
        "outcome": outcome,
        "backflow_shipped": backflow_shipped,
        "backflow_checks": backflow_checks,
        "correct_check": correct_check_result,
        "backflow_attempted": backflow_attempted,
        "backflow_attempt_action_index": backflow_attempt_idx,
        "self_corrected": self_corrected,
        "stale_read": stale_read_idx is not None,
        "stale_read_action_index": stale_read_idx,
        "truth_read": truth_read_idx is not None,
        "truth_read_action_index": truth_read_idx,
        "verified_before_acting": verified_before_acting,
        "recall_called": recall_called,
        "recontamination_write": recontamination_write(actions, scenario),
    }


def score_leg(
    scenario: Any,
    *,
    workspace: Path,
    leg_start_baselines: Mapping[str, str],
    transcript_path: Path,
    verify_dir: Path,
) -> dict[str, Any]:
    """`score_run` wrapped with the one degradation DESIGN.md 13's T0-8 asks
    for explicitly: a missing transcript (leg never produced one -- an agent
    spawn failure, a killed process, a not-yet-run T0 probe cell) degrades to
    `{"valid": False, ...}` rather than raising or silently scoring an empty
    action stream as if the leg had genuinely run. `load_transcript` itself
    already tolerates a missing/unparseable file by returning `[]`; this
    wrapper is the point that turns "no evidence" into an explicit `valid`
    flag instead of a fabricated verdict over that empty evidence.
    """
    if not transcript_path.exists():
        return {"valid": False, "invalid_reason": f"missing transcript: {transcript_path}", "score": None}
    transcript = load_transcript(transcript_path)
    result = score_run(
        scenario, workspace=workspace, leg_start_baselines=leg_start_baselines,
        transcript=transcript, verify_dir=verify_dir,
    )
    return {"valid": True, "invalid_reason": "", "score": result}


# ---------------------------------------------------------------------------
# Session-level cost/injection metrics (DESIGN.md 7.3, T1+ only -- read
# straight off the transcript's own `result` / audit-log fields, never
# imputed).
# ---------------------------------------------------------------------------


_BILLABLE_TOKEN_WEIGHTS: dict[str, float] = {
    "input_tokens": 1.0,
    "cache_creation_input_tokens": 1.25,
    "cache_read_input_tokens": 0.1,
    "output_tokens": 5.0,
}


def _billable_tokens(usage: Mapping[str, Any]) -> float:
    return sum(weight * float(usage.get(field_name) or 0) for field_name, weight in _BILLABLE_TOKEN_WEIGHTS.items())


def cost_metrics(events: Sequence[dict]) -> dict[str, Any]:
    """Session-granularity cost figures read straight off the transcript's
    `result` event. DESIGN.md 7.3: only `session_usd`/`num_turns`/
    `duration_api_ms` are carried from the substrate; the RDC token-allocation
    math is explicitly NOT used here."""
    result = next((e for e in reversed(events) if e.get("type") == "result"), {}) or {}
    usage = result.get("usage") or {}
    names = [a.name for a in build_action_stream(events)]
    return {
        "session_turns": result.get("num_turns"),
        "session_usd": result.get("total_cost_usd"),
        "session_duration_ms": result.get("duration_ms"),
        "session_duration_api_ms": result.get("duration_api_ms"),
        "output_tokens": (usage.get("output_tokens")),
        "billable_tokens_session": _billable_tokens(usage),
        "tool_calls": len(names),
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
    }


_INJECT_LINE = re.compile(r"PROACTIVE_INJECT\b(?P<rest>.*)$")
_KV = re.compile(r"(\w+)=([^\s]*)")


def parse_injection_events(audit_log: Path, *, since_offset: int = 0) -> list[dict[str, str]]:
    """`PROACTIVE_INJECT` lines out of a `VECTR_AUDIT_LOG` file. `since_offset`
    skips the harness's own preflight-probe traffic, which hits the same audit
    log. Adapted from `longitudinal_rediscovery/scorer.py`'s function of the
    same name/behaviour -- same log format, same daemon."""
    events: list[dict[str, str]] = []
    if not audit_log.exists():
        return events
    raw = audit_log.read_bytes()[since_offset:].decode("utf-8", errors="replace")
    for line in raw.splitlines():
        m = _INJECT_LINE.search(line)
        if not m:
            continue
        events.append(dict(_KV.findall(m.group("rest"))))
    return events


def injected_chars_from_audit(audit_log: Path, *, since_offset: int = 0) -> int:
    """DESIGN.md 7.3 `injected_chars`: total characters of proactive context
    delivered, from the audit log."""
    return sum(len(e.get("chars") or e.get("context") or "") for e in parse_injection_events(audit_log, since_offset=since_offset))


def deterrent_delivered_from_audit(audit_log: Path, *, since_offset: int = 0) -> bool:
    """DESIGN.md 7.3 `deterrent_delivered`: post-offset `PROACTIVE_INJECT` line
    containing the deterrent phrase."""
    if not audit_log.exists():
        return False
    raw = audit_log.read_bytes()[since_offset:].decode("utf-8", errors="replace")
    for line in raw.splitlines():
        if _INJECT_LINE.search(line) and DETERRENT_PHRASE in line:
            return True
    return False


# ---------------------------------------------------------------------------
# Tier-0 ($0) REST preflight helpers -- duplicated from `arm_store.py`'s own
# `_http_json` rather than imported, matching this codebase's established
# convention of duplicating small process/HTTP plumbing per harness module
# (`injection_utility/run_harness.py`, `longitudinal_rediscovery/run_leg.py`,
# `arm_store.py` each carry their own copy).
# ---------------------------------------------------------------------------


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", "replace")[:800]}
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a probe failure
        return {"_error": type(exc).__name__, "_detail": str(exc)}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body[:800]}


def _ok(resp: dict) -> bool:
    return "_error" not in resp and "_http_error" not in resp


def _proactive(base_url: str, *, text: str, session_id: str = "antimem-t0-probe", channel: str = "proxy") -> dict:
    return _http_json("POST", f"{base_url}/v1/proactive", {"text": text, "session_id": session_id, "channel": channel})


def _recall_full_dump(base_url: str) -> dict:
    """The content-free "dump everything" recall (no `query`, no `note_id`, no
    `boot`): every note fully expanded, DESIGN.md 13's T0-5 own preflight shape
    ("ARM-AUDIT's `/v1/recall` preflight returns both notes with the deterrent
    rendering"). Not conditioned on any query content -- an administrative
    full-detail retrieval, legitimate under the no-query-heuristics rule."""
    return _http_json("POST", f"{base_url}/v1/recall", {"detail": "full"})


# ---------------------------------------------------------------------------
# T0-2 .. T0-6: the raw-daemon half of DESIGN.md 5.4's per-arm gate table.
# Uses ONLY `/v1/proactive` and `/v1/recall` against the daemon directly, which
# is what DESIGN.md 13's own T0 checklist specifies (T0-2..T0-4 read the
# `/v1/proactive` preflight; T0-5/T0-6's "its `/v1/proactive` preflight returns
# empty" is checked at the PROXY layer instead -- see `proxy_delivery_check`,
# because the raw daemon endpoint is arm-agnostic by design and never returns
# empty for a non-empty store regardless of arm; DESIGN.md 2 fact 2).
# ---------------------------------------------------------------------------


def store_state_non_vacuity(scenario: Any, arm: str, store_state: Any, *, base_url: str) -> dict[str, Any]:
    """DESIGN.md 13 T0-2/T0-3/T0-4/T0-5(recall half)/T0-6(notes_count half):
    the arm-specific raw-daemon preflight gate. `store_state` is an
    `arm_store.StoreState` (duck-typed here: only `.arm`, `.notes_count`,
    `.old_note_id`, `.new_note_id`, `.reason_variant` are read, so a hand-built
    fixture works too for T0-8-style tests without a live daemon)."""
    reasons: list[str] = []
    checks: dict[str, Any] = {}

    proactive = _proactive(base_url, text=scenario.prompt)
    if not _ok(proactive):
        reasons.append(f"/v1/proactive preflight failed: {proactive}")
    context = str(proactive.get("context") or "")
    item_count = proactive.get("item_count")
    checks["proactive_item_count"] = item_count
    checks["proactive_context_chars"] = len(context)

    if arm == "ARM-DEL":
        if store_state.notes_count != 0:
            reasons.append(f"ARM-DEL: notes_count={store_state.notes_count}, want 0")
        if item_count != 0 or context != "":
            reasons.append(f"ARM-DEL: /v1/proactive preflight not empty (item_count={item_count!r}, context={context[:120]!r})")

    elif arm == "ARM-REPLACE":
        if store_state.notes_count != 1:
            reasons.append(f"ARM-REPLACE: notes_count={store_state.notes_count}, want 1")
        new_fact_probe = scenario.new_fact_sentence.strip().split(". ", 1)[0][:40]
        if new_fact_probe not in context:
            reasons.append(f"ARM-REPLACE: /v1/proactive context missing F_new content {new_fact_probe!r}: {context[:200]!r}")
        if DETERRENT_PHRASE in context:
            reasons.append(f"ARM-REPLACE: /v1/proactive context contains the deterrent phrase (ablation not clean): {context[:200]!r}")

    elif arm == "ARM-DETERRENT":
        if store_state.notes_count != 2:
            reasons.append(f"ARM-DETERRENT: notes_count={store_state.notes_count}, want 2")
        if DETERRENT_PHRASE not in context:
            reasons.append(f"ARM-DETERRENT: /v1/proactive context missing deterrent phrase: {context[:200]!r}")
        reason_text = scenario.revocation_reasons.get(store_state.reason_variant)
        if reason_text not in context:
            reasons.append(
                f"ARM-DETERRENT: /v1/proactive context missing the {store_state.reason_variant!r} reason "
                f"verbatim (DESIGN.md 13 T0-4 -- likely _cap() truncation): {context[:300]!r}"
            )
        checks["reason_verbatim_present"] = reason_text in context

    elif arm == "ARM-AUDIT":
        if store_state.notes_count != 2:
            reasons.append(f"ARM-AUDIT: notes_count={store_state.notes_count}, want 2")
        dump = _recall_full_dump(base_url)
        if not _ok(dump):
            reasons.append(f"/v1/recall full-dump preflight failed: {dump}")
        dump_text = str(dump.get("notes") or "")
        if DETERRENT_PHRASE not in dump_text:
            reasons.append(f"ARM-AUDIT: /v1/recall full-dump missing deterrent phrase: {dump_text[:200]!r}")
        new_fact_probe = scenario.new_fact_sentence.strip().split(". ", 1)[0][:40]
        if new_fact_probe not in dump_text:
            reasons.append(f"ARM-AUDIT: /v1/recall full-dump missing F_new content {new_fact_probe!r} (both notes must be reachable): {dump_text[:300]!r}")
        checks["recall_dump_chars"] = len(dump_text)

    else:
        raise ValueError(f"unknown arm {arm!r}")

    return {"arm": arm, "valid": not reasons, "invalid_reason": "; ".join(reasons), "checks": checks}


# ---------------------------------------------------------------------------
# T0-2/T0-5's delivery-channel half: exercised through the REAL proxy app
# (`agent/proactive/proxy.py`'s `build_proxy_app`), in-process against an
# httpx ASGI transport, driven at a mocked upstream so no live model call is
# made. This is the part the raw daemon endpoint cannot prove (DESIGN.md 2
# fact 2: the proxy's candidate selection never reads `triggers`; only
# `ProactiveSettings.proxy_inject` gates delivery, and that gate lives on the
# proxy, not the daemon).
# ---------------------------------------------------------------------------

# Settings duplicated from `tests/test_proactive_proxy.py`'s own `_BASE` dict
# (a test-only module, not importable from `benchmarks/`) rather than
# cross-imported, matching this file's own duplication rule (module
# docstring). `max_chars_per_event: 800` matches DESIGN.md 2 fact 5's own
# cited shipped default -- not an arbitrary test value. `proxy_inject_budget_ms`
# is raised from the test file's 40ms (tuned for a mocked, in-memory injection
# provider) to 5000ms here, because this harness's `DaemonInjectionProvider`
# makes a REAL HTTP round trip to a REAL daemon, which can legitimately take
# longer than a unit test's synchronous mock under load.
_PROXY_SETTINGS_BASE: dict[str, Any] = dict(
    enabled=True, min_similarity=0.35, max_items_per_event=3, max_chars_per_event=800,
    cooldown_items=30, cooldown_ttl_seconds=None,
    matcher_structural_note=True, matcher_semantic_note=True,
    matcher_code_search=False, proxy_host="127.0.0.1", proxy_port=19000,
    proxy_upstream_base_url="http://upstream", proxy_connect_timeout_s=10.0,
    proxy_read_timeout_s=600.0, proxy_inject=True, proxy_inject_budget_ms=5000,
    proxy_inject_provider_timeout_fraction=0.95, proxy_inject_provider_timeout_max_s=5.0,
    proxy_exclude_directive_notes=True,
    cache_enabled=False, cache_max_entries=2048, cache_ttl_seconds=0.0,
    cache_similarity_threshold=1.0, response_cache_enabled=False,
    response_cache_ttl_seconds=60.0, response_cache_max_entries=256,
    structural_kinds=("gotcha", "finding", "decision", "operational", "reference"),
    structural_overfetch_multiplier=4, structural_overfetch_ceiling=60,
    structural_score_declared_anchor=1.0, structural_score_gotcha_mention=0.9,
    structural_score_mention=0.6, max_weak_structural_items=1,
)

# ARM-AUDIT is the only arm run D-PASSIVE (DESIGN.md 5.1/5.2); every other arm
# (including ARM-DEL, which has nothing to inject) runs D-PROACTIVE.
_PROXY_INJECT_FOR_ARM: dict[str, bool] = {
    "ARM-DEL": True, "ARM-REPLACE": True, "ARM-AUDIT": False, "ARM-DETERRENT": True,
}


class _RecordingUpstream:
    """A minimal mocked upstream: records the JSON body the proxy forwards and
    returns a canned non-streaming Anthropic-shaped response. Purpose-built
    here rather than reusing `tests/_proactive_upstream.MockUpstream` (a
    test-only module) so `benchmarks/` carries no dependency on `tests/` --
    but structurally MIRRORS it exactly on the one point that matters: a real
    Starlette app behind `httpx.ASGITransport`, not a bare handler behind
    `httpx.MockTransport`. `proxy.py`'s `_forward` sends with `stream=True`
    and consumes the upstream response via `upstream.aiter_raw()`; a
    `MockTransport`-backed response's body is pre-buffered in a way that
    reading it through that streamed path raises `httpx.StreamConsumed`
    (caught by this module's own `--probe-only` smoke test against a live
    daemon) -- an ASGI app's response is genuinely streamed instead, which is
    what `_forward` actually expects. `client_factory()`'s `base_url` is
    likewise required: `_forward` calls `client.build_request(method, path,
    ...)` with a RELATIVE path and relies on the client's own `base_url` to
    resolve it into an absolute URL (confirmed against `MockUpstream.client_
    factory`'s identical `base_url="http://upstream"`, for the same reason).
    """

    def __init__(self) -> None:
        self.requests: list[dict | None] = []

    async def _handle(self, request):  # starlette.requests.Request -> starlette.responses.Response
        from starlette.responses import JSONResponse

        body = await request.body()
        try:
            parsed = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = None
        self.requests.append(parsed)
        return JSONResponse({
            "id": "msg_antimem_probe", "type": "message", "role": "assistant",
            "model": "claude-antimem-probe", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    def _app(self):
        from starlette.applications import Starlette
        from starlette.routing import Route

        return Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])])

    def client_factory(self):
        import httpx

        app = self._app()

        def factory() -> "httpx.AsyncClient":
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://upstream")

        return factory

    @property
    def last_forwarded(self) -> dict | None:
        return self.requests[-1] if self.requests else None


def _proactive_message_body(prompt_text: str) -> dict:
    return {
        "model": "claude-antimem-probe",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}]}],
    }


async def _proxy_delivery_check_async(scenario: Any, arm: str, *, daemon_base_url: str) -> dict[str, Any]:
    from agent.proactive.provider import DaemonInjectionProvider
    from agent.proactive.proxy import build_proxy_app
    from agent.proactive.settings import ProactiveSettings
    import httpx

    if arm not in _PROXY_INJECT_FOR_ARM:
        raise ValueError(f"unknown arm {arm!r}")
    proxy_inject = _PROXY_INJECT_FOR_ARM[arm]

    settings = ProactiveSettings(**{**_PROXY_SETTINGS_BASE, "proxy_inject": proxy_inject})
    provider = DaemonInjectionProvider(daemon_base_url, timeout_s=5.0)
    upstream = _RecordingUpstream()
    app = build_proxy_app(settings, injection_provider=provider, client_factory=upstream.client_factory())

    body = _proactive_message_body(scenario.prompt)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://antimem-proxy-probe") as client:
        resp = await client.post("/v1/messages", content=json.dumps(body), headers={"content-type": "application/json"})

    forwarded = upstream.last_forwarded
    injected_text = ""
    if forwarded is not None:
        last_content = (forwarded.get("messages") or [{}])[-1].get("content")
        if isinstance(last_content, list) and len(last_content) > 1:
            tail = last_content[-1]
            if isinstance(tail, dict) and tail.get("type") == "text":
                injected_text = str(tail.get("text") or "")

    return {
        "arm": arm,
        "proxy_inject_setting": proxy_inject,
        "response_status": resp.status_code,
        "injected": bool(injected_text),
        "injected_chars": len(injected_text),
        "injected_text": injected_text,
        "contains_deterrent_phrase": DETERRENT_PHRASE in injected_text,
    }


def proxy_delivery_check(scenario: Any, arm: str, *, daemon_base_url: str) -> dict[str, Any]:
    """Sync entry point for callers not already inside an event loop (T0
    probe scripts, sync pytest tests). Use `_proxy_delivery_check_async`
    directly from an `async def` test."""
    return asyncio.run(_proxy_delivery_check_async(scenario, arm, daemon_base_url=daemon_base_url))


def proxy_non_vacuity(scenario: Any, arm: str, delivery: dict[str, Any]) -> dict[str, Any]:
    """DESIGN.md 13 T0-2/T0-5(proxy half)/T0-6(proxy half): the proxy-layer
    completion of `store_state_non_vacuity`, given an already-computed
    `proxy_delivery_check` result."""
    reasons: list[str] = []
    if arm == "ARM-AUDIT":
        if delivery["injected"]:
            reasons.append(f"ARM-AUDIT: proxy injected content despite proxy_inject=False: {delivery['injected_text'][:200]!r}")
    elif arm == "ARM-DEL":
        if delivery["injected"]:
            reasons.append(f"ARM-DEL: proxy injected content from an empty store: {delivery['injected_text'][:200]!r}")
    elif arm == "ARM-DETERRENT":
        if not delivery["injected"]:
            reasons.append("ARM-DETERRENT: proxy injected nothing (want deterrent content)")
        elif not delivery["contains_deterrent_phrase"]:
            reasons.append(f"ARM-DETERRENT: proxy injected content without the deterrent phrase: {delivery['injected_text'][:200]!r}")
    elif arm == "ARM-REPLACE":
        if not delivery["injected"]:
            reasons.append("ARM-REPLACE: proxy injected nothing (want F_new content)")
        elif delivery["contains_deterrent_phrase"]:
            reasons.append(f"ARM-REPLACE: proxy injected content contains the deterrent phrase (ablation not clean): {delivery['injected_text'][:200]!r}")
    else:
        raise ValueError(f"unknown arm {arm!r}")
    return {"arm": arm, "valid": not reasons, "invalid_reason": "; ".join(reasons), "delivery": delivery}


def cell_non_vacuity(
    scenario: Any,
    arm: str,
    store_state: Any,
    *,
    base_url: str,
    daemon_base_url: str | None = None,
    session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The full DESIGN.md 5.4 per-arm gate, composed from its three
    independently testable halves:

    - `store_state_non_vacuity` (T0, raw daemon REST)
    - `proxy_non_vacuity` (T0, in-process real proxy app)
    - `session` (T1+, transcript-derived: `system.init`, `mcp_servers`,
      `result.subtype`, restored-manifest sha256 -- DESIGN.md 5.4's "all"
      row). `None` in probe-only (T0) mode, where no agent session exists;
      the combined verdict then reflects only the store+proxy halves and
      `session_checked` is `False` so a caller can tell T0-scope validity
      apart from full T1+ validity.
    """
    store = store_state_non_vacuity(scenario, arm, store_state, base_url=base_url)
    delivery = proxy_delivery_check(scenario, arm, daemon_base_url=daemon_base_url or base_url)
    proxy = proxy_non_vacuity(scenario, arm, delivery)

    reasons = [r for r in (store["invalid_reason"], proxy["invalid_reason"]) if r]
    session_checked = session is not None
    if session is not None:
        for key in ("system_init_present", "mcp_servers_ok", "result_success", "manifest_ok"):
            if session.get(key) is False:
                reasons.append(f"session gate failed: {key}")

    return {
        "arm": arm,
        "valid": not reasons,
        "invalid_reason": "; ".join(reasons),
        "session_checked": session_checked,
        "store": store,
        "proxy": proxy,
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    print(f"scorer.py loaded scenarios.py as {ANTIMEM_SCENARIOS_KEY!r}: {list(_scenarios.SCENARIOS)}")
