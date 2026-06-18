#!/usr/bin/env python3
"""Eval v2 — within-session /compact scenario across five arms.

Wave 5 made vectr recall harness-injected (Claude Code hooks), so the old
"did the model choose to call recall" success bar is a tautology. Eval v2
measures the product behaviour directly: explore a codebase, force a /compact
mid-session (so fine-grained detail is lost from the conversation summary),
then implement — and compare arms that differ only in how findings survive the
compaction boundary.

Arms (see ARMS):
  A1  bare vanilla              — standard tools only; nothing survives compaction
  A2  vanilla + NOTES.md        — agent maintains a hand-written file on disk
  B   vectr search tools only   — re-query the index after compaction, no memory
  C   vectr + memory via hooks  — shipped product; recall auto-injected
  D   vectr + memory, MCP-only  — memory tools available, model must call them

The scenario runs as ONE Claude Code session driven over stream-json stdin:
    [ <explore prompt>, "/compact", <implement prompt> ]
The CLI processes piped user messages sequentially; /compact is intercepted
locally and emits a second `system`/`init` event (same session_id) rather than
a `result`. That second init is the compaction boundary — events before it are
the research phase, events after it are the implementation phase.

This module owns only the execution mechanics + arm wiring. Scoring
(execution + memory metrics) lives alongside in scoring helpers.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field

from run_poc import (
    CLAUDE_BIN,
    MODEL,
    ToolEvent,
    _STANDARD_TOOLS,
    _VECTR_TOOLS,
    _FULL_RESULT_MAX_CHARS,
    _summarise_input,
)

logger = logging.getLogger("eval_v2")

# vectr search/exploration tools (no working-memory tools)
_VECTR_SEARCH_TOOLS = [
    "mcp__vectr__vectr_search",
    "mcp__vectr__vectr_locate",
    "mcp__vectr__vectr_trace",
    "mcp__vectr__vectr_map",
    "mcp__vectr__vectr_map_save",
    "mcp__vectr__vectr_status",
]
# vectr working-memory tools
_VECTR_MEMORY_TOOLS = [
    "mcp__vectr__vectr_remember",
    "mcp__vectr__vectr_recall",
    "mcp__vectr__vectr_snapshot",
    "mcp__vectr__vectr_snapshot_list",
    "mcp__vectr__vectr_evict_hint",
]

COMPACT_TURN = "/compact"


# ---------------------------------------------------------------------------
# Arm definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    """One experimental configuration. Arms differ only in how findings cross
    the /compact boundary, so the per-arm token/quality deltas isolate that."""
    id: str
    label: str
    use_search: bool      # vectr search/locate/trace/map available
    use_memory: bool      # vectr remember/recall/snapshot available
    hooks: bool           # install vectr Claude Code hooks (auto-inject recall)
    notes_md: bool        # scaffold + instruct a hand-maintained NOTES.md file

    def allowed_tools(self) -> list[str]:
        tools = list(_STANDARD_TOOLS)
        if self.use_search:
            tools += _VECTR_SEARCH_TOOLS
        if self.use_memory:
            tools += _VECTR_MEMORY_TOOLS
        return tools

    @property
    def uses_vectr(self) -> bool:
        return self.use_search or self.use_memory


ARMS: dict[str, Arm] = {
    "A1": Arm("A1", "bare vanilla",            use_search=False, use_memory=False, hooks=False, notes_md=False),
    "A2": Arm("A2", "vanilla + NOTES.md",      use_search=False, use_memory=False, hooks=False, notes_md=True),
    "B":  Arm("B",  "vectr search only",       use_search=True,  use_memory=False, hooks=False, notes_md=False),
    "C":  Arm("C",  "vectr + memory (hooks)",  use_search=True,  use_memory=True,  hooks=True,  notes_md=False),
    "D":  Arm("D",  "vectr + memory (MCP)",    use_search=True,  use_memory=True,  hooks=False, notes_md=False),
}


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class PhaseUsage:
    """Token + turn accounting for one phase (research or implementation)."""
    input_tokens: int = 0            # effective: base + cache_creation + cache_read
    base_input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    cost_usd: float = 0.0
    answer: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CompactSessionResult:
    arm_id: str
    task_id: str
    research: PhaseUsage = field(default_factory=PhaseUsage)
    impl: PhaseUsage = field(default_factory=PhaseUsage)
    research_timeline: list[ToolEvent] = field(default_factory=list)
    impl_timeline: list[ToolEvent] = field(default_factory=list)
    compacted: bool = False           # did a compaction boundary appear?
    wall_time_s: float = 0.0
    error: str | None = None
    file_diff: str = ""
    answer_files: list[str] = field(default_factory=list)
    answer_file_chars: int = 0
    notes_count_before: int = 0
    notes_count_after: int = 0
    vectr_tool_calls_all: dict[str, int] = field(default_factory=dict)
    compaction_summary_chars: int = 0  # size of the summary the CLI produced

    @property
    def total_tokens(self) -> int:
        return self.research.total_tokens + self.impl.total_tokens

    @property
    def total_cost(self) -> float:
        return self.research.cost_usd + self.impl.cost_usd


# ---------------------------------------------------------------------------
# Pure stream parsing — unit-testable on synthetic event lists (no quota)
# ---------------------------------------------------------------------------

def _is_compact_boundary(ev: dict, init_seen: int) -> bool:
    """A compaction boundary is either an explicit compact_boundary subtype
    (newer CLIs) or a *repeated* system/init event (2.1.149 stream mode)."""
    if ev.get("type") == "system":
        st = ev.get("subtype")
        if st == "compact_boundary":
            return True
        if st == "init" and init_seen >= 1:
            return True
    return False


def split_phases_on_compaction(
    events: list[dict],
) -> tuple[list[dict], list[dict], bool]:
    """Split an ordered stream-json event list into (research, impl, compacted).

    The boundary is the *second* system/init (or any compact_boundary). The
    boundary event itself starts the impl slice. If no boundary is found,
    everything is research and compacted is False.
    """
    init_seen = 0
    for i, ev in enumerate(events):
        if _is_compact_boundary(ev, init_seen):
            return events[:i], events[i:], True
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init_seen += 1
    return events, [], False


def events_to_timeline(events: list[dict], session_start: float) -> list[ToolEvent]:
    """Reconstruct the ToolEvent timeline from a slice of parsed events.
    Each event may carry a synthetic ``_t`` wall-clock stamp (added by the live
    driver); tests may omit it, in which case durations default to 0."""
    timeline: list[ToolEvent] = []
    pending: dict[str, tuple[str, str, dict, float]] = {}
    turn = 0
    for ev in events:
        now = ev.get("_t", session_start)
        etype = ev.get("type")
        if etype == "assistant":
            turn += 1
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    tid = block.get("id", "")
                    finput = block.get("input", {})
                    pending[tid] = (name, _summarise_input(name, finput), finput, now)
        elif etype == "user":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id", "")
                content = block.get("content", "")
                text = (
                    content if isinstance(content, str)
                    else "".join(c.get("text", "") for c in content if isinstance(c, dict))
                )
                name, summary, finput, issued = pending.pop(
                    tid, ("unknown", "", {}, now)
                )
                full = text[:_FULL_RESULT_MAX_CHARS]
                if len(text) > _FULL_RESULT_MAX_CHARS:
                    full += f"\n[...{len(text)} total chars]"
                timeline.append(ToolEvent(
                    elapsed_s=now - session_start,
                    turn=turn,
                    tool_name=name,
                    input_summary=summary,
                    result_chars=len(text),
                    duration_s=now - issued,
                    full_input=finput,
                    full_result=full,
                ))
    return timeline


def usage_from_events(events: list[dict]) -> PhaseUsage:
    """Pull token/turn accounting from the (last) result event in a slice."""
    result = None
    for ev in events:
        if ev.get("type") == "result":
            result = ev
    if result is None:
        return PhaseUsage()
    usage = result.get("usage", {})
    iters = usage.get("iterations", [usage])
    base = sum(it.get("input_tokens", 0) for it in iters)
    cc = sum(it.get("cache_creation_input_tokens", 0) for it in iters)
    cr = sum(it.get("cache_read_input_tokens", 0) for it in iters)
    out = sum(it.get("output_tokens", 0) for it in iters)
    return PhaseUsage(
        input_tokens=base + cc + cr,
        base_input_tokens=base,
        cache_creation_tokens=cc,
        cache_read_tokens=cr,
        output_tokens=out,
        turns=result.get("num_turns", 0),
        cost_usd=result.get("total_cost_usd", 0.0) or 0.0,
        answer=result.get("result", ""),
    )


def compaction_summary_chars(impl_events: list[dict]) -> int:
    """Size of the compaction summary, if the boundary init carries one."""
    for ev in impl_events:
        if ev.get("type") != "system":
            continue
        for key in ("compact_metadata", "summary", "compactSummary"):
            val = ev.get(key)
            if isinstance(val, str):
                return len(val)
            if isinstance(val, dict):
                txt = val.get("summary") or val.get("text") or ""
                if txt:
                    return len(txt)
        break
    return 0


# ---------------------------------------------------------------------------
# Live driver — one persistent stream-json session
# ---------------------------------------------------------------------------

def _user_msg(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    })


def run_session(
    turns: list[str],
    working_dir: str,
    allowed_tools: list[str],
    max_turns: int = 60,
    timeout_s: int = 2400,
    model: str | None = None,
) -> tuple[list[dict], float]:
    """Drive one persistent Claude Code session over stream-json stdin.

    ``turns`` is the ordered list of user messages; include the literal
    ``"/compact"`` where compaction should occur. Returns the parsed event
    list (each event stamped with a synthetic ``_t`` wall-clock) and wall time.
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
        "--allowedTools", ",".join(allowed_tools),
    ]
    use_model = model or MODEL
    if use_model:
        cmd += ["--model", use_model]

    stdin_payload = "\n".join(_user_msg(t) for t in turns) + "\n"
    events: list[dict] = []
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
            env={**os.environ},
        )
    except FileNotFoundError:
        return [{"type": "result", "subtype": "error",
                 "error": "claude CLI not found — is Claude Code installed?"}], 0.0

    # Send all turns up front; the CLI drains the queue sequentially.
    try:
        proc.stdin.write(stdin_payload)
        proc.stdin.flush()
        proc.stdin.close()
    except BrokenPipeError:
        pass

    deadline = start + timeout_s
    init_count = 0
    try:
        for raw in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                events.append({"type": "result", "subtype": "error",
                               "error": f"timed out after {timeout_s}s", "_t": time.time()})
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev["_t"] = time.time()
            events.append(ev)

            etype = ev.get("type")
            if etype == "system" and ev.get("subtype") == "init":
                init_count += 1
                if init_count >= 2:
                    logger.info("  /compact boundary reached (init #%d)", init_count)
            elif etype == "result":
                logger.info("  phase result: %s (turns=%d)",
                            ev.get("subtype"), ev.get("num_turns", 0))
            elif etype == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "?").replace("mcp__vectr__", "vectr::")
                        logger.info("  tool: %-26s %s", name,
                                    _summarise_input(block.get("name", ""), block.get("input", {})))
    finally:
        proc.wait()

    if proc.returncode not in (0, None) and not any(
        e.get("type") == "result" for e in events
    ):
        stderr = proc.stderr.read().strip()[:500]
        events.append({"type": "result", "subtype": "error",
                       "error": f"claude exited {proc.returncode}: {stderr}",
                       "_t": time.time()})
    return events, time.time() - start
