#!/usr/bin/env python3
"""Tier-0 deterministic cost-to-answer replay: vectr (MCP) vs bash-native
retrieval (grep/sed/find/read), one corpus at a time.

Design: .claude/bench-vectr-vs-bash/plan.md, "Tier 0" section. Zero LLM
quota -- both arms are scripted replays of pre-registered call/command
sequences, never a model in the loop.

Inputs (per corpus, e.g. "django"):
  - tasks_<corpus>.jsonl  -- pre-registered queries + bash recipes, written
    and committed BEFORE any gold span was looked up (honesty rule).
  - gold_<corpus>.jsonl   -- gold file:line spans + must-contain snippets,
    curated in a separate, later commit.

vectr arm: a fixed, per-archetype call recipe over the MCP JSON-RPC
transport (/mcp tools/call) -- the same tool sequence an instructed agent
would follow (locate -> fallback search, trace for who-calls-X, etc.). The
archetype -> tool-sequence mapping is a structural constant (ARCHETYPE_PLAN
below), fixed once per archetype, never branching on a task's query text.

bash arm: executes each task's pre-registered shell command list verbatim
against the real corpus checkout, one subprocess per list entry = one call.

Both arms measure: rendered-response tokens (tiktoken cl100k proxy), call
count, wall-clock ms, and hit@call-k against the gold "must_contain_any"
snippets (substring match against accumulated captured text -- the same
matching primitive benchmarks/harness/run_acceptance.py uses for its own
assertions, not a new heuristic). The MCP session's one-time tools/list
handshake cost is measured once and reported separately, never charged
per task (plan.md: "amortized session overhead...as a separate reported
line").

Usage:
    python3 run_tier0.py --port 8798 --corpus django --tasks T01,T02,T11
    python3 run_tier0.py --port 8798 --corpus django          # full set
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parent.parent

# ---------------------------------------------------------------------------
# Fixed per-archetype vectr call plans (structural, not query-content-based).
#
# Each entry is a list of steps; each step is (tool, arg_builder) where
# arg_builder(task) returns the MCP `arguments` dict for that tool call.
# A "locate"/"trace" step whose result carries vectr's own "no symbol"
# honesty banner triggers the pre-registered fallback step (same convention
# benchmarks/harness/vectr_full_audit.py already uses to read vectr's own
# response text -- this inspects the TOOL's output, not the query).
# ---------------------------------------------------------------------------

def _search_args(task: dict, query_key: str = "query", n: int = 5) -> dict:
    return {"query": task[query_key], "n_results": n}


def _locate_args(task: dict) -> dict:
    return {"name": task["symbol"]}


def _trace_args(task: dict) -> dict:
    return {"name": task["symbol"], "direction": "callers"}


def _lastsegment_args(task: dict) -> dict:
    return {"name": task["symbol"].rsplit(".", 1)[-1]}


ARCHETYPE_PLAN: dict[int, list[tuple[str, str]]] = {
    # archetype -> list of (tool, step_kind). step_kind selects an
    # arg-builder + a "run only if the previous step's output looks empty"
    # rule, both applied uniformly per archetype -- never per task.
    1: [("locate", "primary"), ("search", "fallback")],   # known-symbol lookup
    2: [("locate", "primary"), ("search", "fallback")],   # qualified name
    # misremembered/typo: on a qualified-name miss, retry locate with the
    # last dot-segment (what an instructed agent does when the class half of
    # a guess is the shaky part), then semantic search as final fallback.
    3: [("locate", "primary"), ("locate", "followup_lastsegment"), ("search", "fallback")],
    4: [("search", "primary")],                            # NL concept
    5: [("search", "primary")],                            # absent topic
    # who-calls-X: trace, then locate each caller name the trace itself
    # rendered (trace output today carries no fetch-chainable ids, so
    # locate-by-name is the only in-band way to read a caller's definition).
    # If the trace itself dead-ends (mis-named symbol), fall back to search --
    # the same recovery the product's own trace-empty hint instructs.
    6: [("trace", "primary"), ("locate", "followup_trace_callers"), ("search", "fallback")],
    7: [("search", "primary")],                            # stack-trace literal
    8: [("search", "primary")],                            # doc/howto
    9: [("search", "primary"), ("search", "hop2")],        # cross-file flow
    10: [("locate", "primary"), ("search", "fallback_n8")], # structural
}

# Uniform cap on how many traced callers the archetype-6 followup locates,
# applied in listed order -- truncation is always printed, never silent.
_TRACE_FOLLOWUP_CAP = 5

_TRACE_CALLER_LINE = re.compile(r"^\s+(\S+)\s+in\s+\S+:\d+", re.MULTILINE)


def _parse_trace_caller_names(trace_text: str) -> list[str]:
    """Caller names as rendered by vectr_trace's own 'Name  in path:line'
    lines, restricted to the 'Called by' section of the output. This parses
    the TOOL's response text (the same convention _looks_empty uses), never
    the task's query text.
    """
    section = trace_text.split("\nCalls:", 1)[0]
    seen: set[str] = set()
    names: list[str] = []
    for m in _TRACE_CALLER_LINE.finditer(section):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names

_NOT_FOUND_MARKERS = ("no symbol", "no results", "not found")


def _looks_empty(text: str) -> bool:
    """Vectr's own honesty banner for a miss (locate/search/trace) -- read
    from the TOOL's response text, the same convention already used by
    benchmarks/harness/vectr_full_audit.py's `found = not ("No symbol" ...)`
    check. Not a query-content heuristic: it classifies vectr's OUTPUT.
    """
    low = text.lower().strip()
    if not low:
        return True
    return any(m in low for m in _NOT_FOUND_MARKERS)


def build_vectr_steps(task: dict) -> list[tuple[str, dict]]:
    """Resolve the archetype's fixed plan into concrete (tool, args) calls
    for this task. Fallback/hop2 steps are appended as pre-registered next
    steps in the plan -- whether they actually fire is decided at run time
    by inspecting the previous step's own output (see _looks_empty), not by
    parsing the task's query text.
    """
    plan = ARCHETYPE_PLAN[task["archetype"]]
    steps = []
    for tool, kind in plan:
        if tool == "locate" and kind == "followup_lastsegment":
            seg = _lastsegment_args(task)
            if seg["name"] != task["symbol"]:  # undotted symbol: retry would be identical
                steps.append(("locate", seg, kind))
        elif tool == "locate" and kind == "followup_trace_callers":
            # args are resolved at run time from the trace step's own output
            steps.append(("locate", {}, kind))
        elif tool == "locate":
            steps.append(("locate", _locate_args(task), kind))
        elif tool == "trace":
            steps.append(("trace", _trace_args(task), kind))
        elif tool == "search" and kind == "hop2":
            steps.append(("search", _search_args(task, "query_2"), kind))
        elif tool == "search" and kind == "fallback_n8":
            steps.append(("search", _search_args(task, "query", n=8), kind))
        else:
            steps.append(("search", _search_args(task), kind))
    return steps


# ---------------------------------------------------------------------------
# Token counting (tiktoken cl100k_base proxy -- report deltas, not absolutes)
# ---------------------------------------------------------------------------

_ENCODER = None


def count_tokens(text: str) -> int:
    global _ENCODER
    if _ENCODER is None:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    if not text:
        return 0
    return len(_ENCODER.encode(text))


# ---------------------------------------------------------------------------
# MCP transport helpers
# ---------------------------------------------------------------------------

def mcp_call(base: str, method: str, params: dict | None = None, timeout: int = 60) -> tuple[dict, float]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    req = urllib.request.Request(
        f"{base}/mcp", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    return d, wall_ms


def mcp_tools_call_text(base: str, name: str, arguments: dict) -> tuple[str, float]:
    d, wall_ms = mcp_call(base, "tools/call", {"name": name, "arguments": arguments})
    if "error" in d:
        err = d["error"]
        return f"[MCP ERROR {err.get('code')}] {err.get('message')}", wall_ms
    blocks = d.get("result", {}).get("content", [])
    text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return text, wall_ms


def measure_tools_list_overhead(base: str) -> dict:
    d, wall_ms = mcp_call(base, "tools/list")
    raw = json.dumps(d.get("result", d))
    return {"tokens": count_tokens(raw), "wall_ms": round(wall_ms, 2), "raw_bytes": len(raw)}


# ---------------------------------------------------------------------------
# Arm execution. Both arms return {"calls": [...]}, where each call dict
# carries a "_text" key (the raw captured output for that single call) plus
# "tokens"/"wall_ms" summary fields -- main() populates this per task and
# score_task() below reads "_text" for gold substring matching.
# ---------------------------------------------------------------------------

def run_vectr_arm(task: dict, base: str) -> dict:
    steps = build_vectr_steps(task)
    calls = []
    prev_empty = False
    for tool, call_args, kind in steps:
        if kind in ("fallback", "fallback_n8", "followup_lastsegment") and not prev_empty:
            continue  # pre-registered fallback only fires on a genuine miss
        if kind == "followup_trace_callers":
            if prev_empty or not calls:
                continue  # the trace itself missed -- nothing to locate
            names = _parse_trace_caller_names(calls[-1]["_text"])
            if len(names) > _TRACE_FOLLOWUP_CAP:
                print(f"  [note] {task['id']}: locating first {_TRACE_FOLLOWUP_CAP} of "
                      f"{len(names)} traced callers (uniform archetype cap)")
            any_located = False
            for name in names[:_TRACE_FOLLOWUP_CAP]:
                text, wall_ms = mcp_tools_call_text(base, "vectr_locate", {"name": name})
                empty = _looks_empty(text)
                any_located = any_located or not empty
                calls.append({
                    "tool": "vectr_locate", "args": {"name": name}, "kind": kind,
                    "wall_ms": round(wall_ms, 2), "tokens": count_tokens(text),
                    "empty": empty, "_text": text,
                })
            # A later fallback step fires only when this whole stage missed:
            # every followup locate empty (or none issued from a parsed name).
            if names:
                prev_empty = not any_located
            continue
        text, wall_ms = mcp_tools_call_text(base, f"vectr_{tool}", call_args)
        prev_empty = _looks_empty(text)
        calls.append({
            "tool": f"vectr_{tool}", "args": call_args, "kind": kind,
            "wall_ms": round(wall_ms, 2), "tokens": count_tokens(text),
            "empty": prev_empty, "_text": text,
        })
    return {"calls": calls}


def run_bash_arm(task: dict, cwd: Path) -> dict:
    calls = []
    for cmd in task["bash_recipe"]:
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=30,
            )
            out, rc = proc.stdout, proc.returncode
        except subprocess.TimeoutExpired:
            out, rc = "", -1
        wall_ms = (time.perf_counter() - t0) * 1000.0
        calls.append({
            "cmd": cmd, "wall_ms": round(wall_ms, 2), "tokens": count_tokens(out),
            "bytes": len(out), "returncode": rc, "_text": out,
        })
    return {"calls": calls}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def hit_at_call_k(call_texts: list[str], must_contain_any: list[str]) -> int | None:
    """Smallest 1-indexed k such that the text accumulated through call k
    contains any gold snippet as a substring. None if never hit.
    """
    if not must_contain_any:
        return None
    acc = ""
    for k, text in enumerate(call_texts, start=1):
        acc += "\n" + text
        if any(snippet in acc for snippet in must_contain_any):
            return k
    return None


def score_task(task: dict, gold: dict, vectr_result: dict, bash_result: dict) -> dict:
    must = gold.get("must_contain_any", [])
    expect_absence = gold.get("expect_absence", False)

    v_call_texts = [c.get("_text", "") for c in vectr_result["calls"]]
    b_call_texts = [c.get("_text", "") for c in bash_result["calls"]]

    v_hit = hit_at_call_k(v_call_texts, must) if not expect_absence else None
    b_hit = hit_at_call_k(b_call_texts, must) if not expect_absence else None

    v_tokens = sum(c["tokens"] for c in vectr_result["calls"])
    b_tokens = sum(c["tokens"] for c in bash_result["calls"])
    v_wall = sum(c["wall_ms"] for c in vectr_result["calls"])
    b_wall = sum(c["wall_ms"] for c in bash_result["calls"])

    out = {
        "id": task["id"],
        "archetype": task["archetype"],
        "archetype_name": task["archetype_name"],
        "expect_absence": expect_absence,
        "vectr": {
            "calls": len(vectr_result["calls"]),
            "tokens": v_tokens,
            "wall_ms": round(v_wall, 2),
            "hit_at_call": v_hit,
            "answered": v_hit is not None,
        },
        "bash": {
            "calls": len(bash_result["calls"]),
            "tokens": b_tokens,
            "wall_ms": round(b_wall, 2),
            "hit_at_call": b_hit,
            "answered": b_hit is not None,
        },
    }
    if expect_absence:
        # No positive gold text to match -- report the noise each arm
        # produced instead of a binary hit (plan.md A3 honesty framing).
        out["vectr"]["nonempty_calls"] = sum(1 for c in vectr_result["calls"] if not c["empty"])
        out["bash"]["nonempty_calls"] = sum(1 for c in bash_result["calls"] if c["bytes"] > 0)
    return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> dict[str, dict]:
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tier-0 vectr-vs-bash replay")
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--corpus", default="django")
    parser.add_argument("--tasks", default=None, help="Comma-separated task ids to run (smoke test); default = all")
    parser.add_argument("--fixture-root", default=None, help="Override the corpus checkout root (default tmp/vectr-accept-<corpus>)")
    parser.add_argument("--smoke", action="store_true", help="Tag the results file as a smoke test, not a full scoring run")
    args = parser.parse_args(argv)

    base = f"http://{args.host}:{args.port}"
    tasks_path = _HERE / f"tasks_{args.corpus}.jsonl"
    gold_path = _HERE / f"gold_{args.corpus}.jsonl"
    fixture_root = Path(args.fixture_root) if args.fixture_root else _REPO_ROOT / "tmp" / f"vectr-accept-{args.corpus}"

    if not fixture_root.exists():
        print(f"ERROR: fixture root does not exist: {fixture_root}", file=sys.stderr)
        return 1

    tasks = load_jsonl(tasks_path)
    gold = load_jsonl(gold_path)

    task_ids = [t.strip() for t in args.tasks.split(",")] if args.tasks else sorted(tasks.keys())

    # Preflight: refuse to score against a daemon whose index has not
    # attached yet. vectr serves HTTP before the warm index loads, so a
    # too-early run would replay every task against an empty index and
    # score a silent 0 (this exact failure produced one deleted run).
    try:
        with urllib.request.urlopen(f"{base}/v1/status", timeout=10) as r:
            daemon_status = json.load(r)
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"ERROR: cannot read {base}/v1/status: {exc}", file=sys.stderr)
        return 1
    indexed_files = int(daemon_status.get("indexed_files") or 0)
    if indexed_files == 0:
        print(f"ERROR: daemon at {base} reports indexed_files=0 -- index not attached "
              f"yet; refusing to score. Wait for indexing to finish and retry.", file=sys.stderr)
        return 1

    # Verify daemon reachable + measure one-time tools/list overhead.
    try:
        overhead = measure_tools_list_overhead(base)
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"ERROR: cannot reach vectr MCP endpoint at {base}/mcp: {exc}", file=sys.stderr)
        return 1

    print("=" * 88)
    print(f"Tier-0 replay -- corpus={args.corpus}  daemon={base}  fixture={fixture_root}")
    print(f"index: {indexed_files} files / {daemon_status.get('total_chunks')} chunks")
    print(f"tools/list overhead (one-time, amortized, NOT charged per task): "
          f"{overhead['tokens']} tokens, {overhead['wall_ms']}ms")
    print(f"Running {len(task_ids)} task(s): {task_ids}")
    print("=" * 88)

    results = []
    for tid in task_ids:
        if tid not in tasks:
            print(f"[SKIP] {tid}: not in {tasks_path.name}")
            continue
        if tid not in gold:
            print(f"[SKIP] {tid}: no gold entry in {gold_path.name}")
            continue
        task = tasks[tid]
        g = gold[tid]

        vectr_result = run_vectr_arm(task, base)
        bash_result = run_bash_arm(task, fixture_root)

        scored = score_task(task, g, vectr_result, bash_result)
        results.append(scored)

        v, b = scored["vectr"], scored["bash"]
        print(f"\n[{tid}] archetype={scored['archetype']}({scored['archetype_name']})  "
              f"expect_absence={scored['expect_absence']}")
        print(f"  vectr: calls={v['calls']} tokens={v['tokens']} wall_ms={v['wall_ms']} "
              f"hit_at_call={v['hit_at_call']} answered={v['answered']}"
              + (f" nonempty_calls={v.get('nonempty_calls')}" if scored["expect_absence"] else ""))
        print(f"  bash:  calls={b['calls']} tokens={b['tokens']} wall_ms={b['wall_ms']} "
              f"hit_at_call={b['hit_at_call']} answered={b['answered']}"
              + (f" nonempty_calls={b.get('nonempty_calls')}" if scored["expect_absence"] else ""))

    # ------------------------------------------------------------------
    # Aggregate + write SHA-stamped results
    # ------------------------------------------------------------------
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, text=True,
    ).strip()
    out_dir = _REPO_ROOT / "results" / "vectr-vs-bash" / args.corpus / sha
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    tag = "smoke" if args.smoke else "run"
    out_path = out_dir / f"tier0_{tag}_{stamp}.json"

    payload = {
        "corpus": args.corpus,
        "vectr_sha": sha,
        "smoke_test": bool(args.smoke),
        "port": args.port,
        "tools_list_overhead": overhead,
        "task_ids": task_ids,
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2))

    print("\n" + "=" * 88)
    n_answered_v = sum(1 for r in results if r["vectr"]["answered"])
    n_answered_b = sum(1 for r in results if r["bash"]["answered"])
    n_scored = sum(1 for r in results if not r["expect_absence"])
    print(f"Answered (non-absence tasks, {n_scored} scored): vectr {n_answered_v}/{n_scored}  bash {n_answered_b}/{n_scored}")
    print(f"Results written: {out_path}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
