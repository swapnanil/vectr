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
    QUALITY_TRIVIAL as _Q_TRIVIAL,
    QUALITY_NAVIGATIONAL as _Q_NAVIGATIONAL,
    QUALITY_HEADING_ONLY as _Q_HEADING_ONLY,
    QUALITY_GENERATED as _Q_GENERATED,
    QUALITY_VECTR_CONFIG as _Q_VECTR_CONFIG,
    QUALITY_TEST_DEPRIORITISED as _Q_TEST_DEPRIORITISED,
    QUALITY_DOC_PROSE as _Q_DOC_PROSE,
    QUALITY_SHORT_PENALTY as _Q_SHORT_PENALTY,
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
# real semantic signal) when it contains a function CALL or a dotted ATTRIBUTE
# access.  An assignment like ``username = forms.CharField(...)`` defines a field
# with a specific type — real code with retrieval value.  A "simple" assignment
# like ``model = Writer``, ``fields = '__all__'``, or a bare tuple/list literal
# ``fields = ()`` / ``fields = ('name', 'age')`` is a config option, trivial on
# its own.  Two signals (either makes the RHS complex):
#   * a call:  a name/closing-bracket immediately followed by ``(``
#     (``CharField(``, ``foo()``) — but NOT a grouping/tuple paren ``= (`` where
#     ``(`` is preceded by whitespace/operator, so ``fields = ('a','b')`` stays
#     simple.
#   * dotted attribute access:  an identifier followed by ``.`` (``forms.``,
#     ``models.CASCADE``) — but NOT a numeric literal like ``1.5`` (digit-led).
_COMPLEX_RHS_RE = re.compile(r"[\w\]\)]\s*\(|[A-Za-z_]\w*\.")

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
    if file_path and is_test_file(file_path):
        score *= _Q_TEST_DEPRIORITISED
    if is_doc_language(language):
        score *= _Q_DOC_PROSE

    n_lines = len(_meaningful_lines(content))
    if n_lines <= 2:
        score *= _Q_SHORT_PENALTY
    return score


# ---------------------------------------------------------------------------
# Class-context extraction (UPG-F4)
# ---------------------------------------------------------------------------

# Regex to detect a CLASS-prefix line injected by the indexer (UPG-F4).
# The indexer prepends "# class: ClassName\n" to method chunks so the embedding
# has class context.  We extract this at query time to reconstruct the qualified
# name when symbol_name is a bare leaf.
_CLASS_PREFIX_RE = re.compile(r"^#\s*class:\s*(\w+)", re.MULTILINE)


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


# ---------------------------------------------------------------------------
# Dedup (UPG-2.2)
# ---------------------------------------------------------------------------

def normalized_content(content: str) -> str:
    """Whitespace-collapsed lowercase form for exact/near-duplicate detection."""
    return re.sub(r"\s+", " ", content).strip().lower()
