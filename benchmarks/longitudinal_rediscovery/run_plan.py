#!/usr/bin/env python3
"""EVAL-LONGITUDINAL-REDISCOVERY -- trajectory driver (DESIGN.md section 11, step 4).

`run_leg.py` runs ONE leg (one `claude -p` session). This module is the layer above
it: given a tier (DESIGN.md 9), it enumerates the trajectories that tier touches,
resolves the shared-leg-1 cache (DESIGN.md 5.2), restores/verifies each leg's starting
snapshot, invokes `run_leg.py` as a fresh subprocess per leg (one process per leg is
load-bearing -- DESIGN.md 5.4's fresh-daemon/fresh-proxy-per-leg rule, and the proxy's
cooldown ledger has no time decay so a reused process would let leg k suppress leg
k+1's injection), and persists `<trajectory>/state.json` (schema/trajectory_state.
schema.json) so a later invocation resumes rather than re-running completed cells
(DESIGN.md 8's resumability contract).

Tier definitions (`_tier_trajectories`, `_d2_trajectories`, `_slope_trajectories`) are
transcribed directly from DESIGN.md 9's table; `estimate` mode (`--dry-run`) reproduces
that table's own mean/worst figures exactly when run against an empty `--runs-dir`,
which is asserted in tests -- see `tests/test_longitudinal_plan.py`.

Two DESIGN.md ambiguities were resolved with the narrowest reading (documented here and
in the commit that introduces this file, per the coder brief -- do not re-derive):

1. T0 ("probe-only ... for all six scenarios; no agent") does not name an arm, note
   variant, or k. `run_t0` runs exactly ONE `run_leg.py --probe-only` cell per
   scenario, at k=2, arm "proxy" (T1's own headline memory arm), note_variant "plain"
   (the one variant every scenario ships), with `--plant-note`. `--probe-only` at k>1
   with no `--restore-tar` materializes fresh rather than requiring a prior leg
   (run_leg.py's own documented behavior -- see its `--probe-only` help text, which
   already names this as "the scenario/note reachability smoke check T0 uses"), so one
   call per scenario exercises materialization, note planting, and daemon-side
   reachability together. It does not exercise leg 1's own (unplanted) prompt in
   isolation -- that is covered by every live leg 1 in every later tier, and by the
   scorer discrimination suite's per-scenario fixtures (`tests/
   test_longitudinal_scorer.py`), not by T0.
2. T3 ("`provenance` on S1+S5, `verifiable` on S1") does not name an arm either.
   `_verifiable_notes_trajectories` uses "proxy" throughout -- the same channel as T1's
   headline contrast, so only the note-authoring variant differs from a cell T1 already
   ran, isolating that one variable.

Exit/cost contract mirrored from `run_leg.py`: a leg subprocess exiting 0 means it ran
to completion (including an invalid or task-failing leg, which is still "done" for
resumability purposes); a nonzero exit is a genuine abort and stops that trajectory
without touching later legs. `--dry-run` and `--probe-only` never invoke a live agent;
`--budget-usd` is the only spend guard for a live run, checked before every leg start.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_RUN_LEG = _THIS_DIR / "run_leg.py"
# See run_leg.py's own module docstring / _load_local_module comment: two harnesses
# under benchmarks/ ship same-named scenarios.py/scorer.py files, so a bare `import
# scenarios` races for one sys.modules slot across whichever test/driver imports first.
# Loading by explicit file path under this fixed, cache-checked key converges every
# caller (scorer.py, run_leg.py, this module, their tests) on ONE module object.
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
# `--arms` validates against run_leg.py's own ARMS tuple -- the single source of
# truth for valid arm names -- rather than a second, driftable copy here.
_run_leg_mod = _load_local_module("_vectr_eval_longitudinal_run_leg_module", "run_leg.py")
ARMS = _run_leg_mod.ARMS

TIERS = ("T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7")

# DESIGN.md 9's own cost model: "legs >= 2 at $0.24 mean / $0.35 worst; leg 1 at $0.35
# mean / $0.50 worst (discovery sessions are longer)". Frozen design constants (not a
# tunable knob -- the eval's own accounting of what a session already cost, not a
# threshold that reshapes behavior), transcribed once here for both the budget guard
# and the --dry-run cost estimate.
_LEG_MEAN_USD_K1 = 0.35
_LEG_WORST_USD_K1 = 0.50
_LEG_MEAN_USD_KN = 0.24
_LEG_WORST_USD_KN = 0.35

# S-numbering per DESIGN.md 3/9, in the exact order scenarios.py's own SCENARIOS dict
# defines them (verified: discovered S1-S4, told S5-S6).
_S1_RELEASE_VIA_CI = "release_via_ci"
_S2_SPEC_LIVES_OUTSIDE = "spec_lives_outside"
_S3_RUNNER_NOT_PYTEST = "runner_not_pytest"
_S4_SECRETS_NOT_DOTENV = "secrets_not_dotenv"
_S5_DEPLOY_REVERTED = "deploy_reverted_by_reconciler"
_S6_BENCH_BOX_ONLY = "bench_box_only"


@dataclass(frozen=True)
class TrajectorySpec:
    scenario: str
    arm: str
    note_variant: str
    seed: int
    n_legs: int

    @property
    def trajectory_id(self) -> str:
        # leg_result.schema.json / trajectory_state.schema.json: "<scenario>-<arm>-
        # <variant>-s<seed>".
        return f"{self.scenario}-{self.arm}-{self.note_variant}-s{self.seed}"


# ---------------------------------------------------------------------------
# Tier definitions -- transcribed from DESIGN.md 9's table. Each function returns the
# trajectories a fresh run of that tier touches; `plan_and_run` resolves shared leg 1
# and per-leg resumability against whatever `--runs-dir` actually contains, so running
# tiers out of DESIGN.md's documented order (or against a runs-dir some tiers already
# populated) is just a bigger or smaller bill, never a correctness question.
# ---------------------------------------------------------------------------


def _headline_trajectories(seed: int, n_legs: int) -> list[TrajectorySpec]:
    """T1: A vs C on S1 (DISCOVERED/RDC headline) + S5 (TOLD/MRR headline).
    note_variant "plain" -- T1 tests the delivery channel, not note-authoring
    variants (that is T3).
    """
    return [
        TrajectorySpec(scenario=s, arm=a, note_variant=v, seed=seed, n_legs=n_legs)
        for s in (_S1_RELEASE_VIA_CI, _S5_DEPLOY_REVERTED)
        for a, v in (("none", "none"), ("proxy", "plain"))
    ]


def _channel_breadth_trajectories(seed: int, n_legs: int) -> list[TrajectorySpec]:
    """T2: D1 (SessionStart hook) + B (guided MCP) on the same 2 headline scenarios."""
    return [
        TrajectorySpec(scenario=s, arm=a, note_variant="plain", seed=seed, n_legs=n_legs)
        for s in (_S1_RELEASE_VIA_CI, _S5_DEPLOY_REVERTED)
        for a in ("hook-sessionstart", "mcp")
    ]


def _verifiable_notes_trajectories(seed: int, n_legs: int) -> list[TrajectorySpec]:
    """T3: `provenance` on S1+S5 (Q2 + control), `verifiable` on S1 only (Q1 --
    `verifiable` requires `corroborable=True`, which only S1 has among the two).
    Arm "proxy" throughout; see module docstring point 2.
    """
    trajs = [
        TrajectorySpec(scenario=s, arm="proxy", note_variant="provenance", seed=seed, n_legs=n_legs)
        for s in (_S1_RELEASE_VIA_CI, _S5_DEPLOY_REVERTED)
    ]
    trajs.append(
        TrajectorySpec(scenario=_S1_RELEASE_VIA_CI, arm="proxy", note_variant="verifiable", seed=seed, n_legs=n_legs)
    )
    return trajs


def _scenario_breadth_trajectories(seed: int, n_legs: int) -> list[TrajectorySpec]:
    """T4: S2, S3, S4, S6 x arms {A, C} -- breadth insurance against a two-scenario
    headline being idiosyncratic.
    """
    return [
        TrajectorySpec(scenario=s, arm=a, note_variant=v, seed=seed, n_legs=n_legs)
        for s in (_S2_SPEC_LIVES_OUTSIDE, _S3_RUNNER_NOT_PYTEST, _S4_SECRETS_NOT_DOTENV, _S6_BENCH_BOX_ONLY)
        for a, v in (("none", "none"), ("proxy", "plain"))
    ]


def _d2_trajectories(seed: int, n_legs: int) -> list[TrajectorySpec]:
    """T6 (conditional): full hook set on the 2 headline scenarios. Never run without
    a fresh `verified:true` attestation (DESIGN.md 4); `plan_and_run` marks every leg
    of these trajectories "skipped" instead of invoking run_leg.py when
    `--hook-attestation` is absent or does not verify.
    """
    return [
        TrajectorySpec(scenario=s, arm="hook-full", note_variant="plain", seed=seed, n_legs=n_legs)
        for s in (_S1_RELEASE_VIA_CI, _S5_DEPLOY_REVERTED)
    ]


def _slope_trajectories(seed: int) -> list[TrajectorySpec]:
    """T5: leg 4 on every T1 + T2 trajectory -- does the saving persist past N=3?
    Reuses the exact trajectory identities T1/T2 already created; requesting n_legs=4
    against a trajectory whose state.json still has n_legs=3 extends it in place
    (`_load_or_init_state` appends the missing leg entries rather than
    reinitializing) -- legs 1..3 are untouched, only leg 4 is new work.
    """
    return _headline_trajectories(seed, 4) + _channel_breadth_trajectories(seed, 4)


def _tier_trajectories(tier: str, *, seed: int) -> list[TrajectorySpec]:
    if tier == "T1":
        return _headline_trajectories(seed, 3)
    if tier == "T2":
        return _channel_breadth_trajectories(seed, 3)
    if tier == "T3":
        return _verifiable_notes_trajectories(seed, 3)
    if tier == "T4":
        return _scenario_breadth_trajectories(seed, 3)
    if tier == "T5":
        return _slope_trajectories(seed)
    if tier == "T6":
        return _d2_trajectories(seed, 3)
    if tier == "T7":
        # DESIGN.md 9: "T7 replication | seed 1 of T1" -- fixed, not caller-chosen.
        return _headline_trajectories(1, 3)
    raise ValueError(f"tier {tier!r} has no trajectory-generating preset (T0 is planned separately)")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_cmd(cmd: list[str], *, dry_run: bool, timeout_s: int = 1800) -> tuple[int, str, str]:
    """The one place a leg subprocess is actually spawned. `dry_run` short-circuits
    with no process created -- every dry-run code path in this module routes through
    here rather than checking `dry_run` itself, so tests can assert "never actually
    invoked" by monkeypatching this single function.
    """
    if dry_run:
        return 0, "", ""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


def _read_valid_attestation(path: str | None) -> dict | None:
    """DESIGN.md 4: arm hook-full requires a `verified:true` attestation or it is
    SKIPPED, never run. Returns the parsed attestation only if it actually verifies.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        att = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    return att if att.get("verified") is True else None


_EMPTY_LEG_ENTRY_FIELDS = (
    "result_path", "end_state_tar", "end_state_manifest_sha256", "usd", "valid",
    "started_utc", "finished_utc",
)


def _new_leg_entry(k: int) -> dict:
    entry: dict[str, Any] = {"k": k, "status": "pending"}
    entry.update({f: None for f in _EMPTY_LEG_ENTRY_FIELDS})
    return entry


def _leg_entry(state: dict, k: int) -> dict:
    for leg in state["legs"]:
        if leg["k"] == k:
            return leg
    entry = _new_leg_entry(k)
    state["legs"].append(entry)
    state["legs"].sort(key=lambda leg: leg["k"])
    return entry


def _init_state(spec: TrajectorySpec) -> dict:
    now = _utc_stamp()
    return {
        "trajectory_id": spec.trajectory_id,
        "scenario": spec.scenario,
        "arm": spec.arm,
        "note_variant": spec.note_variant,
        "seed": spec.seed,
        "n_legs": spec.n_legs,
        "db_dir": None,
        "created_utc": now,
        "updated_utc": now,
        "leg1_id": None,
        "leg1_source": None,
        "planted_note_id": None,
        "planted_anchor": None,
        "legs": [_new_leg_entry(k) for k in range(1, spec.n_legs + 1)],
        "trajectory_valid_through": None,
        "spend_usd": 0.0,
        "tier": None,
        "hook_attestation": None,
        "notes": None,
    }


def _load_or_init_state(traj_dir: Path, spec: TrajectorySpec) -> dict:
    path = traj_dir / "state.json"
    if not path.is_file():
        return _init_state(spec)
    state = json.loads(path.read_text())
    # T5 extends an existing T1/T2 trajectory from n_legs=3 to 4 -- append the newly
    # requested leg entries rather than discarding recorded legs 1..3 (DESIGN.md 9 T5).
    existing_ks = {leg["k"] for leg in state["legs"]}
    for k in range(1, spec.n_legs + 1):
        if k not in existing_ks:
            state["legs"].append(_new_leg_entry(k))
    state["legs"].sort(key=lambda leg: leg["k"])
    state["n_legs"] = max(state["n_legs"], spec.n_legs)
    return state


def _save_state(traj_dir: Path, state: dict) -> None:
    # trajectory_state.schema.json: spend_usd is "Sum of completed legs' total_cost_usd"
    # -- THIS trajectory's own legs, not the multi-trajectory running total a single
    # `--budget-usd` invocation accumulates across every spec it processes. Recomputed
    # from state["legs"] at every write so it is correct no matter which code path
    # (normal completion, budget-exhausted early return, invalid-leg early exit) saves.
    state["spend_usd"] = round(sum(leg.get("usd") or 0.0 for leg in state["legs"]), 2)
    state["updated_utc"] = _utc_stamp()
    traj_dir.mkdir(parents=True, exist_ok=True)
    (traj_dir / "state.json").write_text(json.dumps(state, indent=2))


def _is_forced(args: argparse.Namespace, trajectory_id: str, k: int) -> bool:
    return args.force_leg == f"{trajectory_id}:{k}"


def _supersede(path: Path) -> None:
    """Rename (never delete) an existing leg/leg1 directory so a re-run gets a clean
    target -- run_leg.py's `prepare()` aborts if its output dir already has content.
    """
    if path.exists():
        path.rename(path.parent / f"{path.name}.superseded-{_utc_stamp()}")


# ---------------------------------------------------------------------------
# shared leg 1 (DESIGN.md 5.2)
# ---------------------------------------------------------------------------


def _shared_leg1_dir(runs_dir: Path, scenario: str, seed: int) -> Path:
    return runs_dir / "_shared" / "leg1" / f"{scenario}-s{seed}"


def _ensure_shared_leg1(scenario: str, seed: int, *, runs_dir: Path, args: argparse.Namespace, cache: dict) -> dict:
    """Run ONCE per (scenario, seed); every trajectory sharing that pair reuses the
    same cache entry. `cache` is mutated so distinct trajectories in one
    `plan_and_run` call never redo the check or (worse) redo the run.
    """
    key = (scenario, seed)
    if key in cache:
        return cache[key]

    leg1_dir = _shared_leg1_dir(runs_dir, scenario, seed)
    leg1_id_path = leg1_dir / "leg1_id"
    if leg1_id_path.is_file() and not args.refresh_leg1:
        result = json.loads((leg1_dir / "result.json").read_text())
        if result.get("valid"):
            info = {
                "dir": leg1_dir,
                "leg1_id": leg1_id_path.read_text().strip(),
                "workspace_tar": leg1_dir / "workspace.tar",
                "manifest_sha256": result.get("end_state_manifest_sha256"),
                "valid": result.get("valid"),
                "usd": (result.get("cost") or {}).get("session_usd"),
                "result_path": leg1_dir / "result.json",
                "would_run": False,
                "ran": False,
            }
            cache[key] = info
            return info
        # An on-disk shared leg 1 exists but is invalid (e.g. run_leg.py's
        # session-level non-vacuity abort, scorer.leg_non_vacuity's
        # `session_errored` gate -- DESIGN.md 4.1) -- never treat an invalid leg1
        # as a usable starting snapshot for any arm. Fall through; the live-run
        # path below supersedes this stale directory before retrying, so a later
        # invocation retries leg 1 rather than resuming past a hollow one.

    if args.dry_run:
        info = {
            "dir": leg1_dir, "leg1_id": None, "workspace_tar": leg1_dir / "workspace.tar",
            "manifest_sha256": None, "valid": None, "usd": None,
            "result_path": leg1_dir / "result.json", "would_run": True, "ran": False,
        }
        cache[key] = info
        return info

    # Anything left in leg1_dir at this point is stale: an explicit --refresh-leg1
    # request, an invalid cached leg1 just detected above, or leftover partial
    # content from a prior attempt that aborted before ever producing a leg1_id
    # (e.g. run_leg.py exiting nonzero on a session-level abort). run_leg.py's own
    # `prepare()` refuses to write into a directory that already has content, so
    # this must be superseded before every live (re-)run, not only on an explicit
    # refresh; `_supersede` is a no-op if the directory does not exist.
    _supersede(leg1_dir)

    cmd = [
        sys.executable, str(_RUN_LEG), "--shared-leg1",
        "--scenario", scenario, "--seed", str(seed), "--out-dir", str(leg1_dir),
        "--vectr-bin", args.vectr_bin, "--model", args.model,
        "--max-turns", str(args.max_turns), "--timeout-s", str(args.timeout_s),
        "--daemon-port", str(args.daemon_port), "--proxy-port", str(args.proxy_port),
    ]
    if args.probe_only:
        cmd.append("--probe-only")
    rc, out, err = _run_cmd(cmd, dry_run=False)
    info = {
        "dir": leg1_dir, "leg1_id": None, "workspace_tar": leg1_dir / "workspace.tar",
        "manifest_sha256": None, "valid": None, "usd": None,
        "result_path": leg1_dir / "result.json", "would_run": False, "ran": True,
        "returncode": rc, "cmd": cmd, "stdout": out, "stderr": err[-2000:],
    }
    if not args.probe_only:
        if rc == 0 and info["result_path"].is_file():
            result = json.loads(info["result_path"].read_text())
            info.update(
                leg1_id=result.get("leg1_id"),
                manifest_sha256=result.get("end_state_manifest_sha256"),
                valid=result.get("valid"),
                usd=(result.get("cost") or {}).get("session_usd"),
            )
        else:
            info["failed"] = True
    cache[key] = info
    return info


# ---------------------------------------------------------------------------
# per-leg cell (k >= 2)
# ---------------------------------------------------------------------------


def _trajectory_workspace_dir(traj_dir: Path) -> Path:
    """One agent/daemon workspace path per trajectory, identical for every k -- see
    run_leg.py's `--workspace-dir` help text: vectr's working-memory store scopes
    notes by workspace path, so a note leg 2 plants must be registered under the
    same path leg 3+'s fresh daemon queries, even though every leg of one
    trajectory already shares one `--db-dir` (sqlite file). `run_leg.py`'s
    `prepare()` wipes and re-materializes/restores this directory fresh on every
    leg, so reusing the path across serial legs is safe.
    """
    return traj_dir / "workspace"


def _leg_cmd(
    spec: TrajectorySpec, k: int, *, prior: dict, traj_dir: Path,
    planted_anchor: str | None, args: argparse.Namespace,
) -> list[str]:
    leg_dir = traj_dir / "legs" / str(k)
    cmd = [
        sys.executable, str(_RUN_LEG),
        "--scenario", spec.scenario, "--seed", str(spec.seed), "--k", str(k),
        "--leg-dir", str(leg_dir), "--trajectory-id", spec.trajectory_id,
        "--workspace-dir", str(_trajectory_workspace_dir(traj_dir)),
        "--arm", spec.arm, "--db-dir", str(traj_dir / "db"),
        "--restore-tar", str(prior["tar"]), "--restore-manifest-sha256", str(prior["manifest_sha256"]),
        "--vectr-bin", args.vectr_bin, "--model", args.model,
        "--max-turns", str(args.max_turns), "--timeout-s", str(args.timeout_s),
        "--daemon-port", str(args.daemon_port), "--proxy-port", str(args.proxy_port),
    ]
    if spec.arm != "none":
        cmd += ["--note-variant", spec.note_variant]
        if k == 2:
            cmd.append("--plant-note")
        elif planted_anchor:
            cmd += ["--planted-anchor", planted_anchor]
    if spec.arm == "hook-full":
        if not args.hook_attestation:
            raise ValueError("hook-full leg requested without --hook-attestation")
        cmd += ["--hook-attestation", args.hook_attestation]
    if args.probe_only:
        cmd.append("--probe-only")
    if args.allow_unreachable:
        cmd.append("--allow-unreachable")
    if args.allow_hook_unreachable:
        cmd.append("--allow-hook-unreachable")
    if args.allow_manifest_mismatch:
        cmd.append("--allow-manifest-mismatch")
    return cmd


# ---------------------------------------------------------------------------
# T0 -- probe-only smoke check, all six scenarios (see module docstring point 1)
# ---------------------------------------------------------------------------


def run_t0(runs_dir: Path, args: argparse.Namespace) -> dict:
    out_root = runs_dir / "_t0"
    results: dict[str, dict] = {}
    for slug in sorted(scen.SCENARIOS):
        cell_dir = out_root / slug
        cmd = [
            sys.executable, str(_RUN_LEG),
            "--scenario", slug, "--seed", "0", "--k", "2",
            "--leg-dir", str(cell_dir), "--trajectory-id", f"{slug}-proxy-plain-s0-t0probe",
            "--arm", "proxy", "--note-variant", "plain", "--plant-note",
            "--db-dir", str(cell_dir / "db"), "--probe-only",
            "--vectr-bin", args.vectr_bin, "--model", args.model,
            "--daemon-port", str(args.daemon_port), "--proxy-port", str(args.proxy_port),
        ]
        rc, out, err = _run_cmd(cmd, dry_run=args.dry_run)
        results[slug] = {"returncode": rc, "stdout": out, "stderr": err, "cmd": cmd}
    outcome = {"tier": "T0", "results": results}
    if not args.dry_run:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "t0_summary.json").write_text(json.dumps(outcome, indent=2))
        outcome["summary_path"] = str(out_root / "t0_summary.json")
    return outcome


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


def plan_and_run(tier: str, args: argparse.Namespace) -> dict:
    runs_dir = Path(args.runs_dir).resolve()
    if tier == "T0":
        return run_t0(runs_dir, args)

    specs = _tier_trajectories(tier, seed=args.seed)
    # `--arms` filters the tier's OWN enumeration post-hoc; it never reaches
    # `_tier_trajectories` and so never changes what a tier IS or the no-flag
    # --dry-run estimates (getattr default handles callers -- e.g. `_args()` test
    # helpers predating this flag -- that never set `args.arms` at all).
    arms_filter = getattr(args, "arms", None)
    if arms_filter:
        specs = [s for s in specs if s.arm in arms_filter]
    leg1_cache: dict[tuple[str, int], dict] = {}
    announced_leg1: set[tuple[str, int]] = set()
    spend = 0.0
    plan: list[dict] = []

    for spec in specs:
        traj_dir = runs_dir / spec.trajectory_id
        state = _load_or_init_state(traj_dir, spec)
        state["tier"] = tier

        if spec.arm == "hook-full":
            att = _read_valid_attestation(args.hook_attestation)
            if att is None:
                for leg in state["legs"]:
                    if leg["k"] > 1 and leg["status"] in ("pending", "skipped"):
                        leg["status"] = "skipped"
                state["notes"] = "arm hook-full skipped: no verified hook-attestation supplied"
                if not args.dry_run:
                    _save_state(traj_dir, state)
                plan.append({"trajectory_id": spec.trajectory_id, "k": None, "status": "skipped(no-attestation)"})
                continue
            state["hook_attestation"] = att
            # An attestation supplied on THIS invocation makes any leg previously
            # marked "skipped" for lack of one eligible again -- it never actually
            # ran, unlike "done"/"invalid", so it needs no --force-leg override.
            for leg in state["legs"]:
                if leg["k"] > 1 and leg["status"] == "skipped":
                    leg["status"] = "pending"
            state["notes"] = None

        key = (spec.scenario, spec.seed)
        already_seen = key in announced_leg1
        leg1 = _ensure_shared_leg1(spec.scenario, spec.seed, runs_dir=runs_dir, args=args, cache=leg1_cache)
        if not already_seen:
            announced_leg1.add(key)
            if leg1.get("would_run"):
                plan.append({
                    "trajectory_id": None, "scenario": spec.scenario, "seed": spec.seed, "k": 1,
                    "status": "would-run-leg1",
                    "est_mean_usd": _LEG_MEAN_USD_K1, "est_worst_usd": _LEG_WORST_USD_K1,
                })
            elif leg1.get("ran"):
                plan.append({
                    "trajectory_id": None, "scenario": spec.scenario, "seed": spec.seed, "k": 1,
                    "status": "failed(shared-leg1)" if leg1.get("failed") else "done(shared-leg1)",
                    "usd": leg1.get("usd"),
                })
                if leg1.get("usd"):
                    spend += leg1["usd"]
            else:
                plan.append({
                    "trajectory_id": None, "scenario": spec.scenario, "seed": spec.seed, "k": 1,
                    "status": "leg1-cached",
                })

        state["leg1_id"] = leg1.get("leg1_id")
        state["leg1_source"] = str(leg1["dir"])
        leg1_entry = _leg_entry(state, 1)
        leg1_entry.update({
            "status": "done" if leg1.get("valid") else ("invalid" if leg1.get("valid") is False else "pending"),
            "result_path": str(leg1["result_path"]),
            "end_state_tar": str(leg1["workspace_tar"]),
            "end_state_manifest_sha256": leg1.get("manifest_sha256"),
            "usd": leg1.get("usd"),
            "valid": leg1.get("valid"),
        })

        if not args.dry_run and not args.probe_only and leg1.get("failed"):
            _save_state(traj_dir, state)
            continue

        prior = {"tar": leg1["workspace_tar"], "manifest_sha256": leg1.get("manifest_sha256")}
        planted_anchor = state.get("planted_anchor")

        for k in range(2, spec.n_legs + 1):
            leg_entry = _leg_entry(state, k)
            forced = _is_forced(args, spec.trajectory_id, k)

            if leg_entry["status"] in ("done", "invalid") and not forced:
                if leg_entry["end_state_tar"]:
                    prior = {"tar": Path(leg_entry["end_state_tar"]), "manifest_sha256": leg_entry["end_state_manifest_sha256"]}
                plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "already-done"})
                continue
            if leg_entry["status"] == "skipped" and not forced:
                plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "already-skipped"})
                continue

            if args.dry_run:
                # A forced leg is reported as re-runnable without ever touching disk
                # -- superseding the old directory and resetting later legs to
                # pending is a real mutation, deferred to the live path below.
                plan.append({
                    "trajectory_id": spec.trajectory_id, "k": k,
                    "status": "would-force-rerun" if forced else "would-run",
                    "est_mean_usd": _LEG_MEAN_USD_KN, "est_worst_usd": _LEG_WORST_USD_KN,
                })
                continue

            leg_dir = traj_dir / "legs" / str(k)
            # A leg previously recorded "failed" (run_leg.py's subprocess exited
            # nonzero -- a genuine session-level abort, DESIGN.md module docstring's
            # exit-code contract) must be RETRIED on a later invocation, not trusted
            # (it never completed) and not left permanently stuck: its own leg_dir
            # may already have on-disk content from the aborted attempt (run_leg.py's
            # score()/snapshot()/write() still run before the abort in the
            # session-errored case), and run_leg.py's own `prepare()` refuses to
            # write into a directory that already has content -- so without
            # superseding first, every retry would immediately re-abort the same
            # way. Same `_supersede` (rename, never delete) pattern as an explicit
            # --force-leg and the shared-leg1 stale-cache guard.
            if forced or leg_entry["status"] == "failed":
                _supersede(leg_dir)
                for k2 in range(k, spec.n_legs + 1):
                    _leg_entry(state, k2)["status"] = "pending"
                leg_entry = _leg_entry(state, k)

            if not args.probe_only and args.budget_usd is not None:
                remaining = args.budget_usd - spend
                if remaining < _LEG_WORST_USD_KN:
                    plan.append({
                        "trajectory_id": spec.trajectory_id, "k": k, "status": "budget-exhausted",
                        "remaining_usd": round(remaining, 2),
                    })
                    _save_state(traj_dir, state)
                    return {"tier": tier, "plan": plan, "spend_usd": round(spend, 2), "budget_exhausted": True}

            cmd = _leg_cmd(spec, k, prior=prior, traj_dir=traj_dir, planted_anchor=planted_anchor, args=args)
            leg_entry["status"] = "running"
            leg_entry["started_utc"] = _utc_stamp()
            _save_state(traj_dir, state)
            rc, out, err = _run_cmd(cmd, dry_run=False)
            leg_entry["finished_utc"] = _utc_stamp()

            if args.probe_only:
                leg_entry["status"] = "pending"  # probe-only writes no result.json; never "done"
                plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "probed", "returncode": rc, "stdout": out})
                _save_state(traj_dir, state)
                break  # no end-state tar to chain a k+1 restore from -- see module docstring

            if rc != 0:
                leg_entry["status"] = "failed"
                plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "failed", "returncode": rc, "stderr": err[-2000:]})
                _save_state(traj_dir, state)
                break

            result = json.loads((leg_dir / "artifacts" / "result.json").read_text())
            leg_entry["status"] = "done" if result.get("valid") else "invalid"
            leg_entry["result_path"] = str(leg_dir / "artifacts" / "result.json")
            leg_entry["end_state_tar"] = result.get("end_state_tar")
            leg_entry["end_state_manifest_sha256"] = result.get("end_state_manifest_sha256")
            leg_entry["usd"] = (result.get("cost") or {}).get("session_usd")
            leg_entry["valid"] = result.get("valid")
            if leg_entry["usd"]:
                spend += leg_entry["usd"]

            if k == 2 and spec.arm != "none":
                state["planted_note_id"] = result.get("planted_note_id")
                state["planted_anchor"] = result.get("planted_anchor")
                planted_anchor = state["planted_anchor"]

            if not result.get("valid"):
                state["trajectory_valid_through"] = k - 1
                for k2 in range(k, spec.n_legs + 1):
                    later = _leg_entry(state, k2)
                    if later["status"] == "pending":
                        later["status"] = "skipped"
                _save_state(traj_dir, state)
                plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "invalid", "invalid_reason": result.get("invalid_reason")})
                break

            state["trajectory_valid_through"] = k
            prior = {"tar": Path(leg_entry["end_state_tar"]), "manifest_sha256": leg_entry["end_state_manifest_sha256"]}
            plan.append({"trajectory_id": spec.trajectory_id, "k": k, "status": "done", "usd": leg_entry["usd"]})
            _save_state(traj_dir, state)

        if not args.dry_run:
            # --dry-run promises zero spend; it must also leave no trace on disk
            # (the only per-trajectory writes above are inside the k-loop, which
            # never runs a real leg in --dry-run mode). spend_usd itself is
            # recomputed inside _save_state from this trajectory's own legs.
            state["db_dir"] = str(traj_dir / "db")
            _save_state(traj_dir, state)

    outcome: dict[str, Any] = {"tier": tier, "plan": plan, "spend_usd": round(spend, 2), "budget_exhausted": False}
    if args.dry_run:
        outcome["est_mean_usd"] = round(sum(p.get("est_mean_usd", 0) for p in plan), 2)
        outcome["est_worst_usd"] = round(sum(p.get("est_worst_usd", 0) for p in plan), 2)
    return outcome


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _arms_list(value: str) -> tuple[str, ...]:
    """`argparse` `type=` for `--arms`: a comma-separated arm-name list, validated
    against `ARMS` (run_leg.py's own tuple) so an unknown arm name is rejected as a
    normal argparse usage error rather than silently filtering everything out.
    """
    names = tuple(v.strip() for v in value.split(",") if v.strip())
    unknown = [n for n in names if n not in ARMS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown arm(s) {unknown!r}; valid arms are {list(ARMS)}"
        )
    return names


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", required=True, choices=TIERS)
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--seed", type=int, default=0, help="Ignored for T0 (no seed) and T7 (fixed at seed 1, DESIGN.md 9).")
    ap.add_argument("--budget-usd", type=float, default=None, help=(
        "Refuse to START a new leg once the remaining budget is below that leg's "
        "tier-worst-case cost (DESIGN.md 8). Ignored for --dry-run/--probe-only, "
        "which spend nothing regardless."
    ))
    ap.add_argument("--dry-run", action="store_true", help="Print the exact leg list and a cost estimate; spend nothing.")
    ap.add_argument("--probe-only", action="store_true", help=(
        "Convert every leg this tier would run into a zero-quota run_leg.py "
        "--probe-only call instead of a live claude -p session. Stops each "
        "trajectory after its first probed leg (no end-state snapshot exists to "
        "chain a further restore from). T0 always behaves this way and ignores "
        "this flag."
    ))
    ap.add_argument("--force-leg", default=None, metavar="TRAJECTORY_ID:K", help=(
        "Re-run one leg even though it already completed; supersedes (renames, "
        "never deletes) that leg's directory and marks every later leg of the same "
        "trajectory pending again (DESIGN.md 8)."
    ))
    ap.add_argument("--refresh-leg1", action="store_true", help=(
        "Re-run the shared leg 1 for every (scenario, seed) this tier touches and "
        "mint a new leg1_id, instead of reusing a cached one (DESIGN.md 5.2)."
    ))
    ap.add_argument("--hook-attestation", default=None, help=(
        "Path to a verified:true attestation JSON; required for any arm hook-full "
        "(D2) trajectory in this tier. Without it, those trajectories' legs are "
        "marked 'skipped' and run_leg.py is never invoked for them (DESIGN.md 4)."
    ))
    ap.add_argument("--arms", type=_arms_list, default=None, metavar="ARM[,ARM...]", help=(
        "Filter this tier's enumerated trajectories to only the named arm(s), e.g. "
        "'--arms hook-sessionstart' or '--arms none,proxy'. Applied AFTER the tier's "
        "own trajectory enumeration -- never changes what a tier IS, and the no-flag "
        "--dry-run estimates (DESIGN.md 9's table) are unaffected when this flag is "
        "absent. Lets a budget-constrained run buy one arm of a tier without the "
        "others. Unknown arm names are rejected. Valid arms: " + ", ".join(ARMS)
    ))
    ap.add_argument("--vectr-bin", default=shutil.which("vectr") or "vectr")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--daemon-port", type=int, default=8899)
    ap.add_argument("--proxy-port", type=int, default=8900)
    ap.add_argument("--allow-unreachable", action="store_true")
    ap.add_argument("--allow-hook-unreachable", action="store_true", help=(
        "Pass-through to run_leg.py: record a hook-arm leg even when its "
        "SessionStart hook preflight cannot prove the delivery mechanism works "
        "on that leg's own scratch daemon (arms hook-sessionstart/hook-full only)."
    ))
    ap.add_argument("--allow-manifest-mismatch", action="store_true")
    return ap


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()
    if args.daemon_port < 8899 or args.proxy_port < 8899:
        raise SystemExit("ABORT: scratch ports must be >= 8899 (8765/8800 are live daemons)")
    Path(args.runs_dir).mkdir(parents=True, exist_ok=True)
    outcome = plan_and_run(args.tier, args)
    print(json.dumps(outcome, indent=2, default=str))


if __name__ == "__main__":
    main()
