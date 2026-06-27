"""
Module-level constants, language maps, regex patterns, and metadata functions
for the SymbolGraph package.
"""
from __future__ import annotations

import re

from agent.config import OUTPUT_SNIPPET_LINES as SNIPPET_LINES


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
SYMBOL_SCHEMA_VERSION = 6  # 1: base · 2: C/C++ + per-def trace (UPG-3.2/4.x) · 3: Rust uses-edges (UPG-4.4) · 4: module-level constants (UPG-10.3) · 5: .txt/.rst prose docs indexed (UPG-11.3) · 6: symbol_importance table added (ARCH-1a)


def graph_toolchain_fingerprint(embed_model: str = "") -> str:
    """Identity of the toolchain that builds the symbol graph.

    A change here means an already-persisted graph was built by a different
    vectr (new/changed parser, bumped schema, different embed model) and must be
    rebuilt — otherwise locate/trace silently serve stale or partial results
    after an upgrade. See UPG-8.7.

    Reads SYMBOL_SCHEMA_VERSION through the package namespace so that test-time
    monkeypatching of agent.symbol_graph.SYMBOL_SCHEMA_VERSION is reflected in
    the fingerprint (identical to the original flat-module behaviour).
    """
    import hashlib
    import agent.symbol_graph as _sg
    parts = [
        f"schema={_sg.SYMBOL_SCHEMA_VERSION}",
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

# Intentionally NOT in config.yaml (Tier-3): _MAX_DEPTH is a recursion
# safety guard tied to Python's frame limit (~1000).  A user who bumped it
# via config could trigger RecursionError on pathological ASTs.
# Guard against pathological ASTs blowing Python's recursion limit. Must be
# counted on EVERY recursion (see below) or it never fires. 200 is far deeper
# than real functions/calls nest, while staying well under Python's ~1000 frame
# limit — deeply-nested data (big C initializer tables) is cut off, not crashed.
_MAX_DEPTH = 200

# ---------------------------------------------------------------------------
# HTTP route extraction regex patterns
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
