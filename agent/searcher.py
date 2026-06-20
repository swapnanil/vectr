"""Hybrid BM25 + vector search over the indexed codebase."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Plus

from agent.chunk_quality import (
    normalized_content,
    quality_score,
    query_wants_tests,
    symbol_identity_boost,
    _query_symbol_tokens,
)
from agent.indexer import CodeIndexer


# ---------------------------------------------------------------------------
# Code-aware BM25 tokenizer
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")


def _code_tokenize(text: str) -> list[str]:
    """
    Tokenize source code for BM25 in a way that understands identifiers.

    Plain whitespace splitting treats `send_signal_dispatch_uid` as one token,
    so a query for `dispatch_uid` scores zero.  This tokenizer splits on
    snake_case underscores AND camelCase boundaries so every sub-word becomes
    a searchable token.

    Examples:
      "RateLimitMiddleware"   → ["ratelimitmiddleware", "rate", "limit", "middleware"]
      "send_signal_dispatch"  → ["send", "signal", "dispatch"]
      "get_or_create"         → ["get", "or", "create"]
    """
    # Insert space at camelCase boundaries first
    expanded = _CAMEL_RE.sub(r"\1 \2", text)
    # Split on everything non-alphanumeric
    raw_tokens = _SPLIT_RE.split(expanded.lower())
    # Keep tokens of length ≥ 2; also keep the original unsplit identifier
    # so "ratelimitmiddleware" stays searchable as a whole
    seen: set[str] = set()
    out: list[str] = []
    for t in raw_tokens:
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Optional cross-encoder reranker (lazy-loaded)
# ---------------------------------------------------------------------------

_RERANK_TOP_K = 40  # rerank this many hybrid candidates before trimming to n_results
# When no language filter is set, doc prose dominates the shallow hybrid pool and
# real implementation chunks fall outside it (audit: extractor.py / chunk_file never
# fetched). Fetch a deeper pool in that case so the quality prior has code to surface.
_RERANK_TOP_K_UNFILTERED = 60


class _Reranker:
    """Lazy-loads a cross-encoder; gracefully disabled if model unavailable."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None
        self._failed = False

    def _load(self) -> None:
        if self._model is not None or self._failed:
            return
        try:
            from sentence_transformers import CrossEncoder
            cache_dir = str(Path.home() / ".cache" / "vectr" / "models")
            self._model = CrossEncoder(
                self._model_name,
                max_length=512,
                automodel_args={"ignore_mismatched_sizes": True},
                cache_folder=cache_dir,
            )
        except Exception:
            self._failed = True

    def rerank(self, query: str, candidates: list[tuple[str, object]]) -> list[object]:
        """Score (query, doc) pairs and return candidates sorted by cross-encoder score."""
        self._load()
        if self._model is None or not candidates:
            return [c for _, c in candidates]
        pairs = [(query, doc) for doc, _ in candidates]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(scores, [c for _, c in candidates]), key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked]


# ---------------------------------------------------------------------------
# Search result
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    file_path: str
    lines: str
    symbol_name: str
    language: str
    score: float
    content: str
    node_type: str = ""
    dup_count: int = 0   # number of identical chunks collapsed into this one (UPG-2.2)


# ---------------------------------------------------------------------------
# Searcher
# ---------------------------------------------------------------------------

class CodeSearcher:
    def __init__(self, indexer: CodeIndexer) -> None:
        self._indexer = indexer
        self._bm25: BM25Plus | None = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_metas: list[dict] = []
        # Read at instantiation so test fixtures can override via os.environ before creating searcher
        reranker_model = os.getenv("VECTR_RERANKER_MODEL", "BAAI/bge-reranker-base")
        self._reranker = _Reranker(reranker_model) if reranker_model else None

    def refresh_bm25(self) -> None:
        """Rebuild the BM25 index from the current ChromaDB collection."""
        ids, docs, metas = self._indexer.get_all_documents()
        if not docs:
            self._bm25 = None
            return
        self._bm25_ids = ids
        self._bm25_docs = docs
        self._bm25_metas = metas
        self._bm25 = BM25Plus([_code_tokenize(d) for d in docs])

    def search(
        self,
        query: str,
        n_results: int = 10,
        language: str | None = None,
        semantic_weight: float = 0.70,
        rerank: bool = True,
    ) -> tuple[list[SearchResult], int]:
        """Return (results, query_time_ms)."""
        t0 = time.monotonic()

        # UPG-3.1: normalise the language filter HERE — the single point both the
        # REST (service.search) and MCP (service.search_routed) paths funnel
        # through — so case/whitespace ("C", " Rust ") match indexed values and a
        # blank string degrades to "no filter" consistently for every caller.
        if language is not None:
            language = language.strip().lower() or None

        if self._indexer.total_chunks == 0:
            return [], 0

        # Fetch extra candidates when reranking so the reranker has room to reorder.
        # Go deeper when unfiltered, where doc prose otherwise crowds code out of the pool.
        if rerank and self._reranker:
            top_k = _RERANK_TOP_K_UNFILTERED if language is None else _RERANK_TOP_K
        else:
            top_k = n_results * 2
        fetch_n = min(top_k, self._indexer.total_chunks)

        # --- Vector search ---
        query_embedding = self._indexer.embed_query(query)
        vec_result = self._indexer.query_vector(query_embedding, n_results=fetch_n, language=language)

        vec_ids: list[str] = vec_result["ids"][0] if vec_result["ids"] else []
        vec_distances: list[float] = vec_result["distances"][0] if vec_result["distances"] else []
        vec_docs: list[str] = vec_result["documents"][0] if vec_result["documents"] else []
        vec_metas: list[dict] = vec_result["metadatas"][0] if vec_result["metadatas"] else []

        # cosine distance → similarity [0..1]
        vec_scores: dict[str, float] = {
            cid: max(0.0, 1.0 - dist)
            for cid, dist in zip(vec_ids, vec_distances)
        }

        # --- BM25 search ---
        bm25_scores: dict[str, float] = {}
        if self._bm25 is not None:
            query_tokens = _code_tokenize(query)
            raw = self._bm25.get_scores(query_tokens)
            if len(raw) > 0:
                max_raw = max(raw) or 1.0
                for idx, score in enumerate(raw):
                    if idx < len(self._bm25_ids):
                        meta = self._bm25_metas[idx]
                        if language and meta.get("language") != language:
                            continue
                        bm25_scores[self._bm25_ids[idx]] = score / max_raw

        # --- Hybrid merge ---
        all_ids = set(vec_scores) | set(bm25_scores)
        merged: dict[str, float] = {}
        for cid in all_ids:
            v = vec_scores.get(cid, 0.0)
            b = bm25_scores.get(cid, 0.0)
            merged[cid] = semantic_weight * v + (1.0 - semantic_weight) * b

        sorted_ids = sorted(merged, key=lambda x: merged[x], reverse=True)[:fetch_n]

        # Build result objects
        id_to_doc: dict[str, str] = dict(zip(vec_ids, vec_docs))
        id_to_meta: dict[str, dict] = dict(zip(vec_ids, vec_metas))
        for idx, cid in enumerate(self._bm25_ids):
            if cid not in id_to_doc:
                id_to_doc[cid] = self._bm25_docs[idx]
                id_to_meta[cid] = self._bm25_metas[idx]

        candidates: list[SearchResult] = []
        for cid in sorted_ids:
            doc = id_to_doc.get(cid, "")
            meta = id_to_meta.get(cid, {})
            candidates.append(SearchResult(
                file_path=meta.get("file_path", ""),
                lines=f"{meta.get('start_line', 0)}-{meta.get('end_line', 0)}",
                symbol_name=meta.get("symbol_name", ""),
                language=meta.get("language", ""),
                score=round(merged[cid], 4),
                content=doc[:2000],
                node_type=meta.get("node_type", ""),
            ))

        # --- Cross-encoder rerank ---
        if rerank and self._reranker and len(candidates) > 1:
            doc_candidate_pairs = [(r.content, r) for r in candidates]
            candidates = self._reranker.rerank(query, doc_candidate_pairs)

        # --- Quality prior + dedup + deterministic tiebreaker (UPG-2.1/2.2/2.3) ---
        candidates = self._apply_quality_and_dedup(query, candidates)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return candidates[:n_results], elapsed_ms

    def _apply_quality_and_dedup(
        self, query: str, candidates: list[SearchResult],
    ) -> list[SearchResult]:
        """Re-rank by relevance×quality, collapse duplicates, break ties deterministically.

        Relevance is taken from the candidates' current (post-rerank) order; a
        per-chunk quality prior then demotes trivial / navigational / generated /
        heading-only / (off-topic) test chunks (UPG-2.1, 2.3). Byte-identical
        chunks collapse to a single representative with a duplicate count so
        boilerplate can't flood the top-N (UPG-2.2).
        """
        if not candidates:
            return candidates
        targets_tests = query_wants_tests(query)
        # Precompute query tokens once for symbol-identity scoring (UPG-11.1).
        sym_tokens = _query_symbol_tokens(query)
        n = len(candidates)
        scored: list[tuple[float, float, int, SearchResult]] = []
        for i, r in enumerate(candidates):
            base = 1.0 - (i / n)  # rank-based relevance, best-first
            q = quality_score(
                r.content, r.file_path, r.language, r.node_type,
                query_targets_tests=targets_tests,
            )
            # Symbol-identity bonus: additive boost when the candidate's symbol
            # name matches the query's symbol intent (UPG-11.1).
            sym_boost = symbol_identity_boost(r.symbol_name, sym_tokens)
            scored.append((base * q + sym_boost, q, i, r))

        # Deterministic order: final score desc, quality desc, length desc, then
        # original rank, then path — so equal-scoring boilerplate never wins by
        # unstable sort order.
        scored.sort(key=lambda t: (-t[0], -t[1], -len(t[3].content), t[2], t[3].file_path))

        seen: dict[str, int] = {}
        out: list[SearchResult] = []
        for _, _, _, r in scored:
            key = normalized_content(r.content)
            if key in seen:
                out[seen[key]].dup_count += 1
                continue
            seen[key] = len(out)
            r.dup_count = 0
            out.append(r)
        return out
