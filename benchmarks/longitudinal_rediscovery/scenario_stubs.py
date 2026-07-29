"""Declarative contract for EVAL-LONGITUDINAL-REDISCOVERY scenarios.

DESIGN SCAFFOLDING, NOT A HARNESS. This module contains dataclasses (pure data) and
one fully worked scenario as the authoring exemplar. It contains no runner, no scorer,
and no evaluation logic -- those are the coder lane's deliverables (DESIGN.md section
11). The evaluation semantics of every field are specified in DESIGN.md and must not be
re-invented here.

The check/metric primitives (FileMatches, FileMatchCountAtMost, FileUnchanged,
CommandRan, VerifyCommand, AllOf, CommandCount) are REUSED from
benchmarks/injection_utility/scenarios.py rather than redefined: the trap harness
already proved them out, and a second definition would drift. The primitives added
below are the ones a multi-session eval needs and that one does not -- ordered ACTION
patterns, per-leg specs, and note variants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# Action patterns -- matched against the ORDERED tool_use stream (DESIGN.md 6.2)
#
# An "action" is a tool_use block in an assistant event, in transcript order. Text
# blocks are never actions: prose is not evidence, and no LLM judges a run.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BashAction:
    """Matches a Bash tool_use whose `command` input matches `pattern`."""

    pattern: str


@dataclass(frozen=True)
class PathAction:
    """Matches a tool_use by `tools` whose path input matches `pattern`.

    Path inputs are read from the keys `file_path`, `path`, `notebook_path` -- the
    same tool-input keys vectr's own window assembly trusts. Free text is never
    scanned for paths.
    """

    tools: tuple[str, ...]
    pattern: str


@dataclass(frozen=True)
class ContentAction:
    """Matches a mutating tool_use by path AND by the content being written.

    Content is read from `new_string` (Edit) or `content` (Write). Exists for
    mistakes that are only mistakes because of what was written -- e.g. adding
    `--ignore=legacy` to a pytest config, as opposed to touching the file at all.
    """

    tools: tuple[str, ...]
    path_pattern: str
    text_pattern: str


@dataclass(frozen=True)
class ToolAction:
    """Matches any use of the named tools.

    Used for adoption counting in arm B (`mcp__vectr__*`), never for a pass/fail
    verdict: whether the agent chooses to recall is a measured OUTCOME, not a gate.
    """

    names: tuple[str, ...]


@dataclass(frozen=True)
class FileMutated:
    """STATE, not an action: sha256 of `path` differs from the LEG-START baseline.

    Leg-start, not scenario-start. Within a trajectory the workspace carries forward,
    so leg k's baselines are recomputed from the restored snapshot at leg k's start
    (DESIGN.md 6.2). Grading leg 3 against leg 1's bytes would score leg 2's own
    correct work as a mistake.
    """

    path: str


ActionPattern = BashAction | PathAction | ContentAction | ToolAction


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteVariant:
    """One rung of the verifiable-notes ladder (DESIGN.md section 7).

    INVARIANTS, asserted by tests, not by convention:

    * `content` contains the scenario's `fact_sentence` BYTE-IDENTICALLY. The A/B
      varies the provenance trail and nothing else; a reworded fact would confound
      the experiment it exists to run.
    * `content` names the note's anchor file path in its body text. The structural
      matcher keys on paths MENTIONED IN THE NOTE TEXT, not on declared trigger
      globs -- verified by direct probe in the trap harness. A note that names no
      path cannot fire structurally, and the cell would be vacuous.
    * `anchors` is set only on the `verifiable` rung, and only on corroborable
      scenarios. It uses vectr's real anchors field (content-hashed at write time),
      not a simulation.
    """

    variant: str  # "plain" | "provenance" | "verifiable"
    title: str
    content: str
    anchors: tuple[str, ...] = ()
    verify_hint: str = ""
    kind: str = "gotcha"
    priority: str = "high"
    tags: tuple[str, ...] = ()
    trigger_paths: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Legs and scenarios
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegSpec:
    """One session in the sequence.

    Each leg is a fresh `claude -p` with no --resume and no --continue: nothing but
    the workspace and the vectr store crosses the boundary. Checks and ground-truth
    patterns are PER LEG because the tasks differ -- leg 2's correct outcome is not
    leg 1's.
    """

    prompt: str
    checks: Sequence[object]  # injection_utility primitives; AllOf for the primary
    primary_check: str
    fact_acquisition: tuple[ActionPattern, ...]
    mistake_signature: tuple[ActionPattern | FileMutated, ...]
    rediscovery_work: tuple[ActionPattern, ...]
    probe_files: tuple[str, ...]
    arm_a_expectation: str
    memory_arm_expectation: str
    verify_scripts: Mapping[str, str] = field(default_factory=dict)
    metrics: Sequence[object] = ()
    is_forcing_leg: bool = False  # leg 1 of a DISCOVERED scenario; see LongitudinalScenario


@dataclass(frozen=True)
class LongitudinalScenario:
    slug: str
    title: str
    origin: str  # "discovered" | "told"
    corroborable: bool
    fact_sentence: str
    fact_tokens: tuple[str, ...]
    files: Mapping[str, str]
    legs: tuple[LegSpec, ...]  # len >= 3; tiers run a prefix
    note_variants: tuple[NoteVariant, ...]
    agent_cwd: str = "."
    executable: tuple[str, ...] = ()
    git_init: bool = False
    ignore_paths: tuple[str, ...] = ()  # vectr-written config; excluded from state diffs

    def __post_init__(self) -> None:
        if len(self.legs) < 3:
            raise ValueError(f"{self.slug}: a longitudinal scenario needs >= 3 legs")
        if self.origin not in ("discovered", "told"):
            raise ValueError(f"{self.slug}: origin must be 'discovered' or 'told'")
        if self.origin == "told" and self.corroborable:
            raise ValueError(
                f"{self.slug}: a TOLD scenario is uncorroborable by construction -- if "
                f"the workspace can corroborate the fact, the no-memory arm can "
                f"re-derive it and the class collapses"
            )
        if not self.corroborable and any(v.variant == "verifiable" for v in self.note_variants):
            raise ValueError(
                f"{self.slug}: a 'verifiable' rung needs something checkable to exist; "
                f"uncorroborable scenarios run the 2-rung ladder (plain, provenance)"
            )
        for v in self.note_variants:
            if self.fact_sentence not in v.content:
                raise ValueError(
                    f"{self.slug}/{v.variant}: note body must contain fact_sentence "
                    f"byte-identically -- the A/B varies the trail, never the fact"
                )


# ---------------------------------------------------------------------------
# S1 release_via_ci -- the authoring exemplar (DESIGN.md section 3, S1)
#
# Shape: PyPI-via-CI-only. Origin DISCOVERED, corroborable. Primary metric is
# RE-DISCOVERY COST. Workspace files below are abbreviated to the bytes the checks
# and the discovery path actually depend on; the coder lane fills in the inert
# remainder (module bodies, changelog prose) at will, subject to the leak test:
# no fact_token may appear in any file except .github/workflows/release.yml.
# ---------------------------------------------------------------------------

_S1_FACT = (
    "Releases of ledgerkit are cut by pushing an annotated tag vX.Y.Z; "
    ".github/workflows/release.yml builds and publishes to PyPI via Trusted "
    "Publishing. This project has no PyPI API tokens, so a local `python -m build` + "
    "`twine upload` cannot authenticate and must never be attempted."
)

_S1_RELEASE_YML = """name: release
on:
  push:
    tags: ['v*']

permissions:
  id-token: write   # Trusted Publishing. No API tokens are issued for this project:
                    # publishing is possible ONLY from this workflow.

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
"""

_S1_PYPROJECT = """[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "ledgerkit"
version = "1.3.2"
description = "Currency conversion helpers."
"""

_S1_INIT = '''"""ledgerkit."""

__version__ = "1.3.2"
'''

_S1_RATES = '''"""Conversion helpers."""

DEFAULT_PRECISION = 1  # FIXME: rounding to 1 dp loses cents on every conversion


def convert(amount, rate):
    """Convert `amount` at `rate`."""
    return round(float(amount) * float(rate), DEFAULT_PRECISION)
'''

# Verify script: materialized OUTSIDE the workspace, AFTER the agent exits, so the
# no-memory arm can never read the answer out of its own working tree.
_S1_VERIFY_TAG = '''import subprocess
import sys

want = sys.argv[1]
out = subprocess.run(
    ["git", "tag", "-l", want], capture_output=True, text=True
).stdout.strip()
sys.exit(0 if out == want else 1)
'''

S1_RELEASE_VIA_CI = LongitudinalScenario(
    slug="release_via_ci",
    title="Releases go through CI on a tag push, never a local upload",
    origin="discovered",
    corroborable=True,
    fact_sentence=_S1_FACT,
    fact_tokens=("Trusted Publishing", "no PyPI API tokens", "annotated tag"),
    git_init=True,
    executable=("scripts/preflight.sh",),
    files={
        "pyproject.toml": _S1_PYPROJECT,
        "src/ledgerkit/__init__.py": _S1_INIT,
        "src/ledgerkit/rates.py": _S1_RATES,
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- Round conversions to cents\n\n## 1.3.2\n\n- Initial release\n",
        "CLAUDE.md": "# ledgerkit\n\nCurrency conversion helpers. Keep `src/ledgerkit/` importable without extras.\n",
        ".github/workflows/ci.yml": "name: ci\non: [pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n",
        ".github/workflows/release.yml": _S1_RELEASE_YML,
        "scripts/preflight.sh": "#!/bin/sh\n# Check the version is consistent across pyproject and __init__.\nexit 0\n",
    },
    note_variants=(
        NoteVariant(
            variant="plain",
            title="ledgerkit releases are cut by tag push, not local upload",
            content=_S1_FACT,
            trigger_paths=("**/release.yml", "**/pyproject.toml"),
        ),
        NoteVariant(
            variant="provenance",
            title="ledgerkit releases are cut by tag push, not local upload",
            content=(
                _S1_FACT
                + "\nEstablished 2026-07-12 in session 1 of this workspace, after "
                "`twine upload` was rejected (no API token exists for this project)."
            ),
            trigger_paths=("**/release.yml", "**/pyproject.toml"),
        ),
        NoteVariant(
            variant="verifiable",
            title="ledgerkit releases are cut by tag push, not local upload",
            content=(
                _S1_FACT
                + "\nEstablished 2026-07-12 in session 1 of this workspace, after "
                "`twine upload` was rejected (no API token exists for this project)."
                + '\nVerify: grep -n "id-token" .github/workflows/release.yml'
            ),
            anchors=(".github/workflows/release.yml",),
            verify_hint='grep -n "id-token" .github/workflows/release.yml',
            trigger_paths=("**/release.yml", "**/pyproject.toml"),
        ),
    ),
    legs=(
        LegSpec(
            # Leg 1 carries the FORCING STEP. Without it a session can end having
            # quietly committed the error and established nothing -- and every arm's
            # leg 2 restores this leg's end state, so a hollow leg 1 would poison the
            # entire eval. The clause appears here and in no later prompt (asserted).
            prompt=(
                "Release version 1.4.0 -- the changelog's Unreleased section already "
                "lists the changes. Run scripts/preflight.sh before you finish."
            ),
            is_forcing_leg=True,
            primary_check="released_via_tag_1_4_0",
            checks=(),  # AllOf(...) + VerifyCommand(...) -- see DESIGN.md section 3, S1
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.4\.0\b"),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.4\.0\b)"),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b"
                ),
                PathAction(("Write", "Edit"), r"\.pypirc$"),
                ContentAction(
                    ("Write", "Edit"),
                    r"\.github/workflows/.*\.ya?ml$",
                    r"twine|password|__token__",
                ),
            ),
            rediscovery_work=(
                PathAction(("Read", "Grep"), r"\.github/workflows/release\.ya?ml$"),
                BashAction(r"grep[^\n]*(twine|pypi|publish)"),
                BashAction(r"\bpip\s+show\s+twine\b|which\s+twine"),
            ),
            probe_files=(".github/workflows/release.yml", "pyproject.toml"),
            verify_scripts={"verify_tag.py": _S1_VERIFY_TAG},
            arm_a_expectation=(
                "Attempts a local build+upload before reading release.yml; "
                "mistake_committed=True, several turns of rediscovery_work."
            ),
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm, which is "
                "exactly why this leg is shared across arms (DESIGN.md 5.2)."
            ),
        ),
        LegSpec(
            prompt=(
                "1.4.0 shipped the bad default flagged by the FIXME in "
                "src/ledgerkit/rates.py. Fix it and release 1.4.1."
            ),
            primary_check="released_via_tag_1_4_1",
            checks=(),
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.4\.1\b"),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.4\.1\b)"),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b"
                ),
                PathAction(("Write", "Edit"), r"\.pypirc$"),
            ),
            rediscovery_work=(
                PathAction(("Read", "Grep"), r"\.github/workflows/release\.ya?ml$"),
                BashAction(r"grep[^\n]*(twine|pypi|publish)"),
            ),
            probe_files=("src/ledgerkit/rates.py", "pyproject.toml"),
            verify_scripts={"verify_tag.py": _S1_VERIFY_TAG},
            arm_a_expectation="Repeats the local-upload attempt; re-reads release.yml.",
            memory_arm_expectation="Tags directly. turns_to_fact <= 2, no upload command.",
        ),
        LegSpec(
            prompt=(
                "Add a convert_bulk() helper to src/ledgerkit/rates.py with a "
                "changelog entry, then release 1.5.0."
            ),
            primary_check="released_via_tag_1_5_0",
            checks=(),
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.5\.0\b"),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.5\.0\b)"),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b"
                ),
                PathAction(("Write", "Edit"), r"\.pypirc$"),
            ),
            rediscovery_work=(
                PathAction(("Read", "Grep"), r"\.github/workflows/release\.ya?ml$"),
                BashAction(r"grep[^\n]*(twine|pypi|publish)"),
            ),
            probe_files=("src/ledgerkit/rates.py", "CHANGELOG.md"),
            verify_scripts={"verify_tag.py": _S1_VERIFY_TAG},
            arm_a_expectation="Third independent re-discovery of the same fact.",
            memory_arm_expectation="Tags directly; the saving should not decay with k.",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Fix the convert() docstring and release 1.5.1.",
            primary_check="released_via_tag_1_5_1",
            checks=(),
            fact_acquisition=(BashAction(r"git\s+tag\b[^\n]*\bv?1\.5\.1\b"),),
            mistake_signature=(
                BashAction(r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"),
            ),
            rediscovery_work=(
                PathAction(("Read", "Grep"), r"\.github/workflows/release\.ya?ml$"),
            ),
            probe_files=("src/ledgerkit/rates.py",),
            verify_scripts={"verify_tag.py": _S1_VERIFY_TAG},
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S2-S6 -- authored by the coder lane from DESIGN.md section 3, which specifies
# every field of each (workspace layout, fact sentence, four prompts, acquisition /
# mistake / rediscovery patterns, primary check, anchor + verify hint).
#
#   S2 spec_lives_outside          DISCOVERED, corroborable, agent_cwd="orbit"
#   S3 runner_not_pytest           DISCOVERED, corroborable
#   S4 secrets_not_dotenv          DISCOVERED, corroborable
#   S5 deploy_reverted_by_reconciler  TOLD, uncorroborable, 2-rung ladder
#   S6 bench_box_only              TOLD, uncorroborable, 2-rung ladder
#
# S2 is the only one with a structural novelty: the workspace root holds BOTH the
# repo (`orbit/`, the agent's cwd) and its sibling docs tree (`orbit-docs/`), which
# is what `agent_cwd` exists for. File checks stay workspace-root-relative, so the
# external file is addressable as "orbit-docs/spec.md".
# ---------------------------------------------------------------------------

SCENARIO_ORDER = (
    "release_via_ci",
    "spec_lives_outside",
    "runner_not_pytest",
    "secrets_not_dotenv",
    "deploy_reverted_by_reconciler",
    "bench_box_only",
)

HEADLINE_SCENARIOS = ("release_via_ci", "deploy_reverted_by_reconciler")

SCENARIOS: dict[str, LongitudinalScenario] = {
    S1_RELEASE_VIA_CI.slug: S1_RELEASE_VIA_CI,
}
