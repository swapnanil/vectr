"""
UPG-EMBED-THREAD-CONTENTION — a debounced watcher batch embed with no thread
governance saturates every CPU core, starving the HTTP event loop's
scheduler slice; a live session's first MCP call (vectr_status/vectr_recall)
can then time out 30-60s even though no lock is held.

These tests guard the fix at the smallest level: LocalEmbedProvider must cap
torch's process-wide thread pool (torch.set_num_threads /
set_num_interop_threads) at construction, using the resolved config value —
without downloading a real model or spinning up real torch parallelism.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


class _FakeSTModel:
    def __init__(self, model_name=None, **kwargs):
        self.prompts = {}
        self.max_seq_length = 512


def _construct_provider():
    """Build a real LocalEmbedProvider around a fake sentence-transformers
    model, bypassing any real model download/network access."""
    from agent.indexer._types import LocalEmbedProvider

    with patch("sentence_transformers.SentenceTransformer", _FakeSTModel), \
         patch("agent.model_cache.load_with_offline_preference",
               side_effect=lambda loader, *a, **kw: loader(True)):
        return LocalEmbedProvider("any-model-name")


class TestLocalEmbedProviderThreadCap:
    def test_construction_calls_set_num_threads_with_config_value(self):
        from agent.config import EMBEDDING_THREAD_CAP

        with patch("torch.set_num_threads") as mock_set_threads, \
             patch("torch.set_num_interop_threads"):
            _construct_provider()

        mock_set_threads.assert_called_once_with(EMBEDDING_THREAD_CAP)

    def test_construction_calls_set_num_interop_threads_with_config_value(self):
        from agent.config import EMBEDDING_THREAD_CAP

        with patch("torch.set_num_threads"), \
             patch("torch.set_num_interop_threads") as mock_set_interop:
            _construct_provider()

        mock_set_interop.assert_called_once_with(EMBEDDING_THREAD_CAP)

    def test_repeat_set_num_interop_threads_runtime_error_is_swallowed(self):
        """torch raises RuntimeError if set_num_interop_threads is called a
        second time in the same process (after parallel work has started) —
        e.g. a second LocalEmbedProvider built later in a long-lived daemon
        or across tests in the same process. Construction must still
        succeed; the first call already applied the cap process-wide."""
        with patch("torch.set_num_threads"), \
             patch("torch.set_num_interop_threads", side_effect=RuntimeError("already set")):
            provider = _construct_provider()  # must not raise

        assert provider is not None

    def test_thread_cap_applied_before_model_load(self):
        """Thread governance must be in place before the (potentially
        parallel) model-loading work runs, not after."""
        call_order = []

        def _record_set_threads(n):
            call_order.append("set_num_threads")

        def _record_load(loader, *a, **kw):
            call_order.append("model_load")
            return loader(True)

        with patch("torch.set_num_threads", side_effect=_record_set_threads), \
             patch("torch.set_num_interop_threads"), \
             patch("sentence_transformers.SentenceTransformer", _FakeSTModel), \
             patch("agent.model_cache.load_with_offline_preference", side_effect=_record_load):
            from agent.indexer._types import LocalEmbedProvider
            LocalEmbedProvider("any-model-name")

        assert call_order == ["set_num_threads", "model_load"]


class TestOtherProvidersNoThreadCap:
    """Voyage/OpenAI providers are remote-HTTP embedders with no local torch
    thread pool — construction must never touch torch thread settings."""

    def test_voyage_provider_construction_does_not_touch_torch(self):
        from agent.indexer._types import VoyageEmbedProvider

        # voyageai is an optional dependency not installed in this
        # environment — inject a stand-in module exposing the one attribute
        # VoyageEmbedProvider.__init__ touches (Client), rather than skipping
        # this construction-level assertion entirely.
        fake_voyageai = types.ModuleType("voyageai")
        fake_voyageai.Client = MagicMock()

        with patch("torch.set_num_threads") as mock_set_threads, \
             patch.dict(sys.modules, {"voyageai": fake_voyageai}):
            VoyageEmbedProvider("voyage-code-2")

        mock_set_threads.assert_not_called()

    def test_openai_provider_construction_does_not_touch_torch(self):
        from agent.indexer._types import OpenAIEmbedProvider

        # openai is an optional dependency, same as voyageai above — inject a
        # stand-in module exposing the one attribute OpenAIEmbedProvider.
        # __init__ touches (OpenAI), so this test never needs the real
        # package installed.
        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = MagicMock()

        with patch("torch.set_num_threads") as mock_set_threads, \
             patch.dict(sys.modules, {"openai": fake_openai}):
            OpenAIEmbedProvider("text-embedding-3-small")

        mock_set_threads.assert_not_called()


class TestResolveEmbeddingThreadCap:
    """agent.config._resolve_embedding_thread_cap: the 0-means-auto formula,
    isolated from the module-import-time resolution so both branches
    (explicit override vs. auto-from-cores) are directly testable."""

    def test_positive_configured_value_wins_verbatim(self):
        import agent.config as config

        with patch.object(config, "EMBEDDING_THREAD_CAP_CONFIGURED", 3):
            assert config._resolve_embedding_thread_cap() == 3

    def test_zero_configured_value_derives_from_cpu_count(self):
        import agent.config as config

        with patch.object(config, "EMBEDDING_THREAD_CAP_CONFIGURED", 0), \
             patch.object(config, "EMBEDDING_THREAD_CAP_AUTO_FRACTION", 0.5), \
             patch("os.cpu_count", return_value=8):
            assert config._resolve_embedding_thread_cap() == 4

    def test_auto_resolution_never_goes_below_one(self):
        import agent.config as config

        with patch.object(config, "EMBEDDING_THREAD_CAP_CONFIGURED", 0), \
             patch.object(config, "EMBEDDING_THREAD_CAP_AUTO_FRACTION", 0.5), \
             patch("os.cpu_count", return_value=1):
            assert config._resolve_embedding_thread_cap() == 1

    def test_auto_resolution_handles_unknown_cpu_count(self):
        """os.cpu_count() can return None (some sandboxed/containerized
        environments) — must not raise or produce a 0/negative cap."""
        import agent.config as config

        with patch.object(config, "EMBEDDING_THREAD_CAP_CONFIGURED", 0), \
             patch.object(config, "EMBEDDING_THREAD_CAP_AUTO_FRACTION", 0.5), \
             patch("os.cpu_count", return_value=None):
            assert config._resolve_embedding_thread_cap() >= 1

    def test_module_level_constant_is_a_positive_int(self):
        """The resolved, import-time constant every real call site uses."""
        from agent.config import EMBEDDING_THREAD_CAP

        assert isinstance(EMBEDDING_THREAD_CAP, int)
        assert EMBEDDING_THREAD_CAP >= 1
