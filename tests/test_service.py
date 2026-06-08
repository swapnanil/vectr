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


# ---------------------------------------------------------------------------
# Strategy integration — weights flow through to searcher.search()
# ---------------------------------------------------------------------------

class TestStrategyIntegration:
    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        make_py(tmp_path, "a.py", "def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))
        return svc

    def test_strategy_set_after_workspace_index(self, tmp_path, monkeypatch) -> None:
        from agent.strategy_selector import RetrievalStrategy
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        assert isinstance(svc._strategy, RetrievalStrategy)

    def test_search_passes_strategy_weight_to_searcher(self, tmp_path, monkeypatch) -> None:
        from agent.strategy_selector import RetrievalStrategy
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))

        svc._strategy = RetrievalStrategy(
            semantic_weight=0.85,
            bm25_weight=0.15,
            graph_first=False,
            recommended_embed_model="Snowflake/snowflake-arctic-embed-m-v1.5",
            rationale="test",
        )
        received: dict = {}
        _orig = svc._searcher.search

        def _spy(*args, **kwargs):
            received.update(kwargs)
            return _orig(*args, **kwargs)

        svc._searcher.search = _spy
        svc.search("foo")
        assert received.get("semantic_weight") == 0.85

    def test_search_fallback_weight_when_strategy_none(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        svc._strategy = None

        received: dict = {}
        _orig = svc._searcher.search

        def _spy(*args, **kwargs):
            received.update(kwargs)
            return _orig(*args, **kwargs)

        svc._searcher.search = _spy
        svc.search("foo")
        assert received.get("semantic_weight") == 0.70


# ---------------------------------------------------------------------------
# T14: suggest_instruction_style
# ---------------------------------------------------------------------------

class TestSuggestInstructionStyle:
    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        make_py(tmp_path, "a.py", "def foo(): pass\n")
        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))
        return svc

    def test_default_style_is_additive(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        assert svc.suggest_instruction_style() == "additive"

    def test_style_override_file_wins(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        override_dir = Path(svc._workspace_root) / ".vectr"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / "style").write_text("directed", encoding="utf-8")
        assert svc.suggest_instruction_style() == "directed"

    def test_style_override_memory_only(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        override_dir = Path(svc._workspace_root) / ".vectr"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / "style").write_text("memory-only", encoding="utf-8")
        assert svc.suggest_instruction_style() == "memory-only"

    def test_invalid_override_falls_through_to_logic(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        override_dir = Path(svc._workspace_root) / ".vectr"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / "style").write_text("bogus-value", encoding="utf-8")
        # Invalid override → falls through to heuristic → "additive" (small workspace)
        assert svc.suggest_instruction_style() == "additive"

    def test_large_unfamiliar_codebase_returns_directed(self, tmp_path, monkeypatch) -> None:
        from agent.strategy_selector import RetrievalStrategy, CodebaseFingerprint
        svc = self._make_service(tmp_path, monkeypatch)
        # Simulate a large unfamiliar codebase (no known frameworks)
        svc._strategy = RetrievalStrategy(0.75, 0.25, False, "model", "rationale")

        with patch("agent.strategy_selector.fingerprint") as mock_fp:
            mock_fp.return_value = CodebaseFingerprint(
                total_files=2000,
                language_dist={"python": 2000},
                dominant_language="python",
                is_monorepo=False,
                size_class="large",
                detected_frameworks=[],  # no known frameworks
                complexity_class="complex",
            )
            style = svc.suggest_instruction_style()
        assert style == "directed"

    def test_notes_with_known_framework_returns_memory_only(self, tmp_path, monkeypatch) -> None:
        from agent.strategy_selector import RetrievalStrategy, CodebaseFingerprint
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        # Store a note to simulate prior work
        svc.remember("some note content", tags=["test"])

        svc._strategy = RetrievalStrategy(0.70, 0.30, False, "model", "rationale")
        with patch("agent.strategy_selector.fingerprint") as mock_fp:
            mock_fp.return_value = CodebaseFingerprint(
                total_files=50,
                language_dist={"python": 50},
                dominant_language="python",
                is_monorepo=False,
                size_class="small",
                detected_frameworks=["django"],  # well-known framework
                complexity_class="simple",
            )
            style = svc.suggest_instruction_style()
        assert style == "memory-only"

    def test_count_notes_returns_integer(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        before = svc.count_notes()
        assert isinstance(before, int)
        svc.remember("test note")
        assert svc.count_notes() == before + 1
