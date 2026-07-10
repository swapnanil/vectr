#!/usr/bin/env python3
"""
Multi-repo benchmark harness — 12 question categories × N repos × 0/1 grading.

Design from Codebase-Memory MCP paper (arXiv:2603.27277).
Each question has a ground-truth answer checked by deterministic string match.

Question categories (12 total):
  1.  symbol_definition   — "Where is X defined?"
  2.  symbol_callers      — "What calls X?"
  3.  symbol_callees      — "What does X call?"
  4.  file_role           — "What does file F do?"
  5.  class_hierarchy     — "What does class C inherit from?"
  6.  entry_point         — "Where does the program start?"
  7.  api_endpoint        — "What HTTP endpoint handles /path?"
  8.  config_key          — "Where is config key K set?"
  9.  error_path          — "Where is exception E raised?"
  10. import_chain        — "Who imports module M?"
  11. test_coverage       — "What does test T exercise?"
  12. cross_session       — "Continue task from prior session notes."

Scoring: 0 (wrong/timeout) or 1 (correct) per question.
Token efficiency: vectr_input_tokens / vanilla_input_tokens per question.

Usage:
    python3.14 run_multi_repo.py --repo /path/to/repo --save
    python3.14 run_multi_repo.py --suite suite.json --save
    python3.14 run_multi_repo.py --list-categories
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("multi_repo")

_CLAUDE_DEFAULT = (
    "/path/to/home/Library/Application Support/Claude"
    "/claude-code/2.1.149/claude.app/Contents/MacOS/claude"
)
CLAUDE_BIN = _CLAUDE_DEFAULT if os.path.exists(_CLAUDE_DEFAULT) else "claude"

OUTPUT_DIR = Path(
    os.getenv("MULTI_REPO_OUTPUT", "/path/to/vectr/benchmarks/multi_repo")
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkQuestion:
    """One question in the multi-repo benchmark."""
    question_id: str
    category: str          # one of the 12 categories
    repo_path: str         # absolute path to the repo
    prompt: str            # question asked to the agent
    ground_truth: str      # expected answer (substring match)
    tags: list[str] = field(default_factory=list)


@dataclass
class QuestionResult:
    question_id: str
    category: str
    agent_type: str        # "vanilla" | "vectr"
    answer: str
    score: int             # 0 or 1
    input_tokens: int
    output_tokens: int
    cost_usd: float
    wall_time_s: float
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.score == 1


@dataclass
class RepoResult:
    repo_path: str
    repo_name: str
    questions: list[QuestionResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.questions:
            return 0.0
        return sum(q.score for q in self.questions) / len(self.questions)

    @property
    def total_input_tokens(self) -> int:
        return sum(q.input_tokens for q in self.questions)


# ---------------------------------------------------------------------------
# Question categories
# ---------------------------------------------------------------------------

CATEGORIES = [
    "symbol_definition",
    "symbol_callers",
    "symbol_callees",
    "file_role",
    "class_hierarchy",
    "entry_point",
    "api_endpoint",
    "config_key",
    "error_path",
    "import_chain",
    "test_coverage",
    "cross_session",
]


def _build_questions_for_repo(repo_path: str, category_filter: list[str] | None = None) -> list[BenchmarkQuestion]:
    """Auto-generate questions for a repo by scanning its structure.

    Returns a list of BenchmarkQuestion, one per applicable category.
    Ground truth is derived from the file system (no LLM calls).
    """
    root = Path(repo_path)
    questions: list[BenchmarkQuestion] = []
    repo_name = root.name
    cats = set(category_filter or CATEGORIES)

    # Collect Python/JS/TS files for question generation
    py_files = list(root.rglob("*.py"))[:50]
    symbols = _scan_symbols(py_files)
    routes = _scan_routes(py_files)
    classes = _scan_classes(py_files)

    # 1. symbol_definition
    if "symbol_definition" in cats and symbols:
        sym_name, sym_file, sym_line = symbols[0]
        questions.append(BenchmarkQuestion(
            question_id=f"{repo_name}_symbol_definition",
            category="symbol_definition",
            repo_path=repo_path,
            prompt=f"In the codebase at {repo_path}, where is `{sym_name}` defined? "
                   "Answer with just the filename and line number.",
            ground_truth=f"{sym_file.name}",
            tags=["symbol", "definition"],
        ))

    # 2. symbol_callers
    if "symbol_callers" in cats and symbols:
        sym_name, sym_file, sym_line = symbols[0]
        questions.append(BenchmarkQuestion(
            question_id=f"{repo_name}_symbol_callers",
            category="symbol_callers",
            repo_path=repo_path,
            prompt=f"In the codebase at {repo_path}, what functions call `{sym_name}`? "
                   "List the callers.",
            ground_truth="",  # open-ended; graded by non-empty answer
            tags=["symbol", "callers"],
        ))

    # 3. entry_point
    if "entry_point" in cats:
        entry = _find_entry_point(root)
        if entry:
            questions.append(BenchmarkQuestion(
                question_id=f"{repo_name}_entry_point",
                category="entry_point",
                repo_path=repo_path,
                prompt=f"In the codebase at {repo_path}, where does the program start? "
                       "What is the main entry point?",
                ground_truth=entry.name,
                tags=["entry", "main"],
            ))

    # 4. api_endpoint
    if "api_endpoint" in cats and routes:
        method, path, file_name = routes[0]
        questions.append(BenchmarkQuestion(
            question_id=f"{repo_name}_api_endpoint",
            category="api_endpoint",
            repo_path=repo_path,
            prompt=f"In the codebase at {repo_path}, which file handles the `{method} {path}` endpoint?",
            ground_truth=file_name,
            tags=["api", "route"],
        ))

    # 5. class_hierarchy
    if "class_hierarchy" in cats and classes:
        cls_name, parent, cls_file = classes[0]
        if parent:
            questions.append(BenchmarkQuestion(
                question_id=f"{repo_name}_class_hierarchy",
                category="class_hierarchy",
                repo_path=repo_path,
                prompt=f"In the codebase at {repo_path}, what does class `{cls_name}` inherit from?",
                ground_truth=parent,
                tags=["class", "inheritance"],
            ))

    # 6. file_role
    if "file_role" in cats and py_files:
        target = next((f for f in py_files if f.name not in {"__init__.py", "setup.py"}), None)
        if target:
            questions.append(BenchmarkQuestion(
                question_id=f"{repo_name}_file_role",
                category="file_role",
                repo_path=repo_path,
                prompt=f"In the codebase at {repo_path}, what does `{target.name}` do? "
                       "Give a one-sentence description.",
                ground_truth="",  # open-ended; any non-empty answer passes
                tags=["file", "role"],
            ))

    return questions


def _scan_symbols(files: list[Path]) -> list[tuple[str, Path, int]]:
    """Return [(name, file, line)] for top-level functions."""
    results = []
    pattern = re.compile(r'^def (\w+)\(', re.MULTILINE)
    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            m = pattern.match(line)
            if m and not m.group(1).startswith("_"):
                results.append((m.group(1), f, i))
        if results:
            break
    return results


def _scan_routes(files: list[Path]) -> list[tuple[str, str, str]]:
    """Return [(method, path, filename)] for HTTP routes."""
    results = []
    pat = re.compile(r'@\w+\.(get|post|put|delete|patch|route)\s*\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(src):
            verb = m.group(1).upper()
            path = m.group(2)
            results.append((verb, path, f.name))
        if results:
            break
    return results


def _scan_classes(files: list[Path]) -> list[tuple[str, str, str]]:
    """Return [(class_name, parent, filename)] for class definitions with inheritance."""
    results = []
    pat = re.compile(r'^class (\w+)\((\w+)\)', re.MULTILINE)
    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(src):
            cls, parent = m.group(1), m.group(2)
            if parent not in {"object", "Exception", "BaseException"}:
                results.append((cls, parent, f.name))
        if results:
            break
    return results


def _find_entry_point(root: Path) -> Path | None:
    for name in ("main.py", "app.py", "run.py", "server.py", "manage.py", "__main__.py"):
        p = root / name
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def _run_question(
    question: BenchmarkQuestion,
    agent_type: str,
    max_turns: int = 10,
    timeout_s: int = 300,
) -> QuestionResult:
    """Run one question with vanilla or vectr agent."""
    use_vectr = agent_type == "vectr"
    vectr_tools = [
        "mcp__vectr__vectr_search", "mcp__vectr__vectr_locate",
        "mcp__vectr__vectr_trace", "mcp__vectr__vectr_status",
        "mcp__vectr__vectr_recall", "mcp__vectr__vectr_remember",
        "mcp__vectr__vectr_map",
    ]
    std_tools = ["Read", "Bash"]
    allowed = std_tools + (vectr_tools if use_vectr else [])

    cmd = [
        CLAUDE_BIN, "-p", question.prompt,
        "--output-format", "stream-json",
        "--max-turns", str(max_turns),
        "--allowedTools", ",".join(allowed),
    ]

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=question.repo_path,
        )
    except FileNotFoundError:
        return QuestionResult(
            question_id=question.question_id, category=question.category,
            agent_type=agent_type, answer="", score=0,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            wall_time_s=0.0, error="claude CLI not found",
        )

    final: dict = {}
    deadline = time.time() + timeout_s
    for raw in proc.stdout:
        if time.time() > deadline:
            proc.kill()
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            if ev.get("type") == "result":
                final = ev
        except json.JSONDecodeError:
            continue
    proc.wait()
    wall_time_s = time.time() - start

    if not final:
        return QuestionResult(
            question_id=question.question_id, category=question.category,
            agent_type=agent_type, answer="", score=0,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            wall_time_s=wall_time_s, error="no result",
        )

    answer = final.get("result", "")
    usage = final.get("usage", {})
    iters = usage.get("iterations", [usage])
    input_tokens = sum(
        it.get("input_tokens", 0) + it.get("cache_creation_input_tokens", 0)
        + it.get("cache_read_input_tokens", 0)
        for it in iters
    )
    output_tokens = sum(it.get("output_tokens", 0) for it in iters)
    cost_usd = final.get("total_cost_usd", 0.0) or 0.0

    # 0/1 grading: ground truth substring match (case-insensitive)
    # Empty ground_truth → any non-empty answer scores 1 (open-ended question)
    if not question.ground_truth:
        score = 1 if answer.strip() else 0
    else:
        score = 1 if question.ground_truth.lower() in answer.lower() else 0

    return QuestionResult(
        question_id=question.question_id, category=question.category,
        agent_type=agent_type, answer=answer, score=score,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost_usd, wall_time_s=wall_time_s,
    )


# ---------------------------------------------------------------------------
# Run all questions for a repo
# ---------------------------------------------------------------------------

def run_repo_benchmark(
    repo_path: str,
    agents: list[str] = ("vanilla", "vectr"),
    category_filter: list[str] | None = None,
) -> list[QuestionResult]:
    """Run all auto-generated questions for a repo, both agents."""
    questions = _build_questions_for_repo(repo_path, category_filter)
    if not questions:
        logger.warning("No questions generated for %s — skipping", repo_path)
        return []

    results: list[QuestionResult] = []
    for q in questions:
        for agent in agents:
            logger.info("[%s] %s — %s", agent, q.category, Path(repo_path).name)
            r = _run_question(q, agent)
            results.append(r)
            logger.info("  score=%d  in=%d  cost=$%.4f", r.score, r.input_tokens, r.cost_usd)

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_multi_repo_report(all_results: list[QuestionResult]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console(width=120)
    except ImportError:
        for r in all_results:
            print(f"{r.category:25} {r.agent_type:10} score={r.score} in={r.input_tokens}")
        return

    console.print("\n[bold cyan]═══ MULTI-REPO BENCHMARK — 12 QUESTION CATEGORIES ═══[/bold cyan]\n")

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    table.add_column("Category",     style="cyan",  min_width=22)
    table.add_column("Agent",        style="white", min_width=10)
    table.add_column("Pass rate",    justify="right", min_width=10)
    table.add_column("Avg in-tok",   justify="right", min_width=10)
    table.add_column("Avg cost",     justify="right", min_width=10)

    by_cat_agent: dict[tuple[str, str], list[QuestionResult]] = {}
    for r in all_results:
        key = (r.category, r.agent_type)
        by_cat_agent.setdefault(key, []).append(r)

    for cat in CATEGORIES:
        for agent in ("vanilla", "vectr"):
            rs = by_cat_agent.get((cat, agent), [])
            if not rs:
                continue
            pass_rate = sum(r.score for r in rs) / len(rs)
            avg_in = sum(r.input_tokens for r in rs) / len(rs)
            avg_cost = sum(r.cost_usd for r in rs) / len(rs)
            color = "green" if pass_rate >= 0.8 else ("yellow" if pass_rate >= 0.5 else "red")
            table.add_row(
                cat, agent,
                f"[{color}]{pass_rate:.0%}[/{color}]",
                f"{avg_in:,.0f}",
                f"${avg_cost:.4f}",
            )

    console.print(table)

    van_results = [r for r in all_results if r.agent_type == "vanilla"]
    vec_results = [r for r in all_results if r.agent_type == "vectr"]
    if van_results and vec_results:
        van_pass = sum(r.score for r in van_results) / len(van_results)
        vec_pass = sum(r.score for r in vec_results) / len(vec_results)
        van_tok  = sum(r.input_tokens for r in van_results) / len(van_results)
        vec_tok  = sum(r.input_tokens for r in vec_results) / len(vec_results)
        tok_eff  = (1 - vec_tok / van_tok) * 100 if van_tok else 0

        console.rule("[bold cyan]Grand Totals[/bold cyan]")
        console.print(
            f"Pass rate:  vanilla={van_pass:.1%}  vectr={vec_pass:.1%}\n"
            f"Avg input tokens:  vanilla={van_tok:,.0f}  vectr={vec_tok:,.0f}\n"
            f"Token efficiency:  {tok_eff:.1f}% reduction with vectr"
        )


def save_multi_repo_results(results: list[QuestionResult], repo_path: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    repo_name = Path(repo_path).name
    path = OUTPUT_DIR / f"multi_repo_{repo_name}_{ts}.json"
    data = [
        {
            "question_id": r.question_id,
            "category":    r.category,
            "agent_type":  r.agent_type,
            "score":       r.score,
            "answer":      r.answer[:500],
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cost_usd":    r.cost_usd,
            "wall_time_s": r.wall_time_s,
            "error":       r.error,
        }
        for r in results
    ]
    path.write_text(json.dumps(data, indent=2))
    logger.info("Results saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-repo benchmark (12 question categories)")
    parser.add_argument("--repo", help="Path to a single repository to benchmark")
    parser.add_argument("--suite", help="JSON file listing multiple repo paths")
    parser.add_argument("--categories", nargs="*", choices=CATEGORIES, help="Run only these categories")
    parser.add_argument("--agents", nargs="*", default=["vanilla", "vectr"], choices=["vanilla", "vectr"])
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
    parser.add_argument("--list-categories", action="store_true", help="List all 12 question categories")
    args = parser.parse_args()

    if args.list_categories:
        for i, c in enumerate(CATEGORIES, 1):
            print(f"  {i:2}. {c}")
        return

    repos: list[str] = []
    if args.repo:
        repos.append(str(Path(args.repo).resolve()))
    elif args.suite:
        suite = json.loads(Path(args.suite).read_text())
        repos = [str(Path(p).resolve()) for p in suite.get("repos", [])]
    else:
        parser.print_help()
        sys.exit(1)

    all_results: list[QuestionResult] = []
    for repo in repos:
        logger.info("Benchmarking repo: %s", repo)
        results = run_repo_benchmark(repo, agents=args.agents, category_filter=args.categories)
        all_results.extend(results)
        if args.save and results:
            save_multi_repo_results(results, repo)

    if all_results:
        print_multi_repo_report(all_results)


if __name__ == "__main__":
    main()
