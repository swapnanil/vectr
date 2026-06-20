"""Language-agnostic chunk-quality heuristics (Opus audit Wave 1).

The four audited corpora (Python-multi, C, Zig, Rust) all showed the same
failure: low-information chunks outranking real code — bare ``}`` / ``return 0;``
(C), ``const log = std.log;`` (Zig), ``pub use …`` re-export blocks (Rust), and
heading-only markdown. This module centralises the predicates used to:

  * drop / merge trivial chunks at index time            (UPG-1.1)
  * tag re-export / import-only "navigational" chunks    (UPG-1.2)
  * skip vectr-generated config + machine-generated files (UPG-1.3)
  * fold a quality prior into search ranking             (UPG-2.1)
  * de-prioritise test files vs implementation           (UPG-2.3)

Everything here is pure (no I/O) and cheap so it can run per-chunk at both index
and query time.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

# A synthetic node_type stamped on re-export / import-only chunks so the ranker
# can recognise them without re-parsing.
NAVIGATIONAL_NODE_TYPE = "navigational"


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

# Prefixes the chunker prepends (leading comments, "# class: X" context). These
# are not the chunk's substance, so they're stripped before judging triviality.
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/", "<!--", "-->")

# A line that carries no semantic payload on its own.
_TRIVIAL_LINE_RE = re.compile(
    r"""^[\s]*(
        [{}()\[\];,]+                       # pure punctuation: } ; ){ etc.
        | \#\s*(endif|else|elif|pragma\b.*) # C preprocessor noise
        | return(\s+(0|NULL|nullptr|None|true|false|nil|-?\d+))?\s*;?  # bare returns
        | (pass|break|continue|\.\.\.);?    # python/keyword stubs
        | (else|do|try)\s*[:{]?             # lone block openers
    )[\s]*$""",
    re.VERBOSE,
)

# A single-line variable/const declaration. On its own (Zig `variable_declaration`
# nodes, lone module constants) these carry almost no retrieval signal — the audit
# flagged `const log = std.log;`, `const seed = 42;`, `const fs = std.fs;`.
_DECL_LINE_RE = re.compile(r"^[\s]*(pub\s+)?(const|let|var|val|static|final)\s+\w+\s*[:=]")

# A single import / re-export / alias line (navigational, not implementation).
_IMPORT_LINE_RE = re.compile(
    r"""^[\s]*(
        (pub\s+)?use\s                      # rust use / pub use
        | import\s | from\s.+\simport\s     # python / js import
        | export\s+\*                       # js barrel re-export
        | export\s+\{                       # js named re-export
        | (export\s+)?\{?\s*[\w,\s]+\}?\s*from\s  # ts re-export
        | \#include\b                       # C include
        | using\s                           # C++/C# using
        | (const|let|var)\s+\w+\s*=\s*(require\(|@import\() # js require / zig @import alias
        | const\s+\w+\s*=\s*@import\(       # zig @import alias
        | package\s | module\s              # go/zig package decls
    )""",
    re.VERBOSE,
)

_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")


def _meaningful_lines(content: str) -> list[str]:
    """Lines that are neither blank nor comment/context-prefix lines."""
    out: list[str] = []
    for raw in content.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(_COMMENT_PREFIXES):
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Chunk predicates
# ---------------------------------------------------------------------------

def is_trivial_chunk(content: str, language: str = "") -> bool:
    """True if the chunk has no standalone retrieval value (UPG-1.1).

    A chunk is trivial when, after dropping comments/context lines, it is empty
    or its only meaningful line is pure punctuation, a bare return, a lone block
    keyword, or a single import. These never answer a query on their own.
    """
    raw_nonblank = [l for l in content.splitlines() if l.strip()]
    if not raw_nonblank:
        return True
    lines = _meaningful_lines(content)
    if not lines:
        # Only comments/headings, no code/text payload. Trivial only when tiny —
        # a large comment/doc block (e.g. a window over an unparsed file) is real
        # coverage and must be kept.
        return len(raw_nonblank) <= 2
    if len(lines) == 1:
        line = lines[0]
        if _TRIVIAL_LINE_RE.match(line) or _IMPORT_LINE_RE.match(line) or _DECL_LINE_RE.match(line):
            return True
        # A single very short token line (e.g. `bad_extension = true;`) with no
        # structure is also low value.
        if len(line) <= 3:
            return True
    return False


def is_navigational_chunk(content: str, language: str = "") -> bool:
    """True if every meaningful line is an import / re-export / alias (UPG-1.2).

    These are tables of contents (Rust ``lib.rs`` re-export blocks, Python
    ``__init__`` import aggregators, JS/TS barrels) — they lexically match many
    queries but contain no implementation.
    """
    lines = _meaningful_lines(content)
    if len(lines) < 2:
        # single-line imports are handled by is_trivial_chunk; require a block
        return False
    nav = 0
    for line in lines:
        if _IMPORT_LINE_RE.match(line) or _TRIVIAL_LINE_RE.match(line):
            nav += 1
        else:
            return False
    return nav == len(lines)


def is_markdown_heading_only(content: str) -> bool:
    """True if a markdown chunk is essentially just heading(s) + a scrap of body.

    ``_meaningful_lines`` already strips ``#``-prefixed lines (markdown headings
    read as comments), so what's left is the prose body. A chunk with no body, or
    a heading plus only a tiny fragment of text (e.g. ``### vectr_evict_hint`` /
    ``No distinct content again.``), carries no real retrieval signal (UPG-2.1).
    A genuine one-sentence section (≥40 chars of prose) is real content and kept.
    """
    body = _meaningful_lines(content)
    if not body:
        return True
    return len(body) <= 2 and len(" ".join(body)) < 40


# ---------------------------------------------------------------------------
# File predicates
# ---------------------------------------------------------------------------

# vectr's own injected IDE-config files — noise in a *code* search index.
_VECTR_CONFIG_BASENAMES = {
    "claude.md", "agents.md", "gemini.md", "codex.md",
    ".cursorrules", ".mcp.json", "copilot-instructions.md",
}
_VECTR_CONFIG_DIRS = {".cursor", ".vscode"}

# Machine-generated / vendored file patterns (not hand-authored code).
_GENERATED_NAME_RES = [
    re.compile(r".*_db\.h$"),            # cpython unicodetype_db.h
    re.compile(r".*_metadata\.h$"),      # cpython pycore_uop_metadata.h
    re.compile(r".*\.pb\.(go|cc|h|py)$"),  # protobuf
    re.compile(r".*_pb2\.pyi?$"),        # python protobuf
    re.compile(r".*\.min\.(js|css)$"),   # minified
    re.compile(r".*\.generated\..*$"),
    re.compile(r".*\.g\.dart$"),
]
_GENERATED_DIR_PARTS = {"clinic", "generated", "__generated__", "gen", "node_modules"}


def is_vectr_config_file(file_path: str) -> bool:
    """True for vectr-injected IDE-config files that should never be indexed (UPG-1.3)."""
    p = PurePosixPath(file_path.replace("\\", "/"))
    if p.name.lower() in _VECTR_CONFIG_BASENAMES:
        return True
    return any(part in _VECTR_CONFIG_DIRS for part in p.parts)


def is_generated_file(file_path: str) -> bool:
    """True for machine-generated / vendored files (UPG-1.3)."""
    p = PurePosixPath(file_path.replace("\\", "/"))
    name = p.name.lower()
    if any(rx.match(name) for rx in _GENERATED_NAME_RES):
        return True
    return any(part.lower() in _GENERATED_DIR_PARTS for part in p.parts)


def is_test_file(file_path: str) -> bool:
    """True for test files, which should not outrank implementation (UPG-2.3)."""
    p = PurePosixPath(file_path.replace("\\", "/"))
    name = p.name.lower()
    if name.startswith("test_") or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts", ".spec.js")):
        return True
    if re.match(r"test.*\.(java|kt)$", name):
        return True
    parts = {part.lower() for part in p.parts[:-1]}
    return bool(parts & {"test", "tests", "__tests__", "spec", "testing"})


def query_wants_tests(query: str) -> bool:
    """True if the query explicitly asks about tests/fixtures/scenarios (UPG-2.3)."""
    q = query.lower()
    return any(kw in q for kw in ("test", "fixture", "scenario", "spec ", "unittest", "pytest", "assertion"))


# ---------------------------------------------------------------------------
# Quality prior (UPG-2.1)
# ---------------------------------------------------------------------------

# Documentation languages — prose, not implementation. On code-shaped queries the
# audit found substantive doc prose (blog sections, README walkthroughs, marketing
# HTML) burying real code, because the embedding model scores natural-language prose
# highly against natural-language queries. A mild demotion lets near-tied code edge
# ahead while leaving docs on top when nothing else competes (UPG-2.1).
_DOC_LANGUAGES = {"markdown", "md", "html", "htm", "rst", "text", "txt", "mdx"}


def is_doc_language(language: str) -> bool:
    """True for documentation/prose languages (vs. implementation code)."""
    return (language or "").lower() in _DOC_LANGUAGES


# Multipliers applied to the hybrid similarity score. 1.0 = neutral.
_Q_TRIVIAL = 0.15
_Q_NAVIGATIONAL = 0.35
_Q_HEADING_ONLY = 0.40
_Q_GENERATED = 0.45
_Q_VECTR_CONFIG = 0.10
_Q_TEST_DEPRIORITISED = 0.55
_Q_DOC_PROSE = 0.70       # substantive documentation prose vs implementation code
_Q_SHORT_PENALTY = 0.80   # bodies with very few meaningful lines


def quality_score(
    content: str,
    file_path: str = "",
    language: str = "",
    node_type: str = "",
    *,
    query_targets_tests: bool = False,
) -> float:
    """A per-chunk usefulness prior in (0, 1], folded into ranking as a multiplier.

    Relevance × usefulness: similarity already models relevance; this models
    "is this chunk a good answer at all, regardless of similarity". Cheap and
    language-agnostic. Lower = worse answer.
    """
    if file_path and is_vectr_config_file(file_path):
        return _Q_VECTR_CONFIG
    if node_type == NAVIGATIONAL_NODE_TYPE or is_navigational_chunk(content, language):
        return _Q_NAVIGATIONAL
    if is_trivial_chunk(content, language):
        return _Q_TRIVIAL
    if language == "markdown" and is_markdown_heading_only(content):
        return _Q_HEADING_ONLY

    score = 1.0
    if file_path and is_generated_file(file_path):
        score *= _Q_GENERATED
    if file_path and is_test_file(file_path) and not query_targets_tests:
        score *= _Q_TEST_DEPRIORITISED
    if is_doc_language(language):
        score *= _Q_DOC_PROSE

    n_lines = len(_meaningful_lines(content))
    if n_lines <= 2:
        score *= _Q_SHORT_PENALTY
    return score


# ---------------------------------------------------------------------------
# Symbol identity boost (UPG-11.1)
# ---------------------------------------------------------------------------

# Multipliers for the additive symbol-name bonus folded into rank scoring.
# These are additive boosts on top of base × quality, so they are intentionally
# small — large enough to flip a tie (or a near-tie where quality is equal)
# without overriding a genuinely more-relevant candidate.
_SYM_QUALIFIED_BOOST = 0.20   # query names BOTH the class and the leaf method
_SYM_LEAF_BOOST = 0.10        # query names only the leaf method / symbol token

# Common English words that coincidentally match short method names (UPG-11.5 / F6).
# A bare single-word leaf in this set must NOT receive a boost just because the word
# appears casually in a query like "list all migrations" (leaf "all" → +0.10 is wrong).
_SYM_STOP_WORDS: frozenset[str] = frozenset({
    "all", "any", "get", "set", "run", "add", "new", "old", "put", "pop",
    "top", "end", "key", "map", "use", "log", "out", "try", "do", "for",
    "in", "on", "of", "to", "by", "is", "as", "at", "it", "or", "not",
    "has", "can", "may", "let", "via", "per", "fit", "hit", "cut", "bit",
    "sum", "min", "max", "raw", "tag", "ref", "val", "row", "col", "idx",
    "len", "num", "str", "int", "id", "ok", "no", "up", "go", "db",
})

# Minimum character length for a bare single-word leaf to receive a boost.
# Two- and three-letter leaves (except specific compounds) are too likely to be
# common English; four characters is a reasonable floor for specificity.
_SYM_MIN_LEAF_LEN = 4

# Regex to detect a CLASS-prefix line injected by the indexer (UPG-F4).
# The indexer prepends "# class: ClassName\n" to method chunks so the embedding
# has class context.  We extract this at query time to reconstruct the qualified
# name when symbol_name is a bare leaf.
_CLASS_PREFIX_RE = re.compile(r"^#\s*class:\s*(\w+)", re.MULTILINE)


def _query_symbol_tokens(query: str) -> set[str]:
    """Extract identifier-like tokens from the query (lowercased).

    Splits on non-alphanumeric boundaries AND camelCase so that a query like
    "Field deconstruct" yields {"field", "deconstruct"} and a query like
    "from_db_value convert ..." yields {"from", "db", "value", "from_db_value"}.
    The full snake_case identifier is also kept so exact leaf matches work even
    when the name contains underscores.
    """
    # Keep whole snake_case tokens as-is (for leaf matching)
    raw_tokens = re.split(r"[^a-zA-Z0-9_]+", query)
    tokens: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 2:
            continue
        tokens.add(tok.lower())
        # Also split on underscores and camelCase sub-words
        sub = re.split(r"_+", tok)
        for part in sub:
            if len(part) >= 2:
                tokens.add(part.lower())
        # camelCase split
        expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", tok)
        for part in expanded.split():
            if len(part) >= 2:
                tokens.add(part.lower())
    return tokens


def extract_class_from_content(content: str) -> str:
    """Extract the class name from an indexer-injected '# class: X' prefix line.

    The indexer prepends ``# class: ClassName`` to method chunks so they are
    self-contained for the embedder (indexer.py _collect_chunks_ast).  This
    function recovers that class name at query time so we can reconstruct the
    qualified ``ClassName.leaf`` form when ``symbol_name`` was stored as a bare
    leaf (UPG-F4).

    Returns the class name string, or ``""`` if no prefix is found.
    """
    m = _CLASS_PREFIX_RE.search(content)
    return m.group(1) if m else ""


def symbol_identity_boost(symbol_name: str, query_tokens: set[str]) -> float:
    """Return an additive ranking boost when the symbol name matches the query intent.

    A *qualified match* (both class and leaf parts appear in the query tokens)
    earns a higher boost than a *leaf-only match* (only the final component
    appears).  No match → zero boost.  This is a general signal — it rewards
    whichever candidate's symbol name best aligns with the query vocabulary,
    without hard-coding any language or codebase specifics.

    **Matching strategy** (to avoid rewarding accidental sub-token overlap):

    1. *Exact leaf match*: the full leaf identifier (e.g. ``from_db_value``,
       ``deconstruct``) appears verbatim in ``query_tokens``.  This is the
       primary signal — the user explicitly named the method.
    2. *Sub-word leaf match*: when the leaf is a single word (no underscores /
       camelCase parts), every meaningful sub-token of it must appear together.
       We do NOT boost on partial sub-word overlaps for compound names because
       common sub-words (``value``, ``db``, ``get``) pollute too many symbols.
    3. *Qualified boost*: if a leaf match is found AND at least one prefix part
       also appears in the query tokens (exact or as a single sub-word), the
       boost is upgraded from ``_SYM_LEAF_BOOST`` to ``_SYM_QUALIFIED_BOOST``.
    4. *Stop-word guard* (F6): a bare single-word leaf that is a common English
       word (in ``_SYM_STOP_WORDS``) or is very short (< ``_SYM_MIN_LEAF_LEN``
       chars) receives NO boost, even if it appears in the query, because such
       words appear casually in prose and the overlap is accidental.

    Args:
        symbol_name: The chunk's symbol name.  May be a qualified name already
                     (e.g. ``"Field.deconstruct"``, ``"RemoveField.deconstruct"``)
                     or a bare leaf stored by the indexer (e.g. ``"deconstruct"``).
                     If the caller has already reconstructed a qualified form
                     (via ``extract_class_from_content``), pass that in.
        query_tokens: The lowercased identifier tokens extracted from the query
                      via ``_query_symbol_tokens``.
    """
    if not symbol_name or not query_tokens:
        return 0.0

    # Split on dots / :: (Python, C++, Ruby qualified names).
    # Preserve original casing for camelCase splitting; lowercase at use sites.
    parts = re.split(r"[.:]+", symbol_name)
    # Leaf = last component (original case for camelCase split); prefix = rest.
    leaf_orig = parts[-1]
    leaf = leaf_orig.lower()
    prefix_orig_parts = parts[:-1]

    # --- Stop-word / length guard (F6) ---
    # Only applied when there is NO class prefix in the symbol_name — a qualified
    # name like "Field.all" is specific enough that the class context disambiguates.
    if not prefix_orig_parts:
        if leaf in _SYM_STOP_WORDS:
            return 0.0
        if len(leaf) < _SYM_MIN_LEAF_LEN:
            return 0.0

    # --- Leaf match ---
    # Primary: exact full identifier (lowercased) is in query tokens.
    exact_leaf_hit = leaf in query_tokens

    if not exact_leaf_hit:
        # Fallback for simple single-word leaves: all camelCase / snake sub-words
        # present in query.  This handles queries like "deconstruct" where the leaf
        # is a plain word (no compound parts) and also appears as-is in tokens.
        # For compound names like get_db_prep_value we require the full identifier,
        # NOT just any sub-word overlap (too noisy).
        leaf_sub: set[str] = set()
        leaf_sub.update(t.lower() for t in re.split(r"_+", leaf_orig) if len(t) >= 2)
        expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", leaf_orig)
        leaf_sub.update(t.lower() for t in expanded.split() if len(t) >= 2)
        # Only trust sub-word split when the leaf itself has no compound parts
        # (i.e. single-word identifiers).  For compound leaves require exact.
        is_compound = "_" in leaf or (leaf_orig != leaf_orig.lower() and "_" not in leaf_orig)
        if is_compound:
            # compound identifier — require exact match only
            return 0.0
        # simple single-word leaf: check if it appears as a whole word in query
        if not leaf_sub.issubset(query_tokens):
            return 0.0

    # --- Qualified match: does any prefix part also appear in the query? ---
    prefix_hit = False
    for p in prefix_orig_parts:
        p_lower = p.lower()
        if p_lower in query_tokens:
            prefix_hit = True
            break
        # Single-word camelCase class names: split "RemoveField" → "remove", "field"
        expanded_p = re.sub(r"([a-z])([A-Z])", r"\1 \2", p)
        sub_p = {t.lower() for t in expanded_p.split() if len(t) >= 2}
        # Only count as a prefix hit when the EXACT class name or its
        # sub-words appear in the query — but avoid false positives from
        # coincidental shared sub-words (e.g. "field" in a long prose query).
        # Require that the whole class identifier appears, OR that ALL its
        # sub-words appear together.
        if p_lower in query_tokens or sub_p.issubset(query_tokens):
            prefix_hit = True
            break

    if prefix_orig_parts and prefix_hit:
        return _SYM_QUALIFIED_BOOST

    return _SYM_LEAF_BOOST


# ---------------------------------------------------------------------------
# Dedup (UPG-2.2)
# ---------------------------------------------------------------------------

def normalized_content(content: str) -> str:
    """Whitespace-collapsed lowercase form for exact/near-duplicate detection."""
    return re.sub(r"\s+", " ", content).strip().lower()
