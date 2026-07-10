"""Metrics: two-phase comparison report and timeline display."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import os
OUTPUT_DIR = os.getenv("POC_OUTPUT_DIR", "/path/to/vectr/benchmarks/django")

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _delta(vanilla: float, vectr: float, lower_is_better: bool = True) -> str:
    if vanilla == 0:
        return "N/A"
    diff_pct = (vectr - vanilla) / vanilla * 100
    sign = "+" if diff_pct > 0 else ""
    if lower_is_better:
        arrow = "↓ better" if diff_pct < -5 else ("↑ worse" if diff_pct > 5 else "≈")
    else:
        arrow = "↑ better" if diff_pct > 5 else ("↓ worse" if diff_pct < -5 else "≈")
    return f"{sign}{diff_pct:.0f}% {arrow}"


def _rediscovery_calls(phase_result) -> int:
    """Count Read+Bash calls before the first Write/Edit in a phase."""
    count = 0
    for ev in phase_result.timeline:
        if any(w in ev.tool_name for w in ("Write", "Edit")):
            break
        if any(r in ev.tool_name for r in ("Read", "Bash", "view")):
            count += 1
    return count


def _vectr_mem_count(phase_result, kind: str) -> int:
    total = 0
    for name, count in phase_result.tool_calls.items():
        if kind in name.lower():
            total += count
    return total


def _fmt(val: object, fmt: str = "") -> str:
    if val is None:
        return "—"
    if fmt == "int":
        return f"{int(val):,}"
    if fmt == "usd":
        return f"${float(val):.4f}"
    if fmt == "s":
        return f"{float(val):.1f}s"
    return str(val)


def _tokens_freed_str(pr) -> str:
    """Avg input-token delta before vs after the first eviction hint (per turn)."""
    tl   = getattr(pr, "token_timeline", [])
    turn = getattr(pr, "evict_hint_turn", None)
    if not tl or turn is None:
        return "—"
    # turn is 1-indexed; iterations list is 0-indexed
    before = tl[:turn - 1]
    after  = tl[turn - 1:]
    if not before or not after:
        return "—"
    def eff(it: dict) -> int:
        return (it.get("input_tokens", 0)
                + it.get("cache_creation_input_tokens", 0)
                + it.get("cache_read_input_tokens", 0))
    avg_before = sum(eff(it) for it in before) / len(before)
    avg_after  = sum(eff(it) for it in after)  / len(after)
    freed = avg_before - avg_after
    sign = "↓" if freed >= 0 else "↑"
    return f"{sign}{abs(freed):,.0f}/turn (at turn {turn})"


def _phase_table(console, phase_label: str, v_phase, vr_phase) -> None:
    from rich.table import Table
    from rich import box

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta",
        title=f"[bold]{phase_label}[/bold]", title_style="bold cyan", title_justify="left",
    )
    table.add_column("Metric",  style="cyan",  no_wrap=True, min_width=38)
    table.add_column("Vanilla", justify="right", min_width=13)
    table.add_column("Vectr",   justify="right", min_width=13)
    table.add_column("Delta",   justify="right", min_width=16)

    def row(metric, v_val, vr_val, delta=""):
        table.add_row(metric, v_val, vr_val, delta)

    v_in   = v_phase.input_tokens   if v_phase else 0
    vr_in  = vr_phase.input_tokens  if vr_phase else 0
    v_out  = v_phase.output_tokens  if v_phase else 0
    vr_out = vr_phase.output_tokens if vr_phase else 0
    v_tot  = v_phase.total_tokens   if v_phase else 0
    vr_tot = vr_phase.total_tokens  if vr_phase else 0

    row("Input tokens",
        _fmt(v_in, "int"), _fmt(vr_in, "int"), _delta(v_in, vr_in))
    row("Output tokens",
        _fmt(v_out, "int"), _fmt(vr_out, "int"), _delta(v_out, vr_out))
    row("Total tokens",
        _fmt(v_tot, "int"), _fmt(vr_tot, "int"), _delta(v_tot, vr_tot))
    row("Cost (USD)",
        _fmt(v_phase.cost_usd if v_phase else None, "usd"),
        _fmt(vr_phase.cost_usd if vr_phase else None, "usd"),
        _delta(v_phase.cost_usd if v_phase else 0, vr_phase.cost_usd if vr_phase else 0))
    row("Wall time",
        _fmt(v_phase.wall_time_s if v_phase else None, "s"),
        _fmt(vr_phase.wall_time_s if vr_phase else None, "s"),
        _delta(v_phase.wall_time_s if v_phase else 0, vr_phase.wall_time_s if vr_phase else 0))
    row("Agent turns",
        str(v_phase.turns) if v_phase else "—",
        str(vr_phase.turns) if vr_phase else "—",
        _delta(v_phase.turns if v_phase else 0, vr_phase.turns if vr_phase else 0))
    v_tcc  = getattr(v_phase,  "tool_call_count", 0) if v_phase  else 0
    vr_tcc = getattr(vr_phase, "tool_call_count", 0) if vr_phase else 0
    row("Tool calls (P5-2)",
        str(v_tcc) if v_tcc else "—",
        str(vr_tcc) if vr_tcc else "—",
        _delta(v_tcc, vr_tcc) if v_tcc else "")
    v_evict  = getattr(v_phase,  "evict_hint_fired", 0) if v_phase  else 0
    vr_evict = getattr(vr_phase, "evict_hint_fired", 0) if vr_phase else 0
    row("Evict hint fired (E5)",
        str(v_evict) if v_evict else "—",
        str(vr_evict) if vr_evict else "—",
        "")
    row("Tokens freed/turn by eviction",
        _tokens_freed_str(v_phase)  if v_phase  else "—",
        _tokens_freed_str(vr_phase) if vr_phase else "—",
        "")

    table.add_section()

    is_phase1 = "1" in phase_label or "Research" in phase_label
    if is_phase1:
        vr_rem  = _vectr_mem_count(vr_phase, "remember") if vr_phase else 0
        vr_snap = _vectr_mem_count(vr_phase, "snapshot")  if vr_phase else 0
        row("vectr_remember calls (P1 store)",
            "—", str(vr_rem) if vr_rem else "—", "")
        row("vectr_snapshot calls (end of P1)",
            "—", str(vr_snap) if vr_snap else "—", "")
    else:
        v_rdis  = _rediscovery_calls(v_phase)  if v_phase  else 0
        vr_rdis = _rediscovery_calls(vr_phase) if vr_phase else 0
        vr_rec  = _vectr_mem_count(vr_phase, "recall") if vr_phase else 0
        row("Re-discovery calls (Read+Bash before 1st write)",
            f"{v_rdis}" if v_rdis else "—",
            f"{vr_rdis}" if vr_rdis else "—",
            _delta(v_rdis, vr_rdis) if v_rdis else "")
        row("vectr_recall calls (resume from notes)",
            "—", str(vr_rec) if vr_rec else "—", "")

    console.print(table)


def print_report(results: list) -> None:
    from rich.console import Console

    console = Console(width=120)
    variants = {r.agent_type for r in results if "vectr" in r.agent_type}
    variant_label = ", ".join(sorted(variants)) or "—"
    console.print("\n[bold cyan]═══ VECTR TWO-PHASE BENCHMARK ═══[/bold cyan]")
    console.print(f"[dim]Core metric: Phase 2 input tokens (re-discovery cost) | vectr variant: {variant_label}[/dim]\n")

    tasks: dict[str, dict[str, object]] = {}
    for r in results:
        tasks.setdefault(r.task_id, {})[r.agent_type] = r

    for task_id, agents in tasks.items():
        v_r  = agents.get("vanilla")
        vr_r = agents.get("vectr")

        console.rule(f"[bold yellow]Task: {task_id}[/bold yellow]")

        _phase_table(
            console,
            "Phase 1 — Research  (vectr stores findings via vectr_remember)",
            v_r.phase1 if v_r else None,
            vr_r.phase1 if vr_r else None,
        )
        console.print()

        _phase_table(
            console,
            "Phase 2 — Implementation  (fresh session; KEY: re-discovery cost)",
            v_r.phase2 if v_r else None,
            vr_r.phase2 if vr_r else None,
        )

        if v_r and vr_r:
            v_p2_in  = v_r.phase2.input_tokens
            vr_p2_in = vr_r.phase2.input_tokens
            p2_sav   = (1 - vr_p2_in / v_p2_in) * 100 if v_p2_in else 0
            console.print(
                f"\n  [bold green]↓ Phase 2 input-token savings: {p2_sav:.1f}%[/bold green]"
                f"  ({v_p2_in:,} → {vr_p2_in:,})"
                f"  |  combined cost: vanilla ${v_r.total_cost:.4f} vs vectr ${vr_r.total_cost:.4f}"
            )
        console.print()

    all_v  = [r for r in results if r.agent_type == "vanilla"]
    all_vr = [r for r in results if r.agent_type == "vectr"]
    if all_v and all_vr:
        tv    = sum(r.total_tokens for r in all_v)
        tvr   = sum(r.total_tokens for r in all_vr)
        cv    = sum(r.total_cost   for r in all_v)
        cvr   = sum(r.total_cost   for r in all_vr)
        tv_p2  = sum(r.phase2.input_tokens for r in all_v)
        tvr_p2 = sum(r.phase2.input_tokens for r in all_vr)
        p2_sav = (1 - tvr_p2 / tv_p2) * 100 if tv_p2 else 0

        all_tok_sav = f"{(1 - tvr / tv) * 100:.1f}%  ({tv:,} → {tvr:,})" if tv else "N/A (vanilla timed out)"
        all_cost_sav = f"{(1 - cvr / cv) * 100:.1f}%  (${cv:.4f} → ${cvr:.4f})" if cv else "N/A (vanilla timed out)"
        console.rule("[bold cyan]Grand Totals[/bold cyan]")
        console.print(
            f"Phase 2 input-token savings : [bold green]{p2_sav:.1f}%[/bold green]"
            f"  ({tv_p2:,} → {tvr_p2:,})\n"
            f"All-phases token savings    : {all_tok_sav}\n"
            f"All-phases cost savings     : {all_cost_sav}"
        )
        console.print()


def print_run3_report(runs: list) -> None:
    """Report for benchmark3: 1 research session + 5 impl sessions per agent."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console(width=120)
    console.print("\n[bold cyan]═══ VECTR BENCHMARK 3 — CPython / 5-feature sprint ═══[/bold cyan]")
    console.print("[dim]1 shared research session + 5 isolated implementation sessions per agent[/dim]\n")

    v_run  = next((r for r in runs if "vanilla" in r.agent_type), None)
    vr_run = next((r for r in runs if "vectr"   in r.agent_type), None)

    # Research phase comparison
    console.rule("[bold yellow]Session 1 — Shared Research Phase[/bold yellow]")
    _phase_table(
        console,
        "Research — vectr stores all 5 areas via vectr_remember",
        v_run.research_phase  if v_run  else None,
        vr_run.research_phase if vr_run else None,
    )
    console.print()

    # Per-task impl comparison
    all_task_ids = []
    if v_run:
        all_task_ids = [p.task_id for p in v_run.impl_phases]
    elif vr_run:
        all_task_ids = [p.task_id for p in vr_run.impl_phases]

    total_v_impl_in  = 0
    total_vr_impl_in = 0

    for task_id in all_task_ids:
        console.rule(f"[bold yellow]Impl Session — {task_id}[/bold yellow]")
        v_impl  = next((p for p in v_run.impl_phases  if p.task_id == task_id), None) if v_run  else None
        vr_impl = next((p for p in vr_run.impl_phases if p.task_id == task_id), None) if vr_run else None
        _phase_table(
            console,
            f"Implementation — {task_id}  (fresh session; KEY: re-discovery cost)",
            v_impl, vr_impl,
        )
        if v_impl:  total_v_impl_in  += v_impl.input_tokens
        if vr_impl: total_vr_impl_in += vr_impl.input_tokens
        console.print()

    # Grand totals
    if v_run and vr_run:
        v_total_cost  = v_run.total_cost
        vr_total_cost = vr_run.total_cost
        p2_sav = (1 - total_vr_impl_in / total_v_impl_in) * 100 if total_v_impl_in else 0

        console.rule("[bold cyan]Grand Totals — 5-Feature Sprint[/bold cyan]")

        t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
        t.add_column("Metric", style="cyan", min_width=42)
        t.add_column("Vanilla", justify="right", min_width=14)
        t.add_column("Vectr",   justify="right", min_width=14)
        t.add_column("Delta",   justify="right", min_width=16)

        t.add_row(
            "Research session cost (paid once)",
            _fmt(v_run.research_phase.cost_usd, "usd"),
            _fmt(vr_run.research_phase.cost_usd, "usd"),
            _delta(v_run.research_phase.cost_usd, vr_run.research_phase.cost_usd),
        )
        t.add_row(
            "Impl sessions input tokens (5 sessions × re-discovery)",
            _fmt(total_v_impl_in, "int"),
            _fmt(total_vr_impl_in, "int"),
            _delta(total_v_impl_in, total_vr_impl_in),
        )
        t.add_row(
            "Total sprint cost (all 6 sessions)",
            _fmt(v_total_cost, "usd"),
            _fmt(vr_total_cost, "usd"),
            _delta(v_total_cost, vr_total_cost),
        )
        console.print(t)
        console.print(
            f"\n  [bold green]↓ Impl input-token savings across 5 sessions: {p2_sav:.1f}%[/bold green]"
            f"  ({total_v_impl_in:,} → {total_vr_impl_in:,})\n"
        )

        if vr_run.vectr_index:
            idx = vr_run.vectr_index
            console.print(
                f"  [dim]Vectr index: {idx.get('files_indexed', '?')} files, "
                f"{idx.get('chunks_indexed', '?')} chunks, "
                f"last index {idx.get('last_index_ms', '?')}ms[/dim]"
            )
        console.print()


def print_timeline(results: list) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console(width=120)
    console.print("\n[bold cyan]═══ TOOL CALL TIMELINES ═══[/bold cyan]\n")

    tasks: dict[str, dict[str, object]] = {}
    for r in results:
        tasks.setdefault(r.task_id, {})[r.agent_type] = r

    for task_id, agents in tasks.items():
        for agent_type in ("vanilla", "vectr"):
            r = agents.get(agent_type)
            if r is None:
                continue
            for phase_num, phase_r in ((1, r.phase1), (2, r.phase2)):
                if not phase_r.timeline:
                    continue

                color = "green" if agent_type == "vectr" else "red"
                console.print(
                    f"[bold yellow]{task_id}[/bold yellow] / "
                    f"[bold {color}]{agent_type}[/bold {color}] / "
                    f"phase {phase_num}"
                )

                table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
                table.add_column("T+",    style="dim",  no_wrap=True, width=7)
                table.add_column("Dur",   style="dim",  no_wrap=True, width=6)
                table.add_column("Turn",  style="dim",  no_wrap=True, width=5)
                table.add_column("Tool",  style="cyan", no_wrap=True, width=30)
                table.add_column("Input", style="white", no_wrap=False, max_width=50)
                table.add_column("Chars", style="dim",  no_wrap=True, width=10)

                for ev in phase_r.timeline:
                    table.add_row(
                        f"{ev.elapsed_s:.1f}s",
                        f"{ev.duration_s:.1f}s",
                        str(ev.turn),
                        ev.tool_name,
                        ev.input_summary,
                        f"{ev.result_chars:,}c" if ev.result_chars else "—",
                    )

                console.print(table)

                tool_counts: dict[str, int] = {}
                total_chars = 0
                for ev in phase_r.timeline:
                    tool_counts[ev.tool_name] = tool_counts.get(ev.tool_name, 0) + 1
                    total_chars += ev.result_chars
                console.print(
                    f"  {len(phase_r.timeline)} calls | {total_chars:,} chars | "
                    + ", ".join(f"{n}×{c}" for n, c in sorted(tool_counts.items()))
                )
                console.print()


def print_answers(results: list) -> None:
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.panel import Panel

    console = Console(width=120)
    console.print("\n[bold cyan]═══ PHASE 2 OUTPUTS (code written by each agent) ═══[/bold cyan]\n")

    tasks: dict[str, dict[str, object]] = {}
    for r in results:
        tasks.setdefault(r.task_id, {})[r.agent_type] = r

    for task_id, agents in tasks.items():
        console.rule(f"[bold yellow]{task_id}[/bold yellow]")
        for agent_type in ("vanilla", "vectr"):
            r = agents.get(agent_type)
            if r is None:
                continue
            answer = r.phase2.answer.strip()
            color  = "green" if agent_type == "vectr" else "red"
            label  = f"[bold {color}]{agent_type}[/bold {color}] — Phase 2 output ({len(answer):,} chars)"
            if not answer:
                console.print(f"{label}: [dim](empty)[/dim]")
                continue
            preview = answer[:1200]
            if len(answer) > 1200:
                preview += f"\n\n… [{len(answer) - 1200:,} more chars — see saved answer file]"
            console.print(Panel(preview, title=label, border_style=color, expand=True))
        console.print()


# ---------------------------------------------------------------------------
# Serialization helpers (POC v2)
# ---------------------------------------------------------------------------

def _phase_dict(pr) -> dict:
    from report import _rediscovery_calls, _vectr_mem_count
    return {
        "task_id":               pr.task_id,
        "agent_type":            pr.agent_type,
        "phase":                 pr.phase,
        "total_tokens":          pr.total_tokens,
        "input_tokens":          pr.input_tokens,
        "base_input_tokens":     pr.base_input_tokens,
        "cache_creation_tokens": pr.cache_creation_tokens,
        "cache_read_tokens":     pr.cache_read_tokens,
        "output_tokens":         pr.output_tokens,
        "turns":                 pr.turns,
        "wall_time_s":           pr.wall_time_s,
        "cost_usd":              pr.cost_usd,
        "tool_calls":            pr.tool_calls,
        "error":                 pr.error,
        "answer":                pr.answer,
        "answer_length":         len(pr.answer),
        "answer_file_chars":     pr.answer_file_chars,
        "answer_files":          pr.answer_files,
        "file_diff":             pr.file_diff[:4000] if pr.file_diff else "",
        "rediscovery_calls":     _rediscovery_calls(pr),
        "vectr_remember":        _vectr_mem_count(pr, "remember"),
        "vectr_recall":          _vectr_mem_count(pr, "recall"),
        "vectr_snapshot":        _vectr_mem_count(pr, "snapshot"),
        "evict_hint_fired":      getattr(pr, "evict_hint_fired", 0),
        "evict_hint_turn":       getattr(pr, "evict_hint_turn", None),
        "token_timeline":        getattr(pr, "token_timeline", []),
        "tool_call_count":       getattr(pr, "tool_call_count", sum(pr.tool_calls.values())),
        "subagent_calls":        getattr(pr, "subagent_calls", 0),
        "prompt_length":         len(pr.prompt),
        "timeline": [
            {
                "elapsed_s":    ev.elapsed_s,
                "duration_s":   ev.duration_s,
                "turn":         ev.turn,
                "tool_name":    ev.tool_name,
                "input_summary": ev.input_summary,
                "full_input":   ev.full_input,
                "result_chars": ev.result_chars,
                "full_result":  ev.full_result,
            }
            for ev in pr.timeline
        ],
    }


def save_results(results: list, prompt_variant: str = "additive") -> Path:
    output_dir = Path(os.getenv("POC_OUTPUT_DIR", OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"poc_results_{ts}_{prompt_variant}.json"

    data = [
        {
            "task_id":        r.task_id,
            "agent_type":     r.agent_type,
            "prompt_variant": prompt_variant if "vectr" in r.agent_type else "n/a",
            "total_tokens":   r.total_tokens,
            "total_cost":     r.total_cost,
            "phase1":         _phase_dict(r.phase1),
            "phase2":         _phase_dict(r.phase2),
        }
        for r in results
    ]
    path.write_text(json.dumps(data, indent=2))
    logger.info("Results saved to %s", path)

    for r in results:
        _save_phase_files(output_dir, r.phase2, label="p2")
        _save_phase_files(output_dir, r.phase1, label="p1")

    return path


def save_run3_results(runs: list, prompt_variant: str = "additive", run_ts: str | None = None) -> Path:
    output_dir = Path(os.getenv("POC_OUTPUT_DIR", "/path/to/vectr/benchmarks/cpython"))
    output_dir.mkdir(parents=True, exist_ok=True)
    ts   = run_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"run3_{ts}_{prompt_variant}.json"

    data = []
    for run in runs:
        data.append({
            "run_id":         run.run_id,
            "codebase":       run.codebase,
            "agent_type":     run.agent_type,
            "prompt_variant": prompt_variant if "vectr" in run.agent_type else "n/a",
            "total_cost":     run.total_cost,
            "total_tokens":   run.total_tokens,
            "vectr_index":    run.vectr_index,
            "research_phase": _phase_dict(run.research_phase),
            "impl_phases":    [_phase_dict(p) for p in run.impl_phases],
        })

    path.write_text(json.dumps(data, indent=2))
    logger.info("Results saved to %s", path)

    # Save vectr_index separately for quick inspection
    for run in runs:
        if run.vectr_index:
            idx_path = output_dir / "vectr_index.json"
            idx_path.write_text(json.dumps(run.vectr_index, indent=2))

    # Save research phase answers and diffs
    for run in runs:
        agent_slug = run.agent_type.replace(":", "_")
        _save_phase_files(output_dir, run.research_phase, label="research", agent_slug=agent_slug)
        for impl in run.impl_phases:
            _save_phase_files(output_dir, impl, label=f"impl_{impl.task_id}", agent_slug=agent_slug)

    return path


# ---------------------------------------------------------------------------
# Run 4 report + serialization
# ---------------------------------------------------------------------------

def _vt_count(tr, tool: str) -> int:
    """Server-side vectr tool count (all agents) when available; parent-only fallback."""
    all_counts = getattr(tr, "vectr_tool_calls_all", {})
    if all_counts:
        return all_counts.get(tool, 0)
    return tr.tool_calls.get(f"mcp__vectr__{tool}", 0)


def _rediscovery_calls_r4(tr) -> int:
    """Read+Bash calls before the first Write/Edit in a TaskResult timeline."""
    count = 0
    for ev in tr.timeline:
        if any(w in ev.tool_name for w in ("Write", "Edit")):
            break
        if any(r in ev.tool_name for r in ("Read", "Bash", "view")):
            count += 1
    return count


def _task_result_dict(tr) -> dict:
    vts = tr.vectr_tool_stats if hasattr(tr, "vectr_tool_stats") else {}
    return {
        "task_id":               tr.task_id,
        "task_num":              tr.task_num,
        "agent_type":            tr.agent_type,
        "total_tokens":          tr.total_tokens,
        "input_tokens":          tr.input_tokens,
        "base_input_tokens":     tr.base_input_tokens,
        "cache_creation_tokens": tr.cache_creation_tokens,
        "cache_read_tokens":     tr.cache_read_tokens,
        "output_tokens":         tr.output_tokens,
        "turns":                 tr.turns,
        "wall_time_s":           tr.wall_time_s,
        "cost_usd":              tr.cost_usd,
        "tool_calls":            tr.tool_calls,
        "tool_call_count":       tr.tool_call_count,
        "error":                 tr.error,
        "answer":                tr.answer,
        "answer_length":         len(tr.answer),
        "answer_file_chars":     tr.answer_file_chars,
        "answer_files":          tr.answer_files,
        "file_diff":             tr.file_diff[:4000] if tr.file_diff else "",
        "notes_count_before":    tr.notes_count_before,
        "notes_count_after":     tr.notes_count_after,
        "vectr_recall_fired":    tr.vectr_recall_fired,
        "vectr_tool_calls_all":  getattr(tr, "vectr_tool_calls_all", {}),
        # Server-side counts (parent + sub-agents) when available; else parent-only fallback
        "vectr_remember":        _vt_count(tr, "vectr_remember"),
        "vectr_recall":          _vt_count(tr, "vectr_recall"),
        "vectr_search":          _vt_count(tr, "vectr_search"),
        "vectr_locate":          _vt_count(tr, "vectr_locate"),
        "vectr_trace":           _vt_count(tr, "vectr_trace"),
        "vectr_snapshot":        _vt_count(tr, "vectr_snapshot"),
        "vectr_tool_stats":      vts,
        "rediscovery_calls":     _rediscovery_calls_r4(tr),
        "evict_hint_fired":      tr.evict_hint_fired,
        "evict_hint_turn":       tr.evict_hint_turn,
        "token_timeline":        tr.token_timeline,
        "subagent_calls":        getattr(tr, "subagent_calls", 0),
        "prompt_length":         len(tr.prompt),
        "timeline": [
            {
                "elapsed_s":     ev.elapsed_s,
                "duration_s":    ev.duration_s,
                "turn":          ev.turn,
                "tool_name":     ev.tool_name,
                "input_summary": ev.input_summary,
                "full_input":    ev.full_input,
                "result_chars":  ev.result_chars,
                "full_result":   ev.full_result,
            }
            for ev in tr.timeline
        ],
    }


def print_run4_report(
    runs: list,
    title: str = "VECTR BENCHMARK — single-phase sprint",
    subtitle: str = "Sequential tasks, fresh LLM session per task, workspace accumulates. Vectr notes persist across sessions; vanilla starts cold every time.",
) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console(width=120)
    console.print(f"\n[bold cyan]═══ {title} ═══[/bold cyan]")
    console.print(f"[dim]{subtitle}[/dim]\n")

    v_run  = next((r for r in runs if r.agent_type == "vanilla"), None)
    vr_run = next((r for r in runs if r.agent_type == "vectr"),   None)

    v_tasks  = {t.task_id: t for t in v_run.task_results}  if v_run  else {}
    vr_tasks = {t.task_id: t for t in vr_run.task_results} if vr_run else {}
    all_ids  = list(dict.fromkeys(
        [t.task_id for t in (v_run.task_results if v_run else [])] +
        [t.task_id for t in (vr_run.task_results if vr_run else [])]
    ))

    for task_id in all_ids:
        vt  = v_tasks.get(task_id)
        vrt = vr_tasks.get(task_id)
        num = vt.task_num if vt else (vrt.task_num if vrt else "?")
        console.rule(f"[bold yellow]Task {num}: {task_id}[/bold yellow]")

        t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
        t.add_column("Metric",  style="cyan",  no_wrap=True, min_width=42)
        t.add_column("Vanilla", justify="right", min_width=14)
        t.add_column("Vectr",   justify="right", min_width=14)
        t.add_column("Delta",   justify="right", min_width=16)

        def row(m, v, vr, d=""):
            t.add_row(m, v, vr, d)

        v_in   = vt.input_tokens   if vt  else 0
        vr_in  = vrt.input_tokens  if vrt else 0
        v_out  = vt.output_tokens  if vt  else 0
        vr_out = vrt.output_tokens if vrt else 0

        row("Input tokens",  _fmt(v_in, "int"),  _fmt(vr_in, "int"),  _delta(v_in, vr_in))
        row("Output tokens", _fmt(v_out, "int"), _fmt(vr_out, "int"), _delta(v_out, vr_out))
        row("Total tokens",
            _fmt(v_in + v_out, "int"), _fmt(vr_in + vr_out, "int"),
            _delta(v_in + v_out, vr_in + vr_out))
        vt_sub  = getattr(vt,  "subagent_calls", 0) if vt  else 0
        vrt_sub = getattr(vrt, "subagent_calls", 0) if vrt else 0
        cost_note = ""
        if vt_sub or vrt_sub:
            parts = []
            if vt_sub:  parts.append(f"vanilla: {vt_sub} sub-agents")
            if vrt_sub: parts.append(f"vectr: {vrt_sub} sub-agents")
            cost_note = f"  [cost=total; tokens=parent-only — {', '.join(parts)}]"
        row("Cost (USD)" + cost_note,
            _fmt(vt.cost_usd if vt else None, "usd"),
            _fmt(vrt.cost_usd if vrt else None, "usd"),
            _delta(vt.cost_usd if vt else 0, vrt.cost_usd if vrt else 0))
        row("Wall time",
            _fmt(vt.wall_time_s if vt else None, "s"),
            _fmt(vrt.wall_time_s if vrt else None, "s"),
            _delta(vt.wall_time_s if vt else 0, vrt.wall_time_s if vrt else 0))
        row("Agent turns",
            str(vt.turns) if vt else "—",
            str(vrt.turns) if vrt else "—",
            _delta(vt.turns if vt else 0, vrt.turns if vrt else 0))
        row("Tool calls",
            str(vt.tool_call_count) if vt else "—",
            str(vrt.tool_call_count) if vrt else "—",
            _delta(vt.tool_call_count if vt else 0, vrt.tool_call_count if vrt else 0))

        v_rdis  = _rediscovery_calls_r4(vt)  if vt  else 0
        vr_rdis = _rediscovery_calls_r4(vrt) if vrt else 0
        row("Re-discovery calls (Read+Bash pre-write)",
            str(v_rdis) if v_rdis else "—",
            str(vr_rdis) if vr_rdis else "—",
            _delta(v_rdis, vr_rdis) if v_rdis else "")

        t.add_section()
        # Vectr-specific rows
        row("Notes before session",
            "—",
            str(vrt.notes_count_before) if vrt else "—", "")
        row("Notes after session",
            "—",
            str(vrt.notes_count_after) if vrt else "—", "")
        recall_fired = vrt.vectr_recall_fired if vrt else False
        row("vectr_recall fired",
            "—",
            ("[green]yes[/green]" if recall_fired else "[red]no[/red]") if vrt else "—", "")
        row("vectr_remember calls",
            "—",
            str(_vt_count(vrt, "vectr_remember")) if vrt else "—", "")
        row("vectr_search calls",
            "—",
            str(_vt_count(vrt, "vectr_search")) if vrt else "—", "")
        row("vectr_snapshot calls",
            "—",
            str(_vt_count(vrt, "vectr_snapshot")) if vrt else "—", "")

        console.print(t)

        # Vectr tool latency breakdown
        if vrt and vrt.vectr_tool_stats:
            lt = Table(box=box.SIMPLE, show_header=True, header_style="dim",
                       title="Vectr tool latencies", title_style="dim", title_justify="left")
            lt.add_column("Tool",    style="cyan", min_width=28)
            lt.add_column("Count",   justify="right", min_width=7)
            lt.add_column("Avg ms",  justify="right", min_width=8)
            lt.add_column("Max ms",  justify="right", min_width=8)
            lt.add_column("Min ms",  justify="right", min_width=8)
            for tool, s in sorted(vrt.vectr_tool_stats.items()):
                lt.add_row(
                    tool, str(s["count"]),
                    str(s["avg_ms"]), str(s["max_ms"]), str(s["min_ms"]),
                )
            console.print(lt)

        console.print()

    # Grand totals
    if v_run and vr_run and v_run.task_results and vr_run.task_results:
        console.rule("[bold cyan]Grand Totals — 4-Task Sprint[/bold cyan]")

        gt = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
        gt.add_column("Metric", style="cyan", min_width=42)
        gt.add_column("Vanilla", justify="right", min_width=14)
        gt.add_column("Vectr",   justify="right", min_width=14)
        gt.add_column("Delta",   justify="right", min_width=16)

        def grow(m, v, vr, d=""):
            gt.add_row(m, v, vr, d)

        vc  = v_run.total_cost;   vrc  = vr_run.total_cost
        vt_ = v_run.total_tokens; vrt_ = vr_run.total_tokens
        v_turns  = sum(t.turns for t in v_run.task_results)
        vr_turns = sum(t.turns for t in vr_run.task_results)
        v_tools  = sum(t.tool_call_count for t in v_run.task_results)
        vr_tools = sum(t.tool_call_count for t in vr_run.task_results)
        v_rdis   = sum(_rediscovery_calls_r4(t) for t in v_run.task_results)
        vr_rdis  = sum(_rediscovery_calls_r4(t) for t in vr_run.task_results)
        vr_recall_total   = sum(t.tool_calls.get("mcp__vectr__vectr_recall",   0) for t in vr_run.task_results)
        vr_remember_total = sum(t.tool_calls.get("mcp__vectr__vectr_remember", 0) for t in vr_run.task_results)
        vr_search_total   = sum(t.tool_calls.get("mcp__vectr__vectr_search",   0) for t in vr_run.task_results)

        grow("Total cost",         _fmt(vc, "usd"),   _fmt(vrc, "usd"),   _delta(vc, vrc))
        grow("Total tokens",       _fmt(vt_, "int"),  _fmt(vrt_, "int"),  _delta(vt_, vrt_))
        grow("Total turns",        str(v_turns),      str(vr_turns),      _delta(v_turns, vr_turns))
        grow("Total tool calls",   str(v_tools),      str(vr_tools),      _delta(v_tools, vr_tools))
        grow("Total re-discovery calls",
             str(v_rdis) if v_rdis else "—",
             str(vr_rdis) if vr_rdis else "—",
             _delta(v_rdis, vr_rdis) if v_rdis else "")
        grow("Total vectr_recall fired", "—", str(vr_recall_total), "")
        grow("Total vectr_remember",     "—", str(vr_remember_total), "")
        grow("Total vectr_search",       "—", str(vr_search_total), "")

        console.print(gt)
        console.print()


def save_run4_results(runs: list, run_ts: str | None = None) -> Path:
    output_dir = Path(os.getenv("POC_OUTPUT_DIR", "/path/to/vectr/benchmarks/cpython"))
    output_dir.mkdir(parents=True, exist_ok=True)
    ts   = run_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"run4_{ts}.json"

    data = []
    for run in runs:
        data.append({
            "run_id":       run.run_id,
            "codebase":     run.codebase,
            "agent_type":   run.agent_type,
            "total_cost":   run.total_cost,
            "total_tokens": run.total_tokens,
            "vectr_index":  run.vectr_index,
            "tasks":        [_task_result_dict(t) for t in run.task_results],
        })

    path.write_text(json.dumps(data, indent=2))
    logger.info("Results saved to %s", path)

    # Save per-task answer text, diffs, and prompts
    for run in runs:
        agent_slug = run.agent_type.replace(":", "_")
        for tr in run.task_results:
            label = f"task{tr.task_num}_{tr.task_id}"
            if tr.answer:
                (output_dir / f"answer_{label}_{agent_slug}.txt").write_text(tr.answer)
            if tr.file_diff:
                (output_dir / f"diff_{label}_{agent_slug}.patch").write_text(tr.file_diff)
            if tr.prompt:
                (output_dir / f"prompt_{label}_{agent_slug}.txt").write_text(tr.prompt)

    if any(run.vectr_index for run in runs):
        idx = next(run.vectr_index for run in runs if run.vectr_index)
        (output_dir / "vectr_index.json").write_text(json.dumps(idx, indent=2))

    return path


def save_run_sequential_results(runs: list, run_prefix: str = "run4", run_ts: str | None = None) -> Path:
    """Generic sequential-run save: works for run4, run5 (uv), run6 (tigerbeetle)."""
    output_dir = Path(os.getenv("POC_OUTPUT_DIR", "/path/to/vectr/benchmarks/cpython"))
    output_dir.mkdir(parents=True, exist_ok=True)
    ts   = run_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{run_prefix}_{ts}.json"

    data = []
    for run in runs:
        data.append({
            "run_id":       run.run_id,
            "codebase":     run.codebase,
            "agent_type":   run.agent_type,
            "total_cost":   run.total_cost,
            "total_tokens": run.total_tokens,
            "vectr_index":  run.vectr_index,
            "tasks":        [_task_result_dict(t) for t in run.task_results],
        })

    path.write_text(json.dumps(data, indent=2))
    logger.info("Results saved to %s", path)

    for run in runs:
        agent_slug = run.agent_type.replace(":", "_")
        for tr in run.task_results:
            label = f"task{tr.task_num}_{tr.task_id}"
            if tr.answer:
                (output_dir / f"answer_{label}_{agent_slug}.txt").write_text(tr.answer)
            if tr.file_diff:
                (output_dir / f"diff_{label}_{agent_slug}.patch").write_text(tr.file_diff)
            if tr.prompt:
                (output_dir / f"prompt_{label}_{agent_slug}.txt").write_text(tr.prompt)

    if any(run.vectr_index for run in runs):
        idx = next(run.vectr_index for run in runs if run.vectr_index)
        (output_dir / "vectr_index.json").write_text(json.dumps(idx, indent=2))

    return path


def _save_phase_files(output_dir: Path, pr, label: str, agent_slug: str | None = None) -> None:
    slug = agent_slug or pr.agent_type.replace(":", "_")
    if pr.answer:
        txt_path = output_dir / f"{label}_{slug}.txt"
        txt_path.write_text(pr.answer)
    if pr.file_diff:
        patch_path = output_dir / f"diff_{label}_{slug}.patch"
        patch_path.write_text(pr.file_diff)
    # Also save the prompt for reproducibility
    if pr.prompt:
        prompt_path = output_dir / f"prompt_{label}_{slug}.txt"
        prompt_path.write_text(pr.prompt)
