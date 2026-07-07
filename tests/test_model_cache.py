"""Tests for agent/model_cache.py (UPG-RERANKER-HF-NETWORK).

Covers the offline-when-cached loading helper shared by the embedder
(agent/indexer/_types.py:LocalEmbedProvider) and the reranker
(agent/searcher.py:_Reranker). No real network access or model download —
`huggingface_hub.try_to_load_from_cache` and the model constructor are both
mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.model_cache import is_model_cached, load_with_offline_preference, suppress_model_load_noise


# ---------------------------------------------------------------------------
# is_model_cached
# ---------------------------------------------------------------------------

class TestIsModelCached:
    def test_cache_hit_returns_true(self) -> None:
        with patch(
            "huggingface_hub.try_to_load_from_cache",
            return_value="/fake/cache/models--org--name/snapshots/abc/config.json",
        ):
            assert is_model_cached("org/name", "/fake/cache") is True

    def test_cache_miss_returns_false(self) -> None:
        with patch("huggingface_hub.try_to_load_from_cache", return_value=None):
            assert is_model_cached("org/name", "/fake/cache") is False

    def test_no_exist_sentinel_returns_false(self) -> None:
        """huggingface_hub returns a private `_CACHED_NO_EXIST` sentinel object
        (not a str) when it has cached the *absence* of a file — that must
        still read as "not cached", never crash the isinstance check."""
        class _NoExistSentinel:
            pass

        with patch("huggingface_hub.try_to_load_from_cache", return_value=_NoExistSentinel()):
            assert is_model_cached("org/name", "/fake/cache") is False

    def test_lookup_raising_returns_false(self) -> None:
        """A broken/unreadable cache dir must degrade to 'not cached', not raise."""
        with patch("huggingface_hub.try_to_load_from_cache", side_effect=OSError("boom")):
            assert is_model_cached("org/name", "/fake/cache") is False


# ---------------------------------------------------------------------------
# load_with_offline_preference
# ---------------------------------------------------------------------------

class TestLoadWithOfflinePreference:
    def test_cached_model_loads_with_local_files_only_true_no_network_path(self) -> None:
        """Cached case: build_fn must be invoked exactly once, with
        local_files_only=True — the offline path — and never touch a
        network-enabled build."""
        calls: list[bool] = []

        def build_fn(local_only: bool) -> str:
            calls.append(local_only)
            return "model-instance"

        with patch("agent.model_cache.is_model_cached", return_value=True):
            result = load_with_offline_preference(build_fn, "org/name", "/fake/cache")

        assert result == "model-instance"
        assert calls == [True]

    def test_uncached_model_falls_back_to_network_enabled_load(self) -> None:
        """Uncached case: build_fn must be invoked with local_files_only=False —
        a normal network-enabled download, preserving first-run UX."""
        calls: list[bool] = []

        def build_fn(local_only: bool) -> str:
            calls.append(local_only)
            return "model-instance"

        with patch("agent.model_cache.is_model_cached", return_value=False):
            result = load_with_offline_preference(build_fn, "org/name", "/fake/cache")

        assert result == "model-instance"
        assert calls == [False]

    def test_offline_load_raising_falls_back_to_network(self) -> None:
        """An incomplete/corrupted cache entry: is_model_cached says True but the
        offline attempt itself raises — must retry with local_files_only=False
        rather than propagating the exception."""
        calls: list[bool] = []

        def build_fn(local_only: bool) -> str:
            calls.append(local_only)
            if local_only:
                raise OSError("incomplete snapshot")
            return "model-instance"

        with patch("agent.model_cache.is_model_cached", return_value=True):
            result = load_with_offline_preference(build_fn, "org/name", "/fake/cache")

        assert result == "model-instance"
        assert calls == [True, False]

    def test_genuine_failure_on_network_path_propagates(self) -> None:
        """If the network-enabled build itself fails (e.g. no network AND no
        cache), the exception must propagate to the caller unchanged — callers
        (LocalEmbedProvider, _Reranker._load) are responsible for their own
        graceful-degradation semantics."""
        def build_fn(local_only: bool) -> str:
            raise RuntimeError("no network, no cache")

        with patch("agent.model_cache.is_model_cached", return_value=False):
            with pytest.raises(RuntimeError):
                load_with_offline_preference(build_fn, "org/name", "/fake/cache")


# ---------------------------------------------------------------------------
# LocalEmbedProvider.__init__ — embedder load path parity with the reranker
# (UPG-RERANKER-HF-NETWORK: the startup-time embedder load made the same live
# huggingface.co calls the reranker did; both must go through the same
# offline-when-cached helper).
# ---------------------------------------------------------------------------

class TestLocalEmbedProviderOfflineLoading:
    def test_cached_model_loads_with_local_files_only(self, tmp_path) -> None:
        from agent.indexer._types import LocalEmbedProvider

        calls: list[dict] = []

        class _FakeSTModel:
            def __init__(self, model_name, **kwargs):
                calls.append(kwargs)
                self.prompts = {}

        with patch("agent.model_cache.is_model_cached", return_value=True), \
             patch("sentence_transformers.SentenceTransformer", _FakeSTModel), \
             patch("pathlib.Path.home", return_value=tmp_path):
            LocalEmbedProvider("org/name")

        assert len(calls) == 1
        assert calls[0]["local_files_only"] is True

    def test_uncached_model_falls_back_to_network_enabled_load(self, tmp_path) -> None:
        from agent.indexer._types import LocalEmbedProvider

        calls: list[dict] = []

        class _FakeSTModel:
            def __init__(self, model_name, **kwargs):
                calls.append(kwargs)
                self.prompts = {}

        with patch("agent.model_cache.is_model_cached", return_value=False), \
             patch("sentence_transformers.SentenceTransformer", _FakeSTModel), \
             patch("pathlib.Path.home", return_value=tmp_path):
            LocalEmbedProvider("org/name")

        assert len(calls) == 1
        assert calls[0]["local_files_only"] is False


# ---------------------------------------------------------------------------
# suppress_model_load_noise (UPG-CLI-SMALL-UX): tqdm/HF-logging suppression
# shared by both model-load call sites (embedder, reranker).
# ---------------------------------------------------------------------------

class TestSuppressModelLoadNoise:
    def test_calls_disable_progress_bars_and_set_verbosity_error(self) -> None:
        mock_disable = MagicMock()
        mock_set_verbosity = MagicMock()
        with patch("huggingface_hub.utils.disable_progress_bars", mock_disable), \
             patch("transformers.utils.logging.set_verbosity_error", mock_set_verbosity):
            suppress_model_load_noise()
        mock_disable.assert_called_once()
        mock_set_verbosity.assert_called_once()

    def test_never_raises_if_disable_progress_bars_missing(self) -> None:
        with patch("huggingface_hub.utils.disable_progress_bars", side_effect=ImportError("boom")):
            suppress_model_load_noise()  # must not raise

    def test_never_raises_if_set_verbosity_error_missing(self) -> None:
        with patch("transformers.utils.logging.set_verbosity_error", side_effect=ImportError("boom")):
            suppress_model_load_noise()  # must not raise

    def test_embedder_load_calls_noise_suppression(self, tmp_path) -> None:
        from agent.indexer._types import LocalEmbedProvider

        class _FakeSTModel:
            def __init__(self, model_name, **kwargs):
                self.prompts = {}

        mock_suppress = MagicMock()
        with patch("agent.model_cache.is_model_cached", return_value=True), \
             patch("sentence_transformers.SentenceTransformer", _FakeSTModel), \
             patch("pathlib.Path.home", return_value=tmp_path), \
             patch("agent.model_cache.suppress_model_load_noise", mock_suppress):
            LocalEmbedProvider("org/name")
        mock_suppress.assert_called_once()

    def test_reranker_load_calls_noise_suppression(self, tmp_path) -> None:
        from agent.searcher import _Reranker

        class _FakeCrossEncoder:
            def __init__(self, model_name, **kwargs):
                pass

        mock_suppress = MagicMock()
        with patch("agent.model_cache.is_model_cached", return_value=True), \
             patch("sentence_transformers.CrossEncoder", _FakeCrossEncoder), \
             patch("pathlib.Path.home", return_value=tmp_path), \
             patch("agent.model_cache.suppress_model_load_noise", mock_suppress):
            reranker = _Reranker("org/name")
            reranker._load()
        mock_suppress.assert_called_once()
