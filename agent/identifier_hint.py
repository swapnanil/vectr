"""
Identifier-shape tokenizer for the vectr_search additive symbol-graph hint
(UPG-QUERYTYPE-REROUTE).

This replaces the deleted `agent/query_router.py` regex query-classification
layer. That layer classified a query's INTENT via keyword/phrase lists
(`\\boverride\\b`, `\\bsubclass(es)?\\b`, `\\b(dependenc(y|ies)|downstream|
upstream)\\b`, ...) and, on a match, silently overrode the fingerprint-derived
semantic weight and guessed a symbol to look up from the query's first
non-stopword word — an innocent NL question about "dependencies" or
"overriding a method" misrouted into a same-named-homonym symbol lookup with
no ranking tie to the query at all.

The replacement here does no intent classification whatsoever. It detects
token SHAPE, not meaning: CamelCase, snake_case, and dotted/qualified
(`Class.method`) forms are structural properties of an identifier regardless
of what the surrounding sentence is about. A plain word is never treated as
an identifier candidate even if it happens to also be a symbol name — the
`identifier_hint_symbols()` caller (see `app/service.py`) additionally
requires an EXACT symbol-graph match before anything is surfaced, so a
shape-detected token that doesn't resolve produces no output at all.
"""
from __future__ import annotations

import re

# Combined, single regex — shape detection only, no word/keyword/phrase list:
#   1. dotted/qualified form   e.g. "QuerySet.delete"
#   2. CamelCase/PascalCase    e.g. "WorkspaceLock" (leading capital, an
#                              internal lower-then-upper transition — this
#                              excludes an ordinary capitalised sentence word
#                              like "Where", which has no internal transition)
#   3. snake_case              e.g. "acquire_lock" (contains an underscore)
# Order matters: the dotted alternative is tried first so "Class.method" is
# captured as one token rather than splitting at the dot.
_IDENTIFIER_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*"
    r"|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*"
    r"|[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]*"
)


def extract_identifier_tokens(query: str) -> list[str]:
    """Extract identifier-SHAPED tokens from a raw query string.

    Returns candidates in order of first appearance, deduplicated. Empty for
    a query built entirely of plain words (no CamelCase/snake_case/dotted
    signal) — the common case for ordinary natural-language questions.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for m in _IDENTIFIER_TOKEN_RE.finditer(query):
        tok = m.group(0)
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return tokens
