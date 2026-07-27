#!/usr/bin/env python3
"""Structural-channel proactive-injection precision harness
(UPG-PROXY-INJECT-PRECISION acceptance harness).

Drives the REAL `VectrService.proactive_context(structural_only=True)` --
the same call the hook/high-frequency channels make -- against a synthetic,
self-contained note store built entirely by this repo (see
synthetic_store.py). No live daemon, no network, no LLM, no dependency on
any private note database: every run builds its own store from scratch at
`--db` and reports on it.

`structural_only=True` isolates the measurement to the structural (M1)
channel this UPG item's three levers changed -- the matcher runs the
semantic/code channels unconditionally too, but this harness's whole
purpose is measuring the structural channel specifically, so it asks the
gate to keep only structural candidates (a real, production-exposed call
parameter, used by the high-frequency hook channels precisely because
"only exact matches are cheap enough to be worth the budget" -- see
agent/proactive/gate.py's `select()` docstring).

Every reported metric is MECHANICAL: computed by looking up each returned
anchor_id in the ground-truth dict `synthetic_store.build_synthetic_store()`
handed back (this generator's own bookkeeping of what it planted), or by
reading `anchor_ids`/`scores` straight off the API return -- never by
parsing rendered context text (which a sibling lane's envelope-format change
will alter) and never by asking any judge, human or automated, to assess
relevance at run time.

Usage:
    python3 benchmarks/proactive_injection_precision/run_harness.py \\
        --db /Users/swapnanilsaha/dev/vectr/tmp/proactive-precision-db

`--db` must be a fresh or harness-owned directory -- it is used verbatim as
VECTR_DB_DIR, so pick a path this run may freely create/reuse (the fixture
rule: only ever point this at something under vectr/tmp/, never a real
workspace's cache).

Exit code: 0 always (this is a MEASUREMENT tool, not a pass/fail gate --
the anti-gaming floor from the UPG-PROXY-INJECT-PRECISION brief is a
reporting threshold the caller/reviewer checks against the printed numbers,
never something this script tunes towards or enforces by exiting non-zero).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from synthetic_store import build_synthetic_store  # noqa: E402


def _build_service(db_dir: Path, workspace: str):
    os.environ["VECTR_DB_DIR"] = str(db_dir)
    os.environ["VECTR_PROACTIVE"] = "1"
    os.environ.pop("VECTR_AUDIT_LOG", None)
    os.environ.pop("VECTR_BIND_HOST", None)  # loopback default -> proactive allowed
    with patch("integrations.vscode_bridge.configure_all"), patch(
        "integrations.workspace_detect.find_workspace_root", return_value=workspace
    ):
        from app.service import VectrService

        return VectrService(workspace_root=workspace, memory_only=True)


def _tier_for_score(score: float) -> str:
    from agent.config import (
        PROACTIVE_STRUCTURAL_SCORE_DECLARED_ANCHOR,
        PROACTIVE_STRUCTURAL_SCORE_GOTCHA_MENTION,
        PROACTIVE_STRUCTURAL_SCORE_MENTION,
    )

    if math.isclose(score, PROACTIVE_STRUCTURAL_SCORE_DECLARED_ANCHOR, abs_tol=1e-6):
        return "A_declared_anchor"
    if math.isclose(score, PROACTIVE_STRUCTURAL_SCORE_GOTCHA_MENTION, abs_tol=1e-6):
        return "B_gotcha_mention"
    if math.isclose(score, PROACTIVE_STRUCTURAL_SCORE_MENTION, abs_tol=1e-6):
        return "C_mention"
    return f"unknown(score={score})"


def _note_id_from_anchor(anchor_id: str) -> int | None:
    if not anchor_id.startswith("note:"):
        return None
    try:
        return int(anchor_id[len("note:"):])
    except ValueError:
        return None


def run(db_dir: Path) -> dict:
    from agent.config import PROACTIVE_MAX_ITEMS_PER_EVENT

    workspace = "/repo"
    svc = _build_service(db_dir, workspace)
    gt = build_synthetic_store(svc)

    per_window = []
    tier_counts: dict[str, int] = {}
    kind_counts_injected: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    assertions = 0
    deterrents = 0
    strict_tp = 0
    charitable_tp = 0
    total_injected = 0

    for i, path in enumerate(gt.window_files):
        out = svc.proactive_context(
            text="", file_paths=[path], session_id="", channel="proxy",
            structural_only=True,
        )
        item_count = out["item_count"]
        total_injected += item_count
        rows = []
        for anchor_id, score in zip(out["anchor_ids"], out["scores"]):
            nid = _note_id_from_anchor(anchor_id)
            category = gt.category_by_id.get(nid, "UNKNOWN")
            note_kind = gt.kind_by_id.get(nid, "UNKNOWN")
            note_window = gt.window_by_id.get(nid, None)
            tier = _tier_for_score(score)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            kind_counts_injected[note_kind] = kind_counts_injected.get(note_kind, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1

            is_strict_tp = category == "gold" and note_window == i
            is_charitable_tp = is_strict_tp or (category == "weak_offtopic" and note_window == i)
            if is_strict_tp:
                strict_tp += 1
            if is_charitable_tp:
                charitable_tp += 1
            if nid in gt.revoked_ids:
                deterrents += 1
            else:
                assertions += 1
            rows.append(
                dict(anchor_id=anchor_id, note_id=nid, score=score, tier=tier,
                     kind=note_kind, category=category, window=note_window,
                     strict_tp=is_strict_tp, charitable_tp=is_charitable_tp)
            )
        per_window.append(dict(window=i, path=path, item_count=item_count, items=rows))

    windows_fired = sum(1 for w in per_window if w["item_count"] > 0)
    slots_available = len(gt.window_files) * PROACTIVE_MAX_ITEMS_PER_EVENT
    strict_precision = strict_tp / total_injected if total_injected else 0.0
    charitable_precision = charitable_tp / total_injected if total_injected else 0.0

    return dict(
        per_window=per_window,
        windows_fired=windows_fired,
        windows_total=len(gt.window_files),
        items_injected=total_injected,
        slots_available=slots_available,
        strict_precision=strict_precision,
        charitable_precision=charitable_precision,
        strict_tp=strict_tp,
        charitable_tp=charitable_tp,
        tier_counts=tier_counts,
        kind_counts_injected=kind_counts_injected,
        category_counts=category_counts,
        assertions=assertions,
        deterrents=deterrents,
        store_total_notes=gt.total_notes,
        store_task_kind_notes=gt.task_kind_notes,
        store_task_kind_fraction=(gt.task_kind_notes / gt.total_notes if gt.total_notes else 0.0),
    )


def _print_report(r: dict) -> None:
    print("=== UPG-PROXY-INJECT-PRECISION structural-channel harness (SYNTHETIC store) ===")
    print()
    print(f"synthetic store: {r['store_total_notes']} notes, "
          f"{r['store_task_kind_notes']} kind=task "
          f"({100.0 * r['store_task_kind_fraction']:.1f}%)")
    print()
    print(f"windows firing: {r['windows_fired']}/{r['windows_total']}")
    print(f"items injected: {r['items_injected']} / slots available: {r['slots_available']}")
    print()
    print("evidence-tier split (of injected items):")
    for tier, n in sorted(r["tier_counts"].items()):
        print(f"  {tier:20s} {n}")
    print()
    print("kind mix (of injected items):")
    for kind, n in sorted(r["kind_counts_injected"].items()):
        print(f"  {kind:12s} {n}")
    print()
    print("ground-truth category split (of injected items):")
    for cat, n in sorted(r["category_counts"].items()):
        print(f"  {cat:16s} {n}")
    print()
    print(f"assertions vs deterrents: {r['assertions']} assertions, {r['deterrents']} deterrents")
    print()
    print(f"STRICT precision (gold-only, exact window match): "
          f"{r['strict_tp']}/{r['items_injected']} = {100.0 * r['strict_precision']:.1f}%")
    print(f"CHARITABLE precision (gold + weak-but-eligible, exact window match): "
          f"{r['charitable_tp']}/{r['items_injected']} = {100.0 * r['charitable_precision']:.1f}%")
    print()
    print("(SYNTHETIC data -- these numbers describe this generated store, not any real")
    print(" workspace. The 55% strict / 12-of-20-windows figures from the UPG brief are an")
    print(" anti-gaming FLOOR to report against, not a target to tune towards.)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db", required=True,
        help="VECTR_DB_DIR to build the synthetic store under (must be a fresh or "
             "harness-owned path, e.g. under vectr/tmp/).",
    )
    args = ap.parse_args()
    db_dir = Path(args.db)
    db_dir.mkdir(parents=True, exist_ok=True)
    result = run(db_dir)
    _print_report(result)


if __name__ == "__main__":
    main()
