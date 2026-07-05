"""vectr bundled-config loader.

Reads ``agent/config.yaml`` at import time and caches the results.  All paths
are resolved via ``importlib.resources`` so this works both when running from
the repository root *and* when vectr is installed as the global binary
(``/opt/homebrew/bin/vectr`` or similar pip install), where a ``cwd``-relative
``open()`` would fail.

Public API
----------
QUALITY_TRIVIAL : float
    Quality prior for bare stub chunks (UPG-12.1).

QUALITY_NAVIGATIONAL : float
    Quality prior for re-export / import-only navigational chunks (UPG-12.1).

QUALITY_NAV_DECLARATION_RESCUE : float
    Softened quality prior for a bare-constructor-manifest navigational chunk
    when the query lexically names one of the identifiers it declares
    (UPG-NAV-OVERDEMOTE-DECL / F59). Applied instead of QUALITY_NAVIGATIONAL
    only for that lexically-gated case; every other query still gets the full
    navigational demotion.

QUALITY_HEADING_ONLY : float
    Quality prior for markdown heading-only chunks (UPG-12.1).

QUALITY_GENERATED : float
    Quality prior for machine-generated files (UPG-12.1).

QUALITY_VECTR_CONFIG : float
    Quality prior for vectr's own config files (UPG-12.1).

QUALITY_TEST_DEPRIORITISED : float
    Quality prior for test files (UPG-12.1).

TEST_FRAMEWORK_FAN_IN_THRESHOLD : int
    Minimum unambiguous corpus-wide caller-file count (symbol_graph.file_fan_in())
    above which a test-path-classified file is exempted from
    QUALITY_TEST_DEPRIORITISED (UPG-TESTPATH-FRAMEWORK-MISCLASS / F58) — it is a
    shipped testing-framework subpackage, not disposable test code.

QUALITY_DOC_PROSE : float
    Quality prior for documentation prose chunks (UPG-12.1).

QUALITY_SHORT_PENALTY : float
    Quality prior for chunks with very few meaningful lines (UPG-12.1).

QUALITY_PRIVATE_SYMBOL : float
    Quality prior for a private/internal symbol (single leading underscore,
    not a dunder) — a language-general naming convention, not corpus- or
    query-specific (UPG-16.1 / F30).

TRIVIAL_DOC_MAX_LINES : int
    Maximum non-blank lines for an HTML/markup or plain-text chunk to be
    classified as trivial by is_trivial_chunk() (UPG-15.5). 1–2-line test
    fixture templates and egg-info TXT files are trivial; multi-line .rst/.txt
    docs are not affected.

RERANK_TOP_K : int
    Number of hybrid candidates to rerank before trimming to n_results (UPG-12.1).

RERANK_TOP_K_UNFILTERED : int
    Deeper candidate pool for unfiltered (no language filter) queries (UPG-12.1).

RERANK_PRE_FILTER_FETCH_K : int
    Over-fetch depth for the pool-entry trivial filter (UPG-15.7). The hybrid
    retrieval fetches this many raw candidates, drops trivial (non-forced) chunks
    via is_trivial_chunk(), then trims to top_k_unfiltered before the cross-encoder
    runs. Ensures the rerank pool is filled with real code on fixture-heavy corpora.

INDEXING_MAX_CHUNK_LINES : int
    Hard cap on lines per chunk — prevents single huge chunks diluting embeddings (UPG-12.1).

INDEXING_CLASS_HEADER_LINES : int
    Lines kept for class-level chunk (sig + docstring + attrs) (UPG-12.1).

OUTPUT_SNIPPET_LINES : int
    Lines returned as a snippet with each symbol location (UPG-12.1).

BEHAVIOR_REMEMBER_NUDGE_THRESHOLD : int
    Tool calls without vectr_remember before a nudge fires (UPG-12.1).

BEHAVIOR_REMEMBER_NUDGE_COOLDOWN : int
    Calls between repeated nudges after the threshold first fires (UPG-12.1).

EVICTION_RETRIEVED_TOKEN_GATE : int
    Minimum accumulated retrieved-token estimate since the last auto-eviction hint
    before auto_eviction_hint() will emit. Suppresses the hint on bursts of tiny
    searches that contribute negligible context pressure (UPG-11.15).

INGEST_TRACES_MAX_UNRESOLVED_EXAMPLES : int
    Maximum unresolved caller/callee example strings surfaced in the
    vectr_ingest_traces response (UPG-7.3). Edges are ingested regardless;
    this only bounds the warning text length.

LOCATE_LARGE_SPAN_THRESHOLD : int
    Line-span (end_line - start_line) at or above which a located symbol is
    considered "large" — typically a canonical library class or function (UPG-15.10).
    Symbols with span >= this value get the best (lowest) span bucket in locate
    ranking, so canonical 1000+ line base classes rank before tiny test stubs.

LOCATE_SMALL_SPAN_THRESHOLD : int
    Line-span below which a located symbol is considered "tiny" — a stub or inner
    test class (UPG-15.10). Symbols with span < this value get the worst (highest)
    span bucket in locate ranking, penalising 2–5-line test-inner stub classes.

IMPORTANCE_PRIOR_LAMBDA : float
    Blend weight for the file-level PageRank importance prior in the final search
    sort (ARCH-1b): final_score = base_rerank_score * quality_score * (1 + lambda *
    importance). 0 disables (pre-ARCH-1b behaviour). Relevance-gated by the multiply
    against base_rerank_score.

CLASS_IMPORTANCE_PRIOR_LAMBDA : float
    Blend weight for the class-level reference-frequency importance prior in the
    final search sort (ARCH-2), composed with IMPORTANCE_PRIOR_LAMBDA:
    final_score = base_rerank_score * quality_score * (1 + lambda_file * file_imp)
    * (1 + lambda_class * class_imp). 0 disables (pre-ARCH-2 behaviour). The lever
    that discriminates same-leaf method collisions file-level importance cannot.

PURPOSE_RANK_PRIOR_LAMBDA : float
    Blend weight for the ARCH-4 dual-vector purpose-similarity prior in the final
    search sort (ARCH-4b), composed with the two priors above: final_score =
    base_rerank_score * quality_score * (1 + lambda_file * file_imp) * (1 +
    lambda_class * class_imp) * (1 + lambda_purpose * purpose_sim). purpose_sim is
    the chunk's own body-vs-purpose cosine similarity (0 when no purpose vector
    exists for the chunk). 0 disables (pre-ARCH-4b behaviour). Carries the ARCH-4
    pool-entry signal into the final rank, which the body-only cross-encoder
    rerank cannot see.

INDEXING_FLOW_SCAN_HEAD_BYTES : int
    Bytes scanned from the start of a `.js` file when detecting Flow type syntax
    (UPG-JSFLOW-SYMBOLS). A header scan, not a full-file walk.

INDEXING_FLOW_PRAGMA : str
    Primary Flow-detection signal — the `@flow` pragma (UPG-JSFLOW-SYMBOLS).

INDEXING_FLOW_SECONDARY_MARKERS : tuple[str, ...]
    Secondary Flow-detection signals — Flow-only import syntax (UPG-JSFLOW-SYMBOLS).

SYMBOL_GRAPH_RESERVED_KEYWORDS : dict[str, frozenset[str]]
    Per-language keyword sets that must never be minted as a symbol name or
    call-edge target — guards against a desynced/ERROR-node parse misattributing
    a keyword token as an identifier (UPG-JSFLOW-SYMBOLS).

WORKSPACE_DEFAULT_VECTRIGNORE_DIRS : tuple[str, ...]
    Directory names seeded into a fresh .vectrignore on first `vectr start`/`vectr
    init` when the workspace has none yet (UPG-13.2). Never overwrites an existing
    .vectrignore.

WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S : float
    Seconds between CodeWatcher's shallow top-level-only rescans, used to pick up
    new top-level directories/files since the watcher never watches the workspace
    root itself (UPG-13.1/13.3).

STRATEGY_DEFAULT_SEMANTIC_WEIGHT : float
STRATEGY_DEFAULT_BM25_WEIGHT : float
    Fallback hybrid-search weights used before the first index-time codebase
    fingerprint has run (UPG-8.2). Keeps `search` and `status` deterministic
    from the first call instead of the weight fields being silently absent.
DUAL_VECTOR_ENABLED : bool
    Master switch for the ARCH-4 per-symbol purpose-vector pool-entry mechanism.
    True stores + queries a second body-stripped "purpose" embedding (qualified
    signature + docstring) per symbol chunk. False reduces to pre-ARCH-4
    body-only behaviour.

DUAL_VECTOR_BLEND_MODE : str
    How a chunk's body and purpose similarity scores combine into one dense
    score: "max" (default, non-averaging pool-entry rescue) or "weighted".

DUAL_VECTOR_BLEND_WEIGHT : float
    Purpose-vector weight when DUAL_VECTOR_BLEND_MODE == "weighted".

DUAL_VECTOR_MAX_SIGNATURE_LINES : int
    Maximum declaration lines captured when distilling a chunk's purpose text.

DUAL_VECTOR_MAX_DOCSTRING_LINES : int
    Maximum docstring/leading-comment lines captured when distilling a chunk's
    purpose text.

DUAL_VECTOR_MAX_DOCSTRING_CHARS : int
    Maximum characters kept from the captured docstring/leading-comment text.

NOTFOUND_FLOOR_ENABLED : bool
    Master switch for the UPG-NOTFOUND-FLOOR low-confidence signal (F46).
    False is an exact no-op: low_confidence is always False, restoring
    pre-UPG-NOTFOUND-FLOOR behaviour.

NOTFOUND_FLOOR_MIN_TOKEN_LEN : int
    Minimum length (chars) for a query token to be considered a content word
    in the zero-document-frequency check (UPG-NOTFOUND-FLOOR-2).

NOTFOUND_FLOOR_STOPWORDS : frozenset[str]
    Generic English/query-scaffolding words excluded from the zero-document-
    frequency check.

NOTFOUND_FLOOR_MIN_ZERO_DF_TOKENS : int
    Minimum number of query content tokens with zero corpus-wide document
    frequency (i.e. the token never appears in ANY indexed chunk, not just
    the query's fetched candidate pool) required to flag a search's result
    set low_confidence. Replaces the absolute-cosine floor (dense_score_floor)
    of the first UPG-NOTFOUND-FLOOR iteration, which measurement showed
    cannot separate absent-topic from on-topic queries against the
    production embedder — see config.yaml for the evidence.

NOTFOUND_FLOOR_BANNER : str
    Low-confidence banner text prepended to the MCP vectr_search response
    when the floor fires.
"""
from __future__ import annotations

import importlib.resources as _ilr
from typing import Any

import yaml as _yaml


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_bundled_text(resource_path: str) -> str:
    """Read a text file bundled inside the ``agent`` package.

    ``resource_path`` is relative to the ``agent`` package root, using forward
    slashes (e.g. ``"config.yaml"``).  We use
    ``importlib.resources.files()`` (Python 3.9+) so the file is found whether
    the package is on-disk or inside a zip/wheel.
    """
    parts = resource_path.split("/")
    # Start from the agent package anchor.
    pkg = _ilr.files(__name__.rsplit(".", 1)[0] if "." in __name__ else __name__)
    resource = pkg
    for part in parts:
        resource = resource.joinpath(part)  # type: ignore[arg-type]
    return resource.read_text(encoding="utf-8")


def _load_config() -> dict[str, Any]:
    """Load and return the parsed agent/config.yaml as a dict."""
    raw = _read_bundled_text("config.yaml")
    return _yaml.safe_load(raw)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Eagerly load once at import; cache in module-level constants.
# ---------------------------------------------------------------------------

_cfg = _load_config()

# ---------------------------------------------------------------------------
# Quality priors — ranking multipliers (UPG-12.1)
# ---------------------------------------------------------------------------

_qp_cfg: dict[str, Any] = _cfg["ranking"]["quality_priors"]

QUALITY_TRIVIAL: float = float(_qp_cfg["trivial"])
QUALITY_NAVIGATIONAL: float = float(_qp_cfg["navigational"])
QUALITY_NAV_DECLARATION_RESCUE: float = float(_qp_cfg["navigational_declaration_rescue"])
QUALITY_HEADING_ONLY: float = float(_qp_cfg["heading_only"])
QUALITY_GENERATED: float = float(_qp_cfg["generated"])
QUALITY_VECTR_CONFIG: float = float(_qp_cfg["vectr_config"])
QUALITY_TEST_DEPRIORITISED: float = float(_qp_cfg["test_deprioritised"])
TEST_FRAMEWORK_FAN_IN_THRESHOLD: int = int(_qp_cfg["test_framework_fan_in_threshold"])
QUALITY_DOC_PROSE: float = float(_qp_cfg["doc_prose"])
QUALITY_SHORT_PENALTY: float = float(_qp_cfg["short_penalty"])
QUALITY_PRIVATE_SYMBOL: float = float(_qp_cfg["private_symbol_deprioritised"])
TRIVIAL_DOC_MAX_LINES: int = int(_qp_cfg["trivial_doc_max_lines"])
TRIVIAL_ATTR_CLASS_MAX_ATTRS: int = int(_qp_cfg["trivial_attr_class_max_attrs"])

# ---------------------------------------------------------------------------
# Locate ranking tunables (UPG-15.10)
# ---------------------------------------------------------------------------

_lr_cfg: dict[str, Any] = _cfg["ranking"]["locate_ranking"]

LOCATE_LARGE_SPAN_THRESHOLD: int = int(_lr_cfg["large_span_threshold"])
LOCATE_SMALL_SPAN_THRESHOLD: int = int(_lr_cfg["small_span_threshold"])

# ---------------------------------------------------------------------------
# Importance prior — file-level PageRank blend (ARCH-1b)
# ---------------------------------------------------------------------------

_imp_cfg: dict[str, Any] = _cfg["ranking"]["importance_prior"]

IMPORTANCE_PRIOR_LAMBDA: float = float(_imp_cfg["lambda"])

# ---------------------------------------------------------------------------
# Class importance prior — class-level reference-frequency blend (ARCH-2)
# ---------------------------------------------------------------------------

_cimp_cfg: dict[str, Any] = _cfg["ranking"]["class_importance"]

CLASS_IMPORTANCE_PRIOR_LAMBDA: float = float(_cimp_cfg["lambda"])

# ---------------------------------------------------------------------------
# Purpose-rank prior — dual-vector purpose-similarity blend into final sort (ARCH-4b)
# ---------------------------------------------------------------------------

_prp_cfg: dict[str, Any] = _cfg["ranking"]["purpose_rank"]

PURPOSE_RANK_PRIOR_LAMBDA: float = float(_prp_cfg["lambda"])

# ---------------------------------------------------------------------------
# Rerank pool sizes (UPG-12.1)
# ---------------------------------------------------------------------------

_rr_cfg: dict[str, Any] = _cfg["ranking"]["rerank"]

RERANK_TOP_K: int = int(_rr_cfg["top_k"])
RERANK_TOP_K_UNFILTERED: int = int(_rr_cfg["top_k_unfiltered"])
RERANK_PRE_FILTER_FETCH_K: int = int(_rr_cfg["pre_filter_fetch_k"])

# ---------------------------------------------------------------------------
# Indexing tunables (UPG-12.1)
# ---------------------------------------------------------------------------

_idx_cfg: dict[str, Any] = _cfg["indexing"]

INDEXING_MAX_CHUNK_LINES: int = int(_idx_cfg["max_chunk_lines"])
INDEXING_CLASS_HEADER_LINES: int = int(_idx_cfg["class_header_lines"])
INDEXING_BUILD_ARTIFACT_DIR_SUFFIXES: tuple[str, ...] = tuple(
    str(s).lower() for s in _idx_cfg["build_artifact_dir_suffixes"]
)

# UPG-JSFLOW-SYMBOLS: Flow-typed .js detection (routes to the typescript/tsx grammar).
_flow_cfg: dict[str, Any] = _idx_cfg["flow_detection"]

INDEXING_FLOW_SCAN_HEAD_BYTES: int = int(_flow_cfg["scan_head_bytes"])
INDEXING_FLOW_PRAGMA: str = str(_flow_cfg["pragma"])
INDEXING_FLOW_SECONDARY_MARKERS: tuple[str, ...] = tuple(
    str(m) for m in _flow_cfg["secondary_markers"]
)

# ---------------------------------------------------------------------------
# Output tunables (UPG-12.1)
# ---------------------------------------------------------------------------

_out_cfg: dict[str, Any] = _cfg["output"]

OUTPUT_SNIPPET_LINES: int = int(_out_cfg["snippet_lines"])

# ---------------------------------------------------------------------------
# Behaviour tunables (UPG-12.1)
# ---------------------------------------------------------------------------

_beh_cfg: dict[str, Any] = _cfg["behavior"]["remember_nudge"]

BEHAVIOR_REMEMBER_NUDGE_THRESHOLD: int = int(_beh_cfg["threshold"])
BEHAVIOR_REMEMBER_NUDGE_COOLDOWN: int = int(_beh_cfg["cooldown"])

# ---------------------------------------------------------------------------
# Eviction auto-hint token gate (UPG-11.15)
# ---------------------------------------------------------------------------

_evict_cfg: dict[str, Any] = _cfg["behavior"]["eviction"]

EVICTION_RETRIEVED_TOKEN_GATE: int = int(_evict_cfg["retrieved_token_gate"])

# ---------------------------------------------------------------------------
# Symbol graph — reserved keywords (UPG-JSFLOW-SYMBOLS)
# ---------------------------------------------------------------------------

_sg_cfg: dict[str, Any] = _cfg["symbol_graph"]

SYMBOL_GRAPH_RESERVED_KEYWORDS: dict[str, frozenset[str]] = {
    str(lang): frozenset(str(kw) for kw in kws)
    for lang, kws in _sg_cfg["reserved_keywords"].items()
}

# ---------------------------------------------------------------------------
# Workspace / watcher tunables (UPG-13.1/13.2/13.3)
# ---------------------------------------------------------------------------

_ws_cfg: dict[str, Any] = _cfg["workspace"]

WORKSPACE_DEFAULT_VECTRIGNORE_DIRS: tuple[str, ...] = tuple(
    str(d) for d in _ws_cfg["default_vectrignore_dirs"]
)

_watcher_cfg: dict[str, Any] = _cfg["watcher"]

WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S: float = float(_watcher_cfg["top_level_rescan_interval_s"])

# ---------------------------------------------------------------------------
# ingest_traces unresolved-caller/callee warning cap (UPG-7.3)
# ---------------------------------------------------------------------------

_ingest_cfg: dict[str, Any] = _cfg["behavior"]["ingest_traces"]

INGEST_TRACES_MAX_UNRESOLVED_EXAMPLES: int = int(_ingest_cfg["max_unresolved_examples"])

# ---------------------------------------------------------------------------
# Retrieval strategy fallback (UPG-8.2)
# ---------------------------------------------------------------------------

_strat_cfg: dict[str, Any] = _cfg["strategy"]

STRATEGY_DEFAULT_SEMANTIC_WEIGHT: float = float(_strat_cfg["default_semantic_weight"])
STRATEGY_DEFAULT_BM25_WEIGHT: float = float(_strat_cfg["default_bm25_weight"])
# Dual-vector pool entry — purpose (signature+docstring) vector (ARCH-4)
# ---------------------------------------------------------------------------

_dv_cfg: dict[str, Any] = _cfg["retrieval"]["dual_vector"]

DUAL_VECTOR_ENABLED: bool = bool(_dv_cfg["enabled"])
DUAL_VECTOR_BLEND_MODE: str = str(_dv_cfg["blend_mode"])
DUAL_VECTOR_BLEND_WEIGHT: float = float(_dv_cfg["blend_weight"])
DUAL_VECTOR_MAX_SIGNATURE_LINES: int = int(_dv_cfg["max_signature_lines"])
DUAL_VECTOR_MAX_DOCSTRING_LINES: int = int(_dv_cfg["max_docstring_lines"])
DUAL_VECTOR_MAX_DOCSTRING_CHARS: int = int(_dv_cfg["max_docstring_chars"])

# ---------------------------------------------------------------------------
# Not-found floor — lexical-vocabulary-anchor low-confidence signal
# (UPG-NOTFOUND-FLOOR, UPG-NOTFOUND-FLOOR-2)
# ---------------------------------------------------------------------------

_nff_cfg: dict[str, Any] = _cfg["ranking"]["notfound_floor"]

NOTFOUND_FLOOR_ENABLED: bool = bool(_nff_cfg["enabled"])
NOTFOUND_FLOOR_MIN_TOKEN_LEN: int = int(_nff_cfg["min_content_token_length"])
NOTFOUND_FLOOR_STOPWORDS: frozenset[str] = frozenset(_nff_cfg["stopwords"])
NOTFOUND_FLOOR_MIN_ZERO_DF_TOKENS: int = int(_nff_cfg["min_zero_df_tokens"])
NOTFOUND_FLOOR_BANNER: str = str(_nff_cfg["banner"])
