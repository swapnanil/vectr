"""Chunk dataclass, EmbedProvider protocol, embed provider implementations."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent.config import EMBEDDING_DEFAULT_MODEL as _EMBEDDING_DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class CodeChunk:
    chunk_id: str
    content: str
    file_path: str
    language: str
    node_type: str
    start_line: int
    end_line: int
    symbol_name: str


# ---------------------------------------------------------------------------
# Embedding provider protocol
# ---------------------------------------------------------------------------

class EmbedProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        """Embed search-query text. Distinct from `embed()` (document/indexing side)
        because some asymmetric embedding models require a different, model-
        registered prompt for queries than for the passages they're matched
        against. Providers with no such distinction may just delegate to
        `embed()`.
        """
        ...


class LocalEmbedProvider:
    """Uses sentence-transformers (no API key). Default model: config.yaml embedding.default_model
    (UPG-EMBEDDER-SWAP-GRANITE)."""

    def __init__(self, model_name: str = _EMBEDDING_DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer
        from agent.model_cache import load_with_offline_preference
        cache_dir = Path.home() / ".cache" / "vectr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # UPG-RERANKER-HF-NETWORK: prefer an offline (local_files_only) load
        # when this model is already fully cached, so a warm daemon start
        # never makes live huggingface.co calls (HEAD/GET on config.json,
        # tokenizer_config.json, repo tree listings, ...) just to re-confirm
        # a cache it already has. Falls back to a network-enabled load on a
        # genuine cache miss (first run) so that UX is unchanged.
        self._model = load_with_offline_preference(
            lambda local_only: SentenceTransformer(
                model_name,
                cache_folder=str(cache_dir),
                trust_remote_code=True,
                device="cpu",
                local_files_only=local_only,
            ),
            model_name,
            str(cache_dir),
        )
        # Asymmetric embedding models (arctic-embed and others) register a "query"
        # prompt in their sentence-transformers config that must be prepended to
        # search queries but NOT to the documents/chunks being indexed. Detected from
        # the loaded model itself (never hardcoded) so symmetric models — which
        # register no such prompt — embed queries and documents identically, unchanged.
        self._has_query_prompt = "query" in getattr(self._model, "prompts", {})

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        if not self._has_query_prompt:
            return self.embed(texts)
        embeddings = self._model.encode(
            texts,
            prompt_name="query",
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()


class VoyageEmbedProvider:
    """Uses Voyage AI code embedding model (requires VOYAGE_API_KEY)."""

    def __init__(self, model_name: str = "voyage-code-2") -> None:
        import voyageai  # type: ignore
        self._client = voyageai.Client()
        self._model = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._client.embed(texts, model=self._model)
        return result.embeddings

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)


class OpenAIEmbedProvider:
    """Uses OpenAI embedding model (requires OPENAI_API_KEY)."""

    def __init__(self, model_name: str = "text-embedding-3-small") -> None:
        from openai import OpenAI
        self._client = OpenAI()
        self._model = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)


def get_embed_provider(model_spec: str) -> EmbedProvider:
    """Factory: parse VECTR_EMBED_MODEL and return the right provider."""
    if model_spec.startswith("voyage"):
        return VoyageEmbedProvider(model_spec)
    if model_spec.startswith("openai/"):
        return OpenAIEmbedProvider(model_spec.split("/", 1)[1])
    return LocalEmbedProvider(model_spec)
