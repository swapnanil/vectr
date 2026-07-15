"""Chunk dataclass, EmbedProvider protocol, embed provider implementations."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent.config import (
    EMBEDDING_DEFAULT_MODEL as _EMBEDDING_DEFAULT_MODEL,
    EMBEDDING_MAX_SEQ_LENGTH as _EMBEDDING_MAX_SEQ_LENGTH,
    EMBEDDING_THREAD_CAP as _EMBEDDING_THREAD_CAP,
)


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
        from agent.model_cache import load_with_offline_preference, suppress_model_load_noise
        suppress_model_load_noise()
        import torch
        from sentence_transformers import SentenceTransformer

        # UPG-EMBED-THREAD-CONTENTION: cap torch's CPU thread pool at
        # construction time (config.yaml embedding.thread_cap /
        # thread_cap_auto_fraction). This is a process-wide torch setting —
        # applying it once here also caps the cross-encoder reranker, which
        # shares the same pool — so it belongs at the one embed-provider
        # construction site, not scattered across call sites. Without a cap,
        # a debounced watcher batch embed saturates every core and the HTTP
        # event loop gets no scheduler slice, so a live session's first MCP
        # call (vectr_status/vectr_recall) can time out even though no lock
        # is held.
        torch.set_num_threads(_EMBEDDING_THREAD_CAP)
        try:
            # Interop-thread count can only be set once per process, before
            # any parallel torch work has started — a later
            # LocalEmbedProvider construction in the same process (e.g.
            # across tests) raises RuntimeError on a second call. Safe to
            # ignore: the first call already applied the cap process-wide.
            torch.set_num_interop_threads(_EMBEDDING_THREAD_CAP)
        except RuntimeError:
            pass

        cache_dir = Path.home() / ".cache" / "vectr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # UPG-RERANKER-HF-NETWORK: prefer an offline (local_files_only) load
        # when this model is already fully cached, so a warm daemon start
        # never makes live huggingface.co calls (HEAD/GET on config.json,
        # tokenizer_config.json, repo tree listings, ...) just to re-confirm
        # a cache it already has. Falls back to a network-enabled load on a
        # genuine cache miss (first run) so that UX is unchanged.
        self._model = load_with_offline_preference(
            # torch_dtype float32 is load-bearing on CPU (UPG-EMBED-CPU-DTYPE):
            # transformers honors the checkpoint's declared dtype, and a model
            # shipping bfloat16 weights (the current default does) runs through
            # software-emulated bf16 matmuls on CPU — measured 12x slower than
            # float32 for identical inputs. On CPU float32 is strictly correct;
            # revisit only if a GPU/MPS device path is ever added.
            lambda local_only: SentenceTransformer(
                model_name,
                cache_folder=str(cache_dir),
                trust_remote_code=True,
                device="cpu",
                local_files_only=local_only,
                model_kwargs={"torch_dtype": torch.float32},
            ),
            model_name,
            str(cache_dir),
        )
        # Cap the encoder's sequence length (config.yaml embedding.max_seq_length).
        # A long-context model's own config otherwise wins — the current default
        # declares 8192, and encoding long chunks at full length on CPU has
        # quadratic-attention cost that stalls a full-corpus index for hours.
        # Only ever lowers: a model that already declares less keeps its own cap.
        current_cap = getattr(self._model, "max_seq_length", None)
        if current_cap is None or current_cap > _EMBEDDING_MAX_SEQ_LENGTH:
            self._model.max_seq_length = _EMBEDDING_MAX_SEQ_LENGTH
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
    """Uses Voyage AI code embedding model (requires VOYAGE_API_KEY).

    No CPU thread cap (UPG-EMBED-THREAD-CONTENTION) applies here — embedding
    happens server-side over HTTP; there is no local torch thread pool to
    contend with the event loop.
    """

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
    """Uses OpenAI embedding model (requires OPENAI_API_KEY).

    No CPU thread cap (UPG-EMBED-THREAD-CONTENTION) applies here — same
    reasoning as VoyageEmbedProvider above: embedding is a remote HTTP call,
    not local CPU-bound torch work.
    """

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
