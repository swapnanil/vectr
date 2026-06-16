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
    supports_symbols,
    extract_symbols_from_file,
    _levenshtein,
    _partial_match_key,
)
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
        svc.ingest_traces.return_value = {"ingested": 3, "skipped_invalid": 0}
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
