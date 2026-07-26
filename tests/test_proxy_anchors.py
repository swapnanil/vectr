"""Tests for agent/proxy_anchors.py — versioned proxy-anchor manifest +
deterministic glob-presence suggestion (no content inspection whatsoever).
"""
from __future__ import annotations

from agent.proxy_anchors import (
    PROXY_ANCHOR_MANIFEST_VERSION,
    load_manifest,
    suggest_proxy_anchors,
)


class TestManifest:
    def test_version_is_one(self) -> None:
        assert PROXY_ANCHOR_MANIFEST_VERSION == 1

    def test_domain_ids_are_unique(self) -> None:
        manifest = load_manifest()
        ids = [domain_id for domain_id, _globs in manifest]
        assert len(ids) == len(set(ids))
        assert len(ids) > 0

    def test_every_glob_is_a_nonempty_string(self) -> None:
        for _domain_id, globs in load_manifest():
            assert len(globs) > 0
            for g in globs:
                assert isinstance(g, str)
                assert g.strip() != ""

    def test_no_absolute_paths_in_globs(self) -> None:
        for domain_id, globs in load_manifest():
            for g in globs:
                assert not g.startswith("/"), f"{domain_id}: glob {g!r} looks absolute"

    def test_manifest_is_cached(self) -> None:
        assert load_manifest() is load_manifest()


class TestSuggestProxyAnchors:
    def test_finds_lockfile_ci_and_dockerfile_in_manifest_order(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n")
        (tmp_path / "Dockerfile").write_text("FROM python:3\n")

        result = suggest_proxy_anchors(tmp_path, limit=10)

        # python.dependencies precedes ci.pipeline precedes container.build
        # in the manifest, so this exact order is required, not incidental.
        assert result == ["pyproject.toml", ".github/workflows/ci.yml", "Dockerfile"]

    def test_result_is_capped_at_limit(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "poetry.lock").write_text("")
        (tmp_path / "Dockerfile").write_text("FROM python:3\n")

        result = suggest_proxy_anchors(tmp_path, limit=1)
        assert result == ["pyproject.toml"]

    def test_ignores_files_not_in_the_manifest(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hello\n")
        (tmp_path / "main.py").write_text("print('hi')\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_empty_workspace_returns_empty_list(self, tmp_path) -> None:
        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_limit_zero_or_negative_returns_empty_list(self, tmp_path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert suggest_proxy_anchors(tmp_path, limit=0) == []
        assert suggest_proxy_anchors(tmp_path, limit=-1) == []

    def test_nonexistent_workspace_root_returns_empty_list_not_exception(self) -> None:
        assert suggest_proxy_anchors("/no/such/workspace/dir/at/all", limit=10) == []

    def test_directories_are_never_matched_only_regular_files(self, tmp_path) -> None:
        # A directory literally named "Cargo.toml" must never be reported —
        # only a regular file matching the glob counts.
        (tmp_path / "Cargo.toml").mkdir()
        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_never_returns_anything_under_git(self, tmp_path) -> None:
        # container.build's globs are recursive ("**/Dockerfile"), which
        # (absent the explicit .git skip) would otherwise reach a file
        # nested inside .git the same way it reaches a nested service
        # directory — this proves the skip is load-bearing, not dead code.
        git_nested = tmp_path / ".git" / "hooks"
        git_nested.mkdir(parents=True)
        (git_nested / "Dockerfile").write_text("not a real dockerfile\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []

        # A real, non-.git Dockerfile alongside it is still found.
        (tmp_path / "Dockerfile").write_text("FROM python:3\n")
        assert suggest_proxy_anchors(tmp_path, limit=10) == ["Dockerfile"]

    def test_deterministic_across_repeated_calls(self, tmp_path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "go.mod").write_text("module x\n")
        services = tmp_path / "services" / "api"
        services.mkdir(parents=True)
        (services / "Dockerfile").write_text("FROM alpine\n")

        first = suggest_proxy_anchors(tmp_path, limit=10)
        for _ in range(5):
            assert suggest_proxy_anchors(tmp_path, limit=10) == first

    def test_recursive_glob_domain_returns_workspace_relative_posix_paths(self, tmp_path) -> None:
        nested = tmp_path / "services" / "api"
        nested.mkdir(parents=True)
        (nested / "Dockerfile").write_text("FROM alpine\n")

        result = suggest_proxy_anchors(tmp_path, limit=10)
        assert result == ["services/api/Dockerfile"]
        assert "\\" not in result[0]
