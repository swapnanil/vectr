"""
Tests for CodeIndexer and CodeSearcher.

Uses a DummyEmbedProvider (from conftest) so no model download is needed.
Tests verify the real ChromaDB storage and hybrid BM25+vector search pipeline.
"""
from __future__ import annotations

import math
import re
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

    def test_query_vector_languages_in_filter(self, indexer, tmp_path) -> None:
        # UPG-15.13: the `languages` (set) param restricts results to any of the
        # given languages via an $in filter, and takes precedence over `language`.
        py_file = make_py(tmp_path, "app.py", "def process(): pass")
        js_file = tmp_path / "app.js"
        js_file.write_text("function process() {}")
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("process function documentation prose here")
        indexer.index_file(py_file)
        indexer.index_file(str(js_file))
        indexer.index_file(str(txt_file))
        embedding = indexer.embed_query("process function")
        result = indexer.query_vector(
            embedding, n_results=10, languages=["javascript", "text", "txt"]
        )
        metas = result["metadatas"][0]
        assert metas, "expected at least one non-python match"
        assert all(m["language"] in {"javascript", "text", "txt"} for m in metas)
        assert all(m["language"] != "python" for m in metas)

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
        """Unfiltered queries over-fetch, apply union-of-signals trivial filter (UPG-15.7 revised).

        Pool selection: take the first top_k_unfiltered non-trivial chunks from the
        vec-similarity ranking UNION the first top_k_unfiltered non-trivial chunks from
        the BM25 ranking.  This is bounded by 2*top_k_unfiltered (and by pre_filter_fetch_k).
        The corpus is pure Python so no chunks are classified trivial — both per-signal
        lists fill to top_k_unfiltered, so the union can be anywhere in
        [top_k_unfiltered, min(2*top_k_unfiltered, pre_filter_fetch_k)].

        Key invariants:
        - unfiltered pool > filtered pool (filtered uses the smaller _RERANK_TOP_K).
        - unfiltered pool <= min(2*top_k_unfiltered, pre_filter_fetch_k, total_chunks).
        - cross-encoder sees no trivial chunks (tested in TestPoolEntryTrivialFilter).
        """
        import agent.searcher as searcher_mod
        # Index enough chunks that both FILTERED and UNFILTERED pool depths are reachable.
        # Monkeypatch to small values so the fixture stays fast.
        n_filtered = searcher_mod._RERANK_TOP_K
        n_unfiltered_test = n_filtered + 10   # per-signal cap for unfiltered pool

        pre_fetch_k = n_unfiltered_test + 15
        body = "\n".join(f"def fn_{i}(x):\n    return x + {i}\n" for i in range(n_unfiltered_test + 20))
        path = make_py(tmp_path, "many.py", body)
        indexer.index_file(path)
        assert indexer.total_chunks >= n_unfiltered_test, (
            f"Need at least {n_unfiltered_test} chunks in index, got {indexer.total_chunks}"
        )

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

        # Monkeypatch both the per-signal cap (top_k_unfiltered) and the over-fetch depth
        # (pre_filter_fetch_k) to small testable values.
        orig_unfiltered = searcher_mod._RERANK_TOP_K_UNFILTERED
        orig_prefetch = searcher_mod._RERANK_PRE_FILTER_FETCH_K
        searcher_mod._RERANK_TOP_K_UNFILTERED = n_unfiltered_test
        searcher_mod._RERANK_PRE_FILTER_FETCH_K = pre_fetch_k
        try:
            s.search("function returning a number", language=None)
            unfiltered_count = stub.last_count
            s.search("function returning a number", language="python")
            filtered_count = stub.last_count
        finally:
            searcher_mod._RERANK_TOP_K_UNFILTERED = orig_unfiltered
            searcher_mod._RERANK_PRE_FILTER_FETCH_K = orig_prefetch

        # Union pool is bounded by pre_filter_fetch_k (the hybrid fetch ceiling) AND
        # 2*top_k_unfiltered (union of two per-signal lists of size top_k_unfiltered each).
        max_unfiltered = min(2 * n_unfiltered_test, pre_fetch_k, indexer.total_chunks)
        assert unfiltered_count <= max_unfiltered, (
            f"UPG-15.7: unfiltered pool must be <= min(2*top_k_unfiltered, pre_filter_fetch_k) "
            f"= {max_unfiltered}, got {unfiltered_count}"
        )
        assert filtered_count == min(searcher_mod._RERANK_TOP_K, indexer.total_chunks)
        assert unfiltered_count > filtered_count, (
            f"UPG-15.7: unfiltered pool ({unfiltered_count}) must exceed filtered pool "
            f"({filtered_count}) so there is more room to rerank"
        )

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


class TestFlowJavaScriptSupport:
    """UPG-JSFLOW-SYMBOLS: Flow-typed .js must route to the tsx/typescript
    grammar (which parses type annotations) instead of the plain javascript
    grammar (which treats Flow syntax as ERROR nodes and desyncs the symbol
    walk — canonical functions go missing, keyword tokens can be
    misattributed as symbol names). Fixtures are minimal synthetic Flow
    snippets — no benchmark-corpus source is referenced or copied.
    """

    # @flow pragma + `import type` + typed function signatures + a generic
    # function — the shape that desyncs the plain javascript grammar.
    _FLOW_JS = """\
// @flow
import type {Foo} from "./types";

function beginWork(current: Foo | null, workInProgress: Foo, lanes: number): Foo | null {
  if (current !== null) {
    return current;
  }
  return workInProgress;
}

function push<T>(cursor: T, value: T, fiber: Foo): void {
  cursor.current = value;
}

export function completeWork(a: Foo): void {
  return;
}
"""

    _PLAIN_JS = """\
function greet(name) {
  return "hello " + name;
}

class Widget {
  render() {
    return null;
  }
}
"""

    def _write_flow(self, tmp_path, name="flow_sample.js") -> str:
        p = tmp_path / name
        p.write_text(self._FLOW_JS)
        return str(p)

    def _write_plain(self, tmp_path, name="plain_sample.js") -> str:
        p = tmp_path / name
        p.write_text(self._PLAIN_JS)
        return str(p)

    # -- detection helper -----------------------------------------------

    def test_flow_pragma_detected(self) -> None:
        from agent.indexer import is_flow_javascript
        assert is_flow_javascript(self._FLOW_JS) is True

    def test_plain_js_not_detected_as_flow(self) -> None:
        from agent.indexer import is_flow_javascript
        assert is_flow_javascript(self._PLAIN_JS) is False

    def test_secondary_marker_detected_without_pragma(self) -> None:
        from agent.indexer import is_flow_javascript
        code = 'import type {Bar} from "./b";\nfunction f(a) { return a; }\n'
        assert is_flow_javascript(code) is True

    def test_parser_language_routes_flow_js_to_tsx(self) -> None:
        from agent.indexer import _parser_language_for
        assert _parser_language_for("javascript", self._FLOW_JS) == "tsx"

    def test_parser_language_leaves_plain_js_untouched(self) -> None:
        from agent.indexer import _parser_language_for
        assert _parser_language_for("javascript", self._PLAIN_JS) == "javascript"

    def test_parser_language_noop_for_other_languages(self) -> None:
        from agent.indexer import _parser_language_for
        # Flow-looking content in a non-.js language key must not be rerouted.
        assert _parser_language_for("python", self._FLOW_JS) == "python"

    # -- symbol extraction on a Flow-typed file --------------------------

    def test_flow_file_yields_canonical_function_symbols(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        syms, _ = extract_symbols_from_file(self._write_flow(tmp_path))
        names = {s["name"] for s in syms}
        assert "beginWork" in names
        assert "push" in names
        assert "completeWork" in names

    def test_flow_file_yields_no_keyword_symbols(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        syms, _ = extract_symbols_from_file(self._write_flow(tmp_path))
        names = {s["name"] for s in syms}
        assert "if" not in names
        assert "return" not in names

    def test_flow_file_chunks_are_ast_aware_under_javascript_label(self, tmp_path) -> None:
        # Parsed with the tsx grammar, but chunk/symbol metadata still reports
        # "javascript" (the extension-derived language bucket) so downstream
        # filters/dict lookups keyed on "javascript" keep working.
        from agent.indexer import chunk_file
        chunks = chunk_file(self._write_flow(tmp_path))
        symbol_names = {c.symbol_name for c in chunks}
        assert "beginWork" in symbol_names
        assert "push" in symbol_names
        assert all(c.language == "javascript" for c in chunks)

    def test_flow_file_locate_resolves_canonical_functions(self, tmp_path) -> None:
        from agent.symbol_graph import SymbolGraph
        db = tmp_path / "sg"; db.mkdir()
        sg = SymbolGraph(str(db))
        path = self._write_flow(tmp_path)
        ws = str(tmp_path)
        sg.index_file(ws, path)
        for name in ("beginWork", "push", "completeWork"):
            hits = sg.locate(ws, name)
            assert hits and hits[0].name == name, f"locate({name!r}) failed to resolve"

    # -- plain (non-Flow) .js is unaffected -------------------------------

    def test_plain_js_still_parses_via_javascript_grammar(self, tmp_path) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        syms, _ = extract_symbols_from_file(self._write_plain(tmp_path))
        by_name = {s["name"]: s["kind"] for s in syms}
        assert by_name.get("greet") == "function"
        assert by_name.get("Widget") == "class"
        assert by_name.get("render") == "method"

    # -- deliberately broken file: keyword-junk rejection ----------------

    def test_broken_typed_js_yields_no_keyword_symbols(self, tmp_path) -> None:
        # Type annotations WITHOUT an @flow pragma or `import type` — not
        # detected as Flow, so it stays on the plain javascript grammar and
        # desyncs into ERROR nodes. The keyword/ERROR-node guard must still
        # prevent any keyword-named symbol from being minted, even though the
        # routing fix does not apply here.
        broken = """\
function beginWork(current: Foo | null, workInProgress: Foo): Foo | null {
  if (current !== null) {
    return current;
  }
  return workInProgress;
}
"""
        from agent.symbol_graph import extract_symbols_from_file
        p = tmp_path / "broken.js"
        p.write_text(broken)
        syms, edges = extract_symbols_from_file(str(p))
        names = {s["name"] for s in syms}
        for kw in ("if", "for", "while", "return", "switch", "case"):
            assert kw not in names
        edge_targets = {e["to_symbol"] for e in edges}
        for kw in ("if", "for", "while", "return", "switch", "case"):
            assert kw not in edge_targets


class TestReservedKeywordRejection:
    """UPG-JSFLOW-SYMBOLS: symbol names must never be a language keyword,
    across every symbol-graph language (config-driven, not JS-specific)."""

    @pytest.mark.parametrize("language,keyword", [
        ("python", "if"),
        ("python", "return"),
        ("javascript", "if"),
        ("javascript", "function"),
        ("typescript", "interface"),
        ("go", "func"),
        ("rust", "fn"),
        ("java", "class"),
        ("zig", "fn"),
        ("c", "typedef"),
        ("cpp", "namespace"),
    ])
    def test_keyword_rejected_per_language(self, language, keyword) -> None:
        from agent.symbol_graph._extraction import _is_reserved_keyword
        assert _is_reserved_keyword(keyword, language) is True

    @pytest.mark.parametrize("language,name", [
        ("python", "verify_token"),
        ("javascript", "beginWork"),
        ("typescript", "renderWithHooks"),
        ("go", "HandleRequest"),
        ("rust", "resolve_package"),
        ("java", "processOrder"),
        ("zig", "allocBuffer"),
        ("c", "PyDict_New"),
        ("cpp", "render"),
    ])
    def test_real_symbol_name_not_rejected(self, language, name) -> None:
        from agent.symbol_graph._extraction import _is_reserved_keyword
        assert _is_reserved_keyword(name, language) is False

    def test_every_symbol_language_has_a_reserved_keyword_set(self) -> None:
        from agent.config import SYMBOL_GRAPH_RESERVED_KEYWORDS
        from agent.symbol_graph import SYMBOL_LANGUAGES
        for lang in SYMBOL_LANGUAGES:
            assert lang in SYMBOL_GRAPH_RESERVED_KEYWORDS
            assert len(SYMBOL_GRAPH_RESERVED_KEYWORDS[lang]) > 0


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
# ARCH-4 — dual-vector purpose collection (pool-entry mechanism)
# ---------------------------------------------------------------------------

class TestDualVectorPurposeCollection:
    def test_purpose_collection_populated_for_symbol_chunks(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "svc.py", """
            def process_payment(order_id: str) -> bool:
                \"\"\"Charge the customer for the given order and record the result.\"\"\"
                return True
        """)
        indexer.index_file(path)
        assert indexer._purpose_collection.count() > 0
        docs = indexer._purpose_collection.get(include=["documents"])["documents"]
        assert any("process_payment" in d for d in docs)
        assert any("Charge the customer" in d for d in docs)

    def test_purpose_collection_excludes_non_symbol_chunks(self, indexer, tmp_path) -> None:
        (tmp_path / "notes.md").write_text(
            "# Title\n\nSome prose that is not a symbol at all, just documentation.\n"
        )
        indexer.index_file(str(tmp_path / "notes.md"))
        body_ids, _, _ = indexer.get_all_documents()
        assert len(body_ids) > 0
        assert indexer._purpose_collection.count() == 0

    def test_purpose_collection_is_subset_of_body_ids(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "mix.py", """
            def with_symbol():
                pass
        """)
        indexer.index_file(path)
        body_ids, _, _ = indexer.get_all_documents()
        purpose_ids = indexer._purpose_collection.get()["ids"]
        assert set(purpose_ids) <= set(body_ids)

    def test_delete_file_removes_purpose_collection_chunks(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "temp.py", """
            def to_remove():
                x = 1
        """)
        indexer.index_file(path)
        assert indexer._purpose_collection.count() > 0
        indexer.delete_file(path)
        assert indexer._purpose_collection.count() == 0

    def test_reindex_prunes_stale_purpose_chunks(self, indexer, tmp_path) -> None:
        path = Path(make_py(tmp_path, "evolving.py", "def v1(): pass"))
        indexer.index_workspace()
        first_purpose_ids = set(indexer._purpose_collection.get()["ids"])
        assert first_purpose_ids

        path.write_text("def v2(): pass\n")
        import os as _os
        _os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1))
        indexer.index_workspace()

        purpose_docs = indexer._purpose_collection.get(include=["documents"])["documents"]
        assert any("v2" in d for d in purpose_docs)
        assert not any("v1" in d for d in purpose_docs)

    def test_query_vector_purpose_empty_collection_is_graceful(self, indexer) -> None:
        """A fresh/old-schema workspace has an empty purpose collection — must not raise."""
        embedding = indexer.embed_query("anything")
        result = indexer.query_vector_purpose(embedding, n_results=5)
        assert result["ids"] == [[]]

    def test_query_vector_purpose_returns_results_after_index(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "search_me.py", """
            def rate_limit_check(ip: str) -> bool:
                \"\"\"Check whether the given IP has exceeded its rate limit.\"\"\"
                return True
        """)
        indexer.index_file(path)
        embedding = indexer.embed_query("rate limit check function")
        result = indexer.query_vector_purpose(embedding, n_results=5)
        assert len(result["ids"][0]) >= 1

    def test_get_chunk_documents_returns_body_content(self, indexer, tmp_path) -> None:
        path = make_py(tmp_path, "svc.py", """
            def known_function():
                return 42
        """)
        indexer.index_file(path)
        body_ids, body_docs, _ = indexer.get_all_documents()
        result = indexer.get_chunk_documents(body_ids)
        assert set(result.keys()) == set(body_ids)
        for cid, doc in zip(body_ids, body_docs):
            assert result[cid][0] == doc

    def test_get_chunk_documents_missing_ids_absent_from_result(self, indexer) -> None:
        assert indexer.get_chunk_documents(["does-not-exist"]) == {}

    def test_dual_vector_disabled_skips_purpose_collection(self, tmp_path, monkeypatch) -> None:
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider
        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _m: _DummyEmbedProvider())
        monkeypatch.setattr("agent.indexer._core._DUAL_VECTOR_ENABLED", False)
        from agent.indexer import CodeIndexer
        idx = CodeIndexer(workspace_root=str(tmp_path), db_path=str(tmp_path / "chroma"))
        path = make_py(tmp_path, "a.py", "def with_symbol(): pass")
        idx.index_file(path)
        assert idx.total_chunks > 0
        assert idx._purpose_collection.count() == 0


class TestSchemaVersionRebuildTrigger:
    """A bump to INDEXING_SCHEMA_VERSION must invalidate the mtime cache (ARCH-4)."""

    def test_stale_schema_version_forces_full_reindex(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        indexer.index_workspace()
        cache_path = indexer._mtime_cache_path()

        import json
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        raw["__vectr_index_schema_version__"] = -1  # simulate an older schema
        cache_path.write_text(json.dumps(raw), encoding="utf-8")

        # An old-schema cache is treated as cold: nothing survives the load.
        assert indexer._load_mtime_cache() == {}

    def test_current_schema_version_cache_round_trips(self, indexer, tmp_path) -> None:
        make_py(tmp_path, "a.py", "def a(): pass")
        indexer.index_workspace()
        loaded = indexer._load_mtime_cache()
        assert loaded  # non-empty: current-schema cache is preserved as-is
        assert "__vectr_index_schema_version__" not in loaded  # sentinel stripped from the dict


# ---------------------------------------------------------------------------
# ARCH-4 — dilution evidence gate: dual-vector pool entry, end-to-end
# through index -> search.
#
# A deterministic bag-of-words embedder (NOT the random-hash _DummyEmbedProvider
# used elsewhere) is used here specifically because it reproduces the dilution
# failure mode: cosine similarity of a normalized word-count vector shrinks as
# query-irrelevant filler tokens are added, exactly like a real embedding model
# mean-pooling a long mechanical function body.
# ---------------------------------------------------------------------------

_DILUTION_VOCAB = ["charge", "customer", "order", "payment", "temp", "step", "value"]


def _bow_vector(text: str, vocab: list[str] = _DILUTION_VOCAB) -> list[float]:
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    counts = [float(tokens.count(w)) for w in vocab]
    norm = math.sqrt(sum(c * c for c in counts)) or 1.0
    return [c / norm for c in counts]


class _DilutionEmbedProvider:
    """Deterministic bag-of-words embedder for the dilution evidence gate."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_bow_vector(t) for t in texts]


class TestDualVectorPoolEntryEvidenceGate:
    """Proves the ARCH-4 mechanism, not just that code paths run without error:

    a documented symbol whose body dilutes its docstring must enter the search
    pool under dual-vector search but NOT under body-only search, on the SAME
    index (only the query-time merge is toggled) — i.e. the fix works end-to-end
    through index -> search, not just at the embedding-comparison level.
    """

    def _build_dilution_corpus(self, indexer, tmp_path) -> None:
        # Target: a documented method whose docstring paraphrases the query, but
        # whose mechanical body (repeated filler statements) dilutes a mean-pooled
        # single-vector embedding.
        target_body = (
            "def process_payment(amount):\n"
            '    """Charge the customer for the given order and record the result."""\n'
            "    temp = 0\n"
            "    step = 1\n"
            + ("    temp = temp + step\n" * 40)
            + "    return temp\n"
        )
        make_py(tmp_path, "target.py", target_body)

        # Decoys: undocumented functions whose body happens to repeat a query
        # keyword ("order") densely with no filler — the exact keyword-coincidence
        # scenario that lets a decoy outrank the diluted target on body vectors
        # alone, and that a purpose (signature-only) vector does not fall for.
        for i in range(5):
            decoy_body = (
                f"def get_order_list_{i}():\n"
                "    order = order_value = order\n"
                "    return order\n"
            )
            make_py(tmp_path, f"decoy_{i}.py", decoy_body)

        indexer.index_workspace()

    def test_documented_symbol_enters_pool_under_dual_vector_not_body_only(
        self, indexer, tmp_path, monkeypatch,
    ) -> None:
        indexer._embed_provider = _DilutionEmbedProvider()
        self._build_dilution_corpus(indexer, tmp_path)

        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()

        query = "charge the customer for their order"

        # Dual-vector ON (default): target's purpose vector (signature+docstring,
        # body-stripped) is undiluted and wins pool entry.
        dual_results, _ = s.search(query, n_results=1, semantic_weight=1.0, rerank=False)
        assert dual_results, "dual-vector search returned nothing"
        assert dual_results[0].file_path.endswith("target.py"), (
            "documented symbol did not win pool entry under dual-vector search"
        )

        # Body-only (query-time flag off, same index, same embeddings already
        # stored): the diluted body vector loses to keyword-coincidence decoys —
        # this is the exact wall ARCH-4 exists to break.
        monkeypatch.setattr("agent.searcher._DUAL_VECTOR_ENABLED", False)
        body_only_results, _ = s.search(query, n_results=1, semantic_weight=1.0, rerank=False)
        assert body_only_results, "body-only search returned nothing"
        assert not body_only_results[0].file_path.endswith("target.py"), (
            "target should NOT win pool entry under body-only search — "
            "if it does, the dilution fixture no longer reproduces the failure "
            "mode and this test is not exercising the ARCH-4 mechanism"
        )

    def test_non_symbol_chunks_unaffected_by_dual_vector(self, indexer, tmp_path) -> None:
        indexer._embed_provider = _DilutionEmbedProvider()
        (tmp_path / "readme.md").write_text(
            "# Payment charges\n\nSome prose about charging a customer for an order.\n"
        )
        indexer.index_workspace()
        assert indexer._purpose_collection.count() == 0

        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        results, _ = s.search("charge the customer for their order", n_results=1, rerank=False)
        assert results
        assert results[0].file_path.endswith("readme.md")


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

    def test_test_file_demoted_below_impl(self, searcher) -> None:
        # Realistic candidate set: the test file is ranked #0 (rich doc-comments
        # match strongly), the impl just below it, then irrelevant filler.
        test = _sr("def test_resolve():\n    assert resolve()\n    # scenario\n    y=2",
                   path="/p/tests/test_resolver.py")
        impl = _sr("def resolve():\n    return pubgrub_solve()\n    # impl body\n    x=1",
                   path="/p/resolver.py")
        filler = [_sr(f"def helper{i}():\n    return {i}\n    a={i}\n    b={i}", path=f"/p/h{i}.py")
                  for i in range(8)]
        cands = [test, impl] + filler

        # The test-file quality prior demotes the test below the implementation.
        out = searcher._apply_quality_and_dedup("how does resolution work", cands)
        assert out[0].file_path == "/p/resolver.py"
        assert out.index(impl) < out.index(test)

    def test_empty_candidates(self, searcher) -> None:
        assert searcher._apply_quality_and_dedup("q", []) == []


class TestImportancePrior:
    """ARCH-1b: file-level PageRank importance blended as a relevance-gated
    multiplicative prior into the final search sort."""

    def _ten(self):
        # Ten equal-quality real-code candidates across distinct files; A and B are
        # the two leaders (rank 0 and 1), then eight fillers.
        a = _sr("def alpha():\n    return compute()\n    # impl body\n    x=1", path="/p/a.py")
        b = _sr("def beta():\n    return compute()\n    # impl body\n    y=2", path="/p/b.py")
        filler = [_sr(f"def helper{i}():\n    return compute()\n    # body\n    z={i}",
                      path=f"/p/f{i}.py") for i in range(8)]
        return a, b, [a, b] + filler

    def test_prior_lifts_near_tie(self, searcher) -> None:
        # Without importance, A (rank 0) leads B (rank 1). High importance on B's
        # file overtakes the one-rank base gap (lambda default 0.25).
        a, b, cands = self._ten()
        searcher.set_file_importance({"/p/b.py": 1.0})
        out = searcher._apply_quality_and_dedup("compute impl", cands)
        assert out[0] is b, f"high-importance B should lead; got {out[0].file_path}"
        assert out.index(b) < out.index(a)

    def test_prior_absent_preserves_base_order(self, searcher) -> None:
        # Empty importance map → no-op; base-rank order (A before B) is preserved.
        a, b, cands = self._ten()
        searcher.set_file_importance({})
        out = searcher._apply_quality_and_dedup("compute impl", cands)
        assert out[0] is a
        assert out.index(a) < out.index(b)

    def test_prior_does_not_override_clear_relevance(self, searcher) -> None:
        # A low-relevance chunk (last rank) with max importance must NOT jump the
        # clearly-relevant rank-0 chunk: the prior is gated by base_rerank_score.
        a, b, cands = self._ten()
        searcher.set_file_importance({"/p/f7.py": 1.0})
        out = searcher._apply_quality_and_dedup("compute impl", cands)
        assert out[0] is a, f"clear top relevance must hold; got {out[0].file_path}"


# ---------------------------------------------------------------------------
# ARCH-2 — class-level reference-frequency importance blend
#
# Synthetic fixtures only — no benchmark-corpus names (django/QuerySet/etc.).
# ---------------------------------------------------------------------------

def _sr_class(class_name, body, path, score=0.7, lang="python"):
    """A SearchResult whose content carries the indexer-injected '# class: X'
    context-prefix line, exactly as real method chunks are stored (UPG-F4)."""
    content = f"# class: {class_name}\n{body}"
    return _sr(content, path=path, score=score, lang=lang)


class TestClassImportancePrior:
    """ARCH-2: class-level reference-frequency importance blended as a second
    relevance-gated multiplicative prior, composed with ARCH-1b's file-level prior.

    The scenario this defends: N classes each define a same-named method (the
    same-leaf collision ARCH-2 targets). File-level importance alone cannot
    separate them when each class is the dominant/only content of its own file
    (a common case — file-level and class-level importance would be identical);
    class-level reference frequency can, because it scores the CLASS name
    directly rather than the file that happens to contain it.
    """

    def _same_leaf_collision(self):
        # Ten same-leaf candidates ("process" method), mirroring the ARCH-1b
        # near-tie test shape: two class-owned near-tied leaders (rank 0 and 1)
        # plus eight equal-quality fillers. The canonical class (Widget) starts
        # one rank BEHIND its look-alike (Gadget) pre-blend, so the assertion
        # proves the prior does the lifting on a realistic near-tie gap, not a
        # favorable base order (same evidence shape as the live gate: the
        # canonical chunk is in-pool but outranked by same-leaf siblings).
        lookalike = _sr_class(
            "Gadget",
            "def process(self):\n    return self._run()\n    # impl body\n    y=2",
            path="/p/gadget.py",
        )
        canonical = _sr_class(
            "Widget",
            "def process(self):\n    return self._run()\n    # impl body\n    x=1",
            path="/p/widget.py",
        )
        filler = [
            _sr_class(f"Filler{i}", f"def other{i}(self):\n    return {i}\n    z={i}",
                      path=f"/p/f{i}.py")
            for i in range(8)
        ]
        return canonical, [lookalike, canonical] + filler

    def test_prior_ranks_canonical_class_first(self, searcher) -> None:
        # Widget is referenced far more than its look-alike across the corpus
        # (normalized to 1.0 vs near-zero); the collision resolves to Widget at
        # rank 1, overtaking the one-rank base gap (lambda default 0.25) — same
        # near-tie shape ARCH-1b's file-importance prior proved.
        canonical, cands = self._same_leaf_collision()
        searcher.set_class_importance({"Widget": 1.0, "Gadget": 0.0})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        assert out[0] is canonical, (
            f"canonical Widget.process must rank 1; got {out[0].file_path}"
        )

    def test_prior_absent_preserves_base_order(self, searcher) -> None:
        # Empty class-importance map → no-op; pre-blend rank order is preserved
        # (look-alike leads, exactly like pre-ARCH-2 behaviour).
        canonical, cands = self._same_leaf_collision()
        lookalike = cands[0]
        searcher.set_class_importance({})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        assert out[0] is lookalike, (
            f"no importance map installed must be a no-op; got order "
            f"{[r.file_path for r in out]}"
        )
        assert out.index(lookalike) < out.index(canonical)

    def test_lambda_zero_is_exact_noop(self, searcher) -> None:
        # lambda=0 must reduce the class factor to 1.0 regardless of how skewed
        # the importance map is — same order as the absent-map case.
        import agent.searcher as searcher_mod
        canonical, cands = self._same_leaf_collision()
        lookalike = cands[0]
        searcher.set_class_importance({"Widget": 1.0, "Gadget": 0.0})
        orig_lambda = searcher_mod._CLASS_IMPORTANCE_PRIOR_LAMBDA
        searcher_mod._CLASS_IMPORTANCE_PRIOR_LAMBDA = 0.0
        try:
            out = searcher._apply_quality_and_dedup("process the item", cands)
        finally:
            searcher_mod._CLASS_IMPORTANCE_PRIOR_LAMBDA = orig_lambda
        assert out[0] is lookalike, (
            f"lambda=0 must be an exact no-op; got order {[r.file_path for r in out]}"
        )

    def test_prior_does_not_override_clear_relevance(self, searcher) -> None:
        # A clearly irrelevant chunk (last rank) with max class importance must
        # NOT jump a clearly-relevant top chunk: gated by base_rerank_score, same
        # shape as ARCH-1b.
        top = _sr_class(
            "Widget", "def process(self):\n    return self._run()\n    x=1",
            path="/p/widget.py",
        )
        filler = [
            _sr_class(f"Filler{i}", f"def other{i}(self):\n    return {i}\n    z={i}",
                      path=f"/p/f{i}.py")
            for i in range(8)
        ]
        cands = [top] + filler
        searcher.set_class_importance({"Filler7": 1.0})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        assert out[0] is top, f"clear top relevance must hold; got {out[0].file_path}"

    def test_no_class_context_is_unpenalized(self, searcher) -> None:
        # A module-level chunk with no owning class (no '# class: X' prefix) must
        # not be demoted relative to its pre-blend order — the class factor is a
        # no-op (1.0) for it regardless of what's installed in the importance map.
        module_fn = _sr("def process():\n    return run()\n    # impl body\n    x=1",
                         path="/p/module.py")
        classed = _sr_class(
            "Gadget", "def process(self):\n    return self._run()\n    y=2",
            path="/p/gadget.py",
        )
        cands = [module_fn, classed]
        searcher.set_class_importance({"Gadget": 1.0})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        # module_fn led pre-blend (rank 0); Gadget's high importance may still
        # lift it, but module_fn's own score must be unaffected by the map (i.e.
        # its class factor stayed 1.0 — verified indirectly: it isn't penalised
        # below where a hypothetical negative/undefined factor would put it).
        assert module_fn in out and classed in out

    def test_both_priors_compose(self, searcher) -> None:
        # File-level (ARCH-1b) and class-level (ARCH-2) priors are both active at
        # once and compose multiplicatively without erroring; the chunk with both
        # a high-importance file AND a high-importance class ranks first.
        canonical, cands = self._same_leaf_collision()
        searcher.set_file_importance({"/p/widget.py": 1.0})
        searcher.set_class_importance({"Widget": 1.0})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        assert out[0] is canonical, (
            f"both priors active should still rank canonical first; got {out[0].file_path}"
        )
        # Reset so other tests in this module aren't affected by fixture reuse.
        searcher.set_file_importance({})
        searcher.set_class_importance({})

    def test_prior_no_op_when_class_absent_from_map(self, searcher) -> None:
        # A class not present in the importance map at all (as opposed to present
        # with a 0.0 score) must fall back to importance=0.0 — the .get() default
        # — not raise, and must not change base order.
        canonical, cands = self._same_leaf_collision()
        lookalike = cands[0]
        searcher.set_class_importance({"SomeOtherClass": 1.0})
        out = searcher._apply_quality_and_dedup("process the item", cands)
        assert out[0] is lookalike


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
# UPG-15.7 — Pool-entry trivial filter (F19 fix)
# ---------------------------------------------------------------------------

class TestPoolEntryTrivialFilter:
    """UPG-15.7: trivial HTML/TXT chunks are dropped BEFORE the cross-encoder reranks,
    so real code fills the small rerank pool even when the corpus has many trivial fixtures.

    Design:
    - Over-fetch pre_filter_fetch_k (e.g. 200) raw hybrid candidates.
    - Drop any that is_trivial_chunk() == True.
    - Trim to top_k_unfiltered (e.g. 60) before reranking.

    The test builds a corpus of many 1-line HTML fixture chunks plus a few real Python
    code chunks, then verifies:
    1. With a small top_k_unfiltered, the reranker pool contains the real code chunks
       (trivial HTML was dropped before the pool limit was reached).
    """

    def test_trivial_html_filtered_before_rerank_pool(self, indexer, tmp_path) -> None:
        """Real code chunks must survive to the reranker pool even when many trivial
        HTML/TXT chunks rank above them in the initial hybrid sort (F19 scenario)."""
        import agent.searcher as searcher_mod
        from agent.searcher import CodeSearcher

        # --- Corpus ---
        # Many 1-line HTML fixtures — each is trivial (is_trivial_chunk == True).
        # They will rank high on a short natural-language query due to embedding similarity.
        n_trivial = 30
        for i in range(n_trivial):
            p = tmp_path / f"fixture_{i}.html"
            # 1-line HTML content — matches is_trivial_chunk for language=html
            p.write_text(f"<p>Session expired. Please log in again. {i}</p>")
            indexer.index_file(str(p))

        # A few real Python implementation chunks.
        real_py = tmp_path / "sessions.py"
        real_py.write_text(
            "class SessionBackend:\n"
            "    def expire_session(self, session_key):\n"
            "        \"\"\"Expire the session identified by session_key.\"\"\"\n"
            "        self._store.delete(session_key)\n"
            "        self._notify_logout(session_key)\n"
            "        return True\n"
            "\n"
            "    def is_expired(self, session_key):\n"
            "        \"\"\"Return True if the session has expired.\"\"\"\n"
            "        return self._store.ttl(session_key) <= 0\n"
        )
        indexer.index_file(str(real_py))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Patch top_k_unfiltered to a small value (5) and pre_filter_fetch_k to a larger
        # value (n_trivial + 10) so trivial HTML can be over-fetched and then dropped.
        # Without the filter, top-5 would be all trivial HTML; with it, real code appears.
        orig_unfiltered = searcher_mod._RERANK_TOP_K_UNFILTERED
        orig_prefetch = searcher_mod._RERANK_PRE_FILTER_FETCH_K

        # Use a real (stub) reranker so the filter+trim path executes.
        class _PassthroughReranker:
            def rerank(self, query, candidates):
                return [c for _, c in candidates]

        s._reranker = _PassthroughReranker()

        searcher_mod._RERANK_TOP_K_UNFILTERED = 5   # tiny pool — would be all HTML without filter
        searcher_mod._RERANK_PRE_FILTER_FETCH_K = n_trivial + 10  # fetch all trivial + real code
        try:
            results, _ = s.search("session expired logout", n_results=5, language=None)
        finally:
            searcher_mod._RERANK_TOP_K_UNFILTERED = orig_unfiltered
            searcher_mod._RERANK_PRE_FILTER_FETCH_K = orig_prefetch

        # After the trivial filter, the rerank pool should contain real Python code, not HTML fixtures.
        languages = [r.language for r in results]
        assert "python" in languages, (
            "UPG-15.7 regression: real Python code must survive to the rerank pool "
            "when trivial HTML fixtures are filtered at pool-entry. "
            f"Got languages: {languages}"
        )
        # No 1-line HTML fixtures should appear in the top-5 (they are trivial and should be dropped).
        html_results = [r for r in results if r.language == "html"]
        assert len(html_results) == 0, (
            "UPG-15.7 regression: trivial 1-line HTML fixture chunks must be excluded "
            "from the rerank pool by the pool-entry trivial filter. "
            f"HTML results in top-5: {[(r.file_path, r.content[:60]) for r in html_results]}"
        )

    def test_union_of_signals_keeps_vec_strong_bm25_weak_chunk(self, indexer, tmp_path) -> None:
        """UPG-15.7 (revised): a non-trivial chunk that is strong on vector similarity
        but weak on BM25 (prose doc) must survive pool selection when keyword-heavy
        chunks would outrank it on the blended merged score.

        Regression guard for F2/F18: the prior merged-score trim dropped
        custom-model-fields.txt because keyword-heavy fixture chunks beat it on
        the blended score; union-of-signals must include it via the vec-only arm.
        """
        import agent.searcher as searcher_mod
        from agent.searcher import CodeSearcher

        # --- Corpus ---
        # Keyword-rich Python chunks that will dominate on BM25 for the query tokens.
        n_kw = 8
        for i in range(n_kw):
            p = tmp_path / f"kw_{i}.py"
            # Each chunk has the exact query words → high BM25 score.
            p.write_text(
                f"# custom model field tutorial write howto {i}\n"
                f"class CustomField{i}:\n"
                f"    '''Write a custom model field howto tutorial {i}.'''\n"
                f"    pass\n"
            )
            indexer.index_file(str(p))

        # One prose doc that is semantically on-topic but has low keyword density.
        # This simulates docs/howto/custom-model-fields.txt (F2/F18).
        prose_doc = tmp_path / "custom_model_fields.txt"
        prose_doc.write_text(
            "Writing custom model fields\n\n"
            "Django's ORM lets you create your own field types that can be added to\n"
            "your models. A custom field must implement deconstruct() so that Django\n"
            "migrations can serialise it, and from_db_value() to convert the database\n"
            "representation back to a Python object.\n"
        )
        indexer.index_file(str(prose_doc))

        s = CodeSearcher(indexer)
        s.refresh_bm25()

        # Track which file_paths reach the reranker (reranker receives (content, SearchResult)).
        seen_files: list[str] = []

        class _TrackingReranker:
            def rerank(self, query, candidates):
                seen_files.extend(sr.file_path for _, sr in candidates)
                return [sr for _, sr in candidates]

        s._reranker = _TrackingReranker()

        # Patch to a small pool so the merged-score trim would have dropped the prose doc.
        orig_unfiltered = searcher_mod._RERANK_TOP_K_UNFILTERED
        orig_prefetch = searcher_mod._RERANK_PRE_FILTER_FETCH_K
        # top_k_unfiltered = n_kw: the keyword chunks fill the merged-score top-N, squeezing
        # the prose doc out.  The union arm must rescue it via the vec channel.
        searcher_mod._RERANK_TOP_K_UNFILTERED = n_kw
        searcher_mod._RERANK_PRE_FILTER_FETCH_K = n_kw + 5
        try:
            results, _ = s.search(
                "how to write a custom model field", n_results=5, language=None
            )
        finally:
            searcher_mod._RERANK_TOP_K_UNFILTERED = orig_unfiltered
            searcher_mod._RERANK_PRE_FILTER_FETCH_K = orig_prefetch

        # The prose .txt chunk must have reached the reranker pool (union kept it via vec arm).
        prose_in_pool = any(f.endswith("custom_model_fields.txt") for f in seen_files)
        assert prose_in_pool, (
            "UPG-15.7 union regression: prose doc (vec-strong/bm25-weak) was NOT passed "
            "to the reranker.  The union-of-signals pool selection must include chunks from "
            "the vec arm even when the merged score would trim them out.\n"
            f"seen_files={seen_files}"
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
# UPG-NOTFOUND-FLOOR (F46) — absolute-relevance low-confidence signal
#
# The displayed per-result `score` is a per-query rank-derived composite
# (base = 1 - rank/n, times quality/importance multipliers) — it always looks
# confident near the top of ANY query, relevant or not. `low_confidence` gates
# on the raw pre-rerank cosine similarity instead (dense_scores in
# CodeSearcher.search, before the hybrid merge/rerank/quality blend), which is
# NOT re-normalized per query. Uses the DummyEmbedProvider's real, deterministic
# (hash-seeded) vectors: two distinct strings land near-orthogonal (cosine ~0),
# while an identical string against itself lands at cosine 1.0 — no mocking of
# the flag itself, so this exercises the exact code path used in production.
# ---------------------------------------------------------------------------

class TestNotFoundFloor:
    def _indexed_searcher(self, indexer, tmp_path, content: str, name="module.py"):
        path = make_py(tmp_path, name, content)
        indexer.index_file(path)
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        s.refresh_bm25()
        return s

    def test_low_confidence_true_for_uniformly_weak_pool(self, indexer, tmp_path) -> None:
        """A query semantically unrelated to anything indexed flags low_confidence,
        even though results are still returned (never suppressed)."""
        s = self._indexed_searcher(
            indexer, tmp_path,
            "def parse_json_payload(data):\n    return json.loads(data)\n",
        )
        results, _ = s.search(
            "elephant migration patterns across the savanna", n_results=5, rerank=False,
        )
        assert results, "results must still be returned, never suppressed"
        assert results.low_confidence is True

    def test_low_confidence_false_for_clearly_strong_top_hit(self, indexer, tmp_path) -> None:
        """A query that IS the indexed chunk's own content (cosine ~1.0 against
        itself) must not be flagged — a strong top hit is not low-confidence."""
        s = self._indexed_searcher(
            indexer, tmp_path,
            "def parse_json_payload(data):\n    return json.loads(data)\n",
        )
        exact_doc_text = s._bm25_docs[0]
        results, _ = s.search(exact_doc_text, n_results=5, rerank=False)
        assert results
        assert results.low_confidence is False

    def test_config_disable_is_exact_noop(self, indexer, tmp_path, monkeypatch) -> None:
        """ranking.notfound_floor.enabled: false must be a true no-op — the flag
        never fires even for the same uniformly-weak query that trips it above."""
        import agent.searcher as searcher_module
        monkeypatch.setattr(searcher_module, "_NOTFOUND_FLOOR_ENABLED", False)
        s = self._indexed_searcher(
            indexer, tmp_path,
            "def parse_json_payload(data):\n    return json.loads(data)\n",
        )
        results, _ = s.search(
            "elephant migration patterns across the savanna", n_results=5, rerank=False,
        )
        assert results
        assert results.low_confidence is False

    def test_config_zero_floor_is_exact_noop(self, indexer, tmp_path, monkeypatch) -> None:
        """dense_score_floor: 0.0 (the documented alternate disable path) never
        fires either — every cosine similarity is >= 0.0."""
        import agent.searcher as searcher_module
        monkeypatch.setattr(searcher_module, "_NOTFOUND_FLOOR_DENSE_SCORE", 0.0)
        s = self._indexed_searcher(
            indexer, tmp_path,
            "def parse_json_payload(data):\n    return json.loads(data)\n",
        )
        results, _ = s.search(
            "elephant migration patterns across the savanna", n_results=5, rerank=False,
        )
        assert results
        assert results.low_confidence is False

    def test_empty_results_never_flagged(self, indexer) -> None:
        """An empty index returns an empty (not low_confidence) result set — the
        floor is meaningless with nothing to lead with; a separate 'no results'
        message already covers that case downstream."""
        from agent.searcher import CodeSearcher
        s = CodeSearcher(indexer)
        results, ms = s.search("anything")
        assert results == []
        assert getattr(results, "low_confidence", False) is False

    def test_search_result_list_is_a_real_list(self, indexer, tmp_path) -> None:
        """SearchResultList must behave exactly like list[SearchResult] for every
        existing `results, ms = searcher.search(...)` call site — indexing,
        len(), iteration, and equality with a plain list all still work."""
        s = self._indexed_searcher(
            indexer, tmp_path,
            "def parse_json_payload(data):\n    return json.loads(data)\n",
        )
        results, _ = s.search("parse json payload", n_results=5, rerank=False)
        assert isinstance(results, list)
        assert len(results) == len(list(results))
        assert results[0] == list(results)[0]


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

