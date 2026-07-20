"""AST-aware code chunking: tree-sitter parsers, chunk collection, fallback windows."""
from __future__ import annotations

import re
from pathlib import Path

from agent.chunk_quality import (
    NAVIGATIONAL_NODE_TYPE,
    is_definition_chunk,
    is_navigational_chunk,
    is_trivial_chunk,
)
from agent.config import (
    INDEXING_MAX_CHUNK_LINES as _MAX_CHUNK_LINES,
    INDEXING_CLASS_HEADER_LINES as _CLASS_HEADER_LINES,
    INDEXING_FLOW_SCAN_HEAD_BYTES as _FLOW_SCAN_HEAD_BYTES,
    INDEXING_FLOW_PRAGMA as _FLOW_PRAGMA,
    INDEXING_FLOW_SECONDARY_MARKERS as _FLOW_SECONDARY_MARKERS,
)
from agent.indexer._constants import LANG_BY_EXT
from agent.indexer._types import CodeChunk


# ---------------------------------------------------------------------------
# Tree-sitter AST chunker
# ---------------------------------------------------------------------------

_PARSER_CACHE: dict[str, object] = {}


def _get_parser(language: str):
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    try:
        from tree_sitter import Language, Parser
        if language == "python":
            import tree_sitter_python as ts_lang
        elif language == "javascript":
            import tree_sitter_javascript as ts_lang
        elif language == "typescript":
            import tree_sitter_typescript as ts_lang
            parser = Parser(Language(ts_lang.language_typescript()))
            _PARSER_CACHE[language] = parser
            return parser
        elif language == "tsx":
            # UPG-JSFLOW-SYMBOLS: used to parse Flow-typed `.js` — tsx is the
            # widest JS-family grammar (JSX + type syntax) so it also covers
            # Flow-typed React components, not just plain type annotations.
            import tree_sitter_typescript as ts_lang
            parser = Parser(Language(ts_lang.language_tsx()))
            _PARSER_CACHE[language] = parser
            return parser
        elif language == "go":
            import tree_sitter_go as ts_lang
        elif language == "rust":
            import tree_sitter_rust as ts_lang
        elif language == "java":
            import tree_sitter_java as ts_lang
        elif language == "zig":
            import tree_sitter_zig as ts_lang
        elif language == "c":
            import tree_sitter_c as ts_lang
        elif language == "cpp":
            import tree_sitter_cpp as ts_lang
        else:
            return None
        parser = Parser(Language(ts_lang.language()))
        _PARSER_CACHE[language] = parser
        return parser
    except Exception:
        return None


# UPG-JSFLOW-SYMBOLS: grammar used to parse Flow-typed `.js` — see `_get_parser`
# and `_parser_language_for` below.
_FLOW_JS_GRAMMAR = "tsx"


def is_flow_javascript(code: str) -> bool:
    """Cheap Flow-type-syntax detector for `.js` source.

    tree-sitter-javascript treats Flow syntax (`@flow` pragma, `import type
    {...}`, `: Type` annotations, generics) as ERROR nodes, which desyncs the
    symbol walk — canonical functions go missing and keyword tokens can be
    misattributed as symbol names (UPG-JSFLOW-SYMBOLS). Detection scans only
    the first `INDEXING_FLOW_SCAN_HEAD_BYTES` bytes — O(1) per file, checked
    once at parse time, never per AST node.
    """
    head = code[:_FLOW_SCAN_HEAD_BYTES]
    if _FLOW_PRAGMA in head:
        return True
    return any(marker in head for marker in _FLOW_SECONDARY_MARKERS)


def _parser_language_for(language: str, code: str) -> str:
    """Grammar key to parse `code` with — may differ from `language`, the
    extension-derived key used for chunk/symbol node-type lookups (which stays
    unchanged so results still land under the "javascript" bucket). A `.js`
    file signalling Flow parses with the typescript/tsx grammar instead of the
    plain javascript grammar (UPG-JSFLOW-SYMBOLS); every other language is a
    no-op passthrough. Falls back to `language` itself if grammar loading
    later fails (handled by `_get_parser`'s own try/except).
    """
    if language == "javascript" and is_flow_javascript(code):
        return _FLOW_JS_GRAMMAR
    return language


# UPG-12.1: _MAX_CHUNK_LINES / _CLASS_HEADER_LINES are sourced from
# agent/config.yaml (indexing.*) via agent/config.py — imported above as
# _MAX_CHUNK_LINES / _CLASS_HEADER_LINES.  The alias names are kept so all
# existing call sites work without change.

# Node types that represent class declarations (handled specially — emit header + recurse).
# This is a fast-path/safety net: every one of these already recurses correctly
# today, and is kept as an explicit set so that guarantee never depends on the
# structural check below. Any OTHER container-shaped node type (an impl/extension
# block, a namespace, an interface/trait with default methods, …) is detected
# structurally by `_is_member_container` instead of being enumerated here —
# see UPG-IMPL-BLOCK-CHUNK-TRUNCATION.
_CLASS_NODE_TYPES = {"class_definition", "class_declaration"}

# Node types that represent top-level code units worth indexing per language
#
# UPG-RUST-STRUCT-CHUNK-MISSING: a type DEFINITION (struct/trait/enum/interface/
# type-alias) is a standalone top-level unit exactly like a function or class —
# omitting it from this set means it is either silently swallowed into whatever
# sibling chunk happens to claim the file, or the whole file falls back to one
# anonymous "window" chunk if no other target type is present. Each language's
# type-def node type is added here once verified (via a live tree-sitter parse)
# to be a real standalone top-level node in that grammar, activating
# chunk_quality._TYPE_DEF_NODE_TYPES / is_type_definition_chunk (DEF-B) for it:
#   rust:       struct_item / trait_item / enum_item — verified top-level nodes
#               distinct from impl_item (an implementation of a type, not its
#               definition — UPG-4.5 draws the same line in the symbol graph).
#   typescript: interface_declaration / type_alias_declaration / enum_declaration
#               — each a standalone top-level type definition (verified).
#   go:         type_declaration — a single node type covers struct/interface/
#               alias forms in this grammar (verified); the type's name is
#               nested one level under a `type_spec` child, handled below in
#               `_extract_symbol_name`.
#   java:       interface_declaration / enum_declaration — standalone top-level
#               definitions alongside the already-chunked class_declaration.
# zig `test_declaration` (`test "name" { ... }`) is added too, but for a
# different reason: it is not a type definition, it activates the DORMANT
# Zig inline-test detection in chunk_quality.is_content_structural_test_chunk
# (DEF-A) — without its own chunk, a Zig test block is invisible to the index
# rather than present-but-demoted, which is a worse outcome (it can still
# surface via the window fallback, undemoted, on a file with no functions).
_CHUNK_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "function_expression", "arrow_function", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "function_expression", "arrow_function", "class_declaration",
                   "method_definition", "interface_declaration", "type_alias_declaration", "enum_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "impl_item", "struct_item", "trait_item", "enum_item"},
    "java": {"method_declaration", "class_declaration", "interface_declaration", "enum_declaration"},
    "zig": {"function_declaration", "variable_declaration", "test_declaration"},
    "c": {"function_definition", "struct_specifier", "enum_specifier", "type_definition"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier",
            "enum_specifier", "type_definition", "namespace_definition"},
}

_SYMBOL_FIELD: dict[str, str] = {
    "python": "name",
    "javascript": "name",
    "typescript": "name",
    "go": "name",
    "rust": "name",
    "java": "name",
    "zig": "name",
}


# C/C++ symbol-name extraction (shared with symbol_graph). Needed because C nests
# the name under the declarator chain: in `PyObject *PyDict_New(void)` the only
# direct `type_identifier` child is the RETURN type (PyObject), not the name.
_C_TYPE_NAME_NODES = {"struct_specifier", "union_specifier", "enum_specifier", "class_specifier"}


def _c_declarator_name(node, code_bytes: bytes) -> str:
    """Follow a C/C++ declarator chain (pointer/function/array/parenthesized) to the name."""
    cur = node
    for _ in range(12):  # bounded — declarator nesting is shallow
        if cur is None:
            return ""
        t = cur.type
        if t in ("identifier", "field_identifier", "type_identifier"):
            return code_bytes[cur.start_byte:cur.end_byte].decode("utf-8", errors="replace")
        if t == "qualified_identifier":  # C++ Foo::bar → the trailing member name
            last = None
            for c in cur.named_children:
                if c.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
                    last = c
            return code_bytes[last.start_byte:last.end_byte].decode("utf-8", errors="replace") if last else ""
        nxt = cur.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in cur.named_children
                        if c.type.endswith("declarator") or c.type in ("identifier", "qualified_identifier")), None)
        cur = nxt
    return ""


def c_symbol_name(node, code_bytes: bytes) -> str:
    """Name of a C/C++ symbol-defining node (handles return-type-vs-name confusion)."""
    t = node.type
    if t in _C_TYPE_NAME_NODES or t == "namespace_definition":
        nm = node.child_by_field_name("name")
        return code_bytes[nm.start_byte:nm.end_byte].decode("utf-8", errors="replace") if nm is not None else ""
    if t in ("preproc_def", "preproc_function_def"):
        nm = node.child_by_field_name("name")
        return code_bytes[nm.start_byte:nm.end_byte].decode("utf-8", errors="replace") if nm is not None else ""
    # function_definition / type_definition / declaration → walk the declarator chain
    d = node.child_by_field_name("declarator")
    return _c_declarator_name(d if d is not None else node, code_bytes)


def _extract_symbol_name(node, language: str, code_bytes: bytes) -> str:
    if language in ("c", "cpp"):
        nm = c_symbol_name(node, code_bytes)
        if nm:
            return nm
    # Go nests a type_declaration's own name one level down, under a type_spec
    # child (`type Point struct {...}` -> type_declaration -> type_spec ->
    # name field), rather than exposing it as a direct child like every other
    # node type this function handles (verified via a live tree-sitter-go
    # parse — UPG-RUST-STRUCT-CHUNK-MISSING).
    if language == "go" and node.type == "type_declaration":
        for child in node.children:
            if child.type == "type_spec":
                nm = child.child_by_field_name("name")
                if nm is not None:
                    return code_bytes[nm.start_byte:nm.end_byte].decode("utf-8", errors="replace")
        return ""
    # Prefer tree-sitter's explicit `name` field when the grammar exposes one:
    # the positional child-scan below returns the FIRST identifier-ish child,
    # which for a Java `method_declaration` with a non-primitive return type is
    # the RETURN TYPE (`Producer foo()` → `type_identifier` "Producer" precedes
    # `identifier` "foo"), naming the chunk after its return type
    # (UPG-JAVA-METHOD-NAME-EXTRACTION). The scan stays the fallback for node
    # types the grammar doesn't expose a `name` field for.
    nm = node.child_by_field_name("name")
    if nm is not None:
        return code_bytes[nm.start_byte:nm.end_byte].decode("utf-8", errors="replace")
    for child in node.children:
        # "type_identifier" covers a type definition's own name in grammars
        # that distinguish it from a value identifier — e.g. a TS/Rust
        # struct/interface/type-alias/class name (verified via live parses;
        # UPG-RUST-STRUCT-CHUNK-MISSING). Checked in the same pass as
        # "identifier" so declaration order still decides which child wins
        # when both could appear (a function's own name always precedes any
        # nested return-type token).
        if child.type in ("identifier", "name", "property_identifier", "type_identifier"):
            return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


def _get_leading_comments(lines: list[str], start_line: int) -> str:
    """Return the comment/decorator block immediately preceding start_line (1-indexed)."""
    collected: list[str] = []
    i = start_line - 2  # 0-indexed line just above the node
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith(("#", "//", "*", "/**", "@")):
            collected.insert(0, lines[i])
            i -= 1
        elif not stripped:
            i -= 1  # skip blank separator lines
        else:
            break
    return "\n".join(collected)


# Node types that exist PURELY as a member-group wrapper directly inside a
# container's `body` field — never a chunk-worthy node in their own right, and
# never a general statement/expression wrapper, only a grammar artifact that
# groups a fixed run of members together as one intermediate node. A wrapper
# type belongs here only when it is verified (via a live tree-sitter parse) to
# be nested exactly one level between `body` and the real member declarations,
# with nothing else ever occurring at that position.
#
# UPG-JAVA-ENUM-METHOD-CHUNK-DEPTH: tree-sitter-java nests an enum's methods as
# enum_declaration -> [body] enum_body -> enum_body_declarations ->
# method_declaration — one hop deeper than a class's methods (class_declaration
# -> [body] class_body -> method_declaration directly), because the grammar
# groups the `;` separator and every member after it under one node. This is
# NOT the same shape as a JS closure/anonymous-class false positive: those are
# reachable only through an intervening statement/expression node (a distinct,
# open-ended node family with no fixed member list), which is why a general
# depth increase would reintroduce that class of bug — this allowlist instead
# names the exact, closed set of grammar wrapper node types verified to hold
# nothing but members, and `_is_member_container` may descend through one of
# them exactly one extra hop, never further.
_MEMBER_WRAPPER_NODE_TYPES = frozenset({"enum_body_declarations"})


def _is_member_container(node, target_types: set[str]) -> bool:
    """True when `node` is a CONTAINER — its own body directly lists (or lists
    via exactly one `_MEMBER_WRAPPER_NODE_TYPES` hop) at least one further
    chunk-worthy member — rather than a leaf definition.

    UPG-IMPL-BLOCK-CHUNK-TRUNCATION: `_CLASS_NODE_TYPES` alone under-recognizes
    containers — a Rust `impl_item`, a C++ `namespace_definition`/`class_specifier`,
    a Java `interface_declaration` with default methods, a Swift-style
    `extension_declaration` are all containers too, and enumerating every such
    node type per language is exactly the kind of special-casing that doesn't
    generalize (and was already caught missing `impl_item`, `class_specifier`,
    and `namespace_definition` — none of which are Rust-specific). Detected
    structurally instead: does this node's tree-sitter `body` field directly
    list (one hop down) another node whose type is itself in `target_types`?

    Deliberately checks only DIRECT children of the body field, not the whole
    subtree — a member declaration is always a direct child of its container's
    body list in every grammar checked (tree-sitter python/rust/java/cpp), while
    a chunk-worthy node reachable only several hops deeper — a JS callback
    passed as a call argument, an anonymous inner class built inside a method
    body — is always wrapped in at least one intervening statement/expression
    node first. The one-hop rule is what tells a real member apart from an
    incidental nested occurrence, without naming any language's node types.

    UPG-JAVA-ENUM-METHOD-CHUNK-DEPTH: a body-field child that is itself a known
    member-group WRAPPER (`_MEMBER_WRAPPER_NODE_TYPES`) is allowed exactly one
    further hop — its own children are checked the same way direct body
    children are. This does not touch the JS-closure protection above: the
    wrapper allowlist is a closed, verified set of grammar node types, never
    the open-ended statement/expression families a closure is wrapped in.
    """
    body = node.child_by_field_name("body")
    if body is None:
        return False
    for child in body.children:
        if child.type in target_types:
            return True
        if child.type in _MEMBER_WRAPPER_NODE_TYPES:
            if any(grandchild.type in target_types for grandchild in child.children):
                return True
    return False


def _collect_chunks_ast(
    node,
    code_bytes: bytes,
    lines: list[str],
    language: str,
    file_path: str,
    target_types: set[str],
    results: list[CodeChunk],
    class_context: str = "",
) -> None:
    if node.type in target_types:
        start = node.start_point[0]  # 0-indexed
        end = node.end_point[0]
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        symbol = _extract_symbol_name(node, language, code_bytes)

        # Prepend leading comments/decorators (stripped from AST node but semantically important)
        leading = _get_leading_comments(lines, start + 1)

        # Prepend class context so method chunks are self-contained for the embedder
        context_prefix = f"# class: {class_context}\n" if class_context else ""

        parts = [p for p in [leading, context_prefix + raw] if p]
        content = "\n".join(parts)

        # Cap very long chunks — container bodies (classes, impl/extension blocks,
        # namespaces, …) can be thousands of lines. A CONTAINER only keeps its
        # header here; its members are emitted as their own chunks below, so
        # nothing past the cap is lost. A leaf definition (a plain function, a
        # struct with no nested members) keeps the full budget instead, since
        # nothing else will carry the rest of its content.
        is_container = node.type in _CLASS_NODE_TYPES or _is_member_container(node, target_types)
        cap = _CLASS_HEADER_LINES if is_container else _MAX_CHUNK_LINES
        content_lines = content.splitlines()
        if len(content_lines) > cap:
            content = "\n".join(content_lines[:cap])

        chunk_id = f"{file_path}:{start + 1}-{end + 1}"
        results.append(CodeChunk(
            chunk_id=chunk_id,
            content=content,
            file_path=file_path,
            language=language,
            node_type=node.type,
            start_line=start + 1,
            end_line=end + 1,
            symbol_name=symbol,
        ))

        if is_container:
            # Also recurse into the body so members get their own chunks with context.
            # This walks every child unconditionally (via the plain fallback loop
            # below, for any non-target-type node) all the way down to the real
            # members, so a member-group WRAPPER between `body` and its members
            # (UPG-JAVA-ENUM-METHOD-CHUNK-DEPTH — e.g. Java's enum_body_declarations)
            # is traversed through automatically once `is_container` is True for
            # the enclosing node; no separate wrapper-aware traversal is needed here.
            for child in node.children:
                _collect_chunks_ast(child, code_bytes, lines, language, file_path,
                                    target_types, results, class_context=symbol)
        return  # don't recurse further for non-container nodes (avoids duplicate nested defs)

    for child in node.children:
        _collect_chunks_ast(child, code_bytes, lines, language, file_path,
                            target_types, results, class_context=class_context)


def _fallback_window_chunks(lines: list[str], file_path: str, language: str) -> list[CodeChunk]:
    """Sliding-window chunker for files with no tree-sitter grammar.

    Window=200, overlap=50:
    - 200 lines captures ~3-5 typical functions worth of context — large enough for
      a coherent embedding, small enough that unrelated code doesn't dilute it.
      (100 lines is too small for classes; 500 lines degrades embedding quality.)
    - 50-line overlap ensures a function starting near the tail of one window is
      fully present in the next window, so it's never split across chunk boundaries.
    """
    window, overlap = 200, 50
    chunks: list[CodeChunk] = []
    i = 0
    while i < len(lines):
        end = min(i + window, len(lines))
        content = "\n".join(lines[i:end])
        chunk_id = f"{file_path}:{i + 1}-{end}"
        chunks.append(CodeChunk(
            chunk_id=chunk_id,
            content=content,
            file_path=file_path,
            language=language,
            node_type="window",
            start_line=i + 1,
            end_line=end,
            symbol_name="",
        ))
        i += window - overlap
    return chunks


def _chunk_markdown(lines: list[str], file_path: str) -> list[CodeChunk]:
    """Split markdown at heading boundaries for coherent section-level embeddings.

    Each ATX heading (# through ######) starts a new chunk. Content before the
    first heading is treated as a preamble section. Falls back to window chunks
    if the file has no headings at all (e.g. a flat paragraph document).
    """
    chunks: list[CodeChunk] = []
    current_lines: list[str] = []
    current_start = 1
    current_symbol = ""

    for i, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                chunks.append(CodeChunk(
                    chunk_id=f"{file_path}:{current_start}-{i - 1}",
                    content=content,
                    file_path=file_path,
                    language="markdown",
                    node_type="section",
                    start_line=current_start,
                    end_line=i - 1,
                    symbol_name=current_symbol,
                ))
            current_start = i
            current_symbol = m.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append(CodeChunk(
                chunk_id=f"{file_path}:{current_start}-{len(lines)}",
                content=content,
                file_path=file_path,
                language="markdown",
                node_type="section",
                start_line=current_start,
                end_line=len(lines),
                symbol_name=current_symbol,
            ))

    return chunks or _fallback_window_chunks(lines, file_path, "markdown")


def _postprocess_chunks(chunks: list[CodeChunk]) -> list[CodeChunk]:
    """Wave 1 chunk hygiene applied to every chunker path.

    - UPG-1.1: drop standalone trivial chunks (bare punctuation/return, lone
      import/const) — they have no retrieval value and flood top-N on ties.
      EXEMPT (UPG-TRIVIAL-DROP-ALIAS-DEFS): a chunk that is itself a symbol
      DEFINITION (`is_definition_chunk` — real `symbol_name` + a class/struct/
      enum/interface/type-alias/function/method node_type) is kept even when
      its content alone would read as trivial. A one-line alias like
      ``class ModelForm(BaseModelForm, metaclass=ModelFormMetaclass): pass``
      IS the canonical answer to "where is X defined" — dropping it makes the
      symbol structurally unfindable by search, even though the symbol graph
      / `locate` sees it fine. `is_trivial_chunk` itself is unchanged (other
      callers still judge these chunks' content-only quality at rank time);
      only the DROP decision is gated on the chunk's own recorded properties.
    - UPG-1.2: tag re-export / import-only blocks as navigational so the ranker
      can heavily down-weight them (they're a table of contents, not an answer).
    """
    out: list[CodeChunk] = []
    for c in chunks:
        if is_trivial_chunk(c.content, c.language) and not is_definition_chunk(c.symbol_name, c.node_type):
            continue
        if c.node_type != NAVIGATIONAL_NODE_TYPE and is_navigational_chunk(c.content, c.language):
            c.node_type = NAVIGATIONAL_NODE_TYPE
        out.append(c)
    return out


def chunk_file(file_path: str) -> list[CodeChunk]:
    """Parse a file and return AST-aware chunks (falls back to windows)."""
    path = Path(file_path)
    ext = path.suffix.lower()
    language = LANG_BY_EXT.get(ext, "")

    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = code.splitlines()
    if not lines:
        return []

    if language == "markdown":
        return _postprocess_chunks(_chunk_markdown(lines, file_path))

    if language:
        # UPG-JSFLOW-SYMBOLS: the grammar we PARSE with may differ from `language`
        # (the dict-lookup key below, kept stable so node types resolve the same
        # way) — a Flow-typed .js routes to the tsx grammar.
        parser = _get_parser(_parser_language_for(language, code))
        if parser:
            code_bytes = code.encode("utf-8")
            tree = parser.parse(code_bytes)
            target_types = _CHUNK_NODE_TYPES.get(language, set())
            results: list[CodeChunk] = []
            _collect_chunks_ast(tree.root_node, code_bytes, lines, language, file_path, target_types, results)
            if results:
                return _postprocess_chunks(results)
            # no top-level symbols found → fall through to windows

    # fallback
    lang_label = language or path.suffix.lstrip(".") or "text"
    return _postprocess_chunks(_fallback_window_chunks(lines, file_path, lang_label))
