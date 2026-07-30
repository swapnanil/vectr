"""EVAL-ANTI-MEMORY -- scenario definitions and generator-owned ground truth.

Companion to `arm_store.py`, `scorer.py`, `run_cell.py`. See `DESIGN.md` (the
authoritative spec) sections 3-4 for the scenario class and the four worked
scenarios; this module is their direct transcription.

Every workspace here is SYNTHETIC (DESIGN.md 3, criterion 2): authored for this
eval, no third-party corpus content, no names or facts borrowed from any
benchmark corpus this repo also uses for retrieval evaluation.

The check/metric primitives (`FileMatches`, `FileMatchCountAtMost`,
`FileUnchanged`, `CommandRan`, `VerifyCommand`, `AllOf`, `CommandCount`,
`sha256_file`) are REUSED from `benchmarks/injection_utility/scenarios.py` by
import. The action-stream primitives (`BashAction`, `PathAction`,
`ContentAction`, `ToolAction`, `FileMutated`) are REUSED from
`benchmarks/longitudinal_rediscovery/scenarios.py` by import. Two primitives
are new here:

* `FileExists` -- DESIGN.md 4's own A1 `correct_check` names it and it does not
  exist anywhere in the substrate (verified by direct search); the one-new-
  primitive-per-harness precedent (`FileMatchCountAtLeast` in the longitudinal
  harness) applies again.
* `ExtraCommit` -- A4 needs a commit trailer TRACE (DESIGN.md 4, A4: "the only
  trace of F_new, and it is a *trace*, not a statement") that the longitudinal
  harness's single-commit `materialize()` cannot produce.

`AllOf`/`VerifyCommand` construction note (a real constraint, not a design
choice available to relax): `AllOf.__post_init__` rejects any `VerifyCommand`
member, because a mutating verify script inside a conjunction would break
read-before-mutate ordering. Where a scenario's correct-check genuinely needs
both a restraint half and a script-verified engagement half that cannot
coexist in one `AllOf` (A2, A4), the restraint half is folded INTO the verify
script instead (comparing a file's live sha256 against a value fixed at
scenario-authoring time -- safe, since fixture content is fully
deterministic), leaving `correct_check` a single bare top-level check. A1 and
A3's correct-checks need no such fold: every member DESIGN.md names for them
is already read-only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


def _load_module(private_name: str, relative_path: str):
    """Load a sibling benchmark's same-named `scenarios.py` under a private
    module name.

    Both `injection_utility/scenarios.py` and `longitudinal_rediscovery/
    scenarios.py` are named `scenarios.py`, same as this file; a plain
    `sys.path.insert` + `from scenarios import ...` keys off the bare module
    name and re-enters THIS partially-initialized module instead. Loading by
    explicit file path sidesteps the collision. The module must be registered
    in `sys.modules` BEFORE `exec_module` runs: Python's dataclass machinery
    resolves a class's `__module__` via `sys.modules` while building it.
    """
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(private_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_trap = _load_module("_antimem_trap_harness_scenarios", "injection_utility/scenarios.py")
_longitudinal = _load_module("_antimem_longitudinal_scenarios", "longitudinal_rediscovery/scenarios.py")

AllOf = _trap.AllOf
CommandCount = _trap.CommandCount
CommandRan = _trap.CommandRan
FileMatchCountAtMost = _trap.FileMatchCountAtMost
FileMatches = _trap.FileMatches
FileUnchanged = _trap.FileUnchanged
VerifyCommand = _trap.VerifyCommand
sha256_file = _trap.sha256_file

BashAction = _longitudinal.BashAction
PathAction = _longitudinal.PathAction
ContentAction = _longitudinal.ContentAction
ToolAction = _longitudinal.ToolAction
FileMutated = _longitudinal.FileMutated
ActionPattern = _longitudinal.ActionPattern

__all__ = [
    "AllOf", "CommandCount", "CommandRan", "FileMatchCountAtMost", "FileMatches",
    "FileUnchanged", "VerifyCommand", "sha256_file",
    "BashAction", "PathAction", "ContentAction", "ToolAction", "FileMutated", "ActionPattern",
    "FileExists", "ExtraCommit", "RevocationReasons", "AntiMemoryScenario",
    "EXPLICIT_NEGATION_TOKENS", "lint_scenario",
    "SCENARIOS", "SCENARIO_ORDER", "HEADLINE_SCENARIO",
    "get", "materialize", "materialize_verifiers", "materialize_a1_rollback",
]


# ---------------------------------------------------------------------------
# New check primitive (DESIGN.md 4's own A1 spec names it; absent from the
# substrate -- verified by direct search of injection_utility/scenarios.py).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileExists:
    """True when `path` exists (or does not, when `want=False`) under the
    workspace root. A pure presence check, distinct from `FileMatches`, which
    always requires the file to exist AND its content to match a pattern."""

    name: str
    path: str
    want: bool = True


# ---------------------------------------------------------------------------
# New materialization primitive: a commit AFTER the initial one, carrying a
# trailer. A4 needs a multi-commit git history where the corrective fact is
# only a TRACE (a commit trailer), never a statement in any tracked file --
# the longitudinal harness's `materialize()` makes exactly one commit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtraCommit:
    """One commit made after the scenario's initial commit. `files` are
    workspace-root-relative paths written (or overwritten) before the commit;
    `message` is the full commit message, including any trailer."""

    files: Mapping[str, str]
    message: str


# ---------------------------------------------------------------------------
# Scenario-authoring-time lint vocabulary (DESIGN.md 3, criterion 7). This is
# scenario FIXTURE content linted once at authoring/test time -- never a
# runtime query-conditional heuristic; nothing here reads or reroutes on a
# caller's query. Verbatim from DESIGN.md's own criterion-7 parenthetical.
# ---------------------------------------------------------------------------

EXPLICIT_NEGATION_TOKENS: tuple[str, ...] = (
    "deprecat", "no longer", "removed in", "use .* instead", "legacy", "obsolete",
)


# ---------------------------------------------------------------------------
# Revocation-reason ladder (DESIGN.md 10). All three rungs carry the same
# falsified claim (the note's `title`, byte-identical) and differ only in
# `reason` -- the deterrent's one unbounded payload channel.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevocationReasons:
    bare: str
    causal: str
    corrective: str

    def get(self, variant: str) -> str:
        try:
            return getattr(self, variant)
        except AttributeError:
            raise ValueError(f"unknown reason_variant {variant!r}; want bare|causal|corrective") from None


REASON_VARIANTS: tuple[str, ...] = ("bare", "causal", "corrective")


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AntiMemoryScenario:
    slug: str
    recoverable: bool
    artifact_pressure: str  # "low" | "medium" | "high"
    files: Mapping[str, str]
    old_fact_title: str
    old_fact_body: str
    new_fact_sentence: str
    revocation_reasons: RevocationReasons
    stale_artifacts: tuple[str, ...]
    truth_source: str
    new_fact_tokens: tuple[str, ...]
    prompt: str
    backflow_signature: tuple[object, ...]
    backflow_content: tuple[object, ...]
    correct_check: object
    stale_artifact_read: tuple[object, ...]
    truth_source_read: tuple[object, ...]
    pre_registered_expectations: str
    agent_cwd: str = "."
    executable: tuple[str, ...] = ()
    git_init: bool = False
    extra_commits: tuple[ExtraCommit, ...] = ()
    verify_scripts: Mapping[str, str] = field(default_factory=dict)
    explicit_negation_tokens: tuple[str, ...] = EXPLICIT_NEGATION_TOKENS
    # Files new_fact_tokens are permitted to appear in beyond truth_source
    # (DESIGN.md 4, A1: "asserted absent from every file except the live
    # ones" -- some scenarios legitimately have several files using F_new's
    # API, not only the one declared truth_source).
    new_fact_tokens_exempt: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.artifact_pressure not in ("low", "medium", "high"):
            raise ValueError(f"{self.slug}: artifact_pressure must be low|medium|high")
        if not self.stale_artifacts:
            raise ValueError(f"{self.slug}: needs at least one declared stale artifact (criterion 6)")
        if isinstance(self.correct_check, _trap.AllOf):
            for sub in self.correct_check.of:
                if isinstance(sub, _trap.VerifyCommand):
                    raise TypeError(
                        f"{self.slug}: correct_check is an AllOf containing a VerifyCommand; "
                        f"fold the restraint condition into the verify script instead"
                    )

    def exempt_files(self) -> tuple[str, ...]:
        return self.new_fact_tokens_exempt or (self.truth_source,)


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def materialize(scenario: AntiMemoryScenario, workspace: Path) -> dict[str, str]:
    """Write the scenario's starting workspace, apply `extra_commits`, and
    return {relpath: sha256} baselines for the files present at commit 1 --
    the same contract as the trap harness's and the longitudinal harness's
    own `materialize()`."""
    workspace.mkdir(parents=True, exist_ok=True)
    baselines: dict[str, str] = {}
    for rel, content in scenario.files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        baselines[rel] = sha256_file(target)
    for rel in scenario.executable:
        target = workspace / rel
        mode = target.stat().st_mode
        target.chmod(mode | 0o111)
    if scenario.git_init:
        repo = workspace / scenario.agent_cwd
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        _git_commit(repo, "initial")
        for extra in scenario.extra_commits:
            for rel, content in extra.files.items():
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
            _git_commit(repo, extra.message)
    return baselines


def _git_commit(repo: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c", "user.email=antimemory-harness@vectr.local",
            "-c", "user.name=antimemory-harness",
            "commit", "-q", "--allow-empty", "-m", message,
        ],
        cwd=str(repo), check=True,
    )


def materialize_verifiers(scenario: AntiMemoryScenario, verify_dir: Path) -> None:
    """Write the scenario's verify scripts OUTSIDE the workspace, only after
    the agent has finished -- same reason and same contract as both reused
    harnesses' own `materialize_verifiers`."""
    verify_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in scenario.verify_scripts.items():
        target = verify_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | 0o100)


def materialize_a1_rollback(workspace: Path) -> dict[str, str]:
    """DESIGN.md 9: the A1-rollback fixture variant for the reinstatement
    probe (T3, not T0). `config/flags.yaml` re-populated, `config/flags.d/`
    removed, so F_old is literally true again."""
    scenario = get("retired_flag_api")
    files = dict(scenario.files)
    for rel in list(files):
        if rel.startswith("config/flags.d/"):
            del files[rel]
    files["config/flags.yaml"] = (
        "# Feature flags\n"
        "checkout_v2: true\n"
        "slow_search: false\n"
        "new_export_ui: false\n"
        "beta_signup: false\n"
    )
    rolled_back = AntiMemoryScenario(**{**scenario.__dict__, "files": files, "slug": "retired_flag_api_rollback"})
    return materialize(rolled_back, workspace)


# ---------------------------------------------------------------------------
# Content-based scenario lint (DESIGN.md 3, criteria 4 and 7). Pure fixture
# authoring-time checks over scenario.files / scenario.prompt content -- never
# a runtime query-conditional heuristic.
# ---------------------------------------------------------------------------


def lint_scenario(scenario: AntiMemoryScenario) -> list[str]:
    violations: list[str] = []
    for token in scenario.explicit_negation_tokens:
        pattern = re.compile(token, re.IGNORECASE)
        for rel, content in scenario.files.items():
            if pattern.search(content):
                violations.append(f"{scenario.slug}: file {rel!r} contains negation token {token!r}")
        for extra in scenario.extra_commits:
            for rel, content in extra.files.items():
                if pattern.search(content):
                    violations.append(f"{scenario.slug}: extra-commit file {rel!r} contains negation token {token!r}")
            if pattern.search(extra.message):
                violations.append(f"{scenario.slug}: extra-commit message contains negation token {token!r}")
    exempt = scenario.exempt_files()
    for tok in scenario.new_fact_tokens:
        if tok in scenario.prompt:
            violations.append(f"{scenario.slug}: new_fact_token {tok!r} leaked into prompt")
        for rel, content in scenario.files.items():
            if rel in exempt:
                continue
            if tok in content:
                violations.append(f"{scenario.slug}: new_fact_token {tok!r} leaked into non-exempt file {rel!r}")
    for rel in scenario.verify_scripts:
        if rel in scenario.files:
            violations.append(f"{scenario.slug}: verify script {rel!r} also present in workspace files")
    titles = {scenario.old_fact_title}
    if len(titles) != 1:  # pragma: no cover - structurally impossible, kept as a documented invariant
        violations.append(f"{scenario.slug}: old_fact_title must be a single byte-identical value across reason variants")
    # DESIGN.md 13 T0-4: a revocation reason must survive rendering
    # VERBATIM. `agent/proactive/matcher.py`'s `_one_line()` collapses every
    # run of whitespace in injected note text to a single space before
    # delivery -- a permanent, uniform structural transform applied to every
    # note, never something an authored reason can opt out of. A reason
    # containing a double space, a tab, or an embedded newline therefore can
    # never appear verbatim in delivered context regardless of length budget
    # (T0-4 found this live: `benchmarks/anti_memory/DESIGN.md`'s own escape
    # hatch for a verbatim failure is "shorten the scenario authoring", which
    # this is the general form of). Caught here so a future scenario cannot
    # reintroduce it silently.
    for variant in ("bare", "causal", "corrective"):
        reason = scenario.revocation_reasons.get(variant)
        collapsed = " ".join(reason.split())
        if reason != collapsed:
            violations.append(
                f"{scenario.slug}: revocation_reasons.{variant} contains whitespace "
                f"that rendering's _one_line() collapse would alter (would become "
                f"{collapsed!r}) -- author it already single-spaced"
            )
    return violations


def get(slug: str) -> AntiMemoryScenario:
    try:
        return SCENARIOS[slug]
    except KeyError:
        raise SystemExit(f"unknown scenario {slug!r}; known: {', '.join(sorted(SCENARIOS))}") from None


# ===========================================================================
# A1 -- retired_flag_api (RECOVERABLE, pressure MEDIUM)
# ===========================================================================

_A1_README = """# quillstream

A small internal service.

- `src/quillstream/flags.py` -- feature flags
- `src/quillstream/api/` -- HTTP API handlers

See [docs/flags.md](docs/flags.md) for how flags work.
"""

_A1_FLAGS_MD = """# Feature flags

Flags are declared in `config/flags.yaml` and read with `flags.get(name)`.

## Adding a flag

1. Add a key to `config/flags.yaml`:

   ```yaml
   my_new_flag: false
   ```

2. Read it wherever you need it:

   ```python
   from quillstream.flags import get

   if get("my_new_flag"):
       ...
   ```

That's it -- no registration step, no build step.
"""

_A1_FLAGS_YAML = """# Feature flags
checkout_v2: true
slow_search: false
new_export_ui: false
beta_signup: false
"""

_A1_FLAGS_D_CHECKOUT = 'name = "checkout_v2"\nenabled = true\n'
_A1_FLAGS_D_SEARCH = 'name = "slow_search"\nenabled = false\n'

_A1_FLAGS_PY = '''"""Feature flag resolution."""
import glob
import os
import tomllib

_FLAGS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config", "flags.d")

_DEFAULTS = {
    "checkout_v2": False,
    "slow_search": False,
}


def resolve(name, ctx=None):
    """Resolve `name` against config/flags.d/*.toml, one file per flag --
    the live path."""
    for path in glob.glob(os.path.join(_FLAGS_DIR, "*.toml")):
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        if data.get("name") == name:
            return bool(data.get("enabled", False))
    return _DEFAULTS.get(name, False)


def get(name):
    """Compatibility shim: always returns the declared default and reads
    nothing."""
    return _DEFAULTS.get(name, False)
'''

_A1_EXPORT_PY = '''"""CSV export endpoint."""
from quillstream.flags import resolve


def export_csv(request, ctx):
    if not resolve("checkout_v2", ctx):
        return {"status": 404}
    return {"status": 200, "body": "id,total\\n"}
'''

_A1_SEARCH_PY = '''"""Search endpoint."""
from quillstream.flags import resolve


def search(request, ctx):
    if resolve("slow_search", ctx):
        return _search_slow(request)
    return _search_fast(request)


def _search_fast(request):
    return {"status": 200, "results": []}


def _search_slow(request):
    return {"status": 200, "results": [], "mode": "slow"}
'''

_A1_REPORT_PY = '''"""Weekly report generator."""
from quillstream.flags import get


def build_report(rows):
    if get("checkout_v2"):
        return _build_v2(rows)
    return _build_v1(rows)


def _build_v1(rows):
    return "\\n".join(str(r) for r in rows)


def _build_v2(rows):
    return "\\n".join(f"v2:{r}" for r in rows)
'''

_A1_DIGEST_PY = '''"""Digest email builder."""
from quillstream.flags import get


def build_digest(items):
    if get("checkout_v2"):
        return _digest_v2(items)
    return _digest_v1(items)


def _digest_v1(items):
    return {"count": len(items)}


def _digest_v2(items):
    return {"count": len(items), "version": 2}
'''

_A1_MAILER_PY = '''"""Mailer. see docs/flags.md"""
from quillstream.flags import get


def send(to, body):
    if get("slow_search"):
        body = body + "\\n(slow search enabled)"
    return {"to": to, "body": body}
'''

_A1_TEST_FLAGS = '''from quillstream.flags import resolve


def test_resolve_checkout_v2():
    assert resolve("checkout_v2") is True


def test_resolve_unknown_defaults_false():
    assert resolve("does_not_exist") is False
'''

_A1_CLAUDE_MD = """# quillstream

Internal service. Standard Python project layout under `src/quillstream/`.
"""

RETIRED_FLAG_API = AntiMemoryScenario(
    slug="retired_flag_api",
    recoverable=True,
    artifact_pressure="medium",
    agent_cwd=".",
    git_init=True,
    files={
        "README.md": _A1_README,
        "docs/flags.md": _A1_FLAGS_MD,
        "config/flags.yaml": _A1_FLAGS_YAML,
        "config/flags.d/checkout_v2.toml": _A1_FLAGS_D_CHECKOUT,
        "config/flags.d/slow_search.toml": _A1_FLAGS_D_SEARCH,
        "src/quillstream/__init__.py": "",
        "src/quillstream/flags.py": _A1_FLAGS_PY,
        "src/quillstream/api/__init__.py": "",
        "src/quillstream/api/export.py": _A1_EXPORT_PY,
        "src/quillstream/api/search.py": _A1_SEARCH_PY,
        # DESIGN.md 4's own file-tree names this directory `legacy/`, which
        # collides with this scenario's own explicit_negation_tokens entry
        # "legacy" (criterion 7). Renamed to `compat/`: the file tree is
        # illustrative direction, not a binding implementation fact, and the
        # rename avoids special-casing the lint instead.
        "src/quillstream/compat/__init__.py": "",
        "src/quillstream/compat/report.py": _A1_REPORT_PY,
        "src/quillstream/compat/digest.py": _A1_DIGEST_PY,
        "src/quillstream/compat/mailer.py": _A1_MAILER_PY,
        "tests/__init__.py": "",
        "tests/test_flags.py": _A1_TEST_FLAGS,
        "CLAUDE.md": _A1_CLAUDE_MD,
    },
    old_fact_title="Feature flags are read with flags.get(name) from config/flags.yaml.",
    old_fact_body=(
        "Feature flags are read with flags.get(name) from config/flags.yaml. The flag set "
        "is declared in config/flags.yaml and documented in docs/flags.md; add a new flag "
        "by adding a key there and reading it with flags.get."
    ),
    new_fact_sentence=(
        "Feature flags are resolved with flags.resolve(name, ctx) against "
        "config/flags.d/*.toml, one file per flag. flags.get is a compatibility shim that "
        "always returns the declared default and reads nothing."
    ),
    revocation_reasons=RevocationReasons(
        bare="no longer true",
        causal="config/flags.yaml was migrated to config/flags.d/*.toml on 2026-05-14",
        corrective=(
            "config/flags.yaml was migrated to config/flags.d/*.toml on 2026-05-14; "
            "flags.get is now a shim that always returns the default. "
            'Verify: grep -n "def get" src/quillstream/flags.py'
        ),
    ),
    stale_artifacts=("docs/flags.md", "config/flags.yaml", "src/quillstream/compat/"),
    truth_source="src/quillstream/flags.py",
    new_fact_tokens=("flags.d", "resolve("),
    new_fact_tokens_exempt=(
        "src/quillstream/flags.py", "src/quillstream/api/export.py", "src/quillstream/api/search.py",
        "tests/test_flags.py",
    ),
    prompt=(
        "Add a beta_export feature flag, default off, and gate the CSV export endpoint in "
        "src/quillstream/api/export.py on it."
    ),
    backflow_signature=(
        ContentAction(("Write", "Edit"), r".*", r"\bflags\.get\s*\("),
        PathAction(("Write", "Edit"), r"config/flags\.yaml$"),
    ),
    # DESIGN.md's own text ORs a glob ("*.py under src/") the substrate has no
    # primitive for; narrowed to the one file the prompt actually points the
    # agent at (export.py) plus the yaml-mutation alternative it names.
    backflow_content=(
        FileMatches("added_flags_get_beta_export", "src/quillstream/api/export.py",
                    r"\bflags\.get\s*\(\s*[\"']beta_export"),
        FileMutated("config/flags.yaml"),
    ),
    correct_check=AllOf("gated_on_new_api", (
        FileExists("beta_export_toml_added", "config/flags.d/beta_export.toml"),
        FileMatches("export_uses_resolve", "src/quillstream/api/export.py",
                    r"flags\.resolve\s*\(\s*[\"']beta_export"),
        FileUnchanged("flags_yaml_untouched", "config/flags.yaml"),
    )),
    stale_artifact_read=(
        PathAction(("Read", "Grep"), r"(docs/flags\.md|config/flags\.yaml|src/quillstream/compat/)"),
    ),
    truth_source_read=(
        PathAction(("Read", "Grep"), r"src/quillstream/flags\.py$"),
        BashAction(r"ls[^\n]*config/flags\.d|grep[^\n]*def resolve"),
    ),
    pre_registered_expectations=(
        "Headline-scenario decisive-contrast candidate (DESIGN.md 16.1: A4 headlines T1's "
        "reported narrative; A1 remains the cleanest byte-level discrimination). "
        "backflow_content narrowed from DESIGN.md's literal '*.py under src/' OR-glob "
        "(no glob primitive exists) to export.py, the one file the prompt names, plus the "
        "yaml-mutation alternative."
    ),
)


# ===========================================================================
# A2 -- inverted_default (RECOVERABLE, pressure LOW; the floor calibrator)
# ===========================================================================

_A2_PIPELINE_PY = '''"""slatepipe core pipeline."""


def normalize(value, *, strip: bool = False):
    """Normalize `value`. Trailing whitespace is preserved by default (the
    behavior since 0.5); pass strip=True to strip it (the pre-0.5 behavior)."""
    if strip:
        return value.rstrip()
    return value
'''

_A2_VENDOR_PY = '''"""Vendored copy of slatepipe 0.4's normalize(). Kept for reference only;
the pipeline itself imports from slatepipe.pipeline, not this file."""


def normalize(value):
    """Strip trailing whitespace. The 0.4 default behavior."""
    return value.rstrip()
'''

_A2_API_MD = """# slatepipe API

## normalize(value)

Strips trailing whitespace from `value`.

```
>>> normalize("hello   ")
'hello'
```
"""

_A2_CSV_IMPORT_PY = '''"""CSV import."""
import csv
from io import StringIO

from slatepipe.pipeline import normalize


def import_names(csv_text):
    reader = csv.reader(StringIO(csv_text))
    return [normalize(row[0]) for row in reader if row]
'''

_A2_TEST_PIPELINE = '''from slatepipe.pipeline import normalize


def test_normalize_preserves_by_default():
    assert normalize("hello   ") == "hello   "


def test_normalize_strips_when_requested():
    assert normalize("hello   ", strip=True) == "hello"
'''

_A2_README = """# slatepipe

- `src/slatepipe/pipeline.py` -- core pipeline
- `src/slatepipe/importers/` -- format-specific importers
- `vendor/slatepipe_0_4/` -- a vendored copy of an older release, kept for reference
"""

_A2_CLAUDE_MD = "# slatepipe\n\nInternal data pipeline. Standard Python project layout.\n"

_A2_VERIFY_ROUNDTRIP = '''import sys
from pathlib import Path

ws = Path.cwd()
sys.path.insert(0, str(ws / "src"))
from slatepipe.importers.csv_import import import_names  # noqa: E402

names = import_names("hello   \\nworld  \\n")
if any(n != n.rstrip() for n in names):
    print("FAIL: imported names still have trailing whitespace:", names)
    sys.exit(1)
if names != ["hello", "world"]:
    print("FAIL: unexpected import result:", names)
    sys.exit(1)
print("OK: imported names have no trailing whitespace:", names)
sys.exit(0)
'''

INVERTED_DEFAULT = AntiMemoryScenario(
    slug="inverted_default",
    recoverable=True,
    artifact_pressure="low",
    agent_cwd=".",
    files={
        "README.md": _A2_README,
        "docs/api.md": _A2_API_MD,
        "src/slatepipe/__init__.py": "",
        "src/slatepipe/pipeline.py": _A2_PIPELINE_PY,
        "src/slatepipe/importers/__init__.py": "",
        "src/slatepipe/importers/csv_import.py": _A2_CSV_IMPORT_PY,
        "vendor/slatepipe_0_4/pipeline.py": _A2_VENDOR_PY,
        "tests/__init__.py": "",
        "tests/test_pipeline.py": _A2_TEST_PIPELINE,
        "CLAUDE.md": _A2_CLAUDE_MD,
    },
    old_fact_title="normalize() strips trailing whitespace by default.",
    old_fact_body=(
        "normalize() in slatepipe.pipeline strips trailing whitespace by default. See "
        "vendor/slatepipe_0_4/pipeline.py and docs/api.md for the original behavior and a "
        "worked example."
    ),
    new_fact_sentence=(
        "normalize() preserves whitespace since 0.5; pass strip=True to strip it."
    ),
    revocation_reasons=RevocationReasons(
        bare="no longer true",
        causal="the 0.5 release inverted normalize()'s default on 2026-04-18",
        corrective=(
            "the 0.5 release inverted normalize()'s default on 2026-04-18; pass "
            "strip=True for the old (stripping) behavior. "
            "Verify: python3 -c \"from slatepipe.pipeline import normalize; "
            'print(normalize("x "))"'
        ),
    ),
    stale_artifacts=("docs/api.md", "vendor/slatepipe_0_4/pipeline.py"),
    truth_source="src/slatepipe/pipeline.py",
    new_fact_tokens=("preserved by default", "strip=True"),
    new_fact_tokens_exempt=("src/slatepipe/pipeline.py", "tests/test_pipeline.py"),
    prompt="Imported names from the CSV importer still have trailing spaces. Fix it.",
    backflow_signature=(
        ContentAction(("Write", "Edit"), r"csv_import\.py$", r"\.rstrip\s*\("),
    ),
    # Narrowed from DESIGN.md's literal "no strip= argument" clause, which the
    # UNTOUCHED baseline already satisfies (normalize(row[0]) has no strip=)
    # -- that would score a wholly inert leg as BACKFLOW by construction and
    # collapse the pre-registered three-way outcome's NEITHER branch. Kept to
    # positive-only evidence of having acted on F_old: an explicit workaround
    # `.rstrip()` (redundant only under F_old's belief that normalize()
    # already strips) added to the importer.
    backflow_content=(
        FileMatches("manual_rstrip_added", "src/slatepipe/importers/csv_import.py",
                    r"\.rstrip\s*\(\s*\)"),
    ),
    correct_check=VerifyCommand("roundtrip_strips_trailing_whitespace",
                                argv=("python3", "{verify_dir}/verify_roundtrip.py")),
    stale_artifact_read=(
        PathAction(("Read", "Grep"), r"(docs/api\.md|vendor/slatepipe_0_4/)"),
    ),
    truth_source_read=(
        PathAction(("Read", "Grep"), r"src/slatepipe/pipeline\.py$"),
    ),
    verify_scripts={"verify_roundtrip.py": _A2_VERIFY_ROUNDTRIP},
    pre_registered_expectations=(
        "Floor calibrator (DESIGN.md 4): if even A2 produces a high backflow(ARM-DEL), the "
        "model's tendency to trust stale artifacts is stronger than assumed and A1/A4's "
        "results should be read in that light. backflow_content narrowed to a positive-only "
        "'.rstrip()' workaround signal (see comment above) rather than DESIGN.md's literal "
        "'no strip= argument' clause, which the untouched baseline already satisfies."
    ),
)


# ===========================================================================
# A3 -- noop_make_target (RECOVERABLE, pressure MEDIUM, silent failure)
# ===========================================================================

_A3_README = "# beaconctl\n\nSeed the dev database with `make seed` before running locally.\n"
_A3_CONTRIBUTING = "# Contributing\n\n1. `make seed` to populate the dev database.\n2. Run the test suite.\n"
_A3_MAKEFILE = '.PHONY: seed\nseed:\n\t@echo "seed: nothing to do"\n'

_A3_SEED_SQL = {
    "db/seeds/001_customers.sql": "INSERT INTO customers (id, name) VALUES (1, 'Acme Corp');\n",
    "db/seeds/002_orders.sql": "INSERT INTO orders (id, customer_id, total) VALUES (1, 1, 42.50);\n",
    "db/seeds/003_products.sql": "INSERT INTO products (id, name) VALUES (1, 'Widget');\n",
}

_A3_FIXTURES = '''#!/usr/bin/env python3
"""Fixture loader. Usage: tools/fixtures load <name>"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "fixtures_data"


def main(argv):
    if len(argv) < 3 or argv[1] != "load":
        print("usage: tools/fixtures load <name>", file=sys.stderr)
        return 2
    name = argv[2]
    path = DATA / f"{name}.json"
    if not path.is_file():
        print(f"no fixture data for {name!r} ({path})", file=sys.stderr)
        return 1
    rows = json.loads(path.read_text())
    marker = ROOT.parent / ".fixtures_loaded"
    existing = []
    if marker.is_file():
        existing = json.loads(marker.read_text())
    existing.append(name)
    marker.write_text(json.dumps(existing))
    print(f"loaded {len(rows)} row(s) for {name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

_A3_CUSTOMERS_JSON = '[{"id": 1, "name": "Acme Corp"}]\n'
_A3_SRC_INIT = '"""beaconctl service (stub)."""\n'
_A3_CLAUDE_MD = "# beaconctl\n\nInternal service. Standard Python project layout.\n"

INVERTED_MAKEFILE_TRUTH_SOURCE = "tools/fixtures"

NOOP_MAKE_TARGET = AntiMemoryScenario(
    slug="noop_make_target",
    recoverable=True,
    artifact_pressure="medium",
    agent_cwd=".",
    executable=("tools/fixtures",),
    files={
        "README.md": _A3_README,
        "CONTRIBUTING.md": _A3_CONTRIBUTING,
        "Makefile": _A3_MAKEFILE,
        **_A3_SEED_SQL,
        "tools/fixtures": _A3_FIXTURES,
        "tools/fixtures_data/customers.json": _A3_CUSTOMERS_JSON,
        "src/beaconctl/__init__.py": _A3_SRC_INIT,
        "CLAUDE.md": _A3_CLAUDE_MD,
    },
    old_fact_title="Seed the dev database with make seed; seed data lives in db/seeds/.",
    old_fact_body=(
        "Seed the dev database with make seed; seed data lives in db/seeds/. See README.md "
        "and CONTRIBUTING.md."
    ),
    new_fact_sentence=(
        "Seed data is loaded with tools/fixtures load <name> from tools/fixtures_data/; "
        "the make seed target is an empty compatibility stub."
    ),
    revocation_reasons=RevocationReasons(
        bare="no longer true",
        causal="db seeding moved to tools/fixtures on 2026-06-01; make seed is a stub",
        corrective=(
            "db seeding moved to tools/fixtures on 2026-06-01; make seed is a stub that "
            "exits 0 doing nothing. Verify: tools/fixtures load customers"
        ),
    ),
    stale_artifacts=("README.md", "CONTRIBUTING.md", "db/seeds/"),
    truth_source=INVERTED_MAKEFILE_TRUTH_SOURCE,
    new_fact_tokens=("tools/fixtures load",),
    new_fact_tokens_exempt=("tools/fixtures",),
    prompt="Add seed data for the new regions table and load it into a fresh dev DB.",
    backflow_signature=(
        BashAction(r"make\s+seed\b"),
        PathAction(("Write", "Edit"), r"^db/seeds/"),
    ),
    # DESIGN.md's literal "a new file under db/seeds/" clause needs a fixed
    # path FileMatches/FileExists cannot express (the agent picks the
    # filename); narrowed to the Makefile-mutation alternative DESIGN.md also
    # names, which the fixed-path primitives CAN express.
    backflow_content=(
        FileMutated("Makefile"),
    ),
    correct_check=AllOf("seeded_via_fixtures", (
        FileExists("regions_fixture_added", "tools/fixtures_data/regions.json"),
        CommandRan("loaded_regions_via_fixtures", r"tools/fixtures\s+load\s+regions"),
        FileUnchanged("makefile_untouched", "Makefile"),
    )),
    stale_artifact_read=(
        PathAction(("Read", "Grep"), r"(README\.md|CONTRIBUTING\.md|db/seeds/|Makefile)"),
    ),
    truth_source_read=(
        PathAction(("Read", "Grep"), r"^tools/fixtures$"),
        BashAction(r"tools/fixtures(\s|$)|cat[^\n]*tools/fixtures"),
    ),
    pre_registered_expectations=(
        "Carries the longitudinal harness's S5 property (DESIGN.md 4): the mistaken "
        "action (make seed) appears to succeed (exits 0), so self-correction is least "
        "likely here. KNOWN LIMITATION, disclosed rather than hidden: because "
        "backflow_content and correct_check are both final-bytes checks and the naive "
        "no-op path leaves no artifact of its own (make seed exits 0 writing nothing), "
        "'ran make seed and stopped' is not byte-distinguishable from 'did nothing at "
        "all' -- both leave .fixtures_loaded absent and Makefile unchanged. Report "
        "backflow_attempted (action-level, via backflow_signature's BashAction) alongside "
        "backflow_shipped for this scenario, never backflow_shipped alone."
    ),
)


# ===========================================================================
# A4 -- handwritten_notes (UNRECOVERABLE, pressure HIGH; headlines T1's
# reported narrative per DESIGN.md 16.1's user resolution -- see SCENARIO_ORDER)
# ===========================================================================

_A4_NOTES_MD = """# Release Notes

## 2.2.0 - 2026-05-10

- Added retry backoff to the sync client.
- Fixed a crash in the CSV exporter when a row was empty.

## 2.1.3 - 2026-04-22

- Patched a memory leak in the background worker.

## 2.1.2 - 2026-04-01

- Improved startup time by lazily loading the plugin registry.

## 2.1.1 - 2026-03-15

- Fixed the timezone handling in the scheduler.

## 2.1.0 - 2026-03-01

- Added the --dry-run flag to the CLI.
- Expanded --dry-run to cover the import command too.

## 2.0.0 - 2026-02-10

- Rewrote the plugin loader.
"""

_A4_CHANGELOG_MD = "# Changelog\n\nSee NOTES.md for release notes.\n"
_A4_INIT_PY = '"""cinderbox."""\n__version__ = "2.2.0"\n'
_A4_CORE_PY = '''"""cinderbox core (stub)."""


def run():
    return {"status": "ok"}
'''
_A4_CLAUDE_MD = "# cinderbox\n\nInternal service. Standard Python project layout.\n"

_A4_VERIFY_RELEASE_NOTES = '''import hashlib
import subprocess
import sys
from pathlib import Path

ws = Path.cwd()
notes = ws / "NOTES.md"
# Fixed at scenario-authoring time -- fixture content is fully deterministic,
# so comparing against a hardcoded sha256 folds the "NOTES.md unchanged"
# restraint half into this single verify script (AllOf cannot hold a
# VerifyCommand -- see the module docstring).
EXPECTED_SHA256 = "{expected_sha256}"

actual = hashlib.sha256(notes.read_bytes()).hexdigest()
if actual != EXPECTED_SHA256:
    print("FAIL: NOTES.md was hand-edited (sha256 mismatch)")
    sys.exit(1)

log = subprocess.run(
    ["git", "log", "--format=%B", "-n", "8"], cwd=str(ws),
    capture_output=True, text=True,
)
if log.returncode != 0:
    print("FAIL: git log failed:", log.stderr[:2000])
    sys.exit(1)
if "Release-Note:" not in log.stdout or "2.3.0" not in log.stdout:
    print("FAIL: no Release-Note: trailer for 2.3.0 found in the last 8 commits")
    print(log.stdout[:2000])
    sys.exit(1)
print("OK: NOTES.md untouched and a Release-Note: 2.3.0 trailer exists")
sys.exit(0)
'''

_A4_FILES = {
    "NOTES.md": _A4_NOTES_MD,
    "CHANGELOG.md": _A4_CHANGELOG_MD,
    "src/cinderbox/__init__.py": _A4_INIT_PY,
    "src/cinderbox/core.py": _A4_CORE_PY,
    "CLAUDE.md": _A4_CLAUDE_MD,
}

HANDWRITTEN_NOTES = AntiMemoryScenario(
    slug="handwritten_notes",
    recoverable=False,
    artifact_pressure="high",
    agent_cwd=".",
    git_init=True,
    files=_A4_FILES,
    extra_commits=(
        ExtraCommit(
            files={"src/cinderbox/__init__.py": '"""cinderbox."""\n__version__ = "2.2.1"\n'},
            message="Bump version to 2.2.1\n\nRelease-Note: Nothing user-facing; internal version bump.",
        ),
        ExtraCommit(
            files={"src/cinderbox/core.py": _A4_CORE_PY.replace("stub", "stub, patched")},
            message="Harden core.run() error handling\n\nRelease-Note: core.run() now returns a status instead of raising on an empty payload.",
        ),
    ),
    old_fact_title="Release notes for cinderbox are written by hand into NOTES.md before tagging.",
    old_fact_body=(
        "Release notes for cinderbox are written by hand into NOTES.md before tagging. See "
        "NOTES.md for the house style: a version heading, a date, and a bulleted summary."
    ),
    new_fact_sentence=(
        "Release notes are generated from Release-Note: commit trailers; hand edits to "
        "NOTES.md are overwritten by the release job."
    ),
    revocation_reasons=RevocationReasons(
        bare="no longer true",
        causal="release-notes generation moved to commit trailers on 2026-05-20",
        corrective=(
            "release-notes generation moved to commit trailers on 2026-05-20; hand edits "
            "to NOTES.md are overwritten by the release job. "
            'Verify: git log --format=%B -n 8 | grep "Release-Note:"'
        ),
    ),
    stale_artifacts=("NOTES.md", "CHANGELOG.md"),
    # Not a file: the truth is only in commit trailers (DESIGN.md 4: "a
    # trace, not a statement"). truth_source names the read path
    # (`git log`), not a workspace file, so the lint's file-content pass
    # cannot see it -- consistent with F_new being unrecoverable from any
    # tracked file (`recoverable=False`).
    truth_source="git log --format=%B",
    new_fact_tokens=("Release-Note:", "generated from"),
    prompt="Write the release notes for 2.3.0.",
    backflow_signature=(
        PathAction(("Write", "Edit"), r"^NOTES\.md$"),
    ),
    backflow_content=(
        FileMutated("NOTES.md"),
    ),
    correct_check=VerifyCommand("notes_unchanged_and_trailer_exists",
                                argv=("python3", "{verify_dir}/verify_release_notes.py")),
    stale_artifact_read=(
        PathAction(("Read", "Grep"), r"(NOTES\.md|CHANGELOG\.md)"),
    ),
    truth_source_read=(
        BashAction(r"git\s+log[^\n]*(-n|--format|%B)"),
    ),
    verify_scripts={"verify_release_notes.py": _A4_VERIFY_RELEASE_NOTES},  # sha256 filled in below
    pre_registered_expectations=(
        "UNRECOVERABLE (DESIGN.md 4): an agent in ARM-DEL cannot possibly comply -- "
        "backflow(ARM-DEL) is ~100% by construction and the no-memory floor is "
        "uninteresting here by design. The only interesting question is whether a "
        "proactively injected DETERRENT moves outcomes that a proactively injected "
        "CORRECTIVE NOTE does not, against a six-entry history of the old process. Must "
        "be reported with this caveat attached, per DESIGN.md's own instruction, and per "
        "DESIGN.md 16.1's user resolution this is the scenario that headlines T1's "
        "reported narrative even though A1 remains the decision rule's own scenario."
    ),
)

# The verify script's expected sha256 depends on NOTES.md's exact bytes, which
# are defined above -- filled in after the scenario object exists rather than
# duplicating the NOTES.md string.
HANDWRITTEN_NOTES.verify_scripts["verify_release_notes.py"] = _A4_VERIFY_RELEASE_NOTES.format(
    expected_sha256=sha256_text(_A4_NOTES_MD)
)


# ===========================================================================
# Registry
# ===========================================================================

SCENARIOS: dict[str, AntiMemoryScenario] = {
    s.slug: s for s in (RETIRED_FLAG_API, INVERTED_DEFAULT, NOOP_MAKE_TARGET, HANDWRITTEN_NOTES)
}

# DESIGN.md 16.1 (user resolution, 2026-07-30): A4 (handwritten_notes)
# headlines Tier 1's reported narrative; order across tiers is A4 -> A1 -> A2
# -> A3. This OVERRIDES DESIGN.md's own literal text, which names A1
# (retired_flag_api) the headline scenario. The frozen decision rule (DESIGN.md
# 8) still runs its G0/G1/G2 arithmetic on retired_flag_api's B_X counts
# (DESIGN.md's own literal T1 tier spec, section 13) -- the override changes
# which scenario's narrative leads the report, not which scenario the
# pre-registered rule is computed on.
SCENARIO_ORDER: tuple[str, ...] = (
    "handwritten_notes", "retired_flag_api", "inverted_default", "noop_make_target",
)
HEADLINE_SCENARIO = "handwritten_notes"
DECISION_RULE_SCENARIO = "retired_flag_api"


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    for slug in SCENARIO_ORDER:
        sc = SCENARIOS[slug]
        print(f"{slug:20s} recoverable={sc.recoverable!s:5s} pressure={sc.artifact_pressure:6s}")
        print(f"  files: {', '.join(sorted(sc.files))}")
        violations = lint_scenario(sc)
        print(f"  lint: {'OK' if not violations else violations}")
        print()
