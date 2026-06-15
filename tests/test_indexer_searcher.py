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
