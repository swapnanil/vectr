"""
Tree-sitter symbol/call-edge extraction and HTTP route extraction.
"""
from __future__ import annotations

import logging
from pathlib import Path

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
) -> None:
    """Recursively walk AST collecting symbols and call edges."""
    if depth > _MAX_DEPTH:
        return
    if node.type in symbol_types:
        name = _get_symbol_name(node, code_bytes, language)
        kind = symbol_types[node.type]
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        if name:  # skip anonymous nodes (e.g. C anonymous struct inside a typedef)
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
                "start_line": ln,
                "end_line": node.end_point[0] + 1,
            })

    if node.type in call_types and current_symbol:
        callee = _get_call_name(node, code_bytes)
        if callee and callee not in {"if", "for", "while", "return", "print"}:
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
        )


def extract_symbols_from_file(file_path: str) -> tuple[list[dict], list[dict]]:
    """
    Parse a source file and return (symbols, edges).
    Symbols: list of {name, kind, file_path, start_line, end_line}
    Edges:   list of {from_file, from_symbol, from_line, to_symbol, edge_type}
    """
    from agent.indexer import LANG_BY_EXT
    path = Path(file_path)
    language = LANG_BY_EXT.get(path.suffix.lower(), "")
    if not language:
        return [], []

    parser = _get_parser(language)
    if parser is None:
        return [], []

    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
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
    )

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
