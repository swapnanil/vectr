"""Tests for the longitudinal-rediscovery report layer (DESIGN.md section 11, step 5
-- `benchmarks/longitudinal_rediscovery/report.py`).

No live leg has run against this branch (T1 is sentinel-approval-gated; T0 is
probe-only, zero legs), so every test here builds leg records by hand -- the same
fixture style `tests/test_longitudinal_scorer.py` uses for the scorer -- rather than
depending on a real `results.jsonl`.

`mistake_repetition_rate is null when leg 1 is clean` (DESIGN.md 6.5) is a
TRAJECTORY-level aggregate that `tests/test_longitudinal_scorer.py`'s own docstring
explicitly defers to this file (see its lines on `weak_prior` vs this metric); it is
covered below by `test_mistake_repetition_rate_is_null_when_leg1_is_clean` and its
converse `test_mistake_repetition_rate_equals_rate_post_when_leg1_committed_mistake`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "longitudinal_rediscovery"
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, BENCH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(_LONGITUDINAL_SCENARIOS_KEY, "scenarios.py")
report = _load_local_module("_vectr_eval_longitudinal_report_test", "report.py")


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------


def _metrics(
    *,
    censored: bool = False,
    mistake_committed: bool | None = False,
    self_corrected: bool = False,
    turns_to_fact: int | None = 3,
    tool_calls_to_fact: int | None = 4,
    billable_tokens_to_fact: float | None = 500.0,
    rediscovery_actions: int = 2,
    usd_to_fact_alloc: float | None = 0.05,
) -> dict[str, Any]:
    if censored:
        return {
            "censored": True, "censor_reason": "fact never acquired",
            "mistake_committed": mistake_committed, "self_corrected": self_corrected,
            "turns_to_fact": None, "tool_calls_to_fact": None,
            "billable_tokens_to_fact": None, "rediscovery_actions": rediscovery_actions,
            "usd_to_fact_alloc": None,
        }
    return {
        "censored": False, "censor_reason": None,
        "mistake_committed": mistake_committed, "self_corrected": self_corrected,
        "turns_to_fact": turns_to_fact, "tool_calls_to_fact": tool_calls_to_fact,
        "billable_tokens_to_fact": billable_tokens_to_fact,
        "rediscovery_actions": rediscovery_actions, "usd_to_fact_alloc": usd_to_fact_alloc,
    }


def _leg_record(
    *, scenario: str, arm: str, note_variant: str, seed: int, k: int,
    valid: bool = True, session_usd: float = 0.24, metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "leg_id": f"stamp-{scenario}-{arm}-{note_variant}-s{seed}-k{k}",
        "trajectory_id": f"{scenario}-{arm}-{note_variant}-s{seed}",
        "scenario": scenario, "arm": arm, "note_variant": note_variant, "seed": seed, "k": k,
        "valid": valid, "invalid_reason": "" if valid else "primary check failed",
        "metrics": metrics if metrics is not None else _metrics(),
        "cost": {"session_usd": session_usd},
    }


def _leg1_result(*, mistake_committed: bool, censored: bool = False) -> dict[str, Any]:
    return {"metrics": _metrics(censored=censored, mistake_committed=mistake_committed)}


# ---------------------------------------------------------------------------
# rdc_ratio / rdc_delta primitives
# ---------------------------------------------------------------------------


def test_rdc_ratio_null_when_leg1_component_is_zero_or_missing():
    rdc_k = {"turns_to_fact": 2, "tool_calls_to_fact": 3, "billable_tokens_to_fact": 100.0, "rediscovery_actions": 1, "usd_to_fact_alloc": 0.01}
    rdc_1_zero = {c: 0 for c in report.RDC_COMPONENTS}
    rdc_1_missing = {c: None for c in report.RDC_COMPONENTS}
    assert all(v is None for v in report.rdc_ratio(rdc_k, rdc_1_zero).values())
    assert all(v is None for v in report.rdc_ratio(rdc_k, rdc_1_missing).values())


def test_rdc_ratio_divides_componentwise():
    rdc_k = {"turns_to_fact": 2, "tool_calls_to_fact": 3, "billable_tokens_to_fact": 100.0, "rediscovery_actions": 1, "usd_to_fact_alloc": 0.02}
    rdc_1 = {"turns_to_fact": 4, "tool_calls_to_fact": 6, "billable_tokens_to_fact": 400.0, "rediscovery_actions": 2, "usd_to_fact_alloc": 0.04}
    ratios = report.rdc_ratio(rdc_k, rdc_1)
    assert ratios == {"turns_to_fact": 0.5, "tool_calls_to_fact": 0.5, "billable_tokens_to_fact": 0.25, "rediscovery_actions": 0.5, "usd_to_fact_alloc": 0.5}


def test_rdc_delta_is_a_minus_x_and_null_when_either_side_missing():
    rdc_a = {"turns_to_fact": 5, "tool_calls_to_fact": 6, "billable_tokens_to_fact": 500.0, "rediscovery_actions": 3, "usd_to_fact_alloc": 0.08}
    rdc_x = {"turns_to_fact": 2, "tool_calls_to_fact": 2, "billable_tokens_to_fact": None, "rediscovery_actions": 1, "usd_to_fact_alloc": 0.02}
    delta = report.rdc_delta(rdc_a, rdc_x)
    assert delta["turns_to_fact"] == 3
    assert delta["tool_calls_to_fact"] == 4
    assert delta["billable_tokens_to_fact"] is None  # x-side missing -> null, never imputed
    assert delta["rediscovery_actions"] == 2
    assert delta["usd_to_fact_alloc"] == 0.06


# ---------------------------------------------------------------------------
# leg1_rdc_reference (6.4)
# ---------------------------------------------------------------------------


def test_leg1_rdc_reference_told_is_all_zero_regardless_of_leg1_record():
    rdc_1, ref = report.leg1_rdc_reference("told", None)
    assert ref == "told_prompt"
    assert all(v == 0 for v in rdc_1.values())


def test_leg1_rdc_reference_discovered_uses_shared_leg1_metrics():
    leg1_record = {"metrics": _metrics(turns_to_fact=7, tool_calls_to_fact=8, billable_tokens_to_fact=900.0, rediscovery_actions=4, usd_to_fact_alloc=0.09)}
    rdc_1, ref = report.leg1_rdc_reference("discovered", leg1_record)
    assert ref == "shared_leg1"
    assert rdc_1["turns_to_fact"] == 7
    assert rdc_1["usd_to_fact_alloc"] == 0.09


def test_leg1_rdc_reference_missing_is_all_null_not_imputed():
    rdc_1, ref = report.leg1_rdc_reference("discovered", None)
    assert ref == "leg1_missing"
    assert all(v is None for v in rdc_1.values())


def test_leg1_rdc_reference_censored_is_all_null_not_imputed():
    leg1_record = _leg1_result(mistake_committed=False, censored=True)
    rdc_1, ref = report.leg1_rdc_reference("discovered", leg1_record)
    assert ref == "leg1_censored"
    assert all(v is None for v in rdc_1.values())


# ---------------------------------------------------------------------------
# mistake_repetition_rate (6.5) -- the sentinel-mandated test and its converse
# ---------------------------------------------------------------------------


def test_mistake_repetition_rate_is_null_when_leg1_is_clean():
    """DESIGN.md 6.5: mistake_repetition_rate is defined ONLY when leg 1 committed
    the mistake -- null when leg 1 was clean, even though mistake_rate_post (the
    always-defined workhorse) is a real number for the exact same legs. This is the
    expected state for TOLD scenarios (the user has just stated the fact in leg 1).
    """
    legs = [
        _leg_record(scenario="deploy_reverted_by_reconciler", arm="none", note_variant="none", seed=0, k=2, metrics=_metrics(mistake_committed=True)),
        _leg_record(scenario="deploy_reverted_by_reconciler", arm="none", note_variant="none", seed=0, k=3, metrics=_metrics(mistake_committed=True)),
    ]
    leg1_record = _leg1_result(mistake_committed=False)  # leg 1 was clean
    t = report.trajectory_report("deploy_reverted_by_reconciler-none-none-s0", legs, leg1_record, "told")

    assert t["leg1_mistake_committed"] is False
    assert t["mistake_rate_post"] == 1.0  # both k=2 and k=3 repeated it
    assert t["mistake_repetition_rate"] is None  # but there was nothing to "repeat" from leg 1's own state


def test_mistake_repetition_rate_equals_rate_post_when_leg1_committed_mistake():
    legs = [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, metrics=_metrics(mistake_committed=True)),
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=3, metrics=_metrics(mistake_committed=False)),
    ]
    leg1_record = _leg1_result(mistake_committed=True)  # leg 1 DID commit the error
    t = report.trajectory_report("release_via_ci-none-none-s0", legs, leg1_record, "discovered")

    assert t["leg1_mistake_committed"] is True
    assert t["mistake_rate_post"] == 0.5
    assert t["mistake_repetition_rate"] == 0.5


def test_mistake_repetition_rate_is_null_when_leg1_record_is_unavailable():
    legs = [_leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, metrics=_metrics(mistake_committed=True))]
    t = report.trajectory_report("release_via_ci-none-none-s0", legs, None, "discovered")
    assert t["leg1_mistake_committed"] is None
    assert t["mistake_repetition_rate"] is None
    assert t["mistake_rate_post"] == 1.0


def test_censored_leg_still_counts_toward_mistake_metrics():
    """6.4: 'Censored legs still count in full toward the mistake metrics.'"""
    legs = [_leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, metrics=_metrics(censored=True, mistake_committed=True))]
    leg1_record = _leg1_result(mistake_committed=True)
    t = report.trajectory_report("release_via_ci-none-none-s0", legs, leg1_record, "discovered")
    assert t["censored_count"] == 1
    assert t["mistake_rate_post"] == 1.0  # the censored leg's mistake_committed=True is NOT dropped
    assert t["n_legs_counted"] == 1


# ---------------------------------------------------------------------------
# invalid-leg exclusion
# ---------------------------------------------------------------------------


def test_invalid_leg_is_excluded_from_every_number_but_counted():
    legs = [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, valid=False, metrics=_metrics(mistake_committed=True)),
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=3, valid=True, metrics=_metrics(mistake_committed=False)),
    ]
    leg1_record = _leg1_result(mistake_committed=True)
    t = report.trajectory_report("release_via_ci-none-none-s0", legs, leg1_record, "discovered")
    assert t["invalid_count"] == 1
    assert t["n_legs_counted"] == 1  # only the valid k=3 leg counts
    assert t["mistake_rate_post"] == 0.0  # k=3's own mistake_committed=False, k=2 excluded entirely
    row_k2 = next(r for r in t["legs"] if r["k"] == 2)
    assert row_k2 == {"k": 2, "valid": False, "excluded_reason": "invalid"}


# ---------------------------------------------------------------------------
# trajectory identity fields + cross-arm delta
# ---------------------------------------------------------------------------


def test_trajectory_report_carries_identity_fields_from_first_leg():
    legs = [_leg_record(scenario="release_via_ci", arm="proxy", note_variant="plain", seed=0, k=2)]
    t = report.trajectory_report("release_via_ci-proxy-plain-s0", legs, None, "discovered")
    assert (t["scenario"], t["arm"], t["note_variant"], t["seed"]) == ("release_via_ci", "proxy", "plain", 0)


def test_cross_arm_delta_only_pairs_matching_k_present_in_both():
    leg1_record = _leg1_result(mistake_committed=True)
    a_legs = [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, session_usd=0.30, metrics=_metrics(turns_to_fact=6)),
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=3, session_usd=0.30, metrics=_metrics(turns_to_fact=6)),
    ]
    x_legs = [
        _leg_record(scenario="release_via_ci", arm="proxy", note_variant="plain", seed=0, k=2, session_usd=0.12, metrics=_metrics(turns_to_fact=2)),
        # no k=3 leg for arm X -- e.g. still running / not yet reached
    ]
    a_report = report.trajectory_report("release_via_ci-none-none-s0", a_legs, leg1_record, "discovered")
    x_report = report.trajectory_report("release_via_ci-proxy-plain-s0", x_legs, leg1_record, "discovered")
    delta = report.cross_arm_delta(a_report, x_report)
    assert len(delta) == 1
    assert delta[0]["k"] == 2
    assert delta[0]["rdc_delta_a_minus_x"]["turns_to_fact"] == 4
    assert delta[0]["session_usd_delta_a_minus_x"] == pytest.approx(0.18)


# ---------------------------------------------------------------------------
# scenario_report / build_report -- end-to-end over a real runs-dir layout
# ---------------------------------------------------------------------------


def _write_results_jsonl(runs_dir: Path, records: list[dict]) -> None:
    with (runs_dir / "results.jsonl").open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _write_leg1(runs_dir: Path, scenario: str, seed: int, *, mistake_committed: bool) -> None:
    d = runs_dir / "_shared" / "leg1" / f"{scenario}-s{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(_leg1_result(mistake_committed=mistake_committed)))


def test_scenario_report_groups_trajectories_and_computes_cross_arm_delta(tmp_path):
    _write_leg1(tmp_path, "release_via_ci", 0, mistake_committed=True)
    records = [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, session_usd=0.30, metrics=_metrics(mistake_committed=True, turns_to_fact=6)),
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=3, session_usd=0.30, metrics=_metrics(mistake_committed=True, turns_to_fact=6)),
        _leg_record(scenario="release_via_ci", arm="proxy", note_variant="plain", seed=0, k=2, session_usd=0.12, metrics=_metrics(mistake_committed=False, turns_to_fact=2)),
        _leg_record(scenario="release_via_ci", arm="proxy", note_variant="plain", seed=0, k=3, session_usd=0.12, metrics=_metrics(mistake_committed=False, turns_to_fact=2)),
    ]
    _write_results_jsonl(tmp_path, records)

    sc = report.scenario_report(tmp_path, "release_via_ci")
    assert set(sc["trajectories"]) == {"release_via_ci-none-none-s0", "release_via_ci-proxy-plain-s0"}
    assert sc["trajectories"]["release_via_ci-none-none-s0"]["mistake_repetition_rate"] == 1.0
    assert sc["trajectories"]["release_via_ci-proxy-plain-s0"]["mistake_rate_post"] == 0.0

    delta = sc["cross_arm_delta_vs_none"]["release_via_ci-proxy-plain-s0"]
    assert len(delta) == 2
    assert all(d["rdc_delta_a_minus_x"]["turns_to_fact"] == 4 for d in delta)
    assert all(d["session_usd_delta_a_minus_x"] == pytest.approx(0.18) for d in delta)


def test_scenario_report_filters_by_seed(tmp_path):
    _write_leg1(tmp_path, "release_via_ci", 0, mistake_committed=True)
    _write_leg1(tmp_path, "release_via_ci", 1, mistake_committed=True)
    records = [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2),
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=1, k=2),
    ]
    _write_results_jsonl(tmp_path, records)
    sc = report.scenario_report(tmp_path, "release_via_ci", seed=1)
    assert set(sc["trajectories"]) == {"release_via_ci-none-none-s1"}


def test_build_report_covers_only_requested_scenarios(tmp_path):
    _write_results_jsonl(tmp_path, [])
    full = report.build_report(tmp_path)
    assert set(full) == set(scen.SCENARIOS)
    scoped = report.build_report(tmp_path, scenarios=["release_via_ci", "bench_box_only"])
    assert set(scoped) == {"release_via_ci", "bench_box_only"}


def test_format_human_readable_smoke(tmp_path):
    _write_leg1(tmp_path, "release_via_ci", 0, mistake_committed=True)
    _write_results_jsonl(tmp_path, [
        _leg_record(scenario="release_via_ci", arm="none", note_variant="none", seed=0, k=2, metrics=_metrics(mistake_committed=True)),
    ])
    text = report.format_human_readable(report.build_report(tmp_path, scenarios=["release_via_ci"]))
    assert "release_via_ci" in text
    assert "mistake_rate_post" in text


def test_load_results_jsonl_returns_empty_list_when_file_absent(tmp_path):
    assert report.load_results_jsonl(tmp_path) == []
