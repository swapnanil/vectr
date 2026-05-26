"""
Tests for VectrService business logic.

Covers threading, concurrency, and lifecycle behaviour that mocked-service
tests in test_api.py cannot catch.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import make_py


# ---------------------------------------------------------------------------
# Concurrent indexing — background auto-index vs explicit POST
# ---------------------------------------------------------------------------

class TestConcurrentIndexing:
    def test_explicit_index_waits_for_background(self, tmp_path, monkeypatch) -> None:
        """
        VectrService.index() must not race with the background auto-index thread.

        Regression test for: chromadb.errors.DuplicateIDError when an explicit
        /v1/index POST arrives while startup auto-indexing is still running.
        """
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())

        make_py(tmp_path, "a.py", "def foo(): pass\n")
        make_py(tmp_path, "b.py", "def bar(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))

        # Start background index and immediately fire an explicit index call
        # from a separate thread — the lock must prevent concurrent execution.
        errors: list[Exception] = []

        def bg():
            try:
                svc.start_background_index()
            except Exception as e:
                errors.append(e)

        def explicit():
            try:
                time.sleep(0.05)  # slight delay so bg starts first
                svc.index(str(tmp_path))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=bg)
        t2 = threading.Thread(target=explicit)
        t1.start(); t2.start()
        t1.join(timeout=30); t2.join(timeout=30)

        assert errors == [], f"Concurrent index raised: {errors}"
        assert svc.total_chunks > 0

    def test_background_index_sets_indexing_flag(self, tmp_path, monkeypatch) -> None:
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        make_py(tmp_path, "x.py", "def x(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))

        assert svc._indexing is False
        # Second call to start_background_index while already indexing is a no-op
        svc._indexing = True
        svc.start_background_index()  # should return early, not start a thread
        svc._indexing = False
