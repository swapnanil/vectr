"""Deterministic, structural command-line normalization for arc detection.

Pure functions only — no I/O, no state, no imports from `app.routes` /
`app.service`. Shared by `app/arcs.py` (in-memory similarity matching) and
the episode write path (persisted `verb`/`flags_json`/`args_json` columns)
so both derive the exact same normalized triple from the same raw command
string — nothing to reconcile between the two call sites.

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
    ARC_NORM_NICE_NICENESS_FLAG,
    ARC_NORM_NUM_REGEX,
    ARC_NORM_PATH_EXTENSION_REGEX,
    ARC_NORM_PIPELINE_DISPLAY_ONLY_VERBS,
    ARC_NORM_STDERR_MERGE_TOKEN,
    ARC_NORM_UUID_REGEX,
    ARC_NORM_VERSION_REGEX,
    ARC_NORM_WRAPPER_PREFIXES,
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
    unclassified) used for comparison only.
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
    """Strip leading `cd <path> &&` segments (semantics-neutral decoration)
    — repeatedly, so `cd a && cd b && real-cmd` reduces to `real-cmd`."""
    while segments and segments[0][0] == "cd" and len(segments[0]) <= 2:
        segments = segments[1:]
    return segments


def _is_display_only_stage(stage: list[str]) -> bool:
    return bool(stage) and stage[0] in ARC_NORM_PIPELINE_DISPLAY_ONLY_VERBS


def _strip_trailing_display_stages(pipeline_stages: list[list[str]]) -> list[list[str]]:
    """Drop a TRAILING run of display-only stages (`| tail -30`, `| cat`) —
    these only reshape output for a human, never change what actually ran.
    A stage is only ever eligible while it is the last remaining one, so a
    genuine multi-stage pipeline (`cat data.csv | python train.py`) keeps
    every non-trailing stage's tokens (unconditionally collapsing to stage
    0 made distinct pipelines normalize identical)."""
    while len(pipeline_stages) > 1 and _is_display_only_stage(pipeline_stages[-1]):
        pipeline_stages = pipeline_stages[:-1]
    return pipeline_stages


def _strip_env_and_wrapper_prefixes(stage: list[str]) -> tuple[list[str], list[str]]:
    """Strip, iteratively and in any interleaving, leading bare env-var
    assignments (`FOO=bar cmd`) and transparent wrapper-prefix tokens
    (`timeout N`, `env VAR=...`, `nice [-n N]`, `nohup`, `stdbuf -xX`) from
    the front of a pipeline stage so the WRAPPED command, not the wrapper,
    is what verb extraction sees.
    Returns (remaining_tokens, env_assignment_names)."""
    tokens = list(stage)
    env_names: list[str] = []
    while tokens:
        head = tokens[0]
        if _ENV_ASSIGNMENT_RE.match(head):
            env_names.append(head.split("=", 1)[0])
            tokens = tokens[1:]
            continue
        kind = ARC_NORM_WRAPPER_PREFIXES.get(head)
        if kind is None:
            break
        tokens = tokens[1:]
        if kind == "fixed_arg":
            tokens = tokens[1:]
        elif kind == "nice_niceness":
            if tokens and tokens[0] == ARC_NORM_NICE_NICENESS_FLAG:
                tokens = tokens[1:]
                if tokens:
                    tokens = tokens[1:]
        elif kind == "dash_flags":
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
        # "bare" and "env_assignments" wrappers consume only their own
        # name token — nothing further to drop here.
    return tokens, env_names


def normalize_command(cmd_raw: str) -> NormalizedCommand:
    """Normalize one raw Bash command string into (verb, flags, args)."""
    tokens = tokenize(cmd_raw or "")
    segments = _strip_leading_cd(_split_on_any(tokens, _COMPOUND_SEPARATORS))
    # Multiple genuine (non-cd) segments left over from a compound command
    # (e.g. `make clean && make build`) — the LAST one is what the episode's
    # own rc/outcome actually reflects, so it defines the normalized command.
    primary_segment = segments[-1] if segments else []

    pipeline_stages = _strip_trailing_display_stages(
        _split_on_any(primary_segment, frozenset({"|"}))
    )
    primary_stage = pipeline_stages[0] if pipeline_stages else []
    downstream_stages = pipeline_stages[1:]
    if primary_stage and primary_stage[-1] == ARC_NORM_STDERR_MERGE_TOKEN:
        primary_stage = primary_stage[:-1]

    primary_stage, env_names = _strip_env_and_wrapper_prefixes(primary_stage)

    if not primary_stage and not downstream_stages:
        return NormalizedCommand(
            verb="",
            flags=(),
            args=(),
            arg_classes=(),
            env_prefix_names=tuple(env_names),
            cmd_raw=cmd_raw or "",
        )

    verb = ""
    j = 0
    if primary_stage:
        # verb = binary + immediate subcommand chain (`git commit`, `./mvnw
        # test`, `npm run build`): keep absorbing leading bareword tokens
        # (not flag-shaped, not arg-classified) up to the configured cap,
        # which bounds runaway absorption of positional arguments (e.g.
        # `cp src dest`) into the verb.
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

    # Non-trailing downstream pipeline stages (`| python train.py` in `cat
    # data.csv | python train.py`) must stay in the comparison set so
    # distinct pipelines never normalize identical — every one of their
    # tokens is folded into flags/args exactly like the primary stage's
    # own remainder, preserving order.
    for stage in downstream_stages:
        for tok in stage:
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
