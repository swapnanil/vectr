#!/usr/bin/env python3
"""EVAL-LONGITUDINAL-REDISCOVERY -- mechanical, arm-blind, per-leg scorer.

Grades one completed LEG against the ground truth its `LegSpec` declared before the
leg ran (see `scenarios.py`). Every verdict comes from exactly one of: the final bytes
of a workspace file (regex / occurrence count / sha256), the ordered `tool_use` blocks
of the leg's `stream-json` transcript, the exit code of a scenario-owned verify script,
or the transcript's own `result` / `assistant.message.usage` fields (DESIGN.md 6.1).
No LLM judges a run. Nothing here reads the agent's prose: an agent that merely SAYS it
will use `./tools/t` scores exactly like one that says nothing.

`score_run()` is deliberately arm-blind -- it takes a workspace, leg-start baselines
and a transcript, and has no `arm` parameter at all. Arm-specific reasoning lives only
in `leg_non_vacuity(arm=...)`, which answers a different question (did this leg's
memory channel actually fire?) and gates validity, never the outcome verdict.

The check primitives (`FileMatches`, `FileMatchCountAtMost`, `FileUnchanged`,
`CommandRan`, `VerifyCommand`, `AllOf`, `sha256_file`, plus this eval's own
`FileMatchCountAtLeast`) come from `scenarios.py` in this directory, which itself
re-exports the trap harness's primitives by import (see that file's docstring) --
never redefined here either.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# `benchmarks/injection_utility/` has its own `scorer.py` AND its own `scenarios.py`.
# A plain `sys.path.insert` + `from scenarios import ...` keys off the bare module
# name `scenarios`, which both harnesses claim; whichever imports first in a shared
# interpreter (pytest collecting both harnesses' test files in one process) wins
# `sys.modules["scenarios"]` for the rest of the process, so the loser silently binds
# the wrong module's attributes (observed: `AttributeError` / stale `SCENARIOS`
# resolving to the trap harness's slugs). Loading THIS directory's `scenarios.py` by
# explicit file path under a fixed, private `sys.modules` key sidesteps the collision
# regardless of import order -- the same technique `scenarios.py`'s own
# `_load_trap_harness_scenarios()` already uses for the reverse direction. The key is
# checked first (not re-executed) so every caller in the process -- this module, and
# `tests/test_longitudinal_scorer.py`, which loads the identical key/path pair --
# converges on ONE module object, and therefore the same check-primitive classes
# `isinstance()` dispatch in `evaluate_check()` below depends on.
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"


def _load_sibling_scenarios():
    cached = sys.modules.get(_LONGITUDINAL_SCENARIOS_KEY)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "scenarios.py"
    spec = importlib.util.spec_from_file_location(_LONGITUDINAL_SCENARIOS_KEY, path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses (3.14) resolves a class's module via sys.modules[cls.__module__]
    # while building it, so the module must be registered before exec_module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_scenarios = _load_sibling_scenarios()
AllOf = _scenarios.AllOf
CommandRan = _scenarios.CommandRan
FileMatchCountAtLeast = _scenarios.FileMatchCountAtLeast
FileMatchCountAtMost = _scenarios.FileMatchCountAtMost
FileMatches = _scenarios.FileMatches
FileUnchanged = _scenarios.FileUnchanged
LegSpec = _scenarios.LegSpec
NoteVariant = _scenarios.NoteVariant
VerifyCommand = _scenarios.VerifyCommand
sha256_file = _scenarios.sha256_file

ActionPattern = Any  # BashAction | PathAction | ContentAction | ToolAction (scenarios.py)


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
# The action stream (DESIGN.md 6.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    idx: int  # position within the action stream (0-based)
    event_index: int  # position within the FULL event list this action's event sits at
    name: str
    input: Mapping[str, Any]


def build_action_stream(events: Sequence[dict]) -> list[Action]:
    """Every `tool_use` block, in transcript order. Text blocks are NOT actions --
    prose is not evidence (DESIGN.md 6.1, 6.2).
    """
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
# Exec-position anchoring (DEFECT 9)
#
# A plain `re.search` finds `pattern` ANYWHERE in a Bash `command` string, so
# `cat deploy.sh` or `grep -n foo deploy.sh` -- both read-only -- match a
# mistake_signature written to detect `./deploy.sh` just as readily as an actual
# `./deploy.sh staging` invocation. That collapses "the agent ran X" and "the agent's
# command MENTIONED X" into the same verdict, which is wrong whenever a signature's
# declared semantic is the former (DESIGN.md 6.2's BashAction/CommandRan primitives,
# `exec_anchor=True`). Anchoring instead requires the pattern to match starting at a
# genuine command-EXECUTION position: the start of the string, or immediately after
# a shell command separator (`;`, `&&`, `||`, `|`, `&`, or a newline), optionally
# through one or more interpreter/exec-wrapper tokens a real shell allows to precede
# the invoked program (`env`, one or more `VAR=value` assignments, `sh`/`bash`/`zsh`/
# `exec`, `timeout N`, `caffeinate -flags`).
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
    """Repeatedly strip leading whitespace and interpreter/exec-wrapper tokens off the
    FRONT of `command` so a pattern lands on the actually-invoked program, not its
    wrapper or the whitespace a shell allows around a command separator. A real shell
    allows wrapper tokens to chain in any order and count (`env FOO=bar timeout 30
    bash deploy.sh`), so this loops until a pass makes no further progress rather than
    assuming one fixed order or a single token.
    """
    remainder = command
    while True:
        m = _EXEC_PREFIX_TOKEN.match(remainder)
        end = m.end() if m else 0
        if end == 0:
            return remainder
        remainder = remainder[end:]


def _exec_positions(command: str) -> list[int]:
    """Every index in `command` where a new command begins: 0, or immediately after
    `;`, `&&`, `||`, `|`, `&`, or a newline.
    """
    return [m.end() for m in _EXEC_BOUNDARY.finditer(command)]


def _matches_at_exec_position(pattern: str, command: str) -> bool:
    """True when `pattern` matches starting at some genuine command-execution
    position in `command` (see module note above), after stripping any
    interpreter/exec-wrapper prefix at that position. Uses `re.match`, not
    `re.search`: the pattern must START at the candidate position, not merely occur
    somewhere after it -- that is what distinguishes "ran deploy.sh" from "ran cat,
    whose argument happens to be deploy.sh".
    """
    for pos in _exec_positions(command):
        if re.match(pattern, _strip_exec_prefixes(command[pos:])):
            return True
    return False


def _pattern_matches(action: Action, pattern: ActionPattern) -> bool:
    """Dispatch one action-pattern primitive (scenarios.py: BashAction, PathAction,
    ContentAction, ToolAction) against one action. `FileMutated` is deliberately not
    handled here -- it is end-of-leg file STATE, not a point in the action stream;
    see `any_file_mutated`.
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
    index to return (see `any_file_mutated` for that half of DESIGN.md 6.3's
    `mistake_committed = (m is not None) or any(FileMutated predicates)`).
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
    baseline (not scenario-start -- DESIGN.md 6.2's residue rule).
    """
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
# Per-leg quantities (DESIGN.md 6.3)
# ---------------------------------------------------------------------------

# Anthropic's published RELATIVE price ratios (cache write 1.25x input, cache read
# 0.1x input, output 5x input), held in one named constants block per DESIGN.md 6.3
# point 2. `billable_tokens_to_fact`/`billable_tokens_session` are a TOKEN AGGREGATE,
# not a dollar figure -- ratios are far more stable across models than absolute
# prices. The exact dollar figure is always `cost.session_usd` (`result.total_cost_usd`).
_BILLABLE_TOKEN_WEIGHTS: dict[str, float] = {
    "input_tokens": 1.0,
    "cache_creation_input_tokens": 1.25,
    "cache_read_input_tokens": 0.1,
    "output_tokens": 5.0,
}


def _billable_tokens(usage: Mapping[str, Any]) -> float:
    return sum(
        weight * float(usage.get(field_name) or 0)
        for field_name, weight in _BILLABLE_TOKEN_WEIGHTS.items()
    )


def leg_metrics(
    leg: LegSpec,
    *,
    events: Sequence[dict],
    actions: Sequence[Action],
    workspace: Path,
    leg_start_baselines: Mapping[str, str],
    k: int,
    origin: str,
    session_usd: float | None = None,
    billable_tokens_session: float | None = None,
) -> dict[str, Any]:
    """DESIGN.md 6.3: per-leg re-discovery-cost and mistake-repetition primitives.

    Trajectory-level aggregation (RDC ratios/deltas across legs -- 6.4; mistake-rate
    fractions across k=2..N -- 6.5) is NOT done here: this function scores one leg in
    isolation and has no visibility into sibling legs. `report.py` combines a
    trajectory's sequence of these per-leg records into RDC(k)/RDC(1) ratios,
    cross-arm deltas, and `mistake_rate_post`/`mistake_repetition_rate`.
    """
    a = first_index(actions, leg.fact_acquisition)
    m = first_index(actions, leg.mistake_signature)
    file_mutated = any_file_mutated(
        leg.mistake_signature, workspace=workspace, leg_start_baselines=leg_start_baselines
    )
    mistake_committed = (m is not None) or file_mutated
    self_corrected = mistake_committed and a is not None and (m is None or a > m)

    mistake_before_acquisition: bool | None = None
    if mistake_committed and a is not None:
        mistake_before_acquisition = m is not None and m < a

    mistake_source: str | None = None
    if m is not None:
        mistake_source = "action"
    elif file_mutated:
        mistake_source = "file_state"
    # A "verify_script" source (a mistake determined by a VerifyCommand's exit code
    # rather than the action stream) is not reachable through this type system --
    # `mistake_signature` is `tuple[ActionPattern | FileMutated, ...]` and has no
    # VerifyCommand member. See scenarios.py's S6 section comment: this is a
    # documented DESIGN.md gap (the schema anticipates the value; the type does
    # not yet carry a path to it), reported rather than invented around.

    rediscovery_actions = sum(
        1 for act in actions if _action_matches_any(act, leg.rediscovery_work)
    )
    vectr_tool_calls = sum(1 for act in actions if act.name.startswith("mcp__vectr__"))

    weak_prior: bool | None = None
    if k == 1 and origin == "discovered":
        weak_prior = not mistake_committed

    result: dict[str, Any] = {
        "acquisition_action_index": a,
        "censored": a is None,
        "censor_reason": "fact never acquired" if a is None else None,
        "turns_to_fact": None,
        "tool_calls_to_fact": None,
        "output_tokens_to_fact": None,
        "billable_tokens_to_fact": None,
        "context_tokens_at_fact": None,
        "usd_to_fact_alloc": None,
        "usd_to_fact_basis": None,
        "mistake_committed": mistake_committed,
        "first_mistake_action_index": m,
        "mistake_source": mistake_source,
        "self_corrected": self_corrected,
        "mistake_before_acquisition": mistake_before_acquisition,
        "rediscovery_actions": rediscovery_actions,
        "vectr_tool_calls": vectr_tool_calls,
        "weak_prior": weak_prior,
    }
    if a is None:
        return result  # censored: every RDC component stays null, never imputed

    e_index = actions[a].event_index
    prefix_events = [
        ev for i, ev in enumerate(events) if i <= e_index and ev.get("type") == "assistant"
    ]
    output_tokens = 0
    billable = 0.0
    for ev in prefix_events:
        usage = ((ev.get("message") or {}).get("usage")) or {}
        output_tokens += int(usage.get("output_tokens") or 0)
        billable += _billable_tokens(usage)

    fact_usage = ((events[e_index].get("message") or {}).get("usage")) or {}
    context_tokens_at_fact = (
        int(fact_usage.get("input_tokens") or 0)
        + int(fact_usage.get("cache_creation_input_tokens") or 0)
        + int(fact_usage.get("cache_read_input_tokens") or 0)
    )

    result.update(
        {
            "turns_to_fact": len(prefix_events),
            "tool_calls_to_fact": a + 1,
            "output_tokens_to_fact": output_tokens,
            "billable_tokens_to_fact": billable,
            "context_tokens_at_fact": context_tokens_at_fact,
        }
    )
    if session_usd is not None and billable_tokens_session:
        result["usd_to_fact_alloc"] = session_usd * billable / billable_tokens_session
        result["usd_to_fact_basis"] = "billable_token_share"
    return result


def t3_metrics(
    variant: NoteVariant | None,
    *,
    fact_sentence: str,
    actions: Sequence[Action],
) -> dict[str, Any]:
    """DESIGN.md 7.3 secondary measures. Only meaningful for the `verifiable` rung
    (the only one with a checkable anchor); `None` for every other variant.
    """
    if variant is None or variant.variant != "verifiable" or not variant.anchors:
        return {"anchor_checked": None, "verify_command_ran": None, "trail_chars": None}

    anchor = variant.anchors[0]
    anchor_checked = any(
        (act.name in ("Read", "Grep") and any(anchor in v for v in _path_values(act.input)))
        or (act.name == "Bash" and anchor in str(act.input.get("command") or ""))
        for act in actions
    )
    hint = variant.verify_hint.strip()
    verify_command_ran = bool(hint) and any(
        act.name == "Bash" and hint in str(act.input.get("command") or "") for act in actions
    )
    trail_chars = max(0, len(variant.content) - len(fact_sentence))
    return {
        "anchor_checked": anchor_checked,
        "verify_command_ran": verify_command_ran,
        "trail_chars": trail_chars,
    }


# ---------------------------------------------------------------------------
# Check evaluation and the arm-blind outcome verdict (DESIGN.md 6.6)
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
    """Evaluate one declared check into {name, passed, detail}. `baselines` here are
    always the current LEG's start-of-leg baselines, never scenario-materialization
    baselines (DESIGN.md 6.2's residue rule) -- the caller (`run_leg.py`) is
    responsible for recomputing them fresh from the restored workspace each leg.
    """
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

    if isinstance(check, FileMatchCountAtLeast):
        content = _read(workspace, check.path)
        if content is None:
            return {"name": check.name, "passed": False, "detail": f"missing file {check.path}"}
        count = len(re.findall(check.pattern, content))
        return {
            "name": check.name,
            "passed": count >= check.minimum,
            "detail": f"{count} occurrence(s) in {check.path}, minimum {check.minimum}",
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
            evaluate_check(
                sub, workspace=workspace, baselines=baselines, commands=commands, verify_dir=verify_dir
            )
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
            return {
                "name": check.name,
                "passed": False,
                "detail": f"verify command failed to run: {type(exc).__name__}",
            }
        passed = proc.returncode == check.expect_returncode
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        return {
            "name": check.name,
            "passed": passed,
            "detail": f"exit {proc.returncode} (want {check.expect_returncode})"
            + (f"; {tail[-1][:200]}" if tail else ""),
        }

    raise TypeError(f"unknown check type {type(check).__name__}")


def score_run(
    leg: LegSpec,
    *,
    workspace: Path,
    leg_start_baselines: Mapping[str, str],
    transcript: Sequence[dict],
    verify_dir: Path,
) -> dict[str, Any]:
    """Score one leg. Arm-blind by construction -- there is no `arm` parameter
    anywhere in this function or in anything it calls. `leg_non_vacuity` below is
    the ONLY place arm identity is ever consulted, and it answers a different
    question (did the channel fire?), never the outcome verdict.

    Evaluation ORDER matters and is not declaration order: every read-only check is
    evaluated before any `VerifyCommand`, because a verify script may legitimately
    mutate the workspace (DESIGN.md's `AllOf.__post_init__` accordingly refuses to
    accept a `VerifyCommand` as an `AllOf` member -- it must always be a sibling
    entry in `leg.checks`). Results are reassembled into declaration order after.
    """
    commands = bash_commands(transcript)

    def _eval(c: object) -> dict[str, Any]:
        return evaluate_check(
            c, workspace=workspace, baselines=leg_start_baselines, commands=commands, verify_dir=verify_dir
        )

    read_only = [c for c in leg.checks if not isinstance(c, VerifyCommand)]
    mutating = [c for c in leg.checks if isinstance(c, VerifyCommand)]
    evaluated = {id(c): _eval(c) for c in read_only}
    evaluated.update({id(c): _eval(c) for c in mutating})
    checks = [evaluated[id(c)] for c in leg.checks]

    by_name = {c["name"]: c for c in checks}
    if leg.primary_check not in by_name:
        raise KeyError(
            f"leg declares primary_check {leg.primary_check!r} which is not among its checks"
        )

    return {
        "primary_check": leg.primary_check,
        "fact_used": bool(by_name[leg.primary_check]["passed"]),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
    }


def _find_check_by_name(checks: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """Recursively search a `score_run()` `checks` list (and any `AllOf`'s nested
    `sub_checks`) for the check with `name`. `evaluate_check()`'s `AllOf` branch
    nests, so a check declared inside `_s5_allof(...)` (DESIGN.md/scenarios.py) is
    not a top-level entry -- this is the one place that difference is bridged.
    """
    for c in checks:
        if c.get("name") == name:
            return c
        sub = c.get("sub_checks")
        if sub:
            found = _find_check_by_name(sub, name)
            if found is not None:
                return found
    return None


def detect_contradictions(
    leg: LegSpec,
    *,
    checks: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """DEFECT 9's contradiction guard: called by the caller (`run_leg.py`/
    `rescore.py`) AFTER both `score_run()` (-> `checks`) and `leg_metrics()`
    (-> `metrics`) have run for the same leg -- kept OUT of `score_run()` itself so
    that function stays what its own docstring promises (no dependency beyond
    workspace/baselines/transcript/verify_dir, no metrics computation).

    A mistake_signature match sourced purely from the ACTION STREAM
    (`mistake_source == "action"`, i.e. a BashAction/CommandRan fired but no
    `FileMutated` predicate corroborated it) alongside a scenario-declared
    STATE-mutation check (`leg.mistake_state_check`) that PASSED -- the tracked
    file's bytes are confirmed unchanged from the leg-start baseline -- is a
    structural contradiction: the action-stream signature and the file-state
    evidence disagree about whether the mistake happened. This is recorded as DATA
    for the report layer, never silently resolved by conjoining it into
    `mistake_committed` -- a failed or aborted attempt is still worth knowing about
    on its own terms (that is what `self_corrected` is already for), so the guard's
    job is to surface the disagreement loudly, not to pick a winner.

    `leg.mistake_state_check` is authored once per leg at scenario-design time
    (the same discipline every other `LegSpec` ground-truth field already follows)
    -- never inferred at runtime by pattern-matching check names against the
    mistake signature, which would smuggle back exactly the kind of query/content-
    conditional guessing this harness (and vectr's own product code) forbids
    elsewhere. Most legs declare no `mistake_state_check` (only a scenario with an
    independent file-state signal for its mistake -- currently S5 -- can declare
    one); such legs never contribute a contradiction, which is a declared scope
    limit, not a bug.
    """
    contradictions: list[dict[str, Any]] = []
    state_check_name = leg.mistake_state_check
    if (
        state_check_name
        and metrics.get("mistake_committed")
        and metrics.get("mistake_source") == "action"
    ):
        state_check = _find_check_by_name(checks, state_check_name)
        if state_check is not None and state_check.get("passed"):
            contradictions.append(
                {
                    "kind": "mistake_action_without_state_mutation",
                    "mistake_state_check": state_check_name,
                    "first_mistake_action_index": metrics.get("first_mistake_action_index"),
                    "detail": (
                        f"mistake_signature matched a Bash command "
                        f"(first_mistake_action_index={metrics.get('first_mistake_action_index')}) "
                        f"but the declared state-mutation check {state_check_name!r} passed "
                        f"({state_check.get('detail')}) -- action-stream and file-state "
                        f"evidence disagree about whether the mistake happened; not "
                        f"resolved automatically"
                    ),
                }
            )
    return contradictions


def cost_metrics(events: Sequence[dict]) -> dict[str, Any]:
    """Session-granularity cost figures read straight off the transcript's `result`
    event. Latency is `duration_ms`/`duration_api_ms` from that event -- NEVER
    wall-clock (harness wall time includes queueing, DESIGN.md 6.3 point 3).
    """
    result = next((e for e in reversed(events) if e.get("type") == "result"), {}) or {}
    usage = result.get("usage") or {}
    names = [a.name for a in build_action_stream(events)]
    return {
        "session_turns": result.get("num_turns"),
        "session_usd": result.get("total_cost_usd"),
        "session_duration_ms": result.get("duration_ms"),
        "session_duration_api_ms": result.get("duration_api_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "billable_tokens_session": _billable_tokens(usage),
        "tool_calls": len(names),
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
    }


# ---------------------------------------------------------------------------
# Non-vacuity: did this leg's arm premise actually hold? (DESIGN.md 4.1)
# ---------------------------------------------------------------------------

# MCP column of the arms table (DESIGN.md 4): only arm B carries an MCP server.
# "mcp-bare" (tools, no CLAUDE.md guidance -- DESIGN.md 4's optional bare variant)
# gets the identical expectation to "mcp": the guidance text is an OUTCOME variable
# the two variants exist to compare, never a non-vacuity gate, so both must observe
# the same connected vectr server to be valid.
_EXPECTED_MCP_SERVERS: dict[str, list[dict[str, str]]] = {
    "none": [],
    "mcp": [{"name": "vectr", "status": "connected"}],
    "mcp-bare": [{"name": "vectr", "status": "connected"}],
    "proxy": [],
    "hook-sessionstart": [],
    "hook-full": [],
}

# Adapted from the trap harness's `parse_injection_events`
# (injection_utility/scorer.py) -- same VECTR_AUDIT_LOG line format, same daemon.
_INJECT_LINE = re.compile(r"PROACTIVE_INJECT\b(?P<rest>.*)$")
_KV = re.compile(r"(\w+)=([^\s]*)")


def parse_injection_events(audit_log: Path, *, since_offset: int = 0) -> list[dict[str, str]]:
    """`PROACTIVE_INJECT` lines out of a VECTR_AUDIT_LOG file. `since_offset` skips
    the harness's own preflight-probe traffic, which hits the same audit log.
    """
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


def _content_delivered_in_json_text(content: str, raw_text: str) -> bool:
    """`run_leg.py`'s `_content_delivered_in_json_text` (DEFECT 8), duplicated
    here rather than imported -- `run_leg.py` deliberately never imports
    `scorer.py` or vice versa (each is loaded independently via
    `_load_local_module`/`_load_sibling_scenarios`, see this file's own module
    docstring on the `scenarios` name collision, and `run_plan.py`'s comment
    on why `run_leg.py` runs as a fresh subprocess rather than an import).

    The transcript file this checks (`--output-format stream-json`) is raw
    JSONL text: the planted note's content, once delivered through a
    `SessionStart` hook's `hookSpecificOutput.additionalContext` field,
    appears in it JSON-string-escaped (an internal newline in a multi-line
    variant becomes the two literal characters `\\n`), so a literal or merely
    whitespace-collapsed substring check false-negatives even though the hook
    genuinely fired. Checks BOTH the literal/whitespace-collapsed form and the
    JSON-string-escaped form (`json.dumps(content)[1:-1]`, collapsed too).
    """
    if not content:
        return False

    def collapse(s: str) -> str:  # mirrors run_leg.py's _collapse_ws
        return " ".join(s.split())

    collapsed_haystack = collapse(raw_text)
    if collapse(content) in collapsed_haystack:
        return True
    escaped = json.dumps(content)[1:-1]
    return collapse(escaped) in collapsed_haystack


def leg_non_vacuity(
    *,
    arm: str,
    k: int,
    events: Sequence[dict],
    notes_count_at_start: int | None,
    restored_manifest_ok: bool | None = None,
    audit_log: Path | None = None,
    audit_since_offset: int = 0,
    proxy_injected: int | None = None,
    planted_anchor: str | None = None,
    hook_injection_counts: Mapping[str, Any] | None = None,
    transcript_path: Path | None = None,
    planted_note_content: str | None = None,
    recall_probe_returned_note: bool | None = None,
    trail_text_delivered: bool | None = None,
    agent_returncode: int | None = None,
    is_error: bool | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """DESIGN.md 4.1: was this leg's arm premise independently confirmed?

    Returns `{"valid": bool, "invalid_reason": str, "non_vacuity": {...}}`. A leg
    with `valid=False` is excluded from every reported number and must NEVER be read
    as "memory did not help" -- the arm simply did not test what it claims to.

    `arm` is consulted ONLY here, never in `score_run`/`leg_metrics` -- the outcome
    verdict stays arm-blind; this function alone answers "did the channel fire?".

    `agent_returncode`/`is_error`/`output_tokens` gate a prerequisite BELOW the
    per-arm premise: did the agent session run at all. This is arm-agnostic and
    content-free -- it reads only the session's own error/token fields (the
    process return code, `result.is_error`, `result.usage.output_tokens`), never
    transcript prose. An observed CLI shape this catches that the `result.subtype
    != "success"` check above does not: `subtype: "success"` with `is_error: true`
    and zero output tokens -- a session that errored out before producing any
    response, still tagged "success" by the transcript's own `result` event.
    """
    if arm not in _EXPECTED_MCP_SERVERS:
        raise ValueError(f"unknown arm {arm!r}")

    reasons: list[str] = []

    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None
    )
    if init is None:
        reasons.append("no system.init event in transcript")
    observed_servers = (init or {}).get("mcp_servers") or []
    expected_servers = _EXPECTED_MCP_SERVERS[arm]
    if observed_servers != expected_servers:
        reasons.append(f"mcp_servers {observed_servers!r} != expected {expected_servers!r} for arm {arm!r}")

    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if not result or result.get("subtype") != "success":
        reasons.append(f"result.subtype != 'success' (got {(result or {}).get('subtype')!r})")

    # Session-level abort, checked for every arm (not gated by result.subtype above
    # -- a session can carry subtype "success" while is_error is true and it never
    # produced any output). `output_tokens == 0` is a strict equality, not falsy: a
    # transcript with no `result` event at all leaves this None (already caught by
    # the subtype check above), so this only fires for a `result` event that IS
    # present and explicitly reports zero.
    session_errored = bool(is_error) or (agent_returncode not in (None, 0)) or (output_tokens == 0)
    if session_errored:
        reasons.append(
            f"agent session errored (is_error={is_error!r}, "
            f"agent_returncode={agent_returncode!r}, output_tokens={output_tokens!r})"
        )

    if k > 1 and restored_manifest_ok is False:
        reasons.append("restored snapshot manifest sha256 mismatch")

    inject_events = parse_injection_events(audit_log, since_offset=audit_since_offset) if audit_log else []

    nv: dict[str, Any] = {
        "mcp_servers_observed": observed_servers,
        "mcp_servers_expected": [s.get("name") for s in expected_servers],
        "inject_events": len(inject_events),
        "audit_since_offset": audit_since_offset,
        "proxy_injected": proxy_injected,
        "notes_in_store_at_start": notes_count_at_start,
        "trail_text_delivered": trail_text_delivered,
        "session_errored": session_errored,
    }

    if arm == "none":
        if notes_count_at_start not in (0, None):
            reasons.append(f"notes_count_at_start={notes_count_at_start} != 0 for arm 'none'")
        if inject_events:
            reasons.append(f"{len(inject_events)} PROACTIVE_INJECT event(s) for arm 'none'")
        if proxy_injected:
            reasons.append(f"proxy injected={proxy_injected} for arm 'none'")

    elif arm in ("mcp", "mcp-bare"):
        if not notes_count_at_start:
            reasons.append(f"notes_count_at_start is 0 for arm {arm!r}")
        tool_names = (init or {}).get("tools") or []
        vectr_tools_present = any(str(t).startswith("mcp__vectr__") for t in tool_names)
        nv["vectr_tools_in_init"] = vectr_tools_present
        if not vectr_tools_present:
            reasons.append("no mcp__vectr__* tool present in system.init tools")
        nv["recall_probe_returned_note"] = recall_probe_returned_note
        if recall_probe_returned_note is False:
            reasons.append("daemon-side /v1/recall preflight did not return the planted note")
        if proxy_injected:
            reasons.append(f"proxy injected={proxy_injected} for arm {arm!r}")

    elif arm == "proxy":
        if not notes_count_at_start:
            reasons.append("notes_count_at_start is 0 for arm 'proxy'")
        hits = [e for e in inject_events if planted_anchor in (e.get("anchors") or "").split(",")]
        nv["planted_anchor_injections"] = len(hits)
        nv["planted_note_retrieved"] = bool(hits)
        if not hits:
            reasons.append(f"planted anchor {planted_anchor!r} absent from post-offset PROACTIVE_INJECT lines")
        if not proxy_injected:
            reasons.append("proxy injected == 0 for arm 'proxy'")

    else:  # hook-sessionstart / hook-full
        if not notes_count_at_start:
            reasons.append(f"notes_count_at_start is 0 for arm {arm!r}")
        counts = dict(hook_injection_counts or {})
        nv["hook_injection_counts"] = counts
        hook_fired = any(int(v or 0) > 0 for v in counts.values())
        if not hook_fired:
            reasons.append(f"hook_injection_counts all zero for arm {arm!r}")
        if proxy_injected:
            reasons.append(f"proxy injected={proxy_injected} for arm {arm!r}")

        if arm == "hook-sessionstart":
            transcript_text = ""
            if transcript_path is not None and transcript_path.exists():
                transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")
            # DEFECT 8: whitespace-normalized AND JSON-escape-aware -- the
            # transcript is raw stream-json text, so a multi-line planted
            # note's internal newline is JSON-escaped inside the delivered
            # `additionalContext` field. See `_content_delivered_in_json_text`.
            delivered = _content_delivered_in_json_text(planted_note_content or "", transcript_text)
            nv["planted_content_in_transcript"] = delivered
            if not delivered:
                reasons.append(
                    "planted note content not found in transcript "
                    "(valid for D1: stream-json renders SessionStart additionalContext)"
                )
        else:
            # D2: UserPromptSubmit/PreToolUse additionalContext never render in
            # stream-json transcripts (known bug UPG-IU-HOOK-NONVACUITY-CANARY) --
            # delivery rests on hook_injection_counts plus the attestation's canary
            # method alone (DESIGN.md 4.1). Transcript content is deliberately never
            # consulted for this arm.
            nv["planted_content_in_transcript"] = None

    if trail_text_delivered is False:
        reasons.append("T3: variant's provenance-trail text absent from probe's returned context")

    return {"valid": not reasons, "invalid_reason": "; ".join(reasons), "non_vacuity": nv}
