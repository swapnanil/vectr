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
    is_trivial_chunk,
    extract_class_from_content,
)
from agent.config import (
    RERANK_TOP_K as _RERANK_TOP_K,
    RERANK_TOP_K_UNFILTERED as _RERANK_TOP_K_UNFILTERED,
    RERANK_PRE_FILTER_FETCH_K as _RERANK_PRE_FILTER_FETCH_K,
    IMPORTANCE_PRIOR_LAMBDA as _IMPORTANCE_PRIOR_LAMBDA,
    DUAL_VECTOR_ENABLED as _DUAL_VECTOR_ENABLED,
    DUAL_VECTOR_BLEND_MODE as _DUAL_VECTOR_BLEND_MODE,
    DUAL_VECTOR_BLEND_WEIGHT as _DUAL_VECTOR_BLEND_WEIGHT,
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

# UPG-12.1: _RERANK_TOP_K / _RERANK_TOP_K_UNFILTERED are sourced from
# agent/config.yaml (ranking.rerank) via agent/config.py — imported above as
# _RERANK_TOP_K / _RERANK_TOP_K_UNFILTERED.  The alias names are kept so all
# existing call sites and tests work without change.


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
    # UPG-11.2: monotonic displayed score — updated after final ranking in _apply_quality_and_dedup
    # to guarantee non-increasing values down the returned list.
    # (score above holds the pre-quality hybrid score; this replaces it post-sort.)
    # UPG-11.4: symbol line-range affordance — populated from indexed metadata at search time.
    symbol_start_line: int = 0
    symbol_end_line: int = 0


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
        # ARCH-1b: file_path -> normalized file-level PageRank importance ∈ [0,1].
        # Injected by the service after each symbol-graph build via set_file_importance().
        # Empty until then → the importance prior is a no-op (pre-ARCH-1b behaviour).
        self._file_importance: dict[str, float] = {}
        # Read at instantiation so test fixtures can override via os.environ before creating searcher
        reranker_model = os.getenv("VECTR_RERANKER_MODEL", "BAAI/bge-reranker-base")
        self._reranker = _Reranker(reranker_model) if reranker_model else None

    def set_file_importance(self, importance: dict[str, float]) -> None:
        """Install the file-level importance map consumed by the ARCH-1b ranking
        prior. Called by the service after (re)building the symbol graph. Passing an
        empty dict disables the prior (the searcher falls back to base × quality)."""
        self._file_importance = importance or {}

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
        # UPG-15.7: for unfiltered queries, over-fetch up to pre_filter_fetch_k raw
        # candidates, drop trivial chunks at pool-entry (before the cross-encoder runs),
        # then trim to top_k_unfiltered.  This keeps the rerank pool small (~60) while
        # ensuring trivial HTML/TXT fixture chunks don't consume the pool slots that
        # real code needs.  The cross-encoder only ever sees ~top_k_unfiltered candidates.
        if rerank and self._reranker:
            if language is None:
                # Over-fetch for the trivial pre-filter; reranker sees ≤ top_k_unfiltered.
                top_k = _RERANK_PRE_FILTER_FETCH_K
            else:
                top_k = _RERANK_TOP_K
        else:
            top_k = n_results * 2
        fetch_n = min(top_k, self._indexer.total_chunks)

        # --- Vector search (body) ---
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

        # --- Vector search (purpose) — ARCH-4 dual-vector pool entry ---
        # A mechanical, long chunk body dilutes the intent-bearing signature/docstring
        # tokens when mean-pooled into a single embedding; a documented symbol can miss
        # dense pool entry entirely even though its own docstring paraphrases the query.
        # Querying a second, body-stripped "purpose" vector space and merging the two
        # similarity scores (uniformly, for every query — no keyword gating) lets such
        # chunks re-enter the pool. Old workspaces with an empty purpose collection get
        # {} back from query_vector_purpose and fall through to body-only behaviour.
        purpose_scores: dict[str, float] = {}
        if _DUAL_VECTOR_ENABLED:
            purpose_result = self._indexer.query_vector_purpose(
                query_embedding, n_results=fetch_n, language=language,
            )
            purpose_ids: list[str] = purpose_result["ids"][0] if purpose_result["ids"] else []
            purpose_distances: list[float] = purpose_result["distances"][0] if purpose_result["distances"] else []
            purpose_scores = {
                cid: max(0.0, 1.0 - dist)
                for cid, dist in zip(purpose_ids, purpose_distances)
            }

        # A chunk's dense score is the merge of its body and purpose similarities.
        # "max" (default, config: retrieval.dual_vector.blend_mode) takes whichever
        # vector space best captures the chunk for this query — averaging ("weighted")
        # would re-introduce the dilution the mechanism exists to defeat.
        if purpose_scores:
            dense_ids = set(vec_scores) | set(purpose_scores)
            if _DUAL_VECTOR_BLEND_MODE == "weighted":
                w = _DUAL_VECTOR_BLEND_WEIGHT
                dense_scores: dict[str, float] = {
                    cid: (1.0 - w) * vec_scores.get(cid, 0.0) + w * purpose_scores.get(cid, 0.0)
                    for cid in dense_ids
                }
            else:  # "max" — default
                dense_scores = {
                    cid: max(vec_scores.get(cid, 0.0), purpose_scores.get(cid, 0.0))
                    for cid in dense_ids
                }
        else:
            dense_scores = dict(vec_scores)

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
        all_ids = set(dense_scores) | set(bm25_scores)
        merged: dict[str, float] = {}
        for cid in all_ids:
            v = dense_scores.get(cid, 0.0)
            b = bm25_scores.get(cid, 0.0)
            merged[cid] = semantic_weight * v + (1.0 - semantic_weight) * b

        sorted_ids = sorted(merged, key=lambda x: merged[x], reverse=True)[:fetch_n]

        # Build the content/language lookup from already-fetched data.
        # Used by the pool-entry trivial filter below and later for result objects.
        # No extra ChromaDB round-trips for the body query/BM25 sources; a purpose-only
        # id (rescued into the pool solely by its purpose-vector similarity) has no
        # document in either, so its body content/metadata are backfilled with one
        # batched get_chunk_documents() lookup against the body collection.
        id_to_doc: dict[str, str] = dict(zip(vec_ids, vec_docs))
        id_to_meta: dict[str, dict] = dict(zip(vec_ids, vec_metas))
        for idx, cid in enumerate(self._bm25_ids):
            if cid not in id_to_doc:
                id_to_doc[cid] = self._bm25_docs[idx]
                id_to_meta[cid] = self._bm25_metas[idx]
        purpose_only_ids = [cid for cid in purpose_scores if cid not in id_to_doc]
        if purpose_only_ids:
            backfill = self._indexer.get_chunk_documents(purpose_only_ids)
            for cid, (doc, meta) in backfill.items():
                id_to_doc[cid] = doc
                id_to_meta[cid] = meta

        # --- UPG-15.7 (revised): Union-of-signals pool selection with trivial filter ---
        # Build the rerank pool as the UNION of:
        #   • the first _RERANK_TOP_K_UNFILTERED non-trivial chunks from the vec-similarity
        #     ranking (vec-strong/bm25-weak docs like prose howto pages land here), AND
        #   • the first _RERANK_TOP_K_UNFILTERED non-trivial chunks from the bm25 ranking
        #     (keyword-exact hits land here).
        #
        # Using merged-score order as the sole trim criterion (the previous approach) caused
        # F2/F18 regressions: a prose doc that is strong on vector but weak on BM25 would be
        # outranked by keyword-heavy fixture chunks on the blended score and fall outside the
        # top-60 window before any boost was applied.  The union restores bf223bf semantics
        # (separate-signal top-K lists) while still dropping trivial fixture chunks.
        if language is None and rerank and self._reranker:
            # Vec-signal: iterate the dense (body ⊔ purpose) score in descending order.
            # Using dense_scores rather than raw vec_ids is what lets a purpose-vector-
            # only rescue (a chunk absent from the body-only vec_ids list but present via
            # its purpose vector) actually reach the rerank pool (ARCH-4).
            dense_order = sorted(dense_scores, key=lambda x: dense_scores[x], reverse=True)
            vec_non_trivial: list[str] = []
            for cid in dense_order:
                doc = id_to_doc.get(cid, "")
                lang = (id_to_meta.get(cid) or {}).get("language", "")
                if is_trivial_chunk(doc, lang):
                    continue
                vec_non_trivial.append(cid)
                if len(vec_non_trivial) >= _RERANK_TOP_K_UNFILTERED:
                    break

            # BM25-signal: iterate bm25_scores in descending bm25 order
            bm25_non_trivial: list[str] = []
            for cid in sorted(bm25_scores, key=lambda x: bm25_scores[x], reverse=True):
                doc = id_to_doc.get(cid, "")
                lang = (id_to_meta.get(cid) or {}).get("language", "")
                if is_trivial_chunk(doc, lang):
                    continue
                bm25_non_trivial.append(cid)
                if len(bm25_non_trivial) >= _RERANK_TOP_K_UNFILTERED:
                    break

            # Union the two sets; preserve merged-score order within the union so
            # downstream code that relies on sorted_ids ordering is unaffected.
            union_ids = set(vec_non_trivial) | set(bm25_non_trivial)
            sorted_ids = [cid for cid in sorted_ids if cid in union_ids]

        # Build result objects
        candidates: list[SearchResult] = []
        for cid in sorted_ids:
            doc = id_to_doc.get(cid, "")
            meta = id_to_meta.get(cid, {})
            start_line = int(meta.get("start_line", 0))
            end_line = int(meta.get("end_line", 0))
            candidates.append(SearchResult(
                file_path=meta.get("file_path", ""),
                lines=f"{start_line}-{end_line}",
                symbol_name=meta.get("symbol_name", ""),
                language=meta.get("language", ""),
                score=round(merged[cid], 4),
                content=doc[:2000],
                node_type=meta.get("node_type", ""),
                # UPG-11.4: carry the exact line range of the indexed symbol so
                # callers can expand to the full definition without a blind re-read.
                symbol_start_line=start_line,
                symbol_end_line=end_line,
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

        Pipeline: final_score = base_rerank_score * quality_score(chunk) *
        (1 + lambda * file_importance) (ARCH-1b). The importance factor is a
        relevance-gated multiplicative prior — multiplying by base_rerank_score
        means an irrelevant high-importance file (base ≈ 0) is never lifted. When no
        importance map is installed (or lambda = 0) the factor is 1.0 and the score
        reduces to base × quality.
        Keeps exact-duplicate dedup and the quality_score call (with trivial/navigational/
        doc-language demotion intact).

        Relevance is taken from the candidates' current (post-rerank) order; a
        per-chunk quality prior then demotes trivial / navigational / generated /
        heading-only / test chunks (UPG-2.1, 2.3). Byte-identical chunks collapse
        to a single representative with a duplicate count so boilerplate can't flood
        the top-N (UPG-2.2).
        """
        if not candidates:
            return candidates
        n = len(candidates)
        scored: list[tuple[float, float, int, SearchResult]] = []
        for i, r in enumerate(candidates):
            base = 1.0 - (i / n)  # rank-based relevance, best-first
            q = quality_score(
                r.content, r.file_path, r.language, r.node_type,
            )
            # UPG-11.10: promote symbol_name to the qualified "Class.leaf" form when
            # class context can be extracted from the indexer-injected "# class: X"
            # prefix in the chunk content.  Presentational only — it makes the
            # REST/MCP `symbol` field show "Field.deconstruct" instead of the bare
            # "deconstruct" so callers see which class owns the chunk.  Does not
            # affect ranking.
            if r.symbol_name and "." not in r.symbol_name and "::" not in r.symbol_name:
                class_ctx = extract_class_from_content(r.content)
                if class_ctx:
                    r.symbol_name = f"{class_ctx}.{r.symbol_name}"
            # ARCH-1b: relevance-gated file-importance prior. importance ∈ [0,1];
            # factor ∈ [1, 1+lambda]. No-op when no map installed or lambda = 0.
            imp = self._file_importance.get(r.file_path, 0.0) if self._file_importance else 0.0
            importance_factor = 1.0 + _IMPORTANCE_PRIOR_LAMBDA * imp
            scored.append((base * q * importance_factor, q, i, r))

        # Deterministic order: final score desc, quality desc, length desc, then
        # original rank, then path — so equal-scoring boilerplate never wins by
        # unstable sort order.
        scored.sort(key=lambda t: (-t[0], -t[1], -len(t[3].content), t[2], t[3].file_path))

        seen: dict[str, int] = {}
        out: list[SearchResult] = []
        for final_score, _, _, r in scored:
            key = normalized_content(r.content)
            if key in seen:
                out[seen[key]].dup_count += 1
                continue
            seen[key] = len(out)
            r.dup_count = 0
            # UPG-11.2: replace the stale pre-rerank hybrid score with the actual
            # composite ranking key so displayed scores are non-increasing with rank.
            # base ∈ [0, 1] and quality_score is a ≤1.0 multiplier, so final_score is
            # already in [0, 1]; clamp defensively for callers with a confidence gate.
            r.score = round(min(1.0, final_score), 4)
            out.append(r)
        return out
