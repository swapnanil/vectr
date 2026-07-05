"""
Tree-sitter symbol/call-edge extraction and HTTP route extraction.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent.config import (
    SYMBOL_GRAPH_ERROR_RECOVERY_MAX_EXTEND_STEPS_PER_ATTEMPT,
    SYMBOL_GRAPH_ERROR_RECOVERY_MAX_REPARSE_ATTEMPTS,
    SYMBOL_GRAPH_ERROR_RECOVERY_MIN_SPAN_LINES,
    SYMBOL_GRAPH_RESERVED_KEYWORDS,
)
from agent.symbol_graph._constants import (
    _SYMBOL_TYPES,
    _MODULE_BINDING_TYPES,
    _CALL_TYPES,
    _TYPE_USAGE_NODES,
    _RUST_SKIP_TYPES,
    _MAX_DEPTH,
    _HTTP_METHODS,
    _PY_ROUTE_DECORATOR,
    _PY_ROUTE_METHOD_KW,
    _JAVA_MAPPING,
    _EXPRESS_ROUTE,
)

logger = logging.getLogger(__name__)


def _is_reserved_keyword(name: str, language: str) -> bool:
    """True when `name` is a language keyword for `language` (UPG-JSFLOW-SYMBOLS).

    A desynced/ERROR-node parse (Flow syntax hitting the plain javascript
    grammar, or any other grammar's error-recovery path) can misattribute a
    keyword token — `if`, `for`, `return`, ... — as an identifier. Keyword
    sets are per-language (config.yaml `symbol_graph.reserved_keywords`),
    not a single global list, since keywords differ across language families.
    """
    return name in SYMBOL_GRAPH_RESERVED_KEYWORDS.get(language, frozenset())


# ---------------------------------------------------------------------------
# Tree-sitter helpers (reuse indexer's parser cache)
# ---------------------------------------------------------------------------

def _get_parser(language: str):
    from agent.indexer import _get_parser as _base_get_parser
    return _base_get_parser(language)


def _record_rust_type(name: str) -> bool:
    """A Rust `type_identifier` worth a usage edge: UpperCamelCase (so primitives
    `u32`/`str`/`bool` and snake_case modules are skipped), longer than one char
    (drops generic params `T`/`E`/`K`), and not a std container (UPG-4.4)."""
    return len(name) > 1 and name[0].isupper() and name not in _RUST_SKIP_TYPES


def _rust_call_type_head(func_node, code_bytes: bytes) -> str:
    """Leading type segment of a Rust `Type::assoc(...)` scoped call so
    `trace Type` finds associated-fn and enum-variant construction sites
    (`RegistryClient::new`, `PubGrubPackage::Package`). Returns the rightmost
    path segment for nested paths (`crate::x::RegistryClient::new` → 'RegistryClient')
    or "" when the call isn't a scoped path (UPG-4.4)."""
    if func_node is None or func_node.type != "scoped_identifier":
        return ""
    path = func_node.child_by_field_name("path")
    while path is not None and path.type == "scoped_identifier":
        path = path.child_by_field_name("name")
    if path is not None and path.type in ("identifier", "type_identifier"):
        return code_bytes[path.start_byte:path.end_byte].decode("utf-8", errors="replace")
    return ""


def _get_symbol_name(node, code_bytes: bytes, language: str = "") -> str:
    """Extract identifier from a symbol-defining node."""
    if language in ("c", "cpp"):
        # C/C++ nest the name under the declarator chain; the first direct
        # type_identifier is the RETURN type, not the name — use the shared helper.
        from agent.indexer import c_symbol_name
        return c_symbol_name(node, code_bytes)
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier", "type_identifier"):
            return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


def _get_symbol_name_node(node, language: str):
    """The AST node bearing the symbol's own identifier, when the grammar
    exposes it via tree-sitter's `name` field (UPG-REACT-TSX-FUNCTION-DECL-DROP).

    Used to check parse-error status on the IDENTIFIER ITSELF rather than the
    whole definition subtree: a locally-erroring construct elsewhere in a
    function's signature or body (a Flow-only type the routed grammar can't
    parse, e.g.) must not erase symbol identity when the name token is clean.
    Returns None when the field isn't resolvable for this node type — callers
    then fall back to the broader whole-subtree error check, unchanged from
    before this fix.
    """
    if language in ("c", "cpp"):
        return None  # C/C++ names come from a declarator chain, not this field
    return node.child_by_field_name("name")


def _get_call_name(node, code_bytes: bytes) -> str:
    """Extract called function name from a call node."""
    # Python: call → function(identifier | attribute)
    # JS/TS/Go: call_expression → function(identifier | member_expression)
    func = (
        node.child_by_field_name("function")
        or node.child_by_field_name("name")
        or node.child_by_field_name("method")
    )
    if func is None and node.children:
        func = node.children[0]
    if func is None:
        return ""
    if func.type in ("identifier", "property_identifier"):
        return code_bytes[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
    # attribute access: obj.method / ptr->method (C) — extract just the member name
    if func.type in ("attribute", "member_expression", "field_access", "field_expression"):
        fld = func.child_by_field_name("field") or func.child_by_field_name("property")
        if fld is not None:
            return code_bytes[fld.start_byte:fld.end_byte].decode("utf-8", errors="replace")
        for child in func.children:
            if child.type in ("identifier", "property_identifier", "field_identifier") and child != func.children[0]:
                return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    # fallback: grab last identifier token
    last_ident = ""
    for child in func.children:
        if child.type in ("identifier", "property_identifier"):
            last_ident = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return last_ident


def _module_binding_names(node, code_bytes: bytes, language: str) -> list[tuple[str, int]]:
    """(name, start_line) for each simple module-level binding target (UPG-10.3).
    Python: the `left` of a top-level `assignment` when it's a bare identifier
    (`X = ...`, `X: T = ...`). Tuple/attribute/subscript targets are skipped —
    those aren't 'definitions' a `locate` is looking for."""
    if language == "python" and node.type == "assignment":
        left = node.child_by_field_name("left")
        if left is not None and left.type == "identifier":
            name = code_bytes[left.start_byte:left.end_byte].decode("utf-8", errors="replace")
            if name:
                return [(name, node.start_point[0] + 1)]
    return []


def _collect_symbols_and_calls(
    node,
    code_bytes: bytes,
    language: str,
    file_path: str,
    symbol_types: dict[str, str],
    call_types: set[str],
    symbols: list[dict],
    edges: list[dict],
    current_symbol: str = "",
    current_line: int = 0,
    depth: int = 0,
    type_usage_nodes: set[str] = frozenset(),
    parser=None,
    reparse_budget: list[int] | None = None,
    line_offset: int = 0,
    _just_recovered: bool = False,
    attempted_spans: set[tuple[int, int]] | None = None,
) -> None:
    """Recursively walk AST collecting symbols and call edges.

    `parser`/`reparse_budget`/`line_offset` support UPG-REACT-TSX-FUNCTION-DECL-DROP
    error-recovery reparsing (see the recovery branch below); `code_bytes` is
    whatever byte range the CURRENT frame is walking (the whole file at the
    top level, or an isolated reparsed sub-blob when recovering), and
    `line_offset` is the cumulative row offset from that frame back to the
    true file so emitted `start_line`/`end_line` stay correct. `attempted_spans`
    memoizes (absolute start line, absolute end line) pairs already sent
    through a recovery reparse: a fragment that is genuinely incomplete on
    its own (e.g. a mid-expression excerpt with no enclosing statement)
    reparses into an identically-shaped, identically-errored wrapper node
    covering the same lines — without this guard that reproduces itself
    every recursion and burns the whole budget on one useless span.
    """
    if depth > _MAX_DEPTH:
        return
    if reparse_budget is None:
        reparse_budget = [SYMBOL_GRAPH_ERROR_RECOVERY_MAX_REPARSE_ATTEMPTS]
    if attempted_spans is None:
        attempted_spans = set()
    if node.type in symbol_types:
        name = _get_symbol_name(node, code_bytes, language)
        kind = symbol_types[node.type]
        start = node.start_point[0] + 1 + line_offset
        end = node.end_point[0] + 1 + line_offset
        # UPG-JSFLOW-SYMBOLS / UPG-REACT-TSX-FUNCTION-DECL-DROP: skip anonymous
        # nodes (e.g. C anonymous struct inside a typedef), language keywords
        # misattributed as identifiers, and any node whose own NAME token comes
        # from a parse error — a corrupted/desynced parse (e.g. Flow syntax the
        # grammar can't parse) must not mint a symbol from a bogus identifier.
        # A parse error CONTAINED elsewhere in the subtree (a locally-unparseable
        # type in a parameter or the body) does not erase a legitimate
        # declaration whose own name is clean — checked on the name node in
        # isolation when the grammar exposes one; the previous, broader
        # whole-subtree check remains the fallback where it doesn't.
        name_node = _get_symbol_name_node(node, language)
        name_is_junk = name_node.has_error if name_node is not None else node.has_error
        if name and not _is_reserved_keyword(name, language) and not name_is_junk:
            symbols.append({
                "name": name,
                "kind": kind,
                "file_path": file_path,
                "start_line": start,
                "end_line": end,
            })
        # recurse into body with this symbol as context (use the enclosing symbol
        # name when this node was anonymous, so nested calls still attribute somewhere)
        ctx = name or current_symbol
        ctx_line = start if name else current_line
        for child in node.children:
            _collect_symbols_and_calls(
                child, code_bytes, language, file_path,
                symbol_types, call_types, symbols, edges,
                current_symbol=ctx, current_line=ctx_line,
                depth=depth + 1, type_usage_nodes=type_usage_nodes,
                parser=parser, reparse_budget=reparse_budget, line_offset=line_offset,
                attempted_spans=attempted_spans,
            )
        return

    # UPG-REACT-TSX-FUNCTION-DECL-DROP: a single unparseable construct can
    # desync a grammar's error recovery badly enough that an unrelated span of
    # SIBLING declarations gets swallowed into one opaque, mis-typed node
    # (e.g. tree-sitter emits a bogus `member_expression`/`ERROR` covering
    # hundreds of lines instead of the real `function_declaration` nodes
    # within it) — not just a locally-contained error on one declaration.
    # Reparsing that node's own byte range in isolation frequently resyncs
    # cleanly: the parser starts fresh, no longer carrying the earlier
    # desync's state. Language-agnostic — gated purely on node shape (opaque
    # + errored + large), not on any language/keyword content. A fragment
    # that ISN'T a complete top-level unit on its own (e.g. a mid-expression
    # excerpt) reparses right back into an identically-shaped, identically-
    # errored wrapper covering the same lines — `attempted_spans` recognizes
    # that no-progress case and gives up on that span after one try instead
    # of re-triggering on the lookalike wrapper every recursion.
    span_key = (node.start_point[0] + line_offset, node.end_point[0] + line_offset)
    if (
        parser is not None
        and not _just_recovered
        and node.has_error
        and reparse_budget[0] > 0
        and span_key not in attempted_spans
        and (node.end_point[0] - node.start_point[0]) >= SYMBOL_GRAPH_ERROR_RECOVERY_MIN_SPAN_LINES
    ):
        reparse_budget[0] -= 1
        attempted_spans.add(span_key)
        end_byte = node.end_byte
        sibling = node.next_sibling
        blob = code_bytes[node.start_byte:end_byte]
        sub_tree = parser.parse(blob)
        # The desync's error-recovery boundary for `node` is an arbitrary
        # token cut, not a real statement boundary — it can land mid-
        # declaration, leaving the isolated reparse's own trailing child
        # errored too. Grow the reparsed range to absorb the next ORIGINAL
        # sibling (tree-sitter's own sibling link — a structural move, no
        # content/keyword matching) and retry until the tail clears or the
        # shared budget runs out, so a declaration split across the cut is
        # recovered whole.
        # Extension steps are bounded by their own small per-attempt cap, not
        # the shared per-file budget — otherwise one badly-cut region could
        # spend the entire file's budget on itself and starve every other
        # desynced region.
        extend_steps = 0
        while (
            sub_tree.root_node.children
            and sub_tree.root_node.children[-1].has_error
            and sibling is not None
            and extend_steps < SYMBOL_GRAPH_ERROR_RECOVERY_MAX_EXTEND_STEPS_PER_ATTEMPT
        ):
            extend_steps += 1
            end_byte = sibling.end_byte
            sibling = sibling.next_sibling
            blob = code_bytes[node.start_byte:end_byte]
            sub_tree = parser.parse(blob)
        _collect_symbols_and_calls(
            sub_tree.root_node, blob, language, file_path,
            symbol_types, call_types, symbols, edges,
            current_symbol=current_symbol, current_line=current_line,
            depth=depth + 1, type_usage_nodes=type_usage_nodes,
            parser=parser, reparse_budget=reparse_budget,
            line_offset=line_offset + node.start_point[0],
            _just_recovered=True,
            attempted_spans=attempted_spans,
        )
        return

    # UPG-10.3: module-level constant/variable bindings. ONLY at module scope —
    # `current_symbol == ""` means we're not inside any function or class (those
    # set the context), so locals can never leak in. Don't return: the value side
    # may still contain calls/symbols worth walking.
    if not current_symbol and node.type in _MODULE_BINDING_TYPES.get(language, frozenset()):
        for nm, ln in _module_binding_names(node, code_bytes, language):
            symbols.append({
                "name": nm,
                "kind": "constant" if nm.lstrip("_").isupper() else "variable",
                "file_path": file_path,
                "start_line": ln + line_offset,
                "end_line": node.end_point[0] + 1 + line_offset,
            })

    if node.type in call_types and current_symbol:
        callee = _get_call_name(node, code_bytes)
        # UPG-JSFLOW-SYMBOLS: same reserved-keyword guard as symbol emission —
        # a keyword token misattributed as a call name (desynced/ERROR-node
        # parse) must not mint a call edge either.
        if callee and not _is_reserved_keyword(callee, language):
            edges.append({
                "from_file": file_path,
                "from_symbol": current_symbol,
                "from_line": current_line,
                "to_symbol": callee,
                "edge_type": "calls",
            })
        # UPG-4.4: `Type::assoc(...)` also links the caller to the TYPE so
        # `trace Type` finds construction / enum-variant sites — not just the
        # bare `new`/`Package` method name `_get_call_name` returns above.
        if type_usage_nodes:
            head = _rust_call_type_head(node.child_by_field_name("function"), code_bytes)
            if head and head != current_symbol and _record_rust_type(head):
                edges.append({
                    "from_file": file_path,
                    "from_symbol": current_symbol,
                    "from_line": current_line,
                    "to_symbol": head,
                    "edge_type": "uses",
                })

    # UPG-4.4: a type reference in a signature/field/generic position links the
    # enclosing symbol to that type. Don't return — generic args nest further
    # type_identifiers (`Result<RegistryClient, Error>`) reached by recursion.
    if node.type in type_usage_nodes and current_symbol:
        tname = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if tname != current_symbol and _record_rust_type(tname):
            edges.append({
                "from_file": file_path,
                "from_symbol": current_symbol,
                "from_line": current_line,
                "to_symbol": tname,
                "edge_type": "uses",
            })

    for child in node.children:
        _collect_symbols_and_calls(
            child, code_bytes, language, file_path,
            symbol_types, call_types, symbols, edges,
            current_symbol=current_symbol, current_line=current_line,
            depth=depth + 1,  # MUST increment — generic nodes dominate deep C ASTs;
                              # leaving this at `depth` let the guard never fire → RecursionError
            type_usage_nodes=type_usage_nodes,
            parser=parser, reparse_budget=reparse_budget, line_offset=line_offset,
            attempted_spans=attempted_spans,
        )


def extract_symbols_from_file(file_path: str) -> tuple[list[dict], list[dict]]:
    """
    Parse a source file and return (symbols, edges).
    Symbols: list of {name, kind, file_path, start_line, end_line}
    Edges:   list of {from_file, from_symbol, from_line, to_symbol, edge_type}
    """
    from agent.indexer import LANG_BY_EXT, _parser_language_for
    path = Path(file_path)
    language = LANG_BY_EXT.get(path.suffix.lower(), "")
    if not language:
        return [], []

    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    # UPG-JSFLOW-SYMBOLS: the grammar we PARSE with may differ from `language`
    # (the dict-lookup key used below for _SYMBOL_TYPES/_CALL_TYPES, kept
    # stable so the desugared node types resolve the same way) — a
    # Flow-typed .js routes to the tsx grammar, which parses type
    # annotations instead of desyncing into ERROR nodes.
    parser = _get_parser(_parser_language_for(language, code))
    if parser is None:
        return [], []

    code_bytes = code.encode("utf-8")
    tree = parser.parse(code_bytes)

    symbol_types = _SYMBOL_TYPES.get(language, {})
    call_types = _CALL_TYPES.get(language, set())
    type_usage_nodes = _TYPE_USAGE_NODES.get(language, frozenset())

    symbols: list[dict] = []
    edges: list[dict] = []
    _collect_symbols_and_calls(
        tree.root_node, code_bytes, language, file_path,
        symbol_types, call_types, symbols, edges,
        type_usage_nodes=type_usage_nodes,
        parser=parser,
    )

    # deduplicate symbols — UPG-REACT-TSX-FUNCTION-DECL-DROP's error-recovery
    # reparse can grow its byte range across an original sibling boundary to
    # recover a declaration split by an arbitrary parser-recovery cut; that
    # sibling's content may then also be reached a second time via the
    # normal walk of the (still error-free-looking) original tree, so the
    # same symbol can be emitted twice.
    seen_symbols = set()
    deduped_symbols: list[dict] = []
    for s in symbols:
        key = (s["file_path"], s["name"], s["kind"], s["start_line"], s["end_line"])
        if key not in seen_symbols:
            seen_symbols.add(key)
            deduped_symbols.append(s)
    symbols = deduped_symbols

    # deduplicate edges
    seen = set()
    deduped_edges: list[dict] = []
    for e in edges:
        key = (e["from_file"], e["from_symbol"], e["to_symbol"])
        if key not in seen:
            seen.add(key)
            deduped_edges.append(e)

    # extract HTTP route symbols (Flask/FastAPI/Express/Spring)
    symbols.extend(_extract_routes(file_path, code, language))

    return symbols, deduped_edges


# ---------------------------------------------------------------------------
# HTTP route extraction — framework-aware route nodes
#
# Extracts route symbols from common web frameworks and adds them to the L2
# symbol graph with kind="route". This makes routes navigable via vectr_locate
# and searchable without reading controller/view files.
#
# Supported frameworks:
#   Python: Flask (@app.route, @app.get/post/...), FastAPI (@router.get/post/...)
#   Java:   Spring @GetMapping, @PostMapping, @PutMapping, @DeleteMapping, @RequestMapping
#   JS/TS:  Express (app.get/post/..., router.get/post/...)
# ---------------------------------------------------------------------------

def _extract_routes(file_path: str, source: str, language: str) -> list[dict]:
    """Return a list of route symbol dicts extracted from source code."""
    routes: list[dict] = []
    lines = source.splitlines()

    if language == "python":
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _PY_ROUTE_DECORATOR.search(line)
            if m:
                verb_from_decorator = m.group(1).upper()
                path = m.group(2)

                # If it's @app.route(...), also look for methods=[] kwarg on same line
                methods: list[str] = []
                if verb_from_decorator == "ROUTE":
                    kw = _PY_ROUTE_METHOD_KW.search(line)
                    if kw:
                        raw_methods = kw.group(1)
                        methods = [v.strip().strip("\"'").upper() for v in raw_methods.split(",")]
                    else:
                        methods = ["GET"]  # Flask default
                else:
                    methods = [verb_from_decorator]

                for method in methods:
                    routes.append({
                        "name": f"{method} {path}",
                        "kind": "route",
                        "file_path": file_path,
                        "start_line": i + 1,
                        "end_line": i + 1,
                    })
            i += 1

    elif language == "java":
        for i, line in enumerate(lines):
            m = _JAVA_MAPPING.search(line)
            if m:
                annotation = m.group(1).upper()
                path = m.group(2)
                method = "GET" if annotation == "REQUEST" else annotation
                routes.append({
                    "name": f"{method} {path}",
                    "kind": "route",
                    "file_path": file_path,
                    "start_line": i + 1,
                    "end_line": i + 1,
                })

    elif language in ("javascript", "typescript"):
        for i, line in enumerate(lines):
            m = _EXPRESS_ROUTE.search(line)
            if m:
                method = m.group(1).upper()
                path = m.group(2)
                routes.append({
                    "name": f"{method} {path}",
                    "kind": "route",
                    "file_path": file_path,
                    "start_line": i + 1,
                    "end_line": i + 1,
                })

    return routes
