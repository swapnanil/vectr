"""Tests for cartographer — raw metadata collection and passport store."""
from __future__ import annotations

import json
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# collect_raw_metadata
# ---------------------------------------------------------------------------

class TestCollectRawMetadata:
    def test_returns_workspace_name(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert meta["workspace_name"] == tmp_path.name

    def test_detects_python_language(self, tmp_path) -> None:
        (tmp_path / "main.py").write_text("def main(): pass")
        (tmp_path / "utils.py").write_text("def util(): pass")
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert "Python" in meta["languages"]

    def test_detects_go_language(self, tmp_path) -> None:
        (tmp_path / "main.go").write_text("package main")
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert "Go" in meta["languages"]

    def test_detects_node_framework(self, tmp_path) -> None:
        (tmp_path / "package.json").write_text('{"name": "app"}')
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert any("Node" in f for f in meta["frameworks"])

    def test_readme_excerpt_included(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# My Project\nDoes cool things.")
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert "My Project" in meta["readme_excerpt"]

    def test_structure_excludes_hidden_dirs(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("")
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        dirs = list(meta["structure"].keys())
        assert not any(".git" in d for d in dirs)

    def test_collected_at_is_recent(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata
        before = time.time()
        meta = collect_raw_metadata(str(tmp_path))
        after = time.time()
        assert before <= meta["collected_at"] <= after

    def test_empty_workspace(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata
        meta = collect_raw_metadata(str(tmp_path))
        assert meta["languages"] == []
        assert isinstance(meta["structure"], dict)


# ---------------------------------------------------------------------------
# format_raw_metadata_for_llm
# ---------------------------------------------------------------------------

class TestFormatRawMetadata:
    def test_contains_no_passport_instruction(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata, format_raw_metadata_for_llm
        meta = collect_raw_metadata(str(tmp_path))
        text = format_raw_metadata_for_llm(meta)
        assert "vectr_map_save" in text

    def test_shows_detected_languages(self, tmp_path) -> None:
        (tmp_path / "app.py").write_text("")
        from agent.cartographer import collect_raw_metadata, format_raw_metadata_for_llm
        meta = collect_raw_metadata(str(tmp_path))
        text = format_raw_metadata_for_llm(meta)
        assert "Python" in text


# ---------------------------------------------------------------------------
# PassportStore
# ---------------------------------------------------------------------------

class TestPassportStore:
    def _store(self, tmp_path):
        from agent.cartographer import PassportStore
        return PassportStore(str(tmp_path))

    def test_no_passport_returns_raw_metadata(self, tmp_path) -> None:
        store = self._store(tmp_path)
        text = store.format_for_llm(str(tmp_path))
        assert "vectr_map_save" in text
        assert "No passport cached" in text

    def test_save_and_load_summary(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.save_summary("Python FastAPI service for X.", str(tmp_path))
        data = store.load()
        assert data is not None
        assert data["summary"] == "Python FastAPI service for X."
        assert data["_source"] == "ai_editor"

    def test_format_for_llm_returns_cached_summary(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.save_summary("My codebase summary.", str(tmp_path))
        text = store.format_for_llm(str(tmp_path))
        assert "My codebase summary." in text
        assert "vectr_map_save" not in text

    def test_exists_false_before_save(self, tmp_path) -> None:
        store = self._store(tmp_path)
        assert store.exists() is False

    def test_exists_true_after_save(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.save_summary("summary", str(tmp_path))
        assert store.exists() is True

    def test_save_summary_records_workspace(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.save_summary("summary", str(tmp_path))
        data = store.load()
        assert data["_workspace"] == str(tmp_path)

    def test_overwrite_summary(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.save_summary("first summary", str(tmp_path))
        store.save_summary("updated summary", str(tmp_path))
        text = store.format_for_llm(str(tmp_path))
        assert "updated summary" in text
        assert "first summary" not in text
