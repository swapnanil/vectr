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
    """True if a markdown chunk is essentially just heading lines (UPG-2.1)."""
    lines = _meaningful_lines(content)
    if not lines:
        return True
    non_heading = [l for l in lines if not _MD_HEADING_RE.match(l)]
    # Allow a tiny bit of trailing text; flag if it's dominated by headings.
    return len(non_heading) == 0 or (len(lines) <= 2 and len(non_heading) <= 1 and len(" ".join(non_heading)) < 40)


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

# Multipliers applied to the hybrid similarity score. 1.0 = neutral.
_Q_TRIVIAL = 0.15
_Q_NAVIGATIONAL = 0.35
_Q_HEADING_ONLY = 0.40
_Q_GENERATED = 0.45
_Q_VECTR_CONFIG = 0.10
_Q_TEST_DEPRIORITISED = 0.55
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

    n_lines = len(_meaningful_lines(content))
    if n_lines <= 2:
        score *= _Q_SHORT_PENALTY
    return score


# ---------------------------------------------------------------------------
# Dedup (UPG-2.2)
# ---------------------------------------------------------------------------

def normalized_content(content: str) -> str:
    """Whitespace-collapsed lowercase form for exact/near-duplicate detection."""
    return re.sub(r"\s+", " ", content).strip().lower()
