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
}

# Call node types per language
_CALL_TYPES: dict[str, set[str]] = {
    "python": {"call"},
    "javascript": {"call_expression", "new_expression"},
    "typescript": {"call_expression", "new_expression"},
    "go": {"call_expression"},
    "rust": {"call_expression", "method_call_expression"},
    "java": {"method_invocation", "object_creation_expression"},
}

# Import node types per language
_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_declaration"},
    "typescript": {"import_declaration"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
}


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

    return symbols, deduped_edges


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
