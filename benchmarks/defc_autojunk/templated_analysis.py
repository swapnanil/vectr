"""Offline analysis of the templated-code witness fixture.

For each pair in `templated_pairs.jsonl`, computes the two ratios
(`autojunk` default + `autojunk=False`) and the per-threshold
collapse map under `autojunk=False`, then emits a reviewer-readable
report and a JSON dump. Pure-Python; no daemon, no network.

This is the small committed fixture the brief asks for ("a small
committed fixture of such pairs; keep it honest and say how you
chose them" — see README.md next to the JSONL). The numbers this
script emits are the canonical "what does the corrected metric
actually say about templated bodies" measurement; the real-dedup
replay (`defc_harness.py`) reports its templated subset in the same
per-pair shape so a reviewer can compare rows directly.

Run:
    python3 benchmarks/defc_autojunk/templated_analysis.py \
        --out-dir results/defc_autojunk/<vectr-sha>
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

# Make sibling modules importable when this file is run as
# `python3 benchmarks/defc_autojunk/templated_analysis.py` from
# the repo root (the operator's invocation). Without this,
# sys.path[0] is the script's own dir and the absolute
# `from benchmarks.defc_autojunk.similarity...` import fails to
# find the `benchmarks` package. The repo root is the parent of
# benchmarks/, so adding the script's parent lets
# `from similarity import ...` resolve for sibling modules. The
# same trick the test suite (tests/test_defc_autojunk_harness.py)
# uses, lifted into the script for standalone use.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from similarity import (  # noqa: E402
    classify_pair,
    DEFAULT_THRESHOLDS,
    PairClassification,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "templated_pairs.jsonl"
_BENCH_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RESULTS_ROOT = Path("results/defc_autojunk")


def _load_pairs(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"fixture not found: {path}")
    out: list[dict] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


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


def _format_table(rows: list[PairClassification]) -> str:
    """A reviewer-readable table: per-pair id, the two ratios, the
    templated flag, and the threshold band under which the pair
    starts collapsing. ASCII only, so it reads in any terminal.
    """
    out: list[str] = []
    out.append("  id    default  corrected  templated  collapses-under-autojunk-False")
    out.append("  --    -------  ---------  ---------  ------------------------------")
    for r in rows:
        cts = ",".join(r.collapsing_thresholds()) or "(none above 0.75)"
        out.append(
            f"  {r.pair_id:<4}  "
            f"{r.default_ratio:>6.3f}    "
            f"{r.corrected_ratio:>7.3f}    "
            f"{'Y' if r.is_templated else 'N':<9}  "
            f"{cts}"
        )
    return "\n".join(out)


def _summary(rows: list[PairClassification]) -> dict:
    """The headline numbers a reviewer reads first. min/max/median of
    each ratio, and per-threshold collapse count (so a reviewer
    sees, e.g., "9 of 10 pairs would collapse at 0.85" at a
    glance). The collapse count is broken out by templated /
    non-templated for the same reason the per-pair rows are:
    the templated subset is the one the reviewer has to defend
    against.
    """
    if not rows:
        return {"n_pairs": 0}
    defaults = [r.default_ratio for r in rows]
    corrected = [r.corrected_ratio for r in rows]
    templated_rows = [r for r in rows if r.is_templated]
    non_templated_rows = [r for r in rows if not r.is_templated]
    threshold_counts: dict[str, dict[str, int]] = {}
    for t in DEFAULT_THRESHOLDS:
        threshold_counts[t] = {
            "all": sum(1 for r in rows if r.would_collapse.get(t, False)),
            "templated": sum(
                1 for r in templated_rows if r.would_collapse.get(t, False)
            ),
            "non_templated": sum(
                1 for r in non_templated_rows if r.would_collapse.get(t, False)
            ),
        }
    return {
        "n_pairs": len(rows),
        "n_templated": len(templated_rows),
        "n_non_templated": len(non_templated_rows),
        "default_ratio": {
            "min": min(defaults),
            "max": max(defaults),
            "median": statistics.median(defaults),
        },
        "corrected_ratio": {
            "min": min(corrected),
            "max": max(corrected),
            "median": statistics.median(corrected),
        },
        "threshold_collapse_counts": threshold_counts,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Templated-code witness analysis for the DEF-C autojunk "
            "harness. Pure-Python; no daemon required."
        ),
    )
    p.add_argument(
        "--fixture", type=Path, default=_FIXTURE_PATH,
        help="path to templated_pairs.jsonl",
    )
    p.add_argument(
        "--out-dir", type=Path, default=None,
        help="output directory (default results/defc_autojunk/<vectr-sha>/)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pairs = _load_pairs(args.fixture)

    sha = _resolve_vectr_sha()
    out_root = args.out_dir or (_DEFAULT_RESULTS_ROOT / sha)
    out_root.mkdir(parents=True, exist_ok=True)
    out_json = out_root / "templated_analysis.json"
    out_txt = out_root / "templated_analysis.txt"

    rows: list[PairClassification] = []
    for p in pairs:
        rows.append(classify_pair(
            pair_id=p["id"],
            source="templated",
            a=p["a"],
            b=p["b"],
        ))

    summary = _summary(rows)
    summary["fixture_path"] = str(args.fixture)
    summary["vectr_sha"] = sha
    summary["thresholds"] = list(DEFAULT_THRESHOLDS)

    out_json.write_text(json.dumps({
        "summary": summary,
        "rows": [
            {
                "pair_id": r.pair_id,
                "shape": next(
                    (p.get("shape", "") for p in pairs if p["id"] == r.pair_id),
                    "",
                ),
                "default_ratio": r.default_ratio,
                "corrected_ratio": r.corrected_ratio,
                "is_templated": r.is_templated,
                "would_collapse": r.would_collapse,
            }
            for r in rows
        ],
    }, indent=2, sort_keys=True))
    print(f"  wrote: {out_json}")

    table = _format_table(rows)
    out_txt.write_text(
        "=== DEF-C autojunk harness — templated-code witness analysis ===\n"
        f"vectr_sha: {sha}\n"
        f"fixture:   {args.fixture}\n"
        f"thresholds: {', '.join(DEFAULT_THRESHOLDS)}\n"
        "\n"
        "Pair-level table (per-pair id, both ratios, templated flag, "
        "thresholds under autojunk=False that would collapse):\n"
        "\n"
        f"{table}\n"
        "\n"
        f"summary:\n"
        f"  n_pairs:           {summary['n_pairs']}\n"
        f"  n_templated:       {summary['n_templated']}\n"
        f"  n_non_templated:   {summary['n_non_templated']}\n"
        f"  default_ratio:     "
        f"min={summary['default_ratio']['min']:.3f}  "
        f"max={summary['default_ratio']['max']:.3f}  "
        f"median={summary['default_ratio']['median']:.3f}\n"
        f"  corrected_ratio:   "
        f"min={summary['corrected_ratio']['min']:.3f}  "
        f"max={summary['corrected_ratio']['max']:.3f}  "
        f"median={summary['corrected_ratio']['median']:.3f}\n"
        f"\n"
        f"per-threshold collapse counts under autojunk=False:\n"
        f"  threshold  all  templated  non_templated\n"
        f"  ---------  ---  ---------  -------------\n"
        + "\n".join(
            f"  {t:>8}  "
            f"{threshold_counts['all']:>3}  "
            f"{threshold_counts['templated']:>9}  "
            f"{threshold_counts['non_templated']:>13}"
            for t, threshold_counts in summary["threshold_collapse_counts"].items()
        )
        + "\n"
    )
    print(f"  wrote: {out_txt}")
    print()
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
