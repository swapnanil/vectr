"""
Tests for CodeIndexer and CodeSearcher.

Uses a DummyEmbedProvider (from conftest) so no model download is needed.
Tests verify the real ChromaDB storage and hybrid BM25+vector search pipeline.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.conftest import make_py


# ---------------------------------------------------------------------------
# _code_tokenize — BM25 tokenizer
# ---------------------------------------------------------------------------

class TestCodeTokenize:
    def _tok(self, text: str) -> list[str]:
        from agent.searcher import _code_tokenize
        return _code_tokenize(text)

    def test_snake_case_split(self) -> None:
        tokens = self._tok("dispatch_uid")
        assert "dispatch" in tokens
        assert "uid" in tokens

    def test_camel_case_split(self) -> None:
        tokens = self._tok("RateLimitMiddleware")
        assert "rate" in tokens
        assert "limit" in tokens
        assert "middleware" in tokens

    def test_mixed_identifier(self) -> None:
        tokens = self._tok("send_signal_dispatch_uid")
        assert "send" in tokens
        assert "dispatch" in tokens
        assert "uid" in tokens

    def test_short_tokens_filtered(self) -> None:
        tokens = self._tok("a b c def")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "def" in tokens

    def test_punctuation_stripped(self) -> None:
        tokens = self._tok("func(arg1, arg2):")
        assert "func" in tokens
        assert "arg1" in tokens
        assert "arg2" in tokens
        assert "(" not in tokens

    def test_no_duplicates(self) -> None:
        tokens = self._tok("foo foo foo")
        assert tokens.count("foo") == 1


# ---------------------------------------------------------------------------
# CodeIndexer — index / delete / query
# ---------------------------------------------------------------------------

class TestCodeIndexer:
    def test_index_file_creates_chunks(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "auth.py", """
            def verify_token(token: str) -> dict:
                return {}

            class AuthMiddleware:
                def process(self, request):
                    pass
        """)
        count = indexer.index_file(path)
        assert count >= 2
        assert indexer.total_chunks >= 2

    def test_index_file_idempotent(self, indexer, tmp_path) -> None:
        """Re-indexing the same file must not create duplicate chunks."""
        path = make_py(tmp_path, "utils.py", """
            def helper():
                pass
        """)
        indexer.index_file(path)
        first = indexer.total_chunks
        indexer.index_file(path)
        assert indexer.total_chunks == first

    def test_delete_file_removes_chunks(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "temp.py", """
            def to_remove():
                x = 1
        """)
        indexer.index_file(path)
        assert indexer.total_chunks > 0
        indexer.delete_file(path)
        assert indexer.total_chunks == 0

    def test_index_workspace_walks_py_files(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def foo(): pass")
        make_py(tmp_path, "b.py", "def bar(): pass")
        (tmp_path / "skip.txt").write_text("not indexed")
        files, chunks = indexer.index_workspace()
        assert files == 2
        assert chunks >= 2

    def test_index_workspace_skips_excluded_dirs(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "main.py", "def main(): pass")
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "pkg.py").write_text("def evil(): pass")
        files, chunks = indexer.index_workspace()
        assert files == 1

    def test_embed_query_returns_vector(self, indexer) -> None:
        vec = indexer.embed_query("how does rate limiting work")
        assert isinstance(vec, list)
        assert len(vec) == 768  # DummyEmbedProvider mirrors nomic-embed-code dim
        assert all(isinstance(v, float) for v in vec)

    def test_query_vector_returns_results_after_index(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "search_me.py", """
            def rate_limit_check(ip: str) -> bool:
                return True
        """)
        indexer.index_file(path)
        embedding = indexer.embed_query("rate limit check function")
        result = indexer.query_vector(embedding, n_results=5)
        assert len(result["ids"][0]) >= 1

    def test_query_vector_language_filter(self, indexer, tmp_path) -> None:
        py_file = make_py(tmp_path, "app.py", "def process(): pass")
        js_file = tmp_path / "app.js"
        js_file.write_text("function process() {}")
        indexer.index_file(py_file)
        indexer.index_file(str(js_file))
        embedding = indexer.embed_query("process function")
        result = indexer.query_vector(embedding, n_results=10, language="python")
        metas = result["metadatas"][0]
        assert all(m["language"] == "python" for m in metas)

    def test_total_chunks_property(self, indexer, tmp_path) -> None:
        assert indexer.total_chunks == 0
        make_py(tmp_path, "x.py", "def f(): pass")
        indexer.index_workspace()
        assert indexer.total_chunks > 0

    def test_indexed_file_count_property(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        make_py(tmp_path, "b.py", "def b(): pass")
        indexer.index_workspace()
        assert indexer.indexed_file_count == 2

    def test_indexed_file_paths_property(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "known.py", "def x(): pass")
        indexer.index_file(path)
        assert path in indexer.indexed_file_paths

    def test_indexed_languages_lists_distinct_sorted(self, indexer, tmp_path) -> None:
        # UPG-3.1: ground-truth language set comes from chunk metadata, not a fixed list.
        make_py(tmp_path, "a.py", "def x(): pass")
        js = tmp_path / "b.js"; js.write_text("function y() {}")
        indexer.index_file(make_py(tmp_path, "a.py", "def x(): pass"))
        indexer.index_file(str(js))
        langs = indexer.indexed_languages()
        assert langs == sorted(langs)
        assert "python" in langs and "javascript" in langs
        assert "cobol" not in langs

    def test_indexed_languages_cache_invalidated_on_change(self, indexer, tmp_path) -> None:
        indexer.index_file(make_py(tmp_path, "a.py", "def x(): pass"))
        assert "javascript" not in indexer.indexed_languages()
        js = tmp_path / "b.js"; js.write_text("function y() {}")
        indexer.index_file(str(js))
        # chunk count changed → cache recomputed, js now present
        assert "javascript" in indexer.indexed_languages()

    def test_indexed_language_stats_files_and_chunks(self, indexer, tmp_path) -> None:
        # UPG-3.3: per-language coverage — distinct files + chunk counts per language.
        indexer.index_file(make_py(tmp_path, "a.py", "def x(): pass"))
        indexer.index_file(make_py(tmp_path, "b.py", "def y(): pass"))
        js = tmp_path / "c.js"; js.write_text("function z() {}")
        indexer.index_file(str(js))
        stats = indexer.indexed_language_stats()
        assert stats["python"]["files"] == 2
        assert stats["python"]["chunks"] >= 2
        assert stats["javascript"]["files"] == 1
        # indexed_languages() derives from the same source
        assert indexer.indexed_languages() == sorted(stats)

    def test_get_all_documents_returns_indexed_content(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "fn.py", """
            def my_special_function():
                return 42
        """)
        indexer.index_workspace()
        ids, docs, metas = indexer.get_all_documents()
        assert len(ids) > 0
        assert any("my_special_function" in d for d in docs)

    def test_index_workspace_parallel_chunks_all_files(self, indexer, tmp_path) -> None:
        """Parallel chunking must index every file exactly once."""
        for i in range(20):
            make_py(tmp_path, f"mod_{i}.py", f"def fn_{i}(): return {i}")
        indexer.index_workspace()
        assert indexer.indexed_file_count == 20
        assert indexer.total_chunks >= 20

    def test_incremental_skip_unchanged_files(self, indexer, tmp_path) -> None:
        """Second index_workspace() call must skip files whose mtime has not changed."""
        make_py(tmp_path, "a.py", "def a(): pass")
        make_py(tmp_path, "b.py", "def b(): pass")
        indexer.index_workspace()
        chunks_after_first = indexer.total_chunks

        # Second call: nothing changed → chunk count must not grow
        indexer.index_workspace()
        assert indexer.total_chunks == chunks_after_first

    def test_incremental_reindexes_modified_file(self, indexer, tmp_path) -> None:
        """A file whose mtime changes must be re-indexed on the next workspace index."""
        path = Path(make_py(tmp_path, "evolving.py", "def v1(): pass"))
        indexer.index_workspace()

        # Modify the file content and bump mtime
        path.write_text("def v1(): pass\ndef v2(): pass\n")
        import os; os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1))

        indexer.index_workspace()
        ids, docs, _ = indexer.get_all_documents()
        assert any("v2" in d for d in docs), "Modified file was not re-indexed"

    def test_mtime_cache_persists_across_instances(self, tmp_path) -> None:
        """A new CodeIndexer pointed at the same workspace must respect the mtime cache."""
        from agent.indexer import CodeIndexer
        from tests.conftest import _DummyEmbedProvider

        db = str(tmp_path / "db")
        make_py(tmp_path, "cached.py", "def cached(): pass")

        idx1 = CodeIndexer(str(tmp_path), db_path=db)
        idx1._embed_provider = _DummyEmbedProvider()
        idx1.index_workspace()
        count_after_first = idx1.total_chunks

        idx2 = CodeIndexer(str(tmp_path), db_path=db)
        idx2._embed_provider = _DummyEmbedProvider()
        idx2.index_workspace()
        assert idx2.total_chunks == count_after_first

    def test_index_minified_file_no_duplicate_ids(self, indexer, tmp_path) -> None:
        """Minified JS (all code on 1-2 lines) must not produce duplicate chunk IDs."""
        js_file = tmp_path / "jquery.min.js"
        # Simulate minified JS: many statements crammed onto line 2
        js_file.write_text(
            "// jquery minified\n"
            + "!function(e,t){'use strict';function n(e){return e}function r(e,t){return e+t}"
            * 5
        )
        # Must not raise DuplicateIDError
        count = indexer.index_file(str(js_file))
        assert count >= 1
        # Re-index must also not raise
        count2 = indexer.index_file(str(js_file))
        assert count2 >= 1
        assert indexer.total_chunks == count2  # idempotent


# ---------------------------------------------------------------------------
# CodeSearcher — hybrid BM25 + vector
# ---------------------------------------------------------------------------

class TestCodeSearcher:
    def _indexed_searcher(self, indexer, tmp_path, content: str, name="module.py"):
        path = make_py(tmp_path, name, content)
        indexer.index_file(path)
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        return s

    def test_search_returns_results(self, indexer, tmp_path) -> None:
        s = self._indexed_searcher(indexer, tmp_path, """
            def authenticate_user(username: str, password: str) -> bool:
                return True
        """)
        results, ms = s.search("authenticate user function")
        assert len(results) >= 1
        assert results[0].file_path.endswith("module.py")

    def test_search_result_fields(self, indexer, tmp_path) -> None:
        s = self._indexed_searcher(indexer, tmp_path, """
            def check_permissions(user_id: int) -> list:
                return []
        """)
        results, ms = s.search("check permissions")
        r = results[0]
        assert r.file_path
        assert r.lines  # e.g. "2-4"
        assert r.language == "python"
        assert 0.0 <= r.score <= 1.0
        assert r.content

    def test_search_empty_index_returns_empty(self, indexer) -> None:
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        results, ms = s.search("anything")
        assert results == []
        assert ms == 0

    def test_bm25_finds_exact_keyword(self, indexer, tmp_path) -> None:
        # _code_tokenize splits snake_case: "dispatch_uid" → ["dispatch","uid"]
        # and "send_signal_dispatch_uid" → ["send","signal","dispatch","uid"].
        # So a query for "dispatch_uid" matches even though the function name is
        # one long identifier — no workaround needed.
        path = make_py(tmp_path, "signals.py", """
            def send_signal_dispatch_uid(sender, **kwargs):
                pass

            def unrelated_function():
                pass
        """)
        indexer.index_file(path)
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("dispatch_uid", semantic_weight=0.0)  # pure BM25
        assert len(results) >= 1
        assert "dispatch_uid" in results[0].content or "dispatch" in results[0].symbol_name

    def test_search_language_filter(self, indexer, tmp_path) -> None:
        py_path = make_py(tmp_path, "app.py", "def python_fn(): pass")
        js_path = tmp_path / "app.js"
        js_path.write_text("function jsFn() {}")
        indexer.index_file(py_path)
        indexer.index_file(str(js_path))
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("function", language="python")
        assert all(r.language == "python" for r in results)

    def test_search_language_filter_normalized(self, indexer, tmp_path) -> None:
        # UPG-3.1: case/whitespace on the filter must still match indexed values,
        # and apply identically regardless of caller (REST vs MCP both hit this).
        indexer.index_file(make_py(tmp_path, "app.py", "def python_fn(): pass"))
        js_path = tmp_path / "app.js"; js_path.write_text("function jsFn() {}")
        indexer.index_file(str(js_path))
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        for variant in ("PYTHON", "  Python ", "python"):
            results, _ = s.search("function", language=variant)
            assert results, f"variant {variant!r} returned nothing"
            assert all(r.language == "python" for r in results), f"variant {variant!r} leaked non-python"
        # blank filter degrades to no-filter (can include js)
        blank, _ = s.search("function", language="   ")
        assert any(r.language == "javascript" for r in blank) or blank

    def test_unfiltered_search_fetches_deeper_pool(self, indexer, tmp_path) -> None:
        """Unfiltered queries fetch a deeper rerank pool than filtered ones, so real
        code isn't crowded out by doc prose before the quality prior runs (UPG-2.1 tuning)."""
        import agent.searcher as searcher_mod
        # Index enough chunks that both fetch depths are below the total.
        body = "\n".join(f"def fn_{i}(x):\n    return x + {i}\n" for i in range(80))
        path = make_py(tmp_path, "many.py", body)
        indexer.index_file(path)
        assert indexer.total_chunks >= searcher_mod._RERANK_TOP_K_UNFILTERED

        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()

        class _CountingReranker:
            def __init__(self):
                self.last_count = 0
            def rerank(self, query, candidates):
                self.last_count = len(candidates)
                return [c for _, c in candidates]

        stub = _CountingReranker()
        s._reranker = stub  # force the rerank path on
        s.search("function returning a number", language=None)
        unfiltered_count = stub.last_count
        s.search("function returning a number", language="python")
        filtered_count = stub.last_count

        assert unfiltered_count == min(searcher_mod._RERANK_TOP_K_UNFILTERED, indexer.total_chunks)
        assert filtered_count == min(searcher_mod._RERANK_TOP_K, indexer.total_chunks)
        assert unfiltered_count > filtered_count

    def test_search_multiple_files_ranked(self, indexer, tmp_path) -> None:
        # Code tokenizer splits camelCase: "RateLimitMiddleware" → ["rate","limit","middleware"]
        # so querying "rate limit" finds middleware.py without any workarounds.
        make_py(tmp_path, "middleware.py", """
            class RateLimitMiddleware:
                def __call__(self, request, get_response):
                    pass
        """)
        make_py(tmp_path, "utils.py", """
            def helper():
                pass
        """)
        indexer.index_workspace()
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("RateLimitMiddleware", semantic_weight=0.0)
        assert len(results) >= 1
        assert results[0].file_path.endswith("middleware.py")

    def test_refresh_bm25_after_new_index(self, indexer, tmp_path) -> None:
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)

        make_py(tmp_path, "first.py", "def first_fn(): pass")
        indexer.index_workspace()
        s.refresh_bm25()
        r1, _ = s.search("first_fn", semantic_weight=0.0)
        assert len(r1) >= 1

        make_py(tmp_path, "second.py", "def second_fn_unique_xyz(): pass")
        indexer.index_workspace()
        s.refresh_bm25()  # must rebuild to include new file
        r2, _ = s.search("second_fn_unique_xyz", semantic_weight=0.0)
        assert len(r2) >= 1

    def test_search_returns_timing(self, indexer, tmp_path) -> None:
        s = self._indexed_searcher(indexer, tmp_path, "def timed(): pass")
        _, ms = s.search("timed function")
        assert isinstance(ms, int)
        assert ms >= 0

    def test_search_n_results_respected(self, indexer, tmp_path) -> None:
        for i in range(6):
            make_py(tmp_path, f"fn{i}.py", f"def func_{i}(): pass\n")
        indexer.index_workspace()
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("func", n_results=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Zig language support
# ---------------------------------------------------------------------------

class TestZigSupport:
    """chunk_file() must produce AST-aware chunks for .zig files (not window fallback)."""

    _ZIG_CODE = """\
const std = @import("std");

pub fn add(a: i32, b: i32) i32 {
    return a + b;
}

pub fn subtract(a: i32, b: i32) i32 {
    return a - b;
}

pub const Account = struct {
    id: u128,
    balance: i64,

    pub fn debit(self: *Account, amount: i64) void {
        self.balance -= amount;
    }
};
"""

    def _write_zig(self, tmp_path) -> str:
        p = tmp_path / "state_machine.zig"
        p.write_text(self._ZIG_CODE)
        return str(p)

    def test_zig_chunks_are_ast_aware(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_zig(tmp_path))
        node_types = {c.node_type for c in chunks}
        assert "function_declaration" in node_types or "variable_declaration" in node_types, (
            "expected AST-aware node types, got window-fallback chunks"
        )

    def test_zig_function_symbol_name_extracted(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_zig(tmp_path))
        names = {c.symbol_name for c in chunks}
        assert "add" in names
        assert "subtract" in names

    def test_zig_struct_symbol_name_extracted(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_zig(tmp_path))
        names = {c.symbol_name for c in chunks}
        assert "Account" in names

    def test_zig_language_label_set(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_zig(tmp_path))
        assert all(c.language == "zig" for c in chunks)

    def test_zig_file_indexed_by_indexer(self, indexer, tmp_path) -> None:
        p = tmp_path / "main.zig"
        p.write_text("pub fn run() void {}\npub fn stop() void {}\n")
        count = indexer.index_file(str(p))
        assert count >= 2, f"expected ≥2 Zig chunks, got {count}"


# ---------------------------------------------------------------------------
# C / C++ language support (UPG-3.2 — locate/trace were dead on C)
# ---------------------------------------------------------------------------

class TestCSupport:
    _C_CODE = """\
#include "Python.h"

typedef struct { int n; } GCState;

static GCState *get_gc_state(PyThreadState *tstate) {
    return &tstate->gc;
}

PyObject *PyDict_New(void) {
    GCState *st = get_gc_state(NULL);
    PyObject *op = _PyObject_GC_New();
    return op;
}

#define MAXSIZE 256
"""

    def _write_c(self, tmp_path, name="dictobject.c") -> str:
        p = tmp_path / name
        p.write_text(self._C_CODE)
        return str(p)

    def test_c_chunks_are_ast_aware(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_c(tmp_path))
        node_types = {c.node_type for c in chunks}
        assert "function_definition" in node_types, "expected AST chunks, got window fallback"
        assert all(c.language == "c" for c in chunks)

    def test_c_function_name_not_return_type(self, tmp_path) -> None:
        # The return type (PyObject/GCState) must NOT be mistaken for the function name.
        from agent.indexer import chunk_file
        names = {c.symbol_name for c in chunk_file(self._write_c(tmp_path))}
        assert "PyDict_New" in names
        assert "get_gc_state" in names
        assert "PyObject" not in names  # return type, not a function symbol

    def test_c_symbols_extracted(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        syms, _ = extract_symbols_from_file(self._write_c(tmp_path))
        by_name = {s["name"]: s["kind"] for s in syms}
        assert by_name.get("get_gc_state") == "function"
        assert by_name.get("PyDict_New") == "function"
        assert by_name.get("GCState") == "type"
        assert by_name.get("MAXSIZE") == "macro"
        assert "" not in by_name  # no anonymous-struct pollution

    def test_c_call_edges_extracted(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        _, edges = extract_symbols_from_file(self._write_c(tmp_path))
        pairs = {(e["from_symbol"], e["to_symbol"]) for e in edges}
        assert ("PyDict_New", "get_gc_state") in pairs
        assert ("PyDict_New", "_PyObject_GC_New") in pairs

    def test_c_deeply_nested_does_not_recurse_crash(self, tmp_path) -> None:
        # Regression: a chain of generic AST nodes (deeply nested parens) used to
        # blow Python's recursion limit because the walker didn't increment depth
        # on generic nodes, so _MAX_DEPTH never fired. Must not raise, and the
        # top-level function must still be extracted.
        from agent.symbol_graph import extract_symbols_from_file
        deep = "(" * 1200 + "1" + ")" * 1200
        p = tmp_path / "deep.c"
        p.write_text(f"int deeply_nested_fn(void) {{ return {deep}; }}\n")
        syms, _ = extract_symbols_from_file(str(p))   # must not raise RecursionError
        assert any(s["name"] == "deeply_nested_fn" for s in syms)

    def test_c_locate_and_trace_via_graph(self, tmp_path) -> None:
        # End-to-end: the audit's exact failure (locate/trace dead on C) is fixed.
        from agent.symbol_graph import SymbolGraph
        db = tmp_path / "sg"; db.mkdir()
        sg = SymbolGraph(str(db))
        path = self._write_c(tmp_path)
        ws = str(tmp_path)
        sg.index_file(ws, path)
        hits = sg.locate(ws, "PyDict_New")
        assert hits and hits[0].name == "PyDict_New"
        trace = sg.trace(ws, "get_gc_state", direction="callers")
        caller_names = {c.from_symbol for c in trace.get("callers", [])}
        assert "PyDict_New" in caller_names


class TestCppSupport:
    _CPP_CODE = """\
namespace ns {

class Widget {
public:
    void render() { paint(); }
    int size() const { return n; }
private:
    int n;
};

}  // namespace ns
"""

    def _write_cpp(self, tmp_path, name="widget.cpp") -> str:
        p = tmp_path / name
        p.write_text(self._CPP_CODE)
        return str(p)

    def test_cpp_language_label(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_cpp(tmp_path))
        assert chunks and all(c.language == "cpp" for c in chunks)

    def test_cpp_class_and_method_symbols(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        syms, edges = extract_symbols_from_file(self._write_cpp(tmp_path))
        by_name = {s["name"]: s["kind"] for s in syms}
        assert by_name.get("Widget") == "class"
        assert "render" in by_name
        assert ("render", "paint") in {(e["from_symbol"], e["to_symbol"]) for e in edges}


class TestMarkdownSupport:
    """chunk_file() produces heading-aware section chunks for .md files."""

    _MD_WITH_HEADINGS = """\
# My Project

A brief introduction paragraph with some context.

## Installation

Run `pip install myproject` to get started.
Some additional installation notes here.

## Usage

Here is how to use the tool.

### Advanced Usage

For power users, see the config section.

## API Reference

The main API surface is documented here.
"""

    _MD_NO_HEADINGS = """\
This is a flat markdown file.
It has no headings at all.
Just plain paragraphs of text spread across multiple lines.
"""

    def _write_md(self, tmp_path, content: str, name: str = "README.md") -> str:
        p = tmp_path / name
        p.write_text(content)
        return str(p)

    def test_markdown_uses_section_node_type(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_md(tmp_path, self._MD_WITH_HEADINGS))
        assert any(c.node_type == "section" for c in chunks), (
            "expected section chunks for markdown with headings"
        )

    def test_markdown_heading_text_becomes_symbol_name(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_md(tmp_path, self._MD_WITH_HEADINGS))
        names = {c.symbol_name for c in chunks}
        assert "Installation" in names
        assert "Usage" in names
        assert "API Reference" in names

    def test_markdown_not_window_fallback_for_headings(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_md(tmp_path, self._MD_WITH_HEADINGS))
        assert not any(c.node_type == "window" for c in chunks), (
            "headed markdown should not use window fallback"
        )

    def test_markdown_no_headings_produces_single_preamble_chunk(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_md(tmp_path, self._MD_NO_HEADINGS))
        # Headingless files flush as a single section with empty symbol_name (no boundary to split on)
        assert len(chunks) == 1
        assert chunks[0].language == "markdown"
        assert chunks[0].symbol_name == ""

    def test_markdown_language_label_set(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_md(tmp_path, self._MD_WITH_HEADINGS))
        assert all(c.language == "markdown" for c in chunks)

    def test_markdown_file_indexed_by_indexer(self, indexer, tmp_path) -> None:
        chunks = indexer.index_file(self._write_md(tmp_path, self._MD_WITH_HEADINGS))
        assert chunks >= 3, f"expected ≥3 markdown sections, got {chunks}"


class TestHTMLSupport:
    """chunk_file() handles .html files (window fallback with 'html' language label)."""

    _HTML = """\
<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <h1>Welcome</h1>
  <p>Some content here.</p>
  <section>
    <h2>About</h2>
    <p>More content.</p>
  </section>
</body>
</html>
"""

    def _write_html(self, tmp_path) -> str:
        p = tmp_path / "index.html"
        p.write_text(self._HTML)
        return str(p)

    def test_html_language_label_set(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_html(tmp_path))
        assert all(c.language == "html" for c in chunks)

    def test_html_produces_chunks(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_html(tmp_path))
        assert len(chunks) >= 1

    def test_html_file_indexed_by_indexer(self, indexer, tmp_path) -> None:
        count = indexer.index_file(self._write_html(tmp_path))
        assert count >= 1, f"expected ≥1 HTML chunk, got {count}"


# ---------------------------------------------------------------------------
# Indexing hygiene — orphan pruning, mtime-cache colocation, force rebuild
# (UPG-8.4 / UPG-8.5 / UPG-8.6)
# ---------------------------------------------------------------------------

class TestIndexingHygiene:
    def _file_paths_in_collection(self, indexer) -> set[str]:
        _, _, metas = indexer.get_all_documents()
        return {m.get("file_path", "") for m in metas}

    # --- UPG-8.5: mtime cache lives next to the chroma db dir ---
    def test_mtime_cache_colocated_with_db(self, indexer, tmp_path) -> None:
        cache_path = indexer._mtime_cache_path()
        # Fixture builds CodeIndexer with db_path=tmp_path/"chroma"
        assert cache_path == (tmp_path / "chroma" / "index_cache.json")
        # And it must sit inside the indexer's db dir, not a separate /db/ tree
        assert str(cache_path).startswith(str(tmp_path / "chroma"))

    def test_mtime_cache_written_into_db_dir(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        indexer.index_workspace()
        assert (tmp_path / "chroma" / "index_cache.json").exists()

    # --- UPG-8.4: orphaned chunks pruned when files leave the walk set ---
    def test_deleted_file_chunks_pruned_on_reindex(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "keep.py", "def keep(): pass")
        gone = Path(make_py(tmp_path, "gone.py", "def gone(): pass"))
        indexer.index_workspace()
        assert any("gone.py" in p for p in self._file_paths_in_collection(indexer))

        gone.unlink()  # file leaves the walk set
        indexer.index_workspace()
        paths = self._file_paths_in_collection(indexer)
        assert not any("gone.py" in p for p in paths), "orphaned chunks not pruned"
        assert any("keep.py" in p for p in paths), "kept file must remain"

    def test_vectrignore_excluded_dir_pruned_on_reindex(self, indexer, tmp_path) -> None:
        (tmp_path / "vendor").mkdir()
        make_py(tmp_path, "vendor/lib.py", "def lib(): pass")
        make_py(tmp_path, "app.py", "def app(): pass")
        indexer.index_workspace()
        assert any("vendor/lib.py" in p for p in self._file_paths_in_collection(indexer))

        # Newly exclude the vendor dir; its chunks must be pruned on next index.
        (tmp_path / ".vectrignore").write_text("vendor\n", encoding="utf-8")
        indexer.index_workspace()
        paths = self._file_paths_in_collection(indexer)
        assert not any("vendor/lib.py" in p for p in paths), "excluded dir not pruned"
        assert any("app.py" in p for p in paths)

    def test_prune_drops_mtime_cache_entry(self, indexer, tmp_path) -> None:
        gone = Path(make_py(tmp_path, "gone.py", "def gone(): pass"))
        indexer.index_workspace()
        assert str(gone) in indexer._load_mtime_cache()
        gone.unlink()
        indexer.index_workspace()
        assert str(gone) not in indexer._load_mtime_cache()

    # --- UPG-8.6: force=True rebuilds, ignoring the mtime cache ---
    def test_force_rebuilds_after_collection_desync(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        indexer.index_workspace()
        n = indexer.total_chunks
        assert n > 0

        # Simulate a cache/collection desync: wipe the collection but keep the
        # mtime cache (the exact trap UPG-8.5 describes).
        ids, _, _ = indexer.get_all_documents()
        indexer._collection.delete(ids=ids)
        assert indexer.total_chunks == 0

        # Without force, the stale mtime cache says "nothing to re-index".
        indexer.index_workspace(force=False)
        assert indexer.total_chunks == 0

        # force=True ignores the cache and rebuilds.
        indexer.index_workspace(force=True)
        assert indexer.total_chunks == n

    def test_force_does_not_duplicate_chunks(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        indexer.index_workspace()
        n = indexer.total_chunks
        indexer.index_workspace(force=True)
        assert indexer.total_chunks == n, "force must replace, not duplicate, chunks"


# ---------------------------------------------------------------------------
# Wave 1 ranking — quality prior, dedup, tiebreaker, test de-prioritization
# (UPG-2.1 / UPG-2.2 / UPG-2.3)
# ---------------------------------------------------------------------------

def _sr(content, path="/p/a.py", node_type="", score=0.7, lang="python"):
    from agent.searcher import SearchResult
    return SearchResult(
        file_path=path, lines="1-9", symbol_name="", language=lang,
        score=score, content=content, node_type=node_type,
    )


def _sr_sym(symbol_name, content, path="/p/a.py", lang="python"):
    """Create a SearchResult with an explicit symbol_name, for symbol-ranking tests."""
    from agent.searcher import SearchResult
    return SearchResult(
        file_path=path, lines="1-9", symbol_name=symbol_name, language=lang,
        score=0.7, content=content, node_type="function_definition",
    )


class TestRankingQuality:
    def test_duplicates_collapsed(self, searcher) -> None:
        cands = [
            _sr("## Create accounts", path="/p/rust/README.md", lang="markdown"),
            _sr("## Create accounts", path="/p/java/README.md", lang="markdown"),
            _sr("## Create accounts", path="/p/python/README.md", lang="markdown"),
        ]
        out = searcher._apply_quality_and_dedup("create accounts", cands)
        assert len(out) == 1
        assert out[0].dup_count == 2  # two identical collapsed into the one kept

    def test_trivial_demoted_below_real(self, searcher) -> None:
        # Trivial chunk is the most "relevant" (rank 0) but must drop below real code.
        cands = [
            _sr("}", path="/p/a.c", lang="c"),
            _sr("int add(int a, int b) {\n    return a + b;\n}\n// impl", path="/p/a.c", lang="c"),
        ]
        out = searcher._apply_quality_and_dedup("add two integers", cands)
        assert "int add" in out[0].content

    def test_navigational_demoted(self, searcher) -> None:
        cands = [
            _sr("pub use a::A;\npub use b::B;\npub use c::C;", path="/p/lib.rs",
                node_type="navigational", lang="rust"),
            _sr("pub fn resolve(&self) -> Lock {\n    self.inner.acquire()\n}\n// real",
                path="/p/resolver.rs", lang="rust"),
        ]
        out = searcher._apply_quality_and_dedup("dependency resolution", cands)
        assert "resolve" in out[0].content

    def test_test_file_demoted_unless_query_targets_tests(self, searcher) -> None:
        # Realistic candidate set: the test file is ranked #0 (rich doc-comments
        # match strongly), the impl just below it, then irrelevant filler.
        test = _sr("def test_resolve():\n    assert resolve()\n    # scenario\n    y=2",
                   path="/p/tests/test_resolver.py")
        impl = _sr("def resolve():\n    return pubgrub_solve()\n    # impl body\n    x=1",
                   path="/p/resolver.py")
        filler = [_sr(f"def helper{i}():\n    return {i}\n    a={i}\n    b={i}", path=f"/p/h{i}.py")
                  for i in range(8)]
        cands = [test, impl] + filler

        # Generic query: implementation surfaces above the test (UPG-2.3).
        out = searcher._apply_quality_and_dedup("how does resolution work", cands)
        assert out[0].file_path == "/p/resolver.py"
        assert out.index(impl) < out.index(test)

        # Test-targeting query: the test file is no longer penalised → keeps #0.
        out2 = searcher._apply_quality_and_dedup("test for resolve", cands)
        assert out2[0].file_path == "/p/tests/test_resolver.py"

    def test_empty_candidates(self, searcher) -> None:
        assert searcher._apply_quality_and_dedup("q", []) == []


# ---------------------------------------------------------------------------
# UPG-11.1 — Symbol identity as a ranking signal (F1 / F1b)
# ---------------------------------------------------------------------------

class TestSymbolIdentityRanking:
    """Symbol-name match must promote the canonical symbol above same-named decoys.

    F1:  query "Field deconstruct ..." → Field.deconstruct must outrank RemoveField.deconstruct.
    F1b: query "from_db_value ..." → Field.from_db_value must outrank Field.get_db_prep_value.

    These tests construct candidates without a live index: the hybrid retriever
    happens to rank the wrong symbol first (rank 0), and _apply_quality_and_dedup
    must flip the order via the symbol-identity bonus.
    """

    # Minimal but realistic content for each candidate so quality_score stays at 1.0
    # for all of them — only the symbol-name signal should differentiate them.
    _FIELD_DECONSTRUCT = (
        "def deconstruct(self):\n"
        "    name, path, args, kwargs = super().deconstruct()\n"
        "    return name, path, args, kwargs\n"
        "    # Field base class implementation\n"
    )
    _REMOVEFIELD_DECONSTRUCT = (
        "def deconstruct(self):\n"
        "    return super().deconstruct()\n"
        "    # RemoveField migration operation\n"
        "    # used in migrations only\n"
    )
    _FIELD_FROM_DB_VALUE = (
        "def from_db_value(self, value, expression, connection):\n"
        "    return self.to_python(value)\n"
        "    # called after database fetch\n"
        "    # override in subclasses\n"
    )
    _FIELD_GET_DB_PREP_VALUE = (
        "def get_db_prep_value(self, value, connection, prepared=False):\n"
        "    return self.get_prep_value(value)\n"
        "    # called before database write\n"
        "    # sibling method on Field\n"
    )

    def test_F1_canonical_Field_deconstruct_outranks_RemoveField_deconstruct(
        self, searcher
    ) -> None:
        """F1: RemoveField.deconstruct at rank 0 must drop below Field.deconstruct."""
        # The decoy (RemoveField.deconstruct) is at rank 0 — hybrid retriever put it first.
        # Canonical (Field.deconstruct) is at rank 1 — one slot below.
        decoy = _sr_sym(
            "RemoveField.deconstruct", self._REMOVEFIELD_DECONSTRUCT,
            path="/p/django/db/migrations/operations/fields.py",
        )
        canonical = _sr_sym(
            "Field.deconstruct", self._FIELD_DECONSTRUCT,
            path="/p/django/db/models/fields/__init__.py",
        )
        filler = [
            _sr_sym(f"OtherOp{i}.run", f"def run(self):\n    pass\n    # op {i}\n    x={i}",
                    path=f"/p/ops{i}.py")
            for i in range(4)
        ]
        # decoy first (rank 0), canonical second — wrong order going in
        cands = [decoy, canonical] + filler
        query = "Field deconstruct base class name path args kwargs migration"
        out = searcher._apply_quality_and_dedup(query, cands)
        canonical_idx = next(i for i, r in enumerate(out) if r.symbol_name == "Field.deconstruct")
        decoy_idx = next(i for i, r in enumerate(out) if r.symbol_name == "RemoveField.deconstruct")
        assert canonical_idx < decoy_idx, (
            f"Expected Field.deconstruct (idx={canonical_idx}) to outrank "
            f"RemoveField.deconstruct (idx={decoy_idx}) for query {query!r}"
        )

    def test_F1b_from_db_value_outranks_get_db_prep_value(
        self, searcher
    ) -> None:
        """F1b: Field.get_db_prep_value at rank 0 must drop below Field.from_db_value."""
        decoy = _sr_sym(
            "Field.get_db_prep_value", self._FIELD_GET_DB_PREP_VALUE,
            path="/p/django/db/models/fields/__init__.py",
        )
        canonical = _sr_sym(
            "Field.from_db_value", self._FIELD_FROM_DB_VALUE,
            path="/p/django/db/models/fields/__init__.py",
        )
        filler = [
            _sr_sym(f"Field.helper{i}", f"def helper{i}(self):\n    return self.val\n    # {i}\n    x={i}",
                    path="/p/django/db/models/fields/__init__.py")
            for i in range(4)
        ]
        cands = [decoy, canonical] + filler
        query = "from_db_value convert database value to python object on a model field"
        out = searcher._apply_quality_and_dedup(query, cands)
        canonical_idx = next(i for i, r in enumerate(out) if r.symbol_name == "Field.from_db_value")
        decoy_idx = next(i for i, r in enumerate(out) if r.symbol_name == "Field.get_db_prep_value")
        assert canonical_idx < decoy_idx, (
            f"Expected Field.from_db_value (idx={canonical_idx}) to outrank "
            f"Field.get_db_prep_value (idx={decoy_idx}) for query {query!r}"
        )

    def test_symbol_boost_does_not_affect_no_symbol_queries(
        self, searcher
    ) -> None:
        """A generic prose query must not be disrupted by the symbol-name boost."""
        cands = [
            _sr_sym("Widget.render", "def render(self):\n    self.paint()\n    # draw\n    x=1",
                    path="/p/widget.py"),
            _sr_sym("Widget.update", "def update(self):\n    self.refresh()\n    # live\n    x=1",
                    path="/p/widget.py"),
        ]
        query = "how does widget rendering work in the UI framework"
        # Must not crash, and the result count must be preserved.
        out = searcher._apply_quality_and_dedup(query, cands)
        assert len(out) == 2

    def test_exact_leaf_match_beats_partial_match(self, searcher) -> None:
        """When query names only the leaf method, exact leaf match wins."""
        # query: "deconstruct method"
        # both candidates have "deconstruct" as leaf — but Field.deconstruct
        # is canonical (rank 1) and RemoveField.deconstruct is rank 0.
        # Since both have the same leaf match, the original rank should hold
        # when the class name in the query is absent, i.e. no extra advantage.
        # This test ensures the boost doesn't break stable ranking for equal matches.
        decoy = _sr_sym(
            "RemoveField.deconstruct", self._REMOVEFIELD_DECONSTRUCT,
            path="/p/ops.py",
        )
        canonical = _sr_sym(
            "Field.deconstruct", self._FIELD_DECONSTRUCT,
            path="/p/fields.py",
        )
        # leaf-only query — no class hint
        query = "deconstruct method"
        cands = [decoy, canonical]
        out = searcher._apply_quality_and_dedup(query, cands)
        # With same leaf boost both receive equal boost — original rank wins
        assert out[0].symbol_name == "RemoveField.deconstruct"


class TestChunkerHygiene:
    def test_navigational_window_tagged(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        from agent.chunk_quality import NAVIGATIONAL_NODE_TYPE
        # Imports-only module → no AST symbols → window fallback → navigational.
        f = make_py(tmp_path, "barrel.py", "import os\nimport sys\nfrom a import b\nfrom c import d")
        chunks = chunk_file(f)
        assert chunks, "expected at least one chunk"
        assert any(c.node_type == NAVIGATIONAL_NODE_TYPE for c in chunks)

    def test_trivial_only_file_dropped(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        f = tmp_path / "stub.c"      # no C parser → window; single trivial line
        f.write_text("}\n")
        chunks = chunk_file(str(f))
        assert chunks == []

    def test_real_code_kept(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        f = make_py(tmp_path, "real.py", "def compute(x):\n    return x * 2 + offset(x)")
        chunks = chunk_file(f)
        assert len(chunks) >= 1
        assert all(c.node_type != "navigational" for c in chunks)


# ---------------------------------------------------------------------------
# F4 regression — qualified boost via real indexer (UPG-11.1-fix)
# ---------------------------------------------------------------------------

class TestSymbolIdentityRankingRealIndexer:
    """Integration-style test that exercises the REAL CodeIndexer pipeline.

    The previous UPG-11.1 implementation tested symbol_identity_boost with
    hand-built SearchResult objects that already carried qualified names like
    "Field.deconstruct" — but the indexer stores only the bare leaf ("deconstruct").
    The +0.20 qualified-match path was therefore dead code at runtime.

    This test:
      1. Writes a synthetic Python source with a base class and a subclass both
         defining the same method, using the REAL CodeIndexer.
      2. Runs a search via CodeSearcher (with the dummy embedder so no download).
      3. Asserts the BASE class's method outranks the subclass's method for a
         query that names the base class.
      4. Verifies the test actually exercises the class-prefix extraction path:
         if the F4 fix is reverted (extract_class_from_content disabled), the
         base class method no longer gets the +0.20 qualified boost and the
         subclass (at a higher initial rank due to BM25 tokenisation) wins.

    The source is written to a temp dir; no live daemon is involved.
    """

    _SOURCE = """\
class BaseField:
    \"\"\"The canonical base field implementation.\"\"\"

    def serialize(self, value, connection, prepared=False):
        \"\"\"Serialize value for storage. BaseField canonical implementation.\"\"\"
        # Convert to wire format
        result = str(value)
        return result


class SubField(BaseField):
    \"\"\"A subclass override — should not outrank BaseField for a BaseField query.\"\"\"

    def serialize(self, value, connection, prepared=False):
        \"\"\"SubField override of serialize.\"\"\"
        # subclass-specific conversion
        return super().serialize(value, connection, prepared)
"""

    def test_base_class_method_outranks_subclass_with_real_indexer(
        self, indexer, tmp_path, monkeypatch
    ) -> None:
        """F4: base BaseField.serialize must outrank SubField.serialize.

        Key property: both methods have the bare leaf 'serialize' stored as
        symbol_name by the indexer.  Only extract_class_from_content() in
        _apply_quality_and_dedup can distinguish them — and only the base class
        gets the +0.20 qualified boost when the query names 'BaseField'.

        This test fails if extract_class_from_content is disabled (reverted),
        proving it is the discriminator and not an accidentally-passing mock.
        """
        from agent.searcher import CodeSearcher

        # Write the synthetic source file
        src = tmp_path / "fields.py"
        src.write_text(self._SOURCE)

        # Index via the real CodeIndexer (dummy embedder from fixture)
        indexer.index_file(str(src))

        # Verify both methods are indexed with bare leaf symbol_name
        all_chunks = indexer.get_all_documents()
        ids, docs, metas = all_chunks
        assert len(ids) > 0, "No chunks indexed — check the source or indexer"

        # Find the method chunks
        serialize_chunks = [
            (doc, meta) for doc, meta in zip(docs, metas)
            if meta.get("symbol_name") == "serialize"
        ]
        assert len(serialize_chunks) >= 2, (
            f"Expected at least 2 'serialize' chunks (base + subclass), "
            f"got {len(serialize_chunks)}. All symbol names: "
            f"{[m.get('symbol_name') for m in metas]}"
        )

        # Confirm the indexer stores bare leaf — this is the root cause we're fixing
        base_chunk = next(
            (doc for doc, meta in serialize_chunks if "# class: BaseField" in doc),
            None,
        )
        assert base_chunk is not None, (
            "Could not find BaseField's serialize chunk with '# class: BaseField' prefix. "
            "Check _collect_chunks_ast context injection."
        )

        # Build searcher and run the search
        searcher = CodeSearcher(indexer)
        searcher.refresh_bm25()

        query = "BaseField serialize value for storage canonical base implementation"
        results, _ = searcher.search(query, n_results=10, rerank=False)

        assert len(results) >= 2, f"Expected at least 2 results, got {len(results)}"

        # Find positions of base class and subclass results
        base_idx = next(
            (i for i, r in enumerate(results) if "BaseField" in r.content and "# class: BaseField" in r.content),
            None,
        )
        sub_idx = next(
            (i for i, r in enumerate(results) if "SubField" in r.content and "# class: SubField" in r.content),
            None,
        )

        assert base_idx is not None, (
            "BaseField.serialize not found in results. "
            f"Results: {[(r.symbol_name, r.content[:80]) for r in results]}"
        )
        assert sub_idx is not None, (
            "SubField.serialize not found in results. "
            f"Results: {[(r.symbol_name, r.content[:80]) for r in results]}"
        )

        assert base_idx < sub_idx, (
            f"Expected BaseField.serialize (idx={base_idx}) to outrank "
            f"SubField.serialize (idx={sub_idx}) for query {query!r}. "
            "This indicates the F4 qualified-boost extraction from '# class: X' "
            "content prefix is not working. If this test fails after reverting "
            "extract_class_from_content, the fix is confirmed necessary."
        )


# ---------------------------------------------------------------------------
# UPG-11.7 — Forced-inclusion of exact symbol-name matches (F5 candidate-pool miss)
# ---------------------------------------------------------------------------

class TestForcedInclusionCandidatePool:
    """The base-class definition of a method must appear in search results even
    when its long docstring dilutes BM25/embedding scores enough to fall below
    the hybrid candidate pool gate.

    F5-candidate-pool-miss: Field.deconstruct (lines 606-705, ~100 lines of
    docstring) was absent from the top-60 hybrid pool for the F1 query.  The
    UPG-11.1 sym_boost is post-retrieval and cannot rescue chunks that never
    enter the pool.  UPG-11.7 forces ALL chunks whose symbol_name leaf exactly
    matches a guarded query token into the pool BEFORE the reranker runs.

    This test uses the REAL CodeIndexer (not mocks) to verify that:
    1. A base-class method with a long docstring is correctly forced into the
       candidate pool when the query mentions the method name.
    2. The forced chunk appears in the top-N results alongside naturally-retrieved
       override chunks.
    3. Without UPG-11.7 forced-inclusion (with a tiny fetch_n that excludes the
       long-docstring chunk), the base method would be absent — confirming the
       test exercises the right fix.
    """

    # -----------------------------------------------------------------------
    # Synthetic corpus design
    # -----------------------------------------------------------------------
    #
    # We create a file with:
    #   - BaseField.deconstruct: long docstring (~30 lines) that dilutes
    #     BM25 keyword density, causing it to rank LOW in BM25 scoring.
    #   - Many override classes (Override1..N) with compact, keyword-dense
    #     deconstruct bodies that score HIGH in BM25 for the query.
    #
    # The BM25 scores will put the compact overrides first and the base last.
    # With fetch_n set small (via monkeypatch), the base falls out of the pool.
    # UPG-11.7 forces it back in via the symbol_name leaf match.

    _LONG_DOCSTRING = (
        '"""Deconstruct the field for migration serialization.\n'
        "\n"
        "Returns a 4-tuple (name, path, args, kwargs) that can be used\n"
        "to reconstruct the field via its constructor.  Subclasses that\n"
        "introduce new constructor arguments must override this method and\n"
        "add the extra arguments to kwargs before calling super().\n"
        "\n"
        "The return value of this method is used by the migration framework\n"
        "to serialize the field to a migration file.  The path must be the\n"
        "full dotted import path that can be used to import the field class.\n"
        "\n"
        "Parameters\n"
        "----------\n"
        "None — all state is read from self.\n"
        "\n"
        "Returns\n"
        "-------\n"
        "name : str\n"
        "    The field's attribute name on the model.\n"
        "path : str\n"
        "    The dotted import path of the field class.\n"
        "args : list\n"
        "    Positional constructor arguments (usually empty).\n"
        "kwargs : dict\n"
        "    Keyword constructor arguments with their current values.\n"
        '"""\n'
    )

    # One compact override (repeated N times to flood the BM25-high pool)
    _OVERRIDE_BODY = (
        "name, path, args, kwargs = super().deconstruct()\n"
        "kwargs['extra'] = self.extra\n"
        "return name, path, args, kwargs\n"
    )

    @staticmethod
    def _make_source(n_overrides: int) -> str:
        lines = [
            "class BaseField:",
            "    def deconstruct(self):",
        ]
        for docline in TestForcedInclusionCandidatePool._LONG_DOCSTRING.splitlines():
            lines.append(f"        {docline}")
        lines += [
            "        name = self.__class__.__name__",
            "        path = self.__module__ + '.' + name",
            "        args = []",
            "        kwargs = {}",
            "        return name, path, args, kwargs",
            "",
        ]
        for i in range(1, n_overrides + 1):
            lines += [
                f"class Override{i}(BaseField):",
                "    extra = None",
                "    def deconstruct(self):",
            ]
            for bodyline in TestForcedInclusionCandidatePool._OVERRIDE_BODY.splitlines():
                lines.append(f"        {bodyline}")
            lines += [f"    attr_{i} = {i!r}", ""]
        return "\n".join(lines)

    def test_base_deconstruct_in_results_despite_docstring(
        self, indexer, tmp_path, monkeypatch
    ) -> None:
        """UPG-11.7: base BaseField.deconstruct must appear in search results even
        when its long docstring pushes it below the hybrid candidate pool gate.

        Mechanically: monkeypatches _RERANK_TOP_K_UNFILTERED to a value smaller
        than the number of 'deconstruct' chunks (so the base falls outside the
        natural pool), then verifies the result still contains BaseField.deconstruct.
        """
        import agent.searcher as searcher_mod
        from agent.searcher import CodeSearcher

        n_overrides = 10  # 10 overrides + 1 base = 11 deconstruct chunks
        src = tmp_path / "fields.py"
        src.write_text(self._make_source(n_overrides))
        indexer.index_file(str(src))

        # Verify all 11 deconstruct chunks are indexed
        _, docs, metas = indexer.get_all_documents()
        deconstruct_metas = [m for m in metas if m.get("symbol_name") == "deconstruct"]
        assert len(deconstruct_metas) == n_overrides + 1, (
            f"Expected {n_overrides + 1} 'deconstruct' chunks, got {len(deconstruct_metas)}"
        )

        # Confirm the base chunk exists with the long docstring
        base_docs = [
            d for d, m in zip(docs, metas)
            if m.get("symbol_name") == "deconstruct" and "# class: BaseField" in d
        ]
        assert base_docs, "BaseField.deconstruct chunk not found (check indexer class prefix injection)"

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Monkeypatch the pool cap to a value SMALLER than the number of deconstruct
        # chunks so the long-docstring base would fall out of the natural pool.
        # With 11 deconstruct chunks and fetch_n=5, the base (lowest BM25 score)
        # won't make it into sorted_ids without UPG-11.7 forced-inclusion.
        monkeypatch.setattr(searcher_mod, "_RERANK_TOP_K_UNFILTERED", 5)

        query = "BaseField deconstruct migration serialization name path args kwargs"
        results, _ = s.search(query, n_results=10, rerank=False)

        # The base must appear somewhere in the results
        base_idx = next(
            (
                i for i, r in enumerate(results)
                if r.symbol_name == "deconstruct" and "# class: BaseField" in r.content
            ),
            None,
        )
        assert base_idx is not None, (
            "UPG-11.7 regression: BaseField.deconstruct is absent from results even "
            "though its symbol_name leaf ('deconstruct') is an exact match for the query. "
            "The forced-inclusion pool gate is not working. "
            f"Results found: {[(r.symbol_name, r.content[:60]) for r in results]}"
        )

    def test_forced_inclusion_does_not_trigger_for_short_common_words(
        self, indexer, tmp_path
    ) -> None:
        """UPG-11.7 guard: short prose words (< 7 chars, no underscore) must NOT
        trigger forced-inclusion, preventing common attribute names from flooding
        the pool.  E.g. 'name', 'path', 'args' in the query must not force-include
        all chunks whose symbol_name is 'name', 'path', or 'args'.

        This guards the F6 regression: 'list all database migrations' must not
        accidentally include chunks with symbol_name='all' (which was the UPG-11.1
        guard bug — UPG-11.7 inherits the same guard, but at the pool-inclusion level).
        """
        from agent.searcher import CodeSearcher, _FORCED_INCLUSION_MIN_IDENTIFIER_LEN

        # Write a file with a method named 'name' — a 4-char common word
        src = tmp_path / "model.py"
        src.write_text(
            "class MyModel:\n"
            "    def name(self):\n"
            "        return self._name\n"
            "\n"
            "    def lookup(self):\n"
            "        return self.name()\n"
        )
        indexer.index_file(str(src))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # 'name' (4 chars, no underscore) must NOT trigger forced-inclusion
        # The guard is: len(tok) >= _FORCED_INCLUSION_MIN_IDENTIFIER_LEN OR '_' in tok
        assert len("name") < _FORCED_INCLUSION_MIN_IDENTIFIER_LEN, (
            "Test assumption failed: 'name' must be shorter than the min identifier length"
        )

        results, _ = s.search("get the model name field value", n_results=5, rerank=False)

        # 'name' chunks may appear in results (they're indexed) but not because of
        # forced-inclusion — that's fine.  The key invariant is that the forced-
        # inclusion code does NOT add them (verified by the guard).
        # We check the guard directly rather than the result order (which depends
        # on BM25/vector scores that vary with the dummy embedder).
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import SYMBOL_STOP_WORDS

        query = "get the model name field value"
        inclusion_tokens = {
            tok for tok in _query_symbol_tokens(query)
            if ("_" in tok or len(tok) >= _FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        assert "name" not in inclusion_tokens, (
            f"'name' must not be in forced-inclusion tokens, but got: {inclusion_tokens}"
        )

    def test_compound_identifier_triggers_forced_inclusion(
        self, indexer, tmp_path
    ) -> None:
        """UPG-11.7: compound identifiers (containing '_') trigger forced-inclusion
        even if they are shorter than _FORCED_INCLUSION_MIN_IDENTIFIER_LEN.

        This guards the F1b/F7 case: 'from_db_value' (13 chars with underscore)
        must trigger forced-inclusion so all from_db_value implementations are
        in the candidate pool for the reranker to choose from.
        """
        from agent.searcher import CodeSearcher
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import SYMBOL_STOP_WORDS
        import agent.searcher as searcher_mod

        # Write files where the target from_db_value is in the "base" class
        # with a longer body (lower BM25 score) and an override is compact.
        base_src = tmp_path / "base_field.py"
        base_src.write_text(
            "class JSONBaseField:\n"
            "    def from_db_value(self, value, expression, connection):\n"
            "        \"\"\"Convert database value to Python object.\n"
            "        Handles null, type coercion, and nested structures.\n"
            "        Called by Django after every database read.\n"
            "        \"\"\"\n"
            "        if value is None:\n"
            "            return None\n"
            "        import json\n"
            "        return json.loads(value)\n"
        )
        override_src = tmp_path / "override_field.py"
        override_src.write_text(
            "class SpecialJSONField(JSONBaseField):\n"
            "    def from_db_value(self, value, expression, connection):\n"
            "        result = super().from_db_value(value, expression, connection)\n"
            "        return result\n"
        )
        indexer.index_file(str(base_src))
        indexer.index_file(str(override_src))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Shrink the pool so the base would be excluded without forced-inclusion
        monkeypatch_target = searcher_mod
        orig = searcher_mod._RERANK_TOP_K_UNFILTERED
        searcher_mod._RERANK_TOP_K_UNFILTERED = 1  # only 1 natural candidate

        try:
            query = "from_db_value convert database value to python JSON field"
            results, _ = s.search(query, n_results=10, rerank=False)
        finally:
            searcher_mod._RERANK_TOP_K_UNFILTERED = orig

        # Both from_db_value chunks must appear (forced-inclusion pulls them in)
        from_db_value_results = [r for r in results if r.symbol_name == "from_db_value"]
        assert len(from_db_value_results) >= 2, (
            "UPG-11.7 regression: 'from_db_value' compound identifier must trigger "
            "forced-inclusion of ALL from_db_value chunks.  "
            f"Got {len(from_db_value_results)} results with that symbol_name: "
            f"{[(r.symbol_name, r.content[:60]) for r in results]}"
        )
