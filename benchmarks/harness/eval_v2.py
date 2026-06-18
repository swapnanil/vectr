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

from pathlib import Path

from run_poc import (
    CLAUDE_BIN,
    MODEL,
    ToolEvent,
    _STANDARD_TOOLS,
    _VECTR_TOOLS,
    _FULL_RESULT_MAX_CHARS,
    _summarise_input,
    _git_head,
    _git_diff,
    _clear_vectr_memory,
    _get_vectr_notes_count,
    _reset_vectr_call_counts,
    _get_vectr_call_counts,
    _compute_answer_files,
)

from scoring import ExecScore, score_execution  # noqa: E402  (after run_poc import block)

logger = logging.getLogger("eval_v2")

# Always drive the global vectr binary — what the extension and end users use.
VECTR_BIN = "/opt/homebrew/bin/vectr" if os.path.exists("/opt/homebrew/bin/vectr") else "vectr"

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
    # Harness-injected memory (arm C). impl_injection > 0 after compaction is the
    # core "memory survived /compact with no prompt help" signal.
    research_injection: dict = field(default_factory=dict)
    impl_injection: dict = field(default_factory=dict)
    exec_score: "ExecScore | None" = None  # primary success signal (held-out test)

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
        # content may be a plain string (e.g. the compaction-injected summary
        # user message) rather than a list of blocks — skip those.
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            if etype == "assistant":
                turn += 1
            continue
        if etype == "assistant":
            turn += 1
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    tid = block.get("id", "")
                    finput = block.get("input", {})
                    pending[tid] = (name, _summarise_input(name, finput), finput, now)
        elif etype == "user":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
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


def _iter_injections(obj):
    """Yield (hook_event_name, additionalContext) for every hook-output envelope
    nested anywhere in a parsed event. Tolerant of how the CLI wraps the hook's
    stdout — it just looks for any dict carrying additionalContext."""
    if isinstance(obj, dict):
        ctx = obj.get("additionalContext")
        if isinstance(ctx, str) and ctx:
            name = obj.get("hookEventName") or obj.get("hook_event_name") or "?"
            yield name, ctx
        for v in obj.values():
            yield from _iter_injections(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_injections(v)


def hook_injection_stats(events: list[dict]) -> dict:
    """Aggregate harness-injected memory across a slice (arm C signal).

    Returns counts + total injected characters (injection tax ≈ chars/4 tokens),
    a per-hook breakdown, and the concatenated injected text (for relevance
    scoring against the task — injection precision)."""
    injections = 0
    chars = 0
    by_event: dict[str, int] = {}
    texts: list[str] = []
    for ev in events:
        if ev.get("type") != "system":
            continue
        for name, ctx in _iter_injections(ev):
            injections += 1
            chars += len(ctx)
            by_event[name] = by_event.get(name, 0) + 1
            texts.append(ctx)
    return {
        "injections": injections,
        "injected_chars": chars,
        "by_event": by_event,
        "injected_text": "\n".join(texts),
    }


def injection_precision(injected_text: str,
                        relevant_markers: list[str],
                        irrelevant_markers: list[str]) -> dict:
    """How on-topic was the harness-injected memory? Counts sentinel markers in
    the injected text. Used by the guardrail: when the note store is polluted
    with off-topic notes, a precise injector surfaces the relevant ones and not
    the irrelevant ones. precision = relevant / (relevant + irrelevant)."""
    rel = sum(1 for m in relevant_markers if m in injected_text)
    irr = sum(1 for m in irrelevant_markers if m in injected_text)
    total = rel + irr
    return {
        "relevant": rel,
        "irrelevant": irr,
        "precision": (rel / total) if total else None,
    }


def net_token_delta(totals_by_arm: dict[str, int], baseline: str) -> dict[str, int]:
    """Bottom-line token delta of each arm vs a baseline arm (negative = cheaper).
    Operates on already-totalled tokens, so the injection tax an arm paid is
    already included — this is the net, not the gross saving."""
    base = totals_by_arm.get(baseline)
    if base is None:
        return {}
    return {arm: t - base for arm, t in totals_by_arm.items() if arm != baseline}


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


def _spawn_env() -> dict:
    """Env for a spawned agent session that mimics a FRESH user invocation of
    Claude Code — not a nested child of the session running this harness.

    The harness runs inside Claude Code, so os.environ carries CLAUDE_CODE_*
    markers (CHILD_SESSION=1, SESSION_ID, ENTRYPOINT=claude-vscode, the agent
    SDK version, …) that flip a spawned `claude -p` into child-session /
    advisor-tool-deferral behavior — not how a real user's Claude Code runs.
    Strip every CLAUDE*/ANTHROPIC* var so each arm starts clean, then:
      - disable built-in auto-memory (~/.claude/projects/<proj>/memory/*.md),
        else every arm — including the bare baselines — silently gains a
        competing file-memory across /compact, confounding the comparison.
    OAuth auth still resolves via keychain / ~/.claude credentials (we never
    pass --bare, so keychain reads are allowed)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


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

    Messages are sent SYNCHRONOUSLY — each next message is written only after
    the previous one's completion event is seen, with stdin kept open until the
    end. (Piping all messages up front and closing stdin drops later messages
    when an early turn runs long: the CLI finishes the first message and exits
    rather than draining the buffer.) EVERY message — including ``/compact`` —
    completes on its own ``result`` event. /compact's result is empty
    (turns=0), emitted after the compaction sequence (status → init →
    compact_boundary → injected summary); waiting for it guarantees compaction
    has fully settled before the next message is sent. The compaction boundary
    for phase-splitting is recovered separately from the 2nd ``init`` event.
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-hook-events",   # surface vectr hook injections (arm C metrics)
        "--max-turns", str(max_turns),
        "--allowedTools", ",".join(allowed_tools),
    ]
    use_model = model or MODEL
    if use_model:
        cmd += ["--model", use_model]

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
            env=_spawn_env(),
        )
    except FileNotFoundError:
        return [{"type": "result", "subtype": "error",
                 "error": "claude CLI not found — is Claude Code installed?"}], 0.0

    def _send(text: str) -> None:
        try:
            proc.stdin.write(_user_msg(text) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    # Send the first message; subsequent ones are sent as each result lands.
    _send(turns[0])
    sent = 1

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
                # Every message (including /compact) completes on its result.
                if sent < len(turns):
                    _send(turns[sent])
                    sent += 1
                else:
                    break  # last message done; stop reading
            elif etype == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "?").replace("mcp__vectr__", "vectr::")
                        logger.info("  tool: %-26s %s", name,
                                    _summarise_input(block.get("name", ""), block.get("input", {})))
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        proc.wait()

    if proc.returncode not in (0, None) and not any(
        e.get("type") == "result" for e in events
    ):
        stderr = proc.stderr.read().strip()[:500]
        events.append({"type": "result", "subtype": "error",
                       "error": f"claude exited {proc.returncode}: {stderr}",
                       "_t": time.time()})
    return events, time.time() - start


# ---------------------------------------------------------------------------
# Per-arm prompt decoration
# ---------------------------------------------------------------------------
# All arms get the SAME task body. The only prompt difference is how each arm
# is told to persist findings across the /compact boundary and recover them —
# matching the mechanism that arm actually has. A1 gets nothing (true cold
# baseline); C gets nothing in the prompt because its CLAUDE.md + hooks carry
# the memory behaviour (that is the product surface under test).

_EXPLORE_PREAMBLE = {
    "A1": "",
    "A2": ("As you explore, maintain a NOTES.md file in the repo root: record the "
           "exact file paths, function/class signatures, line numbers, and gotchas "
           "you find. It is your only durable record for the implementation step.\n\n"),
    "B":  "",
    "C":  "",
    "D":  "",
}

_IMPL_PREAMBLE = {
    "A1": "",
    "A2": "First read NOTES.md — it holds the findings from your exploration.\n\n",
    "B":  ("Use vectr_search / vectr_locate to re-find the exact code you need; "
           "do not re-read whole files blindly.\n\n"),
    "C":  "",
    "D":  ("First call vectr_recall to retrieve the notes you stored while "
           "exploring, then implement.\n\n"),
}


def build_turns(arm: Arm, task, codebase_path: str, codebase_desc: str,
                exec_spec=None) -> list[str]:
    """[explore prompt, /compact, implement prompt] for the given arm + task.
    If exec_spec is given, the impl prompt names the output path the held-out
    test will import (the contract is pinned, but identical across arms)."""
    explore = (
        f"{codebase_desc} is at: {codebase_path}\n\n"
        f"{_EXPLORE_PREAMBLE[arm.id]}"
        f"{task.phase1_description}\n\n"
        "Do NOT implement anything yet — only explore and understand."
    )
    impl = f"{_IMPL_PREAMBLE[arm.id]}{task.phase2_description}"
    if exec_spec is not None:
        impl += exec_spec.output_instruction()
    return [explore, COMPACT_TURN, impl]


# ---------------------------------------------------------------------------
# Arm environment setup / teardown
# ---------------------------------------------------------------------------

def _run_vectr(args: list[str]) -> None:
    try:
        subprocess.run([VECTR_BIN, *args], check=False,
                       capture_output=True, text=True, timeout=120)
    except Exception as e:
        logger.warning("vectr %s failed: %s", " ".join(args), e)


def _remember_note(content: str, working_dir: str, port: int,
                   priority: str = "high") -> None:
    """Store one note in the running daemon (used to pre-seed guardrail memory)."""
    _run_vectr(["remember", content, "--priority", priority,
                "--path", working_dir, "--port", str(port)])


# Off-topic notes for the irrelevant-memory guardrail. These describe a
# DIFFERENT Django task (signals / async dispatch) than the one under test, so
# they are plausible same-repo noise. Each carries a sentinel marker so
# injection_precision can tell relevant from irrelevant after the run.
GUARDRAIL_IRRELEVANT_NOTES = [
    "IRRELEVANT-SIGTASK: Signal.send() in django/dispatch/dispatcher.py iterates "
    "self.receivers under self.lock; send_robust() swallows receiver exceptions.",
    "IRRELEVANT-SIGTASK: @receiver decorator and dispatch_uid prevent duplicate "
    "registration; weak references in _live_receivers cause silent drops.",
    "IRRELEVANT-SIGTASK: async_to_sync / sync_to_async live in asgiref.sync; "
    "Signal.asend() would need an async-aware dispatch loop.",
]
GUARDRAIL_IRRELEVANT_MARKERS = ["IRRELEVANT-SIGTASK"]


def setup_arm(arm: Arm, working_dir: str, vectr_port: int = 8765) -> None:
    """Configure the working dir for one arm. Never hand-writes settings.json —
    arm C's hooks come from `vectr init --hooks`."""
    # Start from a clean IDE config every time (strip any prior vectr blocks).
    _run_vectr(["init", "--reset-config", "--path", working_dir])
    if arm.uses_vectr:
        init_cmd = ["init", "--path", working_dir]
        if arm.hooks:
            init_cmd.append("--hooks")
        _run_vectr(init_cmd)
    if arm.use_memory:
        # Each rep starts with an empty note store.
        _clear_vectr_memory(port=vectr_port)
    notes_path = Path(working_dir) / "NOTES.md"
    if arm.notes_md:
        notes_path.write_text("")  # empty scaffold the agent fills in
    elif notes_path.exists():
        notes_path.unlink()         # no stale NOTES.md leaking across arms


# ---------------------------------------------------------------------------
# Scenario orchestrator
# ---------------------------------------------------------------------------

def run_compact_scenario(
    arm: Arm,
    task,
    working_dir: str,
    codebase_desc: str = "The Django source tree",
    vectr_port: int = 8765,
    model: str | None = None,
    max_turns: int = 60,
    timeout_s: int = 2400,
    exec_spec=None,
    python_bin: str = "python",
    seed_notes: list[str] | None = None,
) -> CompactSessionResult:
    """Run one arm × task: explore → /compact → implement in one session.
    If exec_spec is given, the held-out test is run after impl for the primary
    (execution-verified) success signal. seed_notes pre-populates the note store
    (after the per-rep clear) — used by the irrelevant-memory guardrail."""
    setup_arm(arm, working_dir, vectr_port=vectr_port)

    if seed_notes and arm.use_memory:
        for note in seed_notes:
            _remember_note(note, working_dir, vectr_port)
        logger.info("[%s] seeded %d guardrail note(s)", arm.id, len(seed_notes))

    notes_before = _get_vectr_notes_count(port=vectr_port) if arm.use_memory else 0
    if arm.use_memory:
        _reset_vectr_call_counts(port=vectr_port)
    head_before = _git_head(working_dir)

    turns = build_turns(arm, task, working_dir, codebase_desc, exec_spec=exec_spec)
    logger.info("[%s] %s — running compact scenario", arm.id, task.id)
    events, wall = run_session(
        turns, working_dir, arm.allowed_tools(),
        max_turns=max_turns, timeout_s=timeout_s, model=model,
    )

    research_ev, impl_ev, compacted = split_phases_on_compaction(events)
    session_start = events[0].get("_t", 0.0) if events else 0.0

    res = CompactSessionResult(
        arm_id=arm.id, task_id=task.id,
        research=usage_from_events(research_ev),
        impl=usage_from_events(impl_ev),
        research_timeline=events_to_timeline(research_ev, session_start),
        impl_timeline=events_to_timeline(impl_ev, session_start),
        compacted=compacted,
        wall_time_s=wall,
        compaction_summary_chars=compaction_summary_chars(impl_ev),
        research_injection=hook_injection_stats(research_ev),
        impl_injection=hook_injection_stats(impl_ev),
    )

    # Surface a hard error if the session never produced a result.
    err = next((e.get("error") for e in events
                if e.get("type") == "result" and e.get("error")), None)
    if err:
        res.error = err
        logger.error("[%s] %s error: %s", arm.id, task.id, err)
    if not compacted and not err:
        res.error = "no compaction boundary observed (/compact did not fire)"
        logger.error("[%s] %s — %s", arm.id, task.id, res.error)

    res.file_diff = _git_diff(working_dir, head_before)
    res.answer_files, res.answer_file_chars = _compute_answer_files(
        res.impl_timeline + res.research_timeline, working_dir
    )
    if arm.use_memory:
        res.notes_count_after = _get_vectr_notes_count(port=vectr_port)
        res.notes_count_before = notes_before
        res.vectr_tool_calls_all = _get_vectr_call_counts(port=vectr_port)

    if exec_spec is not None:
        res.exec_score = score_execution(exec_spec, working_dir, python_bin=python_bin)
        es = res.exec_score
        logger.info("[%s] %s  exec: %s  (%d/%d passed, success=%s)%s",
                    arm.id, task.id,
                    "ran" if es.ran else "DID-NOT-RUN",
                    es.passed, es.total, es.success,
                    f"  err={es.error}" if es.error else "")

    logger.info(
        "[%s] %s  compacted=%s  research(in=%d out=%d turns=%d) "
        "impl(in=%d out=%d turns=%d)  notes %d→%d  $%.4f  %.0fs",
        arm.id, task.id, compacted,
        res.research.input_tokens, res.research.output_tokens, res.research.turns,
        res.impl.input_tokens, res.impl.output_tokens, res.impl.turns,
        res.notes_count_before, res.notes_count_after, res.total_cost, wall,
    )
    return res
