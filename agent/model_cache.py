"""Shared local-model-cache helpers for embedder/reranker loading.

Both the embedder (agent/indexer/_types.py:LocalEmbedProvider) and the
reranker (agent/searcher.py:_Reranker) load a Hugging Face model that, in
the common case, is already fully present in vectr's local model cache
(``~/.cache/vectr/models``). Without an explicit offline hint,
``sentence_transformers`` still performs live network calls against
huggingface.co (HEAD/GET on config.json, tokenizer_config.json, repo tree
listings, ...) to check for a newer revision before falling back to the
cache — costing seconds of network-dependent latency on every daemon
start and failing hard on an air-gapped or proxied machine despite a
complete local cache (UPG-RERANKER-HF-NETWORK).

``load_with_offline_preference`` is the one entry point both load sites
use: it checks whether the model is already cached via
``huggingface_hub``'s own cache-lookup API (never re-deriving the HF
``models--org--name`` on-disk layout by hand), prefers an offline
(``local_files_only=True``) load when it is, and always falls back to a
normal network-enabled load — either on a genuine cache miss (first run)
or if the offline load raises anyway (an incomplete/corrupted cache
entry) — so first-run UX is never broken by this change.
"""
from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def is_model_cached(model_name: str, cache_dir: str) -> bool:
    """Best-effort check for whether ``model_name`` already has a snapshot in
    ``cache_dir`` (vectr's Hugging Face cache root).

    Probes for ``config.json`` via ``huggingface_hub.try_to_load_from_cache`` —
    every HF model repo vectr loads (sentence-transformers embedders and
    cross-encoder rerankers alike) ships one. This is a proxy for "this
    repo's snapshot already exists locally", not a guarantee every auxiliary
    file is present; ``load_with_offline_preference`` below still falls back
    to a network-enabled load if the offline attempt raises anyway.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    try:
        hit = try_to_load_from_cache(model_name, "config.json", cache_dir=cache_dir)
    except Exception:
        return False
    return isinstance(hit, str)


def load_with_offline_preference(
    build_fn: Callable[[bool], T], model_name: str, cache_dir: str
) -> T:
    """Instantiate a Hugging-Face-backed model, preferring an offline load
    (no network calls at all) when ``model_name`` is already present in
    ``cache_dir``, and falling back to a normal network-enabled load either
    on a genuine cache miss or if the offline load raises anyway.

    ``build_fn(local_files_only)`` must construct and return the model,
    passing ``local_files_only`` straight through to the underlying
    ``from_pretrained`` call (e.g. via ``SentenceTransformer(...,
    local_files_only=local_files_only)`` /
    ``CrossEncoder(..., local_files_only=local_files_only)``).
    """
    if is_model_cached(model_name, cache_dir):
        try:
            return build_fn(True)
        except Exception:
            pass  # incomplete/corrupted cache entry — fall through to network
    return build_fn(False)
