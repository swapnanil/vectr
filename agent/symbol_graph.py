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

logger = logging.getLogger(__name__)

# Number of lines returned as a snippet with each symbol location.
# Enough for the AI to understand signature + first few lines of the body.
SNIPPET_LINES = 12


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
    resolution_strategy: str  # exact|suffix|same_module|unique_name|import_chain|fuzzy|none
    query: str


@dataclass
class CallEdge:
    from_file: str
    from_symbol: str
    from_line: int
    to_symbol: str
    edge_type: str      # calls | imports | inherits | implements


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
        "impl_item": "struct",
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
}

# Import node types per language
_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_declaration"},
    "typescript": {"import_declaration"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "zig": {"variable_declaration"},  # const std = @import("std");
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


def _get_symbol_name(node, code_bytes: bytes) -> str:
    """Extract identifier from a symbol-defining node."""
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
    # attribute access: obj.method — extract just the method name
    if func.type in ("attribute", "member_expression", "field_access"):
        for child in func.children:
            if child.type in ("identifier", "property_identifier") and child != func.children[0]:
                return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    # fallback: grab last identifier token
    last_ident = ""
    for child in func.children:
        if child.type in ("identifier", "property_identifier"):
            last_ident = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return last_ident


_MAX_DEPTH = 60  # guard against pathological ASTs blowing Python's stack


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
) -> None:
    """Recursively walk AST collecting symbols and call edges."""
    if depth > _MAX_DEPTH:
        return
    if node.type in symbol_types:
        name = _get_symbol_name(node, code_bytes)
        kind = symbol_types[node.type]
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        symbols.append({
            "name": name,
            "kind": kind,
            "file_path": file_path,
            "start_line": start,
            "end_line": end,
        })
        # recurse into body with this symbol as context
        for child in node.children:
            _collect_symbols_and_calls(
                child, code_bytes, language, file_path,
                symbol_types, call_types, symbols, edges,
                current_symbol=name, current_line=start,
                depth=depth + 1,
            )
        return

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

    for child in node.children:
        _collect_symbols_and_calls(
            child, code_bytes, language, file_path,
            symbol_types, call_types, symbols, edges,
            current_symbol=current_symbol, current_line=current_line,
            depth=depth,
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

    symbols: list[dict] = []
    edges: list[dict] = []
    _collect_symbols_and_calls(
        tree.root_node, code_bytes, language, file_path,
        symbol_types, call_types, symbols, edges,
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

    def build_for_workspace(self, workspace: str, file_paths: list[str]) -> dict:
        """
        Index all files in a workspace. Called after the main vector index is built.
        Returns {"symbols": int, "edges": int, "files": int}
        """
        total_symbols = 0
        for fp in file_paths:
            total_symbols += self.index_file(workspace, fp)

        with self._conn() as conn:
            edge_count = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE workspace = ?", (workspace,)
            ).fetchone()[0]

        return {"symbols": total_symbols, "edges": edge_count, "files": len(file_paths)}

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
          3 unique_name — exactly one symbol contains name as substring
          4 import_chain— symbols in files imported by caller_file
          5 fuzzy       — edit distance ≤ 2
        """
        def _with_snippets(rows: list) -> list[Symbol]:
            syms = [self._row_to_symbol(r) for r in rows]
            for s in syms:
                s.snippet = self.get_snippet(s.file_path, s.start_line, s.end_line)
            return syms

        # Strategy 0: exact name match
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                "ORDER BY file_path LIMIT ?",
                (workspace, name, limit),
            ).fetchall()
        if rows:
            return LocateResult(symbols=_with_snippets(rows), resolution_strategy="exact", query=name)

        # Strategy 1: suffix match — strip qualifier prefix (e.g. "module.Foo" → "Foo")
        suffix = name
        for sep in (":", "."):
            if sep in name:
                suffix = name.rsplit(sep, 1)[-1]
                break
        if suffix != name:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                    "ORDER BY file_path LIMIT ?",
                    (workspace, suffix, limit),
                ).fetchall()
            if rows:
                return LocateResult(symbols=_with_snippets(rows), resolution_strategy="suffix", query=name)

        # Strategy 2: same-module — symbols in the same directory as caller_file
        if caller_file:
            caller_dir = str(Path(caller_file).parent)
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                    "AND file_path LIKE ? ORDER BY file_path LIMIT ?",
                    (workspace, f"%{name}%", f"{caller_dir}/%", limit),
                ).fetchall()
            if rows:
                return LocateResult(symbols=_with_snippets(rows), resolution_strategy="same_module", query=name)

        # Strategy 3: unique-name — exactly one symbol matches name as substring
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                "ORDER BY length(name) LIMIT ?",
                (workspace, f"%{name}%", limit + 1),
            ).fetchall()
        if len(rows) == 1:
            return LocateResult(symbols=_with_snippets(rows), resolution_strategy="unique_name", query=name)

        # Strategy 4: import-chain — symbols in files imported by caller_file
        if caller_file:
            imported = _get_imported_files(caller_file, workspace)
            if imported:
                ph = ", ".join("?" * len(imported))
                with self._conn() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                        f"AND file_path IN ({ph}) ORDER BY file_path LIMIT ?",
                        (workspace, f"%{name}%", *imported, limit),
                    ).fetchall()
                if rows:
                    return LocateResult(symbols=_with_snippets(rows), resolution_strategy="import_chain", query=name)

        # Strategy 5: fuzzy — edit distance ≤ 2 (pre-filter by length to reduce candidates)
        with self._conn() as conn:
            all_rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND ABS(LENGTH(name) - ?) <= 2",
                (workspace, len(name)),
            ).fetchall()
        name_lower = name.lower()
        fuzzy = [r for r in all_rows if _levenshtein(name_lower, r["name"].lower()) <= 2]
        if fuzzy:
            fuzzy.sort(key=lambda r: (_levenshtein(name_lower, r["name"].lower()), len(r["name"])))
            return LocateResult(symbols=_with_snippets(fuzzy[:limit]), resolution_strategy="fuzzy", query=name)

        return LocateResult(symbols=[], resolution_strategy="none", query=name)

    # ------------------------------------------------------------------
    # Query: trace (call graph)
    # ------------------------------------------------------------------

    def callers(self, workspace: str, symbol_name: str, limit: int = 20) -> list[CallEdge]:
        """Who calls this symbol? Supports partial match."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM edges
                WHERE workspace = ? AND to_symbol LIKE ?
                ORDER BY from_file, from_symbol
                LIMIT ?
                """,
                (workspace, f"%{symbol_name}%", limit),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def callees(self, workspace: str, symbol_name: str, limit: int = 20) -> list[CallEdge]:
        """What does this symbol call? Supports partial match."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM edges
                WHERE workspace = ? AND from_symbol LIKE ?
                ORDER BY to_symbol
                LIMIT ?
                """,
                (workspace, f"%{symbol_name}%", limit),
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def trace(
        self,
        workspace: str,
        symbol_name: str,
        direction: Literal["callers", "callees", "both"] = "both",
        limit: int = 20,
    ) -> dict:
        """Combined callers + callees lookup."""
        result: dict = {}
        if direction in ("callers", "both"):
            result["callers"] = self.callers(workspace, symbol_name, limit)
        if direction in ("callees", "both"):
            result["callees"] = self.callees(workspace, symbol_name, limit)
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

    def format_locate_for_llm(self, symbols: list[Symbol], name: str) -> str:
        if not symbols:
            return f"No symbol matching '{name}' found in the indexed codebase."
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
            return f"No symbol matching '{result.query}' found in the indexed codebase."
        _labels = {
            "exact":        "exact name match",
            "suffix":       "suffix match (qualifier stripped)",
            "same_module":  "same-module resolution",
            "unique_name":  "unique-name match",
            "import_chain": "import-chain resolution",
            "fuzzy":        "fuzzy name match (edit-distance ≤ 2)",
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

    def format_trace_for_llm(self, trace_result: dict, symbol_name: str) -> str:
        lines = [f"Call graph trace for '{symbol_name}':\n"]

        callers = trace_result.get("callers", [])
        if callers is not None:
            if callers:
                lines.append(f"Called by ({len(callers)}):")
                for e in callers:
                    lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}")
            else:
                lines.append("Called by: (none found in index)")

        callees = trace_result.get("callees", [])
        if callees is not None:
            if callees:
                lines.append(f"\nCalls ({len(callees)}):")
                for e in callees:
                    lines.append(f"  {e.to_symbol}")
            else:
                lines.append("\nCalls: (none found in index)")

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
