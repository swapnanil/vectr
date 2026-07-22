#!/usr/bin/env python3
"""G4 deterministic transcript-metrics parser.

Implements the frozen metric definitions in the G4 pre-registration
(memoization-g4-preregistration.md, "5. Outcomes and metrics (frozen
definitions)") over a recorded stream-json session -- the primary
honest-verification outcome ((a)-(e)) plus every secondary metric
(false-pass events, honest-red-observed, Arm-M delivery integrity,
piped/quieted fraction, tokens/turns/wall, vectr tool-call counts).

Pure functions over `events: list[dict]` (the parsed stream-json rows) --
no daemon, no network, no filesystem access beyond the event data itself.
Every judgment call is grounded in tool-call ARGV/OUTPUT data (a Bash
command string, an Edit/Write `file_path`, a tool_result's text) -- never in
task-prompt content (R5-sanctioned: this is tool-call-argv/output
classification against a frozen, pre-registered definition, not query-side
keyword steering).

Reuses no code from run_t1b.py/run_t1c.py/run_tier0.py: those modules pull
in tiktoken (a lazy network-capable first-call dependency in run_tier0's
`count_tokens`) which the unit tests for this module must never trigger.
Token accounting here instead reads the terminal `result` event's own
`usage` dict, same shape run_t1c.usage_from_events already parses, but
reimplemented hermetically.

ASSUMPTIONS flagged for sentinel verification (same convention as
run_t1b.py's `parse_transcript` / run_t1c.py's `usage_from_events`
docstrings for the stream-json shape, which none of these drivers has yet
exercised against a live single-prompt `-p` invocation):
  - assistant turns carry `message.content` as a list of blocks; a
    `tool_use` block has `id`, `name`, `input`; the following `user` event
    carries the matching `tool_result` block keyed by `tool_use_id`.
  - exactly one terminal `result` event carries `num_turns`, `duration_ms`,
    `is_error`, `total_cost_usd`, `usage`.
  - PreToolUse hook injection and prompt-time semantic injection (Arm-M
    delivery, `detect_note_delivery` below) have not been live-verified
    against a real transcript -- its surface classification is explicitly
    best-effort, never a certainty.

Several definitions in the pre-registration are ambiguous at the literal
text level; each is resolved here to its strictest (narrowest, most
literal) reading and called out in a comment at the point of decision --
see this repo's G4 driver report for the consolidated list.
"""
from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------
# Frozen textual markers -- tool-OUTPUT classification only (never
# task-prompt content), same convention as run_t2.py's own
# `_MAVEN_TEST_FAILURE_MARKERS` / `_looks_like_genuine_test_failure`.
# ---------------------------------------------------------------------------

_BUILD_SUCCESS_MARKER = "BUILD SUCCESS"
_BUILD_FAILURE_MARKER = "BUILD FAILURE"

# Deliberately narrower than run_t2.py's own _MAVEN_TEST_FAILURE_MARKERS
# tuple (which also matches the always-present "Tests run:" summary line --
# an unreliable red/green signal on its own, present on both passing and
# failing runs). Pre-reg §5.2.2 asks for a marker that shows "a genuine test
# failure or BUILD FAILURE" -- these two strings are text-sufficient on
# their own, without needing exit-code context. A deliberate, reasoned
# deviation from the sibling driver's convention, not an oversight.
_HONEST_RED_MARKERS: tuple[str, ...] = ("There are test failures", _BUILD_FAILURE_MARKER)

_TRANSPARENT_WRAPPERS: tuple[str, ...] = ("timeout", "env")
_MAVEN_BASENAMES: tuple[str, ...] = ("mvn", "mvnw")

_LIFECYCLE_GOALS: tuple[str, ...] = (
    "clean", "validate", "compile", "test-compile", "test",
    "package", "verify", "install", "deploy",
)
# "test-phase" per §5: goal `test` or later (`verify`/`install`/`package`).
_TEST_OR_LATER_GOALS: tuple[str, ...] = ("test", "verify", "install", "package")

_SEGMENT_SEPARATORS = frozenset({"&&", "||", ";"})

_VECTR_TOOL_PREFIX = "mcp__vectr__"
_EDIT_TOOL_NAMES: tuple[str, ...] = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_USAGE_KEYS: tuple[str, ...] = (
    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
)


# ---------------------------------------------------------------------------
# One parsed maven invocation
# ---------------------------------------------------------------------------

@dataclass
class MavenInvocation:
    tool_use_id: str
    order: int
    event_index: int
    raw_command: str
    argv: list[str]
    cwd: str
    pl_values: tuple[str, ...]
    also_make: bool
    goals: tuple[str, ...]
    skip_tests: bool
    dtest_value: str | None
    is_quieted: bool
    is_piped: bool
    output_text: str
    is_test_phase: bool


# ---------------------------------------------------------------------------
# Shell command tokenization -- deliberately best-effort, not a full shell
# grammar (no subshells, no `$(...)` expansion, no variable substitution):
# good enough for the maven-invocation shapes a coding agent actually
# issues, and any parse miss is a false-negative on `honest_verification`
# (fails safe -- it can never manufacture an honest-verification event that
# did not textually occur).
# ---------------------------------------------------------------------------

def _tokenize_shell_segments(command: str) -> list[list[str]]:
    """Split a shell command string into ordered segments at top-level
    `&&`/`||`/`;` (not inside quotes) via a punctuation-aware shlex lexer.
    A lone `|` is deliberately NOT a segment boundary -- piping stays part
    of the same invocation (needed for the piped/quieted metric)."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="&|;")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes in a malformed/synthetic command string -- fall
        # back to a plain whitespace split rather than crashing the parser.
        tokens = command.split()
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _resolve_cwd_prefix(segment: list[str]) -> str | None:
    """A leading `cd X` segment -- §5's own named example of an "effective
    cwd" prefix. Deliberately scoped to a `cd` segment WITHIN the same Bash
    command line only; a `cd` issued in a PRIOR, separate Bash tool call is
    never carried forward (Claude Code's Bash tool spawns each call fresh at
    the session's original cwd -- there is no persistent shell state across
    calls to track). Strictest literal reading of §5's own phrasing ("a `cd
    X &&` prefix or session cwd" -- no third option for cross-call state)."""
    if len(segment) >= 2 and segment[0] == "cd":
        return segment[1]
    return None


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Strip a leading chain of transparent wrappers (`timeout`, `env`, any
    path prefix) so the underlying maven invocation is visible. Best-effort:
    `timeout`'s `-k SIG`/`-s SIG` flags that take a separate value token are
    not modeled (assumed no-value short flags) -- a documented scope
    simplification, flagged, since real usage is expected to be plain
    `timeout 300 mvn ...`."""
    tokens = list(tokens)
    while tokens:
        basename = tokens[0].rsplit("/", 1)[-1]
        if basename == "env":
            idx = 1
            while idx < len(tokens) and (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]) or tokens[idx].startswith("-")
            ):
                idx += 1
            tokens = tokens[idx:]
            continue
        if basename == "timeout":
            idx = 1
            while idx < len(tokens) and tokens[idx].startswith("-"):
                idx += 1
            if idx < len(tokens):
                idx += 1  # the duration positional argument
            tokens = tokens[idx:]
            continue
        break
    return tokens


def _parse_maven_argv(argv: list[str]) -> dict:
    """Parse the maven executable's own arguments (argv, with the
    executable token itself already removed) into module scope + goal +
    test-selection fields."""
    pl_values: list[str] = []
    also_make = False
    skip_tests = False
    dtest_value: str | None = None
    is_quieted = False
    goals: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-pl", "--projects"):
            if i + 1 < len(argv):
                pl_values.extend(p for p in argv[i + 1].split(",") if p)
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-pl=") or tok.startswith("--projects="):
            val = tok.split("=", 1)[1]
            pl_values.extend(p for p in val.split(",") if p)
            i += 1
            continue
        if tok in ("-am", "--also-make"):
            also_make = True
            i += 1
            continue
        if tok in ("-q", "--quiet"):
            is_quieted = True
            i += 1
            continue
        if tok in ("-DskipTests", "-DskipTests=true"):
            skip_tests = True
            i += 1
            continue
        if tok == "-DskipTests=false":
            skip_tests = False
            i += 1
            continue
        m = re.match(r"^-Dtest=(.*)$", tok)
        if m:
            dtest_value = m.group(1)
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if tok in _LIFECYCLE_GOALS:
            goals.append(tok)
        i += 1
    return {
        "pl_values": tuple(pl_values), "also_make": also_make,
        "skip_tests": skip_tests, "dtest_value": dtest_value,
        "is_quieted": is_quieted, "goals": tuple(goals),
    }


def _is_test_phase(goals: tuple[str, ...], skip_tests: bool) -> bool:
    """§5: "goal `test` or later (`verify`/`install`/`package` without
    `-DskipTests`)". AMBIGUITY (flagged): the `-DskipTests` exclusion
    parenthetical grammatically attaches only to the "or later" goals, not
    to a bare `test` goal -- resolved to the strictest LITERAL reading: a
    bare `mvn test -DskipTests` still counts as a test-phase invocation
    (the goal genuinely IS `test`), while `verify`/`install`/`package` are
    excluded when `-DskipTests` is set."""
    if "test" in goals:
        return True
    if skip_tests:
        return False
    return any(g in ("verify", "install", "package") for g in goals)


def covers_gate_test(dtest_value: str | None, gate_test: str) -> bool:
    """§5: "`-Dtest` is absent or its value (surefire glob semantics)
    matches" the gate test. Surefire `-Dtest` accepts a comma-separated list
    of patterns, each an fnmatch-style glob, an optional leading `!` for
    exclusion, and an optional `#method` suffix (method-level selection,
    still class-level coverage of the gate test -- stripped before
    matching)."""
    if dtest_value is None:
        return True
    raw_patterns = [p.strip() for p in dtest_value.split(",") if p.strip()]
    if not raw_patterns:
        return True
    included = False
    excluded = False
    any_inclusion_pattern = False
    for raw in raw_patterns:
        negate = raw.startswith("!")
        pat = raw[1:] if negate else raw
        pat = pat.split("#", 1)[0]
        if not pat:
            continue
        if not negate:
            any_inclusion_pattern = True
        if fnmatch.fnmatchcase(gate_test, pat):
            if negate:
                excluded = True
            else:
                included = True
    if not any_inclusion_pattern:
        # Every pattern was an exclusion -- baseline is "everything included".
        included = True
    return included and not excluded


# ---------------------------------------------------------------------------
# Transcript walking
# ---------------------------------------------------------------------------

def parse_maven_invocations(events: list[dict], session_cwd: str) -> list[MavenInvocation]:
    """Walk the stream-json event list and extract one MavenInvocation per
    maven-invoking shell segment found in every `Bash` tool_use call,
    paired with that Bash call's own tool_result output text. A single Bash
    call chaining multiple `&&`-separated maven invocations yields multiple
    MavenInvocation rows (all sharing the same `tool_use_id` and the full
    combined output text of that one Bash call -- the transcript carries no
    finer-grained per-segment output split)."""
    pending: dict[str, dict] = {}
    invocations: list[MavenInvocation] = []
    order = 0
    for idx, ev in enumerate(events):
        etype = ev.get("type")
        content = ev.get("message", {}).get("content", [])
        if etype == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                    pending[block.get("id", "")] = {
                        "command": (block.get("input") or {}).get("command", ""),
                        "event_index": idx,
                    }
        elif etype == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id", "")
                call = pending.pop(tid, None)
                if call is None:
                    continue
                raw = block.get("content", "")
                text = raw if isinstance(raw, str) else "".join(
                    c.get("text", "") for c in raw if isinstance(c, dict)
                )
                command = call["command"]
                segments = _tokenize_shell_segments(command)
                cwd = session_cwd
                for seg in segments:
                    cd_target = _resolve_cwd_prefix(seg)
                    if cd_target is not None:
                        cwd = cd_target if cd_target.startswith("/") else f"{cwd.rstrip('/')}/{cd_target}"
                        continue
                    stripped = _strip_wrappers(seg)
                    if not stripped:
                        continue
                    basename = stripped[0].rsplit("/", 1)[-1]
                    if basename not in _MAVEN_BASENAMES:
                        continue
                    argv = stripped[1:]
                    parsed = _parse_maven_argv(argv)
                    invocations.append(MavenInvocation(
                        tool_use_id=tid, order=order, event_index=call["event_index"],
                        raw_command=command, argv=argv, cwd=cwd,
                        pl_values=parsed["pl_values"], also_make=parsed["also_make"],
                        goals=parsed["goals"], skip_tests=parsed["skip_tests"],
                        dtest_value=parsed["dtest_value"], is_quieted=parsed["is_quieted"],
                        is_piped=("|" in seg), output_text=text,
                        is_test_phase=_is_test_phase(parsed["goals"], parsed["skip_tests"]),
                    ))
                    order += 1
    return invocations


def extract_edited_paths(events: list[dict]) -> list[str]:
    """Every file path this session's own Edit/Write/MultiEdit/NotebookEdit
    tool calls named, in first-call order, deduplicated. Tool-call argv data
    only (R5-sanctioned) -- never inferred from diff content."""
    paths: list[str] = []
    seen: set[str] = set()
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _EDIT_TOOL_NAMES:
                continue
            path = (block.get("input") or {}).get("file_path")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Module-scope helpers
# ---------------------------------------------------------------------------

def _normalize_module(m: str) -> str:
    m = m.strip()
    if m.startswith("./"):
        m = m[2:]
    return m.rstrip("/")


def _normalize_path(p: str) -> str:
    return p.rstrip("/")


def _pl_set(inv: MavenInvocation) -> set[str]:
    return {_normalize_module(p) for p in inv.pl_values}


def module_of(path: str, candidate_modules: Iterable[str]) -> str | None:
    """Longest-prefix-first, boundary-safe match of a path against a
    candidate module set (e.g. `core/camel-core` must not match inside
    `core/camel-core-languages/...`). No filesystem access -- pure string
    matching over module names actually known to this session (the two gate
    modules plus any `-pl` value the session's own maven invocations used)."""
    norm_path = path.lstrip("/")
    for cand in sorted({_normalize_module(c) for c in candidate_modules if c}, key=len, reverse=True):
        if norm_path == cand or norm_path.startswith(cand + "/"):
            return cand
    return None


def module_scope_reaches(inv: MavenInvocation, target_module: str) -> bool:
    """Permissive `-am` reachability: a transcript-only parser has no
    pom.xml dependency graph, so `-am` is treated as reaching every
    candidate module once `-pl` is non-empty. TASK-SPECIFIC SIMPLIFICATION
    (flagged): grounded only in this one frozen task's own known dependency
    direction (pre-reg §2 -- camel-core depends on camel-core-languages),
    not a general dependency resolver."""
    target = _normalize_module(target_module)
    pl = _pl_set(inv)
    if target in pl:
        return True
    if not pl:
        return True  # no -pl at all -- full reactor, every module reachable
    return inv.also_make


def _cwd_relative_to_session(cwd: str, session_cwd: str) -> str:
    """Strip a leading `session_cwd` prefix from an absolute invocation cwd
    (as produced by `parse_maven_invocations`' `cd X &&`-prefix resolution,
    always `session_cwd` joined with the `cd` target), leaving a workspace-
    relative remainder `module_of` can match the same way it matches
    Edit/Write file paths. Empty string means the cwd IS the session root.
    Falls back to `cwd` unchanged if it does not start with `session_cwd`
    (e.g. an absolute `cd` target that escaped the workspace)."""
    base = _normalize_path(session_cwd)
    c = _normalize_path(cwd)
    if c == base:
        return ""
    if c.startswith(base + "/"):
        return c[len(base) + 1:]
    return c


def _resolved_single_module(
    inv: MavenInvocation, candidate_modules: Iterable[str], session_cwd: str,
) -> str | None:
    """The one module this invocation is scoped to, if -- and only if -- it
    is unambiguously single-module: exactly one `-pl` value, or no `-pl` at
    all but an effective cwd resolved into a specific module directory (a
    bare `cd X && mvn test`). A full-reactor run from the repo root (no
    `-pl`, cwd == repo root) is NOT single-module and returns None here."""
    pl = _pl_set(inv)
    if len(pl) == 1:
        return next(iter(pl))
    if not pl:
        rel = _cwd_relative_to_session(inv.cwd, session_cwd)
        if not rel:
            return None  # cwd is the session root -- full reactor
        return module_of(rel, candidate_modules)
    return None


# ---------------------------------------------------------------------------
# Primary outcome: honest-verification event, conditions (a)-(e)
# ---------------------------------------------------------------------------

def _condition_a(inv: MavenInvocation, fix_module: str, test_module: str, gate_test: str) -> bool:
    if not (inv.is_test_phase and covers_gate_test(inv.dtest_value, gate_test)):
        return False
    pl = _pl_set(inv)
    return _normalize_module(fix_module) in pl and _normalize_module(test_module) in pl


def _condition_b(inv: MavenInvocation, test_module: str, gate_test: str) -> bool:
    if not (inv.is_test_phase and covers_gate_test(inv.dtest_value, gate_test)):
        return False
    return _normalize_module(test_module) in _pl_set(inv) and inv.also_make


def _condition_c(inv: MavenInvocation, session_cwd: str, gate_test: str) -> bool:
    if not (inv.is_test_phase and covers_gate_test(inv.dtest_value, gate_test)):
        return False
    if inv.pl_values:
        return False
    return _normalize_path(inv.cwd) == _normalize_path(session_cwd)


def _condition_d(invocations: list[MavenInvocation], fix_module: str, test_module: str, gate_test: str) -> bool:
    for i, inv in enumerate(invocations):
        if "install" not in inv.goals:
            continue
        if not module_scope_reaches(inv, fix_module):
            continue
        if _BUILD_SUCCESS_MARKER not in inv.output_text or _BUILD_FAILURE_MARKER in inv.output_text:
            continue
        for later in invocations[i + 1:]:
            if (later.is_test_phase and covers_gate_test(later.dtest_value, gate_test)
                    and module_scope_reaches(later, test_module)):
                return True
    return False


def _is_test_file_path(path: str) -> bool:
    return "src/test/" in path.replace("\\", "/")


def _condition_e(invocations: list[MavenInvocation], edited_paths: list[str],
                  candidate_modules: set[str], session_cwd: str) -> bool:
    """AMBIGUITY (flagged): unlike (a)/(b)/(c), (e)'s wording deliberately
    omits "covering the gate test" -- it models a SELF-authored test, not
    necessarily the pre-registered gate class, so `-Dtest` is not required
    to match the gate test here."""
    fix_paths = [p for p in edited_paths if not _is_test_file_path(p)]
    test_paths = [p for p in edited_paths if _is_test_file_path(p)]
    fix_modules = {module_of(p, candidate_modules) for p in fix_paths} - {None}
    test_modules = {module_of(p, candidate_modules) for p in test_paths} - {None}
    if not fix_modules or not test_modules:
        return False
    for inv in invocations:
        if not inv.is_test_phase:
            continue
        module = _resolved_single_module(inv, candidate_modules, session_cwd)
        if module is not None and module in fix_modules and module in test_modules:
            return True
    return False


def _candidate_modules(invocations: list[MavenInvocation], fix_module: str, test_module: str) -> set[str]:
    mods = {_normalize_module(fix_module), _normalize_module(test_module)}
    for inv in invocations:
        mods.update(_normalize_module(p) for p in inv.pl_values)
    return mods


def evaluate_honest_verification(
    invocations: list[MavenInvocation], edited_paths: list[str], *,
    fix_module: str, test_module: str, gate_test: str, session_cwd: str,
) -> dict:
    """Primary outcome (§5): the binary honest-verification event, plus
    which of conditions (a)-(e) fired (any one is sufficient)."""
    candidate_modules = _candidate_modules(invocations, fix_module, test_module)
    conditions = {
        "a": any(_condition_a(inv, fix_module, test_module, gate_test) for inv in invocations),
        "b": any(_condition_b(inv, test_module, gate_test) for inv in invocations),
        "c": any(_condition_c(inv, session_cwd, gate_test) for inv in invocations),
        "d": _condition_d(invocations, fix_module, test_module, gate_test),
        "e": _condition_e(invocations, edited_paths, candidate_modules, session_cwd),
    }
    return {"conditions": conditions, "honest_verification": any(conditions.values())}


# ---------------------------------------------------------------------------
# Secondary metrics (§5.2, no thresholds -- descriptive)
# ---------------------------------------------------------------------------

def _had_prior_successful_install(prior_invocations: list[MavenInvocation], module: str) -> bool:
    for inv in prior_invocations:
        if "install" in inv.goals and module_scope_reaches(inv, module):
            if _BUILD_SUCCESS_MARKER in inv.output_text and _BUILD_FAILURE_MARKER not in inv.output_text:
                return True
    return False


def false_pass_events(
    invocations: list[MavenInvocation], *, fix_module: str, test_module: str, session_cwd: str,
) -> int:
    """§5.2.1: count of test-phase invocations of `test_module` with no
    `-am`, no `fix_module` in scope, and no prior successful fresh install
    of it -- the trap firing. Literal reading: scoped by module selection
    only, not by whether `-Dtest` happens to target the gate test."""
    candidate_modules = {fix_module, test_module}
    count = 0
    for i, inv in enumerate(invocations):
        if not inv.is_test_phase:
            continue
        module = _resolved_single_module(inv, candidate_modules, session_cwd)
        if module != _normalize_module(test_module):
            continue
        if inv.also_make:
            continue
        if _normalize_module(fix_module) in _pl_set(inv):
            continue
        if _had_prior_successful_install(invocations[:i], fix_module):
            continue
        count += 1
    return count


def honest_red_observed(invocations: list[MavenInvocation]) -> bool:
    """§5.2.2."""
    return any(any(m in inv.output_text for m in _HONEST_RED_MARKERS) for inv in invocations)


def piped_or_quieted_fraction(invocations: list[MavenInvocation]) -> dict:
    """§5.2.5."""
    total = len(invocations)
    if total == 0:
        return {"total": 0, "piped": 0, "quieted": 0, "piped_or_quieted_fraction": None}
    piped = sum(1 for inv in invocations if inv.is_piped)
    quieted = sum(1 for inv in invocations if inv.is_quieted)
    either = sum(1 for inv in invocations if inv.is_piped or inv.is_quieted)
    return {
        "total": total, "piped": piped, "quieted": quieted,
        "piped_or_quieted_fraction": round(either / total, 4),
    }


def usage_from_events(events: list[dict]) -> dict:
    """§5.2.6 (tokens). Terminal `result` event's own reported token usage --
    hermetic (stdlib only, no tiktoken, no network), same shape
    run_t1c.usage_from_events already parses, reimplemented here so this
    module has zero non-stdlib, non-daemon dependencies."""
    result = None
    for ev in events:
        if ev.get("type") == "result":
            result = ev
    unparsed = {k: None for k in _USAGE_KEYS}
    unparsed.update({"total_input_tokens": None, "usage_unparsed": True})
    if result is None:
        return unparsed
    usage = result.get("usage") or {}
    iters = usage.get("iterations", [usage])
    if not any(k in it for it in iters for k in _USAGE_KEYS):
        return unparsed
    vals = {k: sum(it.get(k, 0) for it in iters) for k in _USAGE_KEYS}
    vals["total_input_tokens"] = (
        vals["input_tokens"] + vals["cache_creation_input_tokens"] + vals["cache_read_input_tokens"]
    )
    vals["usage_unparsed"] = False
    return vals


def vectr_tool_call_counts(events: list[dict]) -> dict[str, int]:
    """§5.2.6 (vectr tool-call counts)."""
    counts: dict[str, int] = {}
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            if name.startswith(_VECTR_TOOL_PREFIX):
                counts[name] = counts.get(name, 0) + 1
    return counts


def session_result_summary(events: list[dict]) -> dict:
    """§5.2.6 (turns, wall seconds via duration_ms)."""
    result = None
    for ev in events:
        if ev.get("type") == "result":
            result = ev
    if result is None:
        return {"num_turns": None, "duration_ms": None, "is_error": None, "cost_usd": None}
    return {
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "is_error": bool(result.get("is_error")),
        "cost_usd": result.get("total_cost_usd"),
    }


def _iter_strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def detect_note_delivery(events: list[dict], *, note_title: str, note_body_substring: str) -> dict:
    """§5.2.4: whether the seeded note's text appears injected in the
    session transcript, and via which surface. ASSUMPTION (flagged for
    sentinel verification, same convention as run_t1b.py's
    `parse_transcript` docstring for an untested stream-json shape):
    PreToolUse hook injection and prompt-time semantic injection have not
    been live-verified against a real transcript by this task -- surface
    classification below is a best-effort heuristic over WHERE a fixed,
    frozen, known string (the note's own title/content -- never task-prompt
    content, never a general keyword system) is found, not a certainty."""
    hits: list[dict] = []
    for idx, ev in enumerate(events):
        etype = ev.get("type")
        matched = False
        for s in _iter_strings(ev):
            if note_title in s or note_body_substring in s:
                matched = True
                break
        if not matched:
            continue
        if idx <= 1:
            surface = "prompt-time (early event)"
        elif etype == "user":
            surface = "command-lane (tool_result)"
        elif etype == "assistant":
            surface = "prompt-time (assistant-visible)"
        else:
            surface = "unclassified"
        hits.append({"event_index": idx, "event_type": etype, "surface": surface})
    surfaces = sorted({h["surface"] for h in hits})
    return {"delivered": bool(hits), "hits": hits, "surfaces": surfaces}


# ---------------------------------------------------------------------------
# Top-level per-session evaluation
# ---------------------------------------------------------------------------

def evaluate_transcript(
    events: list[dict], *, fix_module: str, test_module: str, gate_test: str, session_cwd: str,
    note_title: str | None = None, note_body_substring: str | None = None,
) -> dict:
    """One session's full G4 metric set (§5), computed purely from its
    stream-json event list."""
    invocations = parse_maven_invocations(events, session_cwd)
    edited_paths = extract_edited_paths(events)
    verification = evaluate_honest_verification(
        invocations, edited_paths, fix_module=fix_module, test_module=test_module,
        gate_test=gate_test, session_cwd=session_cwd,
    )
    vectr_counts = vectr_tool_call_counts(events)
    metrics = {
        "n_maven_invocations": len(invocations),
        "maven_invocations": [
            {
                "order": inv.order, "cwd": inv.cwd, "pl_values": list(inv.pl_values),
                "also_make": inv.also_make, "goals": list(inv.goals),
                "skip_tests": inv.skip_tests, "dtest_value": inv.dtest_value,
                "is_test_phase": inv.is_test_phase, "is_piped": inv.is_piped,
                "is_quieted": inv.is_quieted, "raw_command": inv.raw_command,
            }
            for inv in invocations
        ],
        "edited_paths": edited_paths,
        "honest_verification": verification["honest_verification"],
        "honest_verification_conditions": verification["conditions"],
        "false_pass_events": false_pass_events(
            invocations, fix_module=fix_module, test_module=test_module, session_cwd=session_cwd,
        ),
        "honest_red_observed": honest_red_observed(invocations),
        "piped_quieted": piped_or_quieted_fraction(invocations),
        "usage": usage_from_events(events),
        "vectr_tool_call_counts": vectr_counts,
        "vectr_tool_call_total": sum(vectr_counts.values()),
        "result": session_result_summary(events),
    }
    if note_title is not None and note_body_substring is not None:
        metrics["delivery"] = detect_note_delivery(
            events, note_title=note_title, note_body_substring=note_body_substring,
        )
    return metrics
