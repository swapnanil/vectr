"""
UPG-ARCTIC-QUERY-PREFIX — asymmetric embedding models require queries to be
embedded with a different (model-registered) prompt than documents/chunks.

The default embedder (Snowflake/snowflake-arctic-embed-m-v1.5) is asymmetric:
sentence-transformers exposes this via `model.prompts = {"query": "...", ...}`.
Embedding a search query the same way as a document silently drops that prompt
and measurably tanks dense retrieval (see the offline spike referenced in the
task). These tests guard the fix at the smallest level: a fake sentence-
transformers model standing in for both an asymmetric model (registered
"query" prompt) and a symmetric one (no such prompt), asserting queries and
documents take different/identical code paths respectively — without any
network access or real model download.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.indexer._types import LocalEmbedProvider


class _FakeSTModel:
    """Stand-in for sentence_transformers.SentenceTransformer.

    Records every `encode(...)` call's `prompt_name` kwarg so tests can assert
    exactly when a prompt was (or wasn't) requested — mirrors the real
    SentenceTransformer.prompts contract without downloading a model.
    """

    def __init__(self, prompts: dict[str, str]):
        self.prompts = prompts
        self.calls: list[dict] = []

    def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True,
               show_progress_bar=False, prompt_name=None):
        self.calls.append({"texts": list(texts), "prompt_name": prompt_name})
        import numpy as np
        # Deterministic fake embedding: dimension 4, value keyed on prompt_name
        # so document-mode and query-mode vectors are trivially distinguishable.
        tag = 1.0 if prompt_name == "query" else 0.0
        return np.array([[tag, 0.0, 0.0, 0.0] for _ in texts])


def _make_provider(prompts: dict[str, str]) -> tuple[LocalEmbedProvider, _FakeSTModel]:
    """Build a LocalEmbedProvider around a fake ST model with the given prompts,
    bypassing the real (network-fetching) SentenceTransformer constructor."""
    fake_model = _FakeSTModel(prompts)
    with patch.object(LocalEmbedProvider, "__init__", lambda self, model_name="x": None):
        provider = LocalEmbedProvider()
    provider._model = fake_model
    provider._has_query_prompt = "query" in fake_model.prompts
    return provider, fake_model


class TestMaxSeqLengthCap:
    """config.yaml embedding.max_seq_length caps the loaded encoder's sequence
    length (a long-context model's own 8k config otherwise stalls CPU indexing),
    while a model that already declares a lower cap keeps its own."""

    def _build_with_model_cap(self, model_max_seq_length):
        fake_model = _FakeSTModel({})
        if model_max_seq_length is not None:
            fake_model.max_seq_length = model_max_seq_length
        with patch("agent.model_cache.load_with_offline_preference", return_value=fake_model):
            provider = LocalEmbedProvider("any-model-name")
        return provider, fake_model

    def test_long_context_model_capped_to_config_value(self):
        from agent.config import EMBEDDING_MAX_SEQ_LENGTH
        _, fake_model = self._build_with_model_cap(8192)
        assert fake_model.max_seq_length == EMBEDDING_MAX_SEQ_LENGTH

    def test_short_context_model_keeps_its_own_lower_cap(self):
        from agent.config import EMBEDDING_MAX_SEQ_LENGTH
        _, fake_model = self._build_with_model_cap(256)
        assert 256 <= EMBEDDING_MAX_SEQ_LENGTH, "test premise: model cap below config cap"
        assert fake_model.max_seq_length == 256

    def test_model_without_seq_length_attribute_gets_config_cap(self):
        from agent.config import EMBEDDING_MAX_SEQ_LENGTH
        _, fake_model = self._build_with_model_cap(None)
        assert fake_model.max_seq_length == EMBEDDING_MAX_SEQ_LENGTH


class TestAsymmetricModelQueryPrompt:
    """Model registers a "query" prompt (e.g. arctic-embed) — asserts the split."""

    def test_query_embedding_requests_query_prompt(self):
        provider, fake_model = _make_provider({"query": "Represent this sentence: ", "document": ""})
        provider.embed_query(["find the rate limiter"])
        assert fake_model.calls[-1]["prompt_name"] == "query"

    def test_document_embedding_requests_no_prompt(self):
        provider, fake_model = _make_provider({"query": "Represent this sentence: ", "document": ""})
        provider.embed(["def rate_limit(): pass"])
        assert fake_model.calls[-1]["prompt_name"] is None

    def test_query_and_document_vectors_differ_for_asymmetric_model(self):
        provider, _ = _make_provider({"query": "Represent this sentence: ", "document": ""})
        query_vec = provider.embed_query(["same text"])[0]
        doc_vec = provider.embed(["same text"])[0]
        assert query_vec != doc_vec, (
            "Asymmetric model must embed identical text differently for query "
            "vs. document mode — the whole point of the query prompt"
        )


class TestSymmetricModelUnchanged:
    """Model registers no "query" prompt — behavior must be exactly unchanged."""

    def test_no_query_prompt_registered_means_embed_query_falls_back(self):
        provider, fake_model = _make_provider({})  # no prompts at all
        provider.embed_query(["find the rate limiter"])
        assert fake_model.calls[-1]["prompt_name"] is None

    def test_symmetric_model_query_and_document_vectors_identical(self):
        provider, _ = _make_provider({})
        query_vec = provider.embed_query(["same text"])[0]
        doc_vec = provider.embed(["same text"])[0]
        assert query_vec == doc_vec


class TestOtherProvidersUnaffected:
    """Voyage/OpenAI providers have no registered-prompt concept; embed_query
    must delegate to embed() unchanged (no behavior change for these providers)."""

    def test_voyage_embed_query_delegates_to_embed(self):
        from agent.indexer._types import VoyageEmbedProvider
        provider = object.__new__(VoyageEmbedProvider)
        provider._client = None
        provider._model = "voyage-code-2"
        calls = []
        provider.embed = lambda texts: calls.append(texts) or [[0.1, 0.2]]
        result = provider.embed_query(["a query"])
        assert result == [[0.1, 0.2]]
        assert calls == [["a query"]]

    def test_openai_embed_query_delegates_to_embed(self):
        from agent.indexer._types import OpenAIEmbedProvider
        provider = object.__new__(OpenAIEmbedProvider)
        provider._client = None
        provider._model = "text-embedding-3-small"
        calls = []
        provider.embed = lambda texts: calls.append(texts) or [[0.3, 0.4]]
        result = provider.embed_query(["a query"])
        assert result == [[0.3, 0.4]]
        assert calls == [["a query"]]


class TestCodeIndexerEmbedQueryUsesQueryMode:
    """CodeIndexer.embed_query / embed_query_batch must call the provider's
    embed_query (not embed) — the actual call site fixed by this task."""

    def test_embed_query_calls_provider_embed_query_not_embed(self):
        from agent.indexer._core import CodeIndexer

        class _RecordingProvider:
            def __init__(self):
                self.embed_calls = []
                self.embed_query_calls = []

            def embed(self, texts):
                self.embed_calls.append(texts)
                return [[0.0] for _ in texts]

            def embed_query(self, texts):
                self.embed_query_calls.append(texts)
                return [[1.0] for _ in texts]

        provider = _RecordingProvider()
        indexer = object.__new__(CodeIndexer)
        indexer._embed_provider = provider

        vec = CodeIndexer.embed_query(indexer, "a search query")
        assert vec == [1.0]
        assert provider.embed_query_calls == [["a search query"]]
        assert provider.embed_calls == [], "embed_query must never call embed() (document mode)"

    def test_embed_query_batch_calls_provider_embed_query(self):
        from agent.indexer._core import CodeIndexer

        class _RecordingProvider:
            def embed(self, texts):
                return [[0.0] for _ in texts]

            def embed_query(self, texts):
                return [[9.0] for _ in texts]

        provider = _RecordingProvider()
        indexer = object.__new__(CodeIndexer)
        indexer._embed_provider = provider

        vecs = CodeIndexer.embed_query_batch(indexer, ["q1", "q2"])
        assert vecs == [[9.0], [9.0]]

    def test_embed_texts_still_uses_document_mode_embed(self):
        """embed_texts (indexing/document side) must be entirely unaffected —
        it always calls embed(), never embed_query()."""
        from agent.indexer._core import CodeIndexer

        class _RecordingProvider:
            def __init__(self):
                self.embed_calls = []

            def embed(self, texts):
                self.embed_calls.append(texts)
                return [[0.0] for _ in texts]

            def embed_query(self, texts):
                raise AssertionError("embed_texts must not use query-mode embedding")

        provider = _RecordingProvider()
        indexer = object.__new__(CodeIndexer)
        indexer._embed_provider = provider

        result = CodeIndexer.embed_texts(indexer, ["chunk one", "chunk two"])
        assert result == [[0.0], [0.0]]
        assert provider.embed_calls == [["chunk one", "chunk two"]]


class TestWorkingMemoryRecallUsesQueryMode:
    """Working-memory recall must embed the recall query via embed_query_fn,
    not the document-side embed_fn used to store note content at remember()
    time — same asymmetric-model concern as code search."""

    def test_recall_embeds_query_via_embed_query_fn_not_embed_fn(self, tmp_path):
        import chromadb
        from agent.working_context_store import WorkingContextStore

        doc_calls: list[str] = []
        query_calls: list[str] = []

        def _embed_fn(texts):
            doc_calls.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        def _embed_query_fn(texts):
            query_calls.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = WorkingContextStore(
            str(tmp_path),
            embed_fn=_embed_fn,
            embed_query_fn=_embed_query_fn,
            notes_chroma_client=client,
        )
        store.remember("/repo", "some note content")
        assert doc_calls == ["some note content"], "remember() must embed via embed_fn"

        store.recall("/repo", query="a search query")
        assert query_calls == ["a search query"], "recall(query=...) must embed via embed_query_fn"

    def test_no_embed_query_fn_falls_back_to_embed_fn(self, tmp_path):
        """Backward compatibility: callers that only pass embed_fn (e.g. a
        symmetric stand-in) keep working exactly as before."""
        import chromadb
        from agent.working_context_store import WorkingContextStore

        calls: list[str] = []

        def _embed_fn(texts):
            calls.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = WorkingContextStore(
            str(tmp_path),
            embed_fn=_embed_fn,
            notes_chroma_client=client,
        )
        store.remember("/repo", "some note content")
        store.recall("/repo", query="a search query")
        assert "a search query" in calls
