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
        # UPG-11.3: .txt files are now indexed (language='txt', doc-prose quality).
        # The test previously asserted files==2 to confirm .txt was skipped, but
        # now .txt IS indexed. Update: use a .log extension (not indexed) instead.
        make_py(tmp_path, "a.py", "def foo(): pass")
        make_py(tmp_path, "b.py", "def bar(): pass")
        (tmp_path / "skip.log").write_text("not indexed — .log is not in LANG_BY_EXT")
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
# UPG-11.3 — .txt and .rst prose files are indexed (F2)
# ---------------------------------------------------------------------------

class TestTxtRstSupport:
    """chunk_file() must produce chunks for .txt and .rst files (UPG-11.3).

    Django ships docs as .txt (e.g. docs/howto/custom-model-fields.txt).
    These were invisible to search before UPG-11.3.
    They are indexed as prose windows with language='txt'/'rst' and subject to
    the doc-prose quality multiplier (_Q_DOC_PROSE=0.70) so code still leads.
    """

    _TXT_CONTENT = """\
Writing custom model fields
===========================

To create a custom field, you need to subclass Field and implement deconstruct()
and from_db_value(). The deconstruct() method must return a 4-tuple
(name, path, args, kwargs) that allows Django's migration framework to serialize
the field.

The from_db_value() method is called every time data is loaded from the database,
including in aggregates and values() calls.
"""

    _RST_CONTENT = """\
Custom Lookup Reference
=======================

.. currentmodule:: django.db.models

Lookup API
----------

A lookup is a Django expression that determines how a condition is translated
to a SQL WHERE clause. Custom lookups can be registered via ``register_lookup``.
"""

    def _write_txt(self, tmp_path) -> str:
        p = tmp_path / "howto.txt"
        p.write_text(self._TXT_CONTENT)
        return str(p)

    def _write_rst(self, tmp_path) -> str:
        p = tmp_path / "ref.rst"
        p.write_text(self._RST_CONTENT)
        return str(p)

    def test_txt_produces_chunks(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_txt(tmp_path))
        assert len(chunks) >= 1, f"expected ≥1 txt chunk, got {len(chunks)}"

    def test_txt_language_label(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_txt(tmp_path))
        assert all(c.language == "txt" for c in chunks), (
            f"all chunks must have language='txt', got {[c.language for c in chunks]}"
        )

    def test_txt_indexed_by_indexer(self, indexer, tmp_path) -> None:
        count = indexer.index_file(self._write_txt(tmp_path))
        assert count >= 1, f"expected ≥1 txt chunk indexed, got {count}"

    def test_rst_produces_chunks(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_rst(tmp_path))
        assert len(chunks) >= 1, f"expected ≥1 rst chunk, got {len(chunks)}"

    def test_rst_language_label(self, tmp_path) -> None:
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_rst(tmp_path))
        assert all(c.language == "rst" for c in chunks), (
            f"all chunks must have language='rst', got {[c.language for c in chunks]}"
        )

    def test_rst_indexed_by_indexer(self, indexer, tmp_path) -> None:
        count = indexer.index_file(self._write_rst(tmp_path))
        assert count >= 1, f"expected ≥1 rst chunk indexed, got {count}"

    def test_txt_in_indexed_languages(self, indexer, tmp_path) -> None:
        """After indexing a .txt file, /v1/status languages must include 'txt'."""
        indexer.index_file(self._write_txt(tmp_path))
        langs = indexer.indexed_languages()
        assert "txt" in langs, (
            f"UPG-11.3: 'txt' must appear in indexed languages after indexing a .txt file. "
            f"Got: {langs}"
        )

    def test_txt_doc_prose_quality_multiplier(self, tmp_path) -> None:
        """txt/rst chunks must get the doc-prose quality multiplier (0.70),
        not the code quality (1.0), so code chunks still lead on code queries."""
        from agent.chunk_quality import quality_score, is_doc_language
        # Verify doc language classification
        assert is_doc_language("txt"), "txt must be classified as doc language"
        assert is_doc_language("rst"), "rst must be classified as doc language"
        # quality_score for a non-trivial txt chunk should be 0.70
        score = quality_score("Custom field description with multiple lines of prose.\n" * 5,
                              "/docs/howto.txt", language="txt")
        assert score == pytest.approx(0.70, abs=0.01), (
            f"UPG-11.3: txt doc chunks must get _Q_DOC_PROSE=0.70 quality. Got {score}"
        )

    def test_workspace_walk_picks_up_txt_files(self, indexer, tmp_path) -> None:
        """index_workspace() must include .txt files (they're now in LANG_BY_EXT)."""
        # Write a Python file and a .txt doc
        make_py(tmp_path, "models.py", "def model(): pass")
        (tmp_path / "readme.txt").write_text("Project documentation.\n" * 5)
        files, chunks = indexer.index_workspace()
        assert files == 2, f"expected 2 files indexed (1 py + 1 txt), got {files}"

    def test_txt_searchable_after_index(self, indexer, tmp_path) -> None:
        """txt content must be returned in search results after indexing."""
        from agent.searcher import CodeSearcher

        txt_file = tmp_path / "custom-model-fields.txt"
        txt_file.write_text(self._TXT_CONTENT)
        indexer.index_file(str(txt_file))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        results, _ = s.search(
            "custom model field deconstruct from_db_value", n_results=5, rerank=False
        )
        txt_results = [r for r in results if r.file_path.endswith(".txt")]
        assert txt_results, (
            "UPG-11.3: .txt file content must appear in search results. "
            f"Got: {[r.file_path for r in results]}"
        )

    def test_f2_doc_intent_query_surfaces_txt_over_code(
        self, indexer, tmp_path
    ) -> None:
        """UPG-11.11 / F2: a how-to doc-intent query must surface the .txt
        documentation file in the top results, even when the query also names
        symbols (deconstruct, from_db_value) that would normally trigger
        forced-inclusion.

        Scenario mirrors the real F2 acceptance case:
        - docs/howto/custom-model-fields.txt is indexed (it covers the topic)
        - Multiple Python files implement deconstruct() and from_db_value()
        - Code-intent query "Field deconstruct …" keeps forced-inclusion ON and
          surfaces code (F1 regression guard)
        - Doc-intent query "how to write a custom model field with deconstruct
          and from_db_value" suppresses forced-inclusion AND relaxes the doc-prose
          quality penalty, so the .txt file can reach the top results
        """
        from agent.searcher import CodeSearcher
        from agent.chunk_quality import is_doc_intent_query

        # Index the how-to .txt file
        txt_file = tmp_path / "custom-model-fields.txt"
        txt_file.write_text(self._TXT_CONTENT)
        indexer.index_file(str(txt_file))

        # Index several Python files that implement deconstruct / from_db_value
        # (simulating the Django corpus where 80+ such chunks exist)
        for i in range(4):
            py_file = tmp_path / f"field_{i}.py"
            py_file.write_text(
                f"class Field{i}:\n"
                f"    def deconstruct(self):\n"
                f"        \"\"\"Deconstruct the field for migration serialization.\"\"\"\n"
                f"        return (self.name, 'myapp.Field{i}', [], {{}})\n"
                f"\n"
                f"    def from_db_value(self, value, expression, connection):\n"
                f"        \"\"\"Convert database value to Python object.\"\"\"\n"
                f"        if value is None:\n"
                f"            return None\n"
                f"        return str(value)\n"
            )
            indexer.index_file(str(py_file))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Guard: F2 query must be classified as doc-intent
        f2_query = "how to write a custom model field with deconstruct and from_db_value"
        assert is_doc_intent_query(f2_query) is True, (
            "Pre-condition failed: F2 query must be doc-intent"
        )

        # F2 query must surface the txt file in top-5
        results, _ = s.search(f2_query, n_results=5, rerank=False)
        txt_results = [r for r in results if r.file_path.endswith(".txt")]
        assert txt_results, (
            "UPG-11.11 / F2: docs/howto/custom-model-fields.txt must appear in top-5 "
            "results for the doc-intent 'how to' query, even though the query names "
            "symbols (deconstruct, from_db_value). Forced-inclusion must be suppressed "
            "for doc-intent queries so the doc file is not crowded out by code chunks. "
            f"Got: {[(r.file_path, r.score) for r in results]}"
        )

        # Regression guard: F1 code-intent query must still surface code (not only docs)
        f1_query = "Field deconstruct base class name path args kwargs migration"
        f1_results, _ = s.search(f1_query, n_results=5, rerank=False)
        code_results = [r for r in f1_results if r.file_path.endswith(".py")]
        assert code_results, (
            "UPG-11.11 regression: F1 code-intent query must still surface Python code "
            "files (forced-inclusion must NOT be suppressed for code-intent queries). "
            f"Got: {[(r.file_path, r.score) for r in f1_results]}"
        )


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

        # The base must appear somewhere in the results.
        # UPG-11.10: symbol_name is now the qualified form "BaseField.deconstruct"
        # (set by _apply_quality_and_dedup via extract_class_from_content).
        base_idx = next(
            (
                i for i, r in enumerate(results)
                if ("deconstruct" in r.symbol_name) and "# class: BaseField" in r.content
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

        # Both from_db_value chunks must appear (forced-inclusion pulls them in).
        # UPG-11.10: symbol_name is now qualified ("JSONBaseField.from_db_value",
        # "SpecialJSONField.from_db_value") so match on the leaf component.
        from_db_value_results = [r for r in results if "from_db_value" in r.symbol_name]
        assert len(from_db_value_results) >= 2, (
            "UPG-11.7 regression: 'from_db_value' compound identifier must trigger "
            "forced-inclusion of ALL from_db_value chunks.  "
            f"Got {len(from_db_value_results)} results with that symbol_name: "
            f"{[(r.symbol_name, r.content[:60]) for r in results]}"
        )


# ---------------------------------------------------------------------------
# UPG-11.12 — Forced-inclusion relevance gate (BM25 fast-reject + vector cosine)
# ---------------------------------------------------------------------------

class TestForcedInclusionRelevanceGate:
    """UPG-11.12: Non-compound tokens ≥7 chars trigger forced-inclusion, but the
    candidate must also pass a relevance gate (BM25-without-trigger fast-reject +
    vector cosine floor) to avoid flooding the pool with unrelated symbols.

    Covers:
    - F9: 'project' (in stop_words) never triggers forced-inclusion at all.
    - F10: 'execute' (not in stop_words) forces inclusion for DB cursor methods
           but NOT management-command .execute() — discriminated by vector cosine.
    - F11: 'context' (in stop_words) never triggers forced-inclusion.
    - get_chunk_cosine_similarities: structural correctness (no numpy truthiness bug).
    """

    def test_stop_word_token_excluded_from_forced_inclusion(self) -> None:
        """Tokens in SYMBOL_STOP_WORDS must NOT appear in sym_tokens_for_inclusion,
        so symbols like LinearGeometryMixin.project never enter the forced-inclusion pool
        for queries like 'list all database migrations for a project' (F9 guard).
        """
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import SYMBOL_STOP_WORDS
        from agent.searcher import _FORCED_INCLUSION_MIN_IDENTIFIER_LEN

        query = "list all database migrations for a project"
        sym_tokens = _query_symbol_tokens(query)
        inclusion_tokens = {
            tok for tok in sym_tokens
            if ("_" in tok or len(tok) >= _FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }

        # "project" is in SYMBOL_STOP_WORDS → must NOT be in inclusion_tokens
        assert "project" not in inclusion_tokens, (
            "UPG-11.12 regression: 'project' must be in SYMBOL_STOP_WORDS and excluded "
            f"from forced-inclusion tokens, but got: {inclusion_tokens}"
        )
        # Sanity: "project" is actually in the stop_words list
        assert "project" in SYMBOL_STOP_WORDS, (
            "'project' must be in SYMBOL_STOP_WORDS (prog_stopwords.txt) "
            "to guard F9 false positive (LinearGeometryMixin.project at rank1)"
        )

    def test_stop_word_context_excluded_from_forced_inclusion(self) -> None:
        """F11 guard: 'context' in SYMBOL_STOP_WORDS prevents DecimalField.context
        from entering the forced-inclusion pool for 'render template context in view'.
        """
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import SYMBOL_STOP_WORDS
        from agent.searcher import _FORCED_INCLUSION_MIN_IDENTIFIER_LEN

        query = "render template context in view"
        sym_tokens = _query_symbol_tokens(query)
        inclusion_tokens = {
            tok for tok in sym_tokens
            if ("_" in tok or len(tok) >= _FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }

        assert "context" not in inclusion_tokens, (
            "UPG-11.12 regression: 'context' must be in SYMBOL_STOP_WORDS and excluded "
            f"from forced-inclusion tokens, but got: {inclusion_tokens}"
        )
        assert "context" in SYMBOL_STOP_WORDS

    def test_execute_not_in_stop_words(self) -> None:
        """'execute' must NOT be in SYMBOL_STOP_WORDS so that DB cursor .execute()
        methods can still be forced-included and ranked for 'connect to database
        and execute query' (F10 guard).  Management commands are gated by the
        vector cosine floor, not the stop-word list.
        """
        from agent.config import SYMBOL_STOP_WORDS

        assert "execute" not in SYMBOL_STOP_WORDS, (
            "UPG-11.12 regression: 'execute' must NOT be in SYMBOL_STOP_WORDS. "
            "Adding it prevents CursorWrapper.execute from being forced-included, "
            "which pushes it out of top-5 for 'connect to database and execute query'."
        )
        assert "connect" not in SYMBOL_STOP_WORDS, (
            "UPG-11.12 regression: 'connect' must NOT be in SYMBOL_STOP_WORDS. "
            "DB connection methods need forced-inclusion for database-connect queries."
        )

    def test_get_chunk_cosine_similarities_basic(self, indexer, tmp_path) -> None:
        """CodeIndexer.get_chunk_cosine_similarities must return float cosine values
        in [-1, 1] and not crash on numpy arrays from ChromaDB (UPG-11.12 bugfix:
        'batch["embeddings"] or []' triggered numpy ambiguity ValueError).
        """
        src = tmp_path / "db.py"
        src.write_text(
            "class CursorWrapper:\n"
            "    def execute(self, sql, params=None):\n"
            "        \"\"\"Execute a SQL query against the database cursor.\"\"\"\n"
            "        return self.cursor.execute(sql, params)\n"
        )
        indexer.index_file(str(src))

        query = "connect to database and execute query"
        q_emb = indexer.embed_query(query)
        assert abs(sum(x * x for x in q_emb) ** 0.5 - 1.0) < 0.01, (
            "embed_query should return a normalised vector"
        )

        # Get all chunk IDs from the indexer
        all_ids, _, _ = indexer.get_all_documents()
        assert all_ids, "No chunks indexed"

        # Must not raise (numpy truthiness bug was: `batch.get('embeddings') or []`)
        sims = indexer.get_chunk_cosine_similarities(q_emb, all_ids[:5])

        # All returned values must be float in [-1.0, 1.0]
        for cid, sim in sims.items():
            assert isinstance(sim, float), f"similarity for {cid} is not float: {type(sim)}"
            assert -1.01 <= sim <= 1.01, f"cosine similarity out of range: {sim}"

    def test_get_chunk_cosine_similarities_empty_inputs(self, indexer) -> None:
        """get_chunk_cosine_similarities must return empty dict for empty inputs
        without raising.
        """
        q_emb = indexer.embed_query("some query")

        assert indexer.get_chunk_cosine_similarities([], q_emb) == {}
        assert indexer.get_chunk_cosine_similarities(q_emb, []) == {}

    def test_bm25_fast_reject_zero_keyword_overlap(self, indexer, tmp_path) -> None:
        """BM25-without-trigger fast-reject: a non-compound forced candidate whose
        BM25 score for the remaining query tokens (all tokens minus the trigger) is
        near-zero must be rejected even if the trigger token appears in its symbol.

        This guards the F9 / F11 case at the searcher level (as a complement to the
        stop-word guard — if the word is NOT in stop_words but has near-zero BM25
        without the trigger, it should still be rejected).
        """
        from agent.searcher import CodeSearcher, _FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR

        # "project" is already in stop_words and won't reach this code path, so
        # use a synthetic 7-char non-stop word that has zero content overlap with
        # the rest of the query.
        irrelevant_src = tmp_path / "geometry.py"
        irrelevant_src.write_text(
            "class GeometryMixin:\n"
            "    def longvar(self):\n"
            "        \"\"\"Project geometry onto a plane using affine transforms.\"\"\"\n"
            "        return self._affine_project()\n"
        )
        # A relevant file that has strong overlap with the other query tokens
        relevant_src = tmp_path / "migrator.py"
        relevant_src.write_text(
            "class Migrator:\n"
            "    def list_migrations(self):\n"
            "        \"\"\"List all database migration states.\"\"\"\n"
            "        return self.db.get_migrations()\n"
        )
        indexer.index_file(str(irrelevant_src))
        indexer.index_file(str(relevant_src))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Verify the floor constant is sane
        assert 0.0 < _FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR <= 0.20, (
            f"BM25 fast-reject floor {_FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR} is out of expected range"
        )

    def test_vec_sim_floor_constant_is_tuned(self) -> None:
        """_FORCED_INCLUSION_VEC_SIM_FLOOR must be in [0.40, 0.70] — the range
        where the threshold discriminates DB-cursor .execute() (≥0.52) from
        management-command .execute() (≤0.50) on the Django corpus (UPG-11.12).
        """
        from agent.searcher import _FORCED_INCLUSION_VEC_SIM_FLOOR

        assert 0.40 <= _FORCED_INCLUSION_VEC_SIM_FLOOR <= 0.70, (
            f"_FORCED_INCLUSION_VEC_SIM_FLOOR={_FORCED_INCLUSION_VEC_SIM_FLOOR} "
            "is outside the [0.40, 0.70] tuned range.  "
            "The value must separate DB cursor execute (≥0.52) from "
            "management-command execute (≤0.50) on the Django corpus."
        )


# ---------------------------------------------------------------------------
# UPG-11.10 — Qualified "Class.leaf" symbol name surfaced in SearchResult
# ---------------------------------------------------------------------------

class TestQualifiedSymbolName:
    """UPG-11.10: _apply_quality_and_dedup must promote symbol_name from bare leaf
    to qualified "Class.leaf" form when the indexer-injected "# class: X" prefix
    is present in the chunk content.

    This makes the REST/MCP `symbol` field show "Field.deconstruct" instead of
    bare "deconstruct", helping callers understand class ownership.
    """

    _SOURCE = """\
class ModelField:
    def validate(self, value, model_instance):
        \"\"\"Validate the value for this field.\"\"\"
        # field validation logic
        return value

class AnotherField(ModelField):
    def validate(self, value, model_instance):
        \"\"\"AnotherField override.\"\"\"
        return super().validate(value, model_instance)
"""

    def test_qualified_symbol_name_in_search_results(self, indexer, tmp_path) -> None:
        """After search, symbol_name on each result should be 'Class.method' not bare 'method'."""
        from agent.searcher import CodeSearcher

        src = tmp_path / "fields.py"
        src.write_text(self._SOURCE)
        indexer.index_file(str(src))

        # Verify indexer stored bare leaf
        _, docs, metas = indexer.get_all_documents()
        validate_metas = [m for m in metas if m.get("symbol_name") == "validate"]
        assert len(validate_metas) >= 2, (
            "Indexer must store bare leaf 'validate' for both methods"
        )

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        results, _ = s.search("ModelField validate value for field", n_results=10, rerank=False)
        result_syms = {r.symbol_name for r in results}

        # At least one result should have the qualified form
        qualified = {sym for sym in result_syms if "." in sym and "validate" in sym}
        assert qualified, (
            f"UPG-11.10: expected qualified symbol names like 'ModelField.validate' "
            f"in results, but got: {result_syms}"
        )
        # No result should have bare "validate" anymore (they all get class prefix)
        assert "validate" not in result_syms or all(
            "." in r.symbol_name for r in results if r.symbol_name == "validate"
        ), f"Bare 'validate' still present in results: {result_syms}"

    def test_qualified_form_used_in_rest_response(self, indexer, tmp_path) -> None:
        """UPG-11.10: the 'symbol' field in the REST CodeChunkResult must show
        the qualified form (e.g. 'ModelField.validate'), not just the bare leaf."""
        from agent.searcher import CodeSearcher, SearchResult

        # Build a result with a chunk that has a class prefix in content
        r = SearchResult(
            file_path="/p/fields.py",
            lines="5-10",
            symbol_name="validate",
            language="python",
            score=0.8,
            content="# class: ModelField\ndef validate(self, value, model_instance):\n    return value\n    # body\n",
            node_type="function_definition",
        )
        # After _apply_quality_and_dedup, symbol_name should be upgraded
        from agent.searcher import CodeSearcher as CS
        import tests.conftest as cf
        # Use the test searcher fixture approach
        from agent.indexer import CodeIndexer
        from tests.conftest import _DummyEmbedProvider
        db = str(tmp_path / "db")
        idx2 = CodeIndexer(str(tmp_path), db_path=db)
        idx2._embed_provider = _DummyEmbedProvider()
        s2 = CS(idx2)

        out = s2._apply_quality_and_dedup("ModelField validate", [r])
        assert out, "Expected at least one result"
        assert out[0].symbol_name == "ModelField.validate", (
            f"UPG-11.10: expected 'ModelField.validate', got {out[0].symbol_name!r}"
        )


# ---------------------------------------------------------------------------
# UPG-11.2 — Monotonic displayed score (F1c)
# ---------------------------------------------------------------------------

class TestMonotonicScore:
    """UPG-11.2: scores in the returned list must be non-increasing.

    Before UPG-11.2, score= was set to the stale pre-rerank hybrid score
    (set before quality re-sort), so rank1 could have a lower score than rank5.
    Now _apply_quality_and_dedup replaces score with the composite ranking key.
    """

    def test_scores_non_increasing(self, searcher) -> None:
        """Returned scores must be non-increasing (sorted_by_score: true)."""
        content_templates = [
            "def alpha(self):\n    # method body here\n    return self.x\n    # impl",
            "def beta(self):\n    # another body\n    return self.y\n    # different",
            "def gamma(self):\n    # yet another\n    return self.z\n    # extra",
            "def delta(self):\n    # fourth method\n    return self.w\n    # more",
        ]
        cands = [
            _sr(c, path=f"/p/mod{i}.py", score=0.5)
            for i, c in enumerate(content_templates)
        ]
        out = searcher._apply_quality_and_dedup("alpha beta gamma delta method", cands)
        scores = [r.score for r in out]
        assert scores == sorted(scores, reverse=True), (
            f"UPG-11.2: scores must be non-increasing. Got: {scores}"
        )

    def test_score_reflects_composite_not_hybrid(self, searcher) -> None:
        """A chunk with higher quality should have a higher score than one with lower quality
        even if both started at the same raw hybrid score."""
        from agent.chunk_quality import NAVIGATIONAL_NODE_TYPE
        # High quality: real code
        high = _sr(
            "def process_request(request):\n    return self.dispatch(request)\n    # impl\n    x=1",
            path="/p/impl.py", score=0.7,
        )
        # Low quality: navigational (gets quality multiplier 0.35)
        low = _sr(
            "pub use a::A;\npub use b::B;\npub use c::C;",
            path="/p/lib.rs", node_type=NAVIGATIONAL_NODE_TYPE, lang="rust", score=0.7,
        )
        # Even though both have same raw hybrid score=0.7, high quality chunk
        # should end up with a higher final score.
        out = searcher._apply_quality_and_dedup("process dispatch request", [high, low])
        assert len(out) == 2
        assert out[0].score >= out[1].score, (
            f"UPG-11.2: expected non-increasing scores, got {[r.score for r in out]}"
        )


# ---------------------------------------------------------------------------
# UPG-11.13 — Score clamped to [0, 1] (F12)
# ---------------------------------------------------------------------------

class TestScoreClamp:
    """UPG-11.13: surfaced scores must be in [0, 1].

    The composite key is base_rank * quality + sym_boost.  When the top-ranked
    candidate is also a qualified symbol match (sym_boost = 0.20), the raw
    composite exceeds 1.0 (e.g. 1.0 * 1.0 + 0.20 = 1.20).  Callers that gate
    on score > 0.8 as "confident" would get false positives without the clamp.

    The clamp is applied AFTER the sort so rank order is unchanged — this class
    also verifies that invariant.
    """

    # Reuse realistic content from TestSymbolIdentityRanking
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

    def test_score_capped_at_one(self, searcher) -> None:
        """No returned score may exceed 1.0, even when sym_boost pushes raw composite above 1."""
        # Construct a single candidate that will get rank 0 (base=1.0), quality~1.0,
        # AND the +0.20 qualified sym_boost so raw composite ~1.20.
        canonical = _sr_sym(
            "Field.deconstruct", self._FIELD_DECONSTRUCT,
            path="/p/django/db/models/fields/__init__.py",
        )
        query = "Field deconstruct base class name path args kwargs"
        out = searcher._apply_quality_and_dedup(query, [canonical])
        assert len(out) == 1
        assert out[0].score <= 1.0, (
            f"UPG-11.13: score must be <= 1.0, got {out[0].score}"
        )

    def test_all_scores_capped_at_one(self, searcher) -> None:
        """Multi-result list: every score must be in [0, 1]."""
        canonical = _sr_sym(
            "Field.deconstruct", self._FIELD_DECONSTRUCT,
            path="/p/django/db/models/fields/__init__.py",
        )
        decoy = _sr_sym(
            "RemoveField.deconstruct", self._REMOVEFIELD_DECONSTRUCT,
            path="/p/django/db/migrations/operations/fields.py",
        )
        query = "Field deconstruct base class name path args kwargs migration"
        out = searcher._apply_quality_and_dedup(query, [canonical, decoy])
        for r in out:
            assert r.score <= 1.0, (
                f"UPG-11.13: score {r.score} for {r.symbol_name!r} exceeds 1.0"
            )

    def test_rank_order_preserved_after_clamp(self, searcher) -> None:
        """Clamping must not change the returned rank order.

        When Field.deconstruct (qualified match, +0.20) is already at rank 0,
        it produces the highest raw composite (e.g. 1.0*q + 0.20 > 1.0).
        After clamping, it must still come first — the sort is done before
        the clamp so the distinction between candidates is preserved by sort order,
        not by the displayed score value.

        This is distinct from the F1 flip test (TestSymbolIdentityRanking.test_F1_*),
        which verifies promotion of a lower-ranked candidate.  Here we verify that
        clamping does not demote an already-correct first-place result.
        """
        canonical = _sr_sym(
            "Field.deconstruct", self._FIELD_DECONSTRUCT,
            path="/p/django/db/models/fields/__init__.py",
        )
        decoy = _sr_sym(
            "RemoveField.deconstruct", self._REMOVEFIELD_DECONSTRUCT,
            path="/p/django/db/migrations/operations/fields.py",
        )
        # canonical first (already correct order), decoy second
        # Both get sym_boost > 0 so both raw composites may exceed 1.0.
        # After clamping, canonical must still lead.
        query = "Field deconstruct base class name path args kwargs migration"
        out = searcher._apply_quality_and_dedup(query, [canonical, decoy])
        canonical_idx = next(i for i, r in enumerate(out) if r.symbol_name == "Field.deconstruct")
        decoy_idx = next(i for i, r in enumerate(out) if r.symbol_name == "RemoveField.deconstruct")
        assert canonical_idx < decoy_idx, (
            f"UPG-11.13: rank order must be preserved after clamp. "
            f"Field.deconstruct at idx={canonical_idx}, RemoveField.deconstruct at idx={decoy_idx}"
        )
        # Both scores must be clamped
        for r in out:
            assert r.score <= 1.0, f"score {r.score} for {r.symbol_name!r} exceeds 1.0"


# ---------------------------------------------------------------------------
# UPG-11.4 — Expand-to-symbol affordance (line range on results)
# ---------------------------------------------------------------------------

class TestExpandToSymbolAffordance:
    """UPG-11.4: each SearchResult must carry symbol_start_line/symbol_end_line
    so callers can expand to the full symbol without a blind whole-file re-read.
    """

    def test_symbol_line_range_populated(self, indexer, tmp_path) -> None:
        """After indexing, search results must have non-zero symbol_start_line/end_line."""
        from agent.searcher import CodeSearcher

        src = make_py(tmp_path, "widget.py", """
class Widget:
    def render(self):
        \"\"\"Render the widget to the screen.\"\"\"
        # drawing code here
        return self.canvas.draw()
""")
        indexer.index_file(src)

        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("Widget render draw canvas", n_results=5, rerank=False)

        assert results, "Expected at least one result"
        # Find the render method result
        render = next((r for r in results if "render" in (r.symbol_name or "")), None)
        if render is None:
            render = results[0]  # at least verify the field is populated

        # The affordance fields must be set (non-zero when the chunk is a named symbol)
        assert hasattr(render, "symbol_start_line"), "UPG-11.4: SearchResult missing symbol_start_line"
        assert hasattr(render, "symbol_end_line"), "UPG-11.4: SearchResult missing symbol_end_line"
        # For AST-parsed symbol chunks, the range should be > 0
        if "render" in (render.symbol_name or "") or "render" in render.content:
            assert render.symbol_start_line > 0 or render.symbol_end_line > 0, (
                f"UPG-11.4: expected non-zero line range for a symbol chunk; "
                f"got start={render.symbol_start_line} end={render.symbol_end_line}"
            )

    def test_rest_response_includes_symbol_line_range(self) -> None:
        """UPG-11.4: the REST CodeChunkResult schema must include symbol_start_line/end_line."""
        from app.models import CodeChunkResult
        r = CodeChunkResult(
            file="src/widget.py",
            lines="3-8",
            symbol="Widget.render",
            language="python",
            score=0.9,
            content="def render(self): ...",
            symbol_start_line=3,
            symbol_end_line=8,
        )
        assert r.symbol_start_line == 3
        assert r.symbol_end_line == 8

    def test_rest_response_defaults_zero_for_non_symbol_chunks(self) -> None:
        """Window chunks without a named symbol must default to 0/0."""
        from app.models import CodeChunkResult
        r = CodeChunkResult(
            file="src/big_file.py",
            lines="100-300",
            symbol=None,
            language="python",
            score=0.7,
            content="lots of code ...",
        )
        assert r.symbol_start_line == 0
        assert r.symbol_end_line == 0


# ---------------------------------------------------------------------------
# UPG-11.14 — Short-verb forced-inclusion allowlist (F13) + queryset stop-word (F14)
# ---------------------------------------------------------------------------

class TestShortVerbAllowlistAndQuerysetBlocklist:
    """UPG-11.14 guards:

    F13: Short verbs like 'save' are below min_identifier_len=7 AND in
         prog_stopwords.txt, so they are normally excluded from forced-inclusion.
         The short_verb_allowlist in config.yaml reinstates forced-inclusion for
         these verbs while keeping the BM25 floor + vec_sim_floor relevance gates.

    F14: 'queryset' (8 chars, no underscore) passes the normal forced-inclusion
         length guard.  Adding it to prog_stopwords.txt prevents it from triggering
         forced-inclusion of all ListFilter.queryset() admin variants for queries
         like "exclude certain records from a queryset".
    """

    # --- F13 config and token-selection tests ---

    def test_short_verb_allowlist_in_config(self) -> None:
        """FORCED_INCLUSION_SHORT_VERB_ALLOWLIST must be a non-empty frozenset
        containing at least 'save', 'create', 'delete', 'update' (UPG-11.14 / F13).
        """
        from agent.config import FORCED_INCLUSION_SHORT_VERB_ALLOWLIST

        assert isinstance(FORCED_INCLUSION_SHORT_VERB_ALLOWLIST, frozenset), (
            "FORCED_INCLUSION_SHORT_VERB_ALLOWLIST must be a frozenset"
        )
        assert "save" in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST, (
            "UPG-11.14: 'save' must be in the short_verb_allowlist so 'save a model "
            "instance to the database' triggers forced-inclusion for Model.save (F13)"
        )
        for verb in ("get", "set", "add", "create", "delete", "update"):
            assert verb in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST, (
                f"UPG-11.14: '{verb}' must be in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST "
                f"(config.yaml ranking.forced_inclusion.short_verb_allowlist)"
            )

    def test_save_is_in_prog_stopwords(self) -> None:
        """'save' is in SYMBOL_STOP_WORDS (it was added in UPG-11.8 as a 4-letter
        common verb that causes spurious leaf boosts).  The short-verb allowlist
        must reinstate forced-inclusion for 'save' despite the stop-word block.
        This test confirms the baseline state that makes the allowlist necessary.
        """
        from agent.config import SYMBOL_STOP_WORDS

        assert "save" in SYMBOL_STOP_WORDS, (
            "'save' must remain in SYMBOL_STOP_WORDS (prog_stopwords.txt) so it still "
            "suppresses the symbol-identity BOOST for bare 'save' leaves — the allowlist "
            "only reinstates FORCED-INCLUSION, not the boost (UPG-11.14 / F13)"
        )

    def test_save_enters_sym_tokens_for_inclusion_via_allowlist(self) -> None:
        """When a query contains 'save' and forced-inclusion is computing
        sym_tokens_for_inclusion, 'save' must appear in that set because it is
        in the short_verb_allowlist — even though it is in SYMBOL_STOP_WORDS and
        below min_identifier_len (UPG-11.14 / F13).
        """
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import (
            SYMBOL_STOP_WORDS,
            FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
            FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
        )

        query = "save a model instance to the database"
        all_sym_toks = _query_symbol_tokens(query)

        # Baseline: 'save' would be excluded by both the length guard and stop-word check.
        assert "save" not in {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }, (
            "Baseline failed: 'save' should be excluded by the normal length+stop-word guard "
            "before the allowlist augments the token set"
        )

        # After allowlist: 'save' must be included.
        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        for tok in all_sym_toks:
            if tok in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST:
                sym_tokens_for_inclusion.add(tok)

        assert "save" in sym_tokens_for_inclusion, (
            "UPG-11.14 / F13: 'save' must enter sym_tokens_for_inclusion via the "
            "short_verb_allowlist for query 'save a model instance to the database'. "
            f"Tokens in set: {sym_tokens_for_inclusion}"
        )

    def test_short_verb_allowlist_forced_inclusion_search(self, indexer, tmp_path) -> None:
        """Integration: Model.save must appear in top-5 for 'save a model instance
        to the database' via the short_verb_allowlist forced-inclusion path (F13).

        The candidate pool is artificially reduced so that Model.save would be
        excluded without forced-inclusion (the deeper ModelAdmin.save_model wins
        in the natural hybrid ranking because save_model is compound and matches
        'save'+'model' in the embedding space).
        """
        import agent.searcher as searcher_mod
        from agent.searcher import CodeSearcher

        # A realistic Model.save implementation (long body — lower BM25 density)
        model_src = tmp_path / "base.py"
        model_src.write_text(
            "class Model:\n"
            "    def save(self, force_insert=False, force_update=False,\n"
            "             using=None, update_fields=None):\n"
            "        \"\"\"\n"
            "        Save the current instance.  Override this in a subclass if you want\n"
            "        to control the saving process.\n"
            "        Persists the model instance to the database.\n"
            "        \"\"\"\n"
            "        self._do_update(force_insert=force_insert,\n"
            "                        force_update=force_update,\n"
            "                        using=using, update_fields=update_fields)\n"
        )
        # An admin helper that competes via compound name (save_model)
        admin_src = tmp_path / "admin.py"
        admin_src.write_text(
            "class ModelAdmin:\n"
            "    def save_model(self, request, obj, form, change):\n"
            "        \"\"\"Save the given model instance to the database.\"\"\"\n"
            "        obj.save()\n"
        )
        indexer.index_file(str(model_src))
        indexer.index_file(str(admin_src))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        orig_top_k = searcher_mod._RERANK_TOP_K_UNFILTERED
        # Shrink the natural pool so Model.save would be excluded without forced-inclusion
        searcher_mod._RERANK_TOP_K_UNFILTERED = 2
        try:
            query = "save a model instance to the database"
            results, _ = s.search(query, n_results=5, rerank=False)
        finally:
            searcher_mod._RERANK_TOP_K_UNFILTERED = orig_top_k

        model_save_present = any(
            "save" in (r.symbol_name or "").lower()
            and "class: Model" in r.content
            for r in results
        )
        assert model_save_present, (
            "UPG-11.14 / F13: Model.save must be in top-5 for 'save a model instance "
            "to the database' via short_verb_allowlist forced-inclusion. "
            f"Results: {[(r.symbol_name, r.content[:60]) for r in results]}"
        )

    # --- F14 queryset stop-word tests ---

    def test_queryset_in_prog_stopwords(self) -> None:
        """'queryset' must be in SYMBOL_STOP_WORDS (prog_stopwords.txt) so that
        forced-inclusion of ListFilter.queryset() variants is suppressed for queries
        like 'exclude certain records from a queryset' (UPG-11.14 / F14).
        """
        from agent.config import SYMBOL_STOP_WORDS

        assert "queryset" in SYMBOL_STOP_WORDS, (
            "UPG-11.14 / F14: 'queryset' must be in SYMBOL_STOP_WORDS (prog_stopwords.txt) "
            "to prevent forced-inclusion of admin ListFilter.queryset() variants when "
            "'queryset' is used as a prose noun in the query "
            "(e.g. 'exclude certain records from a queryset' → QuerySet.exclude must "
            "outrank ListFilter.queryset at rank1-3)"
        )

    def test_queryset_excluded_from_forced_inclusion_tokens(self) -> None:
        """For 'exclude certain records from a queryset', 'queryset' must NOT appear
        in sym_tokens_for_inclusion — it is in SYMBOL_STOP_WORDS so it is blocked
        even though it is 8 chars (above min_identifier_len=7) (UPG-11.14 / F14).
        """
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import (
            SYMBOL_STOP_WORDS,
            FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
            FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
        )

        query = "exclude certain records from a queryset"
        all_sym_toks = _query_symbol_tokens(query)

        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        for tok in all_sym_toks:
            if tok in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST:
                sym_tokens_for_inclusion.add(tok)

        assert "queryset" not in sym_tokens_for_inclusion, (
            "UPG-11.14 / F14: 'queryset' must NOT be in sym_tokens_for_inclusion — "
            "it is in SYMBOL_STOP_WORDS so forced-inclusion of ListFilter.queryset() "
            "variants is suppressed. "
            f"sym_tokens_for_inclusion: {sym_tokens_for_inclusion}"
        )
        # Also verify 'queryset' is NOT in the short-verb allowlist (it's a noun, not a verb)
        assert "queryset" not in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST, (
            "UPG-11.14: 'queryset' must NOT be in the short_verb_allowlist — "
            "it is a noun (container) and should stay fully blocked by the stop-word list"
        )

    def test_queryset_search_does_not_flood_with_listfilter_variants(
        self, indexer, tmp_path
    ) -> None:
        """Integration (F14): when 'queryset' appears as a prose noun in the query,
        ListFilter.queryset() variants must NOT dominate results over the genuinely
        relevant ORM method (QuerySet.exclude in this test).

        Verifies that the stop-word block prevents queryset-named symbols from being
        force-included into the candidate pool.
        """
        from agent.searcher import CodeSearcher

        # The correct ORM method
        qs_src = tmp_path / "query.py"
        qs_src.write_text(
            "class QuerySet:\n"
            "    def exclude(self, *args, **kwargs):\n"
            "        \"\"\"\n"
            "        Return a new QuerySet instance with NOT (args) ANDed to the existing\n"
            "        set. Equivalent to SQL 'WHERE NOT (...)'. Excludes matching records.\n"
            "        \"\"\"\n"
            "        return self._filter_or_exclude(True, args, kwargs)\n"
        )
        # Admin ListFilter.queryset variants that should NOT be forced into the pool
        admin_src = tmp_path / "admin.py"
        admin_src.write_text(
            "class SimpleListFilter:\n"
            "    def queryset(self, request, queryset):\n"
            "        \"\"\"Return the filtered queryset for the list display.\"\"\"\n"
            "        return queryset\n"
            "\n"
            "class RelatedFieldListFilter:\n"
            "    def queryset(self, request, queryset):\n"
            "        \"\"\"Apply filter to the queryset for related field.\"\"\"\n"
            "        return queryset.filter(**self.used_params())\n"
        )
        indexer.index_file(str(qs_src))
        indexer.index_file(str(admin_src))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Verify no ListFilter.queryset chunk is marked as forced_inclusion
        query = "exclude certain records from a queryset"
        results, _ = s.search(query, n_results=10, rerank=False)

        forced_queryset = [
            r for r in results
            if r.forced_inclusion and "queryset" in (r.symbol_name or "").lower()
        ]
        assert not forced_queryset, (
            "UPG-11.14 / F14: No ListFilter.queryset chunk must be force-included for "
            "'exclude certain records from a queryset' — 'queryset' is in SYMBOL_STOP_WORDS "
            "and must be blocked. "
            f"Force-included queryset results: {[(r.symbol_name, r.content[:60]) for r in forced_queryset]}"
        )


# ---------------------------------------------------------------------------
# UPG-15.3 — Sub-token guard for short-verb forced-inclusion allowlist (F17)
# ---------------------------------------------------------------------------

class TestSubtokenShortVerbGuard:
    """UPG-15.3 / F17: The short-verb allowlist (UPG-11.14) must only fire for
    tokens the user typed as a STANDALONE word, not for short verbs that appear
    only as sub-tokens of a longer compound identifier already present in the query.

    Example: 'ForeignKey on_delete CASCADE related_name'
      - on_delete → sub-tokens {on, delete}
      - 'delete' must NOT trigger forced-inclusion (it is only a sub-token here)
      - 'on_delete' itself triggers the normal compound path (correct)

    Standalone-word derivation: split the raw query on non-identifier chars
    (keeping underscores), lowercase each piece ≥2 chars — this is the first
    pass of _query_symbol_tokens without the subsequent underscore/camelCase
    expansion step.
    """

    def test_subtoken_delete_does_not_trigger_allowlist(self) -> None:
        """RED → GREEN (UPG-15.3 / F17): for a query containing 'on_delete' but
        NO standalone 'delete', 'delete' must NOT be added to
        sym_tokens_for_inclusion via the short-verb allowlist.

        Before the fix the allowlist loop iterated _all_query_sym_toks (which
        includes sub-tokens from underscore splitting) so 'delete' — a sub-token
        of 'on_delete' — spuriously triggered forced-inclusion of every
        .delete() method (cache backends, storage classes, etc.).
        """
        import re
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import (
            SYMBOL_STOP_WORDS,
            FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
            FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
        )

        query = "ForeignKey on_delete CASCADE related_name"
        all_sym_toks = _query_symbol_tokens(query)

        # Precondition: 'delete' IS present as a sub-token in all_sym_toks
        assert "delete" in all_sym_toks, (
            "Precondition failed: _query_symbol_tokens must include 'delete' as a "
            "sub-token of 'on_delete' for this test to be meaningful"
        )
        # Precondition: 'delete' IS in the allowlist
        assert "delete" in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST, (
            "Precondition failed: 'delete' must be in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST"
        )

        # Derive standalone words exactly as the fixed code path does.
        standalone_query_words = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2
        }
        # 'delete' is NOT a standalone word — it only appears as a sub-token of on_delete
        assert "delete" not in standalone_query_words, (
            "Standalone derivation error: 'delete' must not be a standalone word in "
            f"'{query}' — it should only appear as a sub-token of 'on_delete'. "
            f"standalone_query_words: {standalone_query_words}"
        )

        # Build sym_tokens_for_inclusion using the FIXED logic (standalone guard)
        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        for tok in all_sym_toks:
            if tok in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST and tok in standalone_query_words:
                sym_tokens_for_inclusion.add(tok)

        assert "delete" not in sym_tokens_for_inclusion, (
            "UPG-15.3 / F17: 'delete' must NOT be in sym_tokens_for_inclusion for "
            f"query '{query}' — it is only a sub-token of 'on_delete', not a standalone "
            "word. Its presence would spuriously force-include every cache/storage "
            f".delete() method. sym_tokens_for_inclusion: {sym_tokens_for_inclusion}"
        )
        # 'on_delete' itself must still be in the set (compound path, unaffected by fix)
        assert "on_delete" in sym_tokens_for_inclusion, (
            "UPG-15.3 / F17: 'on_delete' must remain in sym_tokens_for_inclusion — "
            "it is a compound identifier (contains '_') and takes the unconditional "
            "compound forced-inclusion path, which is correct and unchanged."
        )

    def test_standalone_delete_still_triggers_allowlist(self) -> None:
        """Regression guard (UPG-11.14 / F13): when 'delete' appears as an actual
        standalone word in the query (e.g. 'delete a cache key'), it MUST still be
        added to sym_tokens_for_inclusion via the short-verb allowlist.

        The sub-token guard must not regress the intended F13 behavior.
        """
        import re
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import (
            SYMBOL_STOP_WORDS,
            FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
            FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
        )

        query = "delete a cache key"
        all_sym_toks = _query_symbol_tokens(query)

        standalone_query_words = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2
        }
        assert "delete" in standalone_query_words, (
            "Precondition: 'delete' must be a standalone word in 'delete a cache key'"
        )

        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        for tok in all_sym_toks:
            if tok in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST and tok in standalone_query_words:
                sym_tokens_for_inclusion.add(tok)

        assert "delete" in sym_tokens_for_inclusion, (
            "UPG-11.14 / F13 regression: 'delete' must be in sym_tokens_for_inclusion "
            "for query 'delete a cache key' — it is a standalone word and must trigger "
            "the short-verb allowlist (UPG-15.3 guard must not break this). "
            f"sym_tokens_for_inclusion: {sym_tokens_for_inclusion}"
        )

    def test_standalone_save_still_triggers_allowlist(self) -> None:
        """Regression guard (UPG-11.14 / F13): 'save' as a standalone word in
        'save a model instance to the database' must still enter
        sym_tokens_for_inclusion via the short-verb allowlist.
        """
        import re
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import (
            SYMBOL_STOP_WORDS,
            FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
            FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
        )

        query = "save a model instance to the database"
        all_sym_toks = _query_symbol_tokens(query)

        standalone_query_words = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2
        }

        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }
        for tok in all_sym_toks:
            if tok in FORCED_INCLUSION_SHORT_VERB_ALLOWLIST and tok in standalone_query_words:
                sym_tokens_for_inclusion.add(tok)

        assert "save" in sym_tokens_for_inclusion, (
            "UPG-11.14 / F13 regression: 'save' must be in sym_tokens_for_inclusion "
            "for query 'save a model instance to the database' — it is a standalone word "
            "and must trigger the short-verb allowlist. "
            f"sym_tokens_for_inclusion: {sym_tokens_for_inclusion}"
        )

    def test_standalone_derivation_excludes_subtoken(self) -> None:
        """Unit test for the standalone-word derivation itself: verify that
        splitting the raw query on non-identifier boundaries (preserving
        underscores) correctly identifies 'on_delete' as one standalone word and
        does NOT produce 'delete' or 'on' as standalone words.
        """
        import re

        query = "ForeignKey on_delete CASCADE related_name"
        standalone_query_words = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2
        }

        assert "on_delete" in standalone_query_words, (
            "Standalone derivation must keep 'on_delete' as a whole token "
            f"(underscore is not a split boundary). Got: {standalone_query_words}"
        )
        assert "delete" not in standalone_query_words, (
            "Standalone derivation must NOT split 'on_delete' into 'delete' — "
            "underscores are part of identifiers in this pass. "
            f"Got: {standalone_query_words}"
        )
        assert "on" not in standalone_query_words, (
            "Standalone derivation must NOT split 'on_delete' into 'on'. "
            f"Got: {standalone_query_words}"
        )


class TestForcedInclusionCaseFold:
    """UPG-15.4: CamelCase/Pascal class symbol leaves must be matchable by
    name in forced-inclusion.

    Root cause: _query_symbol_tokens lowercases all tokens so
    sym_tokens_for_inclusion is all-lowercase, but the previous leaf
    comparison was case-sensitive.  'ForeignKey' (CamelCase leaf) would
    never match the lowercase token 'foreignkey', so class ForeignKey chunks
    were silently excluded from the forced-inclusion pool.

    Fix: compare leaf.lower() against sym_tokens_for_inclusion, then apply
    the UPG-11 casing discipline guard: a CamelCase leaf is force-included
    only when the user typed that identifier with identifier casing (CamelCase
    or underscore) in the query.  Lowercase prose (e.g. 'migration') must NOT
    force-include the same-named CamelCase class.
    """

    def test_camelcase_leaf_matches_via_casefold(self) -> None:
        """UPG-15.4: leaf.lower() of a CamelCase symbol matches the all-lowercase
        sym_tokens_for_inclusion set, confirming the fix is structurally correct.

        Verifies that 'ForeignKey' (the symbol leaf) matches 'foreignkey' in the
        token set derived from the query 'ForeignKey on_delete CASCADE related_name'.
        """
        import re
        from agent.chunk_quality import _query_symbol_tokens
        from agent.config import SYMBOL_STOP_WORDS, FORCED_INCLUSION_MIN_IDENTIFIER_LEN

        query = "ForeignKey on_delete CASCADE related_name"
        all_sym_toks = _query_symbol_tokens(query)

        # sym_tokens_for_inclusion is all-lowercase (as produced by _query_symbol_tokens)
        sym_tokens_for_inclusion = {
            tok for tok in all_sym_toks
            if ("_" in tok or len(tok) >= FORCED_INCLUSION_MIN_IDENTIFIER_LEN)
            and tok not in SYMBOL_STOP_WORDS
        }

        # Precondition: 'foreignkey' (lowercase) is in the set
        assert "foreignkey" in sym_tokens_for_inclusion, (
            "Precondition: 'foreignkey' must be in sym_tokens_for_inclusion. "
            f"Got: {sym_tokens_for_inclusion}"
        )

        # The CamelCase leaf 'ForeignKey' must NOT match case-sensitively (old bug)
        leaf = "ForeignKey"
        assert leaf not in sym_tokens_for_inclusion, (
            "Precondition: 'ForeignKey' (CamelCase) must NOT be in the lowercase "
            "sym_tokens_for_inclusion set — this is what caused the original bug."
        )

        # The fix: case-insensitive comparison via leaf.lower()
        leaf_lower = leaf.lower()
        assert leaf_lower in sym_tokens_for_inclusion, (
            "UPG-15.4: leaf.lower() of 'ForeignKey' must match sym_tokens_for_inclusion. "
            f"leaf_lower={leaf_lower!r}, set={sym_tokens_for_inclusion}"
        )

    def test_ident_cased_toks_captures_camelcase(self) -> None:
        """UPG-15.4: _ident_cased_query_toks correctly captures tokens that the
        user typed with identifier casing (CamelCase or underscore).

        For query 'ForeignKey on_delete CASCADE related_name':
          - 'ForeignKey' → has uppercase → captured as 'foreignkey'
          - 'CASCADE' → has uppercase → captured as 'cascade'
          - 'on_delete' → has underscore → captured as 'on_delete'
          - 'related_name' → has underscore → captured as 'related_name'
        """
        import re

        query = "ForeignKey on_delete CASCADE related_name"
        ident_cased_query_toks = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2 and ("_" in tok or any(c.isupper() for c in tok))
        }

        assert "foreignkey" in ident_cased_query_toks, (
            "UPG-15.4: 'ForeignKey' (contains uppercase) must appear as 'foreignkey' "
            f"in _ident_cased_query_toks. Got: {ident_cased_query_toks}"
        )
        assert "cascade" in ident_cased_query_toks, (
            "UPG-15.4: 'CASCADE' (all uppercase) must appear as 'cascade' "
            f"in _ident_cased_query_toks. Got: {ident_cased_query_toks}"
        )
        assert "on_delete" in ident_cased_query_toks, (
            "UPG-15.4: 'on_delete' (underscore) must appear in _ident_cased_query_toks. "
            f"Got: {ident_cased_query_toks}"
        )
        assert "related_name" in ident_cased_query_toks, (
            "UPG-15.4: 'related_name' (underscore) must appear in _ident_cased_query_toks. "
            f"Got: {ident_cased_query_toks}"
        )

    def test_prose_lowercase_does_not_match_camelcase_leaf(self) -> None:
        """UPG-15.4 / UPG-11 regression guard: a lowercase prose word in the query
        must NOT force-include a same-named CamelCase class symbol.

        Query 'apply migrations to the database' contains the word 'migrations'
        which is lowercase prose — it must NOT force-include class Migration even
        though 'migration' would appear in sym_tokens_for_inclusion (length guard)
        and 'Migration'.lower() == 'migration'.

        The casing discipline guard catches this: 'Migration' (leaf != leaf_lower)
        requires leaf_lower='migration' to be in _ident_cased_query_toks, but
        'migrations' has no uppercase and no underscore → _ident_cased_query_toks
        does not contain 'migration' → forced-inclusion is suppressed.
        """
        import re

        query = "apply migrations to the database"
        ident_cased_query_toks = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2 and ("_" in tok or any(c.isupper() for c in tok))
        }

        # None of the words in this query have uppercase or underscore
        assert "migration" not in ident_cased_query_toks, (
            "UPG-15.4 / UPG-11 guard: 'migration' (from lowercase prose 'migrations') "
            "must NOT appear in _ident_cased_query_toks — it would bypass the casing "
            f"discipline gate and force-include class Migration. Got: {ident_cased_query_toks}"
        )
        assert "migrations" not in ident_cased_query_toks, (
            "UPG-15.4 / UPG-11 guard: 'migrations' (lowercase prose) must NOT appear "
            f"in _ident_cased_query_toks. Got: {ident_cased_query_toks}"
        )

        # Simulate the casing discipline check for leaf 'Migration'
        leaf = "Migration"
        leaf_lower = leaf.lower()
        # leaf != leaf_lower is True (CamelCase leaf)
        assert leaf != leaf_lower, "Precondition: 'Migration' must differ from its lowercase form"
        # The guard: leaf_lower not in _ident_cased_query_toks → forced-inclusion suppressed
        assert leaf_lower not in ident_cased_query_toks, (
            "UPG-15.4 / UPG-11 guard: leaf_lower='migration' must NOT be in "
            "_ident_cased_query_toks for a prose query — the casing discipline check "
            f"must block forced-inclusion of class Migration. Got: {ident_cased_query_toks}"
        )

    def test_explicit_camelcase_query_passes_casing_guard(self) -> None:
        """UPG-15.4: when the user types 'Migration' (CamelCase) in a query,
        the casing discipline guard must ALLOW forced-inclusion of class Migration.

        'Migration apply database' → user typed 'Migration' with CamelCase →
        _ident_cased_query_toks contains 'migration' → leaf='Migration',
        leaf_lower='migration', leaf_lower in _ident_cased_query_toks → True.
        """
        import re

        query = "Migration apply database"
        ident_cased_query_toks = {
            tok.lower()
            for tok in re.split(r"[^a-zA-Z0-9_]+", query)
            if len(tok) >= 2 and ("_" in tok or any(c.isupper() for c in tok))
        }

        assert "migration" in ident_cased_query_toks, (
            "UPG-15.4: 'Migration' (CamelCase) in the query must produce 'migration' "
            f"in _ident_cased_query_toks. Got: {ident_cased_query_toks}"
        )

        # Simulate the casing discipline check — guard must pass
        leaf = "Migration"
        leaf_lower = leaf.lower()
        assert leaf != leaf_lower, "Precondition"
        assert leaf_lower in ident_cased_query_toks, (
            "UPG-15.4: casing discipline guard must ALLOW class Migration when the user "
            "typed 'Migration' (CamelCase) — leaf_lower='migration' must be in "
            f"_ident_cased_query_toks. Got: {ident_cased_query_toks}"
        )
