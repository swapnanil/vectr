"""vectr bundled-config loader.

Reads ``agent/config.yaml`` and the associated stopword data files at import
time and caches the results.  All paths are resolved via ``importlib.resources``
so this works both when running from the repository root *and* when vectr is
installed as the global binary (``/opt/homebrew/bin/vectr`` or similar pip
install), where a ``cwd``-relative ``open()`` would fail.

Public API
----------
SYMBOL_STOP_WORDS : frozenset[str]
    Merged set of NLTK English stopwords + programming-aware supplement.
    All entries are lowercased; comment lines and blank lines are stripped.

SYMBOL_QUALIFIED_BOOST : float
    Additive ranking boost for a fully-qualified symbol match (class + leaf).

SYMBOL_LEAF_BOOST : float
    Additive ranking boost for a bare-leaf symbol match.

SYMBOL_MIN_LEAF_LEN : int
    Minimum character length for a bare leaf to be eligible for a boost.

FORCED_INCLUSION_MAX : int
    Safety cap on forced-inclusion candidate pool size (UPG-11.7).

FORCED_INCLUSION_MIN_IDENTIFIER_LEN : int
    Minimum bare-identifier length to trigger forced-inclusion (UPG-11.7).

FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR : float
    BM25 relevance floor for non-compound forced candidates (UPG-11.12).

FORCED_INCLUSION_VEC_SIM_FLOOR : float
    Cosine similarity floor for non-compound forced candidates (UPG-11.12).

QUALITY_TRIVIAL : float
    Quality prior for bare stub chunks (UPG-12.1).

QUALITY_NAVIGATIONAL : float
    Quality prior for re-export / import-only navigational chunks (UPG-12.1).

QUALITY_HEADING_ONLY : float
    Quality prior for markdown heading-only chunks (UPG-12.1).

QUALITY_GENERATED : float
    Quality prior for machine-generated files (UPG-12.1).

QUALITY_VECTR_CONFIG : float
    Quality prior for vectr's own config files (UPG-12.1).

QUALITY_TEST_DEPRIORITISED : float
    Quality prior for test files when query does not target tests (UPG-12.1).

QUALITY_DOC_PROSE : float
    Quality prior for documentation prose chunks (UPG-12.1).

QUALITY_SHORT_PENALTY : float
    Quality prior for chunks with very few meaningful lines (UPG-12.1).

RERANK_TOP_K : int
    Number of hybrid candidates to rerank before trimming to n_results (UPG-12.1).

RERANK_TOP_K_UNFILTERED : int
    Deeper candidate pool for unfiltered (no language filter) queries (UPG-12.1).

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

DOC_INTENT_SUPPRESS_FORCED_INCLUSION : bool
    When True (default), suppress forced-inclusion for doc-intent queries so that
    symbol-name tokens don't flood the candidate pool with code chunks, which would
    bury the documentation the user is actually asking for (UPG-11.11 / F2).

DOC_INTENT_DOC_PROSE_MULTIPLIER : float
    Quality multiplier for doc prose chunks on doc-intent queries (UPG-11.11).
    When the query is doc-intent, this replaces the normal doc_prose multiplier
    (0.70) so documentation can compete with code on how-to/explain queries.
    Default 1.0 = neutral (no doc penalty for doc-intent queries).
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
    slashes (e.g. ``"data/english_stopwords.txt"``).  We use
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


def _load_stopwords(file_path: str) -> set[str]:
    """Parse a stopword file (one word per line; # comments and blank lines stripped)."""
    words: set[str] = set()
    text = _read_bundled_text(file_path)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Normalize to lowercase; the word itself is the token.
        words.add(stripped.lower())
    return words


def _load_config() -> dict[str, Any]:
    """Load and return the parsed agent/config.yaml as a dict."""
    raw = _read_bundled_text("config.yaml")
    return _yaml.safe_load(raw)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Eagerly load once at import; cache in module-level constants.
# ---------------------------------------------------------------------------

_cfg = _load_config()
_sym_cfg: dict[str, Any] = _cfg["ranking"]["symbol_boost"]

# Merge all stopword files into a single frozenset.
_merged_stop: set[str] = set()
for _sw_file in _sym_cfg.get("stopwords_files", []):
    _merged_stop.update(_load_stopwords(_sw_file))

SYMBOL_STOP_WORDS: frozenset[str] = frozenset(_merged_stop)

SYMBOL_QUALIFIED_BOOST: float = float(_sym_cfg["qualified_boost"])
SYMBOL_LEAF_BOOST: float = float(_sym_cfg["leaf_boost"])
SYMBOL_MIN_LEAF_LEN: int = int(_sym_cfg["min_leaf_len"])

# ---------------------------------------------------------------------------
# Forced-inclusion tunables (UPG-11.7 / UPG-11.12)
# ---------------------------------------------------------------------------

_fi_cfg: dict[str, Any] = _cfg["ranking"]["forced_inclusion"]

FORCED_INCLUSION_MAX: int = int(_fi_cfg["max_candidates"])
FORCED_INCLUSION_MIN_IDENTIFIER_LEN: int = int(_fi_cfg["min_identifier_len"])
FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR: float = float(_fi_cfg["nontrigger_bm25_floor"])
FORCED_INCLUSION_VEC_SIM_FLOOR: float = float(_fi_cfg["vec_sim_floor"])

# ---------------------------------------------------------------------------
# Doc-intent query classification (UPG-11.11)
# ---------------------------------------------------------------------------

_di_cfg: dict[str, Any] = _cfg["ranking"]["doc_intent"]

DOC_INTENT_SUPPRESS_FORCED_INCLUSION: bool = bool(_di_cfg["suppress_forced_inclusion"])
DOC_INTENT_DOC_PROSE_MULTIPLIER: float = float(_di_cfg["doc_prose_multiplier"])
DOC_INTENT_PREFIXES: tuple[str, ...] = tuple(_di_cfg["prefixes"])
DOC_INTENT_ANY_SUBSTRINGS: tuple[str, ...] = tuple(_di_cfg["any_substrings"])

# ---------------------------------------------------------------------------
# Quality priors — ranking multipliers (UPG-12.1)
# ---------------------------------------------------------------------------

_qp_cfg: dict[str, Any] = _cfg["ranking"]["quality_priors"]

QUALITY_TRIVIAL: float = float(_qp_cfg["trivial"])
QUALITY_NAVIGATIONAL: float = float(_qp_cfg["navigational"])
QUALITY_HEADING_ONLY: float = float(_qp_cfg["heading_only"])
QUALITY_GENERATED: float = float(_qp_cfg["generated"])
QUALITY_VECTR_CONFIG: float = float(_qp_cfg["vectr_config"])
QUALITY_TEST_DEPRIORITISED: float = float(_qp_cfg["test_deprioritised"])
QUALITY_DOC_PROSE: float = float(_qp_cfg["doc_prose"])
QUALITY_SHORT_PENALTY: float = float(_qp_cfg["short_penalty"])

# ---------------------------------------------------------------------------
# Rerank pool sizes (UPG-12.1)
# ---------------------------------------------------------------------------

_rr_cfg: dict[str, Any] = _cfg["ranking"]["rerank"]

RERANK_TOP_K: int = int(_rr_cfg["top_k"])
RERANK_TOP_K_UNFILTERED: int = int(_rr_cfg["top_k_unfiltered"])

# ---------------------------------------------------------------------------
# Indexing tunables (UPG-12.1)
# ---------------------------------------------------------------------------

_idx_cfg: dict[str, Any] = _cfg["indexing"]

INDEXING_MAX_CHUNK_LINES: int = int(_idx_cfg["max_chunk_lines"])
INDEXING_CLASS_HEADER_LINES: int = int(_idx_cfg["class_header_lines"])

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
