#!/usr/bin/env python3
"""C18 spike — per-boundary consolidation cost, measured not assumed.

The distillation design left one question open with no published figure:
what does a consolidation pass at a session/compaction boundary actually
COST, and how often would it even have material to work on?

This harness answers both from real artifacts, deterministically and
offline (no model call, no daemon required):

  A. FIRING RATE — how much material exists at a real boundary. Reads a
     READ-ONLY snapshot copy of a real workspace's store (episodes/arcs
     tables) and reports the arc yield per episode. Pair with
     benchmarks/arc_replay.py, which reports the same yield over the 20
     archived transcripts.

  B. PAYLOAD COST — token cost of each candidate boundary payload, using
     the product's own `token_estimate`, so the numbers are the same ones
     the render caps are enforced against.

  C. DETACHED-DISTILLER PRICE — what a spawned per-boundary LLM pass would
     have to read, from the real per-session episode volume. Priced in
     input tokens; vectr itself never makes such a call (zero-inference),
     so this exists to make the rejection quantitative instead of assumed.

Usage:
    ./.venv/bin/python benchmarks/boundary_cost_spike.py [snapshot.sqlite]

The snapshot argument is a COPY of a workspace store. Never point this at a
store a running daemon owns: copy the file first, read the copy.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.trigger_engine import token_estimate  # noqa: E402
from integrations.mcp_server._dispatch import (  # noqa: E402
    _format_arc_block,
    _format_pending_arcs,
)
from integrations.mcp_server._schemas import _DISTILL_RULES_TEXT  # noqa: E402


# --- Candidate boundary payloads -------------------------------------------
# D1 is arc-independent: it fires at every compaction. D2/D3 are arc-gated
# and therefore only ever fire as often as arcs actually form (section A).

D1_PRESERVE_ONLY = (
    "Before this context is replaced: preserve, in the summary, the concrete "
    "things this session LEARNED that are not already written down — commands "
    "that failed and what actually fixed them, environment and build facts "
    "specific to this machine or repo, and conventions discovered by reading "
    "the code. Keep them as specific statements (the command, the flag, the "
    "path), not as a topic list."
)

D2_ARC_SENTENCE = (
    "{n} command-discovery arc(s) recorded this session are still pending "
    "distillation; keep the reasoning about what they mean."
)


def _fmt(n: int) -> str:
    return f"{n:,}"


def section_a(db: Path) -> dict:
    """Firing rate: how much material a real boundary actually has."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        ep_total = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        arc_total = con.execute("SELECT COUNT(*) FROM arcs").fetchone()[0]
        pending = con.execute(
            "SELECT COUNT(*) FROM arcs WHERE distilled_at IS NULL"
        ).fetchone()[0]
        outcomes = dict(
            con.execute(
                "SELECT outcome, COUNT(*) FROM episodes GROUP BY outcome"
            ).fetchall()
        )
        sessions = con.execute(
            "SELECT session_id, COUNT(*) AS c,"
            " (SUM(LENGTH(cmd_raw)) + SUM(LENGTH(stdout_digest))"
            "  + SUM(LENGTH(stderr_digest))) AS chars"
            " FROM episodes GROUP BY session_id ORDER BY c DESC"
        ).fetchall()
        arcs = [
            dict(zip([d[0] for d in con.execute("SELECT * FROM arcs LIMIT 0").description], r))
            for r in con.execute("SELECT * FROM arcs").fetchall()
        ]
    finally:
        con.close()

    classified = ep_total - outcomes.get("unknown", 0)
    print("=" * 78)
    print("A. FIRING RATE — material available at a real boundary")
    print("=" * 78)
    print(f"  episodes captured            : {_fmt(ep_total)}")
    print(f"  arcs formed                  : {_fmt(arc_total)}")
    print(f"  arcs pending distillation    : {_fmt(pending)}")
    if ep_total:
        print(f"  arc yield                    : {arc_total / ep_total * 100:.2f} arcs per 100 episodes")
    print("  episode outcome distribution :")
    for k, v in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        pct = v / ep_total * 100 if ep_total else 0
        print(f"      {k:<14} {_fmt(v):>6}  ({pct:.1f}%)")
    print(f"  outcome CLASSIFIED           : {_fmt(classified)} / {_fmt(ep_total)}"
          f" ({classified / ep_total * 100:.1f}%)" if ep_total else "")
    print("  per-session episode volume   :")
    for sid, c, chars in sessions:
        print(f"      {(sid or '(none)')[:12]:<14} {c:>5} episodes"
              f"  ~{_fmt((chars or 0) // 4):>8} tok of raw episode text")
    return {"episodes": ep_total, "arcs": arc_total, "pending": pending,
            "sessions": sessions, "arc_rows": arcs, "outcomes": outcomes}


def _arc_rows_for_render(raw_arcs: list[dict]) -> list[dict]:
    """Shape stored arc rows like `list_arcs()` output so the PRODUCT's own
    renderer can price them. Failure/success verbs are unavailable in this
    offline view, so each failure is rendered at its realistic width using
    the arc's own recorded mutation axes."""
    rows = []
    for a in raw_arcs:
        mutation = json.loads(a.get("mutation_diff_json") or "{}")
        fail_ids = json.loads(a.get("failure_episode_ids_json") or "[]")
        verb = ""
        for axis in ("arg", "flag"):
            pair = mutation.get(axis)
            if pair and pair[0]:
                verb = str(pair[0][0])
                break
        rows.append({
            "id": a["id"],
            "confidence": a.get("confidence", "normal"),
            "ts": a.get("ts"),
            "cwd": a.get("cwd", ""),
            "failures": [
                {"verb": verb, "outcome": "failure", "markers_matched": []}
                for _ in fail_ids
            ],
            "success": {"verb": verb, "cmd_raw": verb},
            "mutation_diff": mutation,
        })
    return rows


def section_b(state: dict) -> None:
    print()
    print("=" * 78)
    print("B. PAYLOAD COST — candidate boundary payloads, product token_estimate")
    print("=" * 78)

    pending = state["pending"]
    rows = _arc_rows_for_render(
        [a for a in state["arc_rows"] if a.get("distilled_at") is None]
    )

    d1 = D1_PRESERVE_ONLY
    d2 = d1 + "\n" + D2_ARC_SENTENCE.format(n=max(pending, 1))
    arc_blocks = "\n\n".join(_format_arc_block(r) for r in rows) if rows else ""
    d3 = (d2 + "\n\n" + arc_blocks) if arc_blocks else d2
    distill_render = _format_pending_arcs(rows, pending)

    for label, text, gated in [
        ("D1 preserve-only (arc-independent)", d1, "every compaction"),
        ("D2 D1 + arc-count sentence", d2, "arcs_pending > 0"),
        ("D3 D2 + full rendered arcs", d3, "arcs_pending > 0"),
        ("(ref) vectr_distill() full render", distill_render, "on demand"),
        ("(ref) static distiller rules text", _DISTILL_RULES_TEXT, "n/a"),
    ]:
        print(f"  {label:<38} {token_estimate(text):>5} tok   fires: {gated}")

    print()
    print("  NOTE: D1 is the only candidate whose firing rate is independent of")
    print("  arc formation. Section A's arc yield is what D2/D3 are worth.")


def section_c(state: dict) -> None:
    print()
    print("=" * 78)
    print("C. DETACHED-DISTILLER PRICE — what a spawned per-boundary pass reads")
    print("=" * 78)
    rules = token_estimate(_DISTILL_RULES_TEXT)
    print(f"  fixed prompt overhead (rules text)   : {_fmt(rules)} tok")
    print("  per-boundary INPUT tokens if the pass reads that session's episodes:")
    for sid, c, chars in state["sessions"]:
        ep_tok = (chars or 0) // 4
        print(f"      {(sid or '(none)')[:12]:<14} {c:>5} episodes"
              f"  -> {_fmt(ep_tok + rules):>8} tok/boundary")
    heaviest = max(((c or 0), (chars or 0) // 4) for _, c, chars in state["sessions"])
    print()
    print(f"  heaviest observed session: {heaviest[0]} episodes"
          f" = {_fmt(heaviest[1] + rules)} input tok for ONE boundary pass,")
    print("  paid per compaction and again at session end, per developer, per session.")
    print("  vectr makes no such call: distillation judgment stays caller-side.")


def main() -> int:
    if len(sys.argv) > 1:
        db = Path(sys.argv[1])
    else:
        print("usage: boundary_cost_spike.py <snapshot.sqlite>", file=sys.stderr)
        print("  (pass a COPY of a workspace store, never a live daemon's file)",
              file=sys.stderr)
        return 2
    if not db.exists():
        print(f"no such snapshot: {db}", file=sys.stderr)
        return 2
    state = section_a(db)
    section_b(state)
    section_c(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
