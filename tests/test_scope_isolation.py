"""Formal scope-isolation guarantees.

Isolation is per WORKSPACE: each workspace runs its own process on its own port
with its own DB directory, and the server fixes the workspace at startup so no
API request can name another workspace's data. These tests turn "isolated by
design" into "guaranteed and tested":

  1. Cross-store non-leak: two DB directories never see each other's notes.
  2. Isolation by construction: no memory request model carries a `workspace`
     field, so a client cannot ask a daemon to read another workspace.
  3. Cache-dir permissions: the per-workspace cache dir is owner-only (0700) on
     POSIX hosts, so another OS user cannot read the plaintext index/notes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Cross-store non-leak
# ---------------------------------------------------------------------------

class TestCrossStoreIsolation:
    def _store(self, db_dir):
        from agent.working_context_store import WorkingContextStore
        Path(db_dir).mkdir(parents=True, exist_ok=True)
        return WorkingContextStore(str(db_dir))

    def test_separate_db_dirs_do_not_share_notes(self, tmp_path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        store_a = self._store(dir_a)
        store_b = self._store(dir_b)
        store_a.remember("/repo", "workspace A private finding")
        # Same logical workspace string, different DB directory → still isolated,
        # because each daemon only ever opens its own db_dir.
        assert store_b.recall("/repo") == []
        assert len(store_a.recall("/repo")) == 1

    def test_forget_all_in_one_store_leaves_other_intact(self, tmp_path) -> None:
        store_a = self._store(tmp_path / "a")
        store_b = self._store(tmp_path / "b")
        store_a.remember("/repo", "note A")
        store_b.remember("/repo", "note B")
        store_a.forget_all("/repo")
        assert store_a.recall("/repo") == []
        assert len(store_b.recall("/repo")) == 1


# ---------------------------------------------------------------------------
# 2. Isolation by construction — no workspace override on the API surface
# ---------------------------------------------------------------------------

class TestNoWorkspaceOverride:
    """The server keys every memory operation on its own workspace_root. If a
    request model exposed a `workspace` field, a client could read or write
    another workspace's notes through one daemon — so none may."""

    def test_memory_request_models_have_no_workspace_field(self) -> None:
        from app.models import (
            RememberRequest,
            RecallRequest,
            ForgetRequest,
            SnapshotRequest,
        )
        for model in (RememberRequest, RecallRequest, ForgetRequest, SnapshotRequest):
            assert "workspace" not in model.model_fields, (
                f"{model.__name__} must not let a client name a workspace"
            )

    def test_search_request_has_no_workspace_field(self) -> None:
        from app.models import SearchRequest, LocateRequest, TraceRequest
        for model in (SearchRequest, LocateRequest, TraceRequest):
            assert "workspace" not in model.model_fields


# ---------------------------------------------------------------------------
# 3. Cache-directory permissions (POSIX)
# ---------------------------------------------------------------------------

class TestCacheDirPermissions:
    def test_secure_dir_creates_owner_only(self, tmp_path) -> None:
        from agent.fs_permissions import secure_dir
        target = tmp_path / "nested" / "cache"
        secure_dir(target)
        assert target.is_dir()
        if sys.platform != "win32":
            assert (target.stat().st_mode & 0o777) == 0o700

    def test_secure_dir_tightens_existing_dir(self, tmp_path) -> None:
        from agent.fs_permissions import secure_dir
        target = tmp_path / "loose"
        target.mkdir(mode=0o755)
        if sys.platform != "win32":
            # Pre-existing world/group-readable dir gets tightened.
            secure_dir(target)
            assert (target.stat().st_mode & 0o777) == 0o700

    def test_default_db_dir_is_owner_only(self, tmp_path, monkeypatch) -> None:
        # Redirect ~/.cache to a temp home so we never touch the real cache.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from app.service import _default_db_dir
        db_dir = Path(_default_db_dir("/some/workspace"))
        assert db_dir.is_dir()
        if sys.platform != "win32":
            assert (db_dir.stat().st_mode & 0o777) == 0o700
            # The shared parent is tightened too.
            assert ((tmp_path / ".cache" / "vectr").stat().st_mode & 0o777) == 0o700

    def test_secure_dir_never_raises_on_bad_path(self) -> None:
        from agent.fs_permissions import secure_dir
        # A path that cannot be created returns without raising (best-effort).
        result = secure_dir("/proc/should-not-be-creatable/vectr")
        assert isinstance(result, Path)
