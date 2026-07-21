"""Deterministic, structural command-line normalization (L1 capture design
doc §3.1: `memoization-l1-capture-design.md`).

Pure functions only — no I/O, no state, no imports from `app.routes` /
`app.service`. Shared by `app/arcs.py` (in-memory similarity matching, this
lane) and the episode write path (persisted `verb`/`flags_json`/`args_json`
columns, a different lane) so both derive the exact same normalized triple
from the same raw command string — the merge between the two lanes is then
just "both call this module", nothing to reconcile.

R5 scope note: everything classified here is the **argv structure of an
already-issued tool call** (which token is a flag, a path, a version
string, semantics-neutral shell decoration) — tool-call structure, not
prompt content. Sanctioned. This module never reads user/task-prompt text.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from agent.config import (
    ARC_NORM_ENV_ASSIGNMENT_REGEX,
    ARC_NORM_MAX_VERB_TOKENS,
    ARC_NORM_NUM_REGEX,
    ARC_NORM_PATH_EXTENSION_REGEX,
    ARC_NORM_STDERR_MERGE_TOKEN,
    ARC_NORM_UUID_REGEX,
    ARC_NORM_VERSION_REGEX,
)

_UUID_RE = re.compile(ARC_NORM_UUID_REGEX)
_VERSION_RE = re.compile(ARC_NORM_VERSION_REGEX)
_NUM_RE = re.compile(ARC_NORM_NUM_REGEX)
_PATH_EXT_RE = re.compile(ARC_NORM_PATH_EXTENSION_REGEX)
_ENV_ASSIGNMENT_RE = re.compile(ARC_NORM_ENV_ASSIGNMENT_REGEX)

# Compound-command boundaries (top-level, unquoted only — shlex has already
# stripped quoting by the time these are compared, so a literal token equal
# to one of these is never something the user quoted, e.g. grep "a|b" stays
# one shlex token and never matches here).
_COMPOUND_SEPARATORS = frozenset({"&&", "||", ";"})


@dataclass(frozen=True)
class NormalizedCommand:
    """The normalized (verb, flags, args) triple for one Bash invocation.

    `args` holds the RAW positional-argument tokens (concrete values, order
    preserved) alongside `arg_classes` — the same-length abstraction of each
    (`<PATH>`/`<VERSION>`/`<UUID>`/`<NUM>`, or the literal token when
    unclassified) used for comparison only (§3.1/§3.2 of the design doc).
    """

    verb: str
    flags: tuple[str, ...]
    args: tuple[str, ...]
    arg_classes: tuple[str, ...]
    env_prefix_names: tuple[str, ...] = field(default_factory=tuple)
    cmd_raw: str = ""


def tokenize(cmd_raw: str) -> list[str]:
    """Quote-aware tokenize. Falls back to a naive whitespace split on
    unbalanced quotes rather than raising — a malformed command is still a
    real episode we must not crash on."""
    if not cmd_raw:
        return []
    try:
        return shlex.split(cmd_raw, posix=True)
    except ValueError:
        return cmd_raw.split()


def classify_arg(token: str) -> str:
    """Return the abstraction class placeholder for a positional-argument
    token, or the token itself verbatim when none applies. Order: uuid >
    version > unambiguous-path (slash / "." / ".." / "~" prefix) > num >
    extension-shaped-path > literal.

    The extension-shaped path check (`name.ext`) is deliberately tried
    AFTER num: it is a loose shape that a bare decimal number like "3.14"
    also satisfies, so num (a strictly digits-and-one-dot pattern) must
    get first refusal on anything that looks purely numeric. The
    unambiguous path indicators checked earlier never collide with a
    number, so they keep precedence over num."""
    if _UUID_RE.match(token):
        return "<UUID>"
    if _VERSION_RE.match(token):
        return "<VERSION>"
    if "/" in token or token in (".", "..") or token.startswith("~"):
        return "<PATH>"
    if _NUM_RE.match(token):
        return "<NUM>"
    if _PATH_EXT_RE.match(token):
        return "<PATH>"
    return token


def _split_on_any(tokens: list[str], seps: frozenset[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in seps:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return [s for s in segments if s]


def _strip_leading_cd(segments: list[list[str]]) -> list[list[str]]:
    """Strip leading `cd <path> &&` segments (semantics-neutral decoration,
    §3.1) — repeatedly, so `cd a && cd b && real-cmd` reduces to `real-cmd`."""
    while segments and segments[0][0] == "cd" and len(segments[0]) <= 2:
        segments = segments[1:]
    return segments


def normalize_command(cmd_raw: str) -> NormalizedCommand:
    """Normalize one raw Bash command string into (verb, flags, args)."""
    tokens = tokenize(cmd_raw or "")
    segments = _strip_leading_cd(_split_on_any(tokens, _COMPOUND_SEPARATORS))
    # Multiple genuine (non-cd) segments left over from a compound command
    # (e.g. `make clean && make build`) — the LAST one is what the episode's
    # own rc/outcome actually reflects, so it defines the normalized command.
    primary_segment = segments[-1] if segments else []

    pipeline_stages = _split_on_any(primary_segment, frozenset({"|"}))
    # `2>&1 | cat` / `| tail -N` / `| head -N` (§3.1): the first pipeline
    # stage always defines the invocation regardless of what output-shaping
    # stages follow it.
    primary_stage = pipeline_stages[0] if pipeline_stages else []
    if primary_stage and primary_stage[-1] == ARC_NORM_STDERR_MERGE_TOKEN:
        primary_stage = primary_stage[:-1]

    env_names: list[str] = []
    i = 0
    while i < len(primary_stage) and _ENV_ASSIGNMENT_RE.match(primary_stage[i]):
        env_names.append(primary_stage[i].split("=", 1)[0])
        i += 1
    primary_stage = primary_stage[i:]

    if not primary_stage:
        return NormalizedCommand(
            verb="",
            flags=(),
            args=(),
            arg_classes=(),
            env_prefix_names=tuple(env_names),
            cmd_raw=cmd_raw or "",
        )

    # verb = binary + immediate subcommand chain (`git commit`, `./mvnw
    # test`, `npm run build`): keep absorbing leading bareword tokens (not
    # flag-shaped, not arg-classified) up to the configured cap, which
    # bounds runaway absorption of positional arguments (e.g. `cp src
    # dest`) into the verb.
    verb_tokens = [primary_stage[0]]
    j = 1
    while j < len(primary_stage) and len(verb_tokens) < ARC_NORM_MAX_VERB_TOKENS:
        tok = primary_stage[j]
        if tok.startswith("-") or classify_arg(tok) != tok:
            break
        verb_tokens.append(tok)
        j += 1
    verb = " ".join(verb_tokens)

    flags: list[str] = []
    args: list[str] = []
    arg_classes: list[str] = []
    for tok in primary_stage[j:]:
        if tok.startswith("-"):
            flags.append(tok)
        else:
            args.append(tok)
            arg_classes.append(classify_arg(tok))

    return NormalizedCommand(
        verb=verb,
        flags=tuple(sorted(flags)),
        args=tuple(args),
        arg_classes=tuple(arg_classes),
        env_prefix_names=tuple(env_names),
        cmd_raw=cmd_raw or "",
    )
