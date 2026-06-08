"""
Tests for integrations/workspace_detect.py.

Covers:
  - find_workspace_root: finds .git directory walking up
  - find_workspace_root: falls back to start_path when no .git
  - find_workspace_root: works when given a file path (uses parent)
  - get_gitignore_patterns: reads patterns from .gitignore
  - get_gitignore_patterns: ignores comment lines and blank lines
  - get_gitignore_patterns: returns empty list when no .gitignore
  - should_index_file: accepts supported languages
  - should_index_file: rejects unsupported extensions
  - should_index_file: rejects files in _ALWAYS_SKIP dirs
  - should_index_file: rejects files matching gitignore patterns
  - should_index_file: accepts files not matching any pattern
  - should_index_file: handles directory-pattern syntax ("dist/")
"""
from __future__ import annotations

from pathlib import Path

import pytest

from integrations.workspace_detect import (
    find_workspace_root,
    get_gitignore_patterns,
    should_index_file,
)


# ---------------------------------------------------------------------------
# find_workspace_root
# ---------------------------------------------------------------------------

class TestFindWorkspaceRoot:
    def test_finds_git_in_same_dir(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        result = find_workspace_root(str(tmp_path))
        assert result == str(tmp_path)

    def test_finds_git_in_parent_dir(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "module"
        subdir.mkdir(parents=True)
        result = find_workspace_root(str(subdir))
        assert result == str(tmp_path)

    def test_finds_git_in_grandparent(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = find_workspace_root(str(deep))
        assert result == str(tmp_path)

    def test_fallback_when_no_git(self, tmp_path) -> None:
        subdir = tmp_path / "project"
        subdir.mkdir()
        result = find_workspace_root(str(subdir))
        assert result == str(subdir)

    def test_works_with_file_path(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        f = tmp_path / "main.py"
        f.write_text("pass")
        result = find_workspace_root(str(f))
        assert result == str(tmp_path)

    def test_file_in_subdir_with_git_at_root(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "pkg"
        sub.mkdir()
        f = sub / "mod.py"
        f.write_text("pass")
        result = find_workspace_root(str(f))
        assert result == str(tmp_path)


# ---------------------------------------------------------------------------
# get_gitignore_patterns
# ---------------------------------------------------------------------------

class TestGetGitignorePatterns:
    def test_reads_patterns(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n.env\n")
        patterns = get_gitignore_patterns(str(tmp_path))
        assert "*.pyc" in patterns
        assert "__pycache__/" in patterns
        assert ".env" in patterns

    def test_ignores_comment_lines(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("# this is a comment\n*.pyc\n")
        patterns = get_gitignore_patterns(str(tmp_path))
        assert "# this is a comment" not in patterns
        assert "*.pyc" in patterns

    def test_ignores_blank_lines(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("\n*.pyc\n\n*.log\n")
        patterns = get_gitignore_patterns(str(tmp_path))
        assert "" not in patterns
        assert len(patterns) == 2

    def test_no_gitignore_returns_empty(self, tmp_path) -> None:
        patterns = get_gitignore_patterns(str(tmp_path))
        assert patterns == []

    def test_all_comments_returns_empty(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("# only comments here\n# another comment\n")
        patterns = get_gitignore_patterns(str(tmp_path))
        assert patterns == []

    def test_strips_trailing_whitespace(self, tmp_path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc   \n  dist/  \n")
        patterns = get_gitignore_patterns(str(tmp_path))
        assert "*.pyc" in patterns


# ---------------------------------------------------------------------------
# should_index_file
# ---------------------------------------------------------------------------

class TestShouldIndexFile:
    def test_accepts_python_file(self, tmp_path) -> None:
        f = tmp_path / "app.py"
        f.touch()
        assert should_index_file(str(f), []) is True

    def test_accepts_javascript_file(self, tmp_path) -> None:
        f = tmp_path / "app.js"
        f.touch()
        assert should_index_file(str(f), []) is True

    def test_accepts_typescript_file(self, tmp_path) -> None:
        f = tmp_path / "app.ts"
        f.touch()
        assert should_index_file(str(f), []) is True

    def test_rejects_txt_file(self, tmp_path) -> None:
        f = tmp_path / "notes.txt"
        f.touch()
        assert should_index_file(str(f), []) is False

    def test_accepts_markdown_file(self, tmp_path) -> None:
        f = tmp_path / "README.md"
        f.touch()
        assert should_index_file(str(f), []) is True

    def test_rejects_file_in_node_modules(self, tmp_path) -> None:
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        f = nm / "index.js"
        f.touch()
        assert should_index_file(str(f), []) is False

    def test_rejects_file_in_venv(self, tmp_path) -> None:
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        f = venv / "utils.py"
        f.touch()
        assert should_index_file(str(f), []) is False

    def test_rejects_file_in_pycache(self, tmp_path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        f = cache / "mod.py"
        f.touch()
        assert should_index_file(str(f), []) is False

    def test_rejects_file_in_dot_venv(self, tmp_path) -> None:
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        f = venv / "utils.py"
        f.touch()
        assert should_index_file(str(f), []) is False

    def test_rejects_file_matching_glob_pattern(self, tmp_path) -> None:
        f = tmp_path / "secret.py"
        f.touch()
        assert should_index_file(str(f), ["secret.py"]) is False

    def test_rejects_file_matching_extension_glob(self, tmp_path) -> None:
        f = tmp_path / "gen_code.py"
        f.touch()
        assert should_index_file(str(f), ["gen_*.py"]) is False

    def test_accepts_file_not_matching_any_pattern(self, tmp_path) -> None:
        f = tmp_path / "service.py"
        f.touch()
        assert should_index_file(str(f), ["*.log", "dist/", "node_modules/"]) is True

    def test_directory_slash_pattern_excludes_files(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        f = dist / "bundle.py"
        f.touch()
        assert should_index_file(str(f), ["dist/"]) is False

    def test_build_dir_pattern(self, tmp_path) -> None:
        build = tmp_path / "build"
        build.mkdir()
        f = build / "output.py"
        f.touch()
        # build is in _ALWAYS_SKIP
        assert should_index_file(str(f), []) is False

    def test_case_insensitive_extension(self, tmp_path) -> None:
        # Extensions are lowercased before checking
        f = tmp_path / "app.PY"
        f.touch()
        assert should_index_file(str(f), []) is True

    def test_no_extension_file_rejected(self, tmp_path) -> None:
        f = tmp_path / "Makefile"
        f.touch()
        assert should_index_file(str(f), []) is False


# ---------------------------------------------------------------------------
# T19: .vectrignore support
# ---------------------------------------------------------------------------

class TestVectrignore:
    def test_get_vectrignore_dirs_empty_when_no_file(self, tmp_path) -> None:
        from integrations.workspace_detect import get_vectrignore_dirs
        assert get_vectrignore_dirs(str(tmp_path)) == set()

    def test_get_vectrignore_dirs_reads_entries(self, tmp_path) -> None:
        from integrations.workspace_detect import get_vectrignore_dirs
        (tmp_path / ".vectrignore").write_text("vendor\ngenerated\n# comment\n")
        dirs = get_vectrignore_dirs(str(tmp_path))
        assert dirs == {"vendor", "generated"}

    def test_get_vectrignore_dirs_skips_comments(self, tmp_path) -> None:
        from integrations.workspace_detect import get_vectrignore_dirs
        (tmp_path / ".vectrignore").write_text("# this is a comment\nproto-gen\n")
        assert get_vectrignore_dirs(str(tmp_path)) == {"proto-gen"}

    def test_write_vectrignore_creates_file(self, tmp_path) -> None:
        from integrations.workspace_detect import write_vectrignore, get_vectrignore_dirs
        write_vectrignore(str(tmp_path), ["vendor", "generated"])
        dirs = get_vectrignore_dirs(str(tmp_path))
        assert dirs == {"vendor", "generated"}

    def test_write_vectrignore_appends_without_duplicates(self, tmp_path) -> None:
        from integrations.workspace_detect import write_vectrignore, get_vectrignore_dirs
        write_vectrignore(str(tmp_path), ["vendor"])
        write_vectrignore(str(tmp_path), ["vendor", "generated"])  # vendor is duplicate
        dirs = get_vectrignore_dirs(str(tmp_path))
        assert dirs == {"vendor", "generated"}
        content = (tmp_path / ".vectrignore").read_text()
        assert content.count("vendor") == 1

    def test_should_index_file_excludes_vectrignore_dir(self, tmp_path) -> None:
        from integrations.workspace_detect import should_index_file
        vendor_dir = tmp_path / "vendor"
        vendor_dir.mkdir()
        f = vendor_dir / "lib.py"
        f.touch()
        assert should_index_file(str(f), [], extra_excluded_dirs={"vendor"}) is False

    def test_should_index_file_includes_non_excluded_dir(self, tmp_path) -> None:
        from integrations.workspace_detect import should_index_file
        src = tmp_path / "src"
        src.mkdir()
        f = src / "main.py"
        f.touch()
        assert should_index_file(str(f), [], extra_excluded_dirs={"vendor"}) is True

    def test_indexer_skips_vectrignore_dirs(self, tmp_path, monkeypatch) -> None:
        """index_workspace() must skip directories listed in .vectrignore."""
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        from agent.indexer import CodeIndexer

        # Create workspace: src/main.py (should index) + vendor/lib.py (should skip)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def main(): pass\n")
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.py").write_text("def vendor_fn(): pass\n")

        # Write .vectrignore excluding vendor
        (tmp_path / ".vectrignore").write_text("vendor\n")

        idx = CodeIndexer(str(tmp_path), db_path=str(tmp_path / "chroma"))
        idx._embed_provider = _DummyEmbedProvider()
        files, chunks = idx.index_workspace()

        # Only src/main.py should be indexed
        indexed = idx.indexed_file_paths
        assert any("main.py" in p for p in indexed)
        assert not any("vendor" in p for p in indexed), (
            "vendor/ must be excluded via .vectrignore"
        )

    def test_vectrignore_with_existing_gitignore(self, tmp_path) -> None:
        from integrations.workspace_detect import get_gitignore_patterns, get_vectrignore_dirs
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        (tmp_path / ".vectrignore").write_text("vendor\n")
        patterns = get_gitignore_patterns(str(tmp_path))
        dirs = get_vectrignore_dirs(str(tmp_path))
        assert "*.pyc" in patterns
        assert "vendor" in dirs
