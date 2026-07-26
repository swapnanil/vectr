"""Tests for agent/proxy_anchors.py — versioned proxy-anchor manifest +
deterministic glob-presence suggestion (no content inspection whatsoever).
"""
from __future__ import annotations

import os

import yaml

import agent.proxy_anchors as _proxy_anchors
from agent.proxy_anchors import (
    PROXY_ANCHOR_MANIFEST_VERSION,
    _MANIFEST_PATH,
    _MAX_DIRS_SCANNED,
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


class TestRecursiveWalkPruning:
    """A recursive glob's Dockerfile/Makefile match must come from THIS
    workspace's own tree, never from a dependency/build-output subtree —
    node_modules/, .venv/, target/, etc. encode a dependency's process, not
    this workspace's (see agent/proxy_anchors.py's `_PRUNED_DIR_NAMES`)."""

    def test_dockerfile_in_node_modules_is_not_suggested(self, tmp_path) -> None:
        vendored = tmp_path / "node_modules" / "some-pkg"
        vendored.mkdir(parents=True)
        (vendored / "Dockerfile").write_text("FROM alpine\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_dockerfile_at_root_is_still_suggested(self, tmp_path) -> None:
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "Dockerfile").write_text("FROM alpine\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == ["Dockerfile"]

    def test_makefile_in_venv_is_not_suggested(self, tmp_path) -> None:
        vendored = tmp_path / ".venv" / "lib"
        vendored.mkdir(parents=True)
        (vendored / "Makefile").write_text("all:\n\techo hi\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_makefile_in_target_is_not_suggested(self, tmp_path) -> None:
        vendored = tmp_path / "target" / "release"
        vendored.mkdir(parents=True)
        (vendored / "Makefile").write_text("all:\n\techo hi\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []

    def test_dockerfile_and_makefile_together_only_root_and_service_survive(self, tmp_path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        service = tmp_path / "services" / "api"
        service.mkdir(parents=True)
        (service / "Dockerfile").write_text("FROM alpine\n")
        vendored = tmp_path / "node_modules" / "some-pkg"
        vendored.mkdir(parents=True)
        (vendored / "Dockerfile").write_text("FROM alpine\n")
        (vendored / "Makefile").write_text("all:\n\techo hi\n")

        result = suggest_proxy_anchors(tmp_path, limit=10)
        assert result == ["services/api/Dockerfile", "Makefile"]


class TestRecursiveWalkDepth:
    """Nothing several directory levels deep is this workspace's own build
    ritual — it is either generated output or a dependency's own tree
    that happened not to match a pruned name. `_MAX_RECURSIVE_DEPTH` bounds
    this independent of directory naming."""

    def test_dockerfile_at_depth_2_is_suggested(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        (nested / "Dockerfile").write_text("FROM alpine\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == ["a/b/Dockerfile"]

    def test_dockerfile_at_depth_6_is_not_suggested(self, tmp_path) -> None:
        nested = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
        nested.mkdir(parents=True)
        (nested / "Dockerfile").write_text("FROM alpine\n")

        assert suggest_proxy_anchors(tmp_path, limit=10) == []


class TestRecursiveWalkBudget:
    def test_max_dirs_scanned_budget_is_respected(self, tmp_path, monkeypatch) -> None:
        # A wide, flat tree with no matches anywhere below the root: the
        # loop can never reach `limit`, so every recursive domain must be
        # evaluated — the exact worst case the walk budget guards against.
        for i in range(5000):
            (tmp_path / f"d{i}").mkdir()
        (tmp_path / "package.json").write_text("{}")

        real_scandir = os.scandir
        calls = {"count": 0}

        def counting_scandir(path):
            calls["count"] += 1
            return real_scandir(path)

        monkeypatch.setattr(_proxy_anchors.os, "scandir", counting_scandir)

        suggest_proxy_anchors(tmp_path, limit=100)

        # A handful of root-scoped *wildcard* manifest globs (e.g.
        # "requirements-*.txt", "*.gemspec") also call os.scandir once
        # each via pathlib itself before a recursive domain is ever
        # reached — irrelevant to what this test guards, so the bound
        # allows generous headroom for that constant, tree-size-independent
        # overhead while still proving the walk never approaches the
        # tree's real size (5001 directories).
        assert calls["count"] <= _MAX_DIRS_SCANNED + 50

    def test_recursive_candidates_are_walked_once_per_call_not_per_glob(self, tmp_path, monkeypatch) -> None:
        # container.build + build.task together declare 14 recursive
        # globs; a shared walk means the tree is walked once regardless.
        (tmp_path / "package.json").write_text("{}")

        real_collect = _proxy_anchors._collect_recursive_candidates
        calls = {"count": 0}

        def counting_collect(root):
            calls["count"] += 1
            return real_collect(root)

        monkeypatch.setattr(_proxy_anchors, "_collect_recursive_candidates", counting_collect)

        suggest_proxy_anchors(tmp_path, limit=100)

        # package.json alone can never satisfy limit=100, so every one of
        # the manifest's 14 recursive globs (container.build + build.task)
        # is reached — a per-glob walk would call this 14 times, a shared
        # one exactly once.
        assert calls["count"] == 1


class TestRecursiveWalkRobustness:
    def test_permission_denied_subdirectory_does_not_raise_or_lose_root_matches(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "Dockerfile").write_text("FROM alpine\n")
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        (blocked / "Dockerfile").write_text("FROM alpine\n")

        real_scandir = os.scandir

        def failing_scandir(path):
            if os.path.basename(os.fspath(path)) == "blocked":
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(_proxy_anchors.os, "scandir", failing_scandir)

        result = suggest_proxy_anchors(tmp_path, limit=10)

        assert result == ["Dockerfile"]

    def test_deterministic_across_repeated_calls_with_recursive_matches(self, tmp_path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        for i in range(20):
            svc = tmp_path / "services" / f"svc{i}"
            svc.mkdir(parents=True)
            (svc / "Dockerfile").write_text("FROM alpine\n")

        first = suggest_proxy_anchors(tmp_path, limit=10)
        for _ in range(5):
            assert suggest_proxy_anchors(tmp_path, limit=10) == first


class TestManifestVersion:
    def test_manifest_version_matches_yaml_version_field(self) -> None:
        raw = _MANIFEST_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        assert PROXY_ANCHOR_MANIFEST_VERSION == int(data["version"])
