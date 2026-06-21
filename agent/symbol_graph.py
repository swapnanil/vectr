"""
SymbolGraph — L2 knowledge layer: symbols, call graph, import graph.

Enables vectr_locate (where is X defined/used?) and vectr_trace (what calls X?
what does X call?). Built from tree-sitter, no embeddings, no LLM needed.

Design principle: vectr never calls an LLM internally. locate() returns a short
code snippet alongside each symbol so the AI editor can read and understand it
directly — no separate description generation step.

Storage: SQLite in the vectr DB dir. Rebuilt incrementally as files change.
"""
from __future__ import annotations

import logging
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent.config import OUTPUT_SNIPPET_LINES as SNIPPET_LINES

logger = logging.getLogger(__name__)


@dataclass
class Symbol:
    symbol_id: int
    workspace: str
    name: str
    kind: str           # function | method | class | interface | struct | enum
    file_path: str
    start_line: int
    end_line: int
    snippet: str = field(default="")     # first SNIPPET_LINES of the symbol body


@dataclass
class LocateResult:
    symbols: list[Symbol]
    resolution_strategy: str  # exact|suffix|same_module|import_chain|substring|fuzzy|none
    query: str


@dataclass
class CallEdge:
    from_file: str
    from_symbol: str
    from_line: int
    to_symbol: str
    edge_type: str      # calls | imports | inherits | implements
    call_count: int = 1  # UPG-4.2: distinct call sites this aggregated edge stands for


# ---------------------------------------------------------------------------
# Tree-sitter helpers (reuse indexer's parser cache)
# ---------------------------------------------------------------------------

def _get_parser(language: str):
    from agent.indexer import _get_parser as _base_get_parser
    return _base_get_parser(language)


# Node types that define symbols per language
_SYMBOL_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
        "decorated_definition": "function",
    },
    "javascript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "arrow_function": "function",
    },
    "typescript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "arrow_function": "function",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "struct",
    },
    "rust": {
        "function_item": "function",
        # An impl block is an implementation, not the type's definition. Keep it a
        # distinct kind so locate can rank the `struct`/`enum`/`trait` def ahead of
        # the (often many) impl blocks that share the type's name (UPG-4.5).
        "impl_item": "impl",
        "struct_item": "struct",
        "trait_item": "interface",
        "enum_item": "enum",
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
    "zig": {
        "function_declaration": "function",
        "variable_declaration": "struct",  # pub const Foo = struct { ... }
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "union_specifier": "struct",
        "enum_specifier": "enum",
        "type_definition": "type",       # typedef … Name;
        "preproc_def": "macro",
        "preproc_function_def": "macro",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "union_specifier": "struct",
        "enum_specifier": "enum",
        "type_definition": "type",
        "namespace_definition": "namespace",
        "preproc_def": "macro",
        "preproc_function_def": "macro",
    },
}

# UPG-10.3: node types that bind a MODULE-LEVEL name (a constant/config/binding
# that isn't a function or class but IS something callers `locate` — e.g. Python
# `_CLAUDE_MD = """..."""`). Indexed only at module scope (see the scope guard in
# _collect_symbols_and_calls) so function locals never flood the graph. Python
# only for now; JS/TS/Rust/Go top-level const/static are a follow-up (Zig top-
# level `pub const Foo = ...` is already covered via _SYMBOL_TYPES["zig"]).
_MODULE_BINDING_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"assignment"}),
}

# Languages vectr can extract a symbol graph for — i.e. where locate/trace work.
# Anything outside this set is search-only (chunks are indexed, but there are no
# symbol/call-graph edges). UPG-3.3 surfaces this per-language so the caller LLM
# can route: use locate/trace where symbols exist, fall back to search elsewhere.
SYMBOL_LANGUAGES: frozenset[str] = frozenset(_SYMBOL_TYPES)

# Intentionally NOT in config.yaml (Tier-3): SYMBOL_SCHEMA_VERSION is a
# schema-migration trigger.  Changing it via config would silently corrupt or
# force a full reindex without the usual version-bump safeguards.
# Bump whenever symbol/edge extraction changes in a way that makes an
# already-persisted graph stale (new parser language, new edge type, changed
# name resolution). Combined with the parser-language set + embed model into the
# toolchain fingerprint (UPG-8.7) so a vectr upgrade is detectable and the graph
# is rebuilt rather than silently serving partial/old results.
SYMBOL_SCHEMA_VERSION = 5  # 1: base · 2: C/C++ + per-def trace (UPG-3.2/4.x) · 3: Rust uses-edges (UPG-4.4) · 4: module-level constants (UPG-10.3) · 5: .txt/.rst prose docs indexed (UPG-11.3)


def graph_toolchain_fingerprint(embed_model: str = "") -> str:
    """Identity of the toolchain that builds the symbol graph.

    A change here means an already-persisted graph was built by a different
    vectr (new/changed parser, bumped schema, different embed model) and must be
    rebuilt — otherwise locate/trace silently serve stale or partial results
    after an upgrade. See UPG-8.7.
    """
    import hashlib
    parts = [
        f"schema={SYMBOL_SCHEMA_VERSION}",
        "parsers=" + ",".join(sorted(SYMBOL_LANGUAGES)),
        f"embed={embed_model}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def supports_symbols(language: str) -> bool:
    """True if `language` has symbol-graph extraction (locate/trace).

    Normalises common display-name spellings (e.g. "C++"→cpp, "C#"→none) so it
    can be called with either index language keys or human-facing names.
    """
    if not language:
        return False
    norm = language.strip().lower()
    norm = {"c++": "cpp", "cplusplus": "cpp", "objective-c": "c"}.get(norm, norm)
    return norm in SYMBOL_LANGUAGES


# Canonical-ness of a symbol kind for `locate` ranking (UPG-4.5). When several
# symbols share / partially match a name, the user wants "where is X defined",
# so lead with the type/function definition and bury impl blocks and aliases.
# Lower rank = more canonical.
_KIND_RANK: dict[str, int] = {
    "class": 0, "struct": 0, "enum": 0, "interface": 0, "trait": 0,
    "function": 1, "method": 1,
    "route": 2,
    "macro": 3, "variable": 3,
    "impl": 4, "alias": 4, "import": 4,
}
_KIND_RANK_DEFAULT = 2


# Language builtins / stdlib / ubiquitous constructors that pad callee lists with
# noise when answering "what does X call *in this codebase*" (UPG-4.3). A callee
# is treated as a builtin only if it's in this set AND is NOT defined as a symbol
# in the workspace — so a repo that defines its own `len`/`map` keeps it. These
# are suppressed from callee lists by default and shown with include_builtins.
_BUILTINS: dict[str, frozenset[str]] = {
    "python": frozenset({
        "print", "len", "isinstance", "issubclass", "assert", "range", "enumerate",
        "zip", "map", "filter", "sorted", "reversed", "sum", "min", "max", "abs",
        "any", "all", "open", "format", "repr", "str", "int", "float", "bool",
        "list", "dict", "set", "tuple", "frozenset", "bytes", "bytearray",
        "type", "super", "getattr", "setattr", "hasattr", "delattr", "callable",
        "iter", "next", "vars", "dir", "id", "hash", "round", "divmod", "pow",
        "join", "split", "strip", "lstrip", "rstrip", "replace", "startswith",
        "endswith", "lower", "upper", "append", "extend", "pop", "get", "keys",
        "values", "items", "update", "add", "encode", "decode",
    }),
    "rust": frozenset({
        "Ok", "Err", "Some", "None", "Vec", "String", "Box", "Rc", "Arc", "Cell",
        "RefCell", "Mutex", "vec", "format", "println", "print", "eprintln",
        "panic", "assert", "assert_eq", "assert_ne", "write", "writeln", "unwrap",
        "expect", "clone", "into", "from", "to_string", "to_owned", "as_ref",
        "as_mut", "as_str", "borrow", "borrow_mut", "iter", "into_iter", "collect",
        "map", "filter", "push", "pop", "len", "is_empty", "default", "drop",
        "matches", "min", "max", "Default", "Borrowed", "Owned",
    }),
    "go": frozenset({
        "make", "new", "len", "cap", "append", "copy", "delete", "panic",
        "recover", "print", "println", "close", "complex", "real", "imag",
        "string", "byte", "rune", "error", "errors", "fmt",
    }),
    "javascript": frozenset({
        "console", "require", "parseInt", "parseFloat", "isNaN", "JSON",
        "Object", "Array", "String", "Number", "Boolean", "Math", "Date",
        "Promise", "Set", "Map", "Symbol", "Error", "push", "pop", "map",
        "filter", "forEach", "reduce", "slice", "splice", "join", "split",
        "indexOf", "includes", "keys", "values", "entries", "assign",
    }),
    "typescript": frozenset({
        "console", "require", "parseInt", "parseFloat", "isNaN", "JSON",
        "Object", "Array", "String", "Number", "Boolean", "Math", "Date",
        "Promise", "Set", "Map", "Symbol", "Error", "push", "pop", "map",
        "filter", "forEach", "reduce", "slice", "splice", "join", "split",
        "indexOf", "includes", "keys", "values", "entries", "assign",
    }),
    "java": frozenset({
        "System", "String", "Integer", "Long", "Double", "Boolean", "Object",
        "Math", "List", "Map", "Set", "Arrays", "Collections", "Optional",
        "assert", "equals", "hashCode", "toString", "valueOf", "length", "size",
        "get", "add", "put", "remove", "contains", "isEmpty", "println", "print",
    }),
    "c": frozenset({
        "malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset",
        "strlen", "strcmp", "strncmp", "strcpy", "strncpy", "strcat", "strchr",
        "snprintf", "sprintf", "printf", "fprintf", "fputs", "fputc", "puts",
        "abort", "assert", "exit", "sizeof", "offsetof", "va_start", "va_end",
        "va_arg", "qsort", "memcmp",
    }),
    "cpp": frozenset({
        "malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset",
        "strlen", "strcmp", "snprintf", "printf", "fprintf", "abort", "assert",
        "exit", "sizeof", "move", "forward", "make_shared", "make_unique",
        "push_back", "emplace_back", "size", "begin", "end", "at", "find",
        "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    }),
    "zig": frozenset({
        "maxInt", "minInt", "assert", "panic", "print", "alloc", "free", "expect",
        "expectEqual", "expectError", "create", "destroy", "init", "deinit",
        "format", "warn", "debug", "log", "sizeOf", "alignOf", "as", "intCast",
    }),
}


def _partial_match_key(row, query_lower: str) -> tuple:
    """Sort key for partial (substring) `locate` matches.

    Ordering, most→least preferred:
      1. match position — exact (case-insensitive) > prefix > interior substring
      2. canonical kind — def > impl/alias (see _KIND_RANK)
      3. not a test/private file
      4. shorter name (closer to the query)
      5. file_path (stable tiebreak)
    """
    name = row["name"]
    nl = name.lower()
    if nl == query_lower:
        pos = 0
    elif nl.startswith(query_lower):
        pos = 1
    else:
        pos = 2
    kind_rank = _KIND_RANK.get(row["kind"], _KIND_RANK_DEFAULT)
    fp = row["file_path"]
    fp_low = fp.replace("\\", "/").lower()
    segments = fp_low.split("/")
    base = segments[-1]
    stem = base.rsplit(".", 1)[0]
    # Test-file / private-file detection must look at the basename and exact path
    # segments only — substring "test" in a path (e.g. pytest tmp dirs, a
    # "my_test_project" root) must NOT penalise an otherwise-canonical symbol.
    is_test = (
        stem.startswith(("test_", "test-")) or stem in ("test", "tests")
        or stem.endswith(("_test", ".test", ".spec", "_spec"))
        or any(seg in ("test", "tests", "testing", "__tests__") for seg in segments[:-1])
    )
    is_private = base.startswith("_")
    test_penalty = 1 if (is_test or is_private) else 0
    return (pos, kind_rank, test_penalty, len(name), fp)


# Call node types per language
_CALL_TYPES: dict[str, set[str]] = {
    "python": {"call"},
    "javascript": {"call_expression", "new_expression"},
    "typescript": {"call_expression", "new_expression"},
    "go": {"call_expression"},
    "rust": {"call_expression", "method_call_expression"},
    "java": {"method_invocation", "object_creation_expression"},
    "zig": {"call_expression", "builtin_function"},
    "c": {"call_expression"},
    "cpp": {"call_expression", "new_expression"},
}

# Type-usage node types per language (UPG-4.4). In some languages the dominant
# way code interacts with a type is not a free-function call but a by-value/
# by-reference *usage* — a parameter, return type, field, or generic argument.
# `trace <Type>` was empty for heavily-used Rust types (uv `RegistryClient`,
# `BuildContext`, `PubGrubPackage`) because none of those usages produced an
# edge. We record them as `edge_type="uses"` so the type's call sites surface.
# Keyed by language so it stays opt-in (Rust only for now; extensible later).
_TYPE_USAGE_NODES: dict[str, set[str]] = {
    "rust": {"type_identifier"},
}

# Rust type names we never record a usage edge for: `Self`, std containers, and
# the ubiquitous result/option/collection types. They'd be pure noise to trace
# and would bloat the edge table. Primitives (u32, str, bool, …) are filtered
# separately by the UpperCamelCase convention check in `_record_rust_type`.
_RUST_SKIP_TYPES: frozenset[str] = frozenset({
    "Self", "String", "Vec", "Box", "Rc", "Arc", "Option", "Result", "Cow",
    "Cell", "RefCell", "Mutex", "RwLock", "HashMap", "HashSet", "BTreeMap",
    "BTreeSet", "VecDeque", "Ok", "Err", "Some", "None",
})


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


# Import node types per language
_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_declaration"},
    "typescript": {"import_declaration"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "zig": {"variable_declaration"},  # const std = @import("std");
    "c": {"preproc_include"},
    "cpp": {"preproc_include"},
}


def _levenshtein(a: str, b: str) -> int:
    """Compute edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _get_imported_files(caller_file: str, workspace: str) -> list[str]:
    """Parse caller_file's import statements and return workspace-resident file paths it imports."""
    workspace_path = Path(workspace)
    caller_path = Path(caller_file)
    try:
        src = caller_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    # Build stem → path index for workspace Python files
    name_index: dict[str, str] = {}
    for f in workspace_path.rglob("*.py"):
        name_index[f.stem] = str(f)
        try:
            rel = f.relative_to(workspace_path)
            key = str(rel).replace("/", ".").replace("\\", ".").removesuffix(f.suffix)
            name_index[key] = str(f)
        except ValueError:
            pass

    _PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE)
    imported: list[str] = []
    for m in _PY_IMPORT.finditer(src):
        parts = m.group(1).split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
            if candidate in name_index:
                resolved = name_index[candidate]
                if resolved != str(caller_path):
                    imported.append(resolved)
                break

    return list(dict.fromkeys(imported))  # dedup while preserving order


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


# Intentionally NOT in config.yaml (Tier-3): _MAX_DEPTH is a recursion
# safety guard tied to Python's frame limit (~1000).  A user who bumped it
# via config could trigger RecursionError on pathological ASTs.
# Guard against pathological ASTs blowing Python's recursion limit. Must be
# counted on EVERY recursion (see below) or it never fires. 200 is far deeper
# than real functions/calls nest, while staying well under Python's ~1000 frame
# limit — deeply-nested data (big C initializer tables) is cut off, not crashed.
_MAX_DEPTH = 200


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
        # `trace Type` surfaces construction / enum-variant sites — not just the
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

# HTTP method verbs — used to identify route patterns
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Python decorator patterns: @app.route("/path"), @router.get("/path")
_PY_ROUTE_DECORATOR = re.compile(
    r'@\w+\.(route|' + "|".join(_HTTP_METHODS) + r')\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Python: method= kwarg on @app.route
_PY_ROUTE_METHOD_KW = re.compile(r'methods\s*=\s*\[([^\]]+)\]', re.IGNORECASE)

# Java Spring annotations
_JAVA_MAPPING = re.compile(
    r'@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\((?:value\s*=\s*)?["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Express.js: app.get("/path",  router.post("/path",
_EXPRESS_ROUTE = re.compile(
    r'\b(?:app|router|express)\.(get|post|put|delete|patch|use)\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


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


# ---------------------------------------------------------------------------
# SQLite-backed SymbolGraph
# ---------------------------------------------------------------------------

class SymbolGraph:
    """
    Persistent symbol and call graph store.

    Answers:
      locate(name)  → which files define or reference a symbol
      callers(name) → which symbols call this one
      callees(name) → which symbols this one calls
    """

    def __init__(self, db_dir: str) -> None:
        self._db_path = Path(db_dir) / "symbol_graph.sqlite"
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace   TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    start_line  INTEGER NOT NULL,
                    end_line    INTEGER NOT NULL,
                    description TEXT,
                    indexed_at  REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sym_workspace ON symbols(workspace);
                CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_path);

                CREATE TABLE IF NOT EXISTS edges (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace    TEXT NOT NULL,
                    from_file    TEXT NOT NULL,
                    from_symbol  TEXT NOT NULL,
                    from_line    INTEGER NOT NULL,
                    to_symbol    TEXT NOT NULL,
                    edge_type    TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_edge_workspace ON edges(workspace);
                CREATE INDEX IF NOT EXISTS idx_edge_from ON edges(from_symbol);
                CREATE INDEX IF NOT EXISTS idx_edge_to ON edges(to_symbol);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_unique
                    ON edges(workspace, from_file, from_symbol, from_line, to_symbol, edge_type);

                CREATE TABLE IF NOT EXISTS graph_meta (
                    workspace    TEXT NOT NULL,
                    key          TEXT NOT NULL,
                    value        TEXT,
                    PRIMARY KEY (workspace, key)
                );
            """)

    # ------------------------------------------------------------------
    # Build / update
    # ------------------------------------------------------------------

    def index_file(self, workspace: str, file_path: str) -> int:
        """
        Index one file: extract symbols and call edges, store in DB.
        Returns number of symbols found.
        Replaces any previous index for this file.
        """
        symbols, edges = extract_symbols_from_file(file_path)
        now = time.time()

        with self._conn() as conn:
            # delete previous entries for this file
            conn.execute(
                "DELETE FROM symbols WHERE workspace = ? AND file_path = ?",
                (workspace, file_path),
            )
            conn.execute(
                "DELETE FROM edges WHERE workspace = ? AND from_file = ?",
                (workspace, file_path),
            )

            for s in symbols:
                conn.execute(
                    """
                    INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, s["name"], s["kind"], file_path,
                     s["start_line"], s["end_line"], now),
                )

            for e in edges:
                conn.execute(
                    """
                    INSERT INTO edges (workspace, from_file, from_symbol, from_line, to_symbol, edge_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, e["from_file"], e["from_symbol"],
                     e["from_line"], e["to_symbol"], e["edge_type"]),
                )

        return len(symbols)

    def delete_file(self, workspace: str, file_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM symbols WHERE workspace = ? AND file_path = ?",
                (workspace, file_path),
            )
            conn.execute(
                "DELETE FROM edges WHERE workspace = ? AND from_file = ?",
                (workspace, file_path),
            )

    def build_for_workspace(
        self, workspace: str, file_paths: list[str], embed_model: str = "",
    ) -> dict:
        """
        Index all files in a workspace. Called after the main vector index is built.
        Returns {"symbols": int, "edges": int, "files": int, "failed": int, "complete": bool}

        Per-file resilient (UPG-8.7): a file that raises during extraction (e.g. a
        pathological AST that hits the recursion guard, an unreadable file) is
        skipped and counted — it can no longer abort the whole loop and silently
        leave every *later* file without symbols (the real cause of the observed
        "5531 symbols across 154 files" partial graph). After the build, the
        toolchain fingerprint + completeness are stamped so an upgrade is
        detectable and a partial build is never mistaken for a trustworthy one.
        """
        total_symbols = 0
        failed: list[str] = []
        for fp in file_paths:
            try:
                total_symbols += self.index_file(workspace, fp)
            except Exception:
                failed.append(fp)
                logger.warning("Symbol extraction failed for %s — skipped", fp, exc_info=True)

        with self._conn() as conn:
            edge_count = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE workspace = ?", (workspace,)
            ).fetchone()[0]

        if failed:
            logger.warning(
                "Symbol graph: %d/%d files failed extraction (e.g. %s) — graph is PARTIAL",
                len(failed), len(file_paths), ", ".join(Path(f).name for f in failed[:3]),
            )

        complete = not failed
        self._write_meta(workspace, {
            "fingerprint": graph_toolchain_fingerprint(embed_model),
            "schema_version": str(SYMBOL_SCHEMA_VERSION),
            "embed_model": embed_model,
            "files": str(len(file_paths)),
            "symbols": str(total_symbols),
            "failed": str(len(failed)),
            "complete": "1" if complete else "0",
            "built_at": str(time.time()),
        })

        return {
            "symbols": total_symbols, "edges": edge_count, "files": len(file_paths),
            "failed": len(failed), "complete": complete,
        }

    # ------------------------------------------------------------------
    # Build metadata / version stamp (UPG-8.7)
    # ------------------------------------------------------------------

    def _write_meta(self, workspace: str, meta: dict[str, str]) -> None:
        with self._conn() as conn:
            for k, v in meta.items():
                conn.execute(
                    "INSERT INTO graph_meta (workspace, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(workspace, key) DO UPDATE SET value = excluded.value",
                    (workspace, k, v),
                )

    def graph_meta(self, workspace: str) -> dict[str, str]:
        """Stored build stamp for this workspace ({} if never built)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM graph_meta WHERE workspace = ?", (workspace,)
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def is_stale(self, workspace: str, embed_model: str = "") -> bool:
        """True if the persisted graph was built by a different toolchain
        (vectr upgrade / parser change / model change) or left incomplete, so a
        full rebuild is warranted. A never-built graph is stale. (UPG-8.7)"""
        meta = self.graph_meta(workspace)
        if not meta:
            return True
        if meta.get("complete") != "1":
            return True
        return meta.get("fingerprint") != graph_toolchain_fingerprint(embed_model)

    def symbol_count(self, workspace: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE workspace = ?", (workspace,)
            ).fetchone()[0]

    def ingest_trace_data(
        self,
        workspace: str,
        trace_events: list[dict],
    ) -> dict:
        """Ingest runtime trace events and add dynamic call edges to the graph.

        Accepts a list of trace event dicts. Recognised fields:
          caller      — name of the calling function/symbol (required)
          callee      — name of the called function/symbol (required)
          caller_file — source file of the caller (optional, empty string if unknown)
          caller_line — line number of the call site (optional, 0 if unknown)

        Dynamic edges use edge_type="dynamic" to distinguish them from static
        analysis edges. This bridges the dynamic dispatch gap: calls via
        __getattr__, decorators, dependency injection, etc. that static analysis
        misses are captured here.

        Returns {"ingested": int, "skipped_invalid": int}.
        """
        ingested = 0
        skipped = 0

        with self._conn() as conn:
            for ev in trace_events:
                caller = str(ev.get("caller", "")).strip()
                callee = str(ev.get("callee", "")).strip()
                if not caller or not callee:
                    skipped += 1
                    continue

                caller_file = str(ev.get("caller_file", "")).strip()
                caller_line = int(ev.get("caller_line", 0))

                conn.execute(
                    """
                    INSERT OR IGNORE INTO edges
                        (workspace, from_file, from_symbol, from_line, to_symbol, edge_type)
                    VALUES (?, ?, ?, ?, ?, 'dynamic')
                    """,
                    (workspace, caller_file, caller, caller_line, callee),
                )
                ingested += 1

        logger.info(
            "ingest_traces: %d edges added, %d skipped (workspace=%s)",
            ingested, skipped, workspace,
        )
        return {"ingested": ingested, "skipped_invalid": skipped}

    # ------------------------------------------------------------------
    # Query: locate
    # ------------------------------------------------------------------

    def locate(self, workspace: str, name: str, limit: int = 10) -> list[Symbol]:
        """
        Find where a symbol is defined. Supports partial match.
        Returns definition sites (start_line of the defining node).
        """
        sql = """
            SELECT * FROM symbols
            WHERE workspace = ? AND name LIKE ?
            ORDER BY
                CASE WHEN name = ? THEN 0 ELSE 1 END,
                length(name),
                file_path
            LIMIT ?
        """
        pattern = f"%{name}%"
        with self._conn() as conn:
            rows = conn.execute(sql, (workspace, pattern, name, limit)).fetchall()
        symbols = [self._row_to_symbol(r) for r in rows]
        for sym in symbols:
            sym.snippet = self.get_snippet(sym.file_path, sym.start_line, sym.end_line)
        logger.debug("locate '%s': %d results", name, len(symbols))
        return symbols

    def locate_l2(
        self,
        workspace: str,
        name: str,
        limit: int = 10,
        caller_file: str | None = None,
    ) -> LocateResult:
        """
        Multi-strategy L2 call resolution. Falls back through 5 strategies
        when exact name match fails.

        Strategies tried in order:
          0 exact       — name = ?
          1 suffix      — strip qualifier prefix (module.Foo → Foo)
          2 same_module — symbols in same directory as caller_file
          3 import_chain— symbols in files imported by caller_file
          4 substring   — name contained as substring, ranked canonical-first
                          (prefix > interior, def > impl/alias) — UPG-4.5
          5 fuzzy       — edit distance ≤ length-scaled threshold, last resort
        """
        def _with_snippets(rows: list) -> list[Symbol]:
            syms = [self._row_to_symbol(r) for r in rows]
            for s in syms:
                s.snippet = self.get_snippet(s.file_path, s.start_line, s.end_line)
            return syms

        name_lower = name.lower()

        def _ranked_result(rows: list, strategy: str) -> LocateResult:
            # Canonical-first ordering (UPG-4.5): even within an exact-name hit,
            # lead with the type/fn definition and bury impl blocks / aliases that
            # share the name. _partial_match_key handles prefix/kind/test ordering.
            ranked = sorted(rows, key=lambda r: _partial_match_key(r, name_lower))
            return LocateResult(symbols=_with_snippets(ranked[:limit]), resolution_strategy=strategy, query=name)

        # Strategy 0: exact name match
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name = ? LIMIT ?",
                (workspace, name, 200),
            ).fetchall()
        if rows:
            return _ranked_result(rows, "exact")

        # Strategy 1: suffix match — strip qualifier prefix (e.g. "module.Foo" → "Foo")
        suffix = name
        for sep in (":", "."):
            if sep in name:
                suffix = name.rsplit(sep, 1)[-1]
                break
        if suffix != name:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE workspace = ? AND name = ? LIMIT ?",
                    (workspace, suffix, 200),
                ).fetchall()
            if rows:
                return _ranked_result(rows, "suffix")

        # Strategy 2: same-module — symbols in the same directory as caller_file
        if caller_file:
            caller_dir = str(Path(caller_file).parent)
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                    "AND file_path LIKE ? LIMIT ?",
                    (workspace, f"%{name}%", f"{caller_dir}/%", 200),
                ).fetchall()
            if rows:
                return _ranked_result(rows, "same_module")

        # Strategy 3: import-chain — symbols in files imported by caller_file
        if caller_file:
            imported = _get_imported_files(caller_file, workspace)
            if imported:
                ph = ", ".join("?" * len(imported))
                with self._conn() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                        f"AND file_path IN ({ph}) LIMIT ?",
                        (workspace, f"%{name}%", *imported, 200),
                    ).fetchall()
                if rows:
                    return _ranked_result(rows, "import_chain")

        # Strategy 4: substring — any symbol whose name contains the query. Always
        # fires when there's at least one match, so fuzzy is a true last resort.
        # Prefix matches lead interior ones, and canonical defs lead impls/aliases
        # (UPG-4.5: `rand` → randint/randfraction before any fuzzy junk). SQL surfaces
        # prefix matches first into the fetch cap; _partial_match_key does the rest.
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                "ORDER BY (CASE WHEN name LIKE ? THEN 0 ELSE 1 END), length(name) LIMIT ?",
                (workspace, f"%{name}%", f"{name}%", 200),
            ).fetchall()
        if rows:
            return _ranked_result(rows, "substring")

        # Strategy 5: fuzzy — edit distance within a length-scaled threshold, and
        # only against names that share the first character. Short queries get a
        # tighter budget so `rand` (len 4) can't match `nan`/`add` (UPG-4.5).
        max_dist = 1 if len(name) <= 4 else 2
        first = name_lower[0] if name_lower else ""
        with self._conn() as conn:
            all_rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND ABS(LENGTH(name) - ?) <= ?",
                (workspace, len(name), max_dist),
            ).fetchall()
        fuzzy = [
            r for r in all_rows
            if r["name"] and r["name"][0].lower() == first
            and _levenshtein(name_lower, r["name"].lower()) <= max_dist
        ]
        if fuzzy:
            fuzzy.sort(key=lambda r: (_levenshtein(name_lower, r["name"].lower()), len(r["name"])))
            return LocateResult(symbols=_with_snippets(fuzzy[:limit]), resolution_strategy="fuzzy", query=name)

        return LocateResult(symbols=[], resolution_strategy="none", query=name)

    # ------------------------------------------------------------------
    # Query: trace (call graph)
    # ------------------------------------------------------------------

    # UPG-4.2: pull a wide candidate set so dedup + relevance ranking happens
    # over ALL edges, not a pre-truncated alphabetical slice (the old
    # `ORDER BY name LIMIT 20` dropped important callees by name, not relevance).
    _EDGE_FETCH_CAP = 1000

    def _edges(
        self,
        workspace: str,
        column: str,
        name: str,
        group: Literal["from_symbol", "to_symbol"],
        limit: int,
        rank_repo_defined: bool,
        include_builtins: bool = True,
        exclude_uses: bool = False,
    ) -> tuple[list[CallEdge], int]:
        """Fetch edges by exact `column` match; fall back to partial (LIKE) only
        when no exact-named edge exists. Exact-first kills the substring
        conflation that merged unrelated symbols — `trace compare` no longer
        pulls in `compare_stacks` / `_Py_atomic_compare_exchange_*` (UPG-4.1).
        Results are deduped and ranked by relevance, then truncated (UPG-4.2).
        Returns `(edges, hidden_builtins)` — the count of builtin/stdlib callees
        suppressed before truncation when `include_builtins` is False (UPG-4.3).
        `exclude_uses` drops type-usage edges (UPG-4.4) — set on the callees
        direction so "Calls:" stays function calls, not the types a function
        mentions; left off for callers so `trace <Type>` finds its usage sites.
        `column` is a fixed internal literal, never user input."""
        uses_clause = " AND edge_type != 'uses'" if exclude_uses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM edges WHERE workspace = ? AND {column} = ?{uses_clause} LIMIT ?",
                (workspace, name, self._EDGE_FETCH_CAP),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    f"SELECT * FROM edges WHERE workspace = ? AND {column} LIKE ?{uses_clause} LIMIT ?",
                    (workspace, f"%{name}%", self._EDGE_FETCH_CAP),
                ).fetchall()
        edges = [self._row_to_edge(r) for r in rows]
        return self._aggregate_edges(workspace, edges, group, limit, rank_repo_defined, include_builtins)

    @staticmethod
    def _is_builtin_call(name: str, from_file: str, repo: set[str]) -> bool:
        """A callee is builtin noise only if it's a known language builtin AND not
        defined as a symbol in this repo (so a repo's own `len`/`map` stays). The
        language is inferred from the calling file's extension (UPG-4.3)."""
        if name in repo:
            return False
        from agent.indexer import LANG_BY_EXT
        lang = LANG_BY_EXT.get(Path(from_file).suffix.lower(), "")
        return name in _BUILTINS.get(lang, frozenset())

    def _aggregate_edges(
        self,
        workspace: str,
        edges: list[CallEdge],
        group: Literal["from_symbol", "to_symbol"],
        limit: int,
        rank_repo_defined: bool,
        include_builtins: bool = True,
    ) -> tuple[list[CallEdge], int]:
        """Collapse edges that share the same caller/callee name into one entry
        carrying a `call_count` of distinct call sites, then rank by relevance
        — repo-defined first (callees only), then call frequency, then name —
        and truncate to `limit`. Replaces the alphabetical-then-truncate path so
        important, repeatedly-called targets survive the cut (UPG-4.2).

        When `not include_builtins` (callee path only), language-builtin/stdlib
        callees are dropped *before* truncation so they can't push repo-internal
        calls out of the window; returns the count hidden (UPG-4.3). Callers are
        never filtered — a caller is by definition a repo-defined function."""
        groups: dict[str, dict] = {}
        for e in edges:
            k = e.from_symbol if group == "from_symbol" else e.to_symbol
            site = (e.from_file, e.from_line, e.to_symbol)
            g = groups.get(k)
            if g is None:
                groups[k] = {"edge": e, "sites": {site}}
            else:
                g["sites"].add(site)
        # Repo-defined ranking only matters for callees (the *from_symbol* of a
        # caller is by definition a function in this repo). Skipping the lookup
        # for callers also avoids a needless symbols-table scan.
        repo = self._known_symbol_names(workspace, list(groups)) if rank_repo_defined else set(groups)
        suppress = rank_repo_defined and not include_builtins
        ranked: list[tuple] = []
        hidden = 0
        for k, g in groups.items():
            e = g["edge"]
            if suppress and self._is_builtin_call(k, e.from_file, repo):
                hidden += 1
                continue
            e.call_count = len(g["sites"])
            ranked.append((0 if k in repo else 1, -e.call_count, k, e))
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        return [t[3] for t in ranked[:limit]], hidden

    def _known_symbol_names(self, workspace: str, names: list[str]) -> set[str]:
        """Subset of `names` that are defined as symbols in this workspace —
        used to rank repo-internal calls ahead of builtins/externals (UPG-4.2)."""
        if not names:
            return set()
        found: set[str] = set()
        with self._conn() as conn:
            for i in range(0, len(names), 500):  # stay under SQLite's bound-var limit
                chunk = names[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT DISTINCT name FROM symbols WHERE workspace = ? "
                    f"AND name IN ({placeholders})",
                    (workspace, *chunk),
                ).fetchall()
                found.update(r["name"] for r in rows)
        return found

    def callers(self, workspace: str, symbol_name: str, limit: int = 20) -> list[CallEdge]:
        """Who calls this symbol? Exact name match preferred (partial fallback).
        Deduped by calling function, ranked by call frequency (UPG-4.2)."""
        edges, _ = self._edges(workspace, "to_symbol", symbol_name, "from_symbol", limit, rank_repo_defined=False)
        return edges

    def callees(
        self, workspace: str, symbol_name: str, limit: int = 20, include_builtins: bool = True
    ) -> list[CallEdge]:
        """What does this symbol call? Exact name match preferred (partial fallback).
        Deduped by callee, repo-internal calls ranked ahead of builtins (UPG-4.2);
        builtin/stdlib callees suppressed unless `include_builtins` (UPG-4.3)."""
        edges, _ = self._edges(
            workspace, "from_symbol", symbol_name, "to_symbol", limit,
            rank_repo_defined=True, include_builtins=include_builtins,
            exclude_uses=True,
        )
        return edges

    def _exact_definitions(self, workspace: str, name: str, limit: int = 20) -> list[Symbol]:
        """Definition sites whose name matches `name` exactly. Each (file_path,
        name) is a distinct node — this is the fully-qualified identity that
        keeps same-named symbols in different modules from merging (UPG-4.1)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                "ORDER BY file_path, start_line LIMIT ?",
                (workspace, name, limit),
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    @staticmethod
    def _module_label(file_path: str, workspace: str) -> str:
        """Repo-relative path used to qualify a definition in trace output."""
        try:
            return str(Path(file_path).relative_to(workspace))
        except ValueError:
            return Path(file_path).name

    def trace(
        self,
        workspace: str,
        symbol_name: str,
        direction: Literal["callers", "callees", "both"] = "both",
        limit: int = 20,
        include_builtins: bool = False,
    ) -> dict:
        """Combined callers + callees lookup.

        UPG-4.1: when `symbol_name` has more than one definition across modules,
        the callees are scoped per definition (by `from_file`) so they are shown
        separately instead of merged into one node. Callees are exactly
        attributable (an edge carries the calling definition's file); callers are
        not (a call site doesn't record which definition it bound), so callers
        stay a flat list with an ambiguity note in the formatter.

        UPG-4.3: builtin/stdlib callees are hidden by default; the count hidden is
        recorded under `hidden_builtins` (flat) / per `by_definition` entry so the
        formatter can offer `include_builtins`.
        """
        result: dict = {}
        if direction in ("callers", "both"):
            result["callers"] = self.callers(workspace, symbol_name, limit)
        if direction in ("callees", "both"):
            callees, hidden = self._edges(
                workspace, "from_symbol", symbol_name, "to_symbol", limit,
                rank_repo_defined=True, include_builtins=include_builtins,
                exclude_uses=True,
            )
            result["callees"] = callees
            result["hidden_builtins"] = hidden

        defs = self._exact_definitions(workspace, symbol_name)
        result["definitions"] = defs
        if direction in ("callees", "both") and len(defs) > 1:
            by_def = []
            with self._conn() as conn:
                for d in defs:
                    rows = conn.execute(
                        "SELECT * FROM edges WHERE workspace = ? AND from_symbol = ? "
                        "AND from_file = ? AND edge_type != 'uses' LIMIT ?",
                        (workspace, symbol_name, d.file_path, self._EDGE_FETCH_CAP),
                    ).fetchall()
                    edges = [self._row_to_edge(r) for r in rows]
                    # dedup + relevance-rank + builtin-suppress this def's callees
                    cs, hidden = self._aggregate_edges(
                        workspace, edges, "to_symbol", limit,
                        rank_repo_defined=True, include_builtins=include_builtins,
                    )
                    by_def.append({
                        "definition": d,
                        "module": self._module_label(d.file_path, workspace),
                        "callees": cs,
                        "hidden_builtins": hidden,
                    })
            result["by_definition"] = by_def
        return result

    def get_snippet(self, file_path: str, start_line: int, end_line: int) -> str:
        """
        Read up to SNIPPET_LINES from a file starting at start_line (1-indexed).
        Returns the raw code so the AI editor can read and understand it directly.
        """
        try:
            lines = Path(file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            s = max(0, start_line - 1)
            e = min(len(lines), s + SNIPPET_LINES)
            snippet = "\n".join(lines[s:e])
            logger.debug("get_snippet: %s:%d-%d (%d lines)", file_path, start_line, end_line, e - s)
            return snippet
        except OSError as exc:
            logger.warning("get_snippet: could not read %s — %s", file_path, exc)
            return ""

    # ------------------------------------------------------------------
    # Formatting for LLM
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_description(query: str) -> bool:
        """A no-match query that reads like a natural-language description rather
        than a symbol name — the LLM likely misrouted a `vectr_search` query to
        `locate`. Whitespace between tokens is the tell: `is_prime` won't fire,
        but "function that checks whether a number is prime" will (UPG-4.6)."""
        return len(query.split()) > 1

    def _no_match_text(self, query: str) -> str:
        """Empty-locate message that ALWAYS hands the LLM a path forward — never a
        dead end (UPG-10.3, extends UPG-4.6). A silent no-match trains the model
        to abandon `locate` and fall back to grep; a redirect keeps it on a vectr
        tool. Description-shaped misses point at the misroute (UPG-4.6); a plain
        single-token miss points at content search, since the name may be a kind
        the symbol graph doesn't make locatable or simply isn't present."""
        base = f"No symbol matching '{query}' found in the indexed codebase."
        if self._looks_like_description(query):
            return base + (" This looks like a description, not a symbol name — "
                           "try vectr_search for concept/semantic lookup.")
        return base + (f' Try vectr_search("{query}") to find it by content — it '
                       "may be defined under a different name or not indexed as a "
                       "locatable symbol.")

    def format_locate_for_llm(self, symbols: list[Symbol], name: str) -> str:
        if not symbols:
            return self._no_match_text(name)
        lines = [f"Symbol locations for '{name}' ({len(symbols)} match{'es' if len(symbols) != 1 else ''}):\n"]
        for s in symbols:
            lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
            if s.snippet:
                for ln in s.snippet.splitlines()[:SNIPPET_LINES]:
                    lines.append(f"    {ln}")
                lines.append("")
        return "\n".join(lines)

    def format_locate_l2_for_llm(self, result: LocateResult) -> str:
        if not result.symbols:
            return self._no_match_text(result.query)
        _labels = {
            "exact":        "exact name match",
            "suffix":       "suffix match (qualifier stripped)",
            "same_module":  "same-module resolution",
            "substring":    "partial-name match (canonical defs first)",
            "import_chain": "import-chain resolution",
            "fuzzy":        "fuzzy name match (edit-distance)",
        }
        label = _labels.get(result.resolution_strategy, result.resolution_strategy)
        n = len(result.symbols)
        lines = [
            f"Symbol locations for '{result.query}' "
            f"({n} match{'es' if n != 1 else ''} via {label}):\n"
        ]
        for s in result.symbols:
            lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
            if s.snippet:
                for ln in s.snippet.splitlines()[:SNIPPET_LINES]:
                    lines.append(f"    {ln}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _count_suffix(edge: CallEdge) -> str:
        """' ×N' when an aggregated edge stands for multiple call sites (UPG-4.2)."""
        return f"  ×{edge.call_count}" if edge.call_count > 1 else ""

    @staticmethod
    def _caller_verb(callers: list) -> str:
        """'Used by' when every reference is a type-usage edge (UPG-4.4) — e.g.
        tracing a Rust struct that's only passed/returned, never free-called;
        'Called/used by' when mixed; 'Called by' otherwise."""
        kinds = {getattr(e, "edge_type", "calls") for e in callers}
        if kinds == {"uses"}:
            return "Used by"
        if "uses" in kinds:
            return "Called/used by"
        return "Called by"

    @staticmethod
    def _hidden_builtins_note(n: int) -> str:
        """Footer telling the LLM repo-internal calls are shown and how to see the
        rest — the suppressed builtin/stdlib calls (UPG-4.3)."""
        if n <= 0:
            return ""
        return (f"    (+{n} builtin/stdlib call{'s' if n != 1 else ''} hidden — "
                f"pass include_builtins=true to show)")

    def format_trace_for_llm(self, trace_result: dict, symbol_name: str) -> str:
        lines = [f"Call graph trace for '{symbol_name}':\n"]

        # UPG-4.1: ambiguous symbol — show callees separated per definition so
        # the LLM sees e.g. resolver `Lock` vs sync `Lock` as distinct, not merged.
        by_def = trace_result.get("by_definition")
        if by_def and len(by_def) > 1:
            lines.append(
                f"⚠ '{symbol_name}' has {len(by_def)} definitions across modules — "
                f"calls are shown per definition. (Callers below match the name only "
                f"and can't be attributed to one definition by static analysis.)\n"
            )
            for entry in by_def:
                d = entry["definition"]
                mod = entry.get("module") or d.file_path
                cs = entry["callees"]
                lines.append(f"[{d.kind}] {symbol_name} @ {mod}:{d.start_line} — calls ({len(cs)}):")
                if cs:
                    for e in cs:
                        lines.append(f"    {e.to_symbol}{self._count_suffix(e)}")
                else:
                    lines.append("    (none found in index)")
                note = self._hidden_builtins_note(entry.get("hidden_builtins", 0))
                if note:
                    lines.append(note)
                lines.append("")
            callers = trace_result.get("callers")
            if callers is not None:
                if callers:
                    lines.append(f"{self._caller_verb(callers)} — any '{symbol_name}' ({len(callers)}):")
                    for e in callers:
                        lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}{self._count_suffix(e)}")
                else:
                    lines.append(f"Called by — any '{symbol_name}': (none found in index)")
            return "\n".join(lines)

        callers = trace_result.get("callers", [])
        if callers is not None:
            if callers:
                lines.append(f"{self._caller_verb(callers)} ({len(callers)}):")
                for e in callers:
                    lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}{self._count_suffix(e)}")
            else:
                lines.append("Called by: (none found in index)")

        callees = trace_result.get("callees", [])
        if callees is not None:
            if callees:
                lines.append(f"\nCalls ({len(callees)}):")
                for e in callees:
                    lines.append(f"  {e.to_symbol}{self._count_suffix(e)}")
            else:
                lines.append("\nCalls: (none found in index)")
            note = self._hidden_builtins_note(trace_result.get("hidden_builtins", 0))
            if note:
                lines.append(note)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_symbol(row: sqlite3.Row) -> Symbol:
        return Symbol(
            symbol_id=row["symbol_id"],
            workspace=row["workspace"],
            name=row["name"],
            kind=row["kind"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            # snippet is populated by locate() after DB fetch — not stored in DB
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> CallEdge:
        return CallEdge(
            from_file=row["from_file"],
            from_symbol=row["from_symbol"],
            from_line=row["from_line"],
            to_symbol=row["to_symbol"],
            edge_type=row["edge_type"],
        )
