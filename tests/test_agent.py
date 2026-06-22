"""Unit tests for indexer, searcher, and chunking logic. No real API calls."""
from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_temp_py(content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
    tmp.write(textwrap.dedent(content))
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# chunk_file tests
# ---------------------------------------------------------------------------

class TestChunkFile:
    def test_python_ast_chunks(self) -> None:
        path = make_temp_py("""
            def hello(name: str) -> str:
                return f"Hello, {name}"

            class Greeter:
                def greet(self) -> None:
                    pass
        """)
        from agent.indexer import chunk_file
        chunks = chunk_file(path)
        assert len(chunks) >= 2
        symbols = {c.symbol_name for c in chunks}
        assert "hello" in symbols or "Greeter" in symbols

    def test_chunk_id_format(self) -> None:
        # Use a non-trivial function body so the chunk is not filtered by is_trivial_chunk
        # (UPG-15.1: 2-line declaration+pass stubs are now dropped as trivial).
        # This test checks the chunk_id format "file:symbol", not stub filtering.
        path = make_temp_py("""
            def foo(x: int) -> int:
                return x + 1
        """)
        from agent.indexer import chunk_file
        chunks = chunk_file(path)
        assert chunks
        assert ":" in chunks[0].chunk_id

    def test_fallback_windows_for_unsupported_ext(self, tmp_path) -> None:
        rb_file = tmp_path / "app.rb"
        rb_file.write_text("\n".join([f"# line {i}" for i in range(250)]))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(rb_file))
        assert len(chunks) >= 2  # should produce overlapping windows
        assert all(c.node_type == "window" for c in chunks)

    def test_empty_file_returns_no_chunks(self, tmp_path) -> None:
        empty = tmp_path / "empty.py"
        empty.write_text("")
        from agent.indexer import chunk_file
        assert chunk_file(str(empty)) == []

    def test_chunk_preserves_start_end_lines(self) -> None:
        path = make_temp_py("""
            def alpha() -> None:
                pass

            def beta() -> None:
                pass
        """)
        from agent.indexer import chunk_file
        chunks = chunk_file(path)
        for c in chunks:
            assert c.start_line <= c.end_line
            assert c.start_line >= 1


# ---------------------------------------------------------------------------
# Chunking fallback edge cases — mixed-language, window math
# ---------------------------------------------------------------------------

class TestChunkFileFallbackEdgeCases:
    """Covers files with no tree-sitter grammar and window-size boundary behavior."""

    def test_jsx_file_is_chunked_as_javascript(self, tmp_path) -> None:
        jsx_file = tmp_path / "UserCard.jsx"
        jsx_file.write_text(textwrap.dedent("""\
            import React from 'react';

            function UserCard({ name, email }) {
              return (
                <div className="card">
                  <h2>{name}</h2>
                  <p>{email}</p>
                </div>
              );
            }

            export default UserCard;
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(jsx_file))
        assert len(chunks) >= 1
        assert all(c.language == "javascript" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "UserCard" in combined

    def test_tsx_file_is_chunked_as_typescript(self, tmp_path) -> None:
        tsx_file = tmp_path / "Button.tsx"
        tsx_file.write_text(textwrap.dedent("""\
            import React from 'react';

            interface ButtonProps {
              label: string;
              onClick: () => void;
            }

            function Button({ label, onClick }: ButtonProps) {
              return <button onClick={onClick}>{label}</button>;
            }

            export default Button;
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(tsx_file))
        assert len(chunks) >= 1
        assert all(c.language == "typescript" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "Button" in combined

    def test_html_file_uses_window_fallback(self, tmp_path) -> None:
        html_file = tmp_path / "index.html"
        html_file.write_text(textwrap.dedent("""\
            <!DOCTYPE html>
            <html>
            <head><title>App</title></head>
            <body>
              <div id="root"></div>
              <script>
                function initApp() {
                  const root = document.getElementById('root');
                  root.innerHTML = '<h1>Hello</h1>';
                }
                initApp();
              </script>
            </body>
            </html>
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(html_file))
        assert len(chunks) >= 1
        assert all(c.node_type == "window" for c in chunks)
        assert all(c.language == "html" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "initApp" in combined

    def test_jinja_template_uses_window_fallback(self, tmp_path) -> None:
        jinja_file = tmp_path / "users.jinja"
        jinja_file.write_text(textwrap.dedent("""\
            {% extends "base.html" %}
            {% block content %}
              <ul>
              {% for user in users %}
                <li>{{ user.name }} — {{ user.email }}</li>
              {% endfor %}
              </ul>
            {% endblock %}
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(jinja_file))
        assert len(chunks) >= 1
        assert all(c.node_type == "window" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "user.name" in combined

    def test_python_inline_sql_preserves_sql_content(self, tmp_path) -> None:
        py_file = tmp_path / "repo.py"
        py_file.write_text(textwrap.dedent("""\
            def get_active_users(db):
                query = '''
                    SELECT id, name, email
                    FROM users
                    WHERE active = TRUE
                    ORDER BY created_at DESC
                '''
                return db.execute(query).fetchall()
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(py_file))
        assert len(chunks) >= 1
        assert all(c.language == "python" for c in chunks)
        combined = "\n".join(c.content for c in chunks)
        assert "SELECT" in combined
        assert "get_active_users" in combined

    def test_window_fallback_small_file_is_single_chunk(self, tmp_path) -> None:
        short_file = tmp_path / "config.rb"
        short_file.write_text("\n".join(f"# line {i}" for i in range(100)))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(short_file))
        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 100

    def test_window_fallback_overlap_math(self, tmp_path) -> None:
        # 300-line file: window=200, overlap=50 → step=150
        # Window 1: lines 1-200  (i=0)
        # Window 2: lines 151-300 (i=150)
        long_file = tmp_path / "long.rb"
        long_file.write_text("\n".join(f"# line {i}" for i in range(300)))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(long_file))
        assert len(chunks) == 2
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 200
        assert chunks[1].start_line == 151  # overlap starts here
        assert chunks[1].end_line == 300

    def test_window_fallback_language_label_from_extension(self, tmp_path) -> None:
        sql_file = tmp_path / "schema.sql"
        sql_file.write_text("CREATE TABLE users (id INT PRIMARY KEY, name TEXT NOT NULL);")
        from agent.indexer import chunk_file
        chunks = chunk_file(str(sql_file))
        assert len(chunks) >= 1
        assert all(c.language == "sql" for c in chunks)
        assert all(c.node_type == "window" for c in chunks)

    def test_python_module_level_only_uses_window_fallback(self, tmp_path) -> None:
        py_file = tmp_path / "settings.py"
        py_file.write_text(textwrap.dedent("""\
            DEBUG = True
            DATABASE_URL = "postgresql://localhost/mydb"
            SECRET_KEY = "abc123"
            ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
        """))
        from agent.indexer import chunk_file
        chunks = chunk_file(str(py_file))
        assert len(chunks) >= 1
        assert all(c.node_type == "window" for c in chunks)


# ---------------------------------------------------------------------------
# workspace_detect tests
# ---------------------------------------------------------------------------

class TestWorkspaceDetect:
    def test_find_workspace_root_git(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "app"
        subdir.mkdir(parents=True)
        from integrations.workspace_detect import find_workspace_root
        assert find_workspace_root(str(subdir)) == str(tmp_path)

    def test_find_workspace_root_fallback(self, tmp_path) -> None:
        from integrations.workspace_detect import find_workspace_root
        result = find_workspace_root(str(tmp_path))
        assert result == str(tmp_path)

    def test_should_index_file_py(self, tmp_path) -> None:
        f = tmp_path / "app.py"
        f.touch()
        from integrations.workspace_detect import should_index_file
        assert should_index_file(str(f), []) is True

    def test_should_index_file_gitignore(self, tmp_path) -> None:
        f = tmp_path / "generated.py"
        f.touch()
        from integrations.workspace_detect import should_index_file
        assert should_index_file(str(f), ["generated.py"]) is False

    def test_should_skip_node_modules(self, tmp_path) -> None:
        f = tmp_path / "node_modules" / "pkg" / "index.js"
        f.parent.mkdir(parents=True)
        f.touch()
        from integrations.workspace_detect import should_index_file
        assert should_index_file(str(f), []) is False

    def test_should_skip_unsupported_ext(self, tmp_path) -> None:
        f = tmp_path / "data.csv"
        f.touch()
        from integrations.workspace_detect import should_index_file
        assert should_index_file(str(f), []) is False


# ---------------------------------------------------------------------------
# vscode_bridge tests
# ---------------------------------------------------------------------------

class TestVscodeBridge:
    def test_configure_cursor_creates_file(self, tmp_path) -> None:
        from integrations.vscode_bridge import configure_cursor
        configure_cursor(str(tmp_path), port=8765)
        mcp_file = tmp_path / ".cursor" / "mcp.json"
        assert mcp_file.exists()
        import json
        data = json.loads(mcp_file.read_text())
        assert "vectr" in data["mcpServers"]
        assert "8765" in data["mcpServers"]["vectr"]["url"]

    def test_configure_cursor_merges_existing(self, tmp_path) -> None:
        import json
        mcp_dir = tmp_path / ".cursor"
        mcp_dir.mkdir()
        existing = {"mcpServers": {"other-tool": {"url": "http://localhost:9000"}}}
        (mcp_dir / "mcp.json").write_text(json.dumps(existing))
        from integrations.vscode_bridge import configure_cursor
        configure_cursor(str(tmp_path), port=8765)
        data = json.loads((mcp_dir / "mcp.json").read_text())
        assert "other-tool" in data["mcpServers"]  # existing preserved
        assert "vectr" in data["mcpServers"]  # new added

    def test_configure_claude_code(self, tmp_path) -> None:
        from integrations.vscode_bridge import configure_claude_code
        configure_claude_code(str(tmp_path), port=8765)
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.exists()
        import json
        data = json.loads(settings.read_text())
        assert data["mcpServers"]["vectr"]["type"] == "http"


# ---------------------------------------------------------------------------
# MCP server tests
# ---------------------------------------------------------------------------

class TestMcpServer:
    def test_tools_list_returns_all_tools(self) -> None:
        from integrations.mcp_server import handle_tools_list
        result = handle_tools_list()
        names = {t["name"] for t in result["tools"]}
        # core tools
        assert "vectr_search" in names
        assert "vectr_status" in names
        # L1 map (passport)
        assert "vectr_map" in names
        assert "vectr_map_save" in names
        # L2 symbol graph
        assert "vectr_locate" in names
        assert "vectr_trace" in names
        # memory layer
        assert "vectr_remember" in names
        assert "vectr_recall" in names
        assert "vectr_evict_hint" in names
        assert "vectr_snapshot" in names

    def test_tools_call_search_missing_query(self) -> None:
        from integrations.mcp_server import handle_tools_call
        mock_svc = MagicMock()
        result = handle_tools_call("vectr_search", {}, mock_svc)
        assert result["isError"] is True

    def test_tools_call_status(self) -> None:
        from integrations.mcp_server import handle_tools_call
        mock_svc = MagicMock()
        mock_svc.status.return_value = {
            "indexed_files": 42,
            "total_chunks": 1200,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "BAAI/bge-base-en-v1.5",
            "workspace_root": "/repo",
        }
        result = handle_tools_call("vectr_status", {}, mock_svc)
        assert result["isError"] is False
        assert "42" in result["content"][0]["text"]

    def test_tools_call_map_save(self) -> None:
        from integrations.mcp_server import handle_tools_call
        mock_svc = MagicMock()
        result = handle_tools_call("vectr_map_save", {"summary": "Python FastAPI service."}, mock_svc)
        assert result["isError"] is False
        mock_svc.save_map.assert_called_once_with("Python FastAPI service.")

    def test_tools_call_map_save_missing_summary(self) -> None:
        from integrations.mcp_server import handle_tools_call
        result = handle_tools_call("vectr_map_save", {}, MagicMock())
        assert result["isError"] is True

    def test_tools_call_locate(self) -> None:
        from integrations.mcp_server import handle_tools_call
        mock_svc = MagicMock()
        mock_svc.locate_with_snippets.return_value = []
        mock_svc.format_locate.return_value = "No results."
        result = handle_tools_call("vectr_locate", {"name": "MyClass"}, mock_svc)
        assert result["isError"] is False
        mock_svc.locate_with_snippets.assert_called_once_with("MyClass", limit=10, caller_file=None)

    def test_tools_call_unknown_tool(self) -> None:
        from integrations.mcp_server import handle_tools_call
        result = handle_tools_call("nonexistent_tool", {}, MagicMock())
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# WorkingContextStore tests
# ---------------------------------------------------------------------------

class TestWorkingContextStore:
    def _store(self, tmp_path):
        from agent.working_context_store import WorkingContextStore
        return WorkingContextStore(str(tmp_path))

    def test_remember_and_recall(self, tmp_path) -> None:
        store = self._store(tmp_path)
        note_id = store.remember("/repo", "Working on segment targeting", tags=["seg"], priority="high")
        assert isinstance(note_id, int)
        notes = store.recall("/repo")
        assert len(notes) == 1
        assert notes[0].content == "Working on segment targeting"
        assert notes[0].priority == "high"

    def test_recall_with_query_filter(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.remember("/repo", "segment targeting entry point is EvaluateSegments", tags=["seg"])
        store.remember("/repo", "bid pipeline starts in RequestBid", tags=["bid"])
        notes = store.recall("/repo", query="EvaluateSegments")
        assert len(notes) == 1
        assert "EvaluateSegments" in notes[0].content

    def test_recall_with_tag_filter(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.remember("/repo", "note A", tags=["seg"])
        store.remember("/repo", "note B", tags=["bid"])
        notes = store.recall("/repo", tags=["bid"])
        assert len(notes) == 1
        assert notes[0].content == "note B"

    def test_forget_removes_note(self, tmp_path) -> None:
        store = self._store(tmp_path)
        nid = store.remember("/repo", "to forget")
        removed = store.forget("/repo", nid)
        assert removed is True
        assert store.recall("/repo") == []

    def test_count_notes_returns_zero_for_empty_workspace(self, tmp_path) -> None:
        store = self._store(tmp_path)
        assert store.count_notes("/repo") == 0

    def test_count_notes_increments_on_remember(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.remember("/repo", "note one")
        assert store.count_notes("/repo") == 1
        store.remember("/repo", "note two")
        assert store.count_notes("/repo") == 2

    def test_count_notes_decrements_on_forget(self, tmp_path) -> None:
        store = self._store(tmp_path)
        nid = store.remember("/repo", "to forget")
        assert store.count_notes("/repo") == 1
        store.forget("/repo", nid)
        assert store.count_notes("/repo") == 0

    def test_count_notes_is_workspace_scoped(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.remember("/repo/a", "note in a")
        store.remember("/repo/b", "note in b")
        assert store.count_notes("/repo/a") == 1
        assert store.count_notes("/repo/b") == 1

    def test_eviction_hint_lists_chunks(self, tmp_path) -> None:
        store = self._store(tmp_path)
        chunks = [{"file": "main.py", "lines": "1-40", "symbol": "run", "content": "def run(): pass"}]
        hint = store.build_eviction_hint("/repo", chunks)
        assert "main.py" in hint
        assert "<50ms" in hint

    def test_snapshot_and_restore(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.remember("/repo", "snapshot test note")
        sid = store.snapshot("/repo", label="test-snap")
        restored = store.restore_snapshot(sid)
        assert restored is not None
        assert len(restored["notes"]) == 1


# ---------------------------------------------------------------------------
# SymbolGraph tests
# ---------------------------------------------------------------------------

class TestSymbolGraph:
    def _graph(self, tmp_path):
        from agent.symbol_graph import SymbolGraph
        return SymbolGraph(str(tmp_path))

    def test_extract_python_symbols(self) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        path = make_temp_py("""
            def foo():
                bar()

            class MyClass:
                def method(self):
                    pass
        """)
        symbols, edges = extract_symbols_from_file(path)
        names = {s["name"] for s in symbols}
        assert "foo" in names
        assert "MyClass" in names

    def test_extract_python_call_edges(self) -> None:
        from agent.symbol_graph import extract_symbols_from_file
        path = make_temp_py("""
            def caller():
                callee()
        """)
        symbols, edges = extract_symbols_from_file(path)
        call_names = {e["to_symbol"] for e in edges if e["edge_type"] == "calls"}
        assert "callee" in call_names

    def test_index_file_and_locate(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        path = make_temp_py("""
            def find_me():
                pass
        """)
        graph.index_file("/repo", path)
        results = graph.locate("/repo", "find_me")
        assert len(results) == 1
        assert results[0].name == "find_me"
        assert results[0].kind == "function"

    def test_locate_partial_match(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        path = make_temp_py("""
            def process_segment():
                pass
            def process_bid():
                pass
        """)
        graph.index_file("/repo", path)
        results = graph.locate("/repo", "process")
        assert len(results) == 2

    def test_callers_trace(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        path = make_temp_py("""
            def evaluate():
                pass
            def run():
                evaluate()
        """)
        graph.index_file("/repo", path)
        callers = graph.callers("/repo", "evaluate")
        assert any(e.from_symbol == "run" for e in callers)

    def test_locate_returns_snippet(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        path = make_temp_py("""
            def documented_fn():
                x = 1
                return x
        """)
        graph.index_file("/repo", path)
        results = graph.locate("/repo", "documented_fn")
        assert len(results) == 1
        assert "documented_fn" in results[0].snippet

    def test_format_locate_includes_snippet(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        path = make_temp_py("""
            def my_fn():
                pass
        """)
        graph.index_file("/repo", path)
        symbols = graph.locate("/repo", "my_fn")
        text = graph.format_locate_for_llm(symbols, "my_fn")
        assert "my_fn" in text
        assert "def my_fn" in text

    def test_format_locate_no_results(self, tmp_path) -> None:
        graph = self._graph(tmp_path)
        text = graph.format_locate_for_llm([], "missing")
        assert "No symbol" in text


# ---------------------------------------------------------------------------
# EvictionAdvisor tests
# ---------------------------------------------------------------------------

class TestEvictionAdvisor:
    def test_no_eviction_when_empty(self) -> None:
        from agent.eviction_advisor import EvictionAdvisor
        advisor = EvictionAdvisor()
        assert advisor.should_evict() is False
        assert advisor.eviction_hint() == ""

    def test_eviction_hint_content(self) -> None:
        from agent.eviction_advisor import EvictionAdvisor
        advisor = EvictionAdvisor(eviction_threshold_tokens=1)
        advisor.record("main.py", "1-100", "run", "x" * 500)
        hint = advisor.eviction_hint()
        assert "main.py" in hint
        assert "<50ms" in hint

    def test_threshold_triggers_eviction(self) -> None:
        from agent.eviction_advisor import EvictionAdvisor
        advisor = EvictionAdvisor(eviction_threshold_tokens=10)
        advisor.record("big.py", "1-500", "foo", "x" * 500)
        assert advisor.should_evict() is True

    def test_duplicate_chunk_not_recorded_twice(self) -> None:
        from agent.eviction_advisor import EvictionAdvisor
        advisor = EvictionAdvisor()
        advisor.record("a.py", "1-10", "foo", "content")
        advisor.record("a.py", "1-10", "foo", "content")
        assert len(advisor._chunks) == 1

    def test_clear_session(self) -> None:
        from agent.eviction_advisor import EvictionAdvisor
        advisor = EvictionAdvisor()
        advisor.record("a.py", "1-10", "foo", "content")
        advisor.clear_session()
        assert advisor.should_evict() is False


# llm_client tests removed — vectr no longer makes internal LLM calls.
# Intelligence is delegated entirely to the AI editor (Claude Code, Cursor, etc.)
# that has integrated vectr. See upgrade-plan.md core principles.
