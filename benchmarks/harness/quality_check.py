"""
P5-1 — Layer 1 quality checker: deterministic structural checks on impl diffs.

Zero API calls. Zero model inference. Each check is a regex pattern applied to
the diff produced by an implementation session. Score: 0/1 per check; total is
checks_passed / checks_total.

Design rationale (from spec):
- SWE-bench is execution-only — no static shortcut.
- CodeBLEU correlates poorly with correctness.
- Tree-sitter-c structural checks (PyMethodDef entry, function signature,
  sentinel) are deterministic and zero-cost.
- GCC Python Plugin cpychecker validates PyMethodDef flag-to-signature
  statically (out of scope here — just pattern checks).

Checks are task-specific. CPython C tasks require different signals than
CPython Python-output tasks (debug tasks write Python test scripts, not C).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class QualityScore:
    task_id: str
    agent_type: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def summary(self) -> str:
        lines = [f"[{self.task_id}] [{self.agent_type}] {self.passed}/{self.total} checks passed"]
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name}" + (f" — {c.detail}" if c.detail else ""))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

def _match(pattern: str, text: str, flags: int = re.MULTILINE) -> tuple[bool, str]:
    m = re.search(pattern, text, flags)
    return bool(m), (m.group(0)[:80] if m else "")


# ---------------------------------------------------------------------------
# Per-task check definitions
# ---------------------------------------------------------------------------

def _checks_feature_dict_pop_last(diff: str) -> list[CheckResult]:
    results = []

    ok, matched = _match(r'dict_pop_last', diff)
    results.append(CheckResult(
        "function name dict_pop_last present", ok,
        matched or "not found",
    ))

    ok, matched = _match(r'PyMethodDef.*dict_pop_last|"pop_last".*dict_pop_last', diff, re.DOTALL)
    results.append(CheckResult(
        "PyMethodDef entry for pop_last", ok,
        matched[:60] if matched else "PyMethodDef entry not found",
    ))

    ok, matched = _match(r'PyTuple_New|PyTuple_Pack', diff)
    results.append(CheckResult(
        "returns a tuple (PyTuple_New or PyTuple_Pack)", ok,
        matched or "no tuple construction found",
    ))

    ok, matched = _match(r'KeyError|PyErr_SetString.*KeyError|PyErr_Format.*KeyError', diff)
    results.append(CheckResult(
        "raises KeyError on empty dict", ok,
        matched or "KeyError not raised",
    ))

    ok, matched = _match(r'ma_used\s*==\s*0|ma_used\s*<\s*1|len.*==.*0|empty', diff, re.IGNORECASE)
    results.append(CheckResult(
        "empty-dict guard present", ok,
        matched or "no empty check found",
    ))

    return results


def _checks_cross_session_set_cartesian(diff: str) -> list[CheckResult]:
    results = []

    ok, matched = _match(r'cartesian_product', diff)
    results.append(CheckResult(
        "function name cartesian_product present", ok,
        matched or "not found",
    ))

    ok, matched = _match(r'"cartesian_product"\s*,.*cartesian|PyMethodDef.*cartesian', diff, re.DOTALL)
    results.append(CheckResult(
        "PyMethodDef entry for cartesian_product", ok,
        matched[:60] if matched else "PyMethodDef entry not found",
    ))

    ok, matched = _match(r'frozenset|make_new_set.*Frozen|FrozenSet', diff, re.IGNORECASE)
    results.append(CheckResult(
        "result is a frozenset", ok,
        matched or "frozenset not found in diff",
    ))

    ok, matched = _match(r'PyTuple_New|PyTuple_Pack', diff)
    results.append(CheckResult(
        "tuple construction for (a,b) pairs", ok,
        matched or "no tuple construction found",
    ))

    ok, matched = _match(r'Py_INCREF|Py_DECREF|Py_XDECREF', diff)
    results.append(CheckResult(
        "reference counting (Py_INCREF/Py_DECREF)", ok,
        matched or "no refcount operations found",
    ))

    return results


def _checks_cross_session_bytes_find_all(diff: str) -> list[CheckResult]:
    results = []

    ok, matched = _match(r'find_all', diff)
    results.append(CheckResult(
        "function name find_all present", ok,
        matched or "not found",
    ))

    ok, matched = _match(r'"find_all"\s*,.*bytes_find_all|PyMethodDef.*find_all', diff, re.DOTALL)
    results.append(CheckResult(
        "PyMethodDef entry for find_all", ok,
        matched[:60] if matched else "PyMethodDef entry not found",
    ))

    ok, matched = _match(r'Py_buffer|PyBUF_|y\*', diff)
    results.append(CheckResult(
        "Py_buffer acquisition", ok,
        matched or "Py_buffer not found",
    ))

    ok, matched = _match(r'PyBuffer_Release', diff)
    results.append(CheckResult(
        "PyBuffer_Release on exit", ok,
        matched or "PyBuffer_Release not found — possible leak",
    ))

    ok, matched = _match(r'PyList_New|PyList_Append', diff)
    results.append(CheckResult(
        "builds a list result (PyList_New/Append)", ok,
        matched or "no list construction found",
    ))

    ok, matched = _match(r'FASTSEARCH|fastsearch|stringlib_find', diff, re.IGNORECASE)
    results.append(CheckResult(
        "uses FASTSEARCH or stringlib search", ok,
        matched or "no FASTSEARCH call found",
    ))

    return results


def _checks_cross_session_list_rotate(diff: str) -> list[CheckResult]:
    results = []

    ok, matched = _match(r'list_rotate|list\.rotate', diff)
    results.append(CheckResult(
        "function name list_rotate present", ok,
        matched or "not found",
    ))

    ok, matched = _match(r'"rotate"\s*,.*list_rotate|PyMethodDef.*rotate', diff, re.DOTALL)
    results.append(CheckResult(
        "PyMethodDef entry for rotate", ok,
        matched[:60] if matched else "PyMethodDef entry not found",
    ))

    ok, matched = _match(r'_list_reverse_slice|list_reverse_impl|reverse.*slice', diff, re.IGNORECASE)
    results.append(CheckResult(
        "three-reverse algorithm (reverse_slice helper or inline)", ok,
        matched or "no reverse slice logic found",
    ))

    ok, matched = _match(r'n\s*%=\s*len|n\s*%\s*Py_SIZE|n\s*%\s*size|modulo|normaliz', diff, re.IGNORECASE)
    results.append(CheckResult(
        "modulo normalisation for n >= len", ok,
        matched or "no modulo normalisation found",
    ))

    ok, matched = _match(r'Py_ssize_t\s+n', diff)
    results.append(CheckResult(
        "n parameter typed as Py_ssize_t", ok,
        matched or "Py_ssize_t n not found",
    ))

    ok, matched = _match(r'\[clinic\s+input\]|clinic|Py_ssize_t.*n.*clinic', diff, re.IGNORECASE)
    results.append(CheckResult(
        "Argument Clinic block present", ok,
        matched or "clinic block not found (may use manual PyArg_ParseTuple)",
    ))

    return results


def _checks_debug_gc_finalizer(diff: str) -> list[CheckResult]:
    """Output is a Python test script, not C code."""
    results = []

    ok, matched = _match(r'import\s+gc', diff)
    results.append(CheckResult("imports gc module", ok, matched or "not found"))

    ok, matched = _match(r'gc\.garbage', diff)
    results.append(CheckResult("references gc.garbage", ok, matched or "not found"))

    ok, matched = _match(r'gc\.collect\(\)', diff)
    results.append(CheckResult("calls gc.collect()", ok, matched or "not found"))

    ok, matched = _match(r'__del__', diff)
    results.append(CheckResult("defines __del__ method", ok, matched or "not found"))

    ok, matched = _match(r'weakref\.finalize', diff)
    results.append(CheckResult("uses weakref.finalize as the fix", ok, matched or "not found"))

    ok, matched = _match(r'handle_legacy_finalizers|tp_finalize|tp_del|gcmodule|finaliz', diff, re.IGNORECASE)
    results.append(CheckResult(
        "cites C source (function name or file)",
        ok, matched or "no C-level citation found",
    ))

    return results


def _checks_debug_descriptor_priority(diff: str) -> list[CheckResult]:
    """Output is a Python test script."""
    results = []

    ok, matched = _match(r'def\s+__get__.*def\s+__set__|class.*__get__|__set__', diff, re.DOTALL)
    results.append(CheckResult(
        "data descriptor defined (__get__ + __set__)", ok,
        matched[:60] if matched else "data descriptor not found",
    ))

    ok, matched = _match(r'def\s+__get__', diff)
    results.append(CheckResult("non-data descriptor defined (__get__ only)", ok, matched or "not found"))

    ok, matched = _match(r'assert\b', diff)
    results.append(CheckResult("assertions present", ok, matched or "no assert statements found"))

    ok, matched = _match(r'type_getattro|_PyType_Lookup|tp_descr_set|typeobject', diff, re.IGNORECASE)
    results.append(CheckResult(
        "cites C source (typeobject.c / tp_descr_set)",
        ok, matched or "no C-level citation found",
    ))

    return results


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_CHECKERS = {
    "feature_dict_pop_last":        _checks_feature_dict_pop_last,
    "cross_session_set_cartesian":   _checks_cross_session_set_cartesian,
    "cross_session_bytes_find_all":  _checks_cross_session_bytes_find_all,
    "cross_session_list_rotate":     _checks_cross_session_list_rotate,
    "debug_gc_finalizer":            _checks_debug_gc_finalizer,
    "debug_descriptor_priority":     _checks_debug_descriptor_priority,
}


def check_impl(task_id: str, agent_type: str, diff: str) -> QualityScore:
    """Run structural checks for one impl phase. Returns QualityScore."""
    score = QualityScore(task_id=task_id, agent_type=agent_type)
    if not diff:
        score.checks.append(CheckResult("diff non-empty", False, "empty diff — no files written"))
        return score

    checker = _CHECKERS.get(task_id)
    if checker is None:
        score.checks.append(CheckResult(f"checker defined for {task_id}", False, "no checker registered"))
        return score

    score.checks = checker(diff)
    return score


def score_runs(runs: list) -> list[QualityScore]:
    """Score all impl phases in a list of BenchmarkRun objects."""
    scores: list[QualityScore] = []
    for run in runs:
        for impl in run.impl_phases:
            diff = getattr(impl, "file_diff", "") or impl.answer
            scores.append(check_impl(impl.task_id, run.agent_type, diff))
    return scores


def print_quality_report(scores: list[QualityScore]) -> None:
    """Print P5-1 quality report to stdout."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        _rich = True
    except ImportError:
        _rich = False

    if not _rich:
        for s in scores:
            print(s.summary())
        return

    console = Console(width=120)
    console.print("\n[bold cyan]═══ P5-1: STRUCTURAL QUALITY CHECKS ═══[/bold cyan]")
    console.print("[dim]Layer 1 — deterministic regex checks on impl diffs (zero API calls)[/dim]\n")

    # Summary table
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    table.add_column("Task",       style="cyan",  min_width=32)
    table.add_column("Agent",      style="white", min_width=16)
    table.add_column("Score",      justify="right", min_width=8)
    table.add_column("Passed",     justify="right", min_width=8)
    table.add_column("Total",      justify="right", min_width=8)

    for s in scores:
        bar = "█" * s.passed + "░" * (s.total - s.passed)
        color = "green" if s.score >= 0.8 else ("yellow" if s.score >= 0.5 else "red")
        table.add_row(
            s.task_id,
            s.agent_type,
            f"[{color}]{s.score:.0%}[/{color}]",
            str(s.passed),
            str(s.total),
        )
    console.print(table)

    # Detailed per-task breakdown
    for s in scores:
        console.rule(f"[yellow]{s.task_id}[/yellow] / [white]{s.agent_type}[/white]")
        for c in s.checks:
            color = "green" if c.passed else "red"
            mark = "✓" if c.passed else "✗"
            detail = f" [dim]— {c.detail[:60]}[/dim]" if c.detail else ""
            console.print(f"  [{color}]{mark}[/{color}] {c.name}{detail}")
        console.print()
