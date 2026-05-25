"""Hybrid BM25 + vector search over the indexed codebase."""
from __future__ import annotations

import time
from dataclasses import dataclass

from rank_bm25 import BM25Plus

from agent.indexer import CodeIndexer


@dataclass
class SearchResult:
    file_path: str
    lines: str
    symbol_name: str
    language: str
    score: float
    content: str


class CodeSearcher:
    def __init__(self, indexer: CodeIndexer) -> None:
        self._indexer = indexer
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_metas: list[dict] = []

    def refresh_bm25(self) -> None:
        """Rebuild the BM25 index from the current ChromaDB collection."""
        ids, docs, metas = self._indexer.get_all_documents()
        if not docs:
            self._bm25 = None
            return
        self._bm25_ids = ids
        self._bm25_docs = docs
        self._bm25_metas = metas
        tokenised = [d.lower().split() for d in docs]
        self._bm25 = BM25Plus(tokenised)

    def search(
        self,
        query: str,
        n_results: int = 10,
        language: str | None = None,
        semantic_weight: float = 0.70,
    ) -> tuple[list[SearchResult], int]:
        """Return (results, query_time_ms)."""
        t0 = time.monotonic()

        if self._indexer.total_chunks == 0:
            return [], 0

        n = min(n_results, self._indexer.total_chunks)

        # --- Vector search ---
        query_embedding = self._indexer.embed_query(query)
        vec_result = self._indexer.query_vector(query_embedding, n_results=n * 2, language=language)

        vec_ids: list[str] = vec_result["ids"][0] if vec_result["ids"] else []
        vec_distances: list[float] = vec_result["distances"][0] if vec_result["distances"] else []
        vec_docs: list[str] = vec_result["documents"][0] if vec_result["documents"] else []
        vec_metas: list[dict] = vec_result["metadatas"][0] if vec_result["metadatas"] else []

        # cosine distance → similarity score [0..1]
        vec_scores: dict[str, float] = {
            cid: max(0.0, 1.0 - dist)
            for cid, dist in zip(vec_ids, vec_distances)
        }

        # --- BM25 search ---
        bm25_scores: dict[str, float] = {}
        if self._bm25 is not None:
            raw = self._bm25.get_scores(query.lower().split())
            if len(raw) > 0:
                max_raw = max(raw) or 1.0
                for idx, score in enumerate(raw):
                    if idx < len(self._bm25_ids):
                        meta = self._bm25_metas[idx]
                        if language and meta.get("language") != language:
                            continue
                        bm25_scores[self._bm25_ids[idx]] = score / max_raw

        # --- Merge (reciprocal rank fusion style) ---
        all_ids = set(vec_scores) | set(bm25_scores)
        merged: dict[str, float] = {}
        for cid in all_ids:
            v = vec_scores.get(cid, 0.0)
            b = bm25_scores.get(cid, 0.0)
            merged[cid] = semantic_weight * v + (1.0 - semantic_weight) * b

        sorted_ids = sorted(merged, key=lambda x: merged[x], reverse=True)[:n]

        # build result objects — pull content from vec results or BM25 store
        id_to_doc: dict[str, str] = dict(zip(vec_ids, vec_docs))
        id_to_meta: dict[str, dict] = dict(zip(vec_ids, vec_metas))

        for idx, cid in enumerate(self._bm25_ids):
            if cid not in id_to_doc:
                id_to_doc[cid] = self._bm25_docs[idx]
                id_to_meta[cid] = self._bm25_metas[idx]

        results: list[SearchResult] = []
        for cid in sorted_ids:
            doc = id_to_doc.get(cid, "")
            meta = id_to_meta.get(cid, {})
            results.append(SearchResult(
                file_path=meta.get("file_path", ""),
                lines=f"{meta.get('start_line', 0)}-{meta.get('end_line', 0)}",
                symbol_name=meta.get("symbol_name", ""),
                language=meta.get("language", ""),
                score=round(merged[cid], 4),
                content=doc[:2000],  # truncate very long chunks for transport
            ))

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return results, elapsed_ms
