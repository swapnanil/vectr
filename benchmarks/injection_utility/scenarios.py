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
    """True when `pattern` searches ANY Bash tool_use `command` in the transcript.

    `exec_anchor=True` (DEFECT 9, longitudinal_rediscovery) restricts the search to
    genuine command-execution positions in each command string -- see scorer.py's
    `_matches_at_exec_position` in whichever harness consumes this primitive. Default
    False preserves the original anywhere-in-string `re.search` behavior unchanged for
    every existing caller (`injection_utility` and `anti_memory` scenarios never set
    this field).
    """

    name: str
    pattern: str
    want: bool = True
    exec_anchor: bool = False


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
class AllOf:
    """True only when every sub-check passes.

    Exists so a scenario whose note says "do X but NOT Y" can make its PRIMARY
    check a conjunction. Without it, a primary like "POOL_SIZE is still 1" is
    also satisfied by an agent that did nothing at all, and a dead run would be
    scored as a utility hit. Pairing the restraint half with an engagement half
    ("...and BATCH_ROWS was actually raised") closes that hole inside the single
    check the utility verdict reads.

    Members must be read-only checks. A `VerifyCommand` inside a conjunction
    would break `score_run`'s read-before-mutate ordering, which exists so file
    checks describe the state the agent left rather than the state a verifier
    rewrote, so it is rejected at construction time rather than at score time.
    """

    name: str
    of: tuple[object, ...]

    def __post_init__(self) -> None:
        if not self.of:
            raise ValueError(f"AllOf {self.name!r} has no sub-checks")
        for sub in self.of:
            if isinstance(sub, VerifyCommand):
                raise TypeError(
                    f"AllOf {self.name!r} contains VerifyCommand "
                    f"{getattr(sub, 'name', '?')!r}; conjunctions must be read-only"
                )


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


# ===========================================================================
# Second-generation scenarios (the pilot's three had a CEILING problem)
# ===========================================================================
#
# The pilot measured `generated_config` and `superseded_api` at one seed and
# found the no-injection control solving BOTH correctly: the agent read
# tools/gen_limits.py and inferred the config was generated, and it picked the
# non-legacy currency helper on name alone. A scenario the control already
# passes cannot show utility no matter how well injection works -- the ceiling
# is the measurement's, not the product's.
#
# So every scenario below is built to one rule: THE DISCRIMINATING FACT IS NOT
# DERIVABLE FROM THE WORKSPACE. It lives outside the repo -- a vendor bug, a
# prod incident, a licensing constraint, a decision taken in a room -- and the
# planted note is its only carrier. Reading every byte of the workspace, in any
# order, cannot recover it.
#
# Two further design rules, both learned the hard way:
#
#  * THE NOTE NAMES THE FILE THE AGENT OPENS FIRST. Structural matching on the
#    proxy channel keys on paths MENTIONED IN THE NOTE'S TEXT, not on a note's
#    declared `triggers` globs (measured; see the README). And the window only
#    acquires a path once the agent has made a file-touching tool call, so the
#    note lands after the first Read. The task prompt therefore points at one
#    file, and the note names that file.
#
#  * THE PRIMARY CHECK IS NEVER SATISFIED BY AN AGENT THAT DID NOTHING. Where
#    the note-following behavior is RESTRAINT ("leave this pin alone"), the
#    primary check is an `AllOf` pairing the restraint with an engagement half,
#    so a dead run scores False rather than looking like a utility hit.


# ---------------------------------------------------------------------------
# Scenario 4 -- pinned dependency (class: prod incident pinned a version)
# ---------------------------------------------------------------------------

_S4_REQUIREMENTS = """# runtime pins
requests==2.28.2
urllib3==1.26.18
pydantic==2.4.2
rich==13.5.2
"""

_S4_SCAN = """# dependency scanner report -- latest release on the index
requests 2.32.3
urllib3 2.2.3
pydantic 2.9.2
rich 13.9.2
"""

_S4_CLIENT = '''"""Outbound HTTP calls for the checkout API."""
import requests

TIMEOUT_S = 10


def post_charge(payload):
    return requests.post(
        "https://payments.internal/charge", json=payload, timeout=TIMEOUT_S
    )
'''

_S4_README = """# checkout-api

- `requirements.txt` -- runtime pins
- `tools/available_versions.txt` -- dependency scanner output
- `src/client.py` -- outbound HTTP calls
"""

_S4_PIN_HELD = FileMatches(
    name="urllib3_pin_held",
    path="requirements.txt",
    pattern=r"urllib3==1\.26\.18",
    want=True,
)
_S4_REQUESTS_BUMPED = FileMatches(
    name="requests_bumped",
    path="requirements.txt",
    pattern=r"requests==2\.32\.3",
    want=True,
)

PINNED_DEPENDENCY = Scenario(
    slug="pinned_dependency",
    title="Deliberately pinned dependency",
    utility_class="prod incident pinned a version; nothing in the repo says why",
    task_prompt=(
        "The dependency scanner's report for this project is in "
        "tools/available_versions.txt.\n\n"
        "Update requirements.txt so every pinned package sits at the version the "
        "scanner reports.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S4_README,
        "requirements.txt": _S4_REQUIREMENTS,
        "tools/available_versions.txt": _S4_SCAN,
        "src/client.py": _S4_CLIENT,
    },
    note=PlantedNote(
        content=(
            "urllib3 is held at 1.26.18 in requirements.txt on purpose. INC-2210: "
            "urllib3 2.x changed certificate validation behind our egress proxy and "
            "every outbound call started failing in prod within minutes of the deploy. "
            "Unpinning is blocked on the proxy migration, PLAT-884, which is "
            "unscheduled. Bump anything else in requirements.txt; never urllib3."
        ),
        title="urllib3 is pinned at 1.26.18 by INC-2210 -- do not bump it",
        kind="gotcha",
        priority="high",
        tags=("dependencies", "pins", "incident"),
        trigger_paths=("**/requirements.txt",),
    ),
    probe_file="requirements.txt",
    checks=(
        AllOf(
            name="held_pin_while_bumping",
            of=(_S4_PIN_HELD, _S4_REQUESTS_BUMPED),
        ),
        _S4_PIN_HELD,
        _S4_REQUESTS_BUMPED,
        FileMatches(
            name="pydantic_bumped",
            path="requirements.txt",
            pattern=r"pydantic==2\.9\.2",
            want=True,
        ),
        FileMatches(
            name="no_urllib3_2x",
            path="requirements.txt",
            pattern=r"urllib3==2\.",
            want=False,
        ),
    ),
    primary_check="held_pin_while_bumping",
    naive_expectation=(
        "An agent that never sees the note does exactly what the task says and moves "
        "all four pins to the scanner's versions, urllib3 included -- urllib3_pin_held "
        "is False, so held_pin_while_bumping is False."
    ),
    note_following_expectation=(
        "An agent that receives the note bumps requests, pydantic and rich and leaves "
        "urllib3==1.26.18 alone -- held_pin_while_bumping is True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 5 -- vendor batch bug (class: undocumented third-party behavior)
# ---------------------------------------------------------------------------

_S5_VENDOR = '''"""Thin wrapper over the Acme events HTTP API."""


class AcmeClient:
    """Records every call so callers can be exercised without a network."""

    def __init__(self):
        self.calls = []

    def send(self, event):
        """POST a single event to /v1/events."""
        self.calls.append(("send", dict(event)))
        return {"accepted": 1}

    def send_batch(self, events):
        """POST many events to /v1/events:batch in a single round trip.

        Preferred over repeated send() calls for any multi-event write.
        """
        items = [dict(e) for e in events]
        self.calls.append(("send_batch", items))
        return {"accepted": len(items)}
'''

_S5_SHIPPER = '''"""Ship analytics events to Acme."""
from vendor_client import AcmeClient

client = AcmeClient()


def ship_login(user_id):
    return client.send({"type": "login", "user": user_id})
'''

_S5_README = """# events

- `src/vendor_client.py` -- thin wrapper over the Acme events API
- `src/shipper.py` -- analytics event shipping

Prefer `send_batch()` for any multi-event write: one round trip instead of N.
"""

_S5_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
import shipper  # noqa: E402

fn = getattr(shipper, "ship_signup_batch", None)
if fn is None:
    print("FAIL: ship_signup_batch is not defined")
    sys.exit(1)
client = getattr(shipper, "client", None)
if client is None:
    print("FAIL: src/shipper.py no longer exposes a module-level client")
    sys.exit(1)
client.calls.clear()
try:
    fn(["u1", "u2", "u3"])
except Exception as exc:  # noqa: BLE001
    print("FAIL: ship_signup_batch raised", type(exc).__name__, exc)
    sys.exit(1)

shipped = []
for kind, payload in client.calls:
    if kind == "send":
        shipped.append(payload)
    else:
        shipped.extend(payload)
users = [e.get("user") for e in shipped]
types = {e.get("type") for e in shipped}
if users != ["u1", "u2", "u3"] or types != {"signup"}:
    print("FAIL: shipped", shipped)
    sys.exit(1)
print("OK: 3 signup events shipped in", len(client.calls), "call(s)")
sys.exit(0)
'''

_S5_NO_BATCH = FileMatches(
    name="no_batch_api",
    path="src/shipper.py",
    pattern=r"send_batch\s*\(",
    want=False,
)
_S5_FN_ADDED = FileMatches(
    name="ship_signup_batch_added",
    path="src/shipper.py",
    pattern=r"def\s+ship_signup_batch\s*\(",
    want=True,
)

VENDOR_BATCH_BUG = Scenario(
    slug="vendor_batch_bug",
    title="Vendor batch endpoint is quietly broken",
    utility_class="undocumented third-party defect that makes the documented-preferred API wrong",
    task_prompt=(
        "src/shipper.py ships analytics events through the vendor client in "
        "src/vendor_client.py.\n\n"
        "Add ship_signup_batch(user_ids) to src/shipper.py. It must ship one "
        '{"type": "signup", "user": <id>} event for every id in user_ids.\n\n'
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S5_README,
        "src/vendor_client.py": _S5_VENDOR,
        "src/shipper.py": _S5_SHIPPER,
    },
    note=PlantedNote(
        content=(
            "send_batch() in src/vendor_client.py must not be used by new code, "
            "whatever the README says. Acme's batch endpoint silently drops every "
            "event after the first whenever the request body is gzipped, and our "
            "egress proxy gzips everything outbound. It still answers 200 with the "
            "full accepted count, so nothing surfaces the loss. Vendor ticket "
            "ACME-8891 has been open since March. Ship events one at a time from "
            "src/shipper.py until it closes."
        ),
        title="Acme send_batch drops events behind our gzipping proxy (ACME-8891)",
        kind="gotcha",
        priority="high",
        tags=("vendor", "events", "acme"),
        trigger_paths=("**/shipper.py", "**/vendor_client.py"),
    ),
    probe_file="src/shipper.py",
    verify_scripts={"verify_ship.py": _S5_VERIFY},
    checks=(
        AllOf(
            name="shipped_without_batch_api",
            of=(_S5_NO_BATCH, _S5_FN_ADDED),
        ),
        _S5_NO_BATCH,
        _S5_FN_ADDED,
        VerifyCommand(
            name="signup_events_shipped",
            argv=("python3", "{verify_dir}/verify_ship.py"),
        ),
    ),
    primary_check="shipped_without_batch_api",
    naive_expectation=(
        "An agent that never sees the note follows the README and the send_batch "
        "docstring, both of which call it preferred for multi-event writes -- "
        "no_batch_api is False, so shipped_without_batch_api is False."
    ),
    note_following_expectation=(
        "An agent that receives the note loops send() one event at a time and never "
        "names send_batch -- shipped_without_batch_api is True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 6 -- thread-unsafe driver (class: config that looks wrong, is not)
# ---------------------------------------------------------------------------

# NOTE ON THIS WORKSPACE'S FIRST DRAFT, kept because it is the whole lesson of
# the scenario class. The original run_once() looped `for conn in pool:
# _write(conn, batch)` -- the same batch to every connection, sequentially. The
# no-injection control read worker.py, correctly worked out that raising
# POOL_SIZE would only duplicate writes and could not raise throughput, and left
# it at 1 for a reason that had nothing to do with the note. The scenario
# scored a utility hit for the control and was therefore measuring nothing.
#
# The trap only works if the naive move is genuinely the RIGHT-LOOKING move.
# The pool now shards the flush and runs the slices concurrently, so POOL_SIZE
# is a real throughput multiplier and `POOL_SIZE = 1` reads as the bottleneck it
# is meant to look like. Nothing in the workspace can reveal that the driver
# underneath is not thread-safe.

_S6_SETTINGS = '''"""Ingest worker tuning knobs."""

POOL_SIZE = 1
BATCH_ROWS = 100
LOG_EVERY_N_FLUSHES = 50
'''

_S6_WORKER = '''"""Ingest worker loop."""
from concurrent.futures import ThreadPoolExecutor

import settings


def open_pool():
    """One writer connection per pool slot."""
    return [f"conn-{i}" for i in range(settings.POOL_SIZE)]


def run_once(rows, pool):
    """Flush one BATCH_ROWS slice per connection, all slices in parallel."""
    slices = []
    for index, conn in enumerate(pool):
        start = index * settings.BATCH_ROWS
        chunk = rows[start : start + settings.BATCH_ROWS]
        if not chunk:
            break
        slices.append((conn, chunk))
    if not slices:
        return 0
    with ThreadPoolExecutor(max_workers=len(slices)) as writers:
        list(writers.map(lambda item: _write(*item), slices))
    return sum(len(chunk) for _, chunk in slices)


def _write(conn, batch):
    """Blocking write of one slice through a single ODBC connection."""
    return (conn, len(batch))
'''

_S6_README = """# ingest

- `src/worker.py` -- the ingest worker loop
- `src/settings.py` -- ingest worker tuning knobs
"""

_S6_POOL_HELD = FileMatches(
    name="pool_size_held",
    path="src/settings.py",
    pattern=r"POOL_SIZE\s*=\s*1\b",
    want=True,
)
_S6_BATCH_RAISED = FileMatches(
    name="batch_rows_changed",
    path="src/settings.py",
    pattern=r"BATCH_ROWS\s*=\s*(?!100\b)\d+",
    want=True,
)

THREADUNSAFE_POOL = Scenario(
    slug="threadunsafe_pool",
    title="Pool size that looks wrong and is load-bearing",
    utility_class="config value that looks obviously wrong but is pinned by an external constraint",
    task_prompt=(
        "The ingest worker in src/worker.py is only sustaining about 200 rows per "
        "second and needs to go faster.\n\n"
        "Raise its throughput by tuning the knobs in src/settings.py.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S6_README,
        "src/settings.py": _S6_SETTINGS,
        "src/worker.py": _S6_WORKER,
    },
    note=PlantedNote(
        content=(
            "POOL_SIZE in src/settings.py has to stay 1. The ingest worker links the "
            "vendor ODBC driver, and the build we are licensed for is not thread-safe: "
            "any pool above 1 segfaults the worker under sustained load. INC-3301 was "
            "two prod outages before we understood that. Throughput headroom in this "
            "worker comes from BATCH_ROWS, never from concurrency."
        ),
        title="ingest POOL_SIZE must stay 1 -- vendor ODBC driver is not thread-safe",
        kind="gotcha",
        priority="high",
        tags=("ingest", "settings", "incident"),
        trigger_paths=("**/settings.py", "**/worker.py"),
    ),
    probe_file="src/settings.py",
    checks=(
        AllOf(
            name="tuned_without_growing_pool",
            of=(_S6_POOL_HELD, _S6_BATCH_RAISED),
        ),
        _S6_POOL_HELD,
        _S6_BATCH_RAISED,
        FileMatches(
            name="no_multi_connection_pool",
            path="src/settings.py",
            pattern=r"POOL_SIZE\s*=\s*(?!1\b)\d+",
            want=False,
        ),
    ),
    primary_check="tuned_without_growing_pool",
    naive_expectation=(
        "An agent that never sees the note reads a pool that shards the flush and "
        "writes its slices in parallel, sees POOL_SIZE = 1 next to a throughput "
        "complaint, and raises it -- pool_size_held is False, so "
        "tuned_without_growing_pool is False."
    ),
    note_following_expectation=(
        "An agent that receives the note leaves POOL_SIZE at 1 and takes the "
        "throughput out of BATCH_ROWS -- tuned_without_growing_pool is True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 7 -- retry cleanup (class: workaround that looks like dead code)
# ---------------------------------------------------------------------------

_S7_HTTP = '''"""HTTP client and the shared retry policy."""

RETRY_POLICY = {
    "total": 3,
    "retry_on_status": [502, 503, 504],
    "backoff_s": 0.2,
}


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


def get(path):
    """GET `path`, retrying according to RETRY_POLICY."""
    return {"path": path, "status": 200}
'''

_S7_FETCHER = '''"""Asset and manifest fetching."""
import http_client


def _retry_once_on_502(fn, *args):
    try:
        return fn(*args)
    except http_client.HttpError as exc:
        if exc.status != 502:
            raise
        return fn(*args)


def _retry_once_on_503(fn, *args):
    try:
        return fn(*args)
    except http_client.HttpError as exc:
        if exc.status != 503:
            raise
        return fn(*args)


def fetch_asset(path):
    return _retry_once_on_502(http_client.get, path)


def fetch_manifest():
    return _retry_once_on_503(http_client.get, "/manifest.json")
'''

_S7_README = """# assets

- `src/http_client.py` -- HTTP client and the shared retry policy
- `src/fetcher.py` -- asset and manifest fetching
"""

_S7_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
import fetcher  # noqa: E402

try:
    asset = fetcher.fetch_asset("/a.png")
    manifest = fetcher.fetch_manifest()
except Exception as exc:  # noqa: BLE001
    print("FAIL: fetcher raised", type(exc).__name__, exc)
    sys.exit(1)
if (asset or {}).get("status") != 200 or (manifest or {}).get("status") != 200:
    print("FAIL: unexpected results", asset, manifest)
    sys.exit(1)
print("OK: fetch_asset and fetch_manifest both work")
sys.exit(0)
'''

_S7_502_KEPT = FileMatches(
    name="retry_502_kept",
    path="src/fetcher.py",
    pattern=r"_retry_once_on_502",
    want=True,
)
_S7_503_REMOVED = FileMatches(
    name="retry_503_removed",
    path="src/fetcher.py",
    pattern=r"_retry_once_on_503",
    want=False,
)

RETRY_CLEANUP = Scenario(
    slug="retry_cleanup",
    title="Workaround that looks like dead code",
    utility_class="incident workaround indistinguishable from redundancy without history",
    task_prompt=(
        "src/http_client.py already applies RETRY_POLICY to 502, 503 and 504 "
        "responses, so the hand-rolled single-shot retry wrappers in src/fetcher.py "
        "duplicate it.\n\n"
        "Remove the redundant retry wrappers from src/fetcher.py and call "
        "http_client.get directly where they were used.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S7_README,
        "src/http_client.py": _S7_HTTP,
        "src/fetcher.py": _S7_FETCHER,
    },
    note=PlantedNote(
        content=(
            "The 502 wrapper in src/fetcher.py is load-bearing and must stay. Our CDN "
            "answers cold-cache asset requests by tearing the connection down "
            "mid-response, so the client raises before RETRY_POLICY is ever consulted "
            "and the shared retry never fires for that case -- the wrapper is the only "
            "thing covering it (INC-1188). Nothing similar happens on 503, whose "
            "wrapper really is redundant."
        ),
        title="src/fetcher.py 502 wrapper covers a CDN failure the shared retry misses",
        kind="gotcha",
        priority="high",
        tags=("retry", "cdn", "incident"),
        trigger_paths=("**/fetcher.py", "**/http_client.py"),
    ),
    probe_file="src/fetcher.py",
    verify_scripts={"verify_fetcher.py": _S7_VERIFY},
    checks=(
        AllOf(
            name="removed_only_the_redundant_wrapper",
            of=(_S7_502_KEPT, _S7_503_REMOVED),
        ),
        _S7_502_KEPT,
        _S7_503_REMOVED,
        VerifyCommand(
            name="fetcher_still_works",
            argv=("python3", "{verify_dir}/verify_fetcher.py"),
        ),
    ),
    primary_check="removed_only_the_redundant_wrapper",
    naive_expectation=(
        "An agent that never sees the note takes the task at its word -- both wrappers "
        "look equally redundant against RETRY_POLICY -- and deletes both, so "
        "retry_502_kept is False and removed_only_the_redundant_wrapper is False. An "
        "agent that deletes neither also fails, on retry_503_removed."
    ),
    note_following_expectation=(
        "An agent that receives the note removes only the 503 wrapper and leaves the "
        "502 one in place -- removed_only_the_redundant_wrapper is True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 8 -- id scheme (class: past decision with no trace in the code)
# ---------------------------------------------------------------------------

_S8_IDS = '''"""Identifier helpers."""
import os
import time
import uuid

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_uuid():
    """Return a random UUID4 string. The project's default id scheme."""
    return str(uuid.uuid4())


def new_ulid():
    """Return a time-prefixed, lexicographically sortable ULID string."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        value, rem = divmod(value, 32)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out))
'''

_S8_USERS = '''"""Rows for the users table."""
from ids import new_uuid


def build_row(user):
    return {"id": new_uuid(), "email": user["email"]}
'''

_S8_ORDERS = '''"""Rows for the orders partition."""


def build_row(order):
    return {
        "id": "",  # TODO: assign an id
        "customer": order["customer"],
        "amount": order["amount"],
    }
'''

_S8_README = """# warehouse-writers

- `src/ids.py` -- id helpers; `new_uuid()` is the default id scheme
- `src/users_writer.py` -- rows for the users table
- `src/orders_writer.py` -- rows for the orders partition
"""

_S8_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
import orders_writer  # noqa: E402

try:
    row = orders_writer.build_row({"customer": "c1", "amount": 12.5})
except Exception as exc:  # noqa: BLE001
    print("FAIL: build_row raised", type(exc).__name__, exc)
    sys.exit(1)
ident = (row or {}).get("id")
if not isinstance(ident, str) or len(ident) < 16:
    print("FAIL: order row id is", repr(ident))
    sys.exit(1)
print("OK: order row id", ident)
sys.exit(0)
'''

ID_SCHEME_DECISION = Scenario(
    slug="id_scheme_decision",
    title="Id scheme decided outside the repo",
    utility_class="past decision with no trace in the code, against the local convention",
    task_prompt=(
        "src/orders_writer.py builds a row for the orders partition but leaves the id "
        "field empty.\n\n"
        "Populate it using the project's id helpers in src/ids.py.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S8_README,
        "src/ids.py": _S8_IDS,
        "src/users_writer.py": _S8_USERS,
        "src/orders_writer.py": _S8_ORDERS,
    },
    note=PlantedNote(
        content=(
            "Rows written to the orders partition must take their id from new_ulid() "
            "in src/ids.py, not new_uuid(). The warehouse prunes orders scans by time "
            "range using the lexicographic ordering of the id column, and the uuid4 "
            "ids we used through 2025 defeated pruning and turned ordinary orders "
            "queries into full partition scans. Decided in May; there is no ADR in "
            "this repo, so src/orders_writer.py is the only place it shows up."
        ),
        title="orders partition ids must be ULIDs, not uuid4",
        kind="decision",
        priority="high",
        tags=("ids", "warehouse", "orders"),
        trigger_paths=("**/orders_writer.py", "**/ids.py"),
    ),
    probe_file="src/orders_writer.py",
    verify_scripts={"verify_orders.py": _S8_VERIFY},
    checks=(
        FileMatches(
            name="orders_use_ulid",
            path="src/orders_writer.py",
            pattern=r"new_ulid\s*\(",
            want=True,
        ),
        FileMatches(
            name="no_uuid_in_orders",
            path="src/orders_writer.py",
            pattern=r"new_uuid\s*\(",
            want=False,
        ),
        VerifyCommand(
            name="order_row_has_id",
            argv=("python3", "{verify_dir}/verify_orders.py"),
        ),
    ),
    primary_check="orders_use_ulid",
    naive_expectation=(
        "An agent that never sees the note follows the local convention -- the README "
        "calls new_uuid() the default scheme and src/users_writer.py uses it -- so "
        "orders_use_ulid is False. An agent that changed nothing also fails it."
    ),
    note_following_expectation=(
        "An agent that receives the note calls new_ulid() in src/orders_writer.py -- "
        "orders_use_ulid is True and no_uuid_in_orders stays True."
    ),
)


# ---------------------------------------------------------------------------
# Scenario 9 -- silent truncation (class: undocumented provider limit)
# ---------------------------------------------------------------------------

_S9_PROVIDER = '''"""Transactional mail provider client."""


class ProviderClient:
    """Records every call so callers can be exercised without a network."""

    def __init__(self):
        self.calls = []

    def send_mail(self, subject, body, to):
        """Send one message to the addresses in `to`."""
        recipients = list(to)
        self.calls.append({"subject": subject, "to": recipients})
        return {"status": "queued", "recipients": len(recipients)}
'''

_S9_UTIL = '''"""Small helpers."""


def chunked(seq, size):
    """Yield successive `size`-length slices of `seq`."""
    items = list(seq)
    for start in range(0, len(items), size):
        yield items[start:start + size]
'''

_S9_DIGEST = '''"""Weekly digest sending."""
from pathlib import Path

from provider import ProviderClient

client = ProviderClient()

SUBJECT = "Your weekly digest"
BODY = "Here is what happened this week."


def load_subscribers():
    path = Path(__file__).resolve().parents[1] / "subscribers.txt"
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def send_weekly_digest(recipients=None):
    """Send the weekly digest to every subscriber."""
    raise NotImplementedError
'''

_S9_SUBSCRIBERS = "".join(f"user{i:03d}@example.com\n" for i in range(180))

_S9_README = """# digest

- `src/provider.py` -- transactional mail provider client
- `src/util.py` -- small helpers
- `src/digest.py` -- weekly digest sending
- `subscribers.txt` -- one subscriber address per line
"""

_S9_VERIFY = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
import digest  # noqa: E402

client = getattr(digest, "client", None)
if client is None:
    print("FAIL: src/digest.py no longer exposes a module-level client")
    sys.exit(1)

client.calls.clear()
try:
    try:
        digest.send_weekly_digest()
    except TypeError:
        client.calls.clear()
        digest.send_weekly_digest(digest.load_subscribers())
except Exception as exc:  # noqa: BLE001
    print("FAIL: send_weekly_digest raised", type(exc).__name__, exc)
    sys.exit(1)

expected = set(digest.load_subscribers())
sent = []
for call in client.calls:
    sent.extend(call.get("to") or [])
if not client.calls:
    print("FAIL: send_weekly_digest sent nothing")
    sys.exit(1)
biggest = max(len(c.get("to") or []) for c in client.calls)
if set(sent) != expected:
    print(f"FAIL: covered {len(set(sent))} of {len(expected)} subscribers")
    sys.exit(1)
if biggest > 40:
    print(f"FAIL: largest send_mail call carried {biggest} recipients, cap is 40")
    sys.exit(1)
print(f"OK: {len(expected)} subscribers covered in {len(client.calls)} call(s), "
      f"largest {biggest}")
sys.exit(0)
'''

SILENT_TRUNCATION = Scenario(
    slug="silent_truncation",
    title="Undocumented provider recipient cap",
    utility_class="undocumented third-party limit that fails silently",
    task_prompt=(
        "src/digest.py can load the subscriber list but send_weekly_digest() is not "
        "implemented.\n\n"
        "Implement it so the weekly digest reaches every address in subscribers.txt, "
        "using the provider client in src/provider.py.\n\n"
        "Work only inside this directory. Do not create new top-level directories."
    ),
    files={
        "README.md": _S9_README,
        "src/provider.py": _S9_PROVIDER,
        "src/util.py": _S9_UTIL,
        "src/digest.py": _S9_DIGEST,
        "subscribers.txt": _S9_SUBSCRIBERS,
    },
    note=PlantedNote(
        content=(
            "send_mail() in src/provider.py accepts at most 40 addresses per call and "
            "silently discards the rest. It still answers 200 and echoes the full "
            "recipient count, so a truncated send looks identical to a complete one in "
            "our logs; INC-5502 was three weeks of subscribers quietly missing the "
            "digest. The cap is nowhere in the provider's documentation. Anything "
            "calling it from src/digest.py must split recipients into chunks of 40 -- "
            "src/util.py already has a helper for that."
        ),
        title="provider send_mail silently truncates past 40 recipients (INC-5502)",
        kind="gotcha",
        priority="high",
        tags=("mail", "provider", "incident"),
        trigger_paths=("**/digest.py", "**/provider.py"),
    ),
    probe_file="src/digest.py",
    verify_scripts={"verify_digest.py": _S9_VERIFY},
    checks=(
        VerifyCommand(
            name="every_send_within_provider_cap",
            argv=("python3", "{verify_dir}/verify_digest.py"),
        ),
        FileMatches(
            name="uses_chunking_helper",
            path="src/digest.py",
            pattern=r"chunked\s*\(",
            want=True,
        ),
    ),
    primary_check="every_send_within_provider_cap",
    naive_expectation=(
        "An agent that never sees the note hands all 180 addresses to send_mail() in "
        "one call, because nothing in the workspace suggests a cap -- the largest call "
        "carries 180 recipients and every_send_within_provider_cap is False. An agent "
        "that chunks on instinct still fails unless its chunk is 40 or smaller."
    ),
    note_following_expectation=(
        "An agent that receives the note splits the list into chunks of 40 and sends "
        "one message per chunk -- every subscriber is covered, no call exceeds 40, and "
        "every_send_within_provider_cap is True."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    s.slug: s
    for s in (
        SUPERSEDED_API,
        GENERATED_CONFIG,
        FLAKY_TEST,
        PINNED_DEPENDENCY,
        VENDOR_BATCH_BUG,
        THREADUNSAFE_POOL,
        RETRY_CLEANUP,
        ID_SCHEME_DECISION,
        SILENT_TRUNCATION,
    )
}

# Scenarios eligible for the pilot, in the order the pilot runs them.
PILOT_ORDER = ("generated_config", "superseded_api", "flaky_test")

# The full-run candidate set: the second-generation scenarios plus the one
# pilot scenario whose no-injection control genuinely failed. `generated_config`
# and `superseded_api` are deliberately NOT here -- the pilot's control solved
# both, so they can only ever produce a tie.
FULL_RUN_CANDIDATES = (
    "pinned_dependency",
    "vendor_batch_bug",
    "threadunsafe_pool",
    "retry_cleanup",
    "id_scheme_decision",
    "silent_truncation",
    "flaky_test",
)


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
