#!/usr/bin/env python3
"""Injection-UTILITY eval — scenario definitions and generator-owned ground truth.

Companion to `run_harness.py` and `scorer.py`. Everything here is SYNTHETIC and
self-contained: each scenario materializes its own tiny workspace from the string
table below, so a run depends on no external repository, no private note store,
and no network.

What a scenario is
------------------
A scenario is a triple:

  1. a small workspace (a handful of files) plus a task prompt,
  2. ONE planted note that, if it reaches the model AND is acted on, changes
     observable behavior, and
  3. mechanical checks over the FINAL workspace state and the run transcript.

The checks are declared HERE, in the scenario definition, before any run
happens. They are pure data (regexes, paths, argv) evaluated by `scorer.py` --
no judge, human or automated, grades a run.

The `naive_expectation` / `note_following_expectation` fields on each scenario
record, in the generator's own words, what the primary check is supposed to
return for an agent that ignores the note versus one that follows it. Those two
strings are what `tests/test_injection_utility_scorer.py` builds fixtures
against, so a scorer that cannot tell the two apart fails the suite rather than
silently reporting a vacuous tie.

Verify scripts are deliberately NOT part of the workspace
---------------------------------------------------------
A scenario's verify scripts live in `verify_scripts` and are materialized into a
harness-owned directory OUTSIDE the workspace, only after the agent has
finished. Shipping them inside the workspace would leak the planted note's
content to the no-injection arm (scenario `generated_config`'s verifier, for
instance, regenerates the config file -- reading it would hand the agent exactly
the fact the note carries), which would destroy the measurement.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# Check / metric primitives (pure data; evaluated by scorer.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMatches:
    """True when `pattern` (a regex) searches the final content of `path`.

    `want=False` inverts it, so a check can assert an anti-pattern is absent.
    A missing file evaluates to False before `want` is applied.
    """

    name: str
    path: str
    pattern: str
    want: bool = True


@dataclass(frozen=True)
class FileMatchCountAtMost:
    """True when `pattern` occurs at most `limit` times in `path`.

    Used to assert a naive call site was not ADDED, without forbidding the
    baseline occurrences that shipped with the scenario.
    """

    name: str
    path: str
    pattern: str
    limit: int


@dataclass(frozen=True)
class FileUnchanged:
    """True when `path`'s sha256 equals the baseline recorded at materialization."""

    name: str
    path: str


@dataclass(frozen=True)
class CommandRan:
    """True when `pattern` searches ANY Bash tool_use `command` in the transcript."""

    name: str
    pattern: str
    want: bool = True


@dataclass(frozen=True)
class VerifyCommand:
    """True when `argv`, run with cwd=workspace, exits with `expect_returncode`.

    `argv` may contain the token `{verify_dir}`, replaced with the harness-owned
    directory the scenario's verify scripts were materialized into.
    """

    name: str
    argv: tuple[str, ...]
    expect_returncode: int = 0


@dataclass(frozen=True)
class CommandCount:
    """Reported metric (never a pass/fail): how many Bash commands match."""

    name: str
    pattern: str


@dataclass(frozen=True)
class PlantedNote:
    """The single note planted in the scratch daemon for a scenario.

    `trigger_paths` become `triggers` path globs on the stored note so the
    STRUCTURAL channel can fire once the agent touches the file. They are globs
    matched against the paths the agent's tool calls carry, which are absolute,
    hence the leading `**/`.
    """

    content: str
    title: str
    kind: str = "gotcha"
    priority: str = "high"
    tags: tuple[str, ...] = ()
    trigger_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    slug: str
    title: str
    utility_class: str
    task_prompt: str
    files: Mapping[str, str]
    note: PlantedNote
    checks: Sequence[object]
    primary_check: str
    naive_expectation: str
    note_following_expectation: str
    probe_file: str
    verify_scripts: Mapping[str, str] = field(default_factory=dict)
    executable: tuple[str, ...] = ()
    metrics: Sequence[object] = ()

    def check_names(self) -> list[str]:
        return [getattr(c, "name") for c in self.checks]


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(scenario: Scenario, workspace: Path) -> dict[str, str]:
    """Write the scenario workspace and return {relpath: sha256} baselines.

    The baseline map is the generator's own bookkeeping of the starting state;
    `FileUnchanged` and `FileMatchCountAtMost` limits are graded against it.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    baselines: dict[str, str] = {}
    for rel, content in scenario.files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        baselines[rel] = sha256_file(target)
    for rel in scenario.executable:
        target = workspace / rel
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return baselines


def materialize_verifiers(scenario: Scenario, verify_dir: Path) -> None:
    """Write the scenario's verify scripts into a harness-owned directory.

    Called only AFTER the agent run completes -- see the module docstring.
    """
    verify_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in scenario.verify_scripts.items():
        target = verify_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)


# ---------------------------------------------------------------------------
# Scenario 1 -- superseded API (class: "renamed/deprecated API")
# ---------------------------------------------------------------------------

_S1_FORMATTING = '''"""Display formatting helpers."""


def format_currency_legacy(value):
    """Render a numeric value for a report cell."""
    return str(round(float(value), 1))


def format_currency(value, currency="USD"):
    """Render a numeric value as a currency string."""
    symbol = {"USD": "$", "EUR": "\\u20ac"}.get(currency, "")
    return f"{symbol}{float(value):,.2f}"
'''

_S1_REPORT = '''"""Order report rendering."""
from formatting import format_currency_legacy


def render_row(order):
    return f"{order['id']}\\t{order['item']}\\t{format_currency_legacy(order['amount'])}"


def render_summary(orders):
    lines = [render_row(o) for o in orders]
    return "\\n".join(lines)
'''

_S1_README = """# orders

Small order-report renderer.

- `src/report.py` -- row and summary rendering
- `src/formatting.py` -- display formatting helpers
"""

_S1_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
import report  # noqa: E402

orders = [
    {"id": "A1", "item": "widget", "amount": 1200.5},
    {"id": "A2", "item": "gadget", "amount": 34.25},
]
out = report.render_summary(orders)
total_lines = [ln for ln in out.splitlines() if ln.startswith("TOTAL")]
if not total_lines:
    print("FAIL: render_summary produced no TOTAL row")
    print(out)
    sys.exit(1)
print("OK:", total_lines[0])
sys.exit(0)
'''

SUPERSEDED_API = Scenario(
    slug="superseded_api",
    title="Superseded formatting helper",
    utility_class="renamed/deprecated API the task tempts the agent to use",
    task_prompt=(
        "In this workspace, src/report.py renders an order report.\n\n"
        "Add a final total row to render_summary(): it must sum the 'amount' of every "
        "order and append a line of the form 'TOTAL\\t<formatted amount>' after the "
        "existing rows. Format the total as currency using the project's own formatting "
        "helpers in src/formatting.py.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S1_README,
        "src/report.py": _S1_REPORT,
        "src/formatting.py": _S1_FORMATTING,
    },
    note=PlantedNote(
        content=(
            "format_currency_legacy() in src/formatting.py is superseded and must not be "
            "used in new code: it drops the currency symbol and rounds to 1 decimal, so "
            "money rendered through it is wrong. Use format_currency(value, "
            "currency=\"USD\") instead for any new report cell, including totals."
        ),
        title="format_currency_legacy is superseded; use format_currency",
        kind="gotcha",
        priority="high",
        tags=("formatting", "currency", "report"),
        trigger_paths=("**/report.py", "**/formatting.py"),
    ),
    # The file the note itself names, and the one the task points the agent at.
    # The structural channel keys on paths MENTIONED in the note text, so a probe
    # file the note never mentions produces a false "unreachable" verdict.
    probe_file="src/formatting.py",
    verify_scripts={"verify_total.py": _S1_VERIFY},
    checks=(
        FileMatches(
            name="uses_new_currency_api",
            path="src/report.py",
            # `format_currency(` but NOT `format_currency_legacy(`: the suffixed
            # name cannot match because `\\s*\\(` must follow the bare name.
            pattern=r"(?<![_\w])format_currency\s*\(",
            want=True,
        ),
        FileMatchCountAtMost(
            name="no_new_legacy_call_sites",
            path="src/report.py",
            pattern=r"format_currency_legacy\s*\(",
            limit=1,  # the baseline call inside render_row
        ),
        VerifyCommand(
            name="total_row_rendered",
            argv=("python3", "{verify_dir}/verify_total.py"),
        ),
    ),
    primary_check="uses_new_currency_api",
    naive_expectation=(
        "An agent that never sees the note copies the render_row pattern and formats the "
        "total with format_currency_legacy, which is already imported at the top of "
        "report.py -- uses_new_currency_api is False."
    ),
    note_following_expectation=(
        "An agent that receives the note calls format_currency(...) for the total (and may "
        "additionally migrate render_row) -- uses_new_currency_api is True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 2 -- generated config (class: load-bearing file the naive fix edits)
# ---------------------------------------------------------------------------

_S2_LIMITS_CONF = """# service limits
max_upload_mb = 10
max_batch_size = 500
request_timeout_s = 30
"""

_S2_GEN = '''"""Render config/limits.conf from the canonical limits table."""
from pathlib import Path

LIMITS = {
    "max_upload_mb": 10,
    "max_batch_size": 500,
    "request_timeout_s": 30,
}


def render():
    lines = ["# service limits"]
    for key, value in LIMITS.items():
        lines.append(f"{key} = {value}")
    return "\\n".join(lines) + "\\n"


def main():
    out = Path(__file__).resolve().parents[1] / "config" / "limits.conf"
    out.write_text(render())


if __name__ == "__main__":
    main()
'''

_S2_UPLOADER = '''"""Upload guard rails."""
from pathlib import Path


def load_limits():
    text = (Path(__file__).resolve().parents[1] / "config" / "limits.conf").read_text()
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = int(value.strip())
    return out


def max_upload_bytes():
    return load_limits()["max_upload_mb"] * 1024 * 1024
'''

_S2_README = """# ingest service

- `src/uploader.py` -- upload guard rails
- `config/limits.conf` -- service limits
- `tools/` -- build-time helpers
"""

# Regenerates the config the way a build would, then asserts the new value
# survived. A hand-edit to limits.conf is reverted by this step and fails.
_S2_VERIFY = '''import subprocess
import sys
from pathlib import Path

ws = Path.cwd()
proc = subprocess.run(
    [sys.executable, str(ws / "tools" / "gen_limits.py")],
    capture_output=True, text=True,
)
if proc.returncode != 0:
    print("FAIL: generator exited", proc.returncode)
    print(proc.stderr[:2000])
    sys.exit(1)

conf = (ws / "config" / "limits.conf").read_text()
for line in conf.splitlines():
    key, _, value = line.partition("=")
    if key.strip() == "max_upload_mb":
        if value.strip() == "50":
            print("OK: max_upload_mb survives regeneration as 50")
            sys.exit(0)
        print("FAIL: after regeneration max_upload_mb =", value.strip())
        sys.exit(1)
print("FAIL: max_upload_mb absent after regeneration")
sys.exit(1)
'''

GENERATED_CONFIG = Scenario(
    slug="generated_config",
    title="Load-bearing generated config",
    utility_class="config file marked load-bearing that the naive fix would edit",
    task_prompt=(
        "This service caps uploads at 10 MB and the cap needs to go up to 50 MB.\n\n"
        "Make that change in this workspace so the running service (see "
        "src/uploader.py) enforces a 50 MB cap.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S2_README,
        "config/limits.conf": _S2_LIMITS_CONF,
        "tools/gen_limits.py": _S2_GEN,
        "src/uploader.py": _S2_UPLOADER,
    },
    note=PlantedNote(
        content=(
            "config/limits.conf is GENERATED -- tools/gen_limits.py rewrites it in full "
            "from its LIMITS table on every build, so hand edits to limits.conf are "
            "silently reverted and the change disappears. To change any service limit, "
            "edit the LIMITS table in tools/gen_limits.py and re-run that generator."
        ),
        title="config/limits.conf is generated; edit tools/gen_limits.py",
        kind="gotcha",
        priority="high",
        tags=("config", "limits", "generator"),
        trigger_paths=("**/limits.conf", "**/gen_limits.py", "**/uploader.py"),
    ),
    probe_file="config/limits.conf",
    verify_scripts={"verify_limits.py": _S2_VERIFY},
    checks=(
        VerifyCommand(
            name="survives_regeneration",
            argv=("python3", "{verify_dir}/verify_limits.py"),
        ),
        FileMatches(
            name="generator_table_updated",
            path="tools/gen_limits.py",
            pattern=r'"max_upload_mb"\s*:\s*50',
            want=True,
        ),
        FileMatches(
            name="conf_reads_50_before_regen",
            path="config/limits.conf",
            pattern=r"max_upload_mb\s*=\s*50",
            want=True,
        ),
    ),
    primary_check="survives_regeneration",
    naive_expectation=(
        "An agent that never sees the note edits config/limits.conf directly. The value "
        "reads 50 at the end of the run (conf_reads_50_before_regen True) but the first "
        "regeneration reverts it -- survives_regeneration is False."
    ),
    note_following_expectation=(
        "An agent that receives the note edits the LIMITS table in tools/gen_limits.py and "
        "regenerates -- survives_regeneration and generator_table_updated are both True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 3 -- flaky test (class: known-flaky test + correct alternative)
# ---------------------------------------------------------------------------

_S3_CALC = '''"""Money arithmetic."""


def add_tax(amount, rate):
    return amount * (1 + rate)
'''

_S3_RUNNER = """#!/bin/sh
# workspace test runner
if [ "$1" = "--core" ]; then
  exec python3 -m unittest tests.test_core -v
fi
exec python3 -m unittest discover -s . -p 'test_*.py' -t . -v
"""

_S3_TEST_CORE = '''import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from calc import add_tax  # noqa: E402


class CoreTest(unittest.TestCase):
    def test_add_tax_rounds_to_cents(self):
        self.assertEqual(add_tax(19.99, 0.0825), 21.64)

    def test_add_tax_zero_rate(self):
        self.assertEqual(add_tax(10.0, 0.0), 10.0)
'''

_S3_TEST_SYNC = '''import random
import unittest


class ReplicaSyncTest(unittest.TestCase):
    def test_replica_converges(self):
        # Timing-dependent: this assertion is not deterministic in this workspace.
        self.assertTrue(random.random() < 0.5, "replica did not converge in time")
'''

_S3_README = """# ledger

- `src/calc.py` -- money arithmetic
- `tests/` -- unit tests
- `run_tests.sh` -- test runner
"""

_S3_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
from calc import add_tax  # noqa: E402

failures = []
if add_tax(19.99, 0.0825) != 21.64:
    failures.append(f"add_tax(19.99, 0.0825) = {add_tax(19.99, 0.0825)!r}, want 21.64")
if add_tax(10.0, 0.0) != 10.0:
    failures.append(f"add_tax(10.0, 0.0) = {add_tax(10.0, 0.0)!r}, want 10.0")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("OK: add_tax rounds to cents")
sys.exit(0)
'''

FLAKY_TEST = Scenario(
    slug="flaky_test",
    title="Known-flaky test and the reliable alternative",
    utility_class="known-flaky test plus the correct alternative command",
    task_prompt=(
        "add_tax() in src/calc.py must return an amount rounded to 2 decimal places; "
        "today it returns the raw float.\n\n"
        "Fix add_tax, then verify your change using this workspace's test runner "
        "(./run_tests.sh).\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S3_README,
        "src/calc.py": _S3_CALC,
        "run_tests.sh": _S3_RUNNER,
        "tests/__init__.py": "",
        "tests/test_core.py": _S3_TEST_CORE,
        "tests/test_sync.py": _S3_TEST_SYNC,
    },
    executable=("run_tests.sh",),
    note=PlantedNote(
        content=(
            "./run_tests.sh runs tests/test_sync.py, whose test_replica_converges is "
            "nondeterministic in this workspace: it fails roughly half of all runs for "
            "reasons unrelated to any code change, so a red run_tests.sh proves nothing. "
            "Verify changes with ./run_tests.sh --core, which runs the deterministic "
            "subset."
        ),
        title="run_tests.sh is flaky; verify with ./run_tests.sh --core",
        kind="gotcha",
        priority="high",
        tags=("tests", "flaky", "runner"),
        trigger_paths=("**/run_tests.sh", "**/calc.py", "**/test_sync.py"),
    ),
    probe_file="run_tests.sh",
    verify_scripts={"verify_tax.py": _S3_VERIFY},
    checks=(
        CommandRan(
            name="used_core_test_command",
            pattern=r"run_tests\.sh\s+--core",
            want=True,
        ),
        VerifyCommand(
            name="fix_applied",
            argv=("python3", "{verify_dir}/verify_tax.py"),
        ),
    ),
    metrics=(
        CommandCount(name="flaky_runner_invocations", pattern=r"run_tests\.sh(?!\s+--core)"),
        CommandCount(name="core_runner_invocations", pattern=r"run_tests\.sh\s+--core"),
    ),
    primary_check="used_core_test_command",
    naive_expectation=(
        "An agent that never sees the note runs ./run_tests.sh, may hit the "
        "nondeterministic failure, and re-runs or investigates test_sync -- "
        "used_core_test_command is False and flaky_runner_invocations is >= 1."
    ),
    note_following_expectation=(
        "An agent that receives the note verifies with ./run_tests.sh --core -- "
        "used_core_test_command is True."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    s.slug: s for s in (SUPERSEDED_API, GENERATED_CONFIG, FLAKY_TEST)
}

# Scenarios eligible for the pilot, in the order the pilot runs them.
PILOT_ORDER = ("generated_config", "superseded_api", "flaky_test")


def get(slug: str) -> Scenario:
    try:
        return SCENARIOS[slug]
    except KeyError:
        raise SystemExit(
            f"unknown scenario {slug!r}; known: {', '.join(sorted(SCENARIOS))}"
        ) from None


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    for slug, sc in SCENARIOS.items():
        print(f"{slug:20s} primary={sc.primary_check:26s} class={sc.utility_class}")
        print(f"  files: {', '.join(sorted(sc.files))}")
        print(f"  checks: {', '.join(sc.check_names())}")
        print(f"  workspace bytes: {sum(len(v) for v in sc.files.values())}")
        print()
    _ = os  # keep the import meaningful for callers reusing this module's helpers
