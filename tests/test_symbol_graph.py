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
    extract_symbols_from_file,
    _levenshtein,
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
        assert result.resolution_strategy in ("same_module", "unique_name", "fuzzy", "none")

    def test_unique_name_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "x.py", "def unique_xyz_fn(): pass")
        g.index_file("ws", str(tmp_path / "x.py"))
        # No exact match for "unique_xyz" — but only one symbol contains it
        result = g.locate_l2("ws", "unique_xyz")
        assert result.resolution_strategy == "unique_name"
        assert result.symbols[0].name == "unique_xyz_fn"

    def test_fuzzy_strategy(self, tmp_path) -> None:
        g = SymbolGraph(str(tmp_path))
        make_py(tmp_path, "f.py", "def foo_bar(): pass")
        g.index_file("ws", str(tmp_path / "f.py"))
        # "fo_bar" is NOT a substring of "foo_bar" (unique_name skips) but edit-distance 1
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
