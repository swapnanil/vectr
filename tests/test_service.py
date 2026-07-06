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
# Eager reranker warm-up at startup (UPG-RERANKER-HF-NETWORK)
#
# Moves the cross-encoder's model-load cost out of the first vectr_search
# call; must run in full/search-only mode but be skipped in memory-only mode,
# where there is no code index and search is disabled.
# ---------------------------------------------------------------------------

class TestEagerRerankerWarmup:
    def test_warm_reranker_called_in_full_mode(self, tmp_path, monkeypatch) -> None:
        from agent import indexer as idx_module
        from agent.searcher import CodeSearcher
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        make_py(tmp_path, "a.py", "def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}), \
             patch.object(CodeSearcher, "warm_reranker", autospec=True) as warm_mock:
            from app.service import VectrService
            VectrService(workspace_root=str(tmp_path))

        warm_mock.assert_called_once()

    def test_warm_reranker_skipped_in_memory_only_mode(self, tmp_path, monkeypatch) -> None:
        from agent import indexer as idx_module
        from agent.searcher import CodeSearcher
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}), \
             patch.object(CodeSearcher, "warm_reranker", autospec=True) as warm_mock:
            from app.service import VectrService
            VectrService(workspace_root=str(tmp_path), memory_only=True)

        warm_mock.assert_not_called()


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
# UPG-8.2: status output is deterministic — retrieval weights + strategy
# fields are always present (not conditional on a strategy having been
# computed yet), and last_indexed agrees between /v1/status and /v1/health.
# ---------------------------------------------------------------------------

class TestStatusDeterminismUPG82:
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

    def test_status_includes_strategy_fields_before_any_index(self, tmp_path, monkeypatch) -> None:
        """Before the first index() call, self._strategy is None — status()
        must still populate the retrieval-weight fields from config defaults
        rather than omitting them."""
        from agent.config import STRATEGY_DEFAULT_BM25_WEIGHT, STRATEGY_DEFAULT_SEMANTIC_WEIGHT
        svc = self._make_service(tmp_path, monkeypatch)
        assert svc._strategy is None
        data = svc.status()
        assert data["semantic_weight"] == STRATEGY_DEFAULT_SEMANTIC_WEIGHT
        assert data["bm25_weight"] == STRATEGY_DEFAULT_BM25_WEIGHT
        assert data["graph_first"] is False
        assert data["strategy_rationale"]

    def test_status_includes_strategy_fields_after_index(self, tmp_path, monkeypatch) -> None:
        """Once a fingerprint-derived strategy exists, status() reports its
        real values (same fields, same shape, different source)."""
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        data = svc.status()
        assert isinstance(data["semantic_weight"], float)
        assert isinstance(data["bm25_weight"], float)
        assert data["strategy_rationale"]

    def test_health_and_status_agree_on_last_indexed(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        svc.index(str(tmp_path))
        assert svc.last_indexed == svc.status()["last_indexed"]
        assert svc.last_indexed != "never"


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


# ---------------------------------------------------------------------------
# UPG-QUERYTYPE-REROUTE: additive identifier-shape symbol-graph hint —
# replaces the deleted agent/query_router.py regex query-classification
# layer. `identifier_hint_symbols()` must never affect `search()`'s retrieval
# weight, must resolve only EXACT identifier-shaped tokens, and must respect
# the configured max_identifiers/max_locations caps.
# ---------------------------------------------------------------------------

class TestIdentifierHintSymbols:
    def _make_service(self, tmp_path, monkeypatch, files: dict[str, str]):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        for name, content in files.items():
            make_py(tmp_path, name, content)

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))
        svc.index(str(tmp_path))
        return svc

    def test_plain_english_query_returns_no_hint_symbols(self, tmp_path, monkeypatch) -> None:
        """An NL question containing bare nouns that used to misroute
        (dependency/override/subclass) resolves nothing — no identifier-
        shaped token is even attempted against the symbol graph."""
        svc = self._make_service(tmp_path, monkeypatch, {
            "a.py": "def dependency(): pass\ndef override(): pass\n",
        })
        assert svc.identifier_hint_symbols(
            "what are the dependencies here and can I override this"
        ) == []

    def test_semantic_weight_unaffected_by_query_content(self, tmp_path, monkeypatch) -> None:
        """A query containing former router trigger-words must retrieve at
        the fingerprint-derived semantic weight — no per-query override."""
        from agent.strategy_selector import RetrievalStrategy
        svc = self._make_service(tmp_path, monkeypatch, {"a.py": "def foo(): pass\n"})
        svc._strategy = RetrievalStrategy(
            semantic_weight=0.85, bm25_weight=0.15, graph_first=False,
            recommended_embed_model="Snowflake/snowflake-arctic-embed-m-v1.5",
            rationale="test",
        )
        received: dict = {}
        _orig = svc._searcher.search

        def _spy(*args, **kwargs):
            received.update(kwargs)
            return _orig(*args, **kwargs)

        svc._searcher.search = _spy
        svc.search("what is the dependency graph, can I override or subclass this")
        assert received.get("semantic_weight") == 0.85

    def test_camelcase_identifier_resolves_exactly(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch, {
            "resolver.py": "class WorkspaceLock:\n    def acquire(self):\n        pass\n",
        })
        symbols = svc.identifier_hint_symbols("how does WorkspaceLock work")
        assert any(s.name == "WorkspaceLock" for s in symbols)

    def test_snake_case_identifier_resolves_exactly(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch, {
            "lock.py": "def acquire_lock():\n    pass\n",
        })
        symbols = svc.identifier_hint_symbols("what does acquire_lock do")
        assert any(s.name == "acquire_lock" for s in symbols)

    def test_plain_word_matching_a_real_symbol_name_yields_no_hint(self, tmp_path, monkeypatch) -> None:
        """A plain lowercase word is never identifier-SHAPED, even when a
        real symbol of that exact name exists — shape gates the attempt,
        not just resolution success."""
        svc = self._make_service(tmp_path, monkeypatch, {
            "t.py": "def resolve():\n    pass\ndef timeout():\n    pass\n",
        })
        assert svc.identifier_hint_symbols("please resolve the timeout quickly") == []

    def test_max_identifiers_caps_number_of_tokens_attempted(self, tmp_path, monkeypatch) -> None:
        from agent import config as config_module
        monkeypatch.setattr(config_module, "SEARCH_IDENTIFIER_HINT_MAX_IDENTIFIERS", 2)
        import app.service as service_module
        monkeypatch.setattr(service_module, "SEARCH_IDENTIFIER_HINT_MAX_IDENTIFIERS", 2)

        svc = self._make_service(tmp_path, monkeypatch, {
            "a.py": (
                "def AlphaLock():\n    pass\n"
                "def beta_lock():\n    pass\n"
                "def GammaLock():\n    pass\n"
            ),
        })
        symbols = svc.identifier_hint_symbols("compare AlphaLock, beta_lock and GammaLock")
        names = {s.name for s in symbols}
        # Only the first 2 identifier-shaped tokens (query order) are attempted —
        # GammaLock is the 3rd and must never be resolved.
        assert "GammaLock" not in names
        assert names == {"AlphaLock", "beta_lock"}

    def test_max_locations_caps_resolved_symbols_per_identifier(self, tmp_path, monkeypatch) -> None:
        from agent import config as config_module
        monkeypatch.setattr(config_module, "SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS", 3)
        import app.service as service_module
        monkeypatch.setattr(service_module, "SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS", 3)

        svc = self._make_service(tmp_path, monkeypatch, {
            f"mod_{i}.py": "def duplicate_name():\n    pass\n" for i in range(5)
        })
        symbols = svc.identifier_hint_symbols("what does duplicate_name do")
        assert len(symbols) <= 3

    def test_disabled_via_config_returns_empty(self, tmp_path, monkeypatch) -> None:
        from agent import config as config_module
        monkeypatch.setattr(config_module, "SEARCH_IDENTIFIER_HINT_ENABLED", False)
        import app.service as service_module
        monkeypatch.setattr(service_module, "SEARCH_IDENTIFIER_HINT_ENABLED", False)

        svc = self._make_service(tmp_path, monkeypatch, {
            "resolver.py": "class WorkspaceLock:\n    pass\n",
        })
        assert svc.identifier_hint_symbols("how does WorkspaceLock work") == []


# ---------------------------------------------------------------------------
# UPG-NEARMISS-SYMBOL-NAMES: additive, honestly-labeled near-miss names for an
# identifier-shaped token that fails EXACT symbol-graph resolution. Sourced
# entirely from the symbol graph's own deterministic partial-match machinery
# — never a query-content guess, never presented as an exact match.
# ---------------------------------------------------------------------------

class TestIdentifierHintNearMiss:
    def _make_service(self, tmp_path, monkeypatch, files: dict[str, str]):
        return TestIdentifierHintSymbols()._make_service(tmp_path, monkeypatch, files)

    def test_close_name_returns_nearmiss_pair(self, tmp_path, monkeypatch) -> None:
        """A one-token misremembering of a real symbol name (an extra
        trailing word appended) surfaces the real, shorter symbol name as an
        honestly-labeled near-miss, keyed by the failed token."""
        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\n",
        })
        pairs = svc.identifier_hint_nearmiss("what does CacheControlHeader do")
        assert len(pairs) == 1
        token, syms = pairs[0]
        assert token == "CacheControlHeader"
        assert any(s.name == "CacheControl" for s in syms)

    def test_garbage_token_with_no_near_neighbors_yields_nothing(self, tmp_path, monkeypatch) -> None:
        """A token that shares no real symbol as a prefix, substring, or
        close edit-distance match yields no near-miss pair at all."""
        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\n",
        })
        assert svc.identifier_hint_nearmiss("what about XyzzyQwerty here") == []

    def test_exactly_resolved_token_has_no_nearmiss_entry(self, tmp_path, monkeypatch) -> None:
        """A token that resolves EXACTLY must never also appear in the
        near-miss list — near-miss is only for tokens that failed exactly."""
        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\n",
        })
        pairs = svc.identifier_hint_nearmiss("look at CacheControl now")
        assert pairs == []

    def test_no_identifier_shaped_token_yields_nothing(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\n",
        })
        assert svc.identifier_hint_nearmiss("what does this class do overall") == []

    def test_nearmiss_disabled_via_config_returns_empty(self, tmp_path, monkeypatch) -> None:
        from agent import config as config_module
        monkeypatch.setattr(config_module, "SEARCH_IDENTIFIER_HINT_NEARMISS_ENABLED", False)
        import app.service as service_module
        monkeypatch.setattr(service_module, "SEARCH_IDENTIFIER_HINT_NEARMISS_ENABLED", False)

        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\n",
        })
        assert svc.identifier_hint_nearmiss("what does CacheControlHeader do") == []

    def test_nearmiss_max_caps_total_names_across_tokens(self, tmp_path, monkeypatch) -> None:
        """The cap is a TOTAL budget across the whole response, not
        per-token — two failing tokens share one small cap."""
        from agent import config as config_module
        monkeypatch.setattr(config_module, "SEARCH_IDENTIFIER_HINT_NEARMISS_MAX", 1)
        import app.service as service_module
        monkeypatch.setattr(service_module, "SEARCH_IDENTIFIER_HINT_NEARMISS_MAX", 1)

        svc = self._make_service(tmp_path, monkeypatch, {
            "control.py": "class CacheControl:\n    pass\nclass AlphaHelperThing:\n    pass\n",
        })
        pairs = svc.identifier_hint_nearmiss(
            "compare CacheControlHeader to AlphaHelperThingExtra"
        )
        total_names = sum(len(syms) for _token, syms in pairs)
        assert total_names <= 1


# ---------------------------------------------------------------------------
# UPG-WS-ROOT-MISDETECT: `vectr start <path>` on a .git-less subdirectory of
# a git repo must index the path AS GIVEN, never silently substitute the
# enclosing repo root. workspace_explicit=True is set by main.py only when
# the CLI resolved an explicit path (positional arg or --path flag); when it
# is False (the default — no path given, cwd default) the pre-existing
# git-toplevel walk-up behavior is unchanged.
# ---------------------------------------------------------------------------

class TestWorkspaceExplicitResolution:
    def test_explicit_path_wins_over_enclosing_git_repo(self, tmp_path, monkeypatch) -> None:
        """The audited bug: a repo-less nested dir must be indexed as given,
        not the enclosing repo it happens to sit inside."""
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "sub" / "project"
        nested.mkdir(parents=True)
        make_py(nested, "a.py", "def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db_nested")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(nested), workspace_explicit=True)

        assert svc._workspace_root == str(nested.resolve())

    def test_default_no_path_keeps_git_toplevel_behavior(self, tmp_path, monkeypatch) -> None:
        """workspace_explicit defaults False (bare `vectr start`, cwd
        default) — the pre-existing git-toplevel walk-up must be unchanged."""
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "sub" / "project"
        nested.mkdir(parents=True)
        make_py(nested, "a.py", "def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db_default")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(nested))  # workspace_explicit not passed

        assert svc._workspace_root == str(tmp_path.resolve())
