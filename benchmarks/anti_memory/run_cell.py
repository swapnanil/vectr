#!/usr/bin/env python3
"""EVAL-ANTI-MEMORY -- run one CELL (one scenario x one arm), DESIGN.md 6.1/13.

A "cell" is one (scenario, arm) pair. This script's ONLY implemented mode is
`--probe-only` (DESIGN.md 13's Tier 0, $0): materialize the scenario
workspace, spin up a scratch memory-only vectr daemon, build the arm's store
state (`arm_store.build_store_state`), run every $0-reachable non-vacuity
gate (`scorer.cell_non_vacuity`: raw-daemon `/v1/proactive` + `/v1/recall`
preflights, plus the real in-process proxy app), tear the daemon down, and
write one machine-readable `result.json`. No agent is spawned; no `claude -p`
call is made anywhere in this path -- zero quota, zero LLM inference.

Paid-tier (T1+) execution -- spawning a real coding-agent session against a
live cell and scoring its transcript with `scorer.score_run` -- is
DELIBERATELY NOT implemented here (out of this task's scope; see
`DESIGN.md` 16.9's sequencing). `--dry-run` prints what such an invocation
would need (scenario prompt, arm, estimated turn budget) without running
anything; invoking this script in any mode other than `--probe-only` or
`--dry-run` raises `NotImplementedError` explicitly, so a future T1+ build-out
has one obvious seam to fill in rather than a silently-wrong live path.

Same isolation/honesty rails as `longitudinal_rediscovery/run_leg.py` (one
fresh `VECTR_DB_DIR`/daemon per invocation, scratch ports >= 8899, `vectr
stop --path` not `--port`, hard-stop fallback via `lsof`) -- duplicated, not
imported, per this codebase's own per-harness-duplication convention (see
`scorer.py`'s module docstring for the same rule stated once already).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
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
arm_store = _load_local_module("_vectr_eval_antimem_arm_store", "arm_store.py")
scorer = _load_local_module("_vectr_eval_antimem_scorer", "scorer.py")

DEFAULT_DAEMON_PORT = 8930  # scratch range >= 8899; offset from longitudinal's 8899/8900 pair


# ---------------------------------------------------------------------------
# process / http helpers -- duplicated from run_leg.py's own helpers of the
# same name (module docstring explains why: `scenarios.py` name collisions
# across harnesses make a shared import the wrong tradeoff).
# ---------------------------------------------------------------------------


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", "replace")[:400]}
    except Exception as exc:  # noqa: BLE001
        return {"_error": type(exc).__name__}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body[:400]}


def _wait_for(url: str, timeout_s: float, label: str) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        last = _http_json("GET", url, timeout=10.0)
        if "_error" not in last and "_http_error" not in last:
            return last
        time.sleep(1.0)
    raise SystemExit(f"ABORT: {label} did not become ready at {url} within {timeout_s:.0f}s: {last}")


def _wait_for_embedder_ready(status_url: str, timeout_s: float) -> dict:
    """`/v1/status` answers as soon as the daemon's phase-1 construction
    finishes; the embedding model itself loads on a background thread
    afterward and flips `embedder_ready` only once attached (`app/service.py`
    `_init_search_layer`, UPG-STDIO-MEMORY-READY). Until then a just-written
    note has no vector yet, so `/v1/proactive`'s semantic match sees zero
    candidates for it regardless of arm -- not an injection-path bug, a
    daemon cold-start ordering the harness must wait out before treating an
    empty proactive response as meaningful. Same polling shape as
    `_wait_for`, gated on this one extra status field."""
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        last = _http_json("GET", status_url, timeout=10.0)
        if last.get("embedder_ready"):
            return last
        time.sleep(1.0)
    raise SystemExit(
        f"ABORT: embedder did not become ready at {status_url} within {timeout_s:.0f}s: {last}"
    )


def _port_pids(port: int) -> list[int]:
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.split() if p.strip().isdigit()]


def _hard_stop_port(port: int) -> list[int]:
    killed: list[int] = []
    for pid in _port_pids(port):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            time.sleep(1.0)
            if not _port_pids(port):
                break
        killed.append(pid)
    return killed


_STARTED_PORT = re.compile(r"on port (\d+)")


def _parse_started_port(text: str) -> int | None:
    m = _STARTED_PORT.search(text or "")
    return int(m.group(1)) if m else None


def _main_checkout_root() -> Path:
    """The main vectr checkout root, resolved via git rather than a fixed
    number of `.parent` hops -- this script may run from a coder worktree
    under `.claude/worktrees/<name>/`, whose own `tmp/` is NOT the shared,
    gitignored/`.vectrignore`d location the always-on 8765 daemon skips. `git
    rev-parse --git-common-dir` always resolves to the MAIN checkout's `.git`
    regardless of which worktree invokes this, unlike `--show-toplevel`,
    which returns the worktree's own root."""
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(_THIS_DIR), capture_output=True, text=True, timeout=20, check=True,
    ).stdout.strip()
    return Path(out).parent


def _free_port_at_or_above(start: int, *, avoid: set[int]) -> int:
    for port in range(start, start + 60):
        if port in avoid or _port_pids(port):
            continue
        return port
    raise SystemExit(f"ABORT: no free scratch port at or above {start}")


def _stop_daemon(vectr_bin: str, workspace: Path, port: int, env: dict[str, str]) -> list[int]:
    """`vectr stop` takes `--path`, not `--port` (a `--port` makes argparse
    exit non-zero without stopping anything)."""
    subprocess.run(
        [vectr_bin, "stop", "--path", str(workspace)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    for _ in range(10):
        if not _port_pids(port):
            return []
        time.sleep(1.0)
    return _hard_stop_port(port)


# ---------------------------------------------------------------------------
# The cell
# ---------------------------------------------------------------------------


class CellRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scenario = scen.get(args.scenario)
        self.arm = args.arm
        self.reason_variant = args.reason_variant
        self.out_dir = Path(args.out_dir).resolve()
        self.workspace = self.out_dir / "workspace"
        self.verify_dir = self.out_dir / "verify"
        self.artifacts = self.out_dir / "artifacts"
        self.vectr_bin = args.vectr_bin
        self.daemon_port = args.daemon_port
        self.db_dir = self.out_dir / "_scratch_db"
        self.audit_log = self.artifacts / "audit.log"

        self.leg_start_baselines: dict[str, str] = {}
        self.store_state: Any = None
        self.record: dict[str, Any] = {
            "cell_id": f"{args.scenario}-{args.arm}-{args.reason_variant}",
            "scenario": args.scenario,
            "arm": args.arm,
            "reason_variant": args.reason_variant,
            "tier": "T0",
            "started_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }

    def _daemon_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["VECTR_DB_DIR"] = str(self.db_dir)
        env["VECTR_AUDIT_LOG"] = str(self.audit_log)
        env["VECTR_PORT"] = str(self.daemon_port)
        env["VECTR_WORKSPACE"] = str(self.workspace)
        return env

    def prepare(self) -> None:
        if self.out_dir.exists() and any(self.out_dir.iterdir()):
            raise SystemExit(f"ABORT: output dir already has content: {self.out_dir}")
        for d in (self.workspace, self.artifacts, self.verify_dir, self.db_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.leg_start_baselines = scen.materialize(self.scenario, self.workspace)
        scen.materialize_verifiers(self.scenario, self.verify_dir)
        (self.artifacts / "baselines.json").write_text(json.dumps(self.leg_start_baselines, indent=2))

    def start_daemon(self) -> str:
        if _port_pids(self.daemon_port):
            self.daemon_port = _free_port_at_or_above(self.daemon_port + 1, avoid=set())
        cmd = [
            self.vectr_bin, "start", str(self.workspace),
            "--port", str(self.daemon_port), "--memory-only", "--no-ide-config",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=self._daemon_env())
        out = f"{proc.stdout}\n{proc.stderr}"
        (self.artifacts / "daemon-start.txt").write_text(f"$ {' '.join(cmd)}\nrc={proc.returncode}\n\n{out}")
        actual = _parse_started_port(out)
        if actual is not None and actual != self.daemon_port:
            self.daemon_port = actual
        if self.daemon_port < 8899:
            raise SystemExit(f"ABORT: daemon landed on port {self.daemon_port}, below the 8899 scratch floor")
        base_url = f"http://127.0.0.1:{self.daemon_port}"
        status = _wait_for(f"{base_url}/v1/status", 180.0, "scratch daemon")
        # Phase-1 readiness (the daemon answers /v1/status) is not phase-2
        # readiness (the embedder has attached to the note store) -- wait for
        # the latter too, or a note `remember()`-d moments later has no
        # vector yet and every semantic non-vacuity check below reads as a
        # false "nothing was injected" (see `_wait_for_embedder_ready`).
        status = _wait_for_embedder_ready(f"{base_url}/v1/status", 180.0)
        (self.artifacts / "daemon-status.json").write_text(json.dumps(status, indent=2))
        if status.get("notes_count", 0) != 0:
            raise SystemExit(
                f"ABORT: scratch daemon store is not empty at start (notes_count="
                f"{status.get('notes_count')}) -- a fresh VECTR_DB_DIR must start empty"
            )
        self.record["daemon_port"] = self.daemon_port
        return base_url

    def build_store(self, base_url: str) -> None:
        self.store_state = arm_store.build_store_state(
            base_url, self.scenario, self.arm, reason_variant=self.reason_variant,
        )
        arm_store.write_store_state_json(self.store_state, self.artifacts / "store_state.json")
        self.record["store_post_conditions"] = arm_store.assert_post_conditions(self.store_state, base_url)

    def run_gates(self, base_url: str) -> None:
        gate = scorer.cell_non_vacuity(
            self.scenario, self.arm, self.store_state, base_url=base_url, daemon_base_url=base_url,
            session=None,
        )
        self.record["non_vacuity"] = gate
        self.record["valid"] = bool(gate["valid"]) and not self.record["store_post_conditions"]
        self.record["invalid_reason"] = "; ".join(
            r for r in (gate.get("invalid_reason"), "; ".join(self.record["store_post_conditions"])) if r
        )

    def write(self) -> None:
        self.record["finished_utc"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (self.out_dir / "result.json").write_text(json.dumps(self.record, indent=2, default=str))

    def report(self) -> None:
        print(f"cell {self.record['cell_id']} (T0, probe-only)")
        print(f"  valid          : {self.record.get('valid')}")
        print(f"  invalid_reason : {self.record.get('invalid_reason')!r}")
        print(f"  artifacts      : {self.out_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(scen.SCENARIOS))
    ap.add_argument("--arm", required=True, choices=arm_store.ARMS)
    ap.add_argument("--reason-variant", default="corrective", choices=("bare", "causal", "corrective"))
    ap.add_argument("--out-dir", required=True, help="Must live under vectr/tmp/ (never /tmp, never elsewhere).")
    ap.add_argument("--daemon-port", type=int, default=DEFAULT_DAEMON_PORT)
    ap.add_argument("--vectr-bin", default=shutil.which("vectr") or "vectr")
    ap.add_argument("--probe-only", action="store_true", help=(
        "The only implemented execution mode (DESIGN.md 13 Tier 0, $0): build "
        "store state and run the non-vacuity gates against a real scratch "
        "daemon. Spawns no agent."
    ))
    ap.add_argument("--dry-run", action="store_true", help=(
        "Print the scenario prompt, arm, and a placeholder turn/cost budget "
        "for what a T1+ live-agent cell would need, without running anything."
    ))
    return ap


def _dry_run(args: argparse.Namespace) -> None:
    scenario = scen.get(args.scenario)
    print(f"[dry-run] cell {args.scenario}-{args.arm}-{args.reason_variant} (T1+, NOT executed)")
    print(f"  prompt        : {scenario.prompt!r}")
    print(f"  arm           : {args.arm} (store shape {arm_store.STORE_STATE_FOR_ARM[args.arm]})")
    print("  estimated turn budget: 30 (longitudinal_rediscovery's own --max-turns default, unverified for this eval)")
    print("  estimated cost/leg   : unestimated -- no paid leg has run yet for this harness")


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()

    if args.dry_run:
        _dry_run(args)
        return

    if not args.probe_only:
        raise NotImplementedError(
            "run_cell.py's live (paid) T1+ execution path -- spawning a real "
            "coding-agent session and scoring its transcript with "
            "scorer.score_run -- is out of scope for this task. Pass "
            "--probe-only (T0, $0, no agent) or --dry-run (prints a cost "
            "estimate, runs nothing)."
        )

    if args.daemon_port < 8899:
        raise SystemExit("ABORT: --daemon-port must be >= 8899 (8765/8800 are live daemons)")
    out_dir = Path(args.out_dir).resolve()
    vectr_tmp = (_main_checkout_root() / "tmp").resolve()
    if not out_dir.is_relative_to(vectr_tmp):
        raise SystemExit(
            f"ABORT: --out-dir must live under {vectr_tmp} (got {out_dir}) -- fixtures "
            f"outside vectr/tmp/ risk collision with the always-on 8765 daemon's indexer, "
            f"and bare /tmp is cleared after 3 days"
        )

    runner = CellRunner(args)
    try:
        runner.prepare()
        base_url = runner.start_daemon()
        runner.build_store(base_url)
        runner.run_gates(base_url)
    finally:
        _stop_daemon(runner.vectr_bin, runner.workspace, runner.daemon_port, runner._daemon_env())
    runner.write()
    runner.report()


if __name__ == "__main__":
    main()
