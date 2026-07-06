"""Hybrid BM25 + vector search over the indexed codebase."""
from __future__ import annotations

import math
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
    is_type_definition_chunk,
    leading_docstring_key,
)
from agent.config import (
    RERANK_TOP_K as _RERANK_TOP_K,
    RERANK_TOP_K_UNFILTERED as _RERANK_TOP_K_UNFILTERED,
    RERANK_PRE_FILTER_FETCH_K as _RERANK_PRE_FILTER_FETCH_K,
    IMPORTANCE_PRIOR_LAMBDA as _IMPORTANCE_PRIOR_LAMBDA,
    CLASS_IMPORTANCE_PRIOR_LAMBDA as _CLASS_IMPORTANCE_PRIOR_LAMBDA,
    PURPOSE_RANK_PRIOR_LAMBDA as _PURPOSE_RANK_PRIOR_LAMBDA,
    TYPE_DEF_PRIOR_LAMBDA as _TYPE_DEF_PRIOR_LAMBDA,
    DUAL_VECTOR_ENABLED as _DUAL_VECTOR_ENABLED,
    DUAL_VECTOR_BLEND_MODE as _DUAL_VECTOR_BLEND_MODE,
    DUAL_VECTOR_BLEND_WEIGHT as _DUAL_VECTOR_BLEND_WEIGHT,
    NOTFOUND_FLOOR_ENABLED as _NOTFOUND_FLOOR_ENABLED,
    NOTFOUND_FLOOR_MIN_TOKEN_LEN as _NOTFOUND_FLOOR_MIN_TOKEN_LEN,
    NOTFOUND_FLOOR_STOPWORDS as _NOTFOUND_FLOOR_STOPWORDS,
    NOTFOUND_FLOOR_MIN_ZERO_DF_TOKENS as _NOTFOUND_FLOOR_MIN_ZERO_DF_TOKENS,
    NOTFOUND_FLOOR_MIN_TOP_RELEVANCE as _NOTFOUND_FLOOR_MIN_TOP_RELEVANCE,
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
      "RateLimitMiddleware"   → ["rate", "limit", "middleware"]
      "send_signal_dispatch"  → ["send", "signal", "dispatch"]
      "get_or_create"         → ["get", "or", "create"]

    This is the RAW token stream — every occurrence is kept, including
    repeats. A document/chunk that mentions a concept five times (its focus)
    must produce a five-times-higher term frequency than one that mentions
    it once (a passing reference); BM25Plus needs that per-document term
    frequency to do anything more than IDF-weighted set-overlap, so this
    function must NOT deduplicate. Building the corpus-wide document-
    frequency table (how many DOCUMENTS contain a term, as opposed to how
    many times) is a different, per-document-deduped statistic — that
    dedupe happens once at the call site that builds it (refresh_bm25),
    not here. Query-side scoring, which has different requirements (a
    caller's sentence repeating a word is a phrasing accident, not a
    relevance signal), dedupes separately via `_code_tokenize_query` below.
    """
    # Insert space at camelCase boundaries first
    expanded = _CAMEL_RE.sub(r"\1 \2", text)
    # Split on everything non-alphanumeric
    raw_tokens = _SPLIT_RE.split(expanded.lower())
    # Keep tokens of length >= 2; every occurrence is preserved (see docstring).
    return [t for t in raw_tokens if len(t) >= 2]


def _code_tokenize_query(text: str) -> list[str]:
    """
    Tokenize a QUERY string for BM25 scoring.

    Uses the same identifier-aware splitting as `_code_tokenize`, but
    collapses repeated tokens (order preserved) before they reach BM25Plus.
    `BM25Plus.get_scores` sums an IDF-weighted contribution for every token
    it is given, including exact duplicates — so a query that happens to
    repeat a content word (e.g. an AI caller's sentence structure, not
    intentional emphasis) would silently double that term's weight relative
    to an equally-relevant query that said it once. Document term frequency
    (kept, uncapped, in `_code_tokenize`) measures how heavily a matched
    CHUNK uses a concept — a real relevance signal. Query term frequency
    mostly measures how the caller's sentence was worded, not the
    underlying concept, so it is neutralized here rather than left to
    accumulate.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in _code_tokenize(text):
        if t not in seen:
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
            from agent.model_cache import load_with_offline_preference
            cache_dir = str(Path.home() / ".cache" / "vectr" / "models")
            # UPG-RERANKER-HF-NETWORK: prefer an offline (local_files_only)
            # load when this model is already fully cached, so search never
            # makes live huggingface.co calls just to re-confirm a cache it
            # already has. Falls back to a network-enabled load on a genuine
            # cache miss (first run) so first-run UX is unchanged.
            self._model = load_with_offline_preference(
                lambda local_only: CrossEncoder(
                    self._model_name,
                    max_length=512,
                    automodel_args={"ignore_mismatched_sizes": True},
                    cache_folder=cache_dir,
                    local_files_only=local_only,
                ),
                self._model_name,
                cache_dir,
            )
        except Exception:
            self._failed = True

    def rerank(self, query: str, candidates: list[tuple[str, object]]) -> list[object]:
        """Score (query, doc) pairs, stamp each candidate's ``ce_relevance`` with
        the absolute cross-encoder relevance score, and return candidates sorted
        by that score (highest first).

        UPG-SCORE-DISPLAY-FLAT: this cross-encoder score is the one absolute,
        per-(query, doc) relevance signal in the pipeline — unlike the
        rank-based composite ``_apply_quality_and_dedup`` builds for ordering,
        it does not get re-derived from this result set's own rank order, so
        it is safe to display verbatim. Previously it was used only to sort
        and then discarded.
        """
        self._load()
        if self._model is None or not candidates:
            return [c for _, c in candidates]
        pairs = [(query, doc) for doc, _ in candidates]
        raw_scores = self._model.predict(pairs)
        scores = self._normalize_scores(raw_scores)
        for score, (_, c) in zip(scores, candidates):
            c.ce_relevance = score
        ranked = sorted(zip(scores, [c for _, c in candidates]), key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked]

    @staticmethod
    def _normalize_scores(raw_scores) -> list[float]:
        """Clamp cross-encoder output to [0, 1] so a displayed relevance score
        is never a raw, unbounded logit.

        A ``sentence_transformers.CrossEncoder`` configured with
        ``num_labels == 1`` (the expected shape for a relevance-scoring model)
        applies a sigmoid activation by default, so ``predict()`` already
        returns values in [0, 1] in the common case. This is a defensive
        fallback for a differently-configured model checkpoint that returns
        raw logits instead: if ANY score in the batch falls outside [0, 1],
        apply a sigmoid to the whole batch before it reaches a SearchResult.
        """
        values = [float(s) for s in raw_scores]
        if any(v < 0.0 or v > 1.0 for v in values):
            return [1.0 / (1.0 + math.exp(-v)) for v in values]
        return values


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
    # UPG-SCORE-DISPLAY-FLAT: `score` is an ABSOLUTE query-doc relevance value
    # (the cross-encoder relevance when reranked, else the dense cosine
    # similarity — see ce_relevance/dense_sim below), set by
    # _apply_quality_and_dedup as the very last step before a result is
    # returned. It is NOT the ordering key: ordering is decided by a separate
    # rank/quality/importance composite that is intentionally never displayed
    # (see _apply_quality_and_dedup's docstring). Consequently displayed
    # scores down a result list are not guaranteed to be monotonic — a
    # quality/importance prior can legitimately promote a candidate with a
    # lower absolute relevance above one with a higher absolute relevance;
    # this is disagreement-as-information, not a bug (formerly UPG-11.2's
    # "monotonic displayed score", superseded here).
    # UPG-11.4: symbol line-range affordance — populated from indexed metadata at search time.
    symbol_start_line: int = 0
    symbol_end_line: int = 0
    # UPG-SCORE-DISPLAY-FLAT: absolute cross-encoder relevance for this exact
    # (query, chunk) pair, in [0, 1] — set by _Reranker.rerank for every
    # candidate the cross-encoder actually scored. None when reranking was
    # skipped/disabled/unavailable, or for candidates the reranker never saw.
    ce_relevance: float | None = None
    # UPG-SCORE-DISPLAY-FLAT: the chunk's dense (body ⊔ purpose) cosine
    # similarity to the query — already absolute (max(0, 1-distance), never
    # re-normalized per result set). Used as the displayed score when no
    # ce_relevance is available (rerank=False, reranker unavailable, or a
    # candidate outside the reranked pool). Defaults to 0.0 for SearchResults
    # built outside search() (e.g. test fixtures), an exact no-op fallback.
    dense_sim: float = 0.0
    # ARCH-4b: the chunk's own body-vs-purpose cosine similarity to the query,
    # already normalized to [0,1] (query_vector_purpose distances are clamped at
    # 0 the same way body vec_scores are). Defaults to 0.0 — an exact no-op —
    # for chunks with no purpose vector (dual_vector disabled, or a non-symbol
    # chunk that never got one) and for any candidate built outside search()
    # (e.g. test fixtures). Consumed by _apply_quality_and_dedup's final-sort
    # blend; never affects pool entry, which ARCH-4's dense_scores merge already
    # handles upstream.
    purpose_sim: float = 0.0


class SearchResultList(list):
    """``list[SearchResult]`` carrying a ``low_confidence`` flag (UPG-NOTFOUND-FLOOR).

    A plain subclass rather than widening ``search()``'s return type — every
    existing ``results, ms = searcher.search(...)`` call site keeps working
    unchanged (indexing, iteration, ``len()``, truthiness all behave exactly
    like a list), while callers that care can read
    ``getattr(results, "low_confidence", False)``.
    """

    low_confidence: bool = False


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
        # UPG-NOTFOUND-FLOOR-2: token -> number of indexed chunks containing that
        # token at least once, across the WHOLE corpus (not just a query's fetched
        # pool). Built alongside the BM25 index in refresh_bm25() from the same
        # tokenization pass. Powers the lexical-vocabulary-anchor low-confidence
        # signal (see search()).
        self._vocab_df: dict[str, int] = {}
        # ARCH-1b: file_path -> normalized file-level PageRank importance ∈ [0,1].
        # Injected by the service after each symbol-graph build via set_file_importance().
        # Empty until then → the importance prior is a no-op (pre-ARCH-1b behaviour).
        self._file_importance: dict[str, float] = {}
        # ARCH-2: class_name -> normalized class-level reference-frequency
        # importance ∈ [0,1]. Injected by the service after each symbol-graph
        # build via set_class_importance(). Empty until then → the class-importance
        # prior is a no-op (pre-ARCH-2 behaviour). Discriminates same-leaf method
        # collisions (two unrelated classes defining a same-named method) that the
        # file-level prior above cannot.
        self._class_importance: dict[str, float] = {}
        # UPG-TESTPATH-FRAMEWORK-MISCLASS (F58): file_path -> corpus-wide
        # unambiguous caller-file count. Injected by the service after each
        # symbol-graph build via set_file_fan_in(). Empty until then → the
        # test-framework fan-in exemption is a no-op (every test-path file keeps
        # the full test_deprioritised demotion, pre-F58 behaviour).
        self._file_fan_in: dict[str, int] = {}
        # Read at instantiation so test fixtures can override via os.environ before creating searcher
        reranker_model = os.getenv("VECTR_RERANKER_MODEL", "BAAI/bge-reranker-base")
        self._reranker = _Reranker(reranker_model) if reranker_model else None

    def warm_reranker(self) -> None:
        """Eagerly load the cross-encoder reranker (UPG-RERANKER-HF-NETWORK) so
        its model-load cost lands at daemon startup instead of inside the first
        ``vectr_search`` call after a restart. Called by the service at startup
        (skipped in memory-only mode, where search is disabled and there is
        nothing to rerank). A no-op if reranking is disabled
        (``VECTR_RERANKER_MODEL=""`` → ``self._reranker is None``); safe to call
        even if the load fails — ``_Reranker._load()`` already degrades to
        ``_failed = True`` and search continues without a reranker exactly as
        before. The lazy call inside ``rerank()`` remains as a safety net for
        the case where warm-up itself never ran (e.g. an older embedded caller
        that doesn't invoke this method).
        """
        if self._reranker is not None:
            self._reranker._load()

    def set_file_importance(self, importance: dict[str, float]) -> None:
        """Install the file-level importance map consumed by the ARCH-1b ranking
        prior. Called by the service after (re)building the symbol graph. Passing an
        empty dict disables the prior (the searcher falls back to base × quality)."""
        self._file_importance = importance or {}

    def set_class_importance(self, importance: dict[str, float]) -> None:
        """Install the class-level importance map consumed by the ARCH-2 ranking
        prior. Called by the service after (re)building the symbol graph. Passing an
        empty dict disables the prior (the searcher falls back to whatever the
        file-level prior and base × quality already produce)."""
        self._class_importance = importance or {}

    def set_file_fan_in(self, fan_in: dict[str, int]) -> None:
        """Install the corpus-wide unambiguous caller-file-count map consumed by
        the UPG-TESTPATH-FRAMEWORK-MISCLASS (F58) quality-prior exemption. Called
        by the service after (re)building the symbol graph. Passing an empty
        dict disables the exemption (every test-path file keeps the full
        test_deprioritised demotion)."""
        self._file_fan_in = fan_in or {}

    def refresh_bm25(self) -> None:
        """Rebuild the BM25 index from the current ChromaDB collection."""
        ids, docs, metas = self._indexer.get_all_documents()
        if not docs:
            self._bm25 = None
            self._vocab_df = {}
            return
        self._bm25_ids = ids
        self._bm25_docs = docs
        self._bm25_metas = metas
        # _code_tokenize keeps every occurrence (repeats included) — real term
        # frequency BM25Plus needs. Reuse the same tokenization pass to build the
        # corpus-wide document-frequency table (UPG-NOTFOUND-FLOOR-2), which is a
        # different statistic (documents-containing-term, not occurrence count)
        # and so dedupes per document via set(toks) below — no extra tokenization
        # cost either way.
        tokenized_docs = [_code_tokenize(d) for d in docs]
        self._bm25 = BM25Plus(tokenized_docs)
        vocab_df: dict[str, int] = {}
        for toks in tokenized_docs:
            for t in set(toks):
                vocab_df[t] = vocab_df.get(t, 0) + 1
        self._vocab_df = vocab_df

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
        # REST and MCP (service.search) paths funnel through — so case/whitespace
        # ("C", " Rust ") match indexed values and a blank string degrades to
        # "no filter" consistently for every caller.
        if language is not None:
            language = language.strip().lower() or None

        if self._indexer.total_chunks == 0:
            return SearchResultList(), 0

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

        # UPG-NOTFOUND-FLOOR-2 (F46/F52): a raw pool cosine similarity is NOT a
        # usable absolute floor — measured against the production embedder, the
        # best pre-rerank cosine for a query naming a concept genuinely absent
        # from the corpus overlaps the same range as (and sometimes exceeds) the
        # best cosine for a real on-topic query; every sentence embedding lands
        # in a narrow band near the corpus centroid regardless of relevance, so
        # no absolute constant separates them (see config.yaml for the measured
        # evidence). A distributional, vocabulary-based question separates them
        # instead: does every content word IN THE QUERY occur, even once,
        # anywhere in the indexed corpus? A query naming something the codebase
        # truly does not contain is built from at least one word with zero
        # document frequency across the WHOLE corpus (not just this query's
        # fetched pool); a query about something the codebase does contain,
        # however badly it then gets ranked, is built entirely from words that
        # really do appear in it. query_tokens is also reused by BM25 below.
        # _code_tokenize_query dedupes (unlike the corpus-side tokenizer used
        # for BM25 documents) — see its docstring for why query-side repeats
        # should not double-count.
        query_tokens = _code_tokenize_query(query)
        zero_df_tokens: list[str] = []
        if _NOTFOUND_FLOOR_ENABLED and self._vocab_df:
            content_tokens = {
                t for t in query_tokens
                if len(t) >= _NOTFOUND_FLOOR_MIN_TOKEN_LEN
                and t not in _NOTFOUND_FLOOR_STOPWORDS
            }
            zero_df_tokens = [t for t in content_tokens if self._vocab_df.get(t, 0) == 0]

        # --- BM25 search ---
        bm25_scores: dict[str, float] = {}
        if self._bm25 is not None:
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
                # Placeholder — _apply_quality_and_dedup overwrites this with the
                # absolute relevance value (ce_relevance, else dense_sim) before
                # the result is returned (UPG-SCORE-DISPLAY-FLAT). Kept here only
                # so the field is never uninitialized if that step is skipped.
                score=round(merged[cid], 4),
                content=doc[:2000],
                node_type=meta.get("node_type", ""),
                # UPG-11.4: carry the exact line range of the indexed symbol so
                # callers can expand to the full definition without a blind re-read.
                symbol_start_line=start_line,
                symbol_end_line=end_line,
                # ARCH-4b: thread the chunk's purpose-vector similarity through to
                # the final sort (0.0 when the chunk never had a purpose vector).
                purpose_sim=purpose_scores.get(cid, 0.0),
                # UPG-SCORE-DISPLAY-FLAT: the chunk's own absolute dense cosine
                # similarity (body ⊔ purpose merge) — the displayed-score
                # fallback for candidates the cross-encoder never scores.
                dense_sim=dense_scores.get(cid, 0.0),
            ))

        # --- Cross-encoder rerank ---
        if rerank and self._reranker and len(candidates) > 1:
            doc_candidate_pairs = [(r.content, r) for r in candidates]
            candidates = self._reranker.rerank(query, doc_candidate_pairs)

        # --- Quality prior + dedup + deterministic tiebreaker (UPG-2.1/2.2/2.3) ---
        candidates = self._apply_quality_and_dedup(query, candidates, frozenset(query_tokens))

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        final = SearchResultList(candidates[:n_results])

        # UPG-SCORE-DISPLAY-FLAT: a second, independent low-confidence signal —
        # the top result's own displayed relevance is below an absolute floor.
        # This is gated on ce_relevance specifically (NOT the dense_sim
        # fallback): config.yaml's UPG-NOTFOUND-FLOOR-2 rationale already
        # established, with measurement against the production embedder, that
        # a raw bi-encoder cosine cannot separate absent-topic from on-topic
        # queries (both land in the same band near the corpus centroid) — so
        # reusing that same cosine here, when no reranker ran, would
        # reintroduce the exact defect that evidence ruled out. A
        # cross-encoder relevance score is a different, calibrated judgment
        # (trained to predict query-doc relevance directly rather than
        # measure embedding proximity), so it is safe to floor on. When no
        # reranker ran (rerank=False, or the model failed to load), this
        # sub-signal is simply absent and low_confidence falls back to the
        # zero-DF vocabulary floor alone (pre-UPG-SCORE-DISPLAY-FLAT behaviour).
        top_ce_relevance = final[0].ce_relevance if final else None
        low_top_relevance = (
            top_ce_relevance is not None
            and top_ce_relevance < _NOTFOUND_FLOOR_MIN_TOP_RELEVANCE
        )

        # low_confidence: only meaningful when there is something to lead with —
        # an empty result set has its own "no results" message downstream.
        final.low_confidence = (
            _NOTFOUND_FLOOR_ENABLED
            and bool(final)
            and (
                len(zero_df_tokens) >= _NOTFOUND_FLOOR_MIN_ZERO_DF_TOKENS
                or low_top_relevance
            )
        )
        return final, elapsed_ms

    def _apply_quality_and_dedup(
        self,
        query: str,
        candidates: list[SearchResult],
        query_tokens: frozenset[str] = frozenset(),
    ) -> list[SearchResult]:
        """Re-rank by relevance×quality, collapse duplicates, break ties deterministically.

        Pipeline: final_score = base_rerank_score * quality_score(chunk) *
        (1 + lambda_file * file_importance) (ARCH-1b) * (1 + lambda_class *
        class_importance) (ARCH-2) * (1 + lambda_purpose * purpose_sim *
        quality_score(chunk)) (ARCH-4b, quality-weighted by UPG-PREFIX-COMPOSE)
        * (1 + lambda_def * is_type_definition) (UPG-RUST-DEF-EVICTION / DEF-B).
        All four are relevance-gated multiplicative priors — multiplying by
        base_rerank_score means an irrelevant high-importance/high-purpose-sim/
        type-def chunk (base ≈ 0) is never lifted. When no importance map is
        installed (or a lambda = 0) that factor is 1.0 and the score falls back
        accordingly (all four absent → base × quality, pre-ARCH-1b). purpose_sim
        defaults to 0.0 on every SearchResult built outside a purpose-vector
        pool-entry rescue, so the ARCH-4b factor is an exact no-op for chunks
        with no purpose vector; is_type_definition is a pure content/node_type
        property computed fresh per chunk (chunk_quality.is_type_definition_chunk),
        so the DEF-B factor is an exact no-op for any non-type-definition chunk
        regardless of query.
        The purpose factor is additionally weighted by the chunk's own
        quality_score so a quality-demoted chunk's textual purpose-match (e.g. a
        test helper whose docstring happens to restate the query) cannot buy
        back more rank than an undemoted chunk with a smaller match — see the
        purpose_factor comment below for the worked case.
        Keeps exact-duplicate dedup and the quality_score call (with trivial/navigational/
        doc-language demotion intact).

        Relevance is taken from the candidates' current (post-rerank) order; a
        per-chunk quality prior then demotes trivial / navigational / generated /
        heading-only / test chunks (UPG-2.1, 2.3). Byte-identical chunks collapse
        to a single representative with a duplicate count so boilerplate can't flood
        the top-N (UPG-2.2); a second collapse key on the normalized leading
        docstring/comment block catches near-duplicate boilerplate headers that
        the byte-identical key misses (UPG-RUST-DEF-EVICTION / DEF-C).

        UPG-SCORE-DISPLAY-FLAT: `final_score` (the composite above) is an
        ORDERING KEY ONLY — it decides sort order and dedup precedence and is
        never displayed. The displayed `r.score` is instead set here, as the
        very last step, to an absolute per-(query, chunk) relevance value
        (r.ce_relevance when the cross-encoder scored this chunk, else
        r.dense_sim, the chunk's own dense cosine similarity) — see
        SearchResult's field docstrings. This ordering/display split is
        intentional: the composite blends quality and importance priors that
        deliberately move a chunk up or down from its raw relevance, so the
        displayed score is no longer guaranteed non-increasing down the
        returned list (a quality-demoted chunk can rank below a promoted one
        despite a higher absolute relevance) — that disagreement is honest
        information about why the ordering differs from a pure-relevance sort,
        not a defect.
        """
        if not candidates:
            return candidates
        n = len(candidates)
        scored: list[tuple[float, float, int, SearchResult]] = []
        for i, r in enumerate(candidates):
            base = 1.0 - (i / n)  # rank-based relevance, best-first
            fan_in = self._file_fan_in.get(r.file_path, 0) if self._file_fan_in else 0
            q = quality_score(
                r.content, r.file_path, r.language, r.node_type,
                query_tokens=query_tokens, file_fan_in=fan_in,
                symbol_name=r.symbol_name,
            )
            # Class context recovered from the indexer-injected "# class: X" prefix
            # in the chunk content (chunk_quality.extract_class_from_content) — the
            # AST-based chunker derives this from actual span containment (UPG-F4),
            # so no separate containment computation is needed here. Used both to
            # promote symbol_name (UPG-11.10, presentational) and to look up the
            # chunk's owning-class importance below (ARCH-2, affects ranking).
            class_ctx = extract_class_from_content(r.content)
            # UPG-11.10: promote symbol_name to the qualified "Class.leaf" form.
            # Presentational only — it makes the REST/MCP `symbol` field show
            # "Field.deconstruct" instead of the bare "deconstruct" so callers see
            # which class owns the chunk. Does not affect ranking.
            if class_ctx and r.symbol_name and "." not in r.symbol_name and "::" not in r.symbol_name:
                r.symbol_name = f"{class_ctx}.{r.symbol_name}"
            # ARCH-1b: relevance-gated file-importance prior. importance ∈ [0,1];
            # factor ∈ [1, 1+lambda]. No-op when no map installed or lambda = 0.
            imp_file = self._file_importance.get(r.file_path, 0.0) if self._file_importance else 0.0
            file_factor = 1.0 + _IMPORTANCE_PRIOR_LAMBDA * imp_file
            # ARCH-2: relevance-gated class-importance prior — same shape as
            # ARCH-1b, at class granularity. No-op when the chunk has no class
            # context, no map is installed, or lambda = 0.
            imp_class = self._class_importance.get(class_ctx, 0.0) if class_ctx and self._class_importance else 0.0
            class_factor = 1.0 + _CLASS_IMPORTANCE_PRIOR_LAMBDA * imp_class
            # ARCH-4b: relevance-gated purpose-similarity prior — carries the
            # ARCH-4 dual-vector pool-entry signal into the FINAL sort, which the
            # body-only cross-encoder rerank cannot see. purpose_sim is already
            # normalized to [0,1] (query_vector_purpose distances are clamped the
            # same way body vec_scores are) and defaults to 0.0 for any chunk with
            # no purpose vector, making the factor an exact no-op (1.0) for it.
            #
            # UPG-PREFIX-COMPOSE: purpose_sim measures textual similarity between
            # the query and a chunk's own qualified-name+docstring, independent of
            # the chunk's quality tier — a demoted chunk (a test helper whose
            # docstring happens to restate the query terms, e.g. "Helper class to
            # track threads and kwargs when signals are dispatched") can carry a
            # HIGHER purpose_sim than the canonical production symbol it
            # duplicates in name, letting a purely textual echo outrank the real
            # implementation no matter how large lambda is scaled (the deep-bury
            # rescue this prior exists for assumes the buried candidate IS the
            # best answer, which a test-file near-duplicate is not). Weighting by
            # the chunk's own quality_score scales trust in that textual echo by
            # the same quality tier the base rerank score is already gated by,
            # so a demoted chunk's purpose match can no longer buy back more rank
            # than an undemoted chunk with a smaller match — the prior still
            # rescues genuinely low-ranked-but-canonical symbols (quality=1.0
            # candidates keep the full boost) without letting quality-demoted
            # look-alikes leapfrog them.
            purpose_factor = 1.0 + _PURPOSE_RANK_PRIOR_LAMBDA * r.purpose_sim * q
            # DEF-B (UPG-RUST-DEF-EVICTION): relevance-gated type-definition
            # node_type prior. is_type_definition is a pure chunk PROPERTY
            # (node_type/content/language only — never the query), so this
            # factor is an exact no-op (1.0) for any chunk that is not a
            # struct/enum/trait/class/interface definition.
            is_type_def = is_type_definition_chunk(r.node_type, r.content, r.language)
            def_factor = 1.0 + _TYPE_DEF_PRIOR_LAMBDA * (1.0 if is_type_def else 0.0)
            scored.append((base * q * file_factor * class_factor * purpose_factor * def_factor, q, i, r))

        # Deterministic order: final score desc, quality desc, length desc, then
        # original rank, then path — so equal-scoring boilerplate never wins by
        # unstable sort order.
        scored.sort(key=lambda t: (-t[0], -t[1], -len(t[3].content), t[2], t[3].file_path))

        # UPG-PREFIX-COMPOSE (F57): the importance/purpose priors are each
        # (1 + lambda * x) with x >= 0, so their product can push the raw
        # composite above 1.0 even though base and quality are each <= 1.0
        # (F12-score-exceeds-unity). This is harmless now that the composite
        # is used purely as an ordering/dedup key and is never displayed
        # (UPG-SCORE-DISPLAY-FLAT) — no clamp or per-result-set normalization
        # is needed; `scored` below is consumed only for its sort order.

        # UPG-RUST-DEF-EVICTION / DEF-C: two independent dedup keys — the
        # existing byte-identical full-content key (UPG-2.2), and a new
        # near-duplicate key on the chunk's own normalized leading docstring/
        # comment block. Either key matching an already-seen representative
        # collapses the chunk into it (dup_count on the best-ranked survivor,
        # since `scored` is already sorted best-first). A chunk with no
        # leading docstring (leading_docstring_key returns "") is NEVER
        # collapsed by the docstring key — only the content key can catch it.
        seen_content: dict[str, int] = {}
        seen_docstring: dict[str, int] = {}
        out: list[SearchResult] = []
        for _final_score, _, _, r in scored:
            content_key = normalized_content(r.content)
            doc_key = leading_docstring_key(r.content, r.language)
            existing_idx = seen_content.get(content_key)
            if existing_idx is None and doc_key:
                existing_idx = seen_docstring.get(doc_key)
            if existing_idx is not None:
                out[existing_idx].dup_count += 1
                continue
            idx = len(out)
            seen_content[content_key] = idx
            if doc_key:
                seen_docstring[doc_key] = idx
            r.dup_count = 0
            # UPG-SCORE-DISPLAY-FLAT: the displayed score is the absolute
            # per-(query, chunk) relevance — the cross-encoder's judgment when
            # this chunk was reranked, else its dense cosine similarity. Both
            # are already in [0, 1] and already absolute (never re-derived
            # from this result set's own rank/composite), so no further
            # normalization is applied here.
            absolute_relevance = r.ce_relevance if r.ce_relevance is not None else r.dense_sim
            r.score = round(absolute_relevance, 4)
            out.append(r)
        return out
