#!/usr/bin/env python3
"""EVAL-LONGITUDINAL-REDISCOVERY -- report layer (DESIGN.md section 11, step 5).

Reads `<runs-dir>/results.jsonl` (one line per k>=2 leg, appended by `run_leg.py`'s
`write()`) and `<runs-dir>/_shared/leg1/<scenario>-s<seed>/result.json` (the shared
leg 1 that IS every arm's own leg 1, DESIGN.md 5.2), and turns them into the
per-scenario tables DESIGN.md 11 step 5 requires: `mistake_rate_post` /
`mistake_repetition_rate` per *k*, RDC absolute + cross-arm delta (6.4), with
censored and invalid counts printed adjacent to every aggregate -- never a bare
mean. `schema/leg_result.schema.json` and `schema/trajectory_state.schema.json` are
the normative contracts; this module reads only the fields they declare.

Everything below `build_report` is a pure function over already-loaded records, so
the discrimination tests in `tests/test_longitudinal_report.py` construct leg
records by hand (mirroring `tests/test_longitudinal_scorer.py`'s fixture style)
rather than requiring a real run -- no live leg has run against this branch yet
(T1 is sentinel-approval-gated; T0 is probe-only, zero legs).

One DESIGN.md ambiguity resolved with the narrowest reading (documented here, not
re-derived): 6.5 says `mistake_rate_post`/`mistake_repetition_rate` are "reported,
per k as well as aggregated, so a decay across k... is visible rather than averaged
away." This is implemented at the TRAJECTORY level only -- each trajectory's own
`legs` list already carries `mistake_committed` for each individual k (satisfying
"per k"), alongside the one aggregate fraction across that trajectory's own k=2..N
(satisfying "aggregated"). No further cross-trajectory-at-matching-k aggregation is
added; DESIGN.md's own text is scoped to a single trajectory's k-sequence ("For a
trajectory..." opens 6.4, and 6.5 continues the same subject), and multi-seed
aggregation is T7 replication's concern, not this module's.

Two exclusion/null rules already stated elsewhere are applied here without further
interpretation, since a report that silently violated either would misrepresent
every number it produces:
  - leg_result.schema.json: `valid:false` legs are EXCLUDED from every number (never
    read as "memory did not help") -- they are counted (`invalid_count`) but never
    enter an RDC or mistake-rate aggregate.
  - DESIGN.md 6.4: a censored leg (`a is None`, fact never demonstrably acquired)
    has every RDC component `null` and is excluded from RDC means, never imputed
    from the session total -- counted (`censored_count`) but not averaged in.

RDC(1) reference resolution (6.4): DISCOVERED scenarios use the shared leg-1
result's own `metrics` vector (`rdc_reference:"shared_leg1"`); TOLD scenarios use an
all-zero reference (`rdc_reference:"told_prompt"`, per 6.4's "RDC(1) = 0 by
construction... never ratios against zero" -- `rdc_ratio`'s zero-denominator guard
alone already makes every ratio component `null` for these, so no separate branch
is needed for the "no ratios" rule itself). A missing or itself-censored shared leg
1 degrades to an all-`null` reference (`rdc_reference:"leg1_missing"` /
`"leg1_censored"`) rather than raising or imputing -- the same no-imputation
principle 6.4 states for ordinary censored legs, extended to the reference leg.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
# See run_plan.py / run_leg.py's own comment: benchmarks/injection_utility/ ships a
# same-named scenarios.py, so a bare `import scenarios` races for one sys.modules
# slot. Loading by file path under this fixed, cache-checked key converges every
# caller on ONE module object.
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, _THIS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(_LONGITUDINAL_SCENARIOS_KEY, "scenarios.py")

# DESIGN.md 6.4's RDC(k) vector, in the order 6.4 lists them.
RDC_COMPONENTS = (
    "turns_to_fact", "tool_calls_to_fact", "billable_tokens_to_fact",
    "rediscovery_actions", "usd_to_fact_alloc",
)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_results_jsonl(runs_dir: Path) -> list[dict]:
    path = Path(runs_dir) / "results.jsonl"
    if not path.is_file():
        return []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def load_leg1_reference(runs_dir: Path, scenario: str, seed: int) -> dict | None:
    path = Path(runs_dir) / "_shared" / "leg1" / f"{scenario}-s{seed}" / "result.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def group_by_trajectory(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(r["trajectory_id"], []).append(r)
    for legs in out.values():
        legs.sort(key=lambda r: r["k"])
    return out


# ---------------------------------------------------------------------------
# RDC (6.4)
# ---------------------------------------------------------------------------


def rdc_vector(metrics: dict) -> dict[str, Any]:
    return {c: metrics.get(c) for c in RDC_COMPONENTS}


def rdc_ratio(rdc_k: dict, rdc_1: dict) -> dict[str, float | None]:
    """RDC(k) / RDC(1) per component, null where the leg-1 component is 0 or
    unknown (6.4). This single zero-denominator guard is also what makes every
    ratio null for TOLD scenarios (rdc_1 is the all-zero reference) without a
    separate branch.
    """
    ratios: dict[str, float | None] = {}
    for c in RDC_COMPONENTS:
        v_k, v_1 = rdc_k.get(c), rdc_1.get(c)
        ratios[c] = (v_k / v_1) if (v_k is not None and v_1) else None
    return ratios


def rdc_delta(rdc_a: dict, rdc_x: dict) -> dict[str, float | None]:
    """RDC_A(k) - RDC_X(k) per component (6.4's headline: turns/tool-calls/tokens/
    dollars SAVED per repeat session by the memory arm relative to no-memory).
    """
    return {
        c: (rdc_a[c] - rdc_x[c]) if (rdc_a.get(c) is not None and rdc_x.get(c) is not None) else None
        for c in RDC_COMPONENTS
    }


def leg1_rdc_reference(scenario_origin: str, leg1_record: dict | None) -> tuple[dict[str, Any], str]:
    if scenario_origin == "told":
        return {c: 0 for c in RDC_COMPONENTS}, "told_prompt"
    if leg1_record is None:
        return {c: None for c in RDC_COMPONENTS}, "leg1_missing"
    leg1_metrics = leg1_record.get("metrics") or {}
    if leg1_metrics.get("censored"):
        return {c: None for c in RDC_COMPONENTS}, "leg1_censored"
    return rdc_vector(leg1_metrics), "shared_leg1"


# ---------------------------------------------------------------------------
# mistake-repetition rate (6.5)
# ---------------------------------------------------------------------------


def mistake_rate_aggregate(
    per_k_committed: dict[int, bool], leg1_mistake_committed: bool | None
) -> dict[str, Any]:
    """6.5: mistake_rate_post is always defined over the legs actually counted
    (invalid/censored-out legs are excluded upstream by the caller, so
    `per_k_committed` already holds only the legs that count); mistake_repetition_rate
    is the identical fraction but null unless leg 1 itself committed the error --
    the expected state for TOLD scenarios, where the user has just stated the fact.
    """
    n = len(per_k_committed)
    rate_post = (sum(1 for v in per_k_committed.values() if v) / n) if n else None
    repetition_rate = rate_post if leg1_mistake_committed is True else None
    return {
        "mistake_rate_post": rate_post,
        "mistake_repetition_rate": repetition_rate,
        "n_legs_counted": n,
    }


# ---------------------------------------------------------------------------
# per-trajectory report
# ---------------------------------------------------------------------------


def trajectory_report(
    trajectory_id: str, legs: list[dict], leg1_record: dict | None, scenario_origin: str
) -> dict[str, Any]:
    rdc_1, rdc_reference = leg1_rdc_reference(scenario_origin, leg1_record)
    leg1_mistake_committed = None
    if leg1_record is not None:
        leg1_mistake_committed = (leg1_record.get("metrics") or {}).get("mistake_committed")

    per_leg_rows: list[dict[str, Any]] = []
    per_k_committed: dict[int, bool] = {}
    censored_count = 0
    invalid_count = 0

    for rec in legs:
        k = rec["k"]
        valid = rec.get("valid")
        if valid is False:
            invalid_count += 1
            per_leg_rows.append({"k": k, "valid": False, "excluded_reason": "invalid"})
            continue  # leg_result.schema.json: excluded from every number

        metrics = rec.get("metrics") or {}
        censored = bool(metrics.get("censored"))
        if censored:
            censored_count += 1

        rdc_k = rdc_vector(metrics)
        row = {
            "k": k,
            "valid": valid,
            "censored": censored,
            "mistake_committed": metrics.get("mistake_committed"),
            "self_corrected": metrics.get("self_corrected"),
            "rdc": rdc_k,
            "rdc_ratio": rdc_ratio(rdc_k, rdc_1),
            "session_usd": (rec.get("cost") or {}).get("session_usd"),
        }
        per_leg_rows.append(row)

        mistake_committed = metrics.get("mistake_committed")
        if mistake_committed is not None:
            # mistake_committed is independent of censoring (censoring is about fact
            # acquisition, not the mistake signature) -- a censored leg still counts
            # toward the mistake metrics, per 6.4's "censored legs still count in
            # full toward the mistake metrics".
            per_k_committed[k] = mistake_committed

    rates = mistake_rate_aggregate(per_k_committed, leg1_mistake_committed)
    scenario = legs[0]["scenario"] if legs else None
    arm = legs[0]["arm"] if legs else None
    note_variant = legs[0]["note_variant"] if legs else None
    seed = legs[0]["seed"] if legs else None

    return {
        "trajectory_id": trajectory_id,
        "scenario": scenario,
        "arm": arm,
        "note_variant": note_variant,
        "seed": seed,
        "rdc_reference": rdc_reference,
        "rdc_1": rdc_1,
        "leg1_mistake_committed": leg1_mistake_committed,
        "legs": per_leg_rows,
        "censored_count": censored_count,
        "invalid_count": invalid_count,
        **rates,
    }


# ---------------------------------------------------------------------------
# cross-arm delta (6.4's headline)
# ---------------------------------------------------------------------------


def cross_arm_delta(arm_a_report: dict, arm_x_report: dict) -> list[dict[str, Any]]:
    """RDC_A(k) - RDC_X(k) at matched k (6.4), for two trajectory_report() outputs
    sharing the same (scenario, seed). Only legs present, valid, and non-excluded in
    BOTH trajectories at a given k are compared.
    """
    a_by_k = {row["k"]: row for row in arm_a_report["legs"] if "rdc" in row}
    out = []
    for row in arm_x_report["legs"]:
        if "rdc" not in row:
            continue
        k = row["k"]
        a_row = a_by_k.get(k)
        if a_row is None:
            continue
        usd_a, usd_x = a_row.get("session_usd"), row.get("session_usd")
        out.append({
            "k": k,
            "rdc_delta_a_minus_x": rdc_delta(a_row["rdc"], row["rdc"]),
            "session_usd_delta_a_minus_x": (usd_a - usd_x) if (usd_a is not None and usd_x is not None) else None,
        })
    return out


# ---------------------------------------------------------------------------
# per-scenario table
# ---------------------------------------------------------------------------


def scenario_report(runs_dir: Path, scenario_slug: str, *, seed: int | None = None) -> dict[str, Any]:
    scenario = scen.SCENARIOS[scenario_slug]
    records = [
        r for r in load_results_jsonl(runs_dir)
        if r.get("scenario") == scenario_slug and (seed is None or r.get("seed") == seed)
    ]
    trajectories: dict[str, dict[str, Any]] = {}
    for traj_id, legs in group_by_trajectory(records).items():
        traj_seed = legs[0]["seed"]
        leg1_record = load_leg1_reference(runs_dir, scenario_slug, traj_seed)
        trajectories[traj_id] = trajectory_report(traj_id, legs, leg1_record, scenario.origin)

    # Cross-arm deltas: every memory-arm trajectory against arm "none" at the same
    # seed (6.4: "matched (scenario, k, seed)").
    none_by_seed = {t["seed"]: t for t in trajectories.values() if t["arm"] == "none"}
    deltas: dict[str, list[dict[str, Any]]] = {}
    for traj_id, t in trajectories.items():
        if t["arm"] == "none":
            continue
        a_report = none_by_seed.get(t["seed"])
        if a_report is not None:
            deltas[traj_id] = cross_arm_delta(a_report, t)

    return {
        "scenario": scenario_slug,
        "origin": scenario.origin,
        "trajectories": trajectories,
        "cross_arm_delta_vs_none": deltas,
    }


def build_report(runs_dir: Path, scenarios: list[str] | None = None) -> dict[str, Any]:
    slugs = scenarios if scenarios is not None else sorted(scen.SCENARIOS)
    return {slug: scenario_report(runs_dir, slug) for slug in slugs}


# ---------------------------------------------------------------------------
# human-readable rendering
# ---------------------------------------------------------------------------


def format_human_readable(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for slug, sc in report.items():
        lines.append("=" * 72)
        lines.append(f"scenario: {slug}  ({sc['origin']})")
        for traj_id, t in sorted(sc["trajectories"].items()):
            lines.append(
                f"  [{t['arm']}/{t['note_variant']}/s{t['seed']}]  "
                f"mistake_rate_post={t['mistake_rate_post']}  "
                f"mistake_repetition_rate={t['mistake_repetition_rate']}  "
                f"(censored={t['censored_count']}, invalid={t['invalid_count']}, "
                f"n_counted={t['n_legs_counted']})"
            )
            for row in t["legs"]:
                if row.get("excluded_reason"):
                    lines.append(f"      k={row['k']}  EXCLUDED ({row['excluded_reason']})")
                    continue
                lines.append(
                    f"      k={row['k']}  mistake={row['mistake_committed']}  "
                    f"censored={row['censored']}  turns_to_fact={row['rdc'].get('turns_to_fact')}  "
                    f"usd={row['session_usd']}"
                )
        for traj_id, delta_rows in sc["cross_arm_delta_vs_none"].items():
            lines.append(f"  delta (none - {traj_id}):")
            for d in delta_rows:
                lines.append(f"      k={d['k']}  {d['rdc_delta_a_minus_x']}  usd_delta={d['session_usd_delta_a_minus_x']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--scenario", action="append", default=None, help="Restrict to one or more scenario slugs; default all six.")
    ap.add_argument("--human", action="store_true", help="Print a readable table instead of JSON.")
    return ap


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()
    report = build_report(Path(args.runs_dir), scenarios=args.scenario)
    if args.human:
        print(format_human_readable(report))
    else:
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
