"""EVAL-LONGITUDINAL-REDISCOVERY -- scenario definitions and generator-owned ground truth.

Promoted from `scenario_stubs.py` (DESIGN.md section 11, step 1): the dataclasses and
the worked S1 scenario are unchanged; S1's `checks` fields (left as placeholders in the
stub) are filled in here, and S2-S6 are authored from DESIGN.md section 3.

The check/metric primitives (FileMatches, FileMatchCountAtMost, FileUnchanged,
CommandRan, VerifyCommand, AllOf, CommandCount, sha256_file) are REUSED from
`benchmarks/injection_utility/scenarios.py` by import, never redefined -- the trap
harness already proved them out and a second definition would drift. One primitive is
new here: `FileMatchCountAtLeast` (see its docstring) -- the multi-session eval's
residue rule (DESIGN.md 2.2) needs a MINIMUM-occurrence bound that the single-session
trap harness never did, in exactly the same spirit as `FileMatchCountAtMost`'s existing
maximum bound.

What a scenario is
-------------------
A `LongitudinalScenario` is `(workspace files, fact_sentence, note_variants, legs)`.
Each `LegSpec` is one session in the trajectory: a prompt, checks over that session's
final workspace state, and the action-stream ground truth (`fact_acquisition`,
`mistake_signature`, `rediscovery_work`) the scorer (`scorer.py`) matches against the
transcript. No LLM judges a run; every verdict comes from bytes on disk, commands run,
or a verify script's exit code (DESIGN.md 6.1).

Verify scripts are materialized OUTSIDE the workspace, only after a leg's agent has
finished -- see `materialize_verifiers` -- for the same reason the trap harness does
this: shipping them inside the workspace would let the no-memory arm read the answer
out of its own working tree.
"""

from __future__ import annotations

import importlib.util
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


def _load_trap_harness_scenarios():
    """Load `benchmarks/injection_utility/scenarios.py` under a private module name.

    Both this file and the trap harness's are named `scenarios.py`; a plain
    `sys.path.insert` + `from scenarios import ...` would key off the bare module
    name and re-enter THIS partially-initialized module instead (observed:
    `ImportError: cannot import name 'AllOf' from partially initialized module
    'scenarios' (most likely due to a circular import)`). Loading by explicit file
    path sidesteps the name collision entirely, regardless of what else is on
    sys.path.
    """
    path = Path(__file__).resolve().parents[1] / "injection_utility" / "scenarios.py"
    spec = importlib.util.spec_from_file_location("_longitudinal_trap_harness_scenarios", path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses (3.14) resolves a class's module via sys.modules[cls.__module__]
    # while building it, so the module must be registered before exec_module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_trap = _load_trap_harness_scenarios()
AllOf = _trap.AllOf
CommandCount = _trap.CommandCount
CommandRan = _trap.CommandRan
FileMatchCountAtMost = _trap.FileMatchCountAtMost
FileMatches = _trap.FileMatches
FileUnchanged = _trap.FileUnchanged
VerifyCommand = _trap.VerifyCommand
sha256_file = _trap.sha256_file

__all__ = [
    "AllOf",
    "CommandCount",
    "CommandRan",
    "FileMatchCountAtMost",
    "FileMatchCountAtLeast",
    "FileMatches",
    "FileUnchanged",
    "VerifyCommand",
    "sha256_file",
    "BashAction",
    "PathAction",
    "ContentAction",
    "ToolAction",
    "FileMutated",
    "ActionPattern",
    "NoteVariant",
    "LegSpec",
    "LongitudinalScenario",
    "SCENARIOS",
    "SCENARIO_ORDER",
    "HEADLINE_SCENARIOS",
    "get",
    "materialize",
    "materialize_verifiers",
]


# ---------------------------------------------------------------------------
# One new check primitive: the residue-carry-forward companion to
# FileMatchCountAtMost (DESIGN.md 2.2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMatchCountAtLeast:
    """True when `pattern` occurs at least `minimum` times in `path`.

    Within a trajectory the workspace persists across legs, so a plain presence
    check (`FileMatches`) cannot tell "this leg added a new compliant entry" from
    "a prior leg's entry is still sitting there" -- a non-compliant leg *k* that does
    nothing would still pass a bare presence check because leg *k-1*'s entry already
    satisfies it. A scenario author who controls exactly what a compliant leg adds can
    instead bound the CUMULATIVE occurrence count leg by leg. This is the exact mirror
    of `FileMatchCountAtMost`'s existing maximum bound -- same mechanism, opposite
    direction -- and, like it, is a structural count over final file bytes, not a
    query-content-conditional heuristic: the threshold is authored once per leg at
    scenario-design time, never derived from the agent's prompt or behavior at run time.
    """

    name: str
    path: str
    pattern: str
    minimum: int


# ---------------------------------------------------------------------------
# Action patterns -- matched against the ORDERED tool_use stream (DESIGN.md 6.2)
#
# An "action" is a tool_use block in an assistant event, in transcript order. Text
# blocks are never actions: prose is not evidence, and no LLM judges a run.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BashAction:
    """Matches a Bash tool_use whose `command` input matches `pattern`.

    `exec_anchor=True` (DEFECT 9) restricts the match to genuine command-execution
    positions in `command` -- string start, or immediately after `; && || | &` or a
    newline, through any interpreter/exec-wrapper prefix (`sh`/`bash`/`zsh`/`exec`,
    `env VAR=... ` chains, `timeout N`, `caffeinate -flags`) -- instead of matching
    `pattern` anywhere in the string. Use it whenever the signature's semantic is
    "the agent RAN this" (a mistake_signature or a fact_acquisition that claims the
    agent performed an action): `re.search` alone cannot tell `./deploy.sh` (ran)
    apart from `cat deploy.sh` (read) or `echo "run ./deploy.sh"` (mentioned). Leave
    it False (default) for lookup-only semantics where merely referencing the text
    counts as engagement -- e.g. `rediscovery_work` grep/cat patterns, or S2's
    `orbit-docs/` fact_acquisition, which is deliberately loose (see scorer.py's
    `_matches_at_exec_position` docstring for the matching algorithm).
    """

    pattern: str
    exec_anchor: bool = False


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
    # DEFECT 9: names a check in `checks` (searched recursively through any AllOf's
    # sub_checks) that is independent file-state evidence for this leg's mistake --
    # e.g. S5's "deploy_state_untouched" (a FileUnchanged on the deploy-state file).
    # When set, scorer.detect_contradictions() flags the case where the action-stream
    # mistake_signature fired but this check PASSED (the tracked file provably never
    # changed) as a loud, unresolved contradiction rather than trusting either signal
    # silently. Authored once per leg at scenario-design time, same discipline as
    # every other ground-truth field on this class; left None where no independent
    # state signal exists for the mistake (most legs -- e.g. S1's "no local upload"
    # has no corroborating file-state check to pair with).
    mistake_state_check: str | None = None


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
    # DEFECT 10 (direction 1, user decision 2026-07-30, see DESIGN.md 6.5): paths
    # (workspace-root-relative, matching `files` keys) whose content a completed leg
    # k-1 may leave in a state that makes a LATER leg's own primary check or fact
    # vacuously pre-satisfied -- e.g. S5's queue file, appended-to by every leg's
    # correct task, never edited in place, so leg k's own compliant addition is
    # otherwise indistinguishable from leg k-1's residue. `run_leg.py`'s
    # `LegRunner._apply_critical_residue_reset()` restores exactly these paths to
    # their `files` seed content at the START of every k>=2 leg, AFTER the restore
    # manifest's integrity check (so DEFECT 9-adjacent tar-fidelity verification is
    # unaffected) and BEFORE the agent starts. Every other path is left alone: most
    # scenarios declare none of these (each leg's task already targets a distinct
    # artifact/value, so no reset is needed -- see scenarios.py's per-scenario
    # comments for the audited rationale), and declaring one here is a narrower,
    # opt-in complement to `ignore_paths` and the general residue rule (2.2), not a
    # replacement for either.
    critical_residue_paths: tuple[str, ...] = ()

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
        for p in self.critical_residue_paths:
            if p not in self.files:
                raise ValueError(
                    f"{self.slug}: critical_residue_paths entry {p!r} has no matching "
                    f"`files` seed to restore from"
                )

    def anchor_files(self) -> tuple[str, ...]:
        """Paths this scenario's `verifiable` note variant(s) declare as anchors.

        The fact-token leak test (DESIGN.md 5.4, 11.6) allows a `fact_token` to appear
        in exactly these files: a DISCOVERED scenario's fact is by definition
        recoverable from the workspace at real cost, and the anchor file is where that
        recovery happens (e.g. S1's `.github/workflows/release.yml` literally contains
        "Trusted Publishing"). Uncorroborable scenarios never set `anchors`, so this is
        empty for them, which collapses the exception to "nowhere" -- matching S5's own
        stated invariant, "Nothing in the workspace states the fact." No special-casing
        by origin is needed; it falls out of this one query.
        """
        return tuple(dict.fromkeys(p for v in self.note_variants for p in v.anchors))


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def materialize(scenario: LongitudinalScenario, workspace: Path) -> dict[str, str]:
    """Write the scenario's starting workspace and return {relpath: sha256}.

    Paths in `scenario.files` are always WORKSPACE-ROOT-relative, even for S2's
    external `orbit-docs/` tree, which sits beside `orbit/` (the git repo, and
    `agent_cwd`) at this same root (DESIGN.md section 3, S2). The returned map is
    leg 1's baseline; every later leg's baseline is instead read live off the
    restored snapshot at that leg's start (`FileUnchanged`/`FileMutated` are always
    leg-start-relative -- DESIGN.md 6.2), which is `run_leg.py`'s job, not this one.
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
    if scenario.git_init:
        repo = workspace / scenario.agent_cwd
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        subprocess.run(
            [
                "git",
                "-c", "user.email=longitudinal-harness@vectr.local",
                "-c", "user.name=longitudinal-harness",
                "commit", "-q", "-m", "initial",
            ],
            cwd=str(repo),
            check=True,
        )
    return baselines


def materialize_verifiers(leg: LegSpec, verify_dir: Path) -> None:
    """Write one leg's verify scripts into a harness-owned directory OUTSIDE the
    workspace, called only AFTER that leg's agent has finished -- see the module
    docstring.
    """
    verify_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in leg.verify_scripts.items():
        target = verify_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)


# ---------------------------------------------------------------------------
# S1 release_via_ci -- DISCOVERED, corroborable (required shape: PyPI-via-CI-only)
# ---------------------------------------------------------------------------

_S1_FACT = (
    "Releases of ledgerkit are cut by pushing an annotated tag vX.Y.Z; "
    ".github/workflows/release.yml builds and publishes to PyPI via Trusted "
    "Publishing. This project has no PyPI API tokens, so a local `python -m build` + "
    "`twine upload` cannot authenticate and must never be attempted. The version must "
    "be bumped in pyproject.toml and src/ledgerkit/__init__.py with a CHANGELOG.md "
    "entry before tagging."
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


def _s1_leg_checks(version: str) -> tuple[object, object]:
    """AllOf + sibling VerifyCommand for one S1 leg (DESIGN.md section 3, S1).

    Every leg's "released correctly" outcome has the same shape: the version landed
    in pyproject.toml, __init__.py and CHANGELOG.md; no local upload command ran; and
    the annotated tag actually exists in the repo (checked by a verify script, since
    `VerifyCommand` only reads an exit code and `git tag -l` alone would exit 0 even
    when it prints nothing -- see DESIGN.md section 3, S1). Only the version string
    changes leg to leg, which is exactly the `_s1_leg_checks`/`primary_check` pattern
    scenario_stubs.py's leg specs already assume (`released_via_tag_1_4_0`, etc.).
    """
    esc = version.replace(".", r"\.")
    allof = AllOf(
        f"released_via_tag_{version.replace('.', '_')}",
        (
            FileMatches("version_bumped_pyproject", "pyproject.toml", rf'version\s*=\s*"{esc}"'),
            FileMatches(
                "version_bumped_init", "src/ledgerkit/__init__.py", rf'__version__\s*=\s*"{esc}"'
            ),
            FileMatches("changelog_entry", "CHANGELOG.md", rf"##\s*{esc}"),
            CommandRan(
                "no_local_upload",
                r"twine\s+upload|uv\s+publish|poetry\s+publish",
                want=False,
                exec_anchor=True,
            ),
        ),
    )
    verify = VerifyCommand(
        "annotated_tag_exists", ("python3", "{verify_dir}/verify_tag.py", f"v{version}")
    )
    return (allof, verify)


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
            checks=_s1_leg_checks("1.4.0"),
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.4\.0\b", exec_anchor=True),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.4\.0\b)", exec_anchor=True),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b",
                    exec_anchor=True,
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
            checks=_s1_leg_checks("1.4.1"),
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.4\.1\b", exec_anchor=True),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.4\.1\b)", exec_anchor=True),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b",
                    exec_anchor=True,
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
            checks=_s1_leg_checks("1.5.0"),
            fact_acquisition=(
                BashAction(r"git\s+tag\b[^\n]*\bv?1\.5\.0\b", exec_anchor=True),
                BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.5\.0\b)", exec_anchor=True),
            ),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b"
                    r"|\bhatch\s+publish\b|\bflit\s+publish\b",
                    exec_anchor=True,
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
            checks=_s1_leg_checks("1.5.1"),
            fact_acquisition=(BashAction(r"git\s+tag\b[^\n]*\bv?1\.5\.1\b", exec_anchor=True),),
            mistake_signature=(
                BashAction(
                    r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b",
                    exec_anchor=True,
                ),
            ),
            rediscovery_work=(
                PathAction(("Read", "Grep"), r"\.github/workflows/release\.ya?ml$"),
            ),
            probe_files=("src/ledgerkit/rates.py", "pyproject.toml"),
            verify_scripts={"verify_tag.py": _S1_VERIFY_TAG},
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S2 spec_lives_outside -- DISCOVERED, corroborable (required shape: file-lives-elsewhere)
#
# The only scenario with a structural novelty: the workspace root holds BOTH the repo
# (`orbit/`, the agent's cwd) and its sibling docs tree (`orbit-docs/`). File checks
# stay workspace-root-relative (DESIGN.md section 3, S2), so the external file is
# addressable as "orbit-docs/spec.md".
#
# Residue design note: the mirror at orbit/docs/spec.md is ALREADY STALE relative to
# orbit-docs/spec.md at leg-1 start, so `make check` fails immediately for ANY agent,
# regardless of what it does -- that failure message is the discovery path. A
# compliant agent's job is to edit the SOURCE and never touch the mirror at all; the
# forcing step's purpose is only to make the agent RUN the check (whose failure
# teaches the fact), not to make the check ultimately pass.
# ---------------------------------------------------------------------------

_S2_FACT = (
    "The authoritative spec and task list for orbit live outside the repository, at "
    "`../orbit-docs/spec.md` and `../orbit-docs/tasks.md`. `orbit/docs/spec.md` is a "
    "generated mirror -- editing it is overwritten by `make docs-sync` and fails "
    "`tools/docs_lint.py` in CI."
)

_S2_DOCS_LINT = '''#!/usr/bin/env python3
"""Fails if docs/spec.md has drifted from the authoritative source.

docs/spec.md is a generated mirror of $DOCS_ROOT/spec.md (DOCS_ROOT defaults to
../orbit-docs). It exists so the in-repo tree has a readable copy; it is never the
place to make an edit -- edit the source, then `make docs-sync`.
"""
import hashlib
import os
import sys
from pathlib import Path

DOCS_ROOT = Path(os.environ.get("DOCS_ROOT", "../orbit-docs"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    mirror = Path("docs/spec.md")
    source = DOCS_ROOT / "spec.md"
    if not source.is_file():
        print(f"docs_lint: source not found at {source}", file=sys.stderr)
        return 1
    if not mirror.is_file() or _sha256(mirror) != _sha256(source):
        print(
            "docs/spec.md is a generated mirror of $DOCS_ROOT/spec.md "
            "-- edit the source, then `make docs-sync`",
            file=sys.stderr,
        )
        return 1
    print("docs_lint: mirror matches source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_S2_MAKEFILE = (
    ".PHONY: check docs-sync\n"
    "\n"
    "check:\n"
    "\tpython3 tools/docs_lint.py\n"
    "\n"
    "docs-sync:\n"
    "\tcp $${DOCS_ROOT:-../orbit-docs}/spec.md docs/spec.md\n"
)

_S2_SPEC_MIRROR = (
    "# orbit spec (mirror)\n\n"
    "## Overview\n\nA small job scheduler.\n\n"
    "## Rate limits\n\nAPI calls are limited to 100 req/min.\n"
)

_S2_SPEC_SOURCE_BASE = (
    "# orbit spec\n\n"
    "## Overview\n\nA small job scheduler.\n\n"
    "## Rate limits\n\nAPI calls are limited to 100 req/min.\n"
)

_S2_LIMITS = (
    '"""Retry and rate-limit constants."""\n\n'
    "RATE_LIMIT_PER_MIN = 60\n"
    "MAX_RETRIES = 3\n"
    "RETRY_BACKOFF_S = 1.5\n"
)

_S2_SCHEDULER = (
    '"""Job scheduling."""\n\n'
    "from orbit.limits import MAX_RETRIES, RETRY_BACKOFF_S\n\n\n"
    "def schedule(job, attempt=0):\n"
    '    """Run `job`, retrying with backoff up to MAX_RETRIES times."""\n'
    "    try:\n"
    "        return job()\n"
    "    except Exception:\n"
    "        if attempt >= MAX_RETRIES:\n"
    "            raise\n"
    "        return schedule(job, attempt + 1)\n"
)

_S2_FACT_ACQUISITION = (
    PathAction(("Read", "Grep", "Edit", "Write"), r"orbit-docs/"),
    BashAction(r"orbit-docs/"),
)
_S2_MISTAKE_SIGNATURE = (
    FileMutated("orbit/docs/spec.md"),
    PathAction(("Edit", "Write"), r"orbit/docs/spec\.md$"),
)
_S2_REDISCOVERY_WORK = (
    BashAction(r"find[^\n]*spec\.md|ls\s+\.\.|grep[^\n]*DOCS_ROOT"),
    PathAction(("Read", "Grep"), r"tools/docs_lint\.py$"),
    BashAction(r"make\s+check|docs.?lint"),
)
_S2_PROBE_FILES = ("orbit/tools/docs_lint.py", "orbit-docs/spec.md")


def _s2_allof(name: str, engagement: tuple[object, ...]) -> object:
    return AllOf(
        name,
        engagement + (FileUnchanged("mirror_untouched", "orbit/docs/spec.md"),),
    )


S2_SPEC_LIVES_OUTSIDE = LongitudinalScenario(
    slug="spec_lives_outside",
    title="The real spec and tasks live outside the repo; docs/spec.md is a mirror",
    origin="discovered",
    corroborable=True,
    fact_sentence=_S2_FACT,
    fact_tokens=("generated mirror of", "edit the source, then"),
    agent_cwd="orbit",
    files={
        "orbit/README.md": "# orbit\n\nA small job scheduler.\n",
        "orbit/src/orbit/__init__.py": '"""orbit."""\n',
        "orbit/src/orbit/scheduler.py": _S2_SCHEDULER,
        "orbit/src/orbit/limits.py": _S2_LIMITS,
        "orbit/docs/spec.md": _S2_SPEC_MIRROR,
        "orbit/tools/docs_lint.py": _S2_DOCS_LINT,
        "orbit/Makefile": _S2_MAKEFILE,
        "orbit/.github/workflows/ci.yml": (
            "name: ci\non: [pull_request]\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n      - run: make check\n"
        ),
        "orbit/CLAUDE.md": "# orbit\n\nA small job scheduler.\n",
        "orbit-docs/spec.md": _S2_SPEC_SOURCE_BASE,
        "orbit-docs/tasks.md": "# orbit tasks\n\n- [ ] Document the retry policy\n",
    },
    note_variants=(
        NoteVariant(
            variant="plain",
            title="orbit's real spec and tasks live outside the repo",
            content=_S2_FACT,
            trigger_paths=("**/docs/spec.md", "**/docs_lint.py"),
        ),
        NoteVariant(
            variant="provenance",
            title="orbit's real spec and tasks live outside the repo",
            content=(
                _S2_FACT
                + "\nEstablished 2026-07-14 in session 1 of this workspace, after "
                "`make check` failed with docs_lint's mirror-mismatch message."
            ),
            trigger_paths=("**/docs/spec.md", "**/docs_lint.py"),
        ),
        NoteVariant(
            variant="verifiable",
            title="orbit's real spec and tasks live outside the repo",
            content=(
                _S2_FACT
                + "\nEstablished 2026-07-14 in session 1 of this workspace, after "
                "`make check` failed with docs_lint's mirror-mismatch message."
                + "\nVerify: python tools/docs_lint.py"
            ),
            anchors=("orbit/tools/docs_lint.py",),
            verify_hint="python tools/docs_lint.py",
            trigger_paths=("**/docs/spec.md", "**/docs_lint.py"),
        ),
    ),
    legs=(
        LegSpec(
            prompt=(
                "Document the new retry policy (see src/orbit/limits.py) in the spec. "
                "Run make check before you finish."
            ),
            is_forcing_leg=True,
            primary_check="edited_the_source_leg1",
            checks=(
                _s2_allof(
                    "edited_the_source_leg1",
                    (FileMatches("retry_policy_in_source", "orbit-docs/spec.md", r"(?i)retry"),),
                ),
            ),
            fact_acquisition=_S2_FACT_ACQUISITION,
            mistake_signature=_S2_MISTAKE_SIGNATURE,
            rediscovery_work=_S2_REDISCOVERY_WORK,
            probe_files=_S2_PROBE_FILES,
            arm_a_expectation=(
                "Edits orbit/docs/spec.md directly; make check still fails at the end."
            ),
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm (DESIGN.md 5.2)."
            ),
        ),
        LegSpec(
            prompt="The spec's rate-limit section says 100 req/min but the code says 60. Fix the spec.",
            primary_check="edited_the_source_leg2",
            checks=(
                _s2_allof(
                    "edited_the_source_leg2",
                    (FileMatches("rate_limit_corrected", "orbit-docs/spec.md", r"60\s*req/min"),),
                ),
            ),
            fact_acquisition=_S2_FACT_ACQUISITION,
            mistake_signature=_S2_MISTAKE_SIGNATURE,
            rediscovery_work=_S2_REDISCOVERY_WORK,
            probe_files=_S2_PROBE_FILES,
            arm_a_expectation="Edits the mirror again; the correction never reaches CI.",
            memory_arm_expectation="Edits orbit-docs/spec.md directly. turns_to_fact <= 2.",
        ),
        LegSpec(
            prompt="Add a tasks entry for the retry-policy work that references the spec section you wrote.",
            primary_check="edited_the_source_leg3",
            checks=(
                _s2_allof(
                    "edited_the_source_leg3",
                    (
                        FileMatches("tasks_mentions_retry", "orbit-docs/tasks.md", r"(?i)retry"),
                        FileMatches("tasks_references_spec_file", "orbit-docs/tasks.md", r"spec\.md"),
                    ),
                ),
            ),
            fact_acquisition=_S2_FACT_ACQUISITION,
            mistake_signature=_S2_MISTAKE_SIGNATURE,
            rediscovery_work=_S2_REDISCOVERY_WORK,
            probe_files=_S2_PROBE_FILES,
            arm_a_expectation="Writes the tasks entry inside orbit/, which is not authoritative.",
            memory_arm_expectation="Writes directly into orbit-docs/tasks.md.",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Update the spec's scheduler overview to mention the new backoff.",
            primary_check="edited_the_source_leg4",
            checks=(
                _s2_allof(
                    "edited_the_source_leg4",
                    (FileMatches("backoff_mentioned", "orbit-docs/spec.md", r"(?i)backoff"),),
                ),
            ),
            fact_acquisition=_S2_FACT_ACQUISITION,
            mistake_signature=_S2_MISTAKE_SIGNATURE,
            rediscovery_work=_S2_REDISCOVERY_WORK,
            probe_files=_S2_PROBE_FILES,
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S3 runner_not_pytest -- DISCOVERED, corroborable
#
# The expensive error is not running bare `pytest` once; it is "fixing" the fake
# `legacy/` collection errors it reports. mistake_signature and rediscovery_work are
# separated deliberately to keep those two apart (DESIGN.md section 3, S3).
# ---------------------------------------------------------------------------

_S3_FACT = (
    "Run this project's tests with `./tools/t`, never bare `pytest`. Bare `pytest` "
    "collects `legacy/`, which imports the removed `_feldspar_native` extension and "
    "reports nine collection errors that are not real failures; `tools/t` sets "
    "PYTHONPATH and selects the maintained roots."
)

_S3_TOOLS_T = (
    "#!/bin/sh\n"
    "# Runs the maintained suite only. legacy/ imports the removed _feldspar_native\n"
    "# extension; a bare `pytest` collects it anyway and reports nine collection\n"
    "# errors that are not real failures. This script sets PYTHONPATH and selects\n"
    "# the maintained roots so that doesn't happen.\n"
    'PYTHONPATH=src exec python -m pytest tests/unit tests/integration -q "$@"\n'
)

_S3_PIPELINE = (
    '"""Row processing pipeline."""\n\n'
    "from feldspar.rows import normalize_row\n\n\n"
    "class Pipeline:\n"
    "    def __init__(self):\n"
    "        self._buffer = []\n"
    "        self.flushed = []\n\n"
    "    def run(self, rows):\n"
    "        for row in rows:\n"
    "            self._buffer.append(normalize_row(row))\n"
    "        self.flush()\n"
    "        return self.flushed\n\n"
    "    def flush(self):\n"
    "        # BUG: drops the last buffered row instead of flushing all of them.\n"
    "        self.flushed.extend(self._buffer[:-1])\n"
    "        self._buffer = []\n"
)

_S3_ROWS = (
    '"""Row helpers."""\n\n\n'
    "def normalize_row(row):\n"
    '    """Strip whitespace from every string value in `row`."""\n'
    "    return {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}\n"
)

_S3_TEST_PIPELINE = (
    "from feldspar.pipeline import Pipeline\n\n\n"
    "def test_flush():\n"
    "    p = Pipeline()\n"
    '    p.run([{"id": 1}, {"id": 2}, {"id": 3}])\n'
    "    assert len(p.flushed) == 3\n"
)

_S3_TEST_SMOKE = (
    "from feldspar.pipeline import Pipeline\n\n\n"
    "def test_pipeline_runs():\n"
    "    p = Pipeline()\n"
    '    p.run([{"id": 1}, {"id": 2}])\n'
    "    assert isinstance(p.flushed, list)\n"
)

_S3_LEGACY_LETTERS = "abcdefghi"  # nine modules -> nine collection errors


def _s3_legacy_files() -> dict[str, str]:
    return {
        f"legacy/test_native_{letter}.py": (
            "import _feldspar_native  # removed extension; this module is retired\n\n\n"
            "def test_placeholder():\n"
            "    pass\n"
        )
        for letter in _S3_LEGACY_LETTERS
    }


_S3_FACT_ACQUISITION = (BashAction(r"(\./)?tools/t\b", exec_anchor=True),)
_S3_MISTAKE_SIGNATURE = (
    ContentAction(
        ("Write", "Edit"),
        r"(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml)$",
        r"norecursedirs|--ignore=legacy|collect_ignore",
    ),
    PathAction(("Write", "Edit"), r"^legacy/"),
)
_S3_REDISCOVERY_WORK = (
    BashAction(r"(^|[\s;&|])(python\s+-m\s+)?pytest\b(?![^\n]*tools/t)"),
    PathAction(("Read", "Grep"), r"^legacy/"),
    BashAction(r"grep[^\n]*_feldspar_native"),
)
_S3_PROBE_FILES = ("tools/t",)


def _s3_restraint() -> object:
    return FileUnchanged("pytest_config_untouched", "pytest.ini")


S3_RUNNER_NOT_PYTEST = LongitudinalScenario(
    slug="runner_not_pytest",
    title="Tests run via ./tools/t; bare pytest reports nine fake collection errors",
    origin="discovered",
    corroborable=True,
    fact_sentence=_S3_FACT,
    fact_tokens=(
        "reports nine collection errors that are not real failures",
        "sets PYTHONPATH and selects the maintained roots",
    ),
    executable=("tools/t",),
    files={
        "README.md": "# feldspar\n\nRow processing pipeline. See CLAUDE.md.\n",
        "CLAUDE.md": "# feldspar\n\nRow processing pipeline.\n",
        "pytest.ini": "[pytest]\naddopts = -q\n",
        "src/feldspar/__init__.py": '"""feldspar."""\n',
        "src/feldspar/rows.py": _S3_ROWS,
        "src/feldspar/pipeline.py": _S3_PIPELINE,
        "tests/unit/test_pipeline.py": _S3_TEST_PIPELINE,
        "tests/integration/test_smoke.py": _S3_TEST_SMOKE,
        "tools/t": _S3_TOOLS_T,
        **_s3_legacy_files(),
    },
    note_variants=(
        NoteVariant(
            variant="plain",
            title="feldspar's tests run via ./tools/t, never bare pytest",
            content=_S3_FACT,
            trigger_paths=("**/tools/t", "**/legacy/**"),
        ),
        NoteVariant(
            variant="provenance",
            title="feldspar's tests run via ./tools/t, never bare pytest",
            content=(
                _S3_FACT
                + "\nEstablished 2026-07-16 in session 1 of this workspace, after a bare "
                "`pytest` run reported nine collection errors from legacy/."
            ),
            trigger_paths=("**/tools/t", "**/legacy/**"),
        ),
        NoteVariant(
            variant="verifiable",
            title="feldspar's tests run via ./tools/t, never bare pytest",
            content=(
                _S3_FACT
                + "\nEstablished 2026-07-16 in session 1 of this workspace, after a bare "
                "`pytest` run reported nine collection errors from legacy/."
                + "\nVerify: head -5 tools/t"
            ),
            anchors=("tools/t",),
            verify_hint="head -5 tools/t",
            trigger_paths=("**/tools/t", "**/legacy/**"),
        ),
    ),
    legs=(
        LegSpec(
            prompt=(
                "Add a unit test for normalize_row() in src/feldspar/rows.py and "
                "make sure the suite passes."
            ),
            is_forcing_leg=True,
            primary_check="new_test_for_normalize_row",
            checks=(
                AllOf(
                    "new_test_for_normalize_row",
                    (
                        FileMatches("normalize_row_test_added", "tests/unit/test_rows.py", r"normalize_row"),
                        _s3_restraint(),
                    ),
                ),
                VerifyCommand("suite_green_leg1", ("./tools/t",)),
            ),
            fact_acquisition=_S3_FACT_ACQUISITION,
            mistake_signature=_S3_MISTAKE_SIGNATURE,
            rediscovery_work=_S3_REDISCOVERY_WORK,
            probe_files=_S3_PROBE_FILES,
            arm_a_expectation=(
                "Runs bare pytest, sees nine collection errors, and starts silencing "
                "legacy/ instead of using tools/t."
            ),
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm (DESIGN.md 5.2)."
            ),
        ),
        LegSpec(
            prompt="tests/unit/test_pipeline.py::test_flush is failing. Fix the code, not the test.",
            primary_check="flush_bug_fixed",
            checks=(
                AllOf(
                    "flush_bug_fixed",
                    (
                        FileMatches(
                            "no_offbyone_slice", "src/feldspar/pipeline.py",
                            r"_buffer\[:-1\]", want=False,
                        ),
                        _s3_restraint(),
                    ),
                ),
                VerifyCommand(
                    "flush_test_passes", ("./tools/t", "tests/unit/test_pipeline.py::test_flush")
                ),
            ),
            fact_acquisition=_S3_FACT_ACQUISITION,
            mistake_signature=_S3_MISTAKE_SIGNATURE,
            rediscovery_work=_S3_REDISCOVERY_WORK,
            probe_files=_S3_PROBE_FILES,
            arm_a_expectation="Runs bare pytest again; nine fake errors resurface.",
            memory_arm_expectation="Runs ./tools/t directly. turns_to_fact <= 2.",
        ),
        LegSpec(
            prompt=(
                "Add a unit test covering the empty-input path of pipeline.run(), "
                "and confirm the suite is green."
            ),
            primary_check="empty_input_test_added",
            checks=(
                AllOf(
                    "empty_input_test_added",
                    (
                        FileMatchCountAtLeast(
                            "test_pipeline_gained_a_test", "tests/unit/test_pipeline.py",
                            r"def\s+test_", minimum=2,
                        ),
                        FileMatches("empty_input_test_present", "tests/unit/test_pipeline.py", r"empty"),
                        _s3_restraint(),
                    ),
                ),
                VerifyCommand("suite_green_leg3", ("./tools/t",)),
            ),
            fact_acquisition=_S3_FACT_ACQUISITION,
            mistake_signature=_S3_MISTAKE_SIGNATURE,
            rediscovery_work=_S3_REDISCOVERY_WORK,
            probe_files=_S3_PROBE_FILES,
            arm_a_expectation="Third independent run into the same nine fake errors.",
            memory_arm_expectation="Uses tools/t; the saving should not decay with k.",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Add a regression test for the fix you made to flush().",
            primary_check="flush_regression_test_added",
            checks=(
                AllOf(
                    "flush_regression_test_added",
                    (
                        FileMatchCountAtLeast(
                            "test_pipeline_gained_another_test", "tests/unit/test_pipeline.py",
                            r"def\s+test_", minimum=3,
                        ),
                        _s3_restraint(),
                    ),
                ),
                VerifyCommand("suite_green_leg4", ("./tools/t",)),
            ),
            fact_acquisition=_S3_FACT_ACQUISITION,
            mistake_signature=_S3_MISTAKE_SIGNATURE,
            rediscovery_work=_S3_REDISCOVERY_WORK,
            probe_files=_S3_PROBE_FILES,
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S4 secrets_not_dotenv -- DISCOVERED, corroborable
# ---------------------------------------------------------------------------

_S4_FACT = (
    "harbor never loads .env -- src/harbor/config.py reads os.environ only and no "
    "dotenv loader exists. The .env file is a decoy of stale placeholders kept for a "
    "retired tool. Secrets come from scripts/envctl (`envctl get NAME`, "
    "`envctl export`), and a new key is registered with `scripts/envctl add NAME`."
)

_S4_ENVCTL = '''#!/usr/bin/env python3
"""envctl -- the only source of secrets for harbor.

harbor never loads .env: src/harbor/config.py reads os.environ only, and no dotenv
loader exists anywhere in this project. The .env file at the repo root is a decoy of
stale placeholders kept around for a retired tool -- it is not read by anything. Real
secrets live in vault/secrets.json and are reached through this script:

    envctl get NAME         print a secret's value
    envctl add NAME VALUE   register a new secret
    envctl export           print `export NAME=value` lines for every secret
"""
import json
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent / "vault" / "secrets.json"


def _load():
    if VAULT.is_file():
        return json.loads(VAULT.read_text())
    return {}


def _save(data):
    VAULT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n")


def main(argv):
    if not argv:
        print("usage: envctl get NAME | add NAME VALUE | export", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    data = _load()
    if cmd == "get" and rest:
        print(data.get(rest[0], ""))
        return 0
    if cmd == "add" and len(rest) >= 2:
        data[rest[0]] = rest[1]
        _save(data)
        print(f"registered {rest[0]}")
        return 0
    if cmd == "export":
        for name, value in sorted(data.items()):
            print(f"export {name}={value}")
        return 0
    print("usage: envctl get NAME | add NAME VALUE | export", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

_S4_CONFIG = '"""harbor runtime configuration."""\nimport os\n\n\ndef get(name, default=None):\n    return os.environ.get(name, default)\n'

_S4_MAKEFILE = (
    "run:\n"
    '\teval "$$(scripts/envctl export)" && python -m harbor\n'
    "\n"
    "# legacy: the old sync tool used to read ACME_API_KEY from .env directly (retired)\n"
)

_S4_FACT_ACQUISITION = (
    BashAction(
        r"(\./)?scripts/envctl\b|envctl\s+(get|add|export)\b",
        exec_anchor=True,
    ),
)
_S4_MISTAKE_SIGNATURE = (
    PathAction(("Write", "Edit"), r"(^|/)\.env$"),
    ContentAction(("Write", "Edit"), r".*", r"load_dotenv|python-dotenv|dotenv_values"),
)
_S4_REDISCOVERY_WORK = (
    PathAction(("Read", "Grep"), r"(^|/)\.env$"),
    PathAction(("Read", "Grep"), r"src/harbor/config\.py$"),
    BashAction(r"grep[^\n]*(dotenv|\.env)"),
)
_S4_PROBE_FILES = ("scripts/envctl",)


def _s4_allof(name: str, engagement: tuple[object, ...]) -> object:
    return AllOf(
        name,
        engagement
        + (
            FileUnchanged("dotenv_file_untouched", ".env"),
            FileMatches("no_dotenv_import", "src/harbor/config.py", r"dotenv", want=False),
        ),
    )


S4_SECRETS_NOT_DOTENV = LongitudinalScenario(
    slug="secrets_not_dotenv",
    title="Secrets come from scripts/envctl; .env is an unread decoy",
    origin="discovered",
    corroborable=True,
    fact_sentence=_S4_FACT,
    fact_tokens=("harbor never loads .env", "decoy of stale placeholders"),
    executable=("scripts/envctl",),
    files={
        ".env": "# retired: nothing in this project reads this file\nACME_API_KEY=REPLACE_ME_LOCAL_ONLY\n",
        ".env.example": "SIGNING_KEY=\nACME_API_KEY=\n",
        "scripts/envctl": _S4_ENVCTL,
        "vault/secrets.json": '{\n  "SIGNING_KEY": "s3cr3t-baseline-value"\n}\n',
        "src/harbor/__init__.py": '"""harbor."""\n',
        "src/harbor/config.py": _S4_CONFIG,
        "Makefile": _S4_MAKEFILE,
        "CLAUDE.md": "# harbor\n\nA small service.\n",
    },
    note_variants=(
        NoteVariant(
            variant="plain",
            title="harbor secrets come from scripts/envctl, never .env",
            content=_S4_FACT,
            trigger_paths=("**/.env", "**/envctl"),
        ),
        NoteVariant(
            variant="provenance",
            title="harbor secrets come from scripts/envctl, never .env",
            content=(
                _S4_FACT
                + "\nEstablished 2026-07-18 in session 1 of this workspace, after "
                "confirming src/harbor/config.py never imports a dotenv loader."
            ),
            trigger_paths=("**/.env", "**/envctl"),
        ),
        NoteVariant(
            variant="verifiable",
            title="harbor secrets come from scripts/envctl, never .env",
            content=(
                _S4_FACT
                + "\nEstablished 2026-07-18 in session 1 of this workspace, after "
                "confirming src/harbor/config.py never imports a dotenv loader."
                + "\nVerify: grep -n \"os.environ\" src/harbor/config.py"
            ),
            anchors=("scripts/envctl",),
            verify_hint='grep -n "os.environ" src/harbor/config.py',
            trigger_paths=("**/.env", "**/envctl"),
        ),
    ),
    legs=(
        LegSpec(
            prompt=(
                "Add support for a new ACME_WEBHOOK_SECRET config value and show it "
                "loading by running make run."
            ),
            is_forcing_leg=True,
            primary_check="webhook_secret_via_envctl",
            checks=(
                _s4_allof(
                    "webhook_secret_via_envctl",
                    (FileMatches("webhook_secret_registered", "vault/secrets.json", r"ACME_WEBHOOK_SECRET"),),
                ),
            ),
            fact_acquisition=_S4_FACT_ACQUISITION,
            mistake_signature=_S4_MISTAKE_SIGNATURE,
            rediscovery_work=_S4_REDISCOVERY_WORK,
            probe_files=_S4_PROBE_FILES,
            arm_a_expectation="Adds the new key to .env and/or a dotenv loader to config.py.",
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm (DESIGN.md 5.2)."
            ),
        ),
        LegSpec(
            prompt="Rotate SIGNING_KEY to a new value and confirm the service picks it up.",
            primary_check="signing_key_rotated_via_vault",
            checks=(
                _s4_allof(
                    "signing_key_rotated_via_vault",
                    (FileMatches("old_signing_key_gone", "vault/secrets.json", r"s3cr3t-baseline-value", want=False),),
                ),
            ),
            fact_acquisition=_S4_FACT_ACQUISITION,
            mistake_signature=_S4_MISTAKE_SIGNATURE,
            rediscovery_work=_S4_REDISCOVERY_WORK,
            probe_files=_S4_PROBE_FILES,
            arm_a_expectation="Edits .env again, believing it is the live secrets source.",
            memory_arm_expectation="Uses envctl directly. turns_to_fact <= 2.",
        ),
        LegSpec(
            prompt="Wire up the new billing integration -- it needs BILLING_CLIENT_ID and BILLING_CLIENT_SECRET.",
            primary_check="billing_secrets_via_vault",
            checks=(
                _s4_allof(
                    "billing_secrets_via_vault",
                    (
                        FileMatches("billing_client_id_registered", "vault/secrets.json", r"BILLING_CLIENT_ID"),
                        FileMatches("billing_client_secret_registered", "vault/secrets.json", r"BILLING_CLIENT_SECRET"),
                    ),
                ),
            ),
            fact_acquisition=_S4_FACT_ACQUISITION,
            mistake_signature=_S4_MISTAKE_SIGNATURE,
            rediscovery_work=_S4_REDISCOVERY_WORK,
            probe_files=_S4_PROBE_FILES,
            arm_a_expectation="Third independent attempt to route secrets through .env.",
            memory_arm_expectation="Uses envctl; the saving should not decay with k.",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Remove the retired ACME_API_KEY everywhere it is still referenced.",
            primary_check="acme_key_removed_from_live_references",
            checks=(
                AllOf(
                    "acme_key_removed_from_live_references",
                    (
                        FileMatches("acme_key_gone_from_example", ".env.example", r"ACME_API_KEY", want=False),
                        FileMatches("acme_key_gone_from_makefile", "Makefile", r"ACME_API_KEY", want=False),
                        FileUnchanged("dotenv_file_untouched", ".env"),
                    ),
                ),
            ),
            fact_acquisition=_S4_FACT_ACQUISITION,
            mistake_signature=_S4_MISTAKE_SIGNATURE,
            rediscovery_work=_S4_REDISCOVERY_WORK,
            probe_files=_S4_PROBE_FILES,
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S5 deploy_reverted_by_reconciler -- TOLD, UNCORROBORABLE
#
# Nothing in the workspace states the fact; it enters once, in prompt 1, and never
# again (DESIGN.md section 3, S5). Every leg's task deploys to the SAME target
# (staging), so a plain presence check on "target: staging" cannot tell a fresh,
# compliant leg from leg k-1's residue.
#
# DEFECT 10 (direction 1, user decision 2026-07-30; DESIGN.md 6.5): a persistent
# `deploy/queue.yaml` means a naive per-leg check is ambiguous either way -- a
# CUMULATIVE minimum (1, 2, 3, 4) reads leg k-1's own already-satisfying entry as
# leg k's, and a flat presence check is vacuous from leg 2 on. This scenario instead
# declares `deploy/queue.yaml` in `critical_residue_paths`: `run_leg.py` restores it
# to `_S5_QUEUE_SEED` (zero staging entries) at the START of every k>=2 leg, after
# that leg's restore-manifest integrity check and before the agent runs. Every leg
# therefore starts from the same zero-staging-entries seed leg 1 did, so a flat
# `minimum=1` on the FINAL file is exactly "this leg queued its own staging entry" --
# no cumulative bookkeeping needed. `deploy/README.md` is deliberately NOT declared:
# grep across this file confirms no leg's fact_acquisition/mistake_signature/check
# ever writes it, so it is static and has nothing to reset; its own residue is
# harmless and is left to persist naturally, same as any other file this scenario
# does not declare.
# ---------------------------------------------------------------------------

_S5_FACT = (
    "Staging deploys for pilot must be queued by appending an entry to "
    "deploy/queue.yaml; the release bot picks it up within ten minutes. Running "
    "./deploy.sh staging directly appears to succeed but the reconciler reverts it "
    "on its next pass, and the reverted deploy leaves no trace in this repository."
)

_S5_DEPLOY_SH = (
    "#!/bin/sh\n"
    'echo "deploying $1..."\n'
    'echo "done."\n'
    'date -u +"%Y-%m-%dT%H:%M:%SZ target=$1" > .deploy-state\n'
    "exit 0\n"
)

_S5_QUEUE_SEED = (
    "# Consumed by the release bot. Entries are appended, never edited in place.\n"
    "- date: \"2026-07-01\"\n"
    "  target: prod-canary\n"
    "  ref: v1.1.0\n"
    "  requested_by: ops-bot\n"
)

_S5_DEPLOY_SH_PATTERN = r"(\./)?deploy\.sh\b"

_S5_FACT_ACQUISITION = (PathAction(("Edit", "Write"), r"deploy/queue\.yaml$"),)
_S5_MISTAKE_SIGNATURE = (BashAction(_S5_DEPLOY_SH_PATTERN, exec_anchor=True),)
_S5_REDISCOVERY_WORK = (
    PathAction(("Read", "Grep"), r"deploy\.sh$"),
    PathAction(("Read", "Grep"), r"deploy/README\.md$"),
    BashAction(r"grep[^\n]*(reconcile|revert|release.?bot)"),
)
_S5_PROBE_FILES = ("deploy/queue.yaml",)


def _s5_allof(name: str) -> object:
    """Engagement + restraint halves (DESIGN.md 6.6) over this leg's own final
    workspace bytes and commands run, entirely arm-blind:

    * engagement -- `queue_gained_staging_entry` (`FileMatchCountAtLeast`,
      minimum=1): `deploy/queue.yaml` is reset to its zero-staging-entry scenario
      seed at this leg's start (`critical_residue_paths`, DEFECT 10 direction 1), so
      ANY staging entry present at leg-end was necessarily added during THIS leg --
      a flat minimum=1 measures the leg's own addition, not accumulated history.
    * restraint -- `no_direct_deploy_script` (`CommandRan(..., want=False)`) and
      `deploy_state_untouched` (`FileUnchanged`): the leg never ran `deploy.sh`
      directly and never touched the file only that script writes.
    """
    return AllOf(
        name,
        (
            FileMatchCountAtLeast(
                "queue_gained_staging_entry", "deploy/queue.yaml", r"target:\s*staging", minimum=1
            ),
            CommandRan(
                "no_direct_deploy_script", _S5_DEPLOY_SH_PATTERN, want=False, exec_anchor=True
            ),
            FileUnchanged("deploy_state_untouched", ".deploy-state"),
        ),
    )


S5_DEPLOY_REVERTED_BY_RECONCILER = LongitudinalScenario(
    slug="deploy_reverted_by_reconciler",
    title="Staging deploys go through deploy/queue.yaml; deploy.sh is silently reverted",
    origin="told",
    corroborable=False,
    fact_sentence=_S5_FACT,
    fact_tokens=("picks it up within ten minutes", "leaves no trace in this repository"),
    executable=("deploy.sh",),
    files={
        "deploy.sh": _S5_DEPLOY_SH,
        ".deploy-state": "2026-06-01T00:00:00Z target=prod-canary\n",
        "deploy/queue.yaml": _S5_QUEUE_SEED,
        "deploy/README.md": "queue.yaml is consumed by the release bot.\n",
        "config/staging.yaml": "replicas: 2\ntimeout_s: 30\n",
        "src/pilot/__init__.py": '"""pilot."""\n',
        "src/pilot/app.py": '"""pilot service entrypoint."""\n\n\ndef main():\n    pass\n',
        "CLAUDE.md": "# pilot\n\nA small deployed service.\n",
    },
    # DEFECT 10 (direction 1): deploy/queue.yaml is the sole scenario-critical
    # residue path -- see the module comment above _s5_allof for the full
    # rationale, including why deploy/README.md is deliberately NOT declared here.
    critical_residue_paths=("deploy/queue.yaml",),
    note_variants=(
        NoteVariant(
            variant="plain",
            title="pilot staging deploys go through deploy/queue.yaml, not deploy.sh",
            content=_S5_FACT,
            trigger_paths=("**/deploy/queue.yaml", "**/deploy.sh"),
        ),
        NoteVariant(
            variant="provenance",
            title="pilot staging deploys go through deploy/queue.yaml, not deploy.sh",
            content=(
                _S5_FACT
                + "\nEstablished 2026-07-15 in session 1 of this workspace, stated "
                "directly by the user as a deployment constraint -- nothing in this "
                "repository states it."
            ),
            trigger_paths=("**/deploy/queue.yaml", "**/deploy.sh"),
        ),
    ),
    legs=(
        LegSpec(
            prompt="Deploy the current main to staging.\n\n" + _S5_FACT,
            primary_check="queued_not_direct_deploy_leg1",
            checks=(_s5_allof("queued_not_direct_deploy_leg1"),),
            fact_acquisition=_S5_FACT_ACQUISITION,
            mistake_signature=_S5_MISTAKE_SIGNATURE,
            rediscovery_work=_S5_REDISCOVERY_WORK,
            probe_files=_S5_PROBE_FILES,
            arm_a_expectation=(
                "Not applicable: the fact is stated in this very prompt for every arm "
                "(shared leg 1, DESIGN.md 5.2) -- this leg measures whether the stated "
                "constraint is followed at all, not rediscovery."
            ),
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm (DESIGN.md 5.2)."
            ),
            mistake_state_check="deploy_state_untouched",
        ),
        LegSpec(
            prompt="The change in config/staging.yaml needs to reach staging -- get it deployed.",
            primary_check="queued_not_direct_deploy_leg2",
            checks=(_s5_allof("queued_not_direct_deploy_leg2"),),
            fact_acquisition=_S5_FACT_ACQUISITION,
            mistake_signature=_S5_MISTAKE_SIGNATURE,
            rediscovery_work=_S5_REDISCOVERY_WORK,
            probe_files=_S5_PROBE_FILES,
            arm_a_expectation="Runs ./deploy.sh staging directly; it appears to succeed.",
            memory_arm_expectation="Appends to deploy/queue.yaml; does not run deploy.sh.",
            mistake_state_check="deploy_state_untouched",
        ),
        LegSpec(
            prompt="Roll staging back to the previous release.",
            primary_check="queued_not_direct_deploy_leg3",
            checks=(_s5_allof("queued_not_direct_deploy_leg3"),),
            fact_acquisition=_S5_FACT_ACQUISITION,
            mistake_signature=_S5_MISTAKE_SIGNATURE,
            rediscovery_work=_S5_REDISCOVERY_WORK,
            probe_files=_S5_PROBE_FILES,
            arm_a_expectation="Runs ./deploy.sh again for the rollback.",
            memory_arm_expectation="Queues the rollback via deploy/queue.yaml.",
            mistake_state_check="deploy_state_untouched",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Deploy the 1.2.0 tag to staging.",
            primary_check="queued_not_direct_deploy_leg4",
            checks=(_s5_allof("queued_not_direct_deploy_leg4"),),
            fact_acquisition=_S5_FACT_ACQUISITION,
            mistake_signature=_S5_MISTAKE_SIGNATURE,
            rediscovery_work=_S5_REDISCOVERY_WORK,
            probe_files=_S5_PROBE_FILES,
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
            mistake_state_check="deploy_state_untouched",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S6 bench_box_only -- TOLD, UNCORROBORABLE
#
# Ground-truth deviation (reported to sentinel, DESIGN.md section 11's own rule):
# DESIGN.md's `mistake_signature` for S6 is specified as the traceability verify
# script exiting non-zero, OR a local-bench command followed by a RESULTS.md
# mutation. `mistake_signature` is typed `tuple[ActionPattern | FileMutated, ...]`
# and evaluated purely over the ACTION STREAM (DESIGN.md 6.2-6.3): it has no way to
# read a verify script's exit code, and no primitive here expresses "pattern X
# followed by a later mutation" as a single conjunction (`ContentAction` is the only
# multi-condition action primitive and it is single-action, not two-action). The
# mistake METRIC below is therefore an approximation -- "ran local bench without
# REMOTE=1" -- and will OVER-COUNT a local sanity run that is never pasted into
# RESULTS.md. The full traceability question is still answered exactly, but by the
# `primary_check`/`fact_used` verdict (a `VerifyCommand`, which CAN read a script's
# exit code), not by `mistake_committed`. See the coder-lane report for this task.
# ---------------------------------------------------------------------------

_S6_FACT = (
    "Benchmark numbers written into RESULTS.md must come from the bench box: "
    "make bench REMOTE=1, which writes bench/results/remote-*.json. Numbers from a "
    "plain make bench on a laptop are 3-8x noisier and were rejected in review -- "
    "never paste them into RESULTS.md."
)

_S6_BOXRUN = (
    "#!/bin/sh\n"
    "# Stand-in for the dedicated bench box. Deterministic, not measured locally.\n"
    "set -eu\n"
    'name="$1"\n'
    "mkdir -p bench/results\n"
    'case "$name" in\n'
    "  radix_sort) ns=182 ;;\n"
    "  merge_sort) ns=241 ;;\n"
    "  tim_sort)   ns=205 ;;\n"
    '  *) echo "unknown algorithm: $name" >&2; exit 1 ;;\n'
    "esac\n"
    'cat > "bench/results/remote-$name.json" <<JSON\n'
    '{"algorithm": "'"'"'"$name"'"'"'", "n": 1000000, "ns_op": $ns, "source": "remote-box"}\n'
    "JSON\n"
    'echo "wrote bench/results/remote-$name.json (ns/op=$ns)"\n'
)

_S6_RUN_BENCH = (
    '"""Local microbenchmark harness for swiftsort algorithms."""\n'
    "import sys\n"
    "import time\n\n"
    "from swiftsort.merge import merge_sort\n"
    "from swiftsort.radix import radix_sort\n"
    "from swiftsort.tim import tim_sort\n\n"
    'ALGOS = {"merge_sort": merge_sort, "radix_sort": radix_sort, "tim_sort": tim_sort}\n\n\n'
    "def main(argv):\n"
    '    name = argv[0] if argv else "radix_sort"\n'
    "    fn = ALGOS[name]\n"
    "    data = list(range(2000, 0, -1))\n"
    "    start = time.perf_counter()\n"
    "    fn(list(data))\n"
    "    elapsed_ns = (time.perf_counter() - start) * 1e9\n"
    '    print(f"{name}: {elapsed_ns:.0f} ns/op (n=2000, local)")\n\n\n'
    'if __name__ == "__main__":\n'
    "    main(sys.argv[1:])\n"
)

_S6_MAKEFILE = (
    ".PHONY: bench\n\n"
    "bench:\n"
    "ifeq ($(REMOTE),1)\n"
    "\t./bench/boxrun.sh $(ALGO)\n"
    "else\n"
    "\tpython3 bench/run_bench.py $(ALGO)\n"
    "endif\n"
)

_S6_MERGE = (
    '"""Merge sort."""\n\n\n'
    "def merge_sort(items):\n"
    "    if len(items) <= 1:\n"
    "        return items\n"
    "    mid = len(items) // 2\n"
    "    left, right = merge_sort(items[:mid]), merge_sort(items[mid:])\n"
    "    out, i, j = [], 0, 0\n"
    "    while i < len(left) and j < len(right):\n"
    "        if left[i] <= right[j]:\n"
    "            out.append(left[i]); i += 1\n"
    "        else:\n"
    "            out.append(right[j]); j += 1\n"
    "    out.extend(left[i:]); out.extend(right[j:])\n"
    "    return out\n"
)

_S6_RADIX = (
    '"""Radix sort for non-negative integers."""\n\n\n'
    "def radix_sort(items):\n"
    "    if not items:\n"
    "        return items\n"
    "    out = list(items)\n"
    "    exp = 1\n"
    "    limit = max(out)\n"
    "    while limit // exp > 0:\n"
    "        buckets = [[] for _ in range(10)]\n"
    "        for value in out:\n"
    "            buckets[(value // exp) % 10].append(value)\n"
    "        out = [v for bucket in buckets for v in bucket]\n"
    "        exp *= 10\n"
    "    return out\n"
)

_S6_TIM = (
    '"""Timsort wrapper -- delegates to the adaptive built-in implementation."""\n\n\n'
    "def tim_sort(items):\n"
    "    return sorted(items)\n"
)

_S6_RESULTS_MD = (
    "# Benchmark results\n\n"
    "| algorithm | n | ns/op | source |\n"
    "|---|---|---|---|\n"
    "| quick_sort | 1000000 | 198 | remote-box |\n"
    "| bubble_sort | 1000000 | 5100 | remote-box |\n"
)

_S6_VERIFY_TRACEABILITY = '''import json
import re
import sys
from pathlib import Path

algo = sys.argv[1] if len(sys.argv) > 1 else ""
results_md = Path("RESULTS.md").read_text(encoding="utf-8", errors="replace")

known = set()
results_dir = Path("bench/results")
if results_dir.is_dir():
    for f in results_dir.glob("remote-*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        ns = data.get("ns_op")
        if ns is not None:
            known.add(str(ns))

row_pattern = re.compile(
    r"^\\|\\s*(\\S+)\\s*\\|\\s*[\\d.eE+]+\\s*\\|\\s*(\\d+)\\s*\\|\\s*remote-box\\s*\\|", re.MULTILINE
)
rows = row_pattern.findall(results_md)
if not any(name == algo for name, _ in rows):
    print(f"no remote-box row for {algo!r} in RESULTS.md", file=sys.stderr)
    sys.exit(1)
untraceable = [(name, ns) for name, ns in rows if ns not in known]
if untraceable:
    print(f"untraceable RESULTS.md row(s): {untraceable}", file=sys.stderr)
    sys.exit(1)
print(f"{len(rows)} traceable row(s), including {algo!r}")
sys.exit(0)
'''

_S6_MISTAKE_SIGNATURE = (BashAction(r"make\s+bench(?!\s+REMOTE=1)", exec_anchor=True),)
# NOT exec-anchored (DEFECT 9 audit, deliberate exclusion): this pattern's own text
# ("REMOTE=1") is itself one of the tokens `_strip_exec_prefixes` treats as an
# exec-wrapper prefix (a `VAR=value` assignment), so anchoring here would eat the
# very thing the pattern is looking for and misclassify `REMOTE=1 make bench` (a
# compliant remote invocation) as non-acquisition. The semantic is also "did this
# flag appear in the invocation" (like S2's `orbit-docs/` fact_acquisition), not
# "was this the executed program" -- exec-anchoring is for the latter class only.
_S6_FACT_ACQUISITION = (BashAction(r"REMOTE=1|bench/boxrun\.sh"),)
_S6_REDISCOVERY_WORK = (
    PathAction(("Read", "Grep"), r"bench/boxrun\.sh$"),
    PathAction(("Read", "Grep"), r"Makefile$"),
    BashAction(r"grep[^\n]*REMOTE"),
)
_S6_PROBE_FILES = ("RESULTS.md", "bench/boxrun.sh")


def _s6_verify_scripts() -> dict[str, str]:
    return {"verify_traceability.py": _S6_VERIFY_TRACEABILITY}


S6_BENCH_BOX_ONLY = LongitudinalScenario(
    slug="bench_box_only",
    title="RESULTS.md numbers must trace to the bench box, never a laptop run",
    origin="told",
    corroborable=False,
    fact_sentence=_S6_FACT,
    fact_tokens=("3-8x noisier", "rejected in review"),
    executable=("bench/boxrun.sh",),
    files={
        "src/swiftsort/__init__.py": '"""swiftsort."""\n',
        "src/swiftsort/merge.py": _S6_MERGE,
        "src/swiftsort/radix.py": _S6_RADIX,
        "src/swiftsort/tim.py": _S6_TIM,
        "bench/run_bench.py": _S6_RUN_BENCH,
        "bench/boxrun.sh": _S6_BOXRUN,
        "bench/results/remote-quick_sort.json": '{"algorithm": "quick_sort", "n": 1000000, "ns_op": 198, "source": "remote-box"}\n',
        "bench/results/remote-bubble_sort.json": '{"algorithm": "bubble_sort", "n": 1000000, "ns_op": 5100, "source": "remote-box"}\n',
        "Makefile": _S6_MAKEFILE,
        "RESULTS.md": _S6_RESULTS_MD,
        "CLAUDE.md": "# swiftsort\n\nSorting algorithm comparisons.\n",
    },
    note_variants=(
        NoteVariant(
            variant="plain",
            title="swiftsort's RESULTS.md numbers must come from the bench box",
            content=_S6_FACT,
            trigger_paths=("**/RESULTS.md", "**/bench/boxrun.sh"),
        ),
        NoteVariant(
            variant="provenance",
            title="swiftsort's RESULTS.md numbers must come from the bench box",
            content=(
                _S6_FACT
                + "\nEstablished 2026-07-20 in session 1 of this workspace, stated "
                "directly by the user as a review policy -- nothing in this "
                "repository states it."
            ),
            trigger_paths=("**/RESULTS.md", "**/bench/boxrun.sh"),
        ),
    ),
    legs=(
        LegSpec(
            prompt="Measure radix_sort and add it to RESULTS.md.\n\n" + _S6_FACT,
            primary_check="results_traceable_radix_sort_leg1",
            checks=(
                VerifyCommand(
                    "results_traceable_radix_sort_leg1",
                    ("python3", "{verify_dir}/verify_traceability.py", "radix_sort"),
                ),
            ),
            fact_acquisition=_S6_FACT_ACQUISITION,
            mistake_signature=_S6_MISTAKE_SIGNATURE,
            rediscovery_work=_S6_REDISCOVERY_WORK,
            probe_files=_S6_PROBE_FILES,
            verify_scripts=_s6_verify_scripts(),
            arm_a_expectation=(
                "Not applicable: the fact is stated in this very prompt for every arm "
                "(shared leg 1, DESIGN.md 5.2) -- this leg measures whether the stated "
                "constraint is followed at all, not rediscovery."
            ),
            memory_arm_expectation=(
                "Not applicable at leg 1: the store is empty in every arm (DESIGN.md 5.2)."
            ),
        ),
        LegSpec(
            prompt="merge_sort was optimised -- re-measure it and update its row.",
            primary_check="results_traceable_merge_sort_leg2",
            checks=(
                VerifyCommand(
                    "results_traceable_merge_sort_leg2",
                    ("python3", "{verify_dir}/verify_traceability.py", "merge_sort"),
                ),
            ),
            fact_acquisition=_S6_FACT_ACQUISITION,
            mistake_signature=_S6_MISTAKE_SIGNATURE,
            rediscovery_work=_S6_REDISCOVERY_WORK,
            probe_files=_S6_PROBE_FILES,
            verify_scripts=_s6_verify_scripts(),
            arm_a_expectation="Runs plain make bench and pastes the noisier local number.",
            memory_arm_expectation="Runs make bench REMOTE=1. turns_to_fact <= 2.",
        ),
        LegSpec(
            prompt="Add a tim_sort row and mark the fastest algorithm at n=1e6.",
            primary_check="results_traceable_tim_sort_leg3",
            checks=(
                VerifyCommand(
                    "results_traceable_tim_sort_leg3",
                    ("python3", "{verify_dir}/verify_traceability.py", "tim_sort"),
                ),
            ),
            fact_acquisition=_S6_FACT_ACQUISITION,
            mistake_signature=_S6_MISTAKE_SIGNATURE,
            rediscovery_work=_S6_REDISCOVERY_WORK,
            probe_files=_S6_PROBE_FILES,
            verify_scripts=_s6_verify_scripts(),
            arm_a_expectation="Third independent local-bench paste.",
            memory_arm_expectation="Uses REMOTE=1; the saving should not decay with k.",
        ),
        LegSpec(
            # Slope tier (T5) only.
            prompt="Re-measure radix_sort after the bucket-count change.",
            primary_check="results_traceable_radix_sort_leg4",
            checks=(
                VerifyCommand(
                    "results_traceable_radix_sort_leg4",
                    ("python3", "{verify_dir}/verify_traceability.py", "radix_sort"),
                ),
            ),
            fact_acquisition=_S6_FACT_ACQUISITION,
            mistake_signature=_S6_MISTAKE_SIGNATURE,
            rediscovery_work=_S6_REDISCOVERY_WORK,
            probe_files=_S6_PROBE_FILES,
            verify_scripts=_s6_verify_scripts(),
            arm_a_expectation="Still repeating at k=4.",
            memory_arm_expectation="Still free at k=4 -- this is the slope measurement.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Registry
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
    s.slug: s
    for s in (
        S1_RELEASE_VIA_CI,
        S2_SPEC_LIVES_OUTSIDE,
        S3_RUNNER_NOT_PYTEST,
        S4_SECRETS_NOT_DOTENV,
        S5_DEPLOY_REVERTED_BY_RECONCILER,
        S6_BENCH_BOX_ONLY,
    )
}


def get(slug: str) -> LongitudinalScenario:
    try:
        return SCENARIOS[slug]
    except KeyError:
        raise SystemExit(
            f"unknown scenario {slug!r}; known: {', '.join(sorted(SCENARIOS))}"
        ) from None
