"""Tests for the longitudinal-rediscovery trajectory driver (DESIGN.md section 11,
step 4 -- `benchmarks/longitudinal_rediscovery/run_plan.py`).

`run_leg.py` runs one leg; this module is the layer above it that enumerates a
tier's trajectories, resolves the shared-leg-1 cache, invokes `run_leg.py` as a
subprocess per leg, and persists resumable `state.json`. Every test here
monkeypatches `run_plan._run_cmd` -- the single subprocess-spawn point the module
docstring calls out -- so no test ever invokes a real `claude -p` session or even a
real `run_leg.py` subprocess; `--dry-run`/`--probe-only` tests do not even need the
monkeypatch since `_run_cmd` itself short-circuits on `dry_run=True`.

Four bugs were found and fixed while building this driver, each re-derived here as
its own regression test rather than left as ad hoc verification:
  1. `--dry-run` must leave zero trace on disk (it was writing state.json).
  2. arm hook-full's "skipped: no attestation" must not persist through a
     `--dry-run` invocation, and must be recoverable (reset to pending, not stuck)
     once a valid attestation is later supplied -- without needing --force-leg,
     since a skipped leg never actually ran.
  3. `--force-leg ... --dry-run` must not supersede (rename) the leg directory on
     disk; that mutation belongs only to the live path.
  4. `state.json`'s `spend_usd` is per-TRAJECTORY (trajectory_state.schema.json:
     "Sum of completed legs' total_cost_usd") and must never be assigned the
     multi-trajectory running total a single `--budget-usd` guard accumulates
     across every trajectory a tier invocation processes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "longitudinal_rediscovery"

# Same collision-safe loading pattern as run_plan.py itself / test_longitudinal_scorer.py:
# benchmarks/injection_utility/ ships its own same-named scenarios.py, so a bare
# `import scenarios` races for one sys.modules slot depending on collection order.
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
run_plan = _load_local_module("_vectr_eval_longitudinal_run_plan_test", "run_plan.py")

STATE_SCHEMA = json.loads((BENCH_DIR / "schema" / "trajectory_state.schema.json").read_text())

S1 = "release_via_ci"
S5 = "deploy_reverted_by_reconciler"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _args(runs_dir: Path, tier: str, **overrides) -> Any:
    ap = run_plan._build_argparser()
    argv = ["--tier", tier, "--runs-dir", str(runs_dir)]
    for flag, value in overrides.items():
        name = f"--{flag.replace('_', '-')}"
        if value is True:
            argv.append(name)
        elif value is False or value is None:
            continue
        else:
            argv += [name, str(value)]
    return ap.parse_args(argv)


def _seed_shared_leg1(runs_dir: Path, scenario: str, seed: int = 0, *, usd: float = 0.35) -> None:
    d = runs_dir / "_shared" / "leg1" / f"{scenario}-s{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "leg1_id").write_text("fakeleg1id")
    (d / "workspace.tar").write_bytes(b"FAKE")
    (d / "result.json").write_text(json.dumps({
        "end_state_manifest_sha256": "fakemanifest",
        "valid": True,
        "cost": {"session_usd": usd},
    }))


def _fake_run_cmd_factory(*, usd: float = 0.24, valid: bool = True):
    """A `_run_cmd` stand-in for the live (non-dry-run, non-probe-only) leg path:
    writes a schema-shaped result.json under the leg dir named --leg-dir on the cmd
    line, exactly as run_leg.py's own `main()` does on success.
    """
    calls: list[list[str]] = []

    def fake(cmd: list[str], *, dry_run: bool, timeout_s: int = 1800):
        calls.append(cmd)
        assert not dry_run
        leg_dir = Path(cmd[cmd.index("--leg-dir") + 1])
        (leg_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (leg_dir / "artifacts" / "result.json").write_text(json.dumps({
            "valid": valid,
            "invalid_reason": "" if valid else "primary check failed",
            "end_state_tar": str(leg_dir / "artifacts" / "end_state.tar"),
            "end_state_manifest_sha256": f"m-{leg_dir.name}",
            "cost": {"session_usd": usd},
            "planted_note_id": 1,
            "planted_anchor": "note:1",
        }))
        return 0, "", ""

    fake.calls = calls
    return fake


# ---------------------------------------------------------------------------
# tier trajectory enumeration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,expected_count",
    [("T1", 4), ("T2", 4), ("T3", 3), ("T4", 8), ("T5", 8), ("T6", 2), ("T7", 4)],
)
def test_tier_trajectory_counts_match_design_table(tier, expected_count):
    specs = run_plan._tier_trajectories(tier, seed=0)
    assert len(specs) == expected_count


def test_t1_and_t7_are_the_same_headline_pair_at_different_seeds():
    t1 = run_plan._tier_trajectories("T1", seed=0)
    t7 = run_plan._tier_trajectories("T7", seed=99)  # T7 pins seed 1 regardless of caller's seed
    assert {s.trajectory_id for s in t1} == {
        "release_via_ci-none-none-s0", "release_via_ci-proxy-plain-s0",
        "deploy_reverted_by_reconciler-none-none-s0", "deploy_reverted_by_reconciler-proxy-plain-s0",
    }
    assert {s.trajectory_id for s in t7} == {
        "release_via_ci-none-none-s1", "release_via_ci-proxy-plain-s1",
        "deploy_reverted_by_reconciler-none-none-s1", "deploy_reverted_by_reconciler-proxy-plain-s1",
    }


def test_t3_uses_proxy_arm_and_verifiable_only_on_corroborable_scenario():
    specs = run_plan._tier_trajectories("T3", seed=0)
    assert all(s.arm == "proxy" for s in specs)
    variants_by_scenario: dict[str, set[str]] = {}
    for s in specs:
        variants_by_scenario.setdefault(s.scenario, set()).add(s.note_variant)
    assert variants_by_scenario[S1] == {"provenance", "verifiable"}
    assert variants_by_scenario[S5] == {"provenance"}  # S5 is TOLD/uncorroborable -- no "verifiable"


def test_t5_reuses_t1_and_t2_trajectory_identities_at_n_legs_4():
    t1_ids = {s.trajectory_id for s in run_plan._tier_trajectories("T1", seed=0)}
    t2_ids = {s.trajectory_id for s in run_plan._tier_trajectories("T2", seed=0)}
    t5 = run_plan._tier_trajectories("T5", seed=0)
    assert {s.trajectory_id for s in t5} == t1_ids | t2_ids
    assert all(s.n_legs == 4 for s in t5)


def test_t0_has_no_trajectory_preset_and_is_planned_separately():
    with pytest.raises(ValueError):
        run_plan._tier_trajectories("T0", seed=0)


# ---------------------------------------------------------------------------
# T0 -- probe-only smoke check
# ---------------------------------------------------------------------------


def test_t0_probes_all_six_scenarios_with_proxy_plain_k2(tmp_path):
    args = _args(tmp_path, "T0", dry_run=True)
    outcome = run_plan.run_t0(tmp_path, args)
    assert set(outcome["results"]) == set(scen.SCENARIOS)
    for slug, result in outcome["results"].items():
        cmd = result["cmd"]
        assert cmd[cmd.index("--scenario") + 1] == slug
        assert cmd[cmd.index("--arm") + 1] == "proxy"
        assert cmd[cmd.index("--note-variant") + 1] == "plain"
        assert cmd[cmd.index("--k") + 1] == "2"
        assert "--plant-note" in cmd
        assert "--probe-only" in cmd


def test_t0_dry_run_leaves_no_trace_on_disk(tmp_path):
    args = _args(tmp_path, "T0", dry_run=True)
    run_plan.run_t0(tmp_path, args)
    assert list(tmp_path.iterdir()) == []


def test_t0_live_writes_a_summary_artifact(tmp_path):
    fake = _fake_run_cmd_factory()
    args = _args(tmp_path, "T0")
    with mock.patch.object(run_plan, "_run_cmd", side_effect=lambda cmd, *, dry_run, timeout_s=1800: (0, "", "")):
        outcome = run_plan.run_t0(tmp_path, args)
    summary_path = Path(outcome["summary_path"])
    assert summary_path.is_file()
    on_disk = json.loads(summary_path.read_text())
    assert set(on_disk["results"]) == set(scen.SCENARIOS)


# ---------------------------------------------------------------------------
# dry-run cost estimates -- DESIGN.md 9's own published table
# ---------------------------------------------------------------------------


def test_t1_fresh_dry_run_matches_design_headline_cost(tmp_path):
    outcome = run_plan.plan_and_run("T1", _args(tmp_path, "T1", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (2.62, 3.80)


def test_t2_dry_run_matches_design_cost_when_leg1_already_cached(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    outcome = run_plan.plan_and_run("T2", _args(tmp_path, "T2", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (1.92, 2.80)


def test_t3_dry_run_matches_design_cost_when_leg1_already_cached(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    outcome = run_plan.plan_and_run("T3", _args(tmp_path, "T3", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (1.44, 2.10)


def test_t4_fresh_dry_run_matches_design_cost(tmp_path):
    outcome = run_plan.plan_and_run("T4", _args(tmp_path, "T4", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (5.24, 7.60)


def test_t6_dry_run_matches_design_cost_with_valid_attestation_and_cached_leg1(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"verified": True, "date": "2026-07-29", "method": "manual", "claude_code_version": "x"}))
    outcome = run_plan.plan_and_run("T6", _args(tmp_path, "T6", dry_run=True, hook_attestation=str(attestation)))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (0.96, 1.40)


def test_t7_fresh_dry_run_matches_design_cost(tmp_path):
    outcome = run_plan.plan_and_run("T7", _args(tmp_path, "T7", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (2.62, 3.80)


def test_t5_dry_run_matches_design_cost_when_t1_and_t2_legs_1to3_already_done(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    for spec in run_plan._tier_trajectories("T5", seed=0):
        traj_dir = tmp_path / spec.trajectory_id
        legs = [
            {
                "k": k, "status": "done", "result_path": None,
                "end_state_tar": str(traj_dir / "legs" / str(k) / "artifacts" / "end_state.tar"),
                "end_state_manifest_sha256": f"m{k}", "usd": 0.35 if k == 1 else 0.24,
                "valid": True, "started_utc": None, "finished_utc": None,
            }
            for k in (1, 2, 3)
        ]
        state = run_plan._init_state(run_plan.TrajectorySpec(spec.scenario, spec.arm, spec.note_variant, spec.seed, 3))
        state["legs"] = legs
        state["trajectory_valid_through"] = 3
        (traj_dir).mkdir(parents=True, exist_ok=True)
        (traj_dir / "state.json").write_text(json.dumps(state))
    outcome = run_plan.plan_and_run("T5", _args(tmp_path, "T5", dry_run=True))
    assert (outcome["est_mean_usd"], outcome["est_worst_usd"]) == (1.92, 2.80)
    already_done = [p for p in outcome["plan"] if p.get("status") == "already-done"]
    would_run = [p for p in outcome["plan"] if p.get("status") == "would-run"]
    assert len(already_done) == 8 * 2  # legs 2 and 3 of all 8 trajectories
    assert len(would_run) == 8  # only the new leg 4 per trajectory


# ---------------------------------------------------------------------------
# Bug #1 regression: --dry-run must leave zero trace on disk
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_files_at_all(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    before = set(tmp_path.rglob("*"))
    run_plan.plan_and_run("T1", _args(tmp_path, "T1", dry_run=True))
    after = set(tmp_path.rglob("*"))
    assert after == before


# ---------------------------------------------------------------------------
# resumability
# ---------------------------------------------------------------------------


def test_already_done_leg_is_not_rerun(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    traj_id = "release_via_ci-none-none-s0"
    traj_dir = tmp_path / traj_id
    traj_dir.mkdir(parents=True)
    state = run_plan._init_state(run_plan.TrajectorySpec(S1, "none", "none", 0, 3))
    state["legs"][1].update(status="done", valid=True, usd=0.24, end_state_tar=str(traj_dir / "legs" / "2" / "artifacts" / "end_state.tar"), end_state_manifest_sha256="m2")
    (traj_dir / "state.json").write_text(json.dumps(state))

    fake = _fake_run_cmd_factory()
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T1", _args(tmp_path, "T1"))

    # Only leg 3 of this trajectory (and the other T1 trajectory's legs 2/3) should
    # have spawned a subprocess -- never leg 2 of the already-done trajectory.
    leg2_calls = [c for c in fake.calls if "--leg-dir" in c and str(traj_dir / "legs" / "2") in c[c.index("--leg-dir") + 1]]
    assert leg2_calls == []
    leg3_calls = [c for c in fake.calls if "--leg-dir" in c and str(traj_dir / "legs" / "3") in c[c.index("--leg-dir") + 1]]
    assert len(leg3_calls) == 1


# ---------------------------------------------------------------------------
# Bug #3 regression: --force-leg must not mutate disk during --dry-run, and must
# supersede (never delete) + reset later legs to pending in live mode.
# ---------------------------------------------------------------------------


def test_force_leg_dry_run_does_not_touch_disk(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    traj_id = "release_via_ci-none-none-s0"
    traj_dir = tmp_path / traj_id
    leg2_dir = traj_dir / "legs" / "2"
    (leg2_dir / "artifacts").mkdir(parents=True)
    marker = leg2_dir / "artifacts" / "MARKER.txt"
    marker.write_text("original")
    state = run_plan._init_state(run_plan.TrajectorySpec(S1, "none", "none", 0, 3))
    state["legs"][1].update(status="done", valid=True, usd=0.24, end_state_tar=str(leg2_dir / "artifacts" / "end_state.tar"), end_state_manifest_sha256="m2")
    (traj_dir / "state.json").write_text(json.dumps(state))

    before = set(tmp_path.rglob("*"))
    outcome = run_plan.plan_and_run("T1", _args(tmp_path, "T1", dry_run=True, force_leg=f"{traj_id}:2"))
    after = set(tmp_path.rglob("*"))

    assert after == before
    assert marker.read_text() == "original"
    entry = next(p for p in outcome["plan"] if p.get("trajectory_id") == traj_id and p.get("k") == 2)
    assert entry["status"] == "would-force-rerun"


def test_force_leg_live_supersedes_old_dir_and_reruns_later_legs(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    traj_id = "release_via_ci-none-none-s0"
    traj_dir = tmp_path / traj_id
    leg2_dir = traj_dir / "legs" / "2"
    (leg2_dir / "artifacts").mkdir(parents=True)
    marker = leg2_dir / "artifacts" / "MARKER.txt"
    marker.write_text("original")
    state = run_plan._init_state(run_plan.TrajectorySpec(S1, "none", "none", 0, 3))
    state["legs"][1].update(status="done", valid=True, usd=0.24, end_state_tar=str(leg2_dir / "artifacts" / "end_state.tar"), end_state_manifest_sha256="m2")
    state["legs"][2].update(status="done", valid=True, usd=0.24, end_state_tar=str(traj_dir / "legs" / "3" / "artifacts" / "end_state.tar"), end_state_manifest_sha256="m3")
    (traj_dir / "state.json").write_text(json.dumps(state))

    fake = _fake_run_cmd_factory()
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T1", _args(tmp_path, "T1", force_leg=f"{traj_id}:2"))

    superseded = list((traj_dir / "legs").glob("2.superseded-*"))
    assert len(superseded) == 1
    assert (superseded[0] / "artifacts" / "MARKER.txt").read_text() == "original"

    new_state = json.loads((traj_dir / "state.json").read_text())
    leg2 = next(leg for leg in new_state["legs"] if leg["k"] == 2)
    leg3 = next(leg for leg in new_state["legs"] if leg["k"] == 3)
    assert leg2["status"] == "done"
    assert leg2["end_state_manifest_sha256"] != "m2"  # rerun produced a fresh manifest
    assert leg3["status"] == "done"  # cascaded reset made leg 3 pending again, then it ran


# ---------------------------------------------------------------------------
# Bug #2 regression: hook-full attestation gating is dry-run-safe and recoverable
# ---------------------------------------------------------------------------


def test_hook_full_without_attestation_is_skipped_and_dry_run_persists_nothing(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    before = set(tmp_path.rglob("*"))
    outcome = run_plan.plan_and_run("T6", _args(tmp_path, "T6", dry_run=True))
    after = set(tmp_path.rglob("*"))
    assert after == before
    assert all(p["status"] == "skipped(no-attestation)" for p in outcome["plan"] if p.get("k") is None)
    assert outcome["est_mean_usd"] == 0.0
    assert outcome["est_worst_usd"] == 0.0


def test_hook_full_skip_recovers_once_a_valid_attestation_is_supplied_without_force_leg(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    fake = _fake_run_cmd_factory()

    # First: live run with no attestation -> both trajectories' legs land "skipped".
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T6", _args(tmp_path, "T6"))
    assert fake.calls == []
    for spec in run_plan._tier_trajectories("T6", seed=0):
        state = json.loads((tmp_path / spec.trajectory_id / "state.json").read_text())
        assert all(leg["status"] == "skipped" for leg in state["legs"] if leg["k"] > 1)

    # Second: same runs-dir, now with a valid attestation and NO --force-leg -- the
    # previously-skipped legs must become eligible again (they never actually ran).
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"verified": True, "date": "2026-07-29", "method": "manual", "claude_code_version": "x"}))
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T6", _args(tmp_path, "T6", hook_attestation=str(attestation)))
    assert len(fake.calls) > 0
    for spec in run_plan._tier_trajectories("T6", seed=0):
        state = json.loads((tmp_path / spec.trajectory_id / "state.json").read_text())
        assert all(leg["status"] == "done" for leg in state["legs"] if leg["k"] > 1)


# ---------------------------------------------------------------------------
# budget guard
# ---------------------------------------------------------------------------


def test_budget_guard_refuses_a_new_leg_once_remaining_is_below_worst_case(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    fake = _fake_run_cmd_factory(usd=0.24)
    # Worst-case per non-leg-1 leg is $0.35; a $0.30 budget must refuse to start
    # even the very first leg without ever calling _run_cmd.
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        outcome = run_plan.plan_and_run("T1", _args(tmp_path, "T1", budget_usd=0.30))
    assert outcome["budget_exhausted"] is True
    assert fake.calls == []
    assert any(p.get("status") == "budget-exhausted" for p in outcome["plan"])


def test_budget_guard_allows_legs_until_it_is_actually_exhausted(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    fake = _fake_run_cmd_factory(usd=0.24)
    # T1 = 4 trajectories x 2 legs each x $0.24 = $1.92 total. A $0.50 budget covers
    # roughly the first 2 legs (worst-case-gated at $0.35 remaining) then must stop.
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        outcome = run_plan.plan_and_run("T1", _args(tmp_path, "T1", budget_usd=0.50))
    assert outcome["budget_exhausted"] is True
    assert 0 < len(fake.calls) < 8


# ---------------------------------------------------------------------------
# Bug #4 regression: state.json's spend_usd is per-trajectory, never the
# multi-trajectory running total the budget guard accumulates.
# ---------------------------------------------------------------------------


def test_per_trajectory_spend_usd_is_isolated_from_other_trajectories(tmp_path):
    _seed_shared_leg1(tmp_path, S1, usd=0.35)
    _seed_shared_leg1(tmp_path, S5, usd=0.35)
    fake = _fake_run_cmd_factory(usd=0.24)
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        outcome = run_plan.plan_and_run("T1", _args(tmp_path, "T1"))

    # Every T1 trajectory has the same shape: cached leg1 ($0.35) + leg2 + leg3
    # ($0.24 each) = $0.83, regardless of processing order within this invocation.
    for spec in run_plan._tier_trajectories("T1", seed=0):
        state = json.loads((tmp_path / spec.trajectory_id / "state.json").read_text())
        assert state["spend_usd"] == pytest.approx(0.83)

    # The invocation-level total (a distinct, intentionally cross-trajectory figure
    # used only for the --budget-usd guard) is unaffected by this fix.
    assert outcome["spend_usd"] == pytest.approx(4 * 0.48)  # 4 trajectories x (leg2+leg3), leg1 was cached


# ---------------------------------------------------------------------------
# state.json schema round-trip
# ---------------------------------------------------------------------------


def test_state_json_validates_against_its_schema_after_a_live_run(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    fake = _fake_run_cmd_factory()
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T1", _args(tmp_path, "T1"))
    for spec in run_plan._tier_trajectories("T1", seed=0):
        state = json.loads((tmp_path / spec.trajectory_id / "state.json").read_text())
        jsonschema.validate(state, STATE_SCHEMA)


def test_state_json_validates_against_its_schema_when_a_leg_is_invalid(tmp_path):
    _seed_shared_leg1(tmp_path, S1)
    _seed_shared_leg1(tmp_path, S5)
    fake = _fake_run_cmd_factory(valid=False)
    with mock.patch.object(run_plan, "_run_cmd", side_effect=fake):
        run_plan.plan_and_run("T1", _args(tmp_path, "T1"))
    for spec in run_plan._tier_trajectories("T1", seed=0):
        state = json.loads((tmp_path / spec.trajectory_id / "state.json").read_text())
        jsonschema.validate(state, STATE_SCHEMA)
        # An invalid leg 2 must skip leg 3 rather than silently running on top of it.
        leg2 = next(leg for leg in state["legs"] if leg["k"] == 2)
        leg3 = next(leg for leg in state["legs"] if leg["k"] == 3)
        assert leg2["status"] == "invalid"
        assert leg3["status"] == "skipped"
        assert state["trajectory_valid_through"] == 1
