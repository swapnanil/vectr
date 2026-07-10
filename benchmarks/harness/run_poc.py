#!/usr/bin/env python3
"""
Vectr POC — two-phase benchmark: Research → Implementation across separate sessions.

Architecture:
  Phase 1 (Research) — one shared session covering all tasks:
    Vanilla  → claude -p <combined_research_prompt>
               Claude explores, writes a prose RESEARCH SUMMARY in the answer
               Summary is gone when the session ends.
    Vectr    → claude -p <combined_research_prompt + vectr suffix>
               Claude explores, calls vectr_remember() throughout, ends with
               vectr_snapshot("research-complete")

  Phase 2 (Implementation, fresh session per task):
    Vanilla  → claude -p <impl_task> (no prior context; must re-discover)
    Vectr    → claude -p <impl_task prefixed with "call vectr_recall() first">
               Claude recalls structured notes, jumps straight to implementation

The core metric: Phase 2 token cost.
  Vanilla Phase 2 must re-discover everything → high token cost × 5 tasks.
  Vectr Phase 2 recalls structured notes in ~200 tokens → low re-discovery cost × 5 tasks.

Usage:
    bash setup_run3.sh
    python3.14 run_poc.py --run run3 --save
    python3.14 run_poc.py --run run3 --agent vectr --save
    python3.14 run_poc.py --run run3 --task debug_gc_finalizer --save
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from tasks import (
    TASKS, CPYTHON_TASKS, TwoPhaseTask, SinglePhaseTask,
    GC_TASKS, UV_TASKS, TIGERBEETLE_TASKS,
)
from report import (
    print_report, print_run3_report, print_run4_report,
    print_timeline, print_answers,
    save_results, save_run3_results, save_run4_results,
    save_run_sequential_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_poc")

VANILLA_DIR = os.getenv("POC_VANILLA_DIR", "/tmp/poc-django-vanilla")
VECTR_DIR   = os.getenv("POC_VECTR_DIR",   "/tmp/poc-django-vectr")

VANILLA_DIR_RUN3 = os.getenv("POC_VANILLA_DIR_RUN3", "/tmp/poc-cpython-vanilla")
VECTR_DIR_RUN3   = os.getenv("POC_VECTR_DIR_RUN3",   "/tmp/poc-cpython-vectr")

_VECTR_BASE = "/path/to/vectr/tmp"

VANILLA_DIR_RUN5 = os.getenv("POC_VANILLA_DIR_RUN5", f"{_VECTR_BASE}/poc-uv-vanilla")
VECTR_DIR_RUN5   = os.getenv("POC_VECTR_DIR_RUN5",   f"{_VECTR_BASE}/poc-uv-vectr")
VECTR_PORT_RUN5  = 8766

VANILLA_DIR_RUN6 = os.getenv("POC_VANILLA_DIR_RUN6", f"{_VECTR_BASE}/poc-tigerbeetle-vanilla")
VECTR_DIR_RUN6   = os.getenv("POC_VECTR_DIR_RUN6",   f"{_VECTR_BASE}/poc-tigerbeetle-vectr")
VECTR_PORT_RUN6  = 8767

_CLAUDE_DEFAULT = (
    "/path/to/home/Library/Application Support/Claude"
    "/claude-code/2.1.149/claude.app/Contents/MacOS/claude"
)
CLAUDE_BIN = _CLAUDE_DEFAULT if os.path.exists(_CLAUDE_DEFAULT) else "claude"

# Driver model for the agent session. Empty = Claude Code's default model.
# Set via --model (main) or POC_MODEL env. Accepts an alias ("opus", "sonnet")
# or a full model id ("claude-opus-4-8", "claude-sonnet-4-6"). Claude Code only —
# Cursor/Composer is out of scope for these benchmarks.
MODEL = os.getenv("POC_MODEL", "")


# ---------------------------------------------------------------------------
# Data model (POC v2)
# ---------------------------------------------------------------------------

@dataclass
class ToolEvent:
    elapsed_s: float
    turn: int
    tool_name: str
    input_summary: str
    result_chars: int
    duration_s: float = 0.0                        # per-call wall time
    full_input: dict = field(default_factory=dict) # complete input JSON
    full_result: str = ""                          # first 2,000 chars + total length


@dataclass
class PhaseResult:
    task_id: str
    agent_type: str
    phase: int              # 1 = research, 2 = implementation
    answer: str
    input_tokens: int
    output_tokens: int
    turns: int
    wall_time_s: float
    tool_calls: dict[str, int] = field(default_factory=dict)
    timeline: list[ToolEvent] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None
    # POC v2 additions
    prompt: str = ""
    base_input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    file_diff: str = ""
    answer_file_chars: int = 0
    answer_files: list[str] = field(default_factory=list)
    # E5: how many times the inline eviction hint actually fired in tool responses
    evict_hint_fired: int = 0
    # 1-indexed turn number when the first eviction hint fired (None if never)
    evict_hint_turn: int | None = None
    # P5-2: total tool calls across all turns
    tool_call_count: int = 0
    # Raw per-turn token breakdown from claude usage.iterations — one dict per turn.
    # Keys: input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens.
    # Saved so we can compute avg tokens-per-turn before vs after eviction.
    token_timeline: list[dict] = field(default_factory=list)
    # Number of Agent tool calls (sub-agents spawned) in the parent session.
    # When > 0: cost_usd includes sub-agent billing; input/output tokens are parent-session only.
    subagent_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TwoPhaseResult:
    """Legacy two-phase result (runs 1 + 2). Kept for backward compatibility."""
    task_id: str
    agent_type: str
    phase1: PhaseResult
    phase2: PhaseResult

    @property
    def total_tokens(self) -> int:
        return self.phase1.total_tokens + self.phase2.total_tokens

    @property
    def total_cost(self) -> float:
        return self.phase1.cost_usd + self.phase2.cost_usd


@dataclass
class BenchmarkRun:
    """Run 3+: one shared research session → N isolated implementation sessions."""
    run_id: str
    codebase: str
    agent_type: str
    research_phase: PhaseResult
    impl_phases: list[PhaseResult]
    vectr_index: dict = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return self.research_phase.cost_usd + sum(p.cost_usd for p in self.impl_phases)

    @property
    def total_tokens(self) -> int:
        return self.research_phase.total_tokens + sum(p.total_tokens for p in self.impl_phases)


# ---------------------------------------------------------------------------
# Run 4 data model
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """One task, one agent, one fresh LLM session (run4)."""
    task_id: str
    task_num: int
    agent_type: str
    answer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    base_input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    turns: int = 0
    wall_time_s: float = 0.0
    cost_usd: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=dict)
    timeline: list[ToolEvent] = field(default_factory=list)
    error: str | None = None
    prompt: str = ""
    file_diff: str = ""
    answer_files: list[str] = field(default_factory=list)
    answer_file_chars: int = 0
    notes_count_before: int = 0   # vectr notes in DB before this session
    notes_count_after: int = 0    # vectr notes in DB after this session
    evict_hint_fired: int = 0
    evict_hint_turn: int | None = None
    tool_call_count: int = 0
    token_timeline: list[dict] = field(default_factory=list)
    # Number of Agent tool calls (sub-agents spawned) in the parent session.
    # When > 0: cost_usd includes sub-agent billing; input/output tokens are parent-session only.
    subagent_calls: int = 0
    # Accurate per-tool counts from the vectr server — covers parent + all sub-agents.
    vectr_tool_calls_all: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def vectr_recall_fired(self) -> bool:
        if self.vectr_tool_calls_all:
            return self.vectr_tool_calls_all.get("vectr_recall", 0) > 0
        return self.tool_calls.get("mcp__vectr__vectr_recall", 0) > 0

    @property
    def vectr_tool_stats(self) -> dict[str, dict]:
        """Per-vectr-tool latency stats derived from timeline."""
        stats: dict[str, dict] = {}
        for ev in self.timeline:
            if "vectr" not in ev.tool_name.lower():
                continue
            short = ev.tool_name.replace("mcp__vectr__", "")
            if short not in stats:
                stats[short] = {"count": 0, "latencies_ms": []}
            stats[short]["count"] += 1
            stats[short]["latencies_ms"].append(round(ev.duration_s * 1000, 1))
        for s in stats.values():
            lats = s["latencies_ms"]
            s["avg_ms"] = round(sum(lats) / len(lats), 1) if lats else 0.0
            s["max_ms"] = max(lats) if lats else 0.0
            s["min_ms"] = min(lats) if lats else 0.0
        return stats


@dataclass
class Run4Result:
    """Full run4: N sequential tasks for one agent."""
    run_id: str
    codebase: str
    agent_type: str
    task_results: list[TaskResult] = field(default_factory=list)
    vectr_index: dict = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return sum(t.cost_usd for t in self.task_results)

    @property
    def total_tokens(self) -> int:
        return sum(t.total_tokens for t in self.task_results)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_VANILLA_RESEARCH_SUFFIX = """

At the end of your exploration, write a RESEARCH SUMMARY section in your answer
with the key findings: file paths, function names, call patterns, and gotchas.
Be specific — this summary is the only reference available for the implementation.
"""

PROMPT_VARIANTS: dict[str, dict[str, str]] = {
    "forced": {
        "research_suffix": """

--- VECTR TOOL USAGE — MANDATORY ---
You have vectr MCP tools. Use them for ALL code exploration.
DO NOT use Read or Bash to browse source files — use vectr tools instead.

EXPLORATION tools (replace file reads and grep):
  vectr_map()              — start here: structural overview of the codebase
  vectr_search(query)      — find relevant code by meaning (replaces grep + reading files)
  vectr_locate(name)       — find exactly where a class/function/method is defined
  vectr_trace(name)        — see what calls a symbol and what it calls (call graph)

MEMORY tools (use throughout, not just at the end):
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
                           — store each finding immediately after you make it
  vectr_snapshot("research-complete")
                           — call this when research is done to seal all notes

Mandatory workflow:
  1. vectr_map()                        — orient yourself
  2. vectr_search() / vectr_locate()    — find the relevant classes and functions
  3. vectr_trace()                      — follow call chains
  4. vectr_remember() after each finding — store it before moving on
  5. vectr_snapshot("research-complete")  — at the very end
---
""",
        "impl_prefix": """Before implementing anything:
  1. Call vectr_recall() to retrieve all research notes from the previous session.
  2. Read the notes carefully — they contain file paths, function signatures,
     and gotchas you will need.
  3. If you need to verify a specific detail, use vectr_search() or vectr_locate()
     rather than reading source files directly.

Then implement based on what the notes tell you.

""",
    },

    "memory-only": {
        "research_suffix": """

--- VECTR MEMORY ---
You have vectr memory tools. Use them to preserve findings for the implementation session —
that session starts cold and won't have any of your current context.

After each key finding, store it immediately:
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
  — store file paths, function signatures, call patterns, gotchas, anything you'll need later

At the end of research:
  vectr_snapshot("research-complete")
  — seals all stored notes so the next session can retrieve them

Explore the codebase however you prefer (Read, Bash, or vectr_search/locate/trace).
Only the memory tools are required.
""",
        "impl_prefix": """Your research notes from the previous session are stored in vectr.
Call vectr_recall() first to retrieve them, then implement.

""",
    },

    "additive": {
        "research_suffix": """

--- VECTR TOOLS (available alongside Read and Bash — use when they help) ---

EXPLORATION — use these when you don't already know where to look:
  vectr_map()          — structural overview of the codebase (good on first contact)
  vectr_search(query)  — find code by semantic meaning; faster than grep for conceptual queries
  vectr_locate(name)   — find exactly where a class/function is defined
  vectr_trace(name)    — see callers and callees without manually opening files

If you already know which file to read or which symbol to look for, Read and Bash
are fine. Use vectr exploration tools when you're navigating unfamiliar territory.

MEMORY — always use these to persist findings to the next session:
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
                       — store each key finding: file paths, signatures, patterns, gotchas.
                         Each implementation session starts fresh and won't have your context.
  vectr_snapshot("research-complete")
                       — call this at the very end to seal all notes.
""",
        "impl_prefix": """Your research notes from the previous session are stored in vectr.
Call vectr_recall() first to retrieve them.
If you need to verify a specific detail, vectr_search() or vectr_locate() is available.

""",
    },
}


def _build_prompt(
    task: TwoPhaseTask, phase: int, use_vectr: bool, prompt_variant: str = "additive",
    codebase_path: str | None = None,
) -> str:
    if codebase_path is None:
        codebase_path = VECTR_DIR if use_vectr else VANILLA_DIR

    if phase == 1:
        prompt = (
            f"The Django web framework source code is at: {codebase_path}\n\n"
            f"=== RESEARCH PHASE ===\n\n"
            f"{task.phase1_description}"
        )
        if use_vectr:
            variant = PROMPT_VARIANTS.get(prompt_variant, PROMPT_VARIANTS["additive"])
            prompt += variant["research_suffix"]
        else:
            prompt += _VANILLA_RESEARCH_SUFFIX
    else:
        prompt = (
            f"The Django web framework source code is at: {codebase_path}\n\n"
            f"=== IMPLEMENTATION PHASE ===\n\n"
        )
        if use_vectr:
            variant = PROMPT_VARIANTS.get(prompt_variant, PROMPT_VARIANTS["additive"])
            prompt += variant["impl_prefix"]
        prompt += task.phase2_description

    return prompt


def _build_run3_research_prompt(
    tasks: list[TwoPhaseTask], use_vectr: bool, codebase_path: str,
    prompt_variant: str = "additive",
) -> str:
    areas = "\n\n".join(
        f"=== Area {i+1}: {t.title} ===\n{t.phase1_description}"
        for i, t in enumerate(tasks)
    )
    prompt = (
        f"The CPython source code is at: {codebase_path}\n\n"
        f"=== RESEARCH SESSION ===\n\n"
        f"You are exploring {len(tasks)} separate areas of CPython internals. "
        f"A colleague will implement each of these in separate sessions later — "
        f"each of those sessions will start completely cold with no context from this one.\n\n"
        f"{areas}"
    )
    if not use_vectr:
        prompt += _VANILLA_RESEARCH_SUFFIX
    # Vectr agent: no suffix added here. CLAUDE.md in the working directory is the
    # sole guide for tool usage — the product must stand on its own without prompt help.
    return prompt


def _build_run3_impl_prompt(
    task: TwoPhaseTask, use_vectr: bool, codebase_path: str,
    prompt_variant: str = "additive",
) -> str:
    prompt = (
        f"[ISOLATION: fresh session, task={task.id}]\n\n"
        f"The CPython source code is at: {codebase_path}\n\n"
        f"=== IMPLEMENTATION SESSION ===\n\n"
    )
    # No impl_prefix for vectr — CLAUDE.md in the workspace guides the agent to
    # check vectr_status() and call vectr_recall(query=...) conditionally.
    # Both agents receive identical task descriptions; vectr's advantage comes
    # solely from CLAUDE.md + the notes stored during research.
    prompt += task.phase2_description
    return prompt


# ---------------------------------------------------------------------------
# Input summary helpers
# ---------------------------------------------------------------------------

def _summarise_input(tool_name: str, tool_input: dict) -> str:
    tn = tool_name.lower()
    if "read" in tn or "view" in tn:
        return tool_input.get("file_path") or tool_input.get("path") or str(tool_input)[:60]
    if "bash" in tn:
        cmd = tool_input.get("command", "")
        return cmd[:80] if cmd else str(tool_input)[:60]
    if "search" in tn:
        return f'"{tool_input.get("query", "")[:60]}"'
    if "locate" in tn or "trace" in tn:
        return tool_input.get("name", str(tool_input)[:60])
    if "remember" in tn:
        content = tool_input.get("content", "")
        return content[:60] + ("…" if len(content) > 60 else "")
    if "recall" in tn:
        q = tool_input.get("query") or str(tool_input.get("tags", ""))
        return q[:60] or "(all notes)"
    if "snapshot" in tn:
        return tool_input.get("label", "")
    if "map" in tn:
        return "(codebase map)"
    return str(tool_input)[:60]


# ---------------------------------------------------------------------------
# Stream-json runner (Popen for real-time timestamps)
# ---------------------------------------------------------------------------

_VECTR_TOOLS = [
    "mcp__vectr__vectr_remember",
    "mcp__vectr__vectr_recall",
    "mcp__vectr__vectr_snapshot",
    "mcp__vectr__vectr_snapshot_list",
    "mcp__vectr__vectr_search",
    "mcp__vectr__vectr_locate",
    "mcp__vectr__vectr_trace",
    "mcp__vectr__vectr_map",
    "mcp__vectr__vectr_map_save",
    "mcp__vectr__vectr_status",
    "mcp__vectr__vectr_evict_hint",
]

_STANDARD_TOOLS = ["Read", "Bash", "Write", "Edit"]

_FULL_RESULT_MAX_CHARS = 2000


def _run_claude_streaming(
    prompt: str,
    working_dir: str,
    max_turns: int = 30,
    timeout_s: int = 1200,
    use_vectr: bool = False,
    restrict_native: bool = False,
) -> tuple[dict, list[ToolEvent]]:
    # restrict_native: strip Read/Bash from vectr agent (diagnostic only — forces MCP tool use)
    if use_vectr and restrict_native:
        allowed = ["Write", "Edit"] + _VECTR_TOOLS
    else:
        allowed = _STANDARD_TOOLS + (_VECTR_TOOLS if use_vectr else [])
    cmd = [
        CLAUDE_BIN,
        "-p", prompt,
        "--output-format", "stream-json",
        "--max-turns", str(max_turns),
        "--verbose",
        "--allowedTools", ",".join(allowed),
    ]
    if MODEL:
        cmd += ["--model", MODEL]
    env = {**os.environ}

    timeline: list[ToolEvent] = []
    final: dict = {}
    session_start: float | None = None
    current_turn = 0
    # tool_id -> (name, summary, full_input, issued_time)
    pending_tool_calls: dict[str, tuple[str, str, dict, float]] = {}

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
            env=env,
        )
    except FileNotFoundError:
        return {"error": "claude CLI not found — is Claude Code installed?"}, []

    deadline = time.time() + timeout_s
    try:
        for raw_line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                return {"error": f"timed out after {timeout_s}s"}, timeline

            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                ev = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            now = time.time()
            ev_type = ev.get("type", "")

            if ev_type == "result":
                final = ev
                continue

            if ev_type == "system" and session_start is None:
                session_start = now
                continue

            if ev_type == "assistant":
                current_turn += 1
                issued_time = now
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_name  = block.get("name", "unknown")
                        tool_id    = block.get("id", "")
                        full_input = block.get("input", {})
                        summary    = _summarise_input(tool_name, full_input)
                        pending_tool_calls[tool_id] = (tool_name, summary, full_input, issued_time)

            if ev_type == "user":
                elapsed = now - (session_start or now)
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        receipt_time = now
                        tool_id = block.get("tool_use_id", "")
                        content = block.get("content", "")
                        result_text = (
                            content if isinstance(content, str)
                            else "".join(c.get("text", "") for c in content if isinstance(c, dict))
                        )
                        result_chars = len(result_text)
                        full_result = result_text[:_FULL_RESULT_MAX_CHARS]
                        if result_chars > _FULL_RESULT_MAX_CHARS:
                            full_result += f"\n[...{result_chars} total chars]"

                        pending = pending_tool_calls.pop(tool_id, ("unknown", "", {}, receipt_time))
                        tool_name, input_summary, full_input, issued_time = pending
                        duration_s = receipt_time - issued_time

                        timeline.append(ToolEvent(
                            elapsed_s=elapsed,
                            turn=current_turn,
                            tool_name=tool_name,
                            input_summary=input_summary,
                            result_chars=result_chars,
                            duration_s=duration_s,
                            full_input=full_input,
                            full_result=full_result,
                        ))
                        # Real-time log so we can monitor mid-run
                        short = tool_name.replace("mcp__vectr__", "vectr::")
                        logger.info(
                            "  turn=%d  %-28s  %s  (%.1fs, %d chars)",
                            current_turn, short, input_summary, duration_s, result_chars,
                        )
    finally:
        proc.wait()

    if proc.returncode != 0 and not final:
        stderr = proc.stderr.read().strip()[:500]
        return {"error": f"claude exited {proc.returncode}: {stderr}"}, timeline

    return final, timeline


# ---------------------------------------------------------------------------
# Compute answer_files / answer_file_chars from timeline
# ---------------------------------------------------------------------------

def _compute_answer_files(timeline: list[ToolEvent], working_dir: str) -> tuple[list[str], int]:
    """Scan Write/Edit events for file paths; sum file sizes on disk."""
    files: list[str] = []
    seen: set[str] = set()
    for ev in timeline:
        if not any(w in ev.tool_name for w in ("Write", "Edit", "MultiEdit")):
            continue
        path = ev.full_input.get("file_path") or ev.full_input.get("path", "")
        if not path or path in seen:
            continue
        seen.add(path)
        abs_path = path if os.path.isabs(path) else os.path.join(working_dir, path)
        if os.path.exists(abs_path):
            files.append(abs_path)
    total_chars = sum(os.path.getsize(f) for f in files)
    return files, total_chars


# ---------------------------------------------------------------------------
# Run one phase
# ---------------------------------------------------------------------------

def run_phase(
    task: TwoPhaseTask,
    phase: int,
    agent_type: str,
    max_turns: int,
    prompt_variant: str = "additive",
    codebase_path: str | None = None,
) -> PhaseResult:
    use_vectr   = agent_type == "vectr"
    if codebase_path is None:
        working_dir = VECTR_DIR if use_vectr else VANILLA_DIR
    else:
        working_dir = codebase_path

    prompt = _build_prompt(task, phase, use_vectr, prompt_variant, codebase_path=working_dir)

    # git snapshot before phase (POC v2)
    head_before = _git_head(working_dir)

    logger.info("[%s/%s] phase %d starting — task '%s'", agent_type, prompt_variant, phase, task.id)
    start = time.time()
    raw, timeline = _run_claude_streaming(
        prompt, working_dir, max_turns=max_turns, use_vectr=use_vectr,
    )
    wall_time_s = time.time() - start

    # git diff after phase (POC v2)
    file_diff = _git_diff(working_dir, head_before)

    error = raw.get("error")
    if error:
        logger.error("[%s] phase %d error: %s", agent_type, phase, error)
        return PhaseResult(
            task_id=task.id, agent_type=agent_type, phase=phase,
            answer="", input_tokens=0, output_tokens=0,
            turns=0, wall_time_s=wall_time_s, error=error,
            prompt=prompt, file_diff=file_diff,
        )

    usage = raw.get("usage", {})
    iterations = usage.get("iterations", [usage])
    base_input_tokens      = sum(it.get("input_tokens", 0)                    for it in iterations)
    cache_creation_tokens  = sum(it.get("cache_creation_input_tokens", 0)     for it in iterations)
    cache_read_tokens      = sum(it.get("cache_read_input_tokens", 0)         for it in iterations)
    effective_input        = base_input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens          = sum(it.get("output_tokens", 0)                   for it in iterations)
    turns    = raw.get("num_turns", 0)
    answer   = raw.get("result", "")
    cost_usd = raw.get("total_cost_usd", 0.0) or 0.0

    tool_calls: dict[str, int] = {}
    evict_hint_fired = 0
    evict_hint_turn: int | None = None
    for ev in timeline:
        tool_calls[ev.tool_name] = tool_calls.get(ev.tool_name, 0) + 1
        if "Context management hint" in ev.full_result:
            evict_hint_fired += 1
            if evict_hint_turn is None:
                evict_hint_turn = ev.turn
    tool_call_count = sum(tool_calls.values())
    subagent_calls = tool_calls.get("Agent", 0)
    token_timeline = [dict(it) for it in iterations]

    answer_files, answer_file_chars = _compute_answer_files(timeline, working_dir)

    logger.info(
        "[%s/%s] phase %d  in=%d(base=%d cc=%d cr=%d) out=%d turns=%d tools=%d evict=%d subagents=%d time=%.1fs cost=$%.4f%s",
        task.id, agent_type, phase,
        effective_input, base_input_tokens, cache_creation_tokens, cache_read_tokens,
        output_tokens, turns, len(timeline), evict_hint_fired, subagent_calls, wall_time_s, cost_usd,
        " [cost includes sub-agents; tokens=parent-only]" if subagent_calls else "",
    )

    return PhaseResult(
        task_id=task.id, agent_type=agent_type, phase=phase,
        answer=answer,
        input_tokens=effective_input,
        output_tokens=output_tokens,
        turns=turns, wall_time_s=wall_time_s,
        tool_calls=tool_calls, timeline=timeline,
        cost_usd=cost_usd,
        prompt=prompt,
        base_input_tokens=base_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        file_diff=file_diff,
        answer_files=answer_files,
        answer_file_chars=answer_file_chars,
        evict_hint_fired=evict_hint_fired,
        evict_hint_turn=evict_hint_turn,
        tool_call_count=tool_call_count,
        token_timeline=token_timeline,
        subagent_calls=subagent_calls,
    )


# ---------------------------------------------------------------------------
# Run 3: research phase + isolated impl phases
# ---------------------------------------------------------------------------

def run_research_phase(
    agent_type: str,
    tasks: list[TwoPhaseTask],
    codebase_path: str,
    max_turns: int,
    prompt_variant: str = "additive",
) -> PhaseResult:
    use_vectr = agent_type == "vectr"
    prompt = _build_run3_research_prompt(tasks, use_vectr, codebase_path, prompt_variant)

    head_before = _git_head(codebase_path)

    logger.info("[%s/%s] research phase starting — %d areas", agent_type, prompt_variant, len(tasks))
    start = time.time()
    raw, timeline = _run_claude_streaming(
        prompt, codebase_path, max_turns=max_turns, use_vectr=use_vectr,
    )
    wall_time_s = time.time() - start

    file_diff = _git_diff(codebase_path, head_before)
    error = raw.get("error")
    if error:
        logger.error("[%s] research phase error: %s", agent_type, error)
        return PhaseResult(
            task_id="research", agent_type=agent_type, phase=1,
            answer="", input_tokens=0, output_tokens=0,
            turns=0, wall_time_s=wall_time_s, error=error,
            prompt=prompt, file_diff=file_diff,
        )

    usage = raw.get("usage", {})
    iterations = usage.get("iterations", [usage])
    base_input_tokens     = sum(it.get("input_tokens", 0)                for it in iterations)
    cache_creation_tokens = sum(it.get("cache_creation_input_tokens", 0) for it in iterations)
    cache_read_tokens     = sum(it.get("cache_read_input_tokens", 0)     for it in iterations)
    effective_input       = base_input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens         = sum(it.get("output_tokens", 0)               for it in iterations)
    turns    = raw.get("num_turns", 0)
    answer   = raw.get("result", "")
    cost_usd = raw.get("total_cost_usd", 0.0) or 0.0

    tool_calls: dict[str, int] = {}
    evict_hint_fired = 0
    evict_hint_turn: int | None = None
    for ev in timeline:
        tool_calls[ev.tool_name] = tool_calls.get(ev.tool_name, 0) + 1
        if "Context management hint" in ev.full_result:
            evict_hint_fired += 1
            if evict_hint_turn is None:
                evict_hint_turn = ev.turn
    tool_call_count = sum(tool_calls.values())
    subagent_calls = tool_calls.get("Agent", 0)
    token_timeline = [dict(it) for it in iterations]

    answer_files, answer_file_chars = _compute_answer_files(timeline, codebase_path)

    logger.info(
        "[research/%s] in=%d(base=%d cc=%d cr=%d) out=%d turns=%d tools=%d evict=%d subagents=%d time=%.1fs cost=$%.4f%s",
        agent_type, effective_input, base_input_tokens, cache_creation_tokens, cache_read_tokens,
        output_tokens, turns, len(timeline), evict_hint_fired, subagent_calls, wall_time_s, cost_usd,
        " [cost includes sub-agents; tokens=parent-only]" if subagent_calls else "",
    )

    return PhaseResult(
        task_id="research", agent_type=agent_type, phase=1,
        answer=answer,
        input_tokens=effective_input, output_tokens=output_tokens,
        turns=turns, wall_time_s=wall_time_s,
        tool_calls=tool_calls, timeline=timeline,
        cost_usd=cost_usd,
        prompt=prompt,
        base_input_tokens=base_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        file_diff=file_diff,
        answer_files=answer_files,
        answer_file_chars=answer_file_chars,
        evict_hint_fired=evict_hint_fired,
        evict_hint_turn=evict_hint_turn,
        tool_call_count=tool_call_count,
        token_timeline=token_timeline,
        subagent_calls=subagent_calls,
    )


def run_impl_phase(
    agent_type: str,
    task: TwoPhaseTask,
    codebase_path: str,
    max_turns: int,
    prompt_variant: str = "additive",
) -> PhaseResult:
    use_vectr = agent_type == "vectr"
    prompt = _build_run3_impl_prompt(task, use_vectr, codebase_path, prompt_variant)

    head_before = _git_head(codebase_path)

    logger.info("[ISOLATION: fresh session, task=%s]", task.id)
    logger.info("[%s/%s] impl phase starting — task '%s'", agent_type, prompt_variant, task.id)
    start = time.time()
    raw, timeline = _run_claude_streaming(
        prompt, codebase_path, max_turns=max_turns, use_vectr=use_vectr,
    )
    wall_time_s = time.time() - start

    file_diff = _git_diff(codebase_path, head_before)
    error = raw.get("error")
    if error:
        logger.error("[%s] impl phase error (%s): %s", agent_type, task.id, error)
        return PhaseResult(
            task_id=task.id, agent_type=agent_type, phase=2,
            answer="", input_tokens=0, output_tokens=0,
            turns=0, wall_time_s=wall_time_s, error=error,
            prompt=prompt, file_diff=file_diff,
        )

    usage = raw.get("usage", {})
    iterations = usage.get("iterations", [usage])
    base_input_tokens     = sum(it.get("input_tokens", 0)                for it in iterations)
    cache_creation_tokens = sum(it.get("cache_creation_input_tokens", 0) for it in iterations)
    cache_read_tokens     = sum(it.get("cache_read_input_tokens", 0)     for it in iterations)
    effective_input       = base_input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens         = sum(it.get("output_tokens", 0)               for it in iterations)
    turns    = raw.get("num_turns", 0)
    answer   = raw.get("result", "")
    cost_usd = raw.get("total_cost_usd", 0.0) or 0.0

    tool_calls: dict[str, int] = {}
    evict_hint_fired = 0
    evict_hint_turn: int | None = None
    for ev in timeline:
        tool_calls[ev.tool_name] = tool_calls.get(ev.tool_name, 0) + 1
        if "Context management hint" in ev.full_result:
            evict_hint_fired += 1
            if evict_hint_turn is None:
                evict_hint_turn = ev.turn
    tool_call_count = sum(tool_calls.values())
    subagent_calls = tool_calls.get("Agent", 0)
    token_timeline = [dict(it) for it in iterations]

    answer_files, answer_file_chars = _compute_answer_files(timeline, codebase_path)

    logger.info(
        "[%s/%s] impl(%s) in=%d(base=%d cc=%d cr=%d) out=%d turns=%d evict=%d subagents=%d time=%.1fs cost=$%.4f%s",
        task.id, agent_type, prompt_variant,
        effective_input, base_input_tokens, cache_creation_tokens, cache_read_tokens,
        output_tokens, turns, evict_hint_fired, subagent_calls, wall_time_s, cost_usd,
        " [cost includes sub-agents; tokens=parent-only]" if subagent_calls else "",
    )

    return PhaseResult(
        task_id=task.id, agent_type=agent_type, phase=2,
        answer=answer,
        input_tokens=effective_input, output_tokens=output_tokens,
        turns=turns, wall_time_s=wall_time_s,
        tool_calls=tool_calls, timeline=timeline,
        cost_usd=cost_usd,
        prompt=prompt,
        base_input_tokens=base_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        file_diff=file_diff,
        answer_files=answer_files,
        answer_file_chars=answer_file_chars,
        evict_hint_fired=evict_hint_fired,
        evict_hint_turn=evict_hint_turn,
        tool_call_count=tool_call_count,
        token_timeline=token_timeline,
        subagent_calls=subagent_calls,
    )


# ---------------------------------------------------------------------------
# Git helpers (POC v2)
# ---------------------------------------------------------------------------

def _git_head(working_dir: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=working_dir, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _git_diff(working_dir: str, head_before: str) -> str:
    if not head_before:
        return ""
    try:
        return subprocess.check_output(
            ["git", "diff", head_before], cwd=working_dir, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Vectr helpers
# ---------------------------------------------------------------------------

def _clear_vectr_memory(port: int = 8765) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/v1/memory/clear",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            logger.info("Cleared vectr memory on port %d (%d notes deleted)", port, result.get("deleted", 0))
    except Exception as e:
        logger.warning("Could not clear vectr memory (port %d): %s", port, e)


def _get_vectr_index_info(port: int = 8765) -> dict:
    import urllib.request
    try:
        req = urllib.request.Request(f"http://localhost:{port}/v1/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _get_vectr_notes_count(port: int = 8765) -> int:
    try:
        return _get_vectr_index_info(port=port).get("notes_count", 0)
    except Exception:
        return -1


def _reset_vectr_call_counts(port: int = 8765) -> None:
    """Reset server-side per-tool call counters before a task run."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/v1/call_counts",
            method="DELETE",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _get_vectr_call_counts(port: int = 8765) -> dict[str, int]:
    """Read server-side per-tool call counts after a task run (parent + sub-agents)."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://localhost:{port}/v1/call_counts")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Run 4 task runner
# ---------------------------------------------------------------------------

def run_task_r4(
    task: SinglePhaseTask,
    task_num: int,
    agent_type: str,
    codebase_path: str,
    max_turns: int,
    codebase_desc: str = "The CPython source tree",
    vectr_port: int = 8765,
    restrict_native: bool = False,
) -> TaskResult:
    use_vectr = agent_type == "vectr"
    prompt = f"{codebase_desc} is at: {codebase_path}\n\n{task.description}"

    notes_before = _get_vectr_notes_count(port=vectr_port) if use_vectr else 0
    if use_vectr:
        _reset_vectr_call_counts(port=vectr_port)
    head_before = _git_head(codebase_path)

    logger.info("[%s] task%d/%s starting", agent_type, task_num, task.id)
    start = time.time()
    timeout_s = min(max_turns * 90, 10_800)  # 90s/turn budget, cap at 3h
    raw, timeline = _run_claude_streaming(
        prompt, codebase_path, max_turns=max_turns, use_vectr=use_vectr,
        timeout_s=timeout_s, restrict_native=restrict_native,
    )
    wall_time_s = time.time() - start

    notes_after = _get_vectr_notes_count(port=vectr_port) if use_vectr else 0
    vectr_tool_calls_all = _get_vectr_call_counts(port=vectr_port) if use_vectr else {}
    file_diff = _git_diff(codebase_path, head_before)

    error = raw.get("error")
    if error:
        logger.error("[%s] task%d error: %s", agent_type, task_num, error)
        return TaskResult(
            task_id=task.id, task_num=task_num, agent_type=agent_type,
            error=error, wall_time_s=wall_time_s, prompt=prompt,
            file_diff=file_diff,
            notes_count_before=notes_before, notes_count_after=notes_after,
        )

    usage = raw.get("usage", {})
    iterations = usage.get("iterations", [usage])
    base_input_tokens     = sum(it.get("input_tokens", 0)                for it in iterations)
    cache_creation_tokens = sum(it.get("cache_creation_input_tokens", 0) for it in iterations)
    cache_read_tokens     = sum(it.get("cache_read_input_tokens", 0)     for it in iterations)
    effective_input       = base_input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens         = sum(it.get("output_tokens", 0)               for it in iterations)
    turns    = raw.get("num_turns", 0)
    answer   = raw.get("result", "")
    cost_usd = raw.get("total_cost_usd", 0.0) or 0.0

    tool_calls: dict[str, int] = {}
    evict_hint_fired = 0
    evict_hint_turn: int | None = None
    for ev in timeline:
        tool_calls[ev.tool_name] = tool_calls.get(ev.tool_name, 0) + 1
        if "Context management hint" in ev.full_result:
            evict_hint_fired += 1
            if evict_hint_turn is None:
                evict_hint_turn = ev.turn
    tool_call_count = sum(tool_calls.values())
    subagent_calls = tool_calls.get("Agent", 0)
    token_timeline = [dict(it) for it in iterations]

    answer_files, answer_file_chars = _compute_answer_files(timeline, codebase_path)

    vt_all_summary = " ".join(f"{k}={v}" for k, v in sorted(vectr_tool_calls_all.items())) or "—"
    logger.info(
        "[%s] task%d  in=%d(base=%d cc=%d cr=%d) out=%d turns=%d tools=%d subagents=%d "
        "time=%.1fs cost=$%.4f notes=%d→%d vectr=[%s]%s",
        agent_type, task_num,
        effective_input, base_input_tokens, cache_creation_tokens, cache_read_tokens,
        output_tokens, turns, tool_call_count, subagent_calls, wall_time_s, cost_usd,
        notes_before, notes_after, vt_all_summary,
        " [cost includes sub-agents; tokens=parent-only]" if subagent_calls else "",
    )

    return TaskResult(
        task_id=task.id, task_num=task_num, agent_type=agent_type,
        answer=answer,
        input_tokens=effective_input, output_tokens=output_tokens,
        base_input_tokens=base_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        turns=turns, wall_time_s=wall_time_s,
        cost_usd=cost_usd, tool_calls=tool_calls, timeline=timeline,
        prompt=prompt, file_diff=file_diff,
        answer_files=answer_files, answer_file_chars=answer_file_chars,
        notes_count_before=notes_before, notes_count_after=notes_after,
        evict_hint_fired=evict_hint_fired, evict_hint_turn=evict_hint_turn,
        tool_call_count=tool_call_count, token_timeline=token_timeline,
        subagent_calls=subagent_calls,
        vectr_tool_calls_all=vectr_tool_calls_all,
    )


# ---------------------------------------------------------------------------
# Legacy runner (runs 1 + 2)
# ---------------------------------------------------------------------------

def run_agent_on_task(
    agent_type: str,
    task: TwoPhaseTask,
    max_turns_p1: int,
    max_turns_p2: int,
    prompt_variant: str = "additive",
) -> TwoPhaseResult:
    if agent_type == "vectr":
        _clear_vectr_memory()
    phase1 = run_phase(task, phase=1, agent_type=agent_type, max_turns=max_turns_p1,
                       prompt_variant=prompt_variant)
    phase2 = run_phase(task, phase=2, agent_type=agent_type, max_turns=max_turns_p2,
                       prompt_variant=prompt_variant)
    tagged_type = f"{agent_type}:{prompt_variant}" if agent_type == "vectr" else agent_type
    return TwoPhaseResult(task_id=task.id, agent_type=tagged_type, phase1=phase1, phase2=phase2)


# ---------------------------------------------------------------------------
# Run 3 runner
# ---------------------------------------------------------------------------

def run_benchmark3(
    agent_type: str,
    tasks: list[TwoPhaseTask],
    task_filter: str | None,
    max_turns_research: int,
    max_turns_impl: int,
    prompt_variant: str = "additive",
) -> BenchmarkRun:
    use_vectr = agent_type == "vectr"
    vanilla_dir = VANILLA_DIR_RUN3
    vectr_dir   = VECTR_DIR_RUN3
    codebase_path = vectr_dir if use_vectr else vanilla_dir

    vectr_index: dict = {}
    if use_vectr:
        _clear_vectr_memory()
        vectr_index = _get_vectr_index_info()
        logger.info("Vectr index: %s", vectr_index)

    # One shared research session covering all tasks
    research_result = run_research_phase(
        agent_type, tasks, codebase_path,
        max_turns=max_turns_research, prompt_variant=prompt_variant,
    )

    # 5 isolated implementation sessions
    tasks_to_impl = [t for t in tasks if task_filter is None or t.id == task_filter]
    impl_results: list[PhaseResult] = []
    for task in tasks_to_impl:
        result = run_impl_phase(
            agent_type, task, codebase_path,
            max_turns=max_turns_impl, prompt_variant=prompt_variant,
        )
        impl_results.append(result)

    from datetime import datetime
    run_id = f"run3_cpython_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tagged_type = f"{agent_type}:{prompt_variant}" if agent_type == "vectr" else agent_type

    return BenchmarkRun(
        run_id=run_id,
        codebase="cpython",
        agent_type=tagged_type,
        research_phase=research_result,
        impl_phases=impl_results,
        vectr_index=vectr_index,
    )


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

def _phase_from_dict(d: dict) -> PhaseResult:
    """Reconstruct a PhaseResult from a saved JSON dict (e.g. from a partial run)."""
    timeline = [
        ToolEvent(
            elapsed_s=ev.get("elapsed_s", 0.0),
            turn=ev.get("turn", 0),
            tool_name=ev.get("tool_name", ""),
            input_summary=ev.get("input_summary", ""),
            result_chars=ev.get("result_chars", 0),
            duration_s=ev.get("duration_s", 0.0),
            full_input=ev.get("full_input", {}),
            full_result=ev.get("full_result", ""),
        )
        for ev in d.get("timeline", [])
    ]
    return PhaseResult(
        task_id=d.get("task_id", ""),
        agent_type=d.get("agent_type", ""),
        phase=d.get("phase", 2),
        answer=d.get("answer", ""),
        input_tokens=d.get("input_tokens", 0),
        output_tokens=d.get("output_tokens", 0),
        turns=d.get("turns", 0),
        wall_time_s=d.get("wall_time_s", 0.0),
        cost_usd=d.get("cost_usd", 0.0),
        tool_calls=d.get("tool_calls", {}),
        timeline=timeline,
        base_input_tokens=d.get("base_input_tokens", 0),
        cache_creation_tokens=d.get("cache_creation_tokens", 0),
        cache_read_tokens=d.get("cache_read_tokens", 0),
        error=d.get("error"),
        file_diff=d.get("file_diff", ""),
        answer_file_chars=d.get("answer_file_chars", 0),
        answer_files=d.get("answer_files", []),
        evict_hint_fired=d.get("evict_hint_fired", 0),
        evict_hint_turn=d.get("evict_hint_turn"),
        tool_call_count=d.get("tool_call_count", 0),
        token_timeline=d.get("token_timeline", []),
        subagent_calls=d.get("subagent_calls", 0),
    )


def _load_resume_state(output_dir: Path, variant: str) -> tuple[str | None, dict]:
    """Load the most recent partial run3 JSON and return (run_ts, state_by_agent).

    state_by_agent maps agent_type → {"research": PhaseResult, "impls": {task_id: PhaseResult}}
    Returns (None, {}) if no partial file is found.
    """
    jsons = sorted(output_dir.glob(f"run3_*_{variant}.json"))
    if not jsons:
        return None, {}
    latest = jsons[-1]
    try:
        data = json.loads(latest.read_text())
    except Exception as exc:
        logger.warning("Could not load resume file %s: %s", latest, exc)
        return None, {}
    # Filename: run3_{date}_{time}_{variant}.json → run_ts = "{date}_{time}"
    parts = latest.stem.split("_")  # ['run3', '20260601', '180016', 'additive']
    if len(parts) < 3:
        return None, {}
    run_ts = f"{parts[1]}_{parts[2]}"
    state: dict = {}
    for run in data:
        agent = run.get("agent_type", "")
        state[agent] = {
            "research": _phase_from_dict(run.get("research_phase", {})),
            "impls": {p["task_id"]: _phase_from_dict(p) for p in run.get("impl_phases", [])},
        }
    logger.info("Resume: loaded %d agent(s) from %s", len(state), latest.name)
    return run_ts, state


# ---------------------------------------------------------------------------
# Generic sequential benchmark runner (run4 / run5 / run6)
# ---------------------------------------------------------------------------

def _run_benchmark_sequential(
    *,
    run_prefix: str,
    codebase_name: str,
    codebase_desc: str,
    tasks_list: list[SinglePhaseTask],
    task_filter: list[str] | None,
    agents: list[str],
    vanilla_dir: str,
    vectr_dir: str,
    vectr_port: int,
    output_dir: str,
    max_turns: int,
    save: bool,
    restrict_native: bool = False,
) -> None:
    tasks_to_run = tasks_list if task_filter is None else [
        t for t in tasks_list if t.id in task_filter
    ]
    if not tasks_to_run:
        logger.error("No matching tasks found. Valid IDs: %s", [t.id for t in tasks_list])
        return

    logger.info(
        "%s | codebase=%s | tasks=%s | agents=%s",
        run_prefix, codebase_name, [t.id for t in tasks_to_run], agents,
    )

    _clear_vectr_memory(port=vectr_port)
    vectr_index = _get_vectr_index_info(port=vectr_port)
    logger.info("Vectr index (port %d): %s", vectr_port, vectr_index)

    from datetime import datetime
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{run_prefix}_{codebase_name}_{run_ts}"

    vanilla_results: list[TaskResult] = []
    vectr_results:   list[TaskResult] = []

    os.environ["POC_OUTPUT_DIR"] = output_dir

    def _snapshot(label: str) -> None:
        if not save:
            return
        runs = []
        if vanilla_results:
            runs.append(Run4Result(
                run_id=run_id, codebase=codebase_name, agent_type="vanilla",
                task_results=list(vanilla_results), vectr_index={},
            ))
        if vectr_results:
            runs.append(Run4Result(
                run_id=run_id, codebase=codebase_name, agent_type="vectr",
                task_results=list(vectr_results), vectr_index=vectr_index,
            ))
        if runs:
            path = save_run_sequential_results(runs, run_prefix=run_prefix, run_ts=run_ts)
            logger.info("Snapshot saved → %s  [%s]", path, label)

    for i, task in enumerate(tasks_to_run, 1):
        logger.info("=== Task %d/%d: %s ===", i, len(tasks_to_run), task.title)

        fns: dict[str, object] = {}
        if "vanilla" in agents:
            def _vanilla(t=task, n=i):
                return run_task_r4(
                    t, n, "vanilla", vanilla_dir, max_turns,
                    codebase_desc=codebase_desc, vectr_port=vectr_port,
                )
            fns["vanilla"] = _vanilla
        if "vectr" in agents:
            def _vectr(t=task, n=i):
                return run_task_r4(
                    t, n, "vectr", vectr_dir, max_turns,
                    codebase_desc=codebase_desc, vectr_port=vectr_port,
                    restrict_native=restrict_native,
                )
            fns["vectr"] = _vectr

        if len(fns) == 2:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {agent: pool.submit(fn) for agent, fn in fns.items()}
            if "vanilla" in futures:
                vanilla_results.append(futures["vanilla"].result())
            if "vectr" in futures:
                vectr_results.append(futures["vectr"].result())
        else:
            if "vanilla" in fns:
                vanilla_results.append(fns["vanilla"]())
            if "vectr" in fns:
                vectr_results.append(fns["vectr"]())

        _snapshot(f"task{i}-done")

    # Final report + save
    runs: list[Run4Result] = []
    if vanilla_results:
        runs.append(Run4Result(
            run_id=run_id, codebase=codebase_name, agent_type="vanilla",
            task_results=vanilla_results, vectr_index={},
        ))
    if vectr_results:
        runs.append(Run4Result(
            run_id=run_id, codebase=codebase_name, agent_type="vectr",
            task_results=vectr_results, vectr_index=vectr_index,
        ))

    n_tasks = len(tasks_to_run)
    print_run4_report(
        runs,
        title=f"VECTR {run_prefix.upper()} — {codebase_name} / {n_tasks}-task sprint",
        subtitle=(
            f"{n_tasks} sequential tasks, fresh LLM session per task, workspace accumulates. "
            "Vectr notes persist across sessions; vanilla starts cold every time."
        ),
    )

    if save:
        path = save_run_sequential_results(runs, run_prefix=run_prefix, run_ts=run_ts)
        print(f"\nResults saved: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Vectr POC benchmark")
    parser.add_argument("--run", choices=["run1", "run2", "run3", "run4", "run5", "run6"],
                        default=None, help="Which benchmark run to execute (default: legacy mode)")
    parser.add_argument("--task", choices=(
        [t.id for t in TASKS] + [t.id for t in CPYTHON_TASKS] + [t.id for t in GC_TASKS]
    ))
    parser.add_argument(
        "--tasks",
        help="Comma-separated task IDs for run5/run6 (e.g. uv_task1 or uv_task1,uv_task2)",
    )
    parser.add_argument("--agent", choices=["vanilla", "vectr", "both"], default="both")
    parser.add_argument(
        "--prompt-variant",
        choices=list(PROMPT_VARIANTS.keys()),
        default="additive",
    )
    parser.add_argument("--max-turns-p1", type=int, default=40,
                        help="Max turns for research phase (default 40)")
    parser.add_argument("--max-turns-p2", type=int, default=30,
                        help="Max turns for implementation phase (default 30)")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Max turns for single-phase runs (run4/5/6). Overrides --max-turns-p2.")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from the most recent partial run3 JSON in the output dir")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--restrict-native", action="store_true",
                        help="Diagnostic: strip Read/Bash from vectr agent to force MCP tool use")
    parser.add_argument("--model", default=None,
                        help="Driver model for the agent session: alias ('opus', 'sonnet') "
                             "or full id ('claude-opus-4-8', 'claude-sonnet-4-6'). "
                             "Default: Claude Code's default model. Claude Code only.")
    args = parser.parse_args()

    if args.output_dir:
        os.environ["POC_OUTPUT_DIR"] = args.output_dir

    if args.model:
        global MODEL
        MODEL = args.model
        os.environ["POC_MODEL"] = args.model
        logger.info("Driver model: %s", MODEL)

    variant = args.prompt_variant
    agents_to_run = ["vanilla", "vectr"] if args.agent == "both" else [args.agent]

    if args.run == "run3":
        # benchmark3: interleaved run — both research phases first, then
        # vectr impl(task N) → vanilla impl(task N) for each task in order.
        # Incremental JSON written after every research phase and every individual
        # impl session — a kill loses at most the one impl currently running.
        os.environ.setdefault(
            "POC_OUTPUT_DIR",
            "/path/to/vectr/benchmarks/cpython",
        )
        task_filter = args.task
        tasks = CPYTHON_TASKS
        tasks_to_run = [t for t in tasks if task_filter is None or t.id == task_filter]

        logger.info(
            "benchmark3 | Tasks: %s | Agents: %s | Variant: %s",
            [t.id for t in tasks_to_run],
            agents_to_run, variant,
        )

        from datetime import datetime
        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")


        def _snapshot(label: str) -> None:
            """Save current partial state to disk immediately."""
            if not args.save:
                return
            partial: list[BenchmarkRun] = []
            if vanilla_research is not None:
                partial.append(BenchmarkRun(
                    run_id=f"run3_cpython_{run_ts}",
                    codebase="cpython",
                    agent_type="vanilla",
                    research_phase=vanilla_research,
                    impl_phases=list(vanilla_impls),
                    vectr_index={},
                ))
            if vectr_research is not None:
                partial.append(BenchmarkRun(
                    run_id=f"run3_cpython_{run_ts}",
                    codebase="cpython",
                    agent_type=f"vectr:{variant}",
                    research_phase=vectr_research,
                    impl_phases=list(vectr_impls),
                    vectr_index=vectr_index,
                ))
            if partial:
                path = save_run3_results(partial, prompt_variant=variant, run_ts=run_ts)
                logger.info("Snapshot saved → %s  [%s]", path, label)

        # ── Resume: load completed phases from the most recent partial JSON ─────
        vanilla_research: PhaseResult | None = None
        vectr_research:   PhaseResult | None = None
        vectr_index: dict = {}
        vanilla_impls: list[PhaseResult] = []
        vectr_impls:   list[PhaseResult] = []

        if args.resume:
            loaded_ts, resume_state = _load_resume_state(
                Path(os.environ.get("POC_OUTPUT_DIR",
                     "/path/to/vectr/benchmarks/cpython")),
                variant,
            )
            if loaded_ts:
                run_ts = loaded_ts  # reuse same filename so snapshots overwrite the partial
                vanilla_key = "vanilla"
                vectr_key   = f"vectr:{variant}"
                if vanilla_key in resume_state:
                    vanilla_research = resume_state[vanilla_key]["research"]
                    for t in tasks:
                        if t.id in resume_state[vanilla_key]["impls"]:
                            vanilla_impls.append(resume_state[vanilla_key]["impls"][t.id])
                if vectr_key in resume_state:
                    vectr_research = resume_state[vectr_key]["research"]
                    for t in tasks:
                        if t.id in resume_state[vectr_key]["impls"]:
                            vectr_impls.append(resume_state[vectr_key]["impls"][t.id])
                completed_both = {p.task_id for p in vanilla_impls} & {p.task_id for p in vectr_impls}
                tasks_to_run = [t for t in tasks_to_run if t.id not in completed_both]
                if completed_both:
                    logger.info("Resume: skipping %d already-done tasks: %s",
                                len(completed_both), sorted(completed_both))

        # ── Phase 1: research sessions — both agents run in parallel ─────────
        if "vectr" in agents_to_run and vectr_research is None:
            _clear_vectr_memory()
            vectr_index = _get_vectr_index_info()
            logger.info("Vectr index: %s", vectr_index)
        elif "vectr" in agents_to_run:
            logger.info("Resume: vectr research already done — skipping")

        def _run_vectr_research():
            return run_research_phase(
                "vectr", tasks, VECTR_DIR_RUN3,
                max_turns=args.max_turns_p1, prompt_variant=variant,
            )

        def _run_vanilla_research():
            return run_research_phase(
                "vanilla", tasks, VANILLA_DIR_RUN3,
                max_turns=args.max_turns_p1, prompt_variant=variant,
            )

        research_fns = {}
        if "vectr" in agents_to_run and vectr_research is None:
            research_fns["vectr"] = _run_vectr_research
        if "vanilla" in agents_to_run and vanilla_research is None:
            research_fns["vanilla"] = _run_vanilla_research

        if len(research_fns) == 2:
            logger.info("Research: running vectr + vanilla in PARALLEL")
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {agent: pool.submit(fn) for agent, fn in research_fns.items()}
            vectr_research   = futures["vectr"].result()   if "vectr"   in futures else None
            vanilla_research = futures["vanilla"].result() if "vanilla" in futures else None
        else:
            # single-agent mode — just run sequentially
            if "vectr" in research_fns:
                vectr_research = _run_vectr_research()
            if "vanilla" in research_fns:
                vanilla_research = _run_vanilla_research()

        if vectr_research:
            _snapshot("vectr-research-done")
        if vanilla_research:
            _snapshot("vanilla-research-done")

        # ── Phase 2: per-task impl — vectr + vanilla run in PARALLEL ─────────
        _done_vectr_impl_ids   = {p.task_id for p in vectr_impls}
        _done_vanilla_impl_ids = {p.task_id for p in vanilla_impls}

        for task in tasks_to_run:
            logger.info("Impl [%s]: running vectr + vanilla in PARALLEL", task.id)

            impl_fns: dict[str, object] = {}
            if "vectr" in agents_to_run and task.id not in _done_vectr_impl_ids:
                def _vectr_impl(t=task):
                    return run_impl_phase(
                        "vectr", t, VECTR_DIR_RUN3,
                        max_turns=args.max_turns_p2, prompt_variant=variant,
                    )
                impl_fns["vectr"] = _vectr_impl
            if "vanilla" in agents_to_run and task.id not in _done_vanilla_impl_ids:
                def _vanilla_impl(t=task):
                    return run_impl_phase(
                        "vanilla", t, VANILLA_DIR_RUN3,
                        max_turns=args.max_turns_p2, prompt_variant=variant,
                    )
                impl_fns["vanilla"] = _vanilla_impl

            if len(impl_fns) == 2:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {agent: pool.submit(fn) for agent, fn in impl_fns.items()}
                if "vectr" in futures:
                    vectr_impls.append(futures["vectr"].result())
                    _snapshot(f"vectr-impl-{task.id}")
                if "vanilla" in futures:
                    vanilla_impls.append(futures["vanilla"].result())
                    _snapshot(f"vanilla-impl-{task.id}")
            else:
                # single-agent mode
                if "vectr" in impl_fns:
                    vectr_impls.append(impl_fns["vectr"]())
                    _snapshot(f"vectr-impl-{task.id}")
                if "vanilla" in impl_fns:
                    vanilla_impls.append(impl_fns["vanilla"]())
                    _snapshot(f"vanilla-impl-{task.id}")

        # ── Final report ─────────────────────────────────────────────────────
        runs: list[BenchmarkRun] = []
        if vanilla_research is not None:
            runs.append(BenchmarkRun(
                run_id=f"run3_cpython_{run_ts}",
                codebase="cpython",
                agent_type="vanilla",
                research_phase=vanilla_research,
                impl_phases=vanilla_impls,
                vectr_index={},
            ))
        if vectr_research is not None:
            runs.append(BenchmarkRun(
                run_id=f"run3_cpython_{run_ts}",
                codebase="cpython",
                agent_type=f"vectr:{variant}",
                research_phase=vectr_research,
                impl_phases=vectr_impls,
                vectr_index=vectr_index,
            ))

        print_run3_report(runs)

        if args.save:
            path = save_run3_results(runs, prompt_variant=variant, run_ts=run_ts)
            print(f"\nResults saved: {path}")

    elif args.run == "run4":
        # ── Run 4: 4 sequential tasks, no research phase ──────────────────────
        # Workspace accumulates across tasks (no git-restore between tasks).
        # Vectr notes accumulate; each task's fresh session recalls prior notes.
        # Vanilla and vectr run in PARALLEL within each task.
        os.environ.setdefault(
            "POC_OUTPUT_DIR",
            "/path/to/vectr/benchmarks/cpython",
        )

        tasks_to_run = [t for t in GC_TASKS if args.task is None or t.id == t.id and t.id == (args.task or t.id)]
        if args.task:
            tasks_to_run = [t for t in GC_TASKS if t.id == args.task]
        else:
            tasks_to_run = list(GC_TASKS)

        logger.info(
            "benchmark4 | Tasks: %s | Agents: %s",
            [t.id for t in tasks_to_run], agents_to_run,
        )

        # Clear vectr notes once at the very start
        _clear_vectr_memory()
        vectr_index = _get_vectr_index_info()
        logger.info("Vectr index: %s", vectr_index)

        from datetime import datetime
        run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run4_cpython_{run_ts}"

        vanilla_results: list[TaskResult] = []
        vectr_results:   list[TaskResult] = []

        def _snapshot_r4(label: str) -> None:
            if not args.save:
                return
            runs = []
            if vanilla_results:
                runs.append(Run4Result(
                    run_id=run_id, codebase="cpython", agent_type="vanilla",
                    task_results=list(vanilla_results), vectr_index={},
                ))
            if vectr_results:
                runs.append(Run4Result(
                    run_id=run_id, codebase="cpython", agent_type="vectr",
                    task_results=list(vectr_results), vectr_index=vectr_index,
                ))
            if runs:
                path = save_run4_results(runs, run_ts=run_ts)
                logger.info("Snapshot saved → %s  [%s]", path, label)

        max_turns = args.max_turns if args.max_turns is not None else args.max_turns_p2

        for i, task in enumerate(tasks_to_run, 1):
            logger.info("=== Task %d/%d: %s ===", i, len(tasks_to_run), task.title)

            fns: dict[str, object] = {}
            if "vanilla" in agents_to_run:
                def _vanilla(t=task, n=i):
                    return run_task_r4(t, n, "vanilla", VANILLA_DIR_RUN3, max_turns)
                fns["vanilla"] = _vanilla
            if "vectr" in agents_to_run:
                def _vectr(t=task, n=i):
                    return run_task_r4(t, n, "vectr", VECTR_DIR_RUN3, max_turns)
                fns["vectr"] = _vectr

            if len(fns) == 2:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {agent: pool.submit(fn) for agent, fn in fns.items()}
                if "vanilla" in futures:
                    vanilla_results.append(futures["vanilla"].result())
                if "vectr" in futures:
                    vectr_results.append(futures["vectr"].result())
            else:
                if "vanilla" in fns:
                    vanilla_results.append(fns["vanilla"]())
                if "vectr" in fns:
                    vectr_results.append(fns["vectr"]())

            _snapshot_r4(f"task{i}-done")

        # Final report + save
        runs: list[Run4Result] = []
        if vanilla_results:
            runs.append(Run4Result(
                run_id=run_id, codebase="cpython", agent_type="vanilla",
                task_results=vanilla_results, vectr_index={},
            ))
        if vectr_results:
            runs.append(Run4Result(
                run_id=run_id, codebase="cpython", agent_type="vectr",
                task_results=vectr_results, vectr_index=vectr_index,
            ))

        print_run4_report(runs)

        if args.save:
            path = save_run4_results(runs, run_ts=run_ts)
            print(f"\nResults saved: {path}")

    elif args.run == "run5":
        task_filter = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
        _run_benchmark_sequential(
            run_prefix="run5",
            codebase_name="uv",
            codebase_desc="The uv Python package manager source code (Rust)",
            tasks_list=UV_TASKS,
            task_filter=task_filter,
            agents=agents_to_run,
            vanilla_dir=VANILLA_DIR_RUN5,
            vectr_dir=VECTR_DIR_RUN5,
            vectr_port=VECTR_PORT_RUN5,
            output_dir=os.environ.get(
                "POC_OUTPUT_DIR",
                "/path/to/vectr/benchmarks/uv",
            ),
            max_turns=args.max_turns if args.max_turns is not None else args.max_turns_p2,
            save=args.save,
        )

    elif args.run == "run6":
        task_filter = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
        _run_benchmark_sequential(
            run_prefix="run6",
            codebase_name="tigerbeetle",
            codebase_desc="The TigerBeetle financial database source code (Zig)",
            tasks_list=TIGERBEETLE_TASKS,
            task_filter=task_filter,
            agents=agents_to_run,
            vanilla_dir=VANILLA_DIR_RUN6,
            vectr_dir=VECTR_DIR_RUN6,
            vectr_port=VECTR_PORT_RUN6,
            output_dir=os.environ.get(
                "POC_OUTPUT_DIR",
                "/path/to/vectr/benchmarks/tigerbeetle",
            ),
            max_turns=args.max_turns if args.max_turns is not None else args.max_turns_p2,
            save=args.save,
            restrict_native=args.restrict_native,
        )

    else:
        # Legacy mode (runs 1 + 2)
        tasks_to_run  = [t for t in TASKS if args.task is None or t.id == args.task]

        logger.info(
            "Tasks: %s | Agents: %s | Prompt variant: %s",
            [t.id for t in tasks_to_run], agents_to_run, variant,
        )

        results: list[TwoPhaseResult] = []
        for task in tasks_to_run:
            logger.info("━━━ Task: %s ━━━", task.title)
            for agent_type in agents_to_run:
                result = run_agent_on_task(
                    agent_type, task,
                    max_turns_p1=args.max_turns_p1,
                    max_turns_p2=args.max_turns_p2,
                    prompt_variant=variant,
                )
                results.append(result)

        print_report(results)
        print_timeline(results)
        print_answers(results)

        if args.save:
            path = save_results(results, prompt_variant=variant)
            print(f"\nResults saved: {path}")


if __name__ == "__main__":
    main()
