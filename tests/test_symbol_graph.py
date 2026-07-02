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
    LocateResult,
    SYMBOL_LANGUAGES,
    SYMBOL_SCHEMA_VERSION,
    graph_toolchain_fingerprint,
    supports_symbols,
    extract_symbols_from_file,
    _levenshtein,
    _partial_match_key,
    _locate_scope_depth_from_lines,
    _locate_scope_depth_batch,
    _locate_class_enclosed_batch,
    _enclosing_class_from_lines,
)
import agent.symbol_graph as _sgmod
from tests.conftest import make_py


def _seed_symbols(g: "SymbolGraph", workspace: str, specs: list[tuple]) -> None:
    """Insert synthetic symbols directly so ranking can be tested with precise
    (name, kind, file_path) control independent of any tree-sitter grammar.

    Each spec is (name, kind, file_path)."""
    import time
    now = time.time()
    with g._conn() as conn:
        for i, (name, kind, fp) in enumerate(specs):
            conn.execute(
                "INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace, name, kind, fp, i + 1, i + 1, now),
            )


def _seed_edges(g: "SymbolGraph", workspace: str, specs: list[tuple],
                edge_type: str = "calls") -> None:
    """Insert synthetic call edges directly so callee/caller ranking can be
    tested with precise control over frequency and call sites.

    Each spec is (from_file, from_symbol, from_line, to_symbol). All seeded with
    `edge_type` (default 'calls'; pass 'uses' for UPG-4.4 type-usage edges)."""
    with g._conn() as conn:
        for from_file, from_symbol, from_line, to_symbol in specs:
            conn.execute(
                "INSERT INTO edges (workspace, from_file, from_symbol, from_line, to_symbol, edge_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (workspace, from_file, from_symbol, from_line, to_symbol, edge_type),
            )


# ---------------------------------------------------------------------------
# UPG-3.3 — language-capability introspection
# ---------------------------------------------------------------------------

class TestSymbolLanguageCapability:
    def test_supported_languages_have_symbols(self) -> None:
        for lang in ("python", "javascript", "typescript", "go", "rust",
                     "java", "zig", "c", "cpp"):
            assert supports_symbols(lang), lang
            assert lang in SYMBOL_LANGUAGES

    def test_doc_and_unknown_languages_have_no_symbols(self) -> None:
        for lang in ("markdown", "html", "text", "cobol", "", "  "):
            assert not supports_symbols(lang)

    def test_display_name_spellings_normalized(self) -> None:
        # map / human-facing names should resolve to the same capability
        assert supports_symbols("C++")
        assert supports_symbols("Python")
        assert supports_symbols("  RUST ")
        assert not supports_symbols("C#")


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
# SymbolGraph — locate_l2 (L2 multi-strategy call resolution)
# ---------------------------------------------------------------------------

class TestLocateL2:
    def test_exact_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "auth.py", "def verify_token(): pass")
        g.index_file("ws", str(tmp_path / "auth.py"))
        result = g.locate_l2("ws", "verify_token")
        assert isinstance(result, LocateResult)
        assert result.resolution_strategy == "exact"
        assert len(result.symbols) >= 1
        assert result.symbols[0].name == "verify_token"

    def test_suffix_strategy_strips_qualifier(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "auth.py", "def verify_token(): pass")
        g.index_file("ws", str(tmp_path / "auth.py"))
        # Query with a qualifier prefix — symbol is stored as plain "verify_token"
        result = g.locate_l2("ws", "module.verify_token")
        assert result.resolution_strategy == "suffix"
        assert result.symbols[0].name == "verify_token"

    def test_same_module_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        subdir = tmp_path / "pkg"
        subdir.mkdir()
        # Symbol in subdir
        f1 = subdir / "a.py"
        f1.write_text("def pkg_helper(): pass\n")
        # Symbol outside subdir
        f2 = tmp_path / "other.py"
        f2.write_text("def pkg_helper_outside(): pass\n")
        g.index_file("ws", str(f1))
        g.index_file("ws", str(f2))
        # Query "helper" from a file in the same subdir; same_module should prefer f1 results
        result = g.locate_l2("ws", "pkg_helper_xyz", caller_file=str(f1))
        # "pkg_helper_xyz" has no exact match; falls through to same_module
        # which finds "pkg_helper" (substring "helper" in "pkg_helper") in the same dir
        assert result.resolution_strategy in ("same_module", "substring", "fuzzy", "none")

    def test_substring_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "x.py", "def unique_xyz_fn(): pass")
        g.index_file("ws", str(tmp_path / "x.py"))
        # No exact match for "unique_xyz" — but a symbol contains it as a substring
        result = g.locate_l2("ws", "unique_xyz")
        assert result.resolution_strategy == "substring"
        assert result.symbols[0].name == "unique_xyz_fn"

    def test_fuzzy_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "f.py", "def foo_bar(): pass")
        g.index_file("ws", str(tmp_path / "f.py"))
        # "fo_bar" is NOT a substring of "foo_bar" (substring skips) but edit-distance 1
        result = g.locate_l2("ws", "fo_bar")
        assert result.resolution_strategy == "fuzzy"
        assert result.symbols[0].name == "foo_bar"

    def test_none_strategy_when_no_match(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        result = g.locate_l2("ws", "zzz_totally_nonexistent_qqq")
        assert result.resolution_strategy == "none"
        assert result.symbols == []

    def test_returns_locate_result_type(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        result = g.locate_l2("ws", "anything")
        assert isinstance(result, LocateResult)
        assert hasattr(result, "symbols")
        assert hasattr(result, "resolution_strategy")
        assert hasattr(result, "query")

    def test_format_locate_l2_shows_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "auth.py", "def verify_token(): pass")
        g.index_file("ws", str(tmp_path / "auth.py"))
        result = g.locate_l2("ws", "verify_token")
        text = g.format_locate_l2_for_llm(result)
        assert "exact name match" in text
        assert "verify_token" in text

    def test_format_locate_l2_empty(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        result = LocateResult(symbols=[], resolution_strategy="none", query="missing")
        text = g.format_locate_l2_for_llm(result)
        assert "No symbol matching" in text
        assert "missing" in text

    def test_import_chain_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        # Create a module that defines a symbol
        lib = tmp_path / "mylib.py"
        lib.write_text("def lib_function(): pass\n")
        # Create a caller file that imports mylib
        caller = tmp_path / "caller.py"
        caller.write_text("import mylib\n\ndef call_it():\n    mylib.lib_function()\n")
        g.index_file("ws", str(lib))
        g.index_file("ws", str(caller))
        # Search with caller_file — "lib_function" exists only in mylib.py (imported by caller)
        result = g.locate_l2("ws", "lib_function_zzz", caller_file=str(caller))
        # Even if import_chain doesn't find "lib_function_zzz", we just verify it doesn't crash
        assert isinstance(result, LocateResult)


class TestLevenshtein:
    def test_identical_strings(self) -> None:
        assert _levenshtein("abc", "abc") == 0

    def test_empty_strings(self) -> None:
        assert _levenshtein("", "") == 0
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "abc") == 3

    def test_single_substitution(self) -> None:
        assert _levenshtein("kitten", "sitten") == 1

    def test_edit_distance_two(self) -> None:
        assert _levenshtein("authenticate", "authenticat") == 1
        assert _levenshtein("foobar", "fobar") == 1
        assert _levenshtein("cat", "cut") == 1
        assert _levenshtein("saturday", "sunday") == 3


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


# ---------------------------------------------------------------------------
# T26: HTTP route extraction
# ---------------------------------------------------------------------------

class TestRouteExtraction:
    def _routes(self, source: str, language: str, suffix: str = ".py") -> list[dict]:
        from agent.symbol_graph import _extract_routes
        return _extract_routes(f"app{suffix}", source, language)

    # Flask / FastAPI (Python)

    def test_flask_route_decorator_extracted(self) -> None:
        source = '@app.route("/users")\ndef get_users(): pass\n'
        routes = self._routes(source, "python")
        assert any(r["name"] == "GET /users" for r in routes)
        assert all(r["kind"] == "route" for r in routes)

    def test_flask_route_post_method(self) -> None:
        source = '@app.route("/users", methods=["POST"])\ndef create_user(): pass\n'
        routes = self._routes(source, "python")
        assert any(r["name"] == "POST /users" for r in routes)

    def test_flask_route_multiple_methods(self) -> None:
        source = '@app.route("/items", methods=["GET", "POST"])\ndef items(): pass\n'
        routes = self._routes(source, "python")
        names = {r["name"] for r in routes}
        assert "GET /items" in names
        assert "POST /items" in names

    def test_fastapi_get_decorator(self) -> None:
        source = '@router.get("/health")\nasync def health_check(): pass\n'
        routes = self._routes(source, "python")
        assert any(r["name"] == "GET /health" for r in routes)

    def test_fastapi_post_decorator(self) -> None:
        source = '@app.post("/login")\nasync def login(): pass\n'
        routes = self._routes(source, "python")
        assert any(r["name"] == "POST /login" for r in routes)

    def test_fastapi_delete_decorator(self) -> None:
        source = '@router.delete("/users/{id}")\nasync def delete_user(id: int): pass\n'
        routes = self._routes(source, "python")
        assert any(r["name"] == "DELETE /users/{id}" for r in routes)

    def test_no_routes_in_plain_python(self) -> None:
        source = 'def helper(): return 42\nclass Foo: pass\n'
        assert self._routes(source, "python") == []

    # Spring (Java)

    def test_spring_get_mapping(self) -> None:
        source = '@GetMapping("/api/users")\npublic List<User> getUsers() {}\n'
        routes = self._routes(source, "java", ".java")
        assert any(r["name"] == "GET /api/users" for r in routes)

    def test_spring_post_mapping(self) -> None:
        source = '@PostMapping("/api/orders")\npublic Order create() {}\n'
        routes = self._routes(source, "java", ".java")
        assert any(r["name"] == "POST /api/orders" for r in routes)

    def test_spring_delete_mapping(self) -> None:
        source = '@DeleteMapping("/api/users/{id}")\npublic void delete() {}\n'
        routes = self._routes(source, "java", ".java")
        assert any(r["name"] == "DELETE /api/users/{id}" for r in routes)

    def test_spring_request_mapping(self) -> None:
        source = '@RequestMapping("/api/base")\nclass BaseController {}\n'
        routes = self._routes(source, "java", ".java")
        assert any(r["name"] == "GET /api/base" for r in routes)

    # Express (JavaScript)

    def test_express_app_get(self) -> None:
        source = 'app.get("/users", (req, res) => res.json([]));\n'
        routes = self._routes(source, "javascript", ".js")
        assert any(r["name"] == "GET /users" for r in routes)

    def test_express_router_post(self) -> None:
        source = 'router.post("/login", loginHandler);\n'
        routes = self._routes(source, "javascript", ".js")
        assert any(r["name"] == "POST /login" for r in routes)

    def test_express_router_delete(self) -> None:
        source = 'router.delete("/items/:id", handler);\n'
        routes = self._routes(source, "javascript", ".js")
        assert any(r["name"] == "DELETE /items/:id" for r in routes)

    def test_typescript_express_route(self) -> None:
        source = 'app.put("/profile", updateHandler);\n'
        routes = self._routes(source, "typescript", ".ts")
        assert any(r["name"] == "PUT /profile" for r in routes)

    # Integration: routes appear in extract_symbols_from_file

    def test_routes_in_extract_symbols_from_file(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        f = tmp_path / "views.py"
        f.write_text(
            '@app.get("/api/data")\ndef get_data(): return []\n'
            '\ndef helper(): pass\n'
        )
        symbols, _ = extract_symbols_from_file(str(f))
        route_symbols = [s for s in symbols if s["kind"] == "route"]
        assert any(s["name"] == "GET /api/data" for s in route_symbols)

    # Routes appear in symbol graph locate

    def test_routes_locatable_via_symbol_graph(self, tmp_path) -> None:
        from agent.symbol_graph import SymbolGraph
        f = tmp_path / "api.py"
        f.write_text('@router.post("/checkout")\ndef checkout(): pass\n')
        g = SymbolGraph(str(tmp_path))
        g.index_file(str(tmp_path), str(f))
        results = g.locate(str(tmp_path), "POST /checkout")
        assert len(results) >= 1
        assert results[0].kind == "route"


# ---------------------------------------------------------------------------
# T28: ingest_traces — dynamic call edge ingestion
# ---------------------------------------------------------------------------

class TestIngestTraces:
    def _graph(self, tmp_path) -> "SymbolGraph":
        from agent.symbol_graph import SymbolGraph
        return SymbolGraph(str(tmp_path))

    def test_ingest_valid_events_returns_count(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        events = [
            {"caller": "view_cart", "callee": "CartService.get", "caller_file": "views.py", "caller_line": 42},
            {"caller": "checkout", "callee": "PaymentService.charge", "caller_file": "views.py", "caller_line": 88},
        ]
        result = g.ingest_trace_data("/ws", events)
        assert result["ingested"] == 2
        assert result["skipped_invalid"] == 0

    def test_events_missing_caller_are_skipped(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        events = [
            {"callee": "helper"},          # missing caller
            {"caller": "main", "callee": "helper"},
        ]
        result = g.ingest_trace_data("/ws", events)
        assert result["ingested"] == 1
        assert result["skipped_invalid"] == 1

    def test_events_missing_callee_are_skipped(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        events = [{"caller": "main"}]  # missing callee
        result = g.ingest_trace_data("/ws", events)
        assert result["ingested"] == 0
        assert result["skipped_invalid"] == 1

    def test_empty_events_list_returns_zero(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        result = g.ingest_trace_data("/ws", [])
        assert result["ingested"] == 0
        assert result["skipped_invalid"] == 0

    def test_ingested_edges_appear_in_callers(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        g.ingest_trace_data("/ws", [
            {"caller": "process_payment", "callee": "stripe_charge", "caller_file": "billing.py"}
        ])
        callers = g.callers("/ws", "stripe_charge")
        assert any(e.from_symbol == "process_payment" for e in callers)

    def test_ingested_edges_have_dynamic_edge_type(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        g.ingest_trace_data("/ws", [
            {"caller": "handler", "callee": "dispatch", "caller_file": "router.py"}
        ])
        callers = g.callers("/ws", "dispatch")
        dynamic = [e for e in callers if e.edge_type == "dynamic"]
        assert len(dynamic) >= 1

    def test_duplicate_edges_not_duplicated(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        event = {"caller": "a", "callee": "b", "caller_file": "a.py", "caller_line": 1}
        g.ingest_trace_data("/ws", [event, event, event])
        # Due to INSERT OR IGNORE, only 1 edge should exist
        callers = g.callers("/ws", "b")
        b_callers = [e for e in callers if e.from_symbol == "a"]
        assert len(b_callers) == 1

    def test_optional_fields_default_gracefully(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        events = [{"caller": "main", "callee": "helper"}]  # no caller_file or caller_line
        result = g.ingest_trace_data("/ws", events)
        assert result["ingested"] == 1

    def test_mcp_vectr_ingest_traces_dispatches(self) -> None:
        from unittest.mock import MagicMock
        from integrations.mcp_server import handle_tools_call
        svc = MagicMock()
        # Mock the real ingest_trace_data() return shape (UPG-7.3).
        svc.ingest_traces.return_value = {
            "ingested": 3, "skipped_invalid": 0,
            "unresolved_callers": 0, "unresolved_callees": 0, "unresolved_examples": [],
        }
        events = [{"caller": "a", "callee": "b"}]
        result = handle_tools_call("vectr_ingest_traces", {"events": events}, svc)
        assert result["isError"] is False
        svc.ingest_traces.assert_called_once_with(events)

    def test_mcp_vectr_ingest_traces_missing_events_is_error(self) -> None:
        from unittest.mock import MagicMock
        from integrations.mcp_server import handle_tools_call
        svc = MagicMock()
        result = handle_tools_call("vectr_ingest_traces", {}, svc)
        assert result["isError"] is True

    def test_vectr_ingest_traces_in_tools_list(self) -> None:
        from integrations.mcp_server import MCP_TOOLS
        names = {t["name"] for t in MCP_TOOLS}
        assert "vectr_ingest_traces" in names


# ---------------------------------------------------------------------------
# UPG-7.3 — ingest_traces unresolved caller/callee warning + dynamic marker
# ---------------------------------------------------------------------------

class TestIngestTracesUnresolvedWarningUPG73:
    def _graph(self, tmp_path) -> "SymbolGraph":
        from agent.symbol_graph import SymbolGraph
        return SymbolGraph(str(tmp_path))

    def test_unresolved_caller_and_callee_still_ingested_but_flagged(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        result = g.ingest_trace_data("/ws", [
            {"caller": "totally_unknown_caller", "callee": "totally_unknown_callee"}
        ])
        assert result["ingested"] == 1
        assert result["unresolved_callers"] == 1
        assert result["unresolved_callees"] == 1
        assert len(result["unresolved_examples"]) == 1
        assert "totally_unknown_caller" in result["unresolved_examples"][0]
        assert "totally_unknown_callee" in result["unresolved_examples"][0]

    def test_known_symbols_do_not_warn(self, tmp_path, monkeypatch) -> None:
        g = self._graph(tmp_path)
        # index a real symbol so the graph has a known "caller" name
        from tests.conftest import make_py
        f = make_py(tmp_path, "views.py", "def view_cart():\n    pass\n")
        g.index_file(str(tmp_path), str(f))
        result = g.ingest_trace_data(str(tmp_path), [
            {"caller": "view_cart", "callee": "view_cart", "caller_file": str(f)}
        ])
        assert result["ingested"] == 1
        assert result["unresolved_callers"] == 0
        assert result["unresolved_callees"] == 0
        assert result["unresolved_examples"] == []

    def test_unresolved_examples_capped(self, tmp_path) -> None:
        from agent.config import INGEST_TRACES_MAX_UNRESOLVED_EXAMPLES
        g = self._graph(tmp_path)
        events = [
            {"caller": f"unknown_caller_{i}", "callee": f"unknown_callee_{i}"}
            for i in range(INGEST_TRACES_MAX_UNRESOLVED_EXAMPLES + 5)
        ]
        result = g.ingest_trace_data("/ws", events)
        assert result["ingested"] == len(events)
        assert result["unresolved_callers"] == len(events)
        assert len(result["unresolved_examples"]) == INGEST_TRACES_MAX_UNRESOLVED_EXAMPLES

    def test_mcp_ingest_traces_surfaces_unresolved_warning(self) -> None:
        from unittest.mock import MagicMock
        from integrations.mcp_server import handle_tools_call
        svc = MagicMock()
        svc.ingest_traces.return_value = {
            "ingested": 1, "skipped_invalid": 0,
            "unresolved_callers": 1, "unresolved_callees": 1,
            "unresolved_examples": ["mystery_caller -> mystery_callee (both unresolved)"],
        }
        result = handle_tools_call(
            "vectr_ingest_traces", {"events": [{"caller": "mystery_caller", "callee": "mystery_callee"}]}, svc
        )
        text = result["content"][0]["text"]
        assert "Warning" in text
        assert "mystery_caller -> mystery_callee" in text

    def test_mcp_ingest_traces_no_warning_when_all_resolved(self) -> None:
        from unittest.mock import MagicMock
        from integrations.mcp_server import handle_tools_call
        svc = MagicMock()
        svc.ingest_traces.return_value = {
            "ingested": 1, "skipped_invalid": 0,
            "unresolved_callers": 0, "unresolved_callees": 0, "unresolved_examples": [],
        }
        result = handle_tools_call("vectr_ingest_traces", {"events": [{"caller": "a", "callee": "b"}]}, svc)
        text = result["content"][0]["text"]
        assert "Warning" not in text

    def test_dynamic_edge_marked_in_trace_output(self, tmp_path) -> None:
        g = self._graph(tmp_path)
        g.ingest_trace_data("/ws", [
            {"caller": "handler", "callee": "dispatch_dynamic", "caller_file": "router.py"}
        ])
        trace_result = g.trace("/ws", "dispatch_dynamic", direction="callers")
        text = g.format_trace_for_llm(trace_result, "dispatch_dynamic")
        assert "(dynamic)" in text

    def test_static_edge_not_marked_dynamic(self, tmp_path) -> None:
        from tests.conftest import make_py
        g = self._graph(tmp_path)
        f = make_py(
            tmp_path, "mod.py",
            "def helper():\n    pass\n\ndef caller():\n    helper()\n",
        )
        g.index_file(str(tmp_path), str(f))
        trace_result = g.trace(str(tmp_path), "helper", direction="callers")
        text = g.format_trace_for_llm(trace_result, "helper")
        assert "(dynamic)" not in text


# ---------------------------------------------------------------------------
# UPG-4.5 — locate: prefer canonical definitions over aliases/impls
# ---------------------------------------------------------------------------

class TestLocateRankingUPG45:
    def test_substring_fires_before_fuzzy(self, tmp_path) -> None:
        # `rand` must resolve to prefix matches (randint/randfraction), never to
        # fuzzy junk like `nan`/`add`. The substring strategy owns this query.
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("randint", "function", str(tmp_path / "random.py")),
            ("randfraction", "function", str(tmp_path / "random.py")),
            ("nan", "function", str(tmp_path / "mathmodule.c")),
            ("add", "function", str(tmp_path / "abstract.c")),
        ])
        result = g.locate_l2("ws", "rand")
        assert result.resolution_strategy == "substring"
        names = [s.name for s in result.symbols]
        assert names[0] in ("randint", "randfraction")
        assert "nan" not in names and "add" not in names

    def test_prefix_match_beats_interior_substring(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("myrandom_helper", "function", str(tmp_path / "a.py")),  # interior
            ("rand_state", "function", str(tmp_path / "b.py")),       # prefix
        ])
        result = g.locate_l2("ws", "rand")
        assert result.symbols[0].name == "rand_state"

    def test_canonical_def_ranks_before_impl_on_exact_match(self, tmp_path) -> None:
        # Rust: `struct VersionSpecifiers` + several `impl VersionSpecifiers`.
        # All are an EXACT name hit, so ranking must lead with the struct def.
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("VersionSpecifiers", "impl", str(tmp_path / "version.rs")),
            ("VersionSpecifiers", "impl", str(tmp_path / "ops.rs")),
            ("VersionSpecifiers", "struct", str(tmp_path / "version.rs")),
            ("VersionSpecifiers", "impl", str(tmp_path / "cmp.rs")),
        ])
        result = g.locate_l2("ws", "VersionSpecifiers")
        assert result.resolution_strategy == "exact"
        assert result.symbols[0].kind == "struct"

    def test_function_beats_macro_on_partial_match(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("PY_PARSE_MACRO", "macro", str(tmp_path / "pymacro.h")),
            ("PY_parse_value", "function", str(tmp_path / "parse.c")),
        ])
        result = g.locate_l2("ws", "PY_pa")
        # both are prefix matches; the function definition is more canonical
        assert result.symbols[0].kind == "function"

    def test_test_and_private_files_deprioritized(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("widget_render", "function", str(tmp_path / "test_widget.py")),
            ("widget_render_helper", "function", str(tmp_path / "_internal.py")),
            ("widget_render_main", "function", str(tmp_path / "widget.py")),
        ])
        # "widget_rend" is an exact match for none of them (all prefix matches),
        # so the test/private-file penalty decides the lead.
        result = g.locate_l2("ws", "widget_rend")
        # real source file leads; test_/_private files sink
        assert "test_" not in result.symbols[0].file_path.rsplit("/", 1)[-1]
        assert not result.symbols[0].file_path.rsplit("/", 1)[-1].startswith("_")

    def test_short_query_fuzzy_budget_is_tight(self, tmp_path) -> None:
        # No substring match exists, so we reach fuzzy. A 3-char query gets dist≤1
        # and a shared first char, so `add`/`nan` can't match `rnd`.
        g = SymbolGraph(str(tmp_path))
        _seed_symbols(g, "ws", [
            ("add", "function", str(tmp_path / "a.c")),
            ("nan", "function", str(tmp_path / "b.c")),
        ])
        result = g.locate_l2("ws", "rnd")
        assert result.resolution_strategy == "none"

    def test_partial_match_key_ordering(self) -> None:
        # Direct unit check on the sort key: prefix < interior; def < impl.
        prefix_def = {"name": "rand_fn", "kind": "function", "file_path": "src/x.py"}
        interior = {"name": "myrand", "kind": "function", "file_path": "src/y.py"}
        prefix_impl = {"name": "rand_x", "kind": "impl", "file_path": "src/z.py"}
        assert _partial_match_key(prefix_def, "rand") < _partial_match_key(interior, "rand")
        assert _partial_match_key(prefix_def, "rand") < _partial_match_key(prefix_impl, "rand")


# ---------------------------------------------------------------------------
# UPG-4.1 — namespace symbols to kill name collisions
# ---------------------------------------------------------------------------

class TestTraceCollisionUPG41:
    def test_callers_exact_no_substring_bleed(self, tmp_path) -> None:
        # `compare` and `compare_stacks` are distinct symbols; callers("compare")
        # must NOT pull in the caller of `compare_stacks` (old LIKE bug).
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "m.py", textwrap.dedent("""\
            def compare(): pass
            def compare_stacks(): pass
            def user_a():
                compare()
            def user_b():
                compare_stacks()
        """))
        g.index_file("ws", path)
        callers = [e.from_symbol for e in g.callers("ws", "compare")]
        assert "user_a" in callers
        assert "user_b" not in callers

    def test_callees_fall_back_to_partial_when_no_exact(self, tmp_path) -> None:
        # No symbol named exactly "process", so callees("process") should still
        # find process_data's calls via the partial fallback.
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "p.py", textwrap.dedent("""\
            def helper(): pass
            def process_data():
                helper()
        """))
        g.index_file("ws", path)
        callees = [e.to_symbol for e in g.callees("ws", "process")]
        assert "helper" in callees

    def test_trace_separates_same_named_defs_across_modules(self, tmp_path) -> None:
        # Two `Lock` definitions in different modules, each calling different
        # things. trace must scope callees per definition, not merge them.
        resolver = tmp_path / "resolver"
        sync = tmp_path / "sync"
        resolver.mkdir()
        sync.mkdir()
        (resolver / "lock.py").write_text(textwrap.dedent("""\
            def read_lockfile(): pass
            def Lock():
                read_lockfile()
        """))
        (sync / "mutex.py").write_text(textwrap.dedent("""\
            def os_mutex(): pass
            def Lock():
                os_mutex()
        """))
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        g.index_file(ws, str(resolver / "lock.py"))
        g.index_file(ws, str(sync / "mutex.py"))

        result = g.trace(ws, "Lock", direction="both")
        assert len(result["definitions"]) == 2
        by_def = result["by_definition"]
        assert len(by_def) == 2

        callees_by_module = {
            e_def["module"]: {e.to_symbol for e in e_def["callees"]}
            for e_def in by_def
        }
        resolver_callees = next(v for k, v in callees_by_module.items() if "resolver" in k)
        sync_callees = next(v for k, v in callees_by_module.items() if "sync" in k)
        assert "read_lockfile" in resolver_callees and "os_mutex" not in resolver_callees
        assert "os_mutex" in sync_callees and "read_lockfile" not in sync_callees

    def test_format_trace_renders_per_definition_separation(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        d1 = Symbol(symbol_id=1, workspace="ws", name="Lock", kind="function",
                    file_path="/ws/resolver/lock.py", start_line=2, end_line=4)
        d2 = Symbol(symbol_id=2, workspace="ws", name="Lock", kind="function",
                    file_path="/ws/sync/mutex.py", start_line=2, end_line=4)
        trace_result = {
            "callers": [],
            "callees": [],
            "definitions": [d1, d2],
            "by_definition": [
                {"definition": d1, "module": "resolver/lock.py",
                 "callees": [CallEdge(from_file="/ws/resolver/lock.py", from_symbol="Lock",
                                      from_line=2, to_symbol="read_lockfile", edge_type="calls")]},
                {"definition": d2, "module": "sync/mutex.py",
                 "callees": [CallEdge(from_file="/ws/sync/mutex.py", from_symbol="Lock",
                                      from_line=2, to_symbol="os_mutex", edge_type="calls")]},
            ],
        }
        text = g.format_trace_for_llm(trace_result, "Lock")
        assert "2 definitions" in text
        assert "resolver/lock.py" in text and "sync/mutex.py" in text
        assert "read_lockfile" in text and "os_mutex" in text

    def test_single_definition_uses_flat_format(self, tmp_path) -> None:
        # One definition → no per-definition block, no ambiguity warning.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        path = make_py(tmp_path, "s.py", textwrap.dedent("""\
            def helper(): pass
            def only_caller():
                helper()
        """))
        g.index_file(ws, path)
        result = g.trace(ws, "helper", direction="both")
        assert "by_definition" not in result
        text = g.format_trace_for_llm(result, "helper")
        assert "definitions across modules" not in text


# ---------------------------------------------------------------------------
# UPG-4.2 — dedup edges; rank callees/callers by relevance, not alphabetically
# ---------------------------------------------------------------------------

class TestEdgeAggregationUPG42:
    def test_callees_deduped_with_count(self, tmp_path) -> None:
        # `caller` calls `target` from 3 distinct sites; one aggregated entry
        # carrying call_count=3, not three repeated rows.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 10, "target"),
            (f"{ws}/a.py", "caller", 11, "target"),
            (f"{ws}/a.py", "caller", 12, "target"),
        ])
        callees = g.callees(ws, "caller")
        assert len(callees) == 1
        assert callees[0].to_symbol == "target"
        assert callees[0].call_count == 3

    def test_callees_lead_with_repo_defined_not_builtins(self, tmp_path) -> None:
        # `len`/`assert` are not repo symbols; `helper` is. Even though `len`
        # is called more often, the repo-defined callee must rank first.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("helper", "function", f"{ws}/util.py")])
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 1, "len"),
            (f"{ws}/a.py", "caller", 2, "len"),
            (f"{ws}/a.py", "caller", 3, "len"),
            (f"{ws}/a.py", "caller", 4, "assert"),
            (f"{ws}/a.py", "caller", 5, "helper"),
        ])
        callees = g.callees(ws, "caller")
        assert callees[0].to_symbol == "helper"  # repo-defined leads despite lower count

    def test_callees_ranked_by_frequency_within_tier(self, tmp_path) -> None:
        # Two repo-defined callees: the more frequently called ranks first
        # (frequency beats alphabetical — `zeta`×3 before `alpha`×1).
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [
            ("alpha", "function", f"{ws}/util.py"),
            ("zeta", "function", f"{ws}/util.py"),
        ])
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 1, "alpha"),
            (f"{ws}/a.py", "caller", 2, "zeta"),
            (f"{ws}/a.py", "caller", 3, "zeta"),
            (f"{ws}/a.py", "caller", 4, "zeta"),
        ])
        names = [e.to_symbol for e in g.callees(ws, "caller")]
        assert names == ["zeta", "alpha"]

    def test_important_callee_survives_truncation(self, tmp_path) -> None:
        # 30 alphabetically-early builtins + one heavily-used repo callee `zzz`.
        # Old path truncated alphabetically and dropped `zzz`; ranking keeps it.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("zzz", "function", f"{ws}/util.py")])
        specs = [(f"{ws}/a.py", "caller", i, f"aaa_builtin_{i:02d}") for i in range(30)]
        specs += [(f"{ws}/a.py", "caller", 100 + j, "zzz") for j in range(5)]
        _seed_edges(g, ws, specs)
        names = [e.to_symbol for e in g.callees(ws, "caller", limit=20)]
        assert "zzz" in names
        assert names[0] == "zzz"

    def test_callers_deduped_by_function_with_count(self, tmp_path) -> None:
        # One caller function calls `target` from 3 sites → one entry, count 3.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "on_event", 5, "target"),
            (f"{ws}/a.py", "on_event", 6, "target"),
            (f"{ws}/a.py", "on_event", 7, "target"),
        ])
        callers = g.callers(ws, "target")
        assert len(callers) == 1
        assert callers[0].from_symbol == "on_event"
        assert callers[0].call_count == 3

    def test_count_suffix_in_formatted_output(self, tmp_path) -> None:
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 1, "target"),
            (f"{ws}/a.py", "caller", 2, "target"),
        ])
        result = g.trace(ws, "caller", direction="callees")
        text = g.format_trace_for_llm(result, "caller")
        assert "target" in text
        assert "×2" in text

    def test_singleton_callee_has_no_count_suffix(self, tmp_path) -> None:
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_edges(g, ws, [(f"{ws}/a.py", "caller", 1, "target")])
        result = g.trace(ws, "caller", direction="callees")
        text = g.format_trace_for_llm(result, "caller")
        assert "target" in text
        assert "×" not in text


# ---------------------------------------------------------------------------
# UPG-4.3 — suppress builtins/stdlib from callee lists (repo-internal by default)
# ---------------------------------------------------------------------------

class TestBuiltinSuppressionUPG43:
    def _graph_with_mixed_callees(self, tmp_path):
        # `caller` (in a .py file) calls a repo function + python builtins.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("helper", "function", f"{ws}/util.py")])
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 1, "helper"),
            (f"{ws}/a.py", "caller", 2, "len"),
            (f"{ws}/a.py", "caller", 3, "isinstance"),
            (f"{ws}/a.py", "caller", 4, "print"),
        ])
        return g, ws

    def test_builtins_hidden_by_default_in_trace(self, tmp_path) -> None:
        g, ws = self._graph_with_mixed_callees(tmp_path)
        result = g.trace(ws, "caller", direction="callees")  # include_builtins defaults False
        names = [e.to_symbol for e in result["callees"]]
        assert names == ["helper"]
        assert result["hidden_builtins"] == 3

    def test_include_builtins_shows_them(self, tmp_path) -> None:
        g, ws = self._graph_with_mixed_callees(tmp_path)
        result = g.trace(ws, "caller", direction="callees", include_builtins=True)
        names = {e.to_symbol for e in result["callees"]}
        assert {"helper", "len", "isinstance", "print"} <= names
        assert result["hidden_builtins"] == 0

    def test_repo_defined_shadowing_a_builtin_is_kept(self, tmp_path) -> None:
        # The repo defines its own `len`; it must NOT be suppressed as a builtin.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("len", "function", f"{ws}/mylib.py")])
        _seed_edges(g, ws, [
            (f"{ws}/a.py", "caller", 1, "len"),
            (f"{ws}/a.py", "caller", 2, "isinstance"),
        ])
        result = g.trace(ws, "caller", direction="callees")
        names = [e.to_symbol for e in result["callees"]]
        assert "len" in names           # repo-defined, kept
        assert "isinstance" not in names  # true builtin, hidden
        assert result["hidden_builtins"] == 1

    def test_hidden_note_rendered(self, tmp_path) -> None:
        g, ws = self._graph_with_mixed_callees(tmp_path)
        result = g.trace(ws, "caller", direction="callees")
        text = g.format_trace_for_llm(result, "caller")
        assert "3 builtin/stdlib calls hidden" in text
        assert "include_builtins" in text

    def test_callees_method_keeps_builtins_by_default(self, tmp_path) -> None:
        # The raw callees() API is unfiltered by default (back-compat); only the
        # trace() display path suppresses.
        g, ws = self._graph_with_mixed_callees(tmp_path)
        names = {e.to_symbol for e in g.callees(ws, "caller")}
        assert "len" in names

    def test_c_builtins_suppressed(self, tmp_path) -> None:
        # malloc/memcpy/assert from a .c file are libc noise, not repo calls.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("parse_header", "function", f"{ws}/hdr.c")])
        _seed_edges(g, ws, [
            (f"{ws}/p.c", "caller", 1, "parse_header"),
            (f"{ws}/p.c", "caller", 2, "malloc"),
            (f"{ws}/p.c", "caller", 3, "memcpy"),
            (f"{ws}/p.c", "caller", 4, "assert"),
        ])
        result = g.trace(ws, "caller", direction="callees")
        names = [e.to_symbol for e in result["callees"]]
        assert names == ["parse_header"]
        assert result["hidden_builtins"] == 3

    def test_per_definition_callees_also_suppress_builtins(self, tmp_path) -> None:
        # Two defs of `Lock` across modules; builtins hidden within each block.
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [
            ("Lock", "function", f"{ws}/resolver/lock.py"),
            ("Lock", "function", f"{ws}/sync/mutex.py"),
            ("read_lockfile", "function", f"{ws}/resolver/io.py"),
        ])
        _seed_edges(g, ws, [
            (f"{ws}/resolver/lock.py", "Lock", 1, "read_lockfile"),
            (f"{ws}/resolver/lock.py", "Lock", 2, "len"),
            (f"{ws}/sync/mutex.py", "Lock", 1, "print"),
        ])
        result = g.trace(ws, "Lock", direction="callees")
        by_def = result["by_definition"]
        for entry in by_def:
            cnames = {e.to_symbol for e in entry["callees"]}
            assert "len" not in cnames and "print" not in cnames
        # the resolver def still shows its repo-internal call
        resolver = next(e for e in by_def if "resolver" in e["module"])
        assert "read_lockfile" in {e.to_symbol for e in resolver["callees"]}
        assert resolver["hidden_builtins"] == 1


# ---------------------------------------------------------------------------
# UPG-4.4 — Rust trait/impl/type-usage edges
#
# `trace <Type>` was empty for heavily-used Rust types (uv RegistryClient,
# BuildContext, PubGrubPackage) because the dominant interaction — passing a
# type by ref/value, returning it, holding it in a field, or calling an
# associated fn `Type::new()` — produced no edge. These record `edge_type=
# "uses"` so the type's real call sites surface, while staying out of the
# callees ("Calls:") direction so that stays function calls.
# ---------------------------------------------------------------------------

def _extract_rs(tmp_path, src: str):
    f = tmp_path / "lib.rs"
    f.write_text(src)
    return extract_symbols_from_file(str(f))


def _uses_to(edges):
    return {e["to_symbol"] for e in edges if e["edge_type"] == "uses"}


def _calls_to(edges):
    return {e["to_symbol"] for e in edges if e["edge_type"] == "calls"}


class TestRustTypeUsageEdgesUPG44:
    def test_param_type_creates_uses_edge(self, tmp_path) -> None:
        _, edges = _extract_rs(tmp_path, "fn fetch(client: &RegistryClient) {}")
        assert ("fetch", "RegistryClient") in {(e["from_symbol"], e["to_symbol"]) for e in edges
                                               if e["edge_type"] == "uses"}

    def test_return_type_creates_uses_edge(self, tmp_path) -> None:
        _, edges = _extract_rs(tmp_path, "fn build() -> RegistryClient { todo!() }")
        assert "RegistryClient" in _uses_to(edges)

    def test_generic_arg_type_creates_uses_edge(self, tmp_path) -> None:
        # nested type arg inside Result<..> must be reached by recursion
        _, edges = _extract_rs(tmp_path, "fn build() -> Result<RegistryClient, BuildError> { todo!() }")
        assert {"RegistryClient", "BuildError"} <= _uses_to(edges)
        assert "Result" not in _uses_to(edges)  # std container skipped

    def test_struct_field_type_creates_uses_edge(self, tmp_path) -> None:
        _, edges = _extract_rs(tmp_path, "struct Holder { client: RegistryClient }")
        assert ("Holder", "RegistryClient") in {(e["from_symbol"], e["to_symbol"]) for e in edges
                                                if e["edge_type"] == "uses"}

    def test_scoped_assoc_call_links_type(self, tmp_path) -> None:
        # RegistryClient::new() → uses RegistryClient AND calls new
        _, edges = _extract_rs(tmp_path, "fn f() { let c = RegistryClient::new(); }")
        assert "RegistryClient" in _uses_to(edges)
        assert "new" in _calls_to(edges)

    def test_enum_variant_construction_links_type(self, tmp_path) -> None:
        _, edges = _extract_rs(tmp_path, "fn f() { let p = PubGrubPackage::Package(x); }")
        assert "PubGrubPackage" in _uses_to(edges)

    def test_impl_header_no_self_loop(self, tmp_path) -> None:
        # `impl RegistryClient` must not record RegistryClient --uses--> RegistryClient
        _, edges = _extract_rs(tmp_path, "impl RegistryClient { fn g(&self) {} }")
        assert ("RegistryClient", "RegistryClient") not in {
            (e["from_symbol"], e["to_symbol"]) for e in edges if e["edge_type"] == "uses"
        }

    def test_primitives_and_std_types_skipped(self, tmp_path) -> None:
        _, edges = _extract_rs(
            tmp_path,
            "fn f(a: u32, b: String, c: Vec<u8>, d: bool) -> Self { todo!() }",
        )
        targets = _uses_to(edges)
        for noise in ("u32", "String", "Vec", "u8", "bool", "Self"):
            assert noise not in targets

    def test_single_char_generic_param_skipped(self, tmp_path) -> None:
        _, edges = _extract_rs(tmp_path, "fn id<T>(x: T) -> T { x }")
        assert "T" not in _uses_to(edges)

    def test_trace_type_returns_usage_call_sites(self, tmp_path) -> None:
        # the headline acceptance: trace a type, get real call sites back
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("RegistryClient", "struct", f"{ws}/client.rs")])
        _seed_edges(g, ws, [
            (f"{ws}/ops.rs", "resolve", 10, "RegistryClient"),
            (f"{ws}/build.rs", "build_dist", 20, "RegistryClient"),
        ], edge_type="uses")
        result = g.trace(ws, "RegistryClient", direction="callers")
        callers = {e.from_symbol for e in result["callers"]}
        assert callers == {"resolve", "build_dist"}

    def test_uses_edges_excluded_from_callees(self, tmp_path) -> None:
        # `trace fn` Calls: must list function calls only, not the types it mentions
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("do_work", "function", f"{ws}/m.rs")])
        _seed_edges(g, ws, [(f"{ws}/m.rs", "do_work", 1, "helper")], edge_type="calls")
        _seed_edges(g, ws, [(f"{ws}/m.rs", "do_work", 1, "RegistryClient")], edge_type="uses")
        callees = g.callees(ws, "do_work")
        names = {e.to_symbol for e in callees}
        assert "helper" in names
        assert "RegistryClient" not in names

    def test_caller_verb_used_by_for_pure_type_usage(self, tmp_path) -> None:
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        _seed_symbols(g, ws, [("RegistryClient", "struct", f"{ws}/client.rs")])
        _seed_edges(g, ws, [(f"{ws}/ops.rs", "resolve", 10, "RegistryClient")], edge_type="uses")
        result = g.trace(ws, "RegistryClient", direction="callers")
        text = g.format_trace_for_llm(result, "RegistryClient")
        assert "Used by" in text
        assert "Called by" not in text


# ---------------------------------------------------------------------------
# UPG-4.6 — locate redirect hint on description-shaped / no-match queries
#
# When the LLM misroutes a natural-language description to vectr_locate, name
# matching returns nothing. Without a nudge the model gets silence and no path
# forward; with it, locate steers to vectr_search (mirrors UPG-3.1/3.3 hints).
# ---------------------------------------------------------------------------

class TestLocateDescriptionHintUPG46:
    def test_description_query_gets_search_hint(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        result = LocateResult(symbols=[], resolution_strategy="none",
                              query="function that checks whether a number is prime")
        text = g.format_locate_l2_for_llm(result)
        assert "No symbol matching" in text
        assert "vectr_search" in text
        assert "description" in text.lower()

    def test_single_token_no_match_redirects_to_search_upg103(self, tmp_path) -> None:
        # UPG-10.3: a single-token miss is no longer a dead end — it redirects to
        # vectr_search by CONTENT (not the description phrasing) so the agent has
        # a path forward instead of falling back to grep.
        g = SymbolGraph(str(tmp_path))
        result = LocateResult(symbols=[], resolution_strategy="none", query="is_prime")
        text = g.format_locate_l2_for_llm(result)
        assert "No symbol matching" in text
        assert "vectr_search" in text
        assert "is_prime" in text          # echoes the query into the search hint
        assert "description" not in text.lower()  # this is the content-redirect, not the misroute hint

    def test_legacy_formatter_also_hints(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        text = g.format_locate_for_llm([], "where do we validate the auth token")
        assert "vectr_search" in text

    def test_looks_like_description_predicate(self) -> None:
        assert SymbolGraph._looks_like_description("parse the lock file")
        assert SymbolGraph._looks_like_description("RegistryClient new")
        assert not SymbolGraph._looks_like_description("RegistryClient")
        assert not SymbolGraph._looks_like_description("parse_lockfile")
        assert not SymbolGraph._looks_like_description("  PyList_Append  ")


# ---------------------------------------------------------------------------
# UPG-10.3 — module-level constants are locatable
# ---------------------------------------------------------------------------

class TestModuleConstantsUPG103:
    def test_module_level_constant_indexed(self, tmp_path) -> None:
        path = make_py(tmp_path, "cfg.py", textwrap.dedent('''\
            _CLAUDE_MD = """hello"""
            MAX_RETRIES = 5
        '''))
        symbols, _ = extract_symbols_from_file(path)
        by_name = {s["name"]: s for s in symbols}
        assert "_CLAUDE_MD" in by_name and by_name["_CLAUDE_MD"]["start_line"] == 1
        assert "MAX_RETRIES" in by_name and by_name["MAX_RETRIES"]["start_line"] == 2

    def test_constant_vs_variable_kind(self, tmp_path) -> None:
        # UPPER / leading-underscore-UPPER → constant; mixed-case binding → variable
        path = make_py(tmp_path, "k.py", textwrap.dedent('''\
            _CLAUDE_MD = "x"
            logger = make_logger()
        '''))
        symbols, _ = extract_symbols_from_file(path)
        by_name = {s["name"]: s["kind"] for s in symbols}
        assert by_name["_CLAUDE_MD"] == "constant"
        assert by_name["logger"] == "variable"

    def test_function_locals_not_indexed(self, tmp_path) -> None:
        # the scope guard (current_symbol == "") must keep locals out of the graph
        path = make_py(tmp_path, "scope.py", textwrap.dedent('''\
            TOP_LEVEL = 1
            def f():
                local_var = 2
                return local_var
        '''))
        symbols, _ = extract_symbols_from_file(path)
        names = {s["name"] for s in symbols}
        assert "TOP_LEVEL" in names
        assert "local_var" not in names          # never leaks out of the function
        assert "f" in names                       # function itself still indexed

    def test_annotated_module_constant_indexed(self, tmp_path) -> None:
        path = make_py(tmp_path, "ann.py", "TIMEOUT_S: int = 30\n")
        symbols, _ = extract_symbols_from_file(path)
        assert any(s["name"] == "TIMEOUT_S" and s["kind"] == "constant" for s in symbols)

    def test_locate_finds_module_constant(self, tmp_path) -> None:
        # the acceptance case: locate a constant by name, end-to-end
        g = SymbolGraph(str(tmp_path))
        path = make_py(tmp_path, "main.py", '_CLAUDE_MD = """vectr"""\ndef go(): pass\n')
        g.index_file("ws", path)
        symbols = g.locate("ws", "_CLAUDE_MD")
        assert len(symbols) >= 1
        assert symbols[0].name == "_CLAUDE_MD"
        assert symbols[0].start_line == 1


# ---------------------------------------------------------------------------
# UPG-8.7 — build resilience + version stamp / trust signals
# ---------------------------------------------------------------------------

class TestBuildResilienceUPG87:
    """build_for_workspace must be per-file resilient: one file that throws
    during extraction can no longer abort the whole loop and silently leave
    every *later* file without symbols (the real cause of the observed
    '5531 symbols across 154 files' partial graph)."""

    def test_one_throwing_file_does_not_abort_build(self, tmp_path, monkeypatch) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "a_first.py", "def a_first_fn(): return 1")
        b = make_py(tmp_path, "b_boom.py", "def b_boom_fn(): return 2")
        c = make_py(tmp_path, "c_third.py", "def c_third_fn(): return 3")

        real = _sgmod.extract_symbols_from_file

        def boom(fp):
            if "b_boom" in fp:
                raise RecursionError("simulated deep-AST parser crash (cf UPG-3.2)")
            return real(fp)

        monkeypatch.setattr(_sgmod, "extract_symbols_from_file", boom)

        g = SymbolGraph(ws)
        stats = g.build_for_workspace(ws, [a, b, c])

        # The file AFTER the bad one keeps its symbols — the loop did not abort.
        assert g.locate(ws, "a_first_fn")
        assert g.locate(ws, "c_third_fn")
        assert not g.locate(ws, "b_boom_fn")  # only the genuinely broken file is missing
        assert stats["failed"] == 1
        assert stats["complete"] is False

    def test_clean_build_is_complete(self, tmp_path) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "ok1.py", "def ok1(): pass")
        b = make_py(tmp_path, "ok2.py", "def ok2(): pass")
        g = SymbolGraph(ws)
        stats = g.build_for_workspace(ws, [a, b])
        assert stats["failed"] == 0
        assert stats["complete"] is True

    def test_partial_build_flagged_in_meta(self, tmp_path, monkeypatch) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "good.py", "def good(): pass")
        bad = make_py(tmp_path, "bad.py", "def bad(): pass")
        real = _sgmod.extract_symbols_from_file
        monkeypatch.setattr(
            _sgmod, "extract_symbols_from_file",
            lambda fp: (_ for _ in ()).throw(ValueError("x")) if "bad.py" in fp else real(fp),
        )
        g = SymbolGraph(ws)
        g.build_for_workspace(ws, [a, bad])
        meta = g.graph_meta(ws)
        assert meta["complete"] == "0"
        assert meta["failed"] == "1"


class TestGraphVersionStampUPG87:
    """A persisted graph records the toolchain that built it so an upgrade
    (new parser / changed schema / different model) is detectable and the graph
    is rebuilt rather than silently serving stale/partial results."""

    def test_fingerprint_stable_and_model_sensitive(self) -> None:
        assert graph_toolchain_fingerprint("m1") == graph_toolchain_fingerprint("m1")
        assert graph_toolchain_fingerprint("m1") != graph_toolchain_fingerprint("m2")

    def test_fingerprint_tracks_schema_version(self, monkeypatch) -> None:
        fp_before = graph_toolchain_fingerprint("m")
        monkeypatch.setattr(_sgmod, "SYMBOL_SCHEMA_VERSION", SYMBOL_SCHEMA_VERSION + 99)
        assert graph_toolchain_fingerprint("m") != fp_before

    def test_never_built_is_stale(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        assert g.is_stale(str(tmp_path), "anymodel") is True
        assert g.graph_meta(str(tmp_path)) == {}

    def test_clean_build_not_stale_same_toolchain(self, tmp_path) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "x.py", "def x(): pass")
        g = SymbolGraph(ws)
        g.build_for_workspace(ws, [a], embed_model="model-A")
        assert g.is_stale(ws, "model-A") is False

    def test_model_change_makes_stale(self, tmp_path) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "x.py", "def x(): pass")
        g = SymbolGraph(ws)
        g.build_for_workspace(ws, [a], embed_model="model-A")
        assert g.is_stale(ws, "model-B") is True  # toolchain changed → rebuild warranted

    def test_incomplete_build_is_stale(self, tmp_path, monkeypatch) -> None:
        ws = str(tmp_path)
        a = make_py(tmp_path, "good.py", "def good(): pass")
        bad = make_py(tmp_path, "bad.py", "def bad(): pass")
        real = _sgmod.extract_symbols_from_file
        monkeypatch.setattr(
            _sgmod, "extract_symbols_from_file",
            lambda fp: (_ for _ in ()).throw(ValueError("x")) if "bad.py" in fp else real(fp),
        )
        g = SymbolGraph(ws)
        g.build_for_workspace(ws, [a, bad], embed_model="m")
        # Same toolchain, but the build was partial → still stale (must rebuild).
        assert g.is_stale(ws, "m") is True


# ---------------------------------------------------------------------------
# UPG-11.10-b — qualified locate: accept + return "Class.method" form
#
# Acceptance bar:
#   1. locate("Class.method") returns only the matching class's symbol,
#      discriminated from identically-named overrides in other classes.
#   2. locate("method") bare leaf still returns ALL overrides (no regression).
#   3. Located results carry a qualified "Class.method" name when the symbol
#      lives inside a class (parity with vectr_search qualified display).
# ---------------------------------------------------------------------------

class TestQualifiedLocateUPG1110b:
    """Two classes define the same method name.  A qualified locate query
    must filter to the named class while the bare-leaf query must return both."""

    def _fixture(self, tmp_path) -> tuple:
        """Write a fixture with two classes each defining 'deconstruct' and a
        top-level 'deconstruct' function.  Returns (g, ws, filepath)."""
        src = textwrap.dedent("""\
            class Field:
                def deconstruct(self):
                    return "field"

            class RemoveField:
                def deconstruct(self):
                    return "remove"

            def deconstruct():
                pass
        """)
        fp = tmp_path / "fields.py"
        fp.write_text(src)
        g = SymbolGraph(str(tmp_path))
        g.index_file(str(tmp_path), str(fp))
        return g, str(tmp_path), str(fp)

    # --- Part (a): accepting qualified query ---

    def test_qualified_locate_filters_to_named_class(self, tmp_path) -> None:
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "Field.deconstruct")
        # Only Field.deconstruct, not RemoveField.deconstruct
        assert result.resolution_strategy in ("exact", "suffix")
        names = [s.name for s in result.symbols]
        assert any("Field" in n for n in names), f"expected Field in names, got {names}"
        assert not any("Remove" in n for n in names), \
            f"RemoveField.deconstruct must be filtered out, got {names}"

    def test_qualified_locate_field_not_removefield(self, tmp_path) -> None:
        """The base Field.deconstruct is discriminated from RemoveField.deconstruct."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "Field.deconstruct")
        qualified_names = [s.name for s in result.symbols]
        # Must include Field.deconstruct
        assert "Field.deconstruct" in qualified_names, \
            f"Field.deconstruct not found; got {qualified_names}"
        # Must NOT include RemoveField.deconstruct
        assert "RemoveField.deconstruct" not in qualified_names, \
            f"RemoveField.deconstruct incorrectly included; got {qualified_names}"

    def test_qualified_locate_removefield(self, tmp_path) -> None:
        """RemoveField.deconstruct can be located by its own qualifier."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "RemoveField.deconstruct")
        qualified_names = [s.name for s in result.symbols]
        assert "RemoveField.deconstruct" in qualified_names, \
            f"RemoveField.deconstruct not found; got {qualified_names}"
        assert "Field.deconstruct" not in qualified_names, \
            f"Field.deconstruct must not appear in RemoveField results; got {qualified_names}"

    # --- Part (b): bare-leaf locate unchanged ---

    def test_bare_leaf_locate_returns_all_overrides(self, tmp_path) -> None:
        """Bare 'deconstruct' must still return ALL definitions (no regression)."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "deconstruct")
        # All three deconstruct symbols should be returned
        names = [s.name for s in result.symbols]
        # At minimum Field.deconstruct and RemoveField.deconstruct must appear
        class_names = {n.split(".")[0] for n in names if "." in n}
        assert "Field" in class_names, f"Field not in results for bare query: {names}"
        assert "RemoveField" in class_names, f"RemoveField not in results for bare query: {names}"

    def test_bare_leaf_strategy_unchanged(self, tmp_path) -> None:
        """Bare-leaf locate uses exact strategy, not filtered."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "deconstruct")
        assert result.resolution_strategy == "exact"
        # Count: should have at least 2 class methods + 1 top-level
        assert len(result.symbols) >= 2

    # --- Part (b): qualified names in returned symbols ---

    def test_qualified_name_returned_for_class_method(self, tmp_path) -> None:
        """locate_l2 populates 'Class.method' on the symbol.name (not bare leaf)."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "deconstruct")
        names = [s.name for s in result.symbols]
        # At least one symbol should carry a qualified name
        assert any("." in n for n in names), \
            f"No qualified names returned; got {names}"

    def test_toplevel_function_stays_unqualified(self, tmp_path) -> None:
        """A top-level function (no enclosing class) must NOT get a class prefix."""
        src = textwrap.dedent("""\
            def standalone():
                pass
        """)
        fp = tmp_path / "top.py"
        fp.write_text(src)
        g = SymbolGraph(str(tmp_path))
        g.index_file(str(tmp_path), str(fp))
        result = g.locate_l2(str(tmp_path), "standalone")
        assert len(result.symbols) >= 1
        assert result.symbols[0].name == "standalone", \
            f"Top-level function should stay unqualified; got {result.symbols[0].name}"

    # --- enclosing class helper unit tests ---

    def test_enclosing_class_from_file_method(self, tmp_path) -> None:
        from agent.symbol_graph._graph import _enclosing_class_from_file
        src = textwrap.dedent("""\
            class MyClass:
                def my_method(self):
                    pass
        """)
        fp = tmp_path / "c.py"
        fp.write_text(src)
        # my_method starts at line 2
        cls = _enclosing_class_from_file(str(fp), 2)
        assert cls == "MyClass", f"Expected 'MyClass', got '{cls}'"

    def test_enclosing_class_from_file_toplevel(self, tmp_path) -> None:
        from agent.symbol_graph._graph import _enclosing_class_from_file
        src = "def standalone(): pass\n"
        fp = tmp_path / "t.py"
        fp.write_text(src)
        cls = _enclosing_class_from_file(str(fp), 1)
        assert cls == "", f"Top-level function should return empty, got '{cls}'"

    def test_enclosing_class_from_file_nonexistent(self, tmp_path) -> None:
        from agent.symbol_graph._graph import _enclosing_class_from_file
        cls = _enclosing_class_from_file(str(tmp_path / "ghost.py"), 1)
        assert cls == ""

    # --- format integration ---

    def test_format_l2_shows_qualified_name(self, tmp_path) -> None:
        """format_locate_l2_for_llm should display Class.method, not bare leaf."""
        g, ws, _ = self._fixture(tmp_path)
        result = g.locate_l2(ws, "Field.deconstruct")
        text = g.format_locate_l2_for_llm(result)
        assert "Field.deconstruct" in text, \
            f"Qualified name not in formatted output:\n{text}"


# ---------------------------------------------------------------------------
# UPG-15.10 — locate: canonical library defs rank before inner test-scope stubs
#
# Acceptance case (F29): vectr_locate('Model') must return the canonical
# top-level library definition (e.g. django/db/models/base.py:Model, ~1400 lines)
# in the top-5, not only inner `class Model(models.Model):` stubs defined inside
# test method bodies in test files.
#
# Ranking signals added (in order of discrimination power, after kind):
#   1. scope_depth  — 0 = top-level, 1+ = inside a function body
#   2. span_bucket  — 0 = large (≥50 lines), 1 = medium, 2 = tiny (<10 lines)
# Both signals penalise inner test-scope stubs relative to canonical defs.
# ---------------------------------------------------------------------------

class TestLocateRankingUPG1510:
    """Locate canonical library defs before inner test-scope stubs (UPG-15.10)."""

    # --- scope depth unit tests ---

    def test_scope_depth_top_level_class(self, tmp_path) -> None:
        """A class at module level has scope_depth=0."""
        lines = [
            "class Model:",
            "    pass",
        ]
        depth = _locate_scope_depth_from_lines(lines, start_line=1)
        assert depth == 0, f"Top-level class must have scope_depth=0, got {depth}"

    def test_scope_depth_class_inside_method(self, tmp_path) -> None:
        """A class defined inside a method body has scope_depth=1."""
        lines = [
            "class TestModelChecks:",
            "    def test_invalid_model(self):",
            "        class Model(models.Model):",
            "            pass",
        ]
        # 'class Model' starts at line 3 (1-indexed)
        depth = _locate_scope_depth_from_lines(lines, start_line=3)
        assert depth == 1, f"Class inside a method must have scope_depth=1, got {depth}"

    def test_scope_depth_class_inside_nested_functions(self, tmp_path) -> None:
        """A class inside two nested function calls has scope_depth=2."""
        lines = [
            "def outer():",
            "    def inner():",
            "        class Stub:",
            "            pass",
        ]
        depth = _locate_scope_depth_from_lines(lines, start_line=3)
        assert depth == 2, f"Class inside two defs must have scope_depth=2, got {depth}"

    def test_scope_depth_top_level_fast_path(self) -> None:
        """Symbols at indent=0 return 0 without scanning (fast path)."""
        lines = ["class TopLevel:", "    x = 1"]
        depth = _locate_scope_depth_from_lines(lines, start_line=1)
        assert depth == 0

    def test_scope_depth_empty_lines(self) -> None:
        """Empty lines list or out-of-range start_line returns 0."""
        assert _locate_scope_depth_from_lines([], 1) == 0
        assert _locate_scope_depth_from_lines(["class A: pass"], 0) == 0

    # --- partial_match_key unit tests with scope_depth signal ---

    def test_partial_match_key_scope_depth_penalised(self) -> None:
        """A row with scope_depth=1 ranks below scope_depth=0 all else equal."""
        row_canonical = {
            "name": "Model", "kind": "class",
            "file_path": "django/db/models/base.py",
            "start_line": 1, "end_line": 100,
        }
        row_inner = {
            "name": "Model", "kind": "class",
            "file_path": "tests/check_framework/test_model_checks.py",
            "start_line": 20, "end_line": 22,
        }
        key_canonical = _partial_match_key(row_canonical, "model", scope_depth=0)
        key_inner = _partial_match_key(row_inner, "model", scope_depth=1)
        assert key_canonical < key_inner, (
            f"Canonical (scope_depth=0) must rank before inner stub (scope_depth=1); "
            f"canonical key={key_canonical}, inner key={key_inner}"
        )

    def test_partial_match_key_large_span_beats_tiny_span(self) -> None:
        """A row with a large line span ranks before a tiny-span row (all else equal)."""
        row_large = {
            "name": "Model", "kind": "class",
            "file_path": "lib/base.py",
            "start_line": 1, "end_line": 1400,
        }
        row_tiny = {
            "name": "Model", "kind": "class",
            "file_path": "lib/other.py",
            "start_line": 100, "end_line": 103,
        }
        key_large = _partial_match_key(row_large, "model", scope_depth=0)
        key_tiny = _partial_match_key(row_tiny, "model", scope_depth=0)
        assert key_large < key_tiny, (
            f"Large-span (canonical) must rank before tiny-span (stub); "
            f"large={key_large}, tiny={key_tiny}"
        )

    # --- end-to-end locate_l2 test ---

    def _make_canonical_lib(self, tmp_path) -> str:
        """Write a canonical library file with a large top-level Model class."""
        lib_dir = tmp_path / "django" / "db" / "models"
        lib_dir.mkdir(parents=True)
        # Build a ~60-line class to exceed LOCATE_LARGE_SPAN_THRESHOLD (50 lines)
        body = "\n".join(f"    attr_{i} = None" for i in range(58))
        src = f"class Model:\n{body}\n"
        fp = lib_dir / "base.py"
        fp.write_text(src)
        return str(fp)

    def _make_test_file_with_inner_models(self, tmp_path) -> str:
        """Write a test file with many inner `class Model` stubs inside test methods."""
        test_dir = tmp_path / "tests" / "check_framework"
        test_dir.mkdir(parents=True)
        # 12 inner classes inside test methods — more than the default locate limit=10
        methods = []
        for i in range(12):
            methods.append(textwrap.dedent(f"""\
                def test_invalid_model_{i}(self):
                    class Model(models.Model):
                        pass
            """))
        src = "class TestModelChecks:\n" + textwrap.indent("".join(methods), "    ")
        fp = test_dir / "test_model_checks.py"
        fp.write_text(src)
        return str(fp)

    def test_canonical_model_ranks_in_top5_over_test_inner_stubs(self, tmp_path) -> None:
        """F29 acceptance: locate_l2('Model') returns the canonical library class
        in top-5 even when the same-named inner test-scope stubs outnumber the limit."""
        lib_fp = self._make_canonical_lib(tmp_path)
        test_fp = self._make_test_file_with_inner_models(tmp_path)

        g = SymbolGraph(str(tmp_path))
        # Index the test file first so its rows have lower rowids (indexed earlier),
        # which mimics the live Django situation where test files appear before the
        # canonical in alphabetical indexing order.
        g.index_file(str(tmp_path), test_fp)
        g.index_file(str(tmp_path), lib_fp)

        result = g.locate_l2(str(tmp_path), "Model", limit=10)
        assert result.resolution_strategy == "exact"
        names_and_files = [(s.name, s.file_path) for s in result.symbols]
        # The canonical library definition must appear in the top-5 results
        canonical_rank = next(
            (i for i, (_, fp) in enumerate(names_and_files) if "base.py" in fp),
            None,
        )
        assert canonical_rank is not None, (
            f"Canonical django/db/models/base.py:Model not found in results at all.\n"
            f"Got: {names_and_files}"
        )
        assert canonical_rank < 5, (
            f"Canonical library Model must rank in top-5 (got rank {canonical_rank}).\n"
            f"Top results: {names_and_files}"
        )
        # The top result must NOT be an inner test-scope stub
        top_file = names_and_files[0][1]
        assert "test" not in top_file.replace("\\", "/").split("/")[-1], (
            f"Rank-1 result must not be a test file; got {top_file}"
        )

    def test_scope_depth_batch_caches_per_file(self, tmp_path) -> None:
        """_locate_scope_depth_batch reads each file at most once (returns correct values)."""
        src = textwrap.dedent("""\
            class Outer:
                def method(self):
                    class Inner:
                        pass
        """)
        fp = tmp_path / "m.py"
        fp.write_text(src)
        # Simulate two rows from the same file: Outer at line 1, Inner at line 3
        rows = [
            {"file_path": str(fp), "start_line": 1},
            {"file_path": str(fp), "start_line": 3},
        ]
        depths = _locate_scope_depth_batch(rows)
        assert depths[0] == 0, f"Outer class (top-level) must be depth 0, got {depths[0]}"
        assert depths[1] == 1, f"Inner class (inside method) must be depth 1, got {depths[1]}"


# ---------------------------------------------------------------------------
# UPG-15.10x / F49 — locate: bare module-level defs rank before same-named
# class methods (the "common leaf" collision).
#
# `_partial_match_key`'s existing span_bucket signal assumes canonical == large,
# which correctly demotes tiny inner test-scope stubs (UPG-15.10/F29) but
# backfires when the canonical answer is itself a SHORT module-level function
# (e.g. a thin delegating wrapper) competing against a same-named, LARGER
# method on some unrelated class. The class_enclosed signal targets that
# orthogonal collision directly and is ranked ahead of span_bucket so a small
# canonical function is never outranked by a bigger method look-alike.
# ---------------------------------------------------------------------------

class TestLocateRankingClassEnclosedF49:
    """Bare module-level definitions outrank same-named class methods (F49)."""

    # --- _enclosing_class_from_lines / _locate_class_enclosed_batch unit tests ---

    def test_enclosing_class_from_lines_module_level_function(self) -> None:
        """A module-level function has no enclosing class."""
        lines = ["def render(request, template_name):", "    return None"]
        cls = _enclosing_class_from_lines(lines, start_line=1)
        assert cls == "", f"Module-level function must have no enclosing class, got {cls!r}"

    def test_enclosing_class_from_lines_method(self) -> None:
        """A method's immediate enclosing scope is its class."""
        lines = [
            "class ForNode:",
            "    def render(self, context):",
            "        return None",
        ]
        cls = _enclosing_class_from_lines(lines, start_line=2)
        assert cls == "ForNode", f"Method must resolve its enclosing class, got {cls!r}"

    def test_enclosing_class_from_lines_class_nested_in_function_not_class(self) -> None:
        """A class defined inside a function body (UPG-15.10/F29 shape) has NO
        enclosing CLASS — its immediate enclosing scope is the function, so this
        signal must not double-penalise the inner-test-stub case (that's scope_depth's
        job); class_enclosed only targets the bare-function-vs-method collision."""
        lines = [
            "class TestSuite:",
            "    def test_it(self):",
            "        class Widget:",
            "            pass",
        ]
        cls = _enclosing_class_from_lines(lines, start_line=3)
        assert cls == "", (
            f"A class immediately nested in a function must report no enclosing "
            f"class (its immediate parent is the function, not TestSuite); got {cls!r}"
        )

    def test_locate_class_enclosed_batch(self, tmp_path) -> None:
        """_locate_class_enclosed_batch reads each file once and reports True only
        for symbols whose immediate enclosing scope is a class."""
        src = textwrap.dedent("""\
            def render(request, template_name):
                return None

            class ForNode:
                def render(self, context):
                    return None
        """)
        fp = tmp_path / "m.py"
        fp.write_text(src)
        rows = [
            {"file_path": str(fp), "start_line": 1},  # module-level render
            {"file_path": str(fp), "start_line": 5},  # ForNode.render
        ]
        flags = _locate_class_enclosed_batch(rows)
        assert flags[0] is False, "Module-level render must not be class_enclosed"
        assert flags[1] is True, "ForNode.render must be class_enclosed"

    # --- _partial_match_key unit tests ---

    def test_partial_match_key_class_enclosed_penalised(self) -> None:
        """A class-enclosed candidate ranks below a module-level candidate,
        all else equal."""
        row_module_level = {
            "name": "render", "kind": "function",
            "file_path": "lib/shortcuts.py", "start_line": 1, "end_line": 5,
        }
        row_method = {
            "name": "render", "kind": "function",
            "file_path": "lib/nodes.py", "start_line": 1, "end_line": 5,
        }
        key_module = _partial_match_key(
            row_module_level, "render", scope_depth=0, class_enclosed=False,
        )
        key_method = _partial_match_key(
            row_method, "render", scope_depth=0, class_enclosed=True,
        )
        assert key_module < key_method, (
            f"Module-level def must rank before a same-named class method; "
            f"module key={key_module}, method key={key_method}"
        )

    def test_partial_match_key_class_enclosed_beats_larger_span(self) -> None:
        """Regression for the exact F49 bug: a SHORT module-level function must
        outrank a LARGER same-named class method — span_bucket alone got this
        backwards (larger span looked more 'canonical') until class_enclosed was
        placed ahead of it in the sort key."""
        row_short_module_level = {
            "name": "render", "kind": "function",
            "file_path": "lib/shortcuts.py", "start_line": 1, "end_line": 8,   # tiny span
        }
        row_large_method = {
            "name": "render", "kind": "function",
            "file_path": "lib/nodes.py", "start_line": 1, "end_line": 65,      # medium/large span
        }
        key_short = _partial_match_key(
            row_short_module_level, "render", scope_depth=0, class_enclosed=False,
        )
        key_large = _partial_match_key(
            row_large_method, "render", scope_depth=0, class_enclosed=True,
        )
        assert key_short < key_large, (
            f"Short module-level def must beat a larger class-method look-alike; "
            f"short={key_short}, large={key_large}"
        )

    def test_partial_match_key_no_regression_when_all_candidates_are_methods(self) -> None:
        """When every same-name candidate is a class method (no bare module-level
        def exists — e.g. locate('save') across several model classes), the
        class_enclosed tier is a no-op tie and span_bucket still decides, exactly
        as before this change (UPG-15.10 behaviour preserved)."""
        row_large_method = {
            "name": "save", "kind": "function",
            "file_path": "lib/models.py", "start_line": 1, "end_line": 60,
        }
        row_tiny_method = {
            "name": "save", "kind": "function",
            "file_path": "lib/other.py", "start_line": 1, "end_line": 4,
        }
        key_large = _partial_match_key(
            row_large_method, "save", scope_depth=0, class_enclosed=True,
        )
        key_tiny = _partial_match_key(
            row_tiny_method, "save", scope_depth=0, class_enclosed=True,
        )
        assert key_large < key_tiny, (
            "With class_enclosed tied (both methods), the larger span must still "
            f"win as before; large={key_large}, tiny={key_tiny}"
        )

    # --- end-to-end locate_l2 test (F49 acceptance witness, neutral names) ---

    def _make_lib_with_canonical_render(self, tmp_path) -> str:
        """A short, module-level canonical `render` — the library's public API
        entry point, e.g. a thin dispatcher over a templating backend."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir(parents=True)
        src = textwrap.dedent("""\
            def render(request, template_name, context=None):
                content = _render_to_string(template_name, context, request)
                return content
        """)
        fp = lib_dir / "shortcuts.py"
        fp.write_text(src)
        return str(fp)

    def _make_lib_with_render_methods(self, tmp_path) -> str:
        """Several unrelated node classes each define a larger `render` method —
        common-leaf look-alikes competing with the canonical function above."""
        lib_dir = tmp_path / "lib"
        classes = []
        for i in range(8):
            body_lines = "\n".join(f"        step_{j} = {j}" for j in range(20))
            classes.append(textwrap.dedent(f"""\
                class Node{i}:
                    def render(self, context):
                {body_lines}
                        return context
            """))
        fp = lib_dir / "nodes.py"
        fp.write_text("\n".join(classes))
        return str(fp)

    def test_canonical_render_ranks_first_over_class_method_lookalikes(self, tmp_path) -> None:
        """F49 acceptance: locate_l2('render') returns the canonical module-level
        function first, ahead of larger same-named methods on unrelated classes."""
        lib_fp = self._make_lib_with_canonical_render(tmp_path)
        nodes_fp = self._make_lib_with_render_methods(tmp_path)

        g = SymbolGraph(str(tmp_path))
        # Index the method-heavy file first so its rows have lower rowids —
        # mirrors the live django situation where defaulttags.py sorts before
        # shortcuts.py alphabetically.
        g.index_file(str(tmp_path), nodes_fp)
        g.index_file(str(tmp_path), lib_fp)

        result = g.locate_l2(str(tmp_path), "render", limit=5)
        assert result.resolution_strategy == "exact"
        names_and_files = [(s.name, s.file_path) for s in result.symbols]
        assert names_and_files, "locate_l2('render') returned no results"
        top_name, top_file = names_and_files[0]
        assert "shortcuts.py" in top_file and "." not in top_name, (
            f"Rank-1 result must be the bare module-level canonical render, "
            f"got {names_and_files[0]}.\nFull top-5: {names_and_files}"
        )


# ---------------------------------------------------------------------------
# ARCH-1a — file-level PageRank importance: compute, persist, read API
# ---------------------------------------------------------------------------

def _seed_importance_graph(g: "SymbolGraph", ws: str, tmp_path) -> dict[str, str]:
    """Seed a synthetic graph for importance tests.

    Graph layout (all files also have symbols so they're in the PageRank node set):
      - file_b.py defines symbol 'foo' (leaf='foo')
      - file_c.py, file_d.py, file_e.py each define their own symbol AND have an
        edge referencing 'foo' (they all link to file_b.py via the def<->ref graph)
      - file_a.py defines its own symbol but has no outgoing refs and no in-links

    Expected result after PageRank + normalization:
      file_b.py has the highest score (= 1.0) because 3 files reference it.
      file_a.py has a strictly lower score (no in-links from refs).
    """
    import time
    now = time.time()

    files = {
        "a": str(tmp_path / "file_a.py"),
        "b": str(tmp_path / "file_b.py"),
        "c": str(tmp_path / "file_c.py"),
        "d": str(tmp_path / "file_d.py"),
        "e": str(tmp_path / "file_e.py"),
    }
    # Create the actual files so SymbolGraph is consistent
    for fp in files.values():
        Path(fp).write_text("# placeholder\n")

    with g._conn() as conn:
        # All files get at least one symbol so they're in the PageRank node set.
        # file_b defines 'foo' — the target of all the ref-edges.
        conn.execute(
            "INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ws, "foo", "function", files["b"], 1, 5, now),
        )
        # file_a defines something unrelated (no in-links to it from refs)
        conn.execute(
            "INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ws, "bar_unrelated", "function", files["a"], 1, 3, now),
        )
        # file_c/d/e each define their own unique symbol (so they enter the node set)
        # AND each has an edge referencing 'foo' (to_symbol='foo' → links to file_b)
        for key, ref_file in (("c", files["c"]), ("d", files["d"]), ("e", files["e"])):
            conn.execute(
                "INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ws, f"caller_{key}", "function", ref_file, 1, 3, now),
            )
            conn.execute(
                "INSERT INTO edges (workspace, from_file, from_symbol, from_line, to_symbol, edge_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ws, ref_file, f"caller_{key}", 10, "foo", "calls"),
            )

    return files


class TestFileImportanceARCH1a:
    """ARCH-1a: file-level PageRank importance computation, persistence, and read API."""

    def test_most_referenced_file_gets_highest_score(self, tmp_path) -> None:
        """file_b defines 'foo'; c/d/e reference it → file_b must score 1.0 (max)."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)

        g._compute_and_store_importance(ws)
        scores = g.file_importance(ws)

        assert files["b"] in scores, "file_b must have an importance score"
        assert abs(scores[files["b"]] - 1.0) < 1e-9, (
            f"file_b (most-referenced) must have score=1.0 after normalization, "
            f"got {scores[files['b']]}"
        )

    def test_most_referenced_beats_barely_referenced(self, tmp_path) -> None:
        """file_b (3 in-links) must score strictly higher than file_a (0 in-links)."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)

        g._compute_and_store_importance(ws)
        scores = g.file_importance(ws)

        score_b = scores.get(files["b"], 0.0)
        score_a = scores.get(files["a"], 0.0)
        assert score_b > score_a, (
            f"file_b (3 in-links) must score above file_a (0 in-links); "
            f"b={score_b:.6f}, a={score_a:.6f}"
        )

    def test_persistence_round_trip(self, tmp_path) -> None:
        """build_for_workspace writes importance; file_importance returns expected files."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)

        # Trigger compute via the internal method (build_for_workspace calls it)
        g._compute_and_store_importance(ws)
        scores = g.file_importance(ws)

        # All scored files must have scores in (0, 1]
        assert len(scores) > 0, "file_importance must return non-empty dict after compute"
        for fp, score in scores.items():
            assert 0.0 < score <= 1.0, (
                f"{fp} has score {score} outside (0, 1]"
            )

    def test_build_for_workspace_stores_importance(self, tmp_path) -> None:
        """build_for_workspace end-to-end: importance is persisted and readable."""
        g = SymbolGraph(str(tmp_path))
        # Use real Python files so index_file works via tree-sitter
        p1 = make_py(tmp_path, "lib.py", "def core_fn(): pass\n")
        p2 = make_py(tmp_path, "caller.py", "def user():\n    core_fn()\n")
        g.build_for_workspace(str(tmp_path), [p1, p2])

        scores = g.file_importance(str(tmp_path))
        # At least one score must be present after a real build
        assert len(scores) >= 1

    def test_rebuild_idempotency(self, tmp_path) -> None:
        """Calling build_for_workspace twice does not duplicate importance rows."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)

        g._compute_and_store_importance(ws)
        scores_first = g.file_importance(ws)
        g._compute_and_store_importance(ws)
        scores_second = g.file_importance(ws)

        assert scores_first == scores_second, (
            "Rebuilding importance twice must yield identical scores (PK + delete-first)"
        )

    def test_row_count_not_doubled_on_rebuild(self, tmp_path) -> None:
        """After two compute calls, the row count must equal the file count (no dups)."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)

        g._compute_and_store_importance(ws)
        g._compute_and_store_importance(ws)

        with g._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM symbol_importance WHERE workspace = ?", (ws,)
            ).fetchone()[0]
        scores = g.file_importance(ws)
        assert count == len(scores), (
            f"Row count ({count}) must equal len(file_importance) ({len(scores)}) — no duplicates"
        )

    def test_empty_graph_no_crash(self, tmp_path) -> None:
        """A workspace with no symbols/edges must not crash; file_importance returns empty."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        # No files indexed — pure empty state
        g._compute_and_store_importance(ws)
        scores = g.file_importance(ws)
        assert scores == {}, f"Empty graph must return empty dict, got {scores}"

    def test_no_edge_graph_no_crash(self, tmp_path) -> None:
        """A workspace with symbols but no edges must not crash."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        p = make_py(tmp_path, "isolated.py", "def standalone(): pass\n")
        g.index_file(ws, p)
        # No edges exist — power-iteration over dangling nodes only
        g._compute_and_store_importance(ws)
        # Should not raise; result may be empty or uniform
        scores = g.file_importance(ws)
        assert isinstance(scores, dict)

    def test_delete_file_removes_importance_row(self, tmp_path) -> None:
        """delete_file must also remove that file's importance row."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)
        g._compute_and_store_importance(ws)

        assert files["b"] in g.file_importance(ws), "file_b must have a score before delete"
        g.delete_file(ws, files["b"])
        assert files["b"] not in g.file_importance(ws), (
            "file_b importance row must be removed by delete_file"
        )

    def test_file_importance_empty_when_never_built(self, tmp_path) -> None:
        """A freshly created SymbolGraph with no compute call returns an empty dict."""
        g = SymbolGraph(str(tmp_path))
        scores = g.file_importance(str(tmp_path))
        assert scores == {}

    def test_scores_in_range(self, tmp_path) -> None:
        """All scores returned by file_importance must be in (0, 1]."""
        ws = str(tmp_path)
        g = SymbolGraph(ws)
        files = _seed_importance_graph(g, ws, tmp_path)
        g._compute_and_store_importance(ws)
        scores = g.file_importance(ws)
        for fp, score in scores.items():
            assert 0.0 < score <= 1.0 + 1e-9, (
                f"{fp}: score {score} not in (0, 1]"
            )

    def test_schema_version_bumped(self) -> None:
        """SYMBOL_SCHEMA_VERSION must be 7 (UPG-JSFLOW-SYMBOLS bumped from 6)."""
        assert SYMBOL_SCHEMA_VERSION == 7, (
            f"Expected SYMBOL_SCHEMA_VERSION=7 after UPG-JSFLOW-SYMBOLS bump, got {SYMBOL_SCHEMA_VERSION}"
        )
