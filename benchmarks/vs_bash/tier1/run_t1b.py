#!/usr/bin/env python3
"""Tier-1b: locate-vs-search steering -- one real model in the loop.

Design: .claude/bench-vectr-vs-bash/plan.md, "Tier 1" section (T1b). Unlike
Tier 0 (benchmarks/vs_bash/run_tier0.py), this is NOT a scripted replay: for
each name-a-symbol task, a real `claude -p` agent session decides for itself
which tool to reach for -- vectr_locate, vectr_search, vectr_trace, or plain
Bash/Grep/Read. The question this answers is observational: given a live
vectr MCP daemon and no instruction about which tool to use, which one does
the agent actually pick, and at what token cost? There is no ablation arm and
no pre-registered "expected" tool -- see tasks_t1b_<corpus>.jsonl's
`expected_tool: null` and the honesty rules below.

THIS SCRIPT NEVER SPAWNS `claude -p` UNLESS EXPLICITLY RUN WITHOUT --dry-run.
Automation (subagents, CI, hooks) must only ever call this with --dry-run;
live sessions burn the user's Claude Code quota and are sentinel's call to
make (see README.md in this directory).

Honesty rules (mirrors run_tier0.py / plan.md):
  - Tasks in tasks_t1b_<corpus>.jsonl are pre-registered: written from general
    knowledge of the corpus, WITHOUT looking up the real symbol's file/line,
    and disjoint from every symbol already used in tasks_<corpus>.jsonl (the
    Tier-0 task set for the same corpus).
  - No query-content branching anywhere in this driver: the `claude -p`
    invocation, the preamble, and the parsing logic are identical for every
    task regardless of what the task's prompt says (.claude/HEURISTIC-
    DIRECTIVE.md R5). The only per-task variation is the prompt text itself,
    which is task-authoring data, not runtime classification.
  - Tool-choice counting reads the tool-call events the `claude` CLI itself
    emits (the `name` field on a `tool_use` block) -- it does not infer
    anything from the task's query text.

Usage:
    # Compose + print the 6 django tasks' claude -p invocations; run the live
    # daemon preflight; spawn nothing. Safe for automation.
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t1b.py --dry-run

    # Real run (sentinel-gated, burns quota) -- one or a few tasks:
    ./.venv/bin/python benchmarks/vs_bash/tier1/run_t1b.py --tasks B01,B03
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
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

# Reuse the tiktoken cl100k proxy counter from the Tier-0 driver rather than
# re-implementing it -- same convention, same encoder, comparable numbers
# across tiers.
sys.path.insert(0, str(_VS_BASH_DIR))
from run_tier0 import count_tokens  # noqa: E402

# ---------------------------------------------------------------------------
# claude CLI resolution -- env override, then PATH, then the desktop-app-
# managed install (the same location benchmarks/harness/run_multi_repo.py
# resolves), newest version first. Derived from the home dir at run time --
# no hardcoded absolute home-directory paths in committed files.
# ---------------------------------------------------------------------------

def _resolve_claude_bin() -> str:
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    import glob
    import re as _re
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    )

    def _version_key(p: str) -> list[int]:
        m = _re.search(r"/claude-code/([0-9.]+)/", p)
        return [int(x) for x in m.group(1).split(".")] if m else [0]

    candidates = sorted(glob.glob(pattern), key=_version_key)
    if candidates:
        return candidates[-1]
    return "claude"


CLAUDE_BIN = _resolve_claude_bin()

# MCP server name registered in the generated --mcp-config file; tool names
# the CLI reports are "mcp__<this>__<tool>", e.g. "mcp__vectr__vectr_locate".
_MCP_SERVER_NAME = "vectr"

# Fixed, task-content-independent preamble. Identical for every task -- the
# only thing that varies per task is the appended prompt text (task-authoring
# data), never a runtime branch on what that text says (R5).
_PREAMBLE = (
    "You are working in the codebase at your current working directory. You have "
    "vectr's MCP tools available (vectr_search, vectr_locate, vectr_trace, vectr_map, "
    "vectr_fetch, vectr_remember, vectr_recall, and more) as well as your normal shell "
    "and file tools (Bash, Grep, Read, Glob). Use whichever tool or tools you judge best "
    "to answer the question below about this codebase. This is a read-only exploration "
    "task -- do not modify any files. Answer concisely once you are confident.\n\n"
)

# Tool-name prefixes/values used only to LABEL already-emitted tool_use
# events for the aggregate report (which tool did the agent pick) -- this
# reads the CLI's own event data, never the task's query text.
_VECTR_TOOL_PREFIX = "mcp__vectr__"
_BASH_NATIVE_TOOLS = ("Bash", "Grep", "Read", "Glob")


# ---------------------------------------------------------------------------
# MCP config generation
# ---------------------------------------------------------------------------

def build_mcp_config(base_url: str) -> dict:
    """Streamable-http MCP config pointing at the live django bench daemon,
    same schema main.py's own IDE-config writer emits (`mcpServers.<name>`)."""
    return {"mcpServers": {_MCP_SERVER_NAME: {"type": "http", "url": f"{base_url}/mcp"}}}


# ---------------------------------------------------------------------------
# Session spawn + stream-json parsing
# ---------------------------------------------------------------------------

def _spawn_env() -> dict:
    """Env for a spawned `claude -p` session that mimics a fresh user
    invocation, not a nested child of whatever session is running this
    driver -- strips CLAUDE_CODE_*/ANTHROPIC_* vars the harness's own
    process carries (same rationale as benchmarks/harness/eval_v2.py's
    `_spawn_env`, verified there against a live child-session confound)."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}


def run_claude_session(cmd: list[str], cwd: str, timeout_s: int) -> dict:
    """Spawn one `claude -p ... --output-format stream-json` session and
    collect its event stream, each event stamped with a wall-clock arrival
    time (`_t`) so per-tool-call durations are recoverable without needing a
    multi-message stdin protocol (this driver issues one prompt per session,
    unlike eval_v2.py's persistent multi-turn sessions).
    """
    events: list[dict] = []
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=_spawn_env(),
        )
    except FileNotFoundError:
        return {"events": [], "wall_s": 0.0, "returncode": None,
                "stderr_tail": "claude CLI not found on PATH"}

    deadline = start + timeout_s
    timed_out = False
    try:
        for raw in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                timed_out = True
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
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    wall_s = time.time() - start
    stderr_tail = ""
    try:
        stderr_tail = (proc.stderr.read() or "")[-2000:]
    except Exception:
        pass
    return {
        "events": events, "wall_s": wall_s, "returncode": proc.returncode,
        "stderr_tail": stderr_tail, "timed_out": timed_out,
    }


def parse_transcript(events: list[dict]) -> dict:
    """Reconstruct the tool-call sequence + final-turn accounting from a
    stream-json event list.

    ASSUMPTION (flagged for sentinel verification before any live run --
    this shape mirrors what benchmarks/harness/eval_v2.py already parses
    successfully from real `claude -p --output-format stream-json` output,
    but that file uses `--input-format stream-json` multi-turn sessions;
    this driver's single-prompt `-p "<text>"` invocation has not itself been
    exercised against a live CLI by this task):
      - assistant turns carry `message.content` as a list of blocks; a
        `tool_use` block has `id`, `name`, `input`.
      - the following `user` event carries the matching `tool_result` block
        keyed by `tool_use_id`, with `content` either a plain string or a
        list of `{"type": "text", "text": ...}` blocks.
      - exactly one terminal `result` event carries `num_turns`,
        `total_cost_usd`, `duration_ms`, `result` (the final answer text),
        and `is_error`.
    """
    pending: dict[str, dict] = {}
    tool_calls: list[dict] = []
    final = {"num_turns": None, "cost_usd": None, "duration_ms": None,
             "answer": "", "is_error": False, "error": None}

    for ev in events:
        etype = ev.get("type")
        now = ev.get("_t")
        content = ev.get("message", {}).get("content", [])
        if etype == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    pending[block.get("id", "")] = {
                        "tool": block.get("name", "unknown"),
                        "args": block.get("input", {}),
                        "issued_t": now,
                    }
        elif etype == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id", "")
                raw = block.get("content", "")
                text = raw if isinstance(raw, str) else "".join(
                    c.get("text", "") for c in raw if isinstance(c, dict)
                )
                call = pending.pop(tid, {"tool": "unknown", "args": {}, "issued_t": None})
                duration_ms = None
                if now is not None and call.get("issued_t") is not None:
                    duration_ms = round((now - call["issued_t"]) * 1000.0, 2)
                tool_calls.append({
                    "tool": call["tool"],
                    "args": call["args"],
                    "result_chars": len(text),
                    "result_tokens": count_tokens(text),
                    "duration_ms": duration_ms,
                    "is_error": bool(block.get("is_error", False)),
                })
        elif etype == "result":
            final["num_turns"] = ev.get("num_turns")
            final["cost_usd"] = ev.get("total_cost_usd")
            final["duration_ms"] = ev.get("duration_ms")
            final["is_error"] = bool(ev.get("is_error"))
            final["answer"] = ev.get("result", "") if not final["is_error"] else ""
            final["error"] = ev.get("result") if final["is_error"] else None

    return {
        "tool_calls": tool_calls,
        "final": final,
        "unresolved_tool_use_ids": list(pending.keys()),
    }


def summarize_task_result(task: dict, parsed: dict, wall_s: float) -> dict:
    """Per-task aggregate: tool-choice sequence + token/timing totals. Reads
    only the CLI's own tool-call events (name/args/result), never re-derives
    anything from the task's prompt text."""
    calls = parsed["tool_calls"]
    sequence = [c["tool"] for c in calls]
    counts = Counter(sequence)
    tokens_by_tool: dict[str, int] = {}
    for c in calls:
        tokens_by_tool[c["tool"]] = tokens_by_tool.get(c["tool"], 0) + c["result_tokens"]

    search_calls = [
        {"n_results": c["args"].get("n_results"), "query": c["args"].get("query")}
        for c in calls if c["tool"] == f"{_VECTR_TOOL_PREFIX}vectr_search"
    ]
    used_vectr = any(t.startswith(_VECTR_TOOL_PREFIX) for t in sequence)
    used_bash_native = any(t in _BASH_NATIVE_TOOLS for t in sequence)

    return {
        "id": task["id"],
        "category": task.get("category"),
        "symbol_hint": task.get("symbol_hint"),
        "prompt": task["prompt"],
        "wall_s": round(wall_s, 2),
        "num_turns": parsed["final"]["num_turns"],
        "cost_usd": parsed["final"]["cost_usd"],
        "reported_duration_ms": parsed["final"]["duration_ms"],
        "is_error": parsed["final"]["is_error"],
        "error": parsed["final"]["error"],
        "tool_sequence": sequence,
        "tool_call_counts": dict(counts),
        "tokens_by_tool": tokens_by_tool,
        "total_tool_result_tokens": sum(c["result_tokens"] for c in calls),
        "search_calls": search_calls,
        "used_vectr": used_vectr,
        "used_bash_native": used_bash_native,
        "final_answer_tokens": count_tokens(parsed["final"]["answer"]),
    }


# ---------------------------------------------------------------------------
# Task loading (same JSONL-by-id convention as run_tier0.load_jsonl)
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


# ---------------------------------------------------------------------------
# Command composition
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
        "--max-turns", str(max_turns),
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tier-1b locate-vs-search steering")
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--corpus", default="django")
    parser.add_argument("--tasks", default=None, help="Comma-separated task ids to run; default = all")
    parser.add_argument("--fixture-root", default=None, help="Override the corpus checkout root (default tmp/vectr-accept-<corpus>)")
    parser.add_argument("--model", default="sonnet", help="Model alias/id passed to --model (cheaper-models rule: sonnet by default)")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=300, help="Per-session wall-clock timeout, seconds")
    parser.add_argument("--smoke", action="store_true", help="Tag the results file as a smoke test")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compose + print every claude -p invocation and run the daemon "
                              "preflight; spawn nothing. The only mode automation may use.")
    args = parser.parse_args(argv)

    base = f"http://{args.host}:{args.port}"
    tasks_path = _HERE / f"tasks_t1b_{args.corpus}.jsonl"
    fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"vectr-accept-{args.corpus}"

    if not tasks_path.exists():
        print(f"ERROR: no task file: {tasks_path}", file=sys.stderr)
        return 1
    tasks = load_jsonl(tasks_path)
    task_ids = [t.strip() for t in args.tasks.split(",")] if args.tasks else sorted(tasks.keys())

    # Preflight (pattern copied from run_tier0.py main()): refuse to run
    # against a daemon whose index has not attached yet -- vectr serves HTTP
    # before the warm index loads, so a too-early run would spend real quota
    # against an empty index.
    try:
        with urllib.request.urlopen(f"{base}/v1/status", timeout=10) as r:
            daemon_status = json.load(r)
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"ERROR: cannot read {base}/v1/status: {exc}", file=sys.stderr)
        return 1
    indexed_files = int(daemon_status.get("indexed_files") or 0)
    if indexed_files == 0:
        print(f"ERROR: daemon at {base} reports indexed_files=0 -- index not attached "
              f"yet; refusing to run. Wait for indexing to finish and retry.", file=sys.stderr)
        return 1

    print("=" * 88)
    print(f"Tier-1b locate-vs-search steering -- corpus={args.corpus}  daemon={base}  "
          f"fixture={fixture_root}")
    print(f"index: {indexed_files} files / {daemon_status.get('total_chunks')} chunks")
    print(f"model={args.model}  max_turns={args.max_turns}  dry_run={args.dry_run}")
    print(f"Running {len(task_ids)} task(s): {task_ids}")
    print("=" * 88)

    if not fixture_root.exists():
        msg = f"fixture root does not exist: {fixture_root}"
        if args.dry_run:
            print(f"[dry-run] NOTE: {msg} (not fatal for --dry-run -- only needed as the "
                  f"session cwd on a real run)")
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 1

    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, text=True,
    ).strip()
    out_dir = _REPO_ROOT / "results" / "vectr-vs-bash" / args.corpus / sha / "t1b"
    out_dir.mkdir(parents=True, exist_ok=True)

    mcp_config_path = out_dir / "mcp_config.json"
    mcp_config_path.write_text(json.dumps(build_mcp_config(base), indent=2) + "\n")
    print(f"MCP config written: {mcp_config_path}")

    results = []
    for tid in task_ids:
        if tid not in tasks:
            print(f"[SKIP] {tid}: not in {tasks_path.name}")
            continue
        task = tasks[tid]
        cmd = compose_command(task, mcp_config_path, args.model, args.max_turns)

        print(f"\n[{tid}] category={task.get('category')}  symbol_hint={task.get('symbol_hint')!r}")
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
        transcript_path = out_dir / f"{tid}_{stamp}.jsonl"
        with open(transcript_path, "w") as fh:
            for ev in session["events"]:
                fh.write(json.dumps(ev) + "\n")
        print(f"  transcript: {transcript_path}")

        parsed = parse_transcript(session["events"])
        summary = summarize_task_result(task, parsed, session["wall_s"])
        summary["transcript_path"] = str(transcript_path.relative_to(_REPO_ROOT))
        results.append(summary)

        print(f"  tools: {summary['tool_sequence']}")
        print(f"  used_vectr={summary['used_vectr']}  used_bash_native={summary['used_bash_native']}  "
              f"tokens={summary['total_tool_result_tokens']}  turns={summary['num_turns']}  "
              f"wall_s={summary['wall_s']}")

    if args.dry_run:
        print("\n" + "=" * 88)
        print(f"[dry-run] preflight OK, {len(task_ids)} command(s) composed, 0 sessions spawned.")
        print("=" * 88)
        return 0

    # ------------------------------------------------------------------
    # Aggregate + write SHA-stamped results
    # ------------------------------------------------------------------
    tool_choice_totals: dict[str, int] = {}
    for r in results:
        for tool, n in r["tool_call_counts"].items():
            tool_choice_totals[tool] = tool_choice_totals.get(tool, 0) + n

    aggregate = {
        "corpus": args.corpus,
        "vectr_sha": sha,
        "smoke_test": bool(args.smoke),
        "port": args.port,
        "model": args.model,
        "max_turns": args.max_turns,
        "task_ids": task_ids,
        "results": results,
        "tool_choice_totals": tool_choice_totals,
        "tasks_used_vectr": sum(1 for r in results if r["used_vectr"]),
        "tasks_used_bash_native": sum(1 for r in results if r["used_bash_native"]),
        "tasks_scored": len(results),
    }
    stamp = time.strftime("%Y%m%dT%H%M%S")
    tag = "smoke" if args.smoke else "run"
    out_path = out_dir / f"t1b_{tag}_{stamp}.json"
    out_path.write_text(json.dumps(aggregate, indent=2))

    print("\n" + "=" * 88)
    print(f"tool_choice_totals: {tool_choice_totals}")
    print(f"tasks_used_vectr={aggregate['tasks_used_vectr']}/{aggregate['tasks_scored']}  "
          f"tasks_used_bash_native={aggregate['tasks_used_bash_native']}/{aggregate['tasks_scored']}")
    print(f"Results written: {out_path}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
