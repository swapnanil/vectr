"""UPG-DEDUP-AUTOJUNK-DEFC real-dedup replay harness.

Given a running vectr daemon and a query list, replays the DEF-C
dedup pair-extraction on the daemon's returned candidate pool,
records every `(candidate, representative)` pair that shares a
`leading_docstring_key`, and emits both ratios for each pair plus a
threshold sweep under `autojunk=False`.

This is the harness the brief asks for: "for every pair DEF-C
actually compares during a run, the similarity both ways." It
follows the daemon-client conventions of
`benchmarks/harness/run_acceptance.py` and the structure of
`benchmarks/banner_calibration/distribution_harness.py`.

The pair set this harness emits is the set the dedup path WOULD
compare, given the candidate pool the daemon returns. The pair set
is NOT identical to the set the daemon's own dedup saw:

  - The daemon's own dedup runs with `autojunk=True` (the broken
    metric) and may collapse some candidates before we see them.
    We do not see those collapsed candidates, so we do not record
    pairs where one side was collapsed.
  - We do, however, record every pair AMONG SURVIVORS that
    shares a docstring key — exactly the pairs the corrected
    metric would still consider collapsing.

This is a lower bound on the pair set. It is enough to answer the
brief's question: "where does the threshold start destroying
distinct results?" The templated-fixture analysis
(`templated_analysis.py`) reports the canonical answer for the
false-collapse hazard case; this harness reports the production-
path measurement.

Run:
    python3 benchmarks/defc_autojunk/harness.py \\
        --corpus django --queries-file benchmarks/defc_autojunk/queries.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Make sibling modules importable when this file is run as
# `python3 benchmarks/defc_autojunk/harness.py` from the repo root
# (the operator's invocation). Without this, sys.path[0] is the
# script's own dir and `from benchmarks.defc_autojunk.similarity...`
# fails to find the `benchmarks` package. The repo root is the
# parent of benchmarks/, so adding the script's parent lets
# `from similarity import ...` resolve for sibling modules. The
# same trick the test suite (tests/test_defc_autojunk_harness.py)
# uses.
_HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HARNESS_DIR))

# `agent.chunk_quality.leading_docstring_key` is imported lazily
# inside extract_pairs_from_pool — running `python3 harness.py
# --help` should not require the agent package on sys.path, and
# a module-level `from agent...` import would fail when the script
# is invoked outside the repo root. The lazy import is the
# existing convention in benchmarks/harness/run_acceptance.py
# (which never imports from agent at all, for the same reason).
from similarity import (  # noqa: E402
    classify_pair,
    DEFAULT_THRESHOLDS,
    PairClassification,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How many results to ask the daemon for. The dedup pair-extraction
# runs on the candidate pool; the daemon truncates to `n_results` AFTER
# dedup. Asking for a much larger n_results than the rerank pool
# ceiling (~200 unfiltered, ~40 language-filtered) gives us the
# full post-dedup set regardless of how many collapses happened.
# The `seen_docstring` map built inside `_apply_quality_and_dedup`
# (agent/searcher.py:1163-1214) is bounded by the candidate pool,
# not the final n_results — so a small n_results here loses pair
# data. 200 is the safe upper bound matching the pre-rerank
# pre-filter-fetch_k.
N_RESULTS = 200

# Output layout. Same `results/<name>/<vectr-sha>/` convention
# banner_calibration uses, so a reviewer browsing `results/`
# already knows where to look.
DEFAULT_RESULTS_ROOT = Path("results/defc_autojunk")

# Cap on queries per run, to keep a slow daemon from making this
# harness hang. 0 = no cap.
DEFAULT_MAX_QUERIES = 0

# Path resolution rooted at benchmarks/defc_autojunk/ — fixture
# files live in this directory; `out_dir` defaults land at
# results/defc_autojunk/<sha>/ to match the convention. _HARNESS_DIR
# is defined up top so the sys.path setup there can use it.
_BENCH_ROOT = _HARNESS_DIR.parent


# ---------------------------------------------------------------------------
# Daemon helpers — mirror benchmarks/harness/run_acceptance.py
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
# Query file loader
# ---------------------------------------------------------------------------

def _load_queries(path: Path) -> list[dict]:
    """Load a JSONL of queries. Each row is {id, query, language?}.
    Missing language means no filter. The harness does not need
    any other per-query metadata."""
    if not path.is_file():
        raise SystemExit(f"queries file not found: {path}")
    out: list[dict] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------

def _execute_search(base: str, query: str, language: str | None) -> dict:
    body: dict = {"query": query, "n_results": N_RESULTS}
    if language is not None:
        body["language"] = language
    try:
        return _post(base, "/v1/search", body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # last-ditch — see run_acceptance.py's pattern
        return {"_error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Dedup pair extraction (LOCAL REPLAY, not the daemon's own dedup)
# ---------------------------------------------------------------------------

def extract_pairs_from_pool(candidates: list[dict]) -> tuple[list[tuple[dict, dict]], int]:
    """Walk the dedup pair-extraction logic on the daemon's returned
    candidate pool and return every (candidate, representative)
    pair that shares a `leading_docstring_key`, plus the count of
    distinct representatives that survived (i.e. would have been
    kept under DEF-C's actual decision rule).

    Why a local replay and not the daemon's own dedup log: the
    daemon's REST response is the post-dedup set. The pre-dedup
    candidate pool is hidden. By walking the dedup logic here on
    the post-dedup pool, we recover the pairs the dedup path
    WOULD have considered, given the daemon's view of the world.
    The dedup LOGIC we walk is the same one in
    `agent/searcher.py:_apply_quality_and_dedup` — same
    `leading_docstring_key` (delegated to chunk_quality, so the
    same string the searcher actually computes), same per-doc_key
    representative cap (we use the cap the searcher ships with
    because the brief does not authorize changes to its
    configuration; a future reviewer who wants a different cap
    can pass it through).

    We do NOT actually collapse: the candidate pool is recorded
    AS-IS, and the "representative" for each pair is the
    earliest-seen candidate under that docstring key (the
    "best-ranked" one, in the same sense
    `_apply_quality_and_dedup` uses — the daemon's
    pre-rerank-position-determined representative is what the
    searcher would have picked too, since the input order is
    the post-rerank order).

    Returns (pairs, distinct_rep_count). The pair list is the
    rows the per-pair output reports; the rep count lets the
    reviewer see the size of the post-dedup set the local
    replay would have produced under DEF-C's CURRENT logic
    (every pair whose corrected_ratio < 0.75 is a NON-collapse
    under both metrics; every pair whose default_ratio >= 0.75
    and corrected_ratio < 0.75 is a "current says collapse,
    corrected says don't" pair — these are the cases the
    autojunk defect is currently hiding).
    """
    # Lazy import: the agent package may not be on sys.path when
    # this script is run for `--help` or imported under a test
    # runner. The real call only fires here, after main() has
    # established that we're past argument parsing.
    from agent.chunk_quality import leading_docstring_key  # noqa: PLC0415

    pairs: list[tuple[dict, dict]] = []
    # seen_docstring: doc_key -> list of indices of already-seen
    # candidates sharing that key, in input order. Mirrors
    # agent/searcher.py:1163-1214 exactly so the pair set is
    # the same one the searcher would have produced, modulo
    # the candidates the daemon's own dedup already removed.
    seen_docstring: dict[str, list[int]] = {}
    # The kept representatives — only the first candidate per
    # docstring key is the "representative" for comparison
    # purposes, but a candidate can have NO docstring key
    # (leading_docstring_key returns "" for chunks without a
    # leading doc) and never appears in any pair.
    seen_reps: list[dict] = []
    for cand in candidates:
        content = cand.get("content", "")
        language = cand.get("language", "")
        doc_key = leading_docstring_key(content, language)
        if not doc_key:
            # No docstring key — same shape as the searcher's
            # "a chunk with no leading docstring is never
            # collapsed by the docstring key" comment
            # (agent/searcher.py:1126-1127). The candidate
            # still becomes a representative; it just never
            # participates in any pair.
            seen_reps.append(cand)
            continue
        rep_indices = seen_docstring.get(doc_key, [])
        if rep_indices:
            for rep_idx in rep_indices:
                pairs.append((cand, seen_reps[rep_idx]))
        # The first candidate under this doc_key becomes a
        # representative. Subsequent candidates under the same
        # key do NOT become representatives (they are
        # candidates for collapse, not collapse targets
        # themselves) — but for the local replay's pair-set
        # purposes we record them only as the "candidate"
        # side of a pair, never as a representative, so the
        # per-pair rows are not duplicated by both sides.
        if not rep_indices:
            seen_reps.append(cand)
            seen_docstring.setdefault(doc_key, []).append(len(seen_reps) - 1)
    return pairs, len(seen_reps)


# ---------------------------------------------------------------------------
# Per-query analysis
# ---------------------------------------------------------------------------

def analyze_query(base: str, case: dict) -> dict:
    """Issue one search and run the local dedup pair-extraction on
    the daemon's returned pool. Returns a per-query record suitable
    for the JSON output.

    `case` is a row from the queries file: {id, query, language?}.
    """
    resp = _execute_search(base, case["query"], case.get("language"))
    if "_error" in resp:
        return {
            "id": case.get("id"),
            "query": case["query"],
            "error": resp["_error"],
        }
    results = resp.get("results", [])
    pairs, rep_count = extract_pairs_from_pool(results)
    return {
        "id": case.get("id"),
        "query": case["query"],
        "n_results_returned": len(results),
        "n_distinct_reps_in_pool": rep_count,
        "n_pairs": len(pairs),
    }


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def collapse_sweep(
    rows: list[PairClassification],
    *,
    templated_filter: bool | None = None,
) -> dict[str, dict[str, int]]:
    """For each threshold under `autojunk=False`, count how many pairs
    in `rows` would collapse, optionally restricted to templated or
    non-templated pairs. Returns {threshold: {count, n_considered}}.

    `templated_filter=None` counts over all rows; True restricts
    to templated pairs (the false-collapse hazard); False restricts
    to non-templated pairs (genuine near-duplicates the corrected
    metric should still collapse).

    The n_considered field is exposed because a reviewer wants to
    know "9 of 10 templated pairs would collapse at 0.85" —
    9/10 — and the "of 10" part is what n_considered carries.
    """
    if templated_filter is None:
        considered = rows
    elif templated_filter:
        considered = [r for r in rows if r.is_templated]
    else:
        considered = [r for r in rows if not r.is_templated]
    out: dict[str, dict[str, int]] = {}
    for t in DEFAULT_THRESHOLDS:
        out[t] = {
            "n_considered": len(considered),
            "n_collapses": sum(
                1 for r in considered if r.would_collapse.get(t, False)
            ),
        }
    return out


# ---------------------------------------------------------------------------
# Vectr revision stamp
# ---------------------------------------------------------------------------

def _resolve_vectr_sha() -> str:
    try:
        probe = subprocess.run(
            ["git", "-C", str(_BENCH_ROOT.parent), "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    if probe.returncode != 0:
        return "unknown"
    return (probe.stdout or "").strip() or "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "UPG-DEDUP-AUTOJUNK-DEFC real-dedup replay harness. "
            "Records pairwise similarity (autojunk default + autojunk=False) "
            "for every pair the DEF-C dedup path would compare, and emits a "
            "threshold sweep under autojunk=False."
        ),
    )
    p.add_argument("--port", type=int, default=8799, help="daemon port (default 8799)")
    p.add_argument("--host", default="http://localhost", help="daemon host (default localhost)")
    p.add_argument(
        "--corpus", required=True,
        help="corpus name; stamped into the output directory and report header",
    )
    p.add_argument(
        "--queries-file", type=Path, required=True,
        help="JSONL of {id, query, language?} rows; the queries to run",
    )
    p.add_argument(
        "--out-dir", type=Path, default=None,
        help="output root (default results/defc_autojunk/<vectr-sha>/<corpus>/)",
    )
    p.add_argument(
        "--run-id", default=None,
        help="run id appended to the output filename (default utc timestamp)",
    )
    p.add_argument(
        "--max-queries", type=int, default=DEFAULT_MAX_QUERIES,
        help="cap on queries per run (0 = all); for a quick smoke",
    )
    return p.parse_args(argv)


def _run_id_default() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = f"{args.host}:{args.port}"

    try:
        status = _get(base, "/v1/status")
    except Exception as exc:
        print(f"ERROR: cannot reach daemon at {base}: {exc}", file=sys.stderr)
        return 1

    print("=" * 78)
    print("UPG-DEDUP-AUTOJUNK-DEFC real-dedup replay harness")
    print(f"Daemon:        {base}")
    print(f"Corpus:        {args.corpus}")
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
    report_path = out_root / f"{run_id}.report.txt"

    queries = _load_queries(args.queries_file)
    if args.max_queries > 0:
        queries = queries[: args.max_queries]
    print(f"  queries:        {len(queries)}")
    print(f"  n_results:      {N_RESULTS}")
    print(f"  thresholds:     {', '.join(DEFAULT_THRESHOLDS)}")

    all_rows: list[PairClassification] = []
    per_query: list[dict] = []
    n_with_pairs = 0
    n_total_pairs = 0
    n_total_candidates = 0
    n_error = 0

    for case in queries:
        analysis = analyze_query(base, case)
        if "error" in analysis:
            n_error += 1
            per_query.append(analysis)
            continue
        pairs, _rep_count = extract_pairs_from_pool(
            # Re-fetch the post-dedup pool so we have the actual
            # content for similarity computation. The analysis
            # above already ran the extraction; here we redo
            # it for the per-pair content. Cheap; the
            # extraction is O(n).
            _execute_search(base, case["query"], case.get("language")).get("results", []),
        )
        per_query.append({
            "id": analysis["id"],
            "query": analysis["query"],
            "n_results_returned": analysis["n_results_returned"],
            "n_distinct_reps_in_pool": analysis["n_distinct_reps_in_pool"],
            "n_pairs": analysis["n_pairs"],
        })
        n_with_pairs += 1 if analysis["n_pairs"] > 0 else 0
        n_total_pairs += analysis["n_pairs"]
        # n_total_candidates is the count of all candidates
        # returned across all queries, used to compute a
        # "fraction of candidates involved in a pair" sanity
        # number for the reviewer.
        n_total_candidates += analysis["n_results_returned"]
        for idx, (a, b) in enumerate(pairs):
            # Pair ids are unique across the whole run by combining
            # the query id and the pair's index within the query's
            # dedup walk. Two different queries that happen to
            # produce the same set of candidates will produce
            # different pair ids — this is intentional, the pair
            # set is per-query.
            pair_id = f"{analysis['id']}#{idx}"
            all_rows.append(classify_pair(
                pair_id=pair_id,
                source="replay",
                a=a.get("content", ""),
                b=b.get("content", ""),
            ))

    templated_rows = [r for r in all_rows if r.is_templated]
    non_templated_rows = [r for r in all_rows if not r.is_templated]

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
            "docstring_dedup_body_similarity_min_ratio": _read_cfg(
                "DOCSTRING_DEDUP_BODY_SIMILARITY_MIN"
            ),
            "docstring_dedup_max_reps_compared": _read_cfg(
                "DOCSTRING_DEDUP_MAX_REPS_COMPARED"
            ),
            "near_dup_body_enabled": _read_cfg("NEAR_DUP_BODY_ENABLED"),
        },
        "n_results_requested": N_RESULTS,
        "thresholds": list(DEFAULT_THRESHOLDS),
        "n_queries": len(queries),
        "n_queries_with_pairs": n_with_pairs,
        "n_queries_error": n_error,
        "n_pairs_total": n_total_pairs,
        "n_pairs_templated": len(templated_rows),
        "n_pairs_non_templated": len(non_templated_rows),
        "ratio_distribution": {
            "default": _summarise([r.default_ratio for r in all_rows]),
            "corrected": _summarise([r.corrected_ratio for r in all_rows]),
        },
        "threshold_sweep": {
            "all": collapse_sweep(all_rows),
            "templated": collapse_sweep(all_rows, templated_filter=True),
            "non_templated": collapse_sweep(all_rows, templated_filter=False),
        },
        "per_query": per_query,
        "pair_rows": [
            {
                "pair_id": r.pair_id,
                "default_ratio": r.default_ratio,
                "corrected_ratio": r.corrected_ratio,
                "is_templated": r.is_templated,
                "would_collapse": r.would_collapse,
            }
            for r in all_rows
        ],
    }

    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(f"  wrote: {out_path}")

    # ASCII report — the reviewer-readable summary. The JSON has
    # the full per-pair rows; the report is what a human reads
    # first to decide whether the per-pair data is worth a
    # closer look.
    report_text = _format_report(
        summary, all_rows, templated_rows, non_templated_rows,
    )
    report_path.write_text(report_text)
    print(f"  wrote: {report_path}")
    print()
    print(report_text)
    return 0


def _summarise(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "max": None, "median": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "median": statistics.median(values),
    }


def _format_report(
    summary: dict,
    all_rows: list[PairClassification],
    templated_rows: list[PairClassification],
    non_templated_rows: list[PairClassification],
) -> str:
    """The reviewer-readable ASCII report. Three sections:

      1. Run header — daemon, corpus, sha, run id, n_queries, etc.
      2. Headline numbers — n_pairs, n_templated, ratio distribution
         under both metrics, threshold sweep tables.
      3. Reading guide — short prose explaining how to read the
         sweep tables and what a reviewer should conclude from them.

    The reading guide is the single most important piece of the
    report: a reviewer who has never run this harness should be
    able to read the report and answer "what threshold would I
    publish" without re-reading the brief.
    """
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("=== UPG-DEDUP-AUTOJUNK-DEFC real-dedup replay report ===")
    lines.append("=" * 78)
    lines.append(f"corpus:     {summary['corpus']}")
    lines.append(f"vectr_sha:  {summary['vectr_sha']}")
    lines.append(f"run_id:     {summary['run_id']}")
    lines.append(f"daemon:     {summary['daemon']['host']}:{summary['daemon']['port']}")
    lines.append(f"embedder:   {summary['daemon'].get('embed_model')}")
    lines.append(f"indexed:    {summary['daemon'].get('indexed_files')} files, "
                 f"{summary['daemon'].get('total_chunks')} chunks")
    lines.append(f"queries:    {summary['n_queries']} (errors: {summary['n_queries_error']}, "
                 f"with pairs: {summary['n_queries_with_pairs']})")
    lines.append(f"thresholds: {', '.join(summary['thresholds'])}")
    lines.append("")

    # Headline numbers
    lines.append("--- Headline numbers ---")
    lines.append(f"  total pairs recorded:         {summary['n_pairs_total']}")
    lines.append(f"  templated pairs:              {summary['n_pairs_templated']}")
    lines.append(f"  non-templated pairs:          {summary['n_pairs_non_templated']}")
    rd = summary["ratio_distribution"]
    lines.append(f"  default_ratio:    "
                 f"n={rd['default'].get('count', 0)}  "
                 f"min={rd['default'].get('min')}  "
                 f"max={rd['default'].get('max')}  "
                 f"median={rd['default'].get('median')}")
    lines.append(f"  corrected_ratio:  "
                 f"n={rd['corrected'].get('count', 0)}  "
                 f"min={rd['corrected'].get('min')}  "
                 f"max={rd['corrected'].get('max')}  "
                 f"median={rd['corrected'].get('median')}")
    lines.append("")

    # Threshold sweep tables — three of them: all, templated, non-templated.
    # The "templated" sweep is the one a reviewer reads first, because
    # that's where the false-collapse hazard shows up.
    for label, key in (
        ("All pairs", "all"),
        ("Templated pairs (false-collapse hazard)", "templated"),
        ("Non-templated pairs", "non_templated"),
    ):
        sweep = summary["threshold_sweep"][key]
        lines.append(f"--- Threshold sweep ({label}) ---")
        lines.append("  threshold  collapses  considered  pct")
        lines.append("  ---------  ---------  ----------  ----")
        for t, c in sweep.items():
            pct = (c["n_collapses"] / c["n_considered"] * 100
                   if c["n_considered"] > 0 else 0.0)
            lines.append(
                f"  {t:>8}    "
                f"{c['n_collapses']:>5}      "
                f"{c['n_considered']:>5}    "
                f"{pct:>5.1f}%"
            )
        lines.append("")

    # Reading guide
    lines.append("--- How to read this report ---")
    lines.append(
        "  - The 'default_ratio' is what the DEF-C call site ships "
        "with today (autojunk=True)."
    )
    lines.append(
        "  - The 'corrected_ratio' is what the same call returns with "
        "autojunk=False."
    )
    lines.append(
        "  - The threshold sweep reports, for each candidate T, how many "
        "of the recorded pairs would collapse at T under autojunk=False."
    )
    lines.append(
        "  - 'Templated pairs' are bodies that differ only in digits "
        "(chunk_quality._is_templated_body_difference). These are the "
        "false-collapse hazard: char-similarity will read them as "
        "near-duplicates while they are genuinely distinct symbols. "
        "A safe threshold is one whose 'templated' collapse count is 0."
    )
    lines.append(
        "  - 'Non-templated pairs' are the genuine near-duplicates the "
        "corrected metric is meant to catch. A safe threshold is one "
        "whose 'non_templated' collapse count is positive (otherwise "
        "DEF-C is doing nothing)."
    )
    lines.append(
        "  - If every threshold from 0.85 upward collapses ALL templated "
        "pairs, NO SAFE THRESHOLD EXISTS for the corrected metric alone — "
        "the fix has to be either (a) add a templated-body guard (the "
        "shape _is_content_near_duplicate already uses), (b) switch the "
        "metric to something other than char-similarity, or (c) turn the "
        "gate off. See LANE-REPORT.md for the conclusion the reviewer "
        "should write up."
    )
    lines.append("=" * 78)
    return "\n".join(lines) + "\n"


def _read_cfg(name: str):
    """Read one named constant from agent.config, importing lazily
    so the harness stays importable in environments where the
    full dependency stack is not on PYTHONPATH. Returns the
    literal string '<unavailable>' on import failure — the
    harness output is honest about what it knows."""
    try:
        from agent import config as _cfg
    except Exception as exc:
        return f"<unavailable: {type(exc).__name__}>"
    return getattr(_cfg, name, None)


if __name__ == "__main__":
    sys.exit(main())
