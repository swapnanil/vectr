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

from agent.config import (
    SYMBOL_QUALIFIED_BOOST as _SYM_QUALIFIED_BOOST,
    SYMBOL_LEAF_BOOST as _SYM_LEAF_BOOST,
    SYMBOL_STOP_WORDS as _SYM_STOP_WORDS,
    SYMBOL_MIN_LEAF_LEN as _SYM_MIN_LEAF_LEN,
    QUALITY_TRIVIAL as _Q_TRIVIAL,
    QUALITY_NAVIGATIONAL as _Q_NAVIGATIONAL,
    QUALITY_HEADING_ONLY as _Q_HEADING_ONLY,
    QUALITY_GENERATED as _Q_GENERATED,
    QUALITY_VECTR_CONFIG as _Q_VECTR_CONFIG,
    QUALITY_TEST_DEPRIORITISED as _Q_TEST_DEPRIORITISED,
    QUALITY_DOC_PROSE as _Q_DOC_PROSE,
    QUALITY_SHORT_PENALTY as _Q_SHORT_PENALTY,
    DOC_INTENT_DOC_PROSE_MULTIPLIER as _Q_DOC_PROSE_DOC_INTENT,
    DOC_INTENT_PREFIXES,
    DOC_INTENT_ANY_SUBSTRINGS,
    TRIVIAL_DOC_MAX_LINES as _TRIVIAL_DOC_MAX_LINES,
    TRIVIAL_ATTR_CLASS_MAX_ATTRS as _TRIVIAL_ATTR_CLASS_MAX_ATTRS,
    INDEXING_BUILD_ARTIFACT_DIR_SUFFIXES as _BUILD_ARTIFACT_DIR_SUFFIXES,
)

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

# Two-line stub detection (UPG-15.1, Python-focused).
# A chunk whose only content is a declaration header followed by a lone stub body
# (pass / ... / raise NotImplementedError) has no retrieval value.  Structural
# patterns — not tunables.
#
# _STUB_BODY_RE: the second meaningful line is a pure empty-body placeholder.
_STUB_BODY_RE = re.compile(
    r"^[\s]*(pass|\.\.\.|(raise\s+NotImplementedError(\s*\(.*\))?));?[\s]*$"
)

# _DECL_HEADER_RE: the first meaningful line is a class declaration or a
# PARAMETER-LESS function declaration (empty parens, or self/cls only, with an
# optional return-type annotation).  Functions with real parameters
# (e.g. ``def handle(request, uid=None):``) carry semantic signal in their
# signature even when the body is ``pass``, so those are excluded here.
# Class declarations are always matched regardless of base classes, because
# a bare class name + bases without any body is not a useful code chunk.
#
# Matches:
#   class Foo:                class Foo(Base, Meta):
#   def foo():                def foo() -> T:
#   async def foo():          async def foo() -> None:
#   def foo(self):            def foo(cls) -> T:
# Does NOT match (functions with real parameters):
#   def handle(request):      def send(sender, uid=None, **kwargs):
_DECL_HEADER_RE = re.compile(
    r"^[\s]*(?:"
    r"class\s+\w+[^:]*:"                             # class Name (any bases OK) + colon
    r"|(?:async\s+)?def\s+\w+\s*\("                  # def/async def Name(
    r"(?:\s*(?:self|cls)\s*)?"                        # optional self or cls only
    r"\s*\)\s*(?:->[^:]+)?"                          # optional -> return type
    r":"                                              # colon
    r")\s*$"
)

# Attribute-assignment-only class body detection (UPG-15.9 / F25).
# A class header whose body consists ONLY of simple attribute assignments
# (e.g. ``class Meta:\n    model = Writer\n    fields = '__all__'``) is a
# configuration stub with no standalone retrieval value.  Guarded to ONLY fire
# when there is no ``def`` / nested ``class`` / control flow — real small library
# classes (e.g. ``class Meta`` with a custom method) must survive.
#
# _CLASS_HEADER_RE: first meaningful line is a class declaration header.
_CLASS_HEADER_RE = re.compile(r"^[\s]*class\s+\w+[^:]*:\s*$")

# _ATTR_ASSIGN_RE: a line is an attribute assignment of the form
# ``identifier = <value>`` or ``identifier: type = <value>`` (Django/dataclass
# style).  Matches any assignment; combined with _COMPLEX_RHS_RE to detect
# whether the RHS is a "simple" value (not a function call or dotted access).
_ATTR_ASSIGN_RE = re.compile(r"^[\s]*\w+\s*(?::[^=]+)?\s*=\s*(.+)$")

# _COMPLEX_RHS_RE: the right-hand side of an assignment is "complex" (carries
# real semantic signal) when it contains a function call ``(`` or a dotted
# attribute access ``.``.  An assignment like ``username = forms.CharField(...)``
# defines a field with a specific type — real code with retrieval value.
# A "simple" assignment like ``model = Writer`` or ``fields = '__all__'`` is a
# bare config option that is trivial on its own.
_COMPLEX_RHS_RE = re.compile(r"[.(]")

# Lines that signal real logic in a class body — if any of these appear in the
# body, the chunk is NOT an attribute-only stub.
_HAS_DEF_OR_LOGIC_RE = re.compile(
    r"""^[\s]*(
        (async\s+)?def\s          # method definition
        | class\s                 # nested class
        | (if|elif|else|for|while|try|except|finally|with|raise|return|yield|assert)\b
    )""",
    re.VERBOSE,
)

# _TRIVIAL_ATTR_CLASS_MAX_ATTRS is imported from agent.config (via config.yaml
# ranking.quality_priors.trivial_attr_class_max_attrs).  No inline literal here —
# the config import at the top of this file already binds the name.


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

    UPG-15.5: HTML/markup and plain-text chunks with ≤ TRIVIAL_DOC_MAX_LINES
    non-blank lines are also trivial.  This catches 1–2-line test-fixture
    templates ("Logged out", "{{ form }}", "<h1>Error</h1>") and egg-info TXT
    stubs ("django", "from-my-custom-list") that otherwise flood short
    natural-language queries.  Multi-line .txt/.rst documentation (Django's
    docs/howto/*.txt, docs/topics/*.txt) has many more lines and is unaffected.
    """
    raw_nonblank = [l for l in content.splitlines() if l.strip()]
    if not raw_nonblank:
        return True

    # UPG-15.5: language-aware short-prose rule for HTML/TXT doc languages.
    # Checked on raw_nonblank (not _meaningful_lines) so RST heading underlines
    # (===, ---) and HTML tags count as non-blank content.  Any real doc chunk
    # has far more than TRIVIAL_DOC_MAX_LINES non-blank lines; only stub fixtures
    # fall at or below the threshold.
    lang_lower = (language or "").lower()
    if lang_lower in _TRIVIAL_DOC_LANGUAGES and len(raw_nonblank) <= _TRIVIAL_DOC_MAX_LINES:
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
    if len(lines) == 2:
        # Declaration header + lone stub body → no retrieval value.
        # e.g. "class Style:\n    pass"  or  "def foo():\n    ..."
        # Only fires when: (1) first line is a class/def declaration header,
        # AND (2) second line is a pure stub (pass / ... / raise NotImplementedError).
        # A real second line ("return self.x", "x = 1") is NOT a stub body and
        # keeps the chunk alive.
        if _DECL_HEADER_RE.match(lines[0]) and _STUB_BODY_RE.match(lines[1]):
            return True

    # UPG-15.9 / F25: attribute-assignment-only class body with SIMPLE values.
    # A class whose body is ONLY attribute assignments with no method definitions,
    # nested classes, control flow, OR complex RHS (function calls, dotted access)
    # is a configuration stub (e.g. Django's inner ``class Meta:`` with
    # ``model=X, fields='__all__'``).  200+ such 3-line chunks exist in Django
    # test files and flood doc-intent queries with zero educational content.
    #
    # Guard conditions (ALL must hold to classify as trivial):
    #   1. First meaningful line is a class declaration header (not a def).
    #   2. The class body has ≤ _TRIVIAL_ATTR_CLASS_MAX_ATTRS meaningful body lines.
    #   3. Every body line is an attribute assignment (matches _ATTR_ASSIGN_RE).
    #   4. NO body line has a complex RHS: no function call ``(`` or dotted ``.``.
    #      This preserves real Django form/model classes like
    #      ``username = forms.CharField(...)`` (dotted + parens → complex → kept).
    #   5. NO body line contains a method def, nested class, or control-flow keyword.
    #
    # A class with any method, complex field declaration, or more body lines than
    # _TRIVIAL_ATTR_CLASS_MAX_ATTRS is NOT trivial — it may be a real form,
    # dataclass, NamedTuple, or library class.
    if len(lines) >= 2 and _CLASS_HEADER_RE.match(lines[0]):
        body_lines = lines[1:]
        # Require at least 2 body lines: a single-attr-assignment class like
        # ``class Foo:\n    x = 1`` is kept by the UPG-15.1 invariant (the
        # two-line stub rule only fires for pass/... stubs; a real assignment
        # body keeps the chunk alive).  The UPG-15.9 rule targets the
        # multi-attribute config stubs (class Meta: model=X; fields='__all__').
        if 1 < len(body_lines) <= _TRIVIAL_ATTR_CLASS_MAX_ATTRS:
            # Reject immediately if any line has def/class/control flow
            if not any(_HAS_DEF_OR_LOGIC_RE.match(bl) for bl in body_lines):
                # Accept only if every body line is a simple attribute assignment
                # (RHS has no function call parens or dotted attribute access).
                if all(
                    (m := _ATTR_ASSIGN_RE.match(bl)) and not _COMPLEX_RHS_RE.search(m.group(1))
                    for bl in body_lines
                ):
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


def is_build_artifact_file(file_path: str) -> bool:
    """True for files inside build-artifact directories (UPG-15.9).

    Detects files whose path contains a directory component that ends with one of
    the configured build-artifact dir suffixes (e.g. ``.egg-info``, ``.dist-info``).
    These directories are entirely machine-generated (Python packaging metadata,
    file-path manifests, PKG-INFO) and contain no educational content — they flood
    BM25 on module/command identifiers.

    Examples that return True:
      ``/project/Django.egg-info/SOURCES.txt``
      ``/project/myapp.egg-info/PKG-INFO``
      ``/project/mylib-1.0.dist-info/RECORD``

    Real documentation (``docs/howto/*.txt``) is unaffected — ``docs`` does not
    end with any of the configured suffixes.

    Suffixes are sourced from ``indexing.build_artifact_dir_suffixes`` in
    ``agent/config.yaml`` via ``config.INDEXING_BUILD_ARTIFACT_DIR_SUFFIXES``.
    """
    p = PurePosixPath(file_path.replace("\\", "/"))
    # Check every directory component (not the filename itself).
    for part in p.parts[:-1]:
        part_lower = part.lower()
        for suffix in _BUILD_ARTIFACT_DIR_SUFFIXES:
            if part_lower.endswith(suffix):
                return True
    return False


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


# Doc-intent query patterns are sourced from agent/config.yaml
# (ranking.doc_intent.prefixes / .any_substrings) via agent/config.py.
# A query is doc-intent when the user is asking about a *topic* or *concept*,
# not looking for a specific symbol implementation — symbol names in the query
# are context/description, not forced-inclusion targets (UPG-11.11 / F2).
# The _DOC_INTENT_* aliases preserve the in-module call sites below.
_DOC_INTENT_PREFIXES: tuple[str, ...] = DOC_INTENT_PREFIXES
_DOC_INTENT_ANY: tuple[str, ...] = DOC_INTENT_ANY_SUBSTRINGS


def is_doc_intent_query(query: str) -> bool:
    """True when the query describes a topic or asks for documentation/tutorial.

    Doc-intent queries are distinguished from code-intent queries by the presence
    of natural-language question/topic phrases.  When a query is doc-intent, forced-
    inclusion of exact symbol-name matches is suppressed (UPG-11.11): symbol names
    in the query describe the *topic* (e.g. "how to use deconstruct") rather than
    requesting a specific symbol implementation.

    Design notes:
    - Anchored-prefix patterns ("how to …", "what is …") are the primary signal —
      they indicate the query is a question/explanation request.
    - Substring patterns (" tutorial", " guide") are secondary, anchored to avoid
      false hits on identifiers like "tutorial_mode" or "guided_setup".
    - The check is intentionally simple and cheap (string matching) — no regex, no
      NLP — consistent with vectr's zero-LLM-call constraint.
    - Compound queries that START with a doc phrase are classified as doc-intent even
      if they also name specific symbols: "how to write a custom field with deconstruct"
      is doc-intent because the user wants documentation, not the deconstruct method.
    """
    q = query.lower().strip()
    for prefix in _DOC_INTENT_PREFIXES:
        if q.startswith(prefix):
            return True
    for phrase in _DOC_INTENT_ANY:
        if phrase in q:
            return True
    return False


# ---------------------------------------------------------------------------
# Quality prior (UPG-2.1)
# ---------------------------------------------------------------------------

# Documentation languages — prose, not implementation. On code-shaped queries the
# audit found substantive doc prose (blog sections, README walkthroughs, marketing
# HTML) burying real code, because the embedding model scores natural-language prose
# highly against natural-language queries. A mild demotion lets near-tied code edge
# ahead while leaving docs on top when nothing else competes (UPG-2.1).
_DOC_LANGUAGES = {"markdown", "md", "html", "htm", "rst", "text", "txt", "mdx"}

# Languages for which a very short chunk (≤ TRIVIAL_DOC_MAX_LINES non-blank lines)
# is classified as trivial (UPG-15.5). Covers test-fixture HTML templates and
# egg-info / requirements TXT stubs that flood short natural-language queries.
# Markdown is intentionally excluded: is_markdown_heading_only() handles its
# trivial sub-cases already (a 1-line markdown heading is caught there, and a
# single prose sentence has real retrieval value).
_TRIVIAL_DOC_LANGUAGES = {"html", "htm", "text", "txt"}


def is_doc_language(language: str) -> bool:
    """True for documentation/prose languages (vs. implementation code)."""
    return (language or "").lower() in _DOC_LANGUAGES


# Multipliers applied to the hybrid similarity score — sourced from
# agent/config.yaml (ranking.quality_priors) via agent/config.py.
# The _Q_* aliases are imported at the top of this file so all call sites
# inside this module continue to work without change (UPG-12.1).


def quality_score(
    content: str,
    file_path: str = "",
    language: str = "",
    node_type: str = "",
    *,
    query_targets_tests: bool = False,
    query_is_doc_intent: bool = False,
) -> float:
    """A per-chunk usefulness prior in (0, 1], folded into ranking as a multiplier.

    Relevance × usefulness: similarity already models relevance; this models
    "is this chunk a good answer at all, regardless of similarity". Cheap and
    language-agnostic. Lower = worse answer.

    Args:
        query_is_doc_intent: When True, documentation prose chunks receive
            ``_Q_DOC_PROSE_DOC_INTENT`` (default 1.0 = no penalty) instead of
            ``_Q_DOC_PROSE`` (0.70), so they can compete with code on how-to /
            explain / tutorial queries (UPG-11.11).
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
        # UPG-11.11: on doc-intent queries use the elevated multiplier so doc
        # prose is not knocked below code by the normal 0.70 demote.
        score *= _Q_DOC_PROSE_DOC_INTENT if query_is_doc_intent else _Q_DOC_PROSE

    n_lines = len(_meaningful_lines(content))
    if n_lines <= 2:
        score *= _Q_SHORT_PENALTY
    return score


# ---------------------------------------------------------------------------
# Symbol identity boost (UPG-11.1)
# ---------------------------------------------------------------------------

# Symbol-boost tunables and stop-word set are loaded from agent/config.yaml via
# agent/config.py (importlib.resources — works for both repo and installed binary).
# The private aliases (_SYM_*) are kept so the rest of this module and the
# existing test suite can still reference them without change.
# _SYM_QUALIFIED_BOOST, _SYM_LEAF_BOOST, _SYM_STOP_WORDS, _SYM_MIN_LEAF_LEN
# are all imported at the top of this file from agent.config.

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
        # UPG-11.9: skip the subset-check when sub_p is empty — a single-char
        # class name like "Q" produces no sub-parts, and set().issubset(...)
        # is vacuously True, firing +0.20 unconditionally.
        if p_lower in query_tokens or (sub_p and sub_p.issubset(query_tokens)):
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
