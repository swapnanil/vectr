"""UPG-BANNER-CALIBRATION Phase 3 distribution harness.

Measures the ce_relevance distribution of vectr_search on two classes
of queries against a single corpus, and emits the rank-1 / rank-2
ce_relevance pairs (the data the relative-cliff hypothesis is tested
against) from the same run. The output is the evidence the reviewer's
distribution study needs to decide between a recalibrated absolute
floor and the relative cliff as the third low_confidence sub-signal
(UPG-BANNER-CALIBRATION Phase 3, the open Phase 1 question).

Two classes per corpus:
  - known-good: product_cases.jsonl cases whose `corpus` matches and
    whose `expect.top_k_contains.symbol` lands in the top-3 of the
    search (per the case's own labelled witness). These are the
    cases the existing notfound_floor has never flagged false-
    positively for.
  - known-absent: absent_queries.jsonl entries whose `corpus` matches.
    These are queries a hand-curator asserts are not in the corpus
    at all (every entry carries an `absent_reason` justifying the
    claim); they are the population against which a separation
    signal must work.

What this script does NOT do (lanes, the sentinel, or the reviewer's
own judgment):
  - It does NOT set a threshold. It measures and reports; a decision
    on which sub-signal (or none) to ship as the next default is
    product work that depends on this output, not a change the
    harness makes.
  - It does NOT mutate the daemon or its indexes. It is a read-only
    consumer of /v1/search.
  - It does NOT inspect query TEXT in any way that affects what it
    reports (the rail against query-side heuristics is preserved by
    design: the script's only per-query decision is which file/class
    to put each row into, and that is fixed by the JSONL, not by
    query content).

Daemon contract: /v1/search returns a list of CodeChunkResult with
`score` (the absolute per-(query, chunk) relevance — ce_relevance when
`score_source == "reranker"`, else the dense-cosine fallback) and
`score_source`. The display order is decided by the
quality/importance composite, not by the displayed score, so a
relevant chunk can legitimately have a lower `score` than a less
relevant one at adjacent ranks. This script records every per-rank
score in the order the daemon returned it (display order), which is
the order the low_confidence OR would see them in agent/searcher.py
(:813-817, the relative-cliff wiring).

Output: results/banner_calibration/<vectr-sha>/<corpus>/<run-id>.json
with a per-class distribution (count / min / max / median / deciles)
and a per-query row containing rank-1 and rank-2 ce_relevance plus
the top-N displayed scores. Per-class histograms (overlapping the
two classes on the same axis) are also written, ASCII-only, so a
reviewer can read overlap at a glance without a plotting library.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Top-N we ask the daemon for. The relative-cliff hypothesis needs at
# least rank-2; we ask for more so the per-query row is also useful for
# a future 3-rank or rank-curve analysis without re-running.
N_RESULTS = 10

# Default results root; matches the results/<repo>/<vectr-sha>/ convention
# benchmarks/vs_bash/ and benchmarks/arc_replay.py already use.
DEFAULT_RESULTS_ROOT = Path("results/banner_calibration")

# Path resolution rooted at the benchmark_calibration/ parent — the
# fixture files live in the standard benchmarks layout (acceptance for
# product_cases, banner_calibration/ for absent_queries).
_BENCH_ROOT = Path(__file__).resolve().parent.parent
_CASES_PATH = _BENCH_ROOT / "acceptance" / "product_cases.jsonl"
_ABSENT_PATH = Path(__file__).resolve().parent / "absent_queries.jsonl"


# ---------------------------------------------------------------------------
# Daemon helpers (mirror benchmarks/harness/run_acceptance.py)
# ---------------------------------------------------------------------------

def _get(base: str, path: str, *, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.load(r)


def _post(base: str, path: str, body: dict, *, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        raise SystemExit(f"fixture not found: {path}")
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _load_known_good(corpus: str) -> list[dict]:
    """Filter product_cases.jsonl to one corpus, keeping only cases whose
    `expect` mentions a top_k_contains symbol (the same case-shape
    that benchmarks/harness/run_acceptance.py evaluates). The other
    cases (score-source audits, language-coverage audits) have no
    per-query top-K to compare against and are not part of the
    known-good class for this measurement."""
    out: list[dict] = []
    for c in _load_jsonl(_CASES_PATH):
        if c.get("corpus") != corpus:
            continue
        expect = c.get("expect", {})
        if not isinstance(expect, dict):
            continue
        if not expect.get("top_k_contains"):
            continue
        out.append(c)
    return out


def _load_known_absent(corpus: str) -> list[dict]:
    out: list[dict] = []
    for c in _load_jsonl(_ABSENT_PATH):
        if c.get("corpus") != corpus:
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------

def _execute_query(base: str, query: str, language: str | None) -> dict:
    """POST /v1/search and return the raw response. Errors are caught
    here rather than raised so one bad query does not abort the run —
    a malformed network response is recorded in the per-query row as
    an error and the loop continues."""
    body: dict[str, Any] = {"query": query, "n_results": N_RESULTS}
    if language is not None:
        body["language"] = language
    try:
        return _post(base, "/v1/search", body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # last-ditch — see run_acceptance.py's pattern
        return {"_error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Per-case analysis
# ---------------------------------------------------------------------------

def _known_good_satisfied(case: dict, results: list[dict]) -> bool:
    """True iff the case's `top_k_contains` assertion holds for these
    results, using the same semantics run_acceptance.py uses (file
    substring + leaf symbol match). The known-good class is only
    meaningful if the case actually passed; a case whose expected
    symbol is NOT in the top-3 against THIS daemon may have been
    verified on a different corpus revision or with a different
    embedder, and counting its score would silently bias the
    distribution toward whatever the case's expected symbol scores
    on a wrong-shaped corpus. Surface that to the reviewer instead."""
    spec = case.get("expect", {}).get("top_k_contains", {})
    if not spec:
        return False
    k = spec.get("k", 3)
    file_substr = spec.get("file")
    sym = spec.get("symbol")
    sym_leaf = (sym or "").split(".")[-1]
    for r in results[:k]:
        file_ok = file_substr is None or file_substr in (r.get("file") or "")
        sym_text = r.get("symbol") or ""
        sym_ok = (
            sym is None
            or sym_text == sym
            or sym_text.endswith("." + sym)
            or sym_text.split(".")[-1] == sym_leaf
        )
        if file_ok and sym_ok:
            return True
    return False


def _scores_in_order(results: list[dict]) -> list[float | None]:
    """The displayed scores in display order. None when score_source is
    not 'reranker' (a raw dense-cosine fallback; the relative-cliff
    signal correctly stays silent on those per its contract)."""
    out: list[float | None] = []
    for r in results:
        if r.get("score_source") == "reranker":
            out.append(float(r.get("score", 0.0)))
        else:
            out.append(None)
    return out


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------

def _deciles(values: list[float]) -> list[float]:
    """Decile boundaries (10%, 20%, ..., 90%) of `values`, interpolated
    by statistics.quantiles (n=10). Returns an empty list for fewer
    than 1 value; the harness is robust to a class with a single
    sample. A class with zero samples is a stronger signal to the
    reviewer and is reported as an empty bucket at the caller."""
    if not values:
        return []
    if len(values) == 1:
        return [values[0]] * 9
    return statistics.quantiles(values, n=10, method="inclusive")


def _ascii_histogram(
    good: list[float],
    absent: list[float],
    *,
    bins: int = 20,
    width: int = 40,
) -> str:
    """Two overlapping ASCII histograms on the same [0, 1] axis. The
    reviewer's actual question is whether the two classes separate at
    all, so making overlap visible at a glance is the load-bearing
    deliverable here — central-tendency tables alone do not answer
    it. Bin counts are normalised within each class to a
    fraction-of-class so the histograms are comparable across
    differently-sized samples; absolute counts are appended next
    to each row so a class with N=2 is never mistaken for a
    class with N=200."""
    if not good and not absent:
        return "  (no data)"
    lo, hi = 0.0, 1.0
    step = (hi - lo) / bins
    edges = [lo + i * step for i in range(bins + 1)]

    def _hist(values: list[float]) -> list[int]:
        counts = [0] * bins
        for v in values:
            # Map v into one of `bins` buckets. The very last edge is
            # 1.0; values equal to 1.0 would otherwise fall off the
            # end, so clamp by index.
            idx = int((v - lo) / step)
            if idx == bins:
                idx = bins - 1
            counts[idx] += 1
        return counts

    g_counts = _hist(good)
    a_counts = _hist(absent)
    g_total = max(len(good), 1)
    a_total = max(len(absent), 1)

    rows: list[str] = []
    rows.append(
        f"  bin            good                 absent"
    )
    rows.append(
        f"  ---            ----                 ------"
    )
    for i, (gc, ac) in enumerate(zip(g_counts, a_counts)):
        g_frac = gc / g_total
        a_frac = ac / a_total
        g_bar = "#" * int(round(g_frac * width))
        a_bar = "#" * int(round(a_frac * width))
        # Two halves of the same row so a terminal reader sees both
        # classes at the same x. Counts on the right.
        rows.append(
            f"  [{edges[i]:.2f}-{edges[i+1]:.2f})  "
            f"{g_bar:<{width}}  {gc:>3}  |  "
            f"{a_bar:<{width}}  {ac:>3}"
        )
    return "\n".join(rows)


def _summarise(
    values: list[float],
) -> dict[str, Any]:
    """count / min / max / median / deciles — the standard set, with
    mean included for the central-tendency table even though
    median is the more robust summary for a [0, 1] bounded scale."""
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "median": None,
            "mean": None,
            "deciles": [],
        }
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "deciles": _deciles(values),
    }


# ---------------------------------------------------------------------------
# Vectr revision stamp
# ---------------------------------------------------------------------------

def _resolve_vectr_sha() -> str:
    """The vectr repo's HEAD SHA, abbreviated to 7 chars (the shortest
    git renders unambiguously). Used to stamp the output directory
    path the existing results/<repo>/<sha>/ convention expects.
    Never raises: a missing git binary or non-repo cwd degrades to
    the literal 'unknown' — the harness must still produce output,
    just with an honest stamp."""
    try:
        probe = subprocess.run(
            ["git", "-C", str(_BENCH_ROOT.parent), "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    if probe.returncode != 0:
        return "unknown"
    sha = (probe.stdout or "").strip()
    return sha or "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "UPG-BANNER-CALIBRATION Phase 3 distribution harness: "
            "measure ce_relevance distribution for known-good and "
            "known-absent classes on a single corpus."
        ),
    )
    p.add_argument("--port", type=int, default=8799, help="daemon port (default 8799)")
    p.add_argument("--host", default="http://localhost", help="daemon host (default localhost)")
    p.add_argument(
        "--corpus", required=True,
        help="corpus name to filter cases on (e.g. django, tigerbeetle, react, cpython, uv)",
    )
    p.add_argument(
        "--out-dir", type=Path, default=None,
        help="output root (default results/banner_calibration/<vectr-sha>/)",
    )
    p.add_argument(
        "--run-id", default=None,
        help="run id appended to the output filename (default utc timestamp)",
    )
    p.add_argument(
        "--max-known-good", type=int, default=0,
        help="cap on known-good cases run (0 = all). For a quick smoke.",
    )
    return p.parse_args(argv)


def _run_id_default() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = f"{args.host}:{args.port}"

    # Verify daemon reachable, capture the corpus's served shape.
    try:
        status = _get(base, "/v1/status")
    except Exception as exc:
        print(f"ERROR: cannot reach daemon at {base}: {exc}", file=sys.stderr)
        return 1

    print("=" * 78)
    print(f"UPG-BANNER-CALIBRATION Phase 3 distribution harness")
    print(f"Daemon:  {base}")
    print(f"Corpus:  {args.corpus}")
    print(f"  workspace_root: {status.get('workspace_root')}")
    print(f"  indexed_files:  {status.get('indexed_files')}")
    print(f"  total_chunks:   {status.get('total_chunks')}")
    print(f"  embed_model:    {status.get('embed_model')}")
    print("=" * 78)

    vectr_sha = _resolve_vectr_sha()
    run_id = args.run_id or _run_id_default()
    out_root = args.out_dir or (DEFAULT_RESULTS_ROOT / vectr_sha / args.corpus)
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{run_id}.json"
    hist_path = out_root / f"{run_id}.histogram.txt"

    # Load both classes of queries.
    good_cases = _load_known_good(args.corpus)
    absent_cases = _load_known_absent(args.corpus)
    if args.max_known_good > 0:
        good_cases = good_cases[: args.max_known_good]

    print(f"  known-good cases:   {len(good_cases)}")
    print(f"  known-absent cases: {len(absent_cases)}")

    # Run every query; collect per-class samples.
    good_rows: list[dict] = []
    absent_rows: list[dict] = []

    for case in good_cases:
        resp = _execute_query(base, case["query"], case.get("language"))
        if "_error" in resp:
            good_rows.append({
                "id": case.get("id"),
                "query": case["query"],
                "error": resp["_error"],
            })
            continue
        results = resp.get("results", [])
        scores = _scores_in_order(results)
        satisfied = _known_good_satisfied(case, results)
        good_rows.append({
            "id": case.get("id"),
            "query": case["query"],
            "expected_top_k_contains": case.get("expect", {}).get("top_k_contains"),
            "satisfied": satisfied,
            "rank1_score": scores[0] if len(scores) >= 1 else None,
            "rank2_score": scores[1] if len(scores) >= 2 else None,
            "scores_in_display_order": scores,
            "low_confidence": resp.get("low_confidence"),
        })

    for case in absent_cases:
        resp = _execute_query(base, case["query"], case.get("language"))
        if "_error" in resp:
            absent_rows.append({
                "id": case.get("id"),
                "query": case["query"],
                "error": resp["_error"],
            })
            continue
        results = resp.get("results", [])
        scores = _scores_in_order(results)
        absent_rows.append({
            "id": case.get("id"),
            "query": case["query"],
            "absent_reason": case.get("absent_reason"),
            "rank1_score": scores[0] if len(scores) >= 1 else None,
            "rank2_score": scores[1] if len(scores) >= 2 else None,
            "scores_in_display_order": scores,
            "low_confidence": resp.get("low_confidence"),
        })

    # Distributions — built from the rank-1 ce_relevance of each
    # satisfied known-good row and the rank-1 of every known-absent
    # row. Unsatisfied known-good rows are excluded from the
    # distribution (their expect.symbol did not land in the top-3
    # against this daemon; mixing them in would silently bias the
    # distribution toward whatever unrelated chunk happened to
    # land at the top), but they remain in the per-query rows so
    # the reviewer can see what happened.
    good_rank1: list[float] = [
        r["rank1_score"] for r in good_rows
        if r.get("satisfied") and r.get("rank1_score") is not None
    ]
    absent_rank1: list[float] = [
        r["rank1_score"] for r in absent_rows
        if r.get("rank1_score") is not None
    ]
    good_rank2: list[float] = [
        r["rank2_score"] for r in good_rows
        if r.get("satisfied") and r.get("rank2_score") is not None
    ]
    absent_rank2: list[float] = [
        r["rank2_score"] for r in absent_rows
        if r.get("rank2_score") is not None
    ]
    # Drop sizes: rank-1 minus rank-2 per query, the data the
    # relative-cliff hypothesis is tested on.
    good_drops: list[float] = [
        g - n for g, n in zip(good_rank1, good_rank2)
    ]
    absent_drops: list[float] = [
        g - n for g, n in zip(absent_rank1, absent_rank2)
    ]

    summary = {
        "vectr_sha": vectr_sha,
        "corpus": args.corpus,
        "run_id": run_id,
        "daemon": {
            "host": args.host,
            "port": args.port,
            "workspace_root": status.get("workspace_root"),
            "indexed_files": status.get("indexed_files"),
            "total_chunks": status.get("total_chunks"),
            "embed_model": status.get("embed_model"),
        },
        "config_snapshot": {
            "notfound_floor_enabled": _read_cfg("NOTFOUND_FLOOR_ENABLED"),
            "notfound_floor_min_top_relevance": _read_cfg("NOTFOUND_FLOOR_MIN_TOP_RELEVANCE"),
            "notfound_floor_ce_override_min_relevance": _read_cfg("NOTFOUND_FLOOR_CE_OVERRIDE_MIN_RELEVANCE"),
            "relative_cliff_enabled": _read_cfg("RELATIVE_CLIFF_ENABLED"),
            "relative_cliff_min_top": _read_cfg("RELATIVE_CLIFF_MIN_TOP"),
            "relative_cliff_min_drop": _read_cfg("RELATIVE_CLIFF_MIN_DROP"),
            "result_floor_enabled": _read_cfg("RESULT_FLOOR_ENABLED"),
            "result_floor_min_relevance": _read_cfg("RESULT_FLOOR_MIN_RELEVANCE"),
        },
        "n_results_requested": N_RESULTS,
        "known_good": {
            "n_cases": len(good_cases),
            "n_satisfied": sum(1 for r in good_rows if r.get("satisfied")),
            "n_rerank_scored": len(good_rank1),
            "rank1_summary": _summarise(good_rank1),
            "rank2_summary": _summarise(good_rank2),
            "rank1_minus_rank2_summary": _summarise(good_drops),
        },
        "known_absent": {
            "n_cases": len(absent_cases),
            "n_rerank_scored": len(absent_rank1),
            "rank1_summary": _summarise(absent_rank1),
            "rank2_summary": _summarise(absent_rank2),
            "rank1_minus_rank2_summary": _summarise(absent_drops),
        },
        "rows": {
            "known_good": good_rows,
            "known_absent": absent_rows,
        },
    }

    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(f"  wrote: {out_path}")

    # ASCII histogram (the visual overlap answer).
    hist_text = (
        f"=== UPG-BANNER-CALIBRATION Phase 3 distribution ===\n"
        f"corpus: {args.corpus}    vectr_sha: {vectr_sha}    run_id: {run_id}\n"
        f"\n"
        f"  known-good   rank-1: n={len(good_rank1)}  "
        f"min={min(good_rank1) if good_rank1 else 'n/a'}  "
        f"max={max(good_rank1) if good_rank1 else 'n/a'}  "
        f"median={statistics.median(good_rank1) if good_rank1 else 'n/a'}\n"
        f"  known-absent rank-1: n={len(absent_rank1)}  "
        f"min={min(absent_rank1) if absent_rank1 else 'n/a'}  "
        f"max={max(absent_rank1) if absent_rank1 else 'n/a'}  "
        f"median={statistics.median(absent_rank1) if absent_rank1 else 'n/a'}\n"
        f"\n"
        f"  rank-1 ce_relevance — known-good vs known-absent (overlapping):\n"
        f"{_ascii_histogram(good_rank1, absent_rank1)}\n"
        f"\n"
        f"  rank-1 minus rank-2 (the relative-cliff data) — known-good vs known-absent:\n"
        f"{_ascii_histogram(good_drops, absent_drops)}\n"
    )
    hist_path.write_text(hist_text)
    print(f"  wrote: {hist_path}")
    print("=" * 78)
    return 0


def _read_cfg(name: str) -> Any:
    """Read one named constant from agent.config, importing lazily so
    this harness stays importable in environments where vectr's
    full dependency stack is not on PYTHONPATH (e.g. an operator
    running it on a release artifact). Returns the literal string
    "<unavailable>" if the import fails — the harness output is
    honest about what it knows, not a fabrication."""
    try:
        from agent import config as _cfg
    except Exception as exc:
        return f"<unavailable: {type(exc).__name__}>"
    return getattr(_cfg, name, None)


if __name__ == "__main__":
    sys.exit(main())
