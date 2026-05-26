"""
Tests for agent/symbol_graph.py — SymbolGraph and extract_symbols_from_file.

Tests verify:
  - extract_symbols_from_file finds functions/classes in Python files
  - extract_symbols_from_file extracts call edges between functions
  - SymbolGraph.index_file stores symbols in SQLite
  - SymbolGraph.index_file is idempotent (re-index clears old entries)
  - SymbolGraph.locate() returns symbols with partial name match
  - SymbolGraph.callers() / callees() return correct edges
  - SymbolGraph.trace() combines both directions
  - SymbolGraph.delete_file() clears entries for that file
  - Workspace isolation: two workspaces don't cross-contaminate
  - format_locate_for_llm / format_trace_for_llm produce readable text
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.symbol_graph import (
    SymbolGraph,
    Symbol,
    CallEdge,
    extract_symbols_from_file,
)
from tests.conftest import make_py


# ---------------------------------------------------------------------------
# extract_symbols_from_file
# ---------------------------------------------------------------------------

class TestExtractSymbolsFromFile:
    def test_finds_top_level_function(self, tmp_path) -> None:
        path = make_py(tmp_path, "auth.py", """
            def verify_token(token: str) -> dict:
                return {}
        """)
        symbols, _ = extract_symbols_from_file(path)
        names = [s["name"] for s in symbols]
        assert "verify_token" in names

    def test_finds_class_definition(self, tmp_path) -> None:
        path = make_py(tmp_path, "models.py", """
            class UserModel:
                pass
        """)
        symbols, _ = extract_symbols_from_file(path)
        names = [s["name"] for s in symbols]
        assert "UserModel" in names

    def test_symbol_has_kind_function(self, tmp_path) -> None:
        path = make_py(tmp_path, "f.py", "def foo(): pass")
        symbols, _ = extract_symbols_from_file(path)
        fn = next(s for s in symbols if s["name"] == "foo")
        assert fn["kind"] == "function"

    def test_symbol_has_kind_class(self, tmp_path) -> None:
        path = make_py(tmp_path, "c.py", "class Bar: pass")
        symbols, _ = extract_symbols_from_file(path)
        cls = next(s for s in symbols if s["name"] == "Bar")
        assert cls["kind"] == "class"

    def test_symbol_line_numbers(self, tmp_path) -> None:
        path = make_py(tmp_path, "lines.py", textwrap.dedent("""\
            def alpha():
                pass

            def beta():
                pass
        """))
        symbols, _ = extract_symbols_from_file(path)
        names_to_lines = {s["name"]: s["start_line"] for s in symbols}
        assert names_to_lines["alpha"] == 1
        assert names_to_lines["beta"] == 4

    def test_extracts_call_edges(self, tmp_path) -> None:
        path = make_py(tmp_path, "calls.py", """
            def helper():
                pass

            def main():
                helper()
        """)
        _, edges = extract_symbols_from_file(path)
        callees = [e["to_symbol"] for e in edges]
        assert "helper" in callees

    def test_edges_have_correct_fields(self, tmp_path) -> None:
        path = make_py(tmp_path, "e.py", """
            def a():
                pass
            def b():
                a()
        """)
        _, edges = extract_symbols_from_file(path)
        assert len(edges) >= 1
        e = edges[0]
        assert "from_file" in e
        assert "from_symbol" in e
        assert "to_symbol" in e
        assert "edge_type" in e

    def test_no_duplicate_edges(self, tmp_path) -> None:
        path = make_py(tmp_path, "dup.py", """
            def caller():
                callee()
                callee()
                callee()
            def callee():
                pass
        """)
        _, edges = extract_symbols_from_file(path)
        keys = [(e["from_symbol"], e["to_symbol"]) for e in edges]
        assert len(keys) == len(set(keys))

    def test_unsupported_extension_returns_empty(self, tmp_path) -> None:
        f = tmp_path / "notes.txt"
        f.write_text("just some text")
        symbols, edges = extract_symbols_from_file(str(f))
        assert symbols == []
        assert edges == []

    def test_nonexistent_file_returns_empty(self, tmp_path) -> None:
        symbols, edges = extract_symbols_from_file(str(tmp_path / "ghost.py"))
        assert symbols == []
        assert edges == []

    def test_multiple_functions_found(self, tmp_path) -> None:
        path = make_py(tmp_path, "multi.py", """
            def foo(): pass
            def bar(): pass
            def baz(): pass
        """)
        symbols, _ = extract_symbols_from_file(path)
        names = {s["name"] for s in symbols}
        assert {"foo", "bar", "baz"}.issubset(names)


# ---------------------------------------------------------------------------
# SymbolGraph — index_file / delete_file / symbol_count
# ---------------------------------------------------------------------------

class TestSymbolGraphIndex:
    def test_index_file_returns_symbol_count(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "a.py", "def foo(): pass\nclass Bar: pass")
        count = g.index_file("ws1", path)
        assert count >= 2

    def test_index_file_stores_in_db(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "b.py", "def my_func(): pass")
        g.index_file("ws1", path)
        assert g.symbol_count("ws1") >= 1

    def test_index_file_idempotent(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "c.py", "def fn(): pass")
        g.index_file("ws1", path)
        first = g.symbol_count("ws1")
        g.index_file("ws1", path)
        assert g.symbol_count("ws1") == first

    def test_delete_file_removes_symbols(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "d.py", "def to_remove(): pass")
        g.index_file("ws1", path)
        assert g.symbol_count("ws1") >= 1
        g.delete_file("ws1", path)
        assert g.symbol_count("ws1") == 0

    def test_build_for_workspace_indexes_multiple_files(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        p1 = make_py(tmp_path, "x.py", "def x(): pass")
        p2 = make_py(tmp_path, "y.py", "def y(): pass")
        stats = g.build_for_workspace("ws1", [p1, p2])
        assert stats["files"] == 2
        assert stats["symbols"] >= 2

    def test_workspace_isolation(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "shared.py", "def fn(): pass")
        g.index_file("ws_a", path)
        g.index_file("ws_b", path)
        assert g.symbol_count("ws_a") >= 1
        g.delete_file("ws_a", path)
        assert g.symbol_count("ws_a") == 0
        assert g.symbol_count("ws_b") >= 1  # ws_b unaffected


# ---------------------------------------------------------------------------
# SymbolGraph — locate
# ---------------------------------------------------------------------------

class TestSymbolGraphLocate:
    def test_locate_exact_match(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "auth.py", "def verify_token(): pass")
        g.index_file("ws", path)
        symbols = g.locate("ws", "verify_token")
        assert len(symbols) >= 1
        assert symbols[0].name == "verify_token"

    def test_locate_partial_match(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "f.py", "def authenticate_user(): pass\ndef authenticate_admin(): pass")
        g.index_file("ws", path)
        symbols = g.locate("ws", "authenticate")
        names = [s.name for s in symbols]
        assert "authenticate_user" in names
        assert "authenticate_admin" in names

    def test_locate_returns_symbol_with_file_path(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "mod.py", "def zap(): pass")
        g.index_file("ws", path)
        symbols = g.locate("ws", "zap")
        assert symbols[0].file_path == path

    def test_locate_returns_symbol_with_snippet(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "snap.py", "def snap_fn():\n    return 42\n")
        g.index_file("ws", path)
        symbols = g.locate("ws", "snap_fn")
        assert symbols[0].snippet != ""

    def test_locate_no_match_returns_empty(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        symbols = g.locate("ws", "nonexistent_xyz")
        assert symbols == []

    def test_locate_respects_limit(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        funcs = "\n".join(f"def fn_{i}(): pass" for i in range(10))
        path = make_py(tmp_path, "many.py", funcs)
        g.index_file("ws", path)
        symbols = g.locate("ws", "fn_", limit=3)
        assert len(symbols) <= 3


# ---------------------------------------------------------------------------
# SymbolGraph — callers / callees / trace
# ---------------------------------------------------------------------------

class TestSymbolGraphTrace:
    def _indexed_graph(self, tmp_path):
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "svc.py", textwrap.dedent("""\
            def helper():
                pass

            def process():
                helper()

            def orchestrate():
                process()
                helper()
        """))
        g.index_file("ws", path)
        return g, path

    def test_callees_of_process_includes_helper(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        edges = g.callees("ws", "process")
        callees = [e.to_symbol for e in edges]
        assert "helper" in callees

    def test_callers_of_helper_includes_process(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        edges = g.callers("ws", "helper")
        callers = [e.from_symbol for e in edges]
        assert "process" in callers

    def test_callers_of_helper_includes_orchestrate(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        edges = g.callers("ws", "helper")
        callers = [e.from_symbol for e in edges]
        assert "orchestrate" in callers

    def test_trace_both_returns_callers_and_callees(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        result = g.trace("ws", "process", direction="both")
        assert "callers" in result
        assert "callees" in result

    def test_trace_callers_only(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        result = g.trace("ws", "process", direction="callers")
        assert "callers" in result
        assert "callees" not in result

    def test_trace_callees_only(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        result = g.trace("ws", "process", direction="callees")
        assert "callees" in result
        assert "callers" not in result

    def test_edges_have_file_path(self, tmp_path) -> None:
        g, path = self._indexed_graph(tmp_path)
        edges = g.callers("ws", "helper")
        assert all(e.from_file == path for e in edges)

    def test_no_edges_for_uncalled_symbol(self, tmp_path) -> None:
        g, _ = self._indexed_graph(tmp_path)
        edges = g.callers("ws", "orchestrate")
        assert edges == []


# ---------------------------------------------------------------------------
# SymbolGraph — get_snippet
# ---------------------------------------------------------------------------

class TestGetSnippet:
    def test_returns_code_lines(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        f = tmp_path / "code.py"
        f.write_text("line1\nline2\nline3\nline4\n")
        snippet = g.get_snippet(str(f), start_line=1, end_line=4)
        assert "line1" in snippet

    def test_snippet_capped_at_snippet_lines(self, tmp_path) -> None:
        from agent.symbol_graph import SNIPPET_LINES
        g = SymbolGraph(str(tmp_path))
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line_{i}" for i in range(100)))
        snippet = g.get_snippet(str(f), start_line=1, end_line=100)
        assert len(snippet.splitlines()) <= SNIPPET_LINES

    def test_nonexistent_file_returns_empty_string(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        snippet = g.get_snippet(str(tmp_path / "ghost.py"), 1, 10)
        assert snippet == ""


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_format_locate_empty(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        text = g.format_locate_for_llm([], "verify_token")
        assert "No symbol" in text
        assert "verify_token" in text

    def test_format_locate_with_symbols(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        sym = Symbol(
            symbol_id=1, workspace="ws", name="verify_token", kind="function",
            file_path="auth.py", start_line=10, end_line=20, snippet="def verify_token(): ...",
        )
        text = g.format_locate_for_llm([sym], "verify_token")
        assert "verify_token" in text
        assert "auth.py" in text

    def test_format_trace_with_callers(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        edge = CallEdge(from_file="svc.py", from_symbol="main", from_line=10, to_symbol="helper", edge_type="calls")
        text = g.format_trace_for_llm({"callers": [edge]}, "helper")
        assert "main" in text
        assert "svc.py" in text

    def test_format_trace_empty_callers(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        text = g.format_trace_for_llm({"callers": [], "callees": []}, "fn")
        assert "none found" in text.lower()
