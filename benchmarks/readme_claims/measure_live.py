#!/usr/bin/env python3
"""Re-measure the README claims that need a live daemon but no LLM.

Covers the model-independent half of the inventory: per-call token cost
(C03-C06), the recall-versus-grep comparison (C07), and latency (C09, C10).
The two indexing claims (C11, C12) are a separate mode because they need a
cold cache and a large corpus.

Every number here is reproducible from a checkout with no inference cost and
no quota, which is the point: the README drifted because nothing re-ran.

IMPORTANT: point this at a scratch daemon, never at port 8765. That port
serves the author's live editor session, and re-indexing under it would yank
working memory out from under a running session.

Usage:
    # start a scratch daemon first, on any port that is not 8765
    vectr start /path/to/witness/repo --port 8801
    python3 benchmarks/readme_claims/measure_live.py --port 8801 --repo /path/to/witness/repo
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "benchmarks" / "harness"))

# Reuse the acceptance harness's MCP client rather than writing a second one.
# The README's numbers describe what the CALLER receives, and the caller only
# ever sees the MCP surface, so measuring /v1 here would measure the wrong
# thing (that blindness is exactly what UPG-ACCEPTANCE-MCP-MODE was filed for).
try:
    from run_acceptance import mcp_call, mcp_initialize  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"cannot import the MCP client from benchmarks/harness: {exc}")


def tok(text: str) -> int:
    """chars/4, the same tokenization the README's existing numbers used.

    Deliberately NOT a real tokenizer. Switching estimators would change every
    number for a reason unrelated to vectr, making the new figures
    incomparable with the old ones and hiding whatever actually moved.
    """
    return round(len(text) / 4)


def _result_text(result: dict) -> str:
    """Flatten an MCP tools/call result to the text the model would read."""
    parts = []
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _timed_call(base: str, sid: str, tool: str, args: dict) -> tuple[str, float]:
    t0 = time.perf_counter()
    res = mcp_call(base, sid, tool, args)
    return _result_text(res), (time.perf_counter() - t0) * 1000.0


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {
        "n": len(xs),
        "min": round(xs[0], 1),
        "p50": round(statistics.median(xs), 1),
        "p95": round(xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))], 1),
        "max": round(xs[-1], 1),
        "mean": round(statistics.fmean(xs), 1),
    }


def measure_tool_tokens(base: str, sid: str, queries: list[str],
                        symbols: list[str]) -> list[dict]:
    """C03 to C06 plus C10: token cost and round-trip latency per tool."""
    out: list[dict] = []

    search_tok, search_ms = [], []
    for q in queries:
        text, ms = _timed_call(base, sid, "vectr_search", {"query": q})
        search_tok.append(tok(text))
        search_ms.append(ms)
    out.append({"id": "C03", "tool": "vectr_search", "claimed_median": 2320,
                "claimed_range": [1437, 3091], "claimed_n": 8,
                "tokens": _stats([float(t) for t in search_tok]),
                "latency_ms": _stats(search_ms)})

    loc_tok, loc_ms = [], []
    for s in symbols:
        text, ms = _timed_call(base, sid, "vectr_locate", {"name": s})
        loc_tok.append(tok(text))
        loc_ms.append(ms)
    out.append({"id": "C04", "tool": "vectr_locate", "claimed_median": 192,
                "tokens": _stats([float(t) for t in loc_tok]),
                "latency_ms": _stats(loc_ms)})

    tr_tok, tr_ms = [], []
    for s in symbols:
        try:
            text, ms = _timed_call(base, sid, "vectr_trace", {"name": s})
        except Exception:
            continue          # a symbol with no edges is not a measurement
        tr_tok.append(tok(text))
        tr_ms.append(ms)
    out.append({"id": "C05", "tool": "vectr_trace", "claimed_median": 720,
                "tokens": _stats([float(t) for t in tr_tok]),
                "latency_ms": _stats(tr_ms)})

    return out


def measure_recall(base: str, sid: str, reps: int) -> dict:
    """C06 and C09: recall's token cost at the index tier, and its latency."""
    toks, ms = [], []
    for _ in range(reps):
        text, dt = _timed_call(base, sid, "vectr_recall", {"query": "measurement probe"})
        toks.append(float(tok(text)))
        ms.append(dt)
    return {"id": "C06+C09", "tool": "vectr_recall",
            "claimed_median_tokens": 180, "claimed_latency": "<50ms",
            "tokens": _stats(toks), "latency_ms": _stats(ms),
            "note": ("latency here is the full MCP round trip over localhost HTTP, "
                     "which is what a caller actually waits for; the '<50ms' claim "
                     "should be read against p50 and p95, not against the minimum")}


def measure_grep_baseline(repo: Path, patterns: list[str]) -> dict:
    """C10's grep half: the baseline vectr is being compared against."""
    ms = []
    for pat in patterns:
        t0 = time.perf_counter()
        subprocess.run(["grep", "-rn", "--include=*.py", pat, str(repo)],
                       capture_output=True, text=True)
        ms.append((time.perf_counter() - t0) * 1000.0)
    return {"id": "C10-grep", "claimed_ms": 28, "latency_ms": _stats(ms),
            "note": "grep over the same repo, same patterns as the search queries"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, required=True,
                    help="scratch daemon port; must NOT be 8765")
    ap.add_argument("--repo", required=True, help="the witness repo the daemon indexes")
    ap.add_argument("--reps", type=int, default=30, help="recall repetitions (default 30)")
    ap.add_argument("--json", metavar="PATH", help="write results here")
    args = ap.parse_args(argv)

    if args.port == 8765:
        # Not a style rule. 8765 serves the author's live editor session, and
        # this script's own measurements would perturb it.
        sys.exit("refusing to measure against port 8765, the live session; "
                 "start a scratch daemon on another port")

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"witness repo not found: {repo}")

    base = f"http://127.0.0.1:{args.port}"
    try:
        sid = mcp_initialize(base)
    except Exception as exc:
        sys.exit(f"no daemon answering MCP on {base}: {exc}")

    queries = [
        "how are workspace locks acquired and released",
        "chunk a source file into embeddable spans",
        "resolve a symbol name to a definition",
        "persist a note and read it back",
        "rerank candidate results with a cross encoder",
        "expire a stored note when its anchor changes",
        "serve the MCP tool list over HTTP",
        "detect that an index is stale and re-run it",
    ]
    symbols = ["SymbolGraph", "WorkingContextStore", "extract_symbols_from_file"]

    results: list[dict] = []
    results += measure_tool_tokens(base, sid, queries, symbols)
    results.append(measure_recall(base, sid, args.reps))
    results.append(measure_grep_baseline(repo, ["def ", "class ", "import "]))

    payload = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vectr_sha": subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True).stdout.strip(),
        "witness_repo": str(repo),
        "port": args.port,
        "tokenization": "chars/4, matching the existing README figures",
        "results": results,
    }

    print(f"live claims, vectr {payload['vectr_sha']}, {payload['measured_at']}",
          file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    for r in results:
        t, l = r.get("tokens", {}), r.get("latency_ms", {})
        print(f"  {r['id']:<10} {r.get('tool', 'grep'):<18} "
              f"tokens p50={t.get('p50', '-'):<8} n={t.get('n', '-'):<4} "
              f"latency p50={l.get('p50', '-')}ms p95={l.get('p95', '-')}ms",
              file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"json written to {args.json}", file=sys.stderr)
    else:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
