#!/usr/bin/env python3
"""EVAL-LONGITUDINAL-REDISCOVERY -- revalidate one preserved leg's non-vacuity
gate at $0 (DEFECT 13).

`scorer.leg_non_vacuity` (DESIGN.md 4.1) is the ONE arm-aware function that
decides whether a leg's `valid` bit stands -- it never touches the outcome
verdict (`score_run`/`leg_metrics`; see `rescore.py` for revalidating THAT
half instead). When the gate ITSELF changes (DEFECT 13: the mcp/mcp-bare
evidence hierarchy, fixed because headless Claude Code legitimately reports
an http-type MCP server as "pending" at `system.init` and never lists
`mcp__vectr__*` schemas there at all once tool discovery is deferred behind
`ToolSearch`), the honest way to see the fix's effect on an already-burned
leg is to recompute `leg_non_vacuity` against that leg's preserved
`result.json` + `transcript.jsonl` (+ `audit.log`, when present) -- no
daemon, no network, no model call, no re-run, and no cross-import of
`run_leg.py` (see that file's own module docstring: this directory's tools
must give identical answers regardless of which worktree's Python happens to
run them, so `run_leg.py` and `scorer.py` never import each other; only
`scorer.py`/`scenarios.py` are loaded here, the same way `rescore.py` does).

Guarantees mirror `rescore.py`'s own (DEFECT 9), narrowed to the gate:
  - `result.json` is read-only evidence and is NEVER modified. The recomputed
    verdict is written to a NEW sibling file, `result.revalidated.json`.
  - Only the non-vacuity gate is recomputed (`valid`, `invalid_reason`,
    `non_vacuity`); `score`/`metrics`/`cost`/every other field is copied
    verbatim from the original record.
  - A `revalidate_meta` block records a static tool version, the old and new
    `valid` bits, the old and new `invalid_reason` strings (both already
    date-free -- `leg_non_vacuity` never embeds a timestamp in a reason
    string), and which non-vacuity evidence class fired (`vectr_tools_
    evidence` for mcp/mcp-bare arms, `None` for arms where that key does not
    apply). Deliberately carries no wall-clock timestamp of its own (unlike
    `rescore_meta`'s `rescored_utc`) so re-running this tool against the same
    inputs and the same `scorer.py` is fully reproducible/diffable.
  - This script does NOT write to `state.json`/`results.jsonl`/any run-plan
    state; a driver (or a human reviewer) applies state updates only after
    reading the printed verdict.

Usage:
    revalidate.py --revalidate-leg /path/to/trajectory-dir:2
    revalidate.py --revalidate-leg /path/to/trajectory-dir/legs/2/artifacts
    revalidate.py --revalidate-leg /path/to/_shared/leg1/<leg1_id>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
# Same collision-safe by-path loading pattern as run_leg.py/rescore.py/report.py:
# benchmarks/injection_utility/ ships same-named scenarios.py/scorer.py files, so a
# bare `import` would race for one sys.modules slot.
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"
_LONGITUDINAL_SCORER_KEY = "_vectr_eval_longitudinal_scorer"

_TOOL_VERSION = "revalidate.py/1"


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
scorer = _load_local_module(_LONGITUDINAL_SCORER_KEY, "scorer.py")


def resolve_leg_artifacts_dir(spec: str) -> Path:
    """`spec` is either `TRAJECTORY_DIR:K` -- resolved to the standard k>=2 leg
    layout `run_plan.py` writes (`leg_dir = traj_dir / "legs" / str(k)`,
    `result.json` under its `artifacts/` subdirectory -- see `run_leg.py`'s
    `self.artifacts = ... (self.root / "artifacts")` for non-shared legs) --
    or a direct path to a directory that already contains `result.json`
    (covers both a k>=2 leg's own `artifacts/` dir and the flat shared-leg1
    layout, `_shared/leg1/<leg1_id>/`, per `rescore.py`'s own
    `_resolve_artifact_paths` docstring). Never guesses beyond these two
    documented, existing on-disk shapes.
    """
    if ":" in spec:
        traj_dir_str, _, k_str = spec.rpartition(":")
        if k_str.isdigit():
            traj_dir = Path(traj_dir_str).resolve()
            candidate = traj_dir / "legs" / k_str / "artifacts"
            if candidate.is_dir():
                return candidate
            raise SystemExit(
                f"ABORT: TRAJECTORY_DIR:K spec {spec!r} resolved to a "
                f"non-existent leg artifacts dir: {candidate}"
            )
    path = Path(spec).resolve()
    if (path / "result.json").is_file():
        return path
    raise SystemExit(
        f"ABORT: {spec!r} is neither a TRAJECTORY_DIR:K spec nor a directory "
        f"directly containing result.json (looked under: {path})"
    )


def revalidate_leg(artifacts_dir: Path) -> dict[str, Any]:
    """Recompute `scorer.leg_non_vacuity` for one preserved leg from its
    on-disk artifacts alone. Returns the new record (NOT yet written to
    disk); raises `SystemExit` if a required sibling artifact is missing.
    """
    result_path = artifacts_dir / "result.json"
    if not result_path.is_file():
        raise SystemExit(f"ABORT: no result.json under {artifacts_dir}")
    original = json.loads(result_path.read_text())

    transcript_path = artifacts_dir / "transcript.jsonl"
    if not transcript_path.is_file():
        raise SystemExit(
            f"ABORT: no transcript.jsonl under {artifacts_dir} -- "
            "leg_non_vacuity requires the transcript, cannot revalidate"
        )
    events = scorer.load_transcript(transcript_path)

    audit_log = artifacts_dir / "audit.log"
    audit_log_arg = audit_log if audit_log.is_file() else None

    scenario_slug = original.get("scenario")
    scenario = scen.SCENARIOS.get(scenario_slug)
    note_variant_name = original.get("note_variant")
    variant = None
    if scenario is not None and note_variant_name and note_variant_name != "none":
        variant = next(
            (v for v in scenario.note_variants if v.variant == note_variant_name), None
        )

    cost = original.get("cost") or {}
    nv = scorer.leg_non_vacuity(
        arm=original.get("arm"),
        k=original.get("k"),
        events=events,
        notes_count_at_start=original.get("notes_in_store_at_start"),
        restored_manifest_ok=original.get("restored_manifest_ok"),
        audit_log=audit_log_arg,
        audit_since_offset=original.get("audit_offset_after_preflight") or 0,
        proxy_injected=(original.get("proxy_metrics") or {}).get("injected"),
        planted_anchor=original.get("planted_anchor"),
        hook_injection_counts=original.get("hook_injection_counts"),
        transcript_path=transcript_path,
        planted_note_content=(variant.content if variant is not None else None),
        note_id=original.get("planted_note_id"),
        recall_probe_returned_note=original.get("recall_probe_returned_note"),
        mcp_handshake_ok=original.get("mcp_handshake_ok"),
        trail_text_delivered=original.get("trail_text_delivered"),
        agent_returncode=original.get("agent_returncode"),
        is_error=cost.get("is_error"),
        output_tokens=cost.get("output_tokens"),
    )

    revalidated = dict(original)
    revalidated["valid"] = nv["valid"]
    revalidated["invalid_reason"] = nv["invalid_reason"]
    revalidated["non_vacuity"] = nv["non_vacuity"]
    revalidated["revalidate_meta"] = {
        "tool_version": _TOOL_VERSION,
        "original_result_path": str(result_path),
        "old_valid": original.get("valid"),
        "new_valid": nv["valid"],
        "old_invalid_reason": original.get("invalid_reason"),
        "new_invalid_reason": nv["invalid_reason"],
        "flipped": original.get("valid") != nv["valid"],
        "evidence_class": (nv.get("non_vacuity") or {}).get("vectr_tools_evidence"),
    }
    return revalidated


def format_verdict(artifacts_dir: Path, revalidated: dict[str, Any], out_path: Path) -> str:
    meta = revalidated["revalidate_meta"]
    arrow = "->" if meta["old_valid"] != meta["new_valid"] else "=="
    return (
        f"[{revalidated.get('leg_id')}] arm={revalidated.get('arm')} "
        f"k={revalidated.get('k')}: valid {meta['old_valid']} {arrow} "
        f"{meta['new_valid']} (evidence_class={meta['evidence_class']!r}) "
        f"-- wrote {out_path}"
    )


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--revalidate-leg", required=True, metavar="TRAJECTORY_DIR:K",
        help="Either 'TRAJECTORY_DIR:K' or a direct path to a leg's artifacts "
             "directory (anything already containing result.json).",
    )
    ap.add_argument("--json", action="store_true", help="Print the full revalidated record instead of the one-line verdict.")
    return ap


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()
    artifacts_dir = resolve_leg_artifacts_dir(args.revalidate_leg)
    revalidated = revalidate_leg(artifacts_dir)
    out_path = artifacts_dir / "result.revalidated.json"
    out_path.write_text(json.dumps(revalidated, indent=2))
    if args.json:
        print(json.dumps(revalidated, indent=2, default=str))
    else:
        print(format_verdict(artifacts_dir, revalidated, out_path))


if __name__ == "__main__":
    main()
