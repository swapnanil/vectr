#!/usr/bin/env python3
"""G1 replay harness (L1 capture design doc §7, evidence gate for LANE-ARC).

Feeds real agent-editor transcripts through `app.arcs.ArcDetector` in
timestamp order, per session, and reports what the detector emitted —
the gate for whether the false-positive traps (§3.5) hold up against
real transcripts, not just synthetic unit fixtures.

R5 scope note: every transcript event replayed here is a TOOL-CALL record
(argv of an already-issued Bash/Edit call, plus that command's own stdout/
stderr/exit outcome) — the identical sanctioned data shape app/arcs.py
itself consumes. Nothing in this harness reads task-prompt/user-turn text;
only assistant `tool_use` blocks and their matching `tool_result` blocks
are parsed.

Outcome derivation here is a standalone, harness-local minimal cascade
(content markers -> weak is_error fallback -> unknown) for REPLAY
purposes only — it does not read or depend on the real product's
episode-write path / marker table, which is a separate, parallel lane.

Usage:
    ./.venv/bin/python benchmarks/arc_replay.py [glob ...]

With no arguments, replays the two G1 corpora named in the design doc.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.arcs import Arc, ArcDetector  # noqa: E402
from app.cmdnorm import normalize_command  # noqa: E402

DEFAULT_GLOBS = [
    "results/vectr-vs-bash/camel/6b422df/t2/*.jsonl",
    "results/vectr-vs-bash/camel/1595096/t1c/*.jsonl",
]

_JUNIT_SUMMARY_RE = re.compile(
    r"Tests run:\s*(?P<run>\d+),\s*Failures:\s*(?P<failures>\d+),\s*Errors:\s*(?P<errors>\d+)"
)
_PYTEST_FAILED_RE = re.compile(r"\b\d+ failed\b")
_PYTEST_PASSED_RE = re.compile(r"\b\d+ passed\b")
_MAVEN_GRADLE_ROOT_VERBS = frozenset({"mvn", "./mvnw", "mvnw", "gradle", "./gradlew", "gradlew"})
_PYTEST_ROOT_VERBS = frozenset({"pytest", "py.test"})
_TAIL_CHARS = 4000


def _verb_root(verb: str) -> str:
    return verb.split(" ", 1)[0] if verb else ""


def derive_outcome(cmd_raw: str, stdout: str, stderr: str, interrupted: bool, is_error: bool) -> str:
    """Standalone (replay-only) outcome cascade: content markers first,
    gated on the INVOKING COMMAND'S OWN STRUCTURE, then the weak is_error
    fallback, else unknown — never a bare-substring scan of arbitrary
    stdout. Gating on the invoked verb matters: `grep "BUILD FAILURE"
    file.java` is an exploration call whose own grep-matched output line
    literally contains that string, but it is not a maven invocation and
    must never be misread as one."""
    if interrupted:
        return "interrupted"

    verb_root = _verb_root(normalize_command(cmd_raw).verb) if cmd_raw else ""
    tail = f"{(stdout or '')[-_TAIL_CHARS:]}\n{(stderr or '')[-_TAIL_CHARS:]}"

    if verb_root in _MAVEN_GRADLE_ROOT_VERBS:
        if "BUILD FAILURE" in tail:
            return "failure"
        m = _JUNIT_SUMMARY_RE.search(tail)
        if m and (int(m.group("failures")) + int(m.group("errors"))) > 0:
            return "failure"
        if "BUILD SUCCESS" in tail:
            return "success"
    if verb_root in _PYTEST_ROOT_VERBS:
        if _PYTEST_FAILED_RE.search(tail):
            return "failure"
        if _PYTEST_PASSED_RE.search(tail) and not _PYTEST_FAILED_RE.search(tail):
            return "success"

    # Weak fallback (§2.4): is_error only ever DEMOTES to failure, never
    # promotes to success on its own — the corpus shows is_error is
    # unreliable (observed False even on genuine failures elsewhere in the
    # project's own investigation), so it cannot be trusted as positive
    # evidence of success.
    if is_error:
        return "failure"
    return "unknown"


def parse_transcript(path: str) -> list[dict[str, Any]]:
    """Convert one Claude-Code-style stream-json transcript file into an
    ordered list of episode dicts (§2.3 shape) ready for
    `ArcDetector.observe()`. Only Bash/Edit/Write/MultiEdit tool_use blocks
    and their matching tool_result blocks are read."""
    with open(path) as f:
        raw_lines = [json.loads(line) for line in f if line.strip()]

    tool_calls: dict[str, dict[str, Any]] = {}
    session_id: str | None = None
    for entry in raw_lines:
        if entry.get("type") != "assistant":
            continue
        session_id = entry.get("session_id") or session_id
        ts = entry.get("_t")
        for block in entry.get("message", {}).get("content", []) or []:
            if block.get("type") == "tool_use":
                tool_calls[block["id"]] = {"name": block.get("name"), "input": block.get("input") or {}, "ts": ts}

    episodes: list[dict[str, Any]] = []
    for entry in raw_lines:
        if entry.get("type") != "user":
            continue
        tur = entry.get("tool_use_result")
        tur = tur if isinstance(tur, dict) else {}
        for block in entry.get("message", {}).get("content", []) or []:
            if block.get("type") != "tool_result":
                continue
            call = tool_calls.get(block.get("tool_use_id"))
            if not call:
                continue
            name = call["name"]
            ts = call["ts"] if call["ts"] is not None else entry.get("_t")
            is_error = bool(block.get("is_error"))

            if name == "Bash":
                cmd_raw = call["input"].get("command", "")
                stdout = tur.get("stdout", "")
                stderr = tur.get("stderr", "")
                interrupted = bool(tur.get("interrupted"))
                outcome = derive_outcome(cmd_raw, stdout, stderr, interrupted, is_error)
                episodes.append(
                    dict(
                        session_id=session_id,
                        ts=ts,
                        cwd="",  # not carried by this transcript format
                        tool="bash",
                        cmd_raw=cmd_raw,
                        outcome=outcome,
                        termination="interrupted" if interrupted else "normal",
                        markers=[],
                        env_delta_names=[],
                        file_path=None,
                    )
                )
            elif name in ("Edit", "Write", "MultiEdit"):
                file_path = call["input"].get("file_path", "")
                episodes.append(
                    dict(
                        session_id=session_id,
                        ts=ts,
                        cwd="",
                        tool="edit",
                        cmd_raw="",
                        outcome=None,
                        termination=None,
                        markers=[],
                        env_delta_names=[],
                        file_path=file_path,
                    )
                )

    episodes.sort(key=lambda e: e["ts"] if e["ts"] is not None else 0.0)
    return episodes


def _success_label(arc: Arc) -> str:
    return arc.success.get("cmd_raw") or f"(edit: {arc.success.get('file_path')})"


def replay(paths: list[str]) -> int:
    total_arcs = 0
    for path in paths:
        episodes = parse_transcript(path)
        detector = ArcDetector()
        arcs: list[Arc] = []
        for ep in episodes:
            arcs.extend(detector.observe(ep))

        n_bash = sum(1 for e in episodes if e["tool"] == "bash")
        n_edit = sum(1 for e in episodes if e["tool"] == "edit")
        n_fail = sum(1 for e in episodes if e["tool"] == "bash" and e["outcome"] in ("failure", "soft_failure"))

        print(f"\n=== {Path(path).name} ===")
        print(f"  episodes: {len(episodes)} (bash={n_bash}, edit={n_edit}, bash-failures-or-soft-failures={n_fail})")
        print(f"  arcs emitted: {len(arcs)}")
        for i, arc in enumerate(arcs, 1):
            print(f"    [{i}] chain_len={len(arc.failures_chain)} confidence={arc.confidence}")
            for f in arc.failures_chain:
                print(f"        FAIL: {f['cmd_raw']!r}")
            print(f"        OK:   {_success_label(arc)!r}")
            print(f"        diff: {arc.mutation_diff}")
        total_arcs += len(arcs)

    print(f"\nTOTAL arcs across {len(paths)} sessions: {total_arcs}")
    return total_arcs


if __name__ == "__main__":
    globs = sys.argv[1:] or DEFAULT_GLOBS
    matched = sorted({p for g in globs for p in glob.glob(g)})
    if not matched:
        print(f"No transcript files matched globs: {globs}", file=sys.stderr)
        sys.exit(1)
    replay(matched)
