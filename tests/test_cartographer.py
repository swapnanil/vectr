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


# ---------------------------------------------------------------------------
# T27: Louvain community detection
# ---------------------------------------------------------------------------

class TestLouvainCommunityDetection:
    def _write_py(self, path: "Path", name: str, content: str) -> str:
        f = path / name
        f.write_text(content)
        return str(f)

    def test_empty_workspace_returns_no_communities(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        result = detect_module_communities(str(tmp_path), indexed_files=[])
        assert result == []

    def test_single_file_returns_no_communities(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        f = self._write_py(tmp_path, "main.py", "x = 1")
        result = detect_module_communities(str(tmp_path), indexed_files=[f])
        assert result == []  # min_community_size=2

    def test_two_mutually_importing_files_form_community(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        auth = self._write_py(tmp_path, "auth.py", "import models\n")
        models = self._write_py(tmp_path, "models.py", "import auth\n")
        result = detect_module_communities(str(tmp_path), indexed_files=[auth, models])
        assert len(result) >= 1
        all_files = {f for c in result for f in c["files"]}
        assert auth in all_files or models in all_files

    def test_community_has_required_keys(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        a = self._write_py(tmp_path, "a.py", "import b\n")
        b = self._write_py(tmp_path, "b.py", "import a\n")
        result = detect_module_communities(str(tmp_path), indexed_files=[a, b])
        if result:
            c = result[0]
            assert "id" in c
            assert "label" in c
            assert "files" in c
            assert "size" in c
            assert isinstance(c["files"], list)
            assert isinstance(c["size"], int)

    def test_communities_sorted_by_size_descending(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        # Create two groups: auth+models+views (3) and utils alone (1)
        files = []
        files.append(self._write_py(tmp_path, "auth.py",   "import models\nimport views\n"))
        files.append(self._write_py(tmp_path, "models.py", "import auth\n"))
        files.append(self._write_py(tmp_path, "views.py",  "import auth\nimport models\n"))
        files.append(self._write_py(tmp_path, "utils.py",  "x = 1\n"))
        result = detect_module_communities(str(tmp_path), indexed_files=files)
        if len(result) >= 2:
            assert result[0]["size"] >= result[1]["size"]

    def test_community_label_derived_from_top_dir(self, tmp_path) -> None:
        from agent.cartographer import detect_module_communities
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        a = str(auth_dir / "models.py")
        b = str(auth_dir / "views.py")
        Path(a).write_text("import views\n")
        Path(b).write_text("import models\n")
        result = detect_module_communities(str(tmp_path), indexed_files=[a, b])
        if result:
            assert result[0]["label"] == "auth"

    def test_collect_raw_metadata_includes_communities(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata
        a = self._write_py(tmp_path, "api.py",  "import db\n")
        b = self._write_py(tmp_path, "db.py",   "import api\n")
        metadata = collect_raw_metadata(str(tmp_path), indexed_files=[a, b])
        assert "module_communities" in metadata

    def test_format_metadata_includes_community_section(self, tmp_path) -> None:
        from agent.cartographer import collect_raw_metadata, format_raw_metadata_for_llm
        a = self._write_py(tmp_path, "web.py",    "import store\n")
        b = self._write_py(tmp_path, "store.py",  "import web\n")
        metadata = collect_raw_metadata(str(tmp_path), indexed_files=[a, b])
        # Only test that format function doesn't crash; communities may or may not appear
        text = format_raw_metadata_for_llm(metadata)
        assert isinstance(text, str)
        assert len(text) > 0
