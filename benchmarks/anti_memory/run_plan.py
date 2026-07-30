#!/usr/bin/env python3
"""EVAL-ANTI-MEMORY -- plan driver, DESIGN.md 13's Tier 0 row: "probe-only
across 4 scenarios x 4 arm states + scorer fixture tests; no agent", $0.

`run_cell.py` runs ONE cell (one scenario x one arm). This module is the
layer above it: enumerate the 4 scenarios x 4 arms = 16 T0 cells (§13's own
count -- T0-1 through T0-6 are exactly these per-scenario/per-arm gates;
T0-7/T0-8 are offline lint/scorer-fixture suites covered by
`tests/test_anti_memory_scenarios.py` / `tests/test_anti_memory_scorer.py`,
not re-run here), invoke `run_cell.py --probe-only` as a fresh subprocess per
cell (one process per cell, matching `run_cell.py`'s own one-fresh-daemon-per-
invocation isolation rail and DESIGN.md 13's "cells run one at a time, each a
separate process invocation" quota-discipline note -- even though T0 spends
no quota, the same isolation still matters: a shared daemon would let one
cell's proactive cooldown ledger or note store bleed into the next), and
aggregate one machine-readable `plan_result.json` under `--runs-dir`.

Scope-limiting convention, matching `run_cell.py`'s own: T0 is the only
implemented tier. Paid-tier (T1+) enumeration/execution is DELIBERATELY NOT
built here (out of this task's scope; see `DESIGN.md` 16.9's sequencing) --
`--tier` accepts only `T0`; any other value raises `NotImplementedError`
explicitly rather than silently no-op-ing, so a future T1+ build-out has one
obvious seam to fill in.

Resumable (DESIGN.md 11's "resumability" note, and 13's own quota-discipline
paragraph): a cell whose `<runs-dir>/<cell_id>/result.json` already exists is
not re-invoked unless `--force`; `plan_and_run` still folds its prior result
into the aggregate so a plan interrupted partway through and re-run picks up
exactly where it left off with zero duplicate daemon spins.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_RUN_CELL = _THIS_DIR / "run_cell.py"
ANTIMEM_SCENARIOS_KEY = "_vectr_eval_antimem_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, _THIS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(ANTIMEM_SCENARIOS_KEY, "scenarios.py")
# `run_cell.py`'s own ARMS-validating import chain loads arm_store.py under a
# fixed key too; reuse the SAME module object here (not a second `import
# arm_store`) so `ARMS` can never drift between the two call sites.
arm_store = _load_local_module("_vectr_eval_antimem_arm_store", "arm_store.py")
ARMS = arm_store.ARMS

TIERS = ("T0",)
DEFAULT_DAEMON_PORT = 8930


def t0_cells() -> list[tuple[str, str]]:
    """The 16 (scenario, arm) pairs DESIGN.md 13's T0 row covers, in
    `scen.SCENARIO_ORDER` (headline-first, the dated 2026-07-30 resolution)
    x `arm_store.ARMS` order -- deterministic, so `--dry-run`'s printed list
    and a live run's `plan_result.json` enumerate cells identically."""
    return [(slug, arm) for slug in scen.SCENARIO_ORDER for arm in ARMS]


def _cell_id(scenario: str, arm: str, reason_variant: str) -> str:
    return f"{scenario}-{arm}-{reason_variant}"


def _run_cmd(cmd: list[str], *, dry_run: bool, timeout_s: int = 300) -> tuple[int, str, str]:
    """The one place a cell subprocess is actually spawned -- every dry-run
    path routes through here (never checks `dry_run` itself elsewhere) so a
    test can assert "never actually invoked" by monkeypatching this alone,
    same convention as `longitudinal_rediscovery/run_plan.py`'s `_run_cmd`."""
    if dry_run:
        return 0, "", ""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


def _cell_cmd(
    scenario: str, arm: str, reason_variant: str, *, cell_dir: Path,
    daemon_port: int, vectr_bin: str,
) -> list[str]:
    return [
        sys.executable, str(_RUN_CELL),
        "--scenario", scenario, "--arm", arm, "--reason-variant", reason_variant,
        "--out-dir", str(cell_dir), "--daemon-port", str(daemon_port),
        "--vectr-bin", vectr_bin, "--probe-only",
    ]


def _read_result(cell_dir: Path) -> dict | None:
    p = cell_dir / "result.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def run_t0(runs_dir: Path, args: argparse.Namespace) -> dict:
    reason_variant = "corrective"  # DESIGN.md 13's T0 row; the bare/causal
    # rungs of the reason ladder are T4-only (paid), out of T0's scope.
    cells: list[dict[str, Any]] = []
    n_valid = 0
    n_invalid = 0
    n_skipped = 0
    for scenario, arm in t0_cells():
        cell_id = _cell_id(scenario, arm, reason_variant)
        cell_dir = runs_dir / cell_id
        prior = None if args.force else _read_result(cell_dir)
        if prior is not None:
            n_skipped += 1
            entry = {
                "cell_id": cell_id, "scenario": scenario, "arm": arm,
                "reason_variant": reason_variant, "status": "skipped_already_ran",
                "valid": prior.get("valid"), "invalid_reason": prior.get("invalid_reason", ""),
                "result_path": str(cell_dir / "result.json"),
            }
            cells.append(entry)
            if prior.get("valid"):
                n_valid += 1
            else:
                n_invalid += 1
            continue
        if args.force and cell_dir.exists():
            shutil.rmtree(cell_dir)
        cmd = _cell_cmd(
            scenario, arm, reason_variant, cell_dir=cell_dir,
            daemon_port=args.daemon_port, vectr_bin=args.vectr_bin,
        )
        rc, out, err = _run_cmd(cmd, dry_run=args.dry_run)
        entry = {
            "cell_id": cell_id, "scenario": scenario, "arm": arm,
            "reason_variant": reason_variant, "returncode": rc, "cmd": cmd,
        }
        if args.dry_run:
            entry["status"] = "dry_run"
        else:
            result = _read_result(cell_dir)
            entry["status"] = "ran"
            entry["valid"] = result.get("valid") if result else None
            entry["invalid_reason"] = result.get("invalid_reason", "") if result else "no result.json written"
            entry["result_path"] = str(cell_dir / "result.json")
            entry["stdout_tail"] = out[-2000:]
            entry["stderr_tail"] = err[-2000:]
            if entry["valid"]:
                n_valid += 1
            else:
                n_invalid += 1
        cells.append(entry)
    outcome = {
        "tier": "T0",
        "dry_run": args.dry_run,
        "total_cells": len(cells),
        "valid": n_valid,
        "invalid": n_invalid,
        "skipped_already_ran": n_skipped,
        "estimated_cost_usd": 0.0,
        "cells": cells,
        "finished_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }
    if not args.dry_run:
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "plan_result.json").write_text(json.dumps(outcome, indent=2))
        outcome["summary_path"] = str(runs_dir / "plan_result.json")
    return outcome


def plan_and_run(tier: str, args: argparse.Namespace) -> dict:
    if tier != "T0":
        raise NotImplementedError(
            f"plan_and_run: tier {tier!r} is out of this task's scope -- only T0 "
            f"(probe-only, $0, no agent) is implemented; see this module's own "
            f"docstring and DESIGN.md 16.9's sequencing"
        )
    runs_dir = Path(args.runs_dir).resolve()
    return run_t0(runs_dir, args)


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="T0", choices=TIERS)
    ap.add_argument("--runs-dir", required=True, help="Must live under vectr/tmp/ (never /tmp, never elsewhere).")
    ap.add_argument("--dry-run", action="store_true", help="Print the exact 16-cell list; spend nothing, run nothing.")
    ap.add_argument("--force", action="store_true", help="Re-run every cell even if its result.json already exists.")
    ap.add_argument("--daemon-port", type=int, default=DEFAULT_DAEMON_PORT)
    ap.add_argument("--vectr-bin", default=shutil.which("vectr") or "vectr")
    return ap


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()
    if args.daemon_port < 8899:
        raise SystemExit("ABORT: --daemon-port must be >= 8899 (8765/8800/8930 may be live daemons)")
    runs_dir = Path(args.runs_dir).resolve()
    # Reuses run_cell.py's own worktree-portable vectr/tmp/ resolution rather
    # than a second copy of the git-common-dir lookup.
    run_cell_mod = _load_local_module("_vectr_eval_antimem_run_cell", "run_cell.py")
    vectr_tmp = (run_cell_mod._main_checkout_root() / "tmp").resolve()
    if not runs_dir.is_relative_to(vectr_tmp):
        raise SystemExit(
            f"ABORT: --runs-dir must live under {vectr_tmp} (got {runs_dir}) -- fixtures "
            f"outside vectr/tmp/ risk collision with the always-on 8765 daemon's indexer, "
            f"and bare /tmp is cleared after 3 days"
        )
    outcome = plan_and_run(args.tier, args)
    print(json.dumps(outcome, indent=2, default=str))


if __name__ == "__main__":
    main()
