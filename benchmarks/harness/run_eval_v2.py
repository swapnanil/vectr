#!/usr/bin/env python3
"""Eval v2 runner — within-session /compact across arms, on one corpus.

Each (arm, rep) is a self-contained experiment: reset the corpus to a clean
checkout, configure the arm, run explore -> /compact -> implement in one
session, score the held-out test, record metrics. Results stream to a JSON
file so a run can resume across 5-hour quota windows.

Example (cheap pipeline smoke):
    python run_eval_v2.py --arms C,D,A2 --reps 1 --model haiku \\
        --working-dir /tmp/poc-django --port 8792 \\
        --python-bin /tmp/poc-django-venv/bin/python --out results/smoke.json

Live runs consume the Claude Code subscription quota.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from tasks import DJANGO_TASKS
from scoring import DJANGO_EXEC_SPECS
from eval_v2 import (
    ARMS,
    GUARDRAIL_IRRELEVANT_MARKERS,
    GUARDRAIL_IRRELEVANT_NOTES,
    injection_precision,
    net_token_delta,
    run_compact_scenario,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_eval_v2")

# Task-relevant markers for injection precision on the MoneyField task.
_RELEVANT_MARKERS = ["MoneyField", "deconstruct", "get_prep_value", "Field",
                     "cents", "Decimal", "currency"]

# BV-MODEL-PIN — Claude Code's own "opus"/"sonnet" aliases can silently point
# at a stale snapshot as new model versions ship (confirmed live: a run with
# `--model opus` resolved to claude-opus-4-7, not the intended 4-8, so the
# whole "Opus" arm of a headline run was on the wrong model). Pin explicit
# full IDs here so the transcript's `message.model` always matches intent.
_MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
}


def _resolve_model_id(model: str) -> str:
    """Resolve a `--model` alias to its explicit full Claude model ID.

    A full ``claude-*`` ID is used verbatim (the caller already pinned it).
    An alias in `_MODEL_ALIASES` resolves to the pinned ID. Anything else
    (e.g. "haiku", not pinned here) passes through unchanged — Claude Code
    resolves it itself.
    """
    if model.startswith("claude-"):
        return model
    return _MODEL_ALIASES.get(model, model)


def _reset_repo(working_dir: str) -> None:
    """Return the corpus to a pristine checkout between arms (drops the prior
    arm's impl files, NOTES.md, and any vectr IDE config)."""
    for cmd in (["git", "reset", "--hard", "--quiet"], ["git", "clean", "-fdq"]):
        subprocess.run(cmd, cwd=working_dir, check=False,
                       capture_output=True, text=True)


def _summarize(res, *, guardrail: bool) -> dict:
    """Compact, JSON-serialisable per-run metrics (no big timelines/diffs)."""
    es = res.exec_score
    out = {
        "arm": res.arm_id,
        "task": res.task_id,
        "compacted": res.compacted,
        "error": res.error,
        "wall_s": round(res.wall_time_s, 1),
        "cost_usd": round(res.total_cost, 4),
        "research_tokens": res.research.total_tokens,
        "impl_tokens": res.impl.total_tokens,
        "total_tokens": res.total_tokens,
        "research_turns": res.research.turns,
        "impl_turns": res.impl.turns,
        "notes_before": res.notes_count_before,
        "notes_after": res.notes_count_after,
        "vectr_calls": res.vectr_tool_calls_all,
        "research_injections": res.research_injection.get("injections", 0),
        "impl_injections": res.impl_injection.get("injections", 0),
        "injected_chars": (res.research_injection.get("injected_chars", 0)
                           + res.impl_injection.get("injected_chars", 0)),
        "compaction_summary_chars": res.compaction_summary_chars,
        "answer_files": [Path(f).name for f in res.answer_files],
        "exec_ran": bool(es and es.ran),
        "exec_passed": es.passed if es else None,
        "exec_total": es.total if es else None,
        "exec_success": bool(es and es.success),
    }
    # Archive the pytest tail whenever a run ran but didn't fully pass, so a
    # failing held-out check is diagnosable from the results JSON without an
    # expensive re-run. Passing runs stay lean.
    if es and es.ran and not es.success and es.log_tail:
        out["exec_log"] = es.log_tail
    if guardrail:
        injected = (res.research_injection.get("injected_text", "")
                    + "\n" + res.impl_injection.get("injected_text", ""))
        out["injection_precision"] = injection_precision(
            injected, _RELEVANT_MARKERS, GUARDRAIL_IRRELEVANT_MARKERS)
    return out


def _load_done(out_path: Path) -> list[dict]:
    if out_path.exists():
        try:
            return json.loads(out_path.read_text()).get("runs", [])
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(out_path: Path, runs: list[dict], meta: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"meta": meta, "runs": runs}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Eval v2 /compact runner")
    ap.add_argument("--arms", default="C,D,A2", help="comma list from A1,A2,B,C,D")
    ap.add_argument("--task", default="custom_field", help="DJANGO_TASKS id")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--model", default="opus",
                    help="Claude Code model alias ('opus'/'sonnet', pinned to an explicit "
                         "full ID — see BV-MODEL-PIN) or a full claude-* id/other alias "
                         "used verbatim")
    ap.add_argument("--working-dir", default="/tmp/poc-django")
    ap.add_argument("--port", type=int, default=8792, help="vectr daemon port")
    ap.add_argument("--python-bin", default=sys.executable,
                    help="python with the corpus importable (for execution scoring)")
    ap.add_argument("--codebase-desc", default="The Django source tree")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--guardrail", action="store_true",
                    help="pre-seed the note store with off-topic notes (must-not-regress)")
    ap.add_argument("--no-score", action="store_true", help="skip execution scoring")
    ap.add_argument("--out", default="results/eval_v2.json")
    ap.add_argument("--resume", action="store_true", help="skip (arm,rep) already in --out")
    args = ap.parse_args()
    args.model = _resolve_model_id(args.model)  # BV-MODEL-PIN

    task = next((t for t in DJANGO_TASKS if t.id == args.task), None)
    if task is None:
        ap.error(f"unknown task {args.task!r}; have {[t.id for t in DJANGO_TASKS]}")
    arm_ids = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arm_ids:
        if a not in ARMS:
            ap.error(f"unknown arm {a!r}; have {list(ARMS)}")
    exec_spec = None if args.no_score else DJANGO_EXEC_SPECS.get(args.task)
    seed_notes = GUARDRAIL_IRRELEVANT_NOTES if args.guardrail else None

    out_path = Path(args.out)
    runs = _load_done(out_path) if args.resume else []
    done = {(r["arm"], r.get("rep")) for r in runs}
    meta = {"task": args.task, "model": args.model, "arms": arm_ids,
            "reps": args.reps, "guardrail": args.guardrail,
            "scored": exec_spec is not None}

    for rep in range(args.reps):
        for arm_id in arm_ids:
            if args.resume and (arm_id, rep) in done:
                logger.info("skip %s rep%d (already done)", arm_id, rep)
                continue
            _reset_repo(args.working_dir)
            res = run_compact_scenario(
                ARMS[arm_id], task, args.working_dir,
                codebase_desc=args.codebase_desc, vectr_port=args.port,
                model=args.model, max_turns=args.max_turns, timeout_s=args.timeout,
                exec_spec=exec_spec, python_bin=args.python_bin,
                seed_notes=seed_notes,
            )
            row = {"rep": rep, **_summarize(res, guardrail=args.guardrail)}
            runs.append(row)
            _save(out_path, runs, meta)  # incremental: resumable

    _reset_repo(args.working_dir)
    _print_table(runs)
    logger.info("results → %s", out_path)


def _print_table(runs: list[dict]) -> None:
    if not runs:
        return
    print("\n=== eval v2 results ===")
    hdr = (f"{'arm':<4} {'rep':>3} {'cmpct':>5} {'res_tok':>8} {'impl_tok':>8} "
           f"{'tot_tok':>8} {'inj':>4} {'notes':>6} {'exec':>10} {'cost$':>7}")
    print(hdr)
    print("-" * len(hdr))
    totals: dict[str, list[int]] = {}
    for r in runs:
        exec_s = (f"{r['exec_passed']}/{r['exec_total']}"
                  if r["exec_ran"] else ("err" if not r["compacted"] else "—"))
        print(f"{r['arm']:<4} {r['rep']:>3} {str(r['compacted'])[:5]:>5} "
              f"{r['research_tokens']:>8} {r['impl_tokens']:>8} {r['total_tokens']:>8} "
              f"{r['impl_injections']:>4} {r['notes_before']}->{r['notes_after']:<3} "
              f"{exec_s:>10} {r['cost_usd']:>7.4f}")
        totals.setdefault(r["arm"], []).append(r["total_tokens"])
    # net token delta vs A2 (or first arm) on mean totals
    means = {a: sum(v) // len(v) for a, v in totals.items()}
    base = "A2" if "A2" in means else next(iter(means))
    delta = net_token_delta(means, base)
    if delta:
        print(f"\nnet token delta vs {base} (mean total tokens, negative = cheaper):")
        for a, d in sorted(delta.items()):
            print(f"  {a}: {d:+d}")


if __name__ == "__main__":
    main()
