#!/usr/bin/env python3
"""Injection-UTILITY eval — mechanical scorer.

Grades one completed run against the checks its scenario declared BEFORE the run
happened (see `scenarios.py`). Every verdict is computed from one of exactly
four sources:

  * the final bytes of a file in the workspace (regex / occurrence count),
  * a sha256 comparison against the baseline recorded at materialization,
  * the `command` field of Bash `tool_use` blocks in the run transcript,
  * the exit code of a scenario-owned verify script.

No LLM judges a run. Nothing here reads the injected context, the note text, or
the agent's prose: an agent that merely SAYS it will use the new API scores
exactly the same as one that says nothing, because only the resulting file
content and commands count.

The scorer is deliberately arm-blind. It is handed a workspace, a baseline map
and a transcript; it has no idea whether injection was on. That is what makes
the same code safe to run over both arms.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios import (  # noqa: E402
    CommandCount,
    CommandRan,
    FileMatchCountAtMost,
    FileMatches,
    FileUnchanged,
    Scenario,
    VerifyCommand,
    sha256_file,
)


# ---------------------------------------------------------------------------
# Transcript reading
# ---------------------------------------------------------------------------


def load_transcript(path: Path) -> list[dict]:
    """Parse a stream-json transcript file (one JSON object per line).

    Unparseable lines are skipped: the CLI occasionally interleaves non-JSON
    diagnostics on stdout and a single bad line must not void a whole run.
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


def bash_commands(events: Iterable[dict]) -> list[str]:
    """Every `command` string from Bash tool_use blocks, in order."""
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        message = ev.get("message")
        if not isinstance(message, dict):
            continue
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            cmd = (block.get("input") or {}).get("command")
            if isinstance(cmd, str) and cmd.strip():
                out.append(cmd)
    return out


def tool_use_names(events: Iterable[dict]) -> list[str]:
    names: list[str] = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        message = ev.get("message")
        if not isinstance(message, dict):
            continue
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.append(str(block.get("name") or ""))
    return names


def cost_metrics(events: Sequence[dict]) -> dict[str, Any]:
    """Cost figures read straight off the transcript.

    Reported for every run but NEVER used as a pass/fail signal: at one seed per
    cell these are far inside agent-to-agent variance.
    """
    result = next(
        (e for e in reversed(events) if e.get("type") == "result"), {}
    )
    usage = result.get("usage") or {}
    names = tool_use_names(events)
    return {
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "total_cost_usd": result.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "tool_calls": len(names),
        "bash_calls": sum(1 for n in names if n == "Bash"),
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
    }


# ---------------------------------------------------------------------------
# Check evaluation
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
    baselines: dict[str, str],
    commands: Sequence[str],
    verify_dir: Path,
) -> dict[str, Any]:
    """Evaluate one declared check into {name, passed, detail}."""
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
            "detail": "sha256 matches baseline" if actual == expected else "file was modified",
        }

    if isinstance(check, CommandRan):
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

    if isinstance(check, VerifyCommand):
        argv = [a.replace("{verify_dir}", str(verify_dir)) for a in check.argv]
        try:
            proc = subprocess.run(
                argv, cwd=str(workspace), capture_output=True, text=True, timeout=120
            )
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


def evaluate_metric(metric: object, *, commands: Sequence[str]) -> dict[str, Any]:
    if isinstance(metric, CommandCount):
        return {
            "name": metric.name,
            "value": sum(1 for c in commands if re.search(metric.pattern, c)),
        }
    raise TypeError(f"unknown metric type {type(metric).__name__}")


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def score_run(
    scenario: Scenario,
    *,
    workspace: Path,
    baselines: dict[str, str],
    transcript: Sequence[dict],
    verify_dir: Path,
) -> dict[str, Any]:
    """Score one run. Arm-blind by construction -- no arm argument exists.

    Evaluation ORDER matters and is not the declaration order: every read-only
    check (file content, occurrence count, sha256, transcript) is evaluated
    BEFORE any `VerifyCommand`, because a verify script may legitimately mutate
    the workspace -- `generated_config`'s verifier regenerates the config file,
    which would overwrite the very bytes a later `FileMatches` is meant to
    inspect. Read-then-mutate keeps every file check describing the state the
    agent actually left behind. Results are reassembled into declaration order
    for reporting, so the printed report is unaffected.
    """
    commands = bash_commands(transcript)

    def _eval(c):
        return evaluate_check(
            c,
            workspace=workspace,
            baselines=baselines,
            commands=commands,
            verify_dir=verify_dir,
        )

    read_only = [c for c in scenario.checks if not isinstance(c, VerifyCommand)]
    mutating = [c for c in scenario.checks if isinstance(c, VerifyCommand)]
    evaluated = {id(c): _eval(c) for c in read_only}
    evaluated.update({id(c): _eval(c) for c in mutating})
    checks = [evaluated[id(c)] for c in scenario.checks]

    by_name = {c["name"]: c for c in checks}
    if scenario.primary_check not in by_name:
        raise KeyError(
            f"scenario {scenario.slug} declares primary_check "
            f"{scenario.primary_check!r} which is not among its checks"
        )
    metrics = [evaluate_metric(m, commands=commands) for m in scenario.metrics]
    return {
        "scenario": scenario.slug,
        "primary_check": scenario.primary_check,
        "utility_hit": bool(by_name[scenario.primary_check]["passed"]),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "metrics": metrics,
        "cost": cost_metrics(list(transcript)),
        "bash_command_count": len(commands),
    }


# ---------------------------------------------------------------------------
# Non-vacuity: did the planted note actually enter the prompt?
# ---------------------------------------------------------------------------

_INJECT_LINE = re.compile(r"PROACTIVE_INJECT\b(?P<rest>.*)$")
_KV = re.compile(r"(\w+)=([^\s]*)")


def parse_injection_events(audit_log: Path) -> list[dict[str, str]]:
    """Parse PROACTIVE_INJECT lines out of a VECTR_AUDIT_LOG file."""
    events: list[dict[str, str]] = []
    if not audit_log.exists():
        return events
    for line in audit_log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INJECT_LINE.search(line)
        if not m:
            continue
        events.append(dict(_KV.findall(m.group("rest"))))
    return events


def non_vacuity(audit_log: Path, planted_anchor: str) -> dict[str, Any]:
    """Did the PLANTED note actually reach the prompt at least once?

    An injection-ON run whose audit log never shows the planted anchor is
    INVALID -- the arm did not test what it claims to test -- and must be
    excluded and reported as such, never counted as "the note did not help".
    """
    events = parse_injection_events(audit_log)
    hits = [
        e for e in events
        if planted_anchor in (e.get("anchors") or "").split(",")
    ]
    return {
        "audit_log_present": audit_log.exists(),
        "inject_events": len(events),
        "planted_anchor": planted_anchor,
        "planted_anchor_injections": len(hits),
        "planted_note_injected": bool(hits),
        "total_chars_injected": sum(int(e.get("chars") or 0) for e in events),
        "events": events,
    }
