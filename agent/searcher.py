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
    is_doc_intent_query,
    is_trivial_chunk,
    symbol_identity_boost,
    extract_class_from_content,
    _query_symbol_tokens,
)
from agent.config import (
    SYMBOL_STOP_WORDS as _SYM_STOP_WORDS_VAL,
    SYMBOL_MIN_LEAF_LEN as _SYM_MIN_LEAF_LEN_VAL,
    FORCED_INCLUSION_MAX as _FORCED_INCLUSION_MAX,
    FORCED_INCLUSION_MIN_IDENTIFIER_LEN as _FORCED_INCLUSION_MIN_IDENTIFIER_LEN,
    FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR as _FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR,
    FORCED_INCLUSION_VEC_SIM_FLOOR as _FORCED_INCLUSION_VEC_SIM_FLOOR,
    FORCED_INCLUSION_SHORT_VERB_ALLOWLIST as _FORCED_INCLUSION_SHORT_VERB_ALLOWLIST,
    RERANK_TOP_K as _RERANK_TOP_K,
    RERANK_TOP_K_UNFILTERED as _RERANK_TOP_K_UNFILTERED,
    RERANK_PRE_FILTER_FETCH_K as _RERANK_PRE_FILTER_FETCH_K,
    DOC_INTENT_SUPPRESS_FORCED_INCLUSION as _DOC_INTENT_SUPPRESS_FORCED_INCLUSION,
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

# UPG-11.7 / UPG-11.12: forced-inclusion tunables — sourced from agent/config.yaml
# (ranking.forced_inclusion block) via agent/config.py.  See config.yaml for the
# full rationale comments.  The names below are thin aliases kept for readability
# at the call sites; all four values are imported from config at module load.
#
# Summary of what each controls:
#   _FORCED_INCLUSION_MAX               — safety cap on pool additions (default 200)
#   _FORCED_INCLUSION_MIN_IDENTIFIER_LEN — min bare-token length to trigger (default 7)
#   _FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR — BM25 fast-reject floor for non-compound
#                                             tokens, 5% of max-raw (default 0.05)
#   _FORCED_INCLUSION_VEC_SIM_FLOOR     — cosine similarity gate for non-compound
#                                         tokens (default 0.52)
#
# (imported above from agent.config)


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
    forced_inclusion: bool = False  # UPG-11.7: chunk was forced into pool by symbol-name match
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

        # UPG-11.11: classify query intent once so both forced-inclusion suppression
        # and quality-score adjustment are consistent within a single search call.
        _is_doc_intent = is_doc_intent_query(query)

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

        # Build the content/language lookup from already-fetched data.
        # Used by the pool-entry trivial filter below and later for result objects.
        # No extra ChromaDB round-trips: both sources (vec and BM25) were fetched above.
        id_to_doc: dict[str, str] = dict(zip(vec_ids, vec_docs))
        id_to_meta: dict[str, dict] = dict(zip(vec_ids, vec_metas))
        for idx, cid in enumerate(self._bm25_ids):
            if cid not in id_to_doc:
                id_to_doc[cid] = self._bm25_docs[idx]
                id_to_meta[cid] = self._bm25_metas[idx]

        # --- UPG-15.7: Pool-entry trivial filter ---
        # Drop trivial chunks from the over-fetched pool before the cross-encoder runs,
        # then trim to top_k_unfiltered.  Forced-inclusion candidates (added after this
        # point) are never subject to this filter — they are inserted deliberately.
        # Only fires for unfiltered queries (language is None) because the over-fetch
        # is only applied there; filtered queries already use the smaller top_k pool.
        if language is None and rerank and self._reranker:
            non_trivial_ids: list[str] = []
            for cid in sorted_ids:
                doc = id_to_doc.get(cid, "")
                lang = (id_to_meta.get(cid) or {}).get("language", "")
                if is_trivial_chunk(doc, lang):
                    continue
                non_trivial_ids.append(cid)
                if len(non_trivial_ids) >= _RERANK_TOP_K_UNFILTERED:
                    break
            sorted_ids = non_trivial_ids

        forced_cids: set[str] = set()  # UPG-11.7: chunks added by forced-inclusion

        # --- UPG-11.7: Forced-inclusion of exact symbol-name matches ---
        # When the query names a specific symbol (e.g. "Field deconstruct …" or
        # "from_db_value …"), the canonical base-class definition may have a long
        # docstring that dilutes both embedding similarity AND BM25 keyword density,
        # causing it to fall outside the top-fetch_n slice.  The UPG-11.1 ranking
        # boost is applied POST-retrieval and cannot rescue a chunk that never entered
        # the pool.  Solution: union ALL chunks whose stored symbol_name leaf exactly
        # matches a guarded query token into sorted_ids BEFORE the candidate list is
        # built, so the reranker + quality prior can place the right one at the top.
        #
        # Guard: stricter than UPG-11.1 (which guards ranking boost at min_leaf_len=4).
        # Forced-inclusion directly adds chunks to the pool, so we require tokens to
        # look like explicit identifier references — either compound (containing "_",
        # e.g. from_db_value) or long enough to be specific (≥ _FORCED_INCLUSION_MIN_IDENTIFIER_LEN).
        # Short prose words like "name", "path", "args", "field" (≤6 chars, no underscore)
        # are excluded so common Python attribute names don't flood the candidate pool
        # and overpower real results via the UPG-11.1 sym_boost they'd inherit.
        # Additionally: case-SENSITIVE leaf match avoids matching class names like
        # "Migration" against the lowercase token "migration" from the query prose.
        # Safety cap: _FORCED_INCLUSION_MAX total additional chunks prevents flooding
        # on pathological corpora where a common method name appears in thousands of
        # files (a real-world codebase typically has ≤100 same-named methods).
        #
        # UPG-11.11: Doc-intent queries (e.g. "how to …", "explain …", "tutorial")
        # suppress forced-inclusion entirely.  On a doc-intent query, symbol names
        # appearing in the query describe the *topic* — not a request for the symbol
        # implementation — so flooding the pool with 80+ same-named code chunks would
        # bury the documentation the user is actually asking for (F2).
        if self._bm25 is not None and not (_is_doc_intent and _DOC_INTENT_SUPPRESS_FORCED_INCLUSION):
            _all_query_sym_toks = _query_symbol_tokens(query)
            sym_tokens_for_inclusion = {
                tok for tok in _all_query_sym_toks
                if (
                    "_" in tok  # compound identifier: from_db_value, get_queryset …
                    or len(tok) >= _FORCED_INCLUSION_MIN_IDENTIFIER_LEN  # long specific word
                )
                and tok not in _SYM_STOP_WORDS_VAL
            }
            # UPG-11.14 / F13: short-verb allowlist bypass.
            # Verbs like "save", "get", "create" are below min_identifier_len AND in
            # prog_stopwords.txt (so they are excluded by both guards above).  But when
            # they appear in the query they frequently name the exact ORM/API method the
            # user wants (e.g. "save a model instance to the database" → Model.save).
            # The allowlist reinstates forced-inclusion for these verbs ONLY; they are
            # treated as NON-COMPOUND candidates so the BM25 floor + vec_sim_floor
            # relevance gate still applies — irrelevant overrides are rejected by the gate.
            #
            # UPG-15.3 / F17: sub-token guard.
            # _query_symbol_tokens expands compound identifiers like "on_delete" into
            # sub-tokens {"on_delete", "on", "delete"}.  The allowlist must only fire
            # for a short verb that the user typed as a STANDALONE word in the query,
            # not for one that appears only as a sub-token of a longer compound already
            # present.  Otherwise "on_delete" → sub-token "delete" → forced-inclusion of
            # every cache/storage .delete() method, burying the actual ForeignKey code.
            # _standalone_query_words splits on non-identifier boundaries without any
            # subsequent underscore/camelCase expansion — so "on_delete" is one token
            # and the bare "delete" sub-token is absent from the standalone set.
            _standalone_query_words = {
                tok.lower()
                for tok in re.split(r"[^a-zA-Z0-9_]+", query)
                if len(tok) >= 2
            }
            # UPG-15.4: tokens the user typed with identifier casing — i.e. tokens
            # containing an underscore OR at least one uppercase letter.  Used below to
            # guard CamelCase leaf matches: a CamelCase symbol leaf is force-included
            # only when the user wrote that identifier with identifier casing in the
            # query (e.g. "ForeignKey"), not when they used it as lowercase prose
            # (e.g. "migration" must not force-include class Migration).
            _ident_cased_query_toks = {
                tok.lower()
                for tok in re.split(r"[^a-zA-Z0-9_]+", query)
                if len(tok) >= 2 and ("_" in tok or any(c.isupper() for c in tok))
            }
            for tok in _all_query_sym_toks:
                if (
                    tok in _FORCED_INCLUSION_SHORT_VERB_ALLOWLIST
                    and tok in _standalone_query_words
                ):
                    sym_tokens_for_inclusion.add(tok)
            if sym_tokens_for_inclusion:
                sorted_set = set(sorted_ids)
                # Use the already-computed full query symbol tokens for the qualified
                # boost check (class-name prefix matching, e.g. "field" from
                # "Field deconstruct …" to qualify Field.deconstruct).
                all_query_sym_tokens = _all_query_sym_toks

                # UPG-11.12: relevance gate for NON-COMPOUND forced candidates.
                # Precompute BM25-without-trigger scores for each non-compound token.
                # Compound tokens (containing "_") keep firing unconditionally.
                non_compound_bm25_without: dict[str, list[float]] = {}
                for tok in sym_tokens_for_inclusion:
                    if "_" in tok:
                        continue
                    tokens_without = [t for t in query_tokens if t != tok]
                    if not tokens_without:
                        continue  # query was only the trigger token — no constraint
                    raw_without = self._bm25.get_scores(tokens_without)
                    max_raw_without = max(raw_without) if len(raw_without) > 0 else 0.0
                    if max_raw_without > 0:
                        non_compound_bm25_without[tok] = [s / max_raw_without for s in raw_without]
                    else:
                        non_compound_bm25_without[tok] = [0.0] * len(raw_without)

                # Pass 1: collect raw forced candidates, separating non-compound
                # (which need the relevance gate) from compound (unconditional).
                # raw_non_compound: list of (idx, cid, leaf) for gate checking.
                raw_non_compound: list[tuple[int, str, str]] = []
                confirmed_forced: list[tuple[float, str]] = []  # (pseudo_score, cid)

                for idx, meta in enumerate(self._bm25_metas):
                    if len(raw_non_compound) + len(confirmed_forced) >= _FORCED_INCLUSION_MAX:
                        break
                    cid = self._bm25_ids[idx]
                    if cid in sorted_set:
                        continue
                    if language and meta.get("language") != language:
                        continue
                    sym = meta.get("symbol_name", "") or ""
                    # Case-insensitive bare leaf comparison (UPG-15.4).
                    leaf = sym.split(".")[-1].split("::")[-1]
                    leaf_lower = leaf.lower()
                    if not leaf or leaf_lower not in sym_tokens_for_inclusion:
                        continue
                    # Case discipline: a leaf with uppercase characters is a CamelCase /
                    # Pascal class symbol.  Force-include it only when the user typed that
                    # identifier with identifier casing (CamelCase or underscore) in the
                    # query — not when it appears only as lowercase prose.  This preserves
                    # the UPG-11 guard: the word "migration" (no uppercase, no underscore)
                    # must NOT force-include class Migration, while a query that literally
                    # names "ForeignKey" (CamelCase) should surface class ForeignKey.
                    if leaf != leaf_lower and leaf_lower not in _ident_cased_query_toks:
                        continue

                    if "_" in leaf:
                        # Compound identifier → unconditional forced-inclusion.
                        doc = self._bm25_docs[idx]
                        eff_sym = sym
                        if eff_sym and "." not in eff_sym and "::" not in eff_sym:
                            class_ctx = extract_class_from_content(doc)
                            if class_ctx:
                                eff_sym = f"{class_ctx}.{eff_sym}"
                        confirmed_forced.append((symbol_identity_boost(eff_sym, all_query_sym_tokens), cid))
                    else:
                        # Non-compound identifier: apply BM25-without-trigger as a
                        # fast-reject filter.  Chunks with near-zero BM25 score for
                        # the full query minus the trigger token (e.g.
                        # LinearGeometryMixin.project for "…migrations for a project"
                        # where removing "project" leaves migration/database terms
                        # absent from the geometry chunk) are immediately rejected.
                        # Chunks with any non-trivial BM25 overlap are deferred to
                        # the vector cosine check (pass 2).
                        if leaf_lower in non_compound_bm25_without:
                            scores_arr = non_compound_bm25_without[leaf_lower]
                            bm25_without = scores_arr[idx] if idx < len(scores_arr) else 0.0
                        else:
                            bm25_without = 1.0  # no constraint → defer to vector check
                        if bm25_without < _FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR:
                            continue  # fast-reject: zero relevant keyword overlap
                        # Non-trivial BM25 OR unconstrained → defer to vector check.
                        raw_non_compound.append((idx, cid, leaf))

                # Pass 2: batch-fetch vector cosine similarities for all non-compound
                # candidates that survived the BM25 fast-reject.
                # One ChromaDB call covers all of them (capped at _FORCED_INCLUSION_MAX).
                # The vector check is the primary gate: a forced candidate must be
                # semantically relevant to the FULL query — not just share a symbol
                # name with one query token.
                if raw_non_compound:
                    deferred_ids = [cid for _, cid, _ in raw_non_compound]
                    vec_sims = self._indexer.get_chunk_cosine_similarities(
                        query_embedding, deferred_ids
                    )
                    for idx, cid, leaf in raw_non_compound:
                        sim = vec_sims.get(cid, 0.0)
                        if sim < _FORCED_INCLUSION_VEC_SIM_FLOOR:
                            continue  # vector gate: not semantically relevant to full query
                        # Passes both gates — include in forced pool.
                        sym = self._bm25_metas[idx].get("symbol_name", "") or ""
                        doc = self._bm25_docs[idx]
                        eff_sym = sym
                        if eff_sym and "." not in eff_sym and "::" not in eff_sym:
                            class_ctx = extract_class_from_content(doc)
                            if class_ctx:
                                eff_sym = f"{class_ctx}.{eff_sym}"
                        confirmed_forced.append((symbol_identity_boost(eff_sym, all_query_sym_tokens), cid))

                # Sort and commit all confirmed forced candidates.
                forced_candidates = confirmed_forced
                forced_candidates.sort(key=lambda t: t[0], reverse=True)
                for pseudo_score, cid in forced_candidates:
                    if cid not in sorted_set:
                        merged[cid] = pseudo_score
                        sorted_ids.append(cid)
                        sorted_set.add(cid)
                        forced_cids.add(cid)

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
                forced_inclusion=cid in forced_cids,
                # UPG-11.4: carry the exact line range of the indexed symbol so
                # callers can expand to the full definition without a blind re-read.
                symbol_start_line=start_line,
                symbol_end_line=end_line,
            ))

        # --- Cross-encoder rerank ---
        # UPG-11.7: separate forced-inclusion candidates so the cross-encoder
        # does not bury them.  Forced chunks often have long docstrings that
        # dilute cross-encoder similarity despite being the canonical definition.
        # We cross-encode only the naturally-retrieved pool; forced chunks are
        # inserted BEFORE the cross-encoded results so _apply_quality_and_dedup
        # sees them at early rank positions where the +0.20 qualified sym_boost
        # can lift them above overrides (which get +0.10).
        if rerank and self._reranker and len(candidates) > 1:
            forced_results = [r for r in candidates if r.forced_inclusion]
            regular_results = [r for r in candidates if not r.forced_inclusion]
            if regular_results:
                doc_candidate_pairs = [(r.content, r) for r in regular_results]
                regular_results = self._reranker.rerank(query, doc_candidate_pairs)
            # Prepend forced results so they get high rank-based relevance in
            # _apply_quality_and_dedup. The sym_boost will further separate
            # qualified forced matches (Field.deconstruct, +0.20) from leaf-only
            # forced matches (CharField.deconstruct, +0.10).
            candidates = forced_results + regular_results

        # --- Quality prior + dedup + deterministic tiebreaker (UPG-2.1/2.2/2.3) ---
        candidates = self._apply_quality_and_dedup(query, candidates, is_doc_intent=_is_doc_intent)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return candidates[:n_results], elapsed_ms

    def _apply_quality_and_dedup(
        self, query: str, candidates: list[SearchResult],
        *, is_doc_intent: bool = False,
    ) -> list[SearchResult]:
        """Re-rank by relevance×quality, collapse duplicates, break ties deterministically.

        Relevance is taken from the candidates' current (post-rerank) order; a
        per-chunk quality prior then demotes trivial / navigational / generated /
        heading-only / (off-topic) test chunks (UPG-2.1, 2.3). Byte-identical
        chunks collapse to a single representative with a duplicate count so
        boilerplate can't flood the top-N (UPG-2.2).

        Args:
            is_doc_intent: When True, documentation prose chunks are scored with
                the elevated ``DOC_INTENT_DOC_PROSE_MULTIPLIER`` (default 1.0) so
                they can compete with code on how-to/explain queries (UPG-11.11).
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
                query_is_doc_intent=is_doc_intent,
            )
            # Symbol-identity bonus: additive boost when the candidate's symbol
            # name matches the query's symbol intent (UPG-11.1).
            #
            # F4 (UPG-11.1-fix): the indexer stores symbol_name as the bare leaf
            # (e.g. "deconstruct"), never "Class.leaf".  The qualified-match path
            # (+0.20) was therefore dead code.  Fix: when the stored symbol_name
            # has no dot/colon qualifier, extract the class name from the indexer-
            # injected "# class: X" prefix in the chunk content and pass the
            # reconstructed "X.leaf" form to symbol_identity_boost.  This works
            # for already-indexed corpora with no reindex required.
            effective_symbol = r.symbol_name
            if effective_symbol and "." not in effective_symbol and "::" not in effective_symbol:
                class_ctx = extract_class_from_content(r.content)
                if class_ctx:
                    effective_symbol = f"{class_ctx}.{effective_symbol}"
            sym_boost = symbol_identity_boost(effective_symbol, sym_tokens)
            # UPG-11.10: promote symbol_name to the qualified "Class.leaf" form when
            # class context was successfully extracted from the indexer-injected prefix.
            # This makes the REST/MCP `symbol` field show "Field.deconstruct" instead
            # of the bare "deconstruct", helping callers understand which class owns the chunk.
            if effective_symbol and effective_symbol != r.symbol_name:
                r.symbol_name = effective_symbol
            scored.append((base * q + sym_boost, q, i, r))

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
            # UPG-11.13: clamp to [0, 1] so callers with a confidence gate (score > 0.8)
            # don't get false positives from sym_boost (which can push the raw composite
            # above 1.0, e.g. base*quality=1.0 + qualified_boost=0.20 → 1.2).
            # The sort above uses final_score (unclamped) so rank order is preserved.
            r.score = round(min(1.0, final_score), 4)
            out.append(r)
        return out
