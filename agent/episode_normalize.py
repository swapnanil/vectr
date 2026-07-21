"""Pure, structural Bash-command normalization (memoization-l1-capture-design
§3.1) — turns one raw shell command string into a `(verb, flags, args,
env_delta_names)` tuple using ONLY the command's own argv structure.

R5 note: every rule here classifies the SHAPE of a tool call's own argv
(which token is a flag, which looks path/version/number/UUID-shaped) — never
the CONTENT of a prompt. No per-binary table, no keyword list: the same five
structural rules run identically over every command, whatever binary it
invokes (the sanctioned "uniform structural transform" category, not the
forbidden query-content-conditional kind).

This module has no I/O and no config dependency — a hook subprocess (via
`agent/hook_cli.py`) never imports it; only the daemon's own `/v1/episode`
write path (`app/service.py`) does, at insert time, so its own import cost
never lands on the ≤50ms hook foreground budget.

Placed here (rather than inside `app/arcs.py`) so the arc detector (a
separate lane) can import the exact same normalization the episode row
itself was written with — one implementation, not two that could drift.
"""
from __future__ import annotations

import re
import shlex

# ---------------------------------------------------------------------------
# Value-shape classes — used both to abstract positional args for comparison
# (§3.1: "concrete values stay in the row") and to decide where the verb's
# leading token-run stops. Checked in this order per token; UUID and NUM are
# exact/narrow enough to have no overlap worth resolving, VERSION is checked
# before the broader PATH catch-all so "1.2.3" classifies as VERSION rather
# than "a path with a 3-character extension".
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_VERSION_RE = re.compile(
    r"^v\d+(\.\d+){0,3}([-+][0-9A-Za-z.]+)?$"
    r"|^\d+\.\d+\.\d+([-+.][0-9A-Za-z.]*)?$"
)
_PATH_PREFIXES = ("~", "./", "../")
_PATH_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")

# Leading `VAR=value` env-prefix token, e.g. `FOO=bar make build`.
_ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _value_class(token: str) -> str | None:
    """Structural class of one argv token, or None if it's a plain bare
    word (an identifier-shaped token no class recognizes)."""
    if _UUID_RE.match(token):
        return "UUID"
    if _NUM_RE.match(token):
        return "NUM"
    if _VERSION_RE.match(token):
        return "VERSION"
    if (
        token.startswith(_PATH_PREFIXES)
        or "/" in token
        or "\\" in token
        or _PATH_EXT_RE.search(token)
    ):
        return "PATH"
    return None


def _tokenize(cmd_raw: str) -> list[str]:
    """Quote-aware tokenize; never raises — an unbalanced-quote command
    (which does happen in the wild) falls back to a plain whitespace split
    rather than blowing up the write path."""
    try:
        return shlex.split(cmd_raw, posix=True)
    except ValueError:
        return cmd_raw.split()


# Trailing semantics-neutral decoration patterns, longest-first so e.g.
# `cmd 2>&1 | tail -20` strips the pipe+count in one pass, then the bare
# `2>&1` in the next — order matters, length doesn't need to be exact since
# each pattern is tried in turn every iteration until none match.
def _strip_trailing_decoration(tokens: list[str]) -> list[str]:
    while tokens:
        if len(tokens) >= 3 and tokens[-3] == "|" and tokens[-2] in ("tail", "head"):
            tokens = tokens[:-3]
            continue
        if len(tokens) >= 2 and tokens[-2] == "|" and tokens[-1] in ("tail", "head"):
            tokens = tokens[:-2]
            continue
        if len(tokens) >= 2 and tokens[-2] == "|" and tokens[-1] == "cat":
            tokens = tokens[:-2]
            continue
        if tokens[-1] == "2>&1":
            tokens = tokens[:-1]
            continue
        break
    return tokens


def _strip_leading_cd_prefix(tokens: list[str]) -> list[str]:
    """Strip one or more leading `cd <path> &&` chain segments — semantically
    a cwd change the episode's own `cwd` field (captured separately by the
    caller) already reflects, not part of the invoked command's identity."""
    while len(tokens) >= 3 and tokens[0] == "cd" and tokens[2] == "&&":
        tokens = tokens[3:]
    return tokens


def _strip_leading_env_prefix(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Peel leading `VAR=value` tokens off the front; returns (remaining
    tokens, env var NAMES only — never values, matching the episode schema's
    `env_delta_names` contract)."""
    names: list[str] = []
    i = 0
    while i < len(tokens) and _ENV_PREFIX_RE.match(tokens[i]):
        names.append(tokens[i].split("=", 1)[0])
        i += 1
    return tokens[i:], names


def normalize_command(cmd_raw: str) -> dict:
    """Normalize one raw Bash command into the §3.1 triple plus env delta.

    Returns:
        {
            "verb": str,                              # e.g. "git commit", "npm run build"
            "flags": list[str],                        # order-normalized, "-m=fix bug" shape
            "args": list[{"value": str, "class": str | None}],
            "env_delta_names": list[str],
        }

    Verb = the leading run of tokens, starting from argv[0] (always included
    — it IS the binary, never a "value" to abstract away, even when it looks
    path-shaped like "./mvnw"), that are neither flags (`-`-prefixed) nor
    value-shaped (NUM/VERSION/PATH/UUID). This reproduces every example in
    the spec (`git commit`, `./mvnw test`, `npm run build`) without any
    binary-specific rule — the tradeoff is that a command with several bare
    positional words before the first flag (e.g. `git push origin main`)
    absorbs all of them into `verb`, since there is no purely-structural way
    to tell "subcommand continuation" from "positional argument" once both
    are equally bare, unadorned words. That is an accepted granularity
    limitation of a uniform rule, not a bug to special-case around.
    """
    if not cmd_raw or not cmd_raw.strip():
        return {"verb": "", "flags": [], "args": [], "env_delta_names": []}

    tokens = _tokenize(cmd_raw)
    tokens = _strip_trailing_decoration(tokens)
    tokens = _strip_leading_cd_prefix(tokens)
    tokens, env_delta_names = _strip_leading_env_prefix(tokens)

    if not tokens:
        return {"verb": "", "flags": [], "args": [], "env_delta_names": env_delta_names}

    verb_tokens = [tokens[0]]
    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.startswith("-") or _value_class(tok) is not None:
            break
        verb_tokens.append(tok)
        idx += 1
    verb = " ".join(verb_tokens)

    flags: list[str] = []
    args: list[dict] = []
    remaining = tokens[idx:]
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        if tok.startswith("-"):
            if "=" in tok:
                flags.append(tok)  # already flag=value shaped
                i += 1
            elif i + 1 < len(remaining) and not remaining[i + 1].startswith("-"):
                flags.append(f"{tok}={remaining[i + 1]}")
                i += 2
            else:
                flags.append(tok)  # boolean flag, no value
                i += 1
        else:
            args.append({"value": tok, "class": _value_class(tok)})
            i += 1

    return {
        "verb": verb,
        "flags": sorted(flags),
        "args": args,
        "env_delta_names": env_delta_names,
    }
