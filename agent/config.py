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

QUALITY_HEADING_ONLY : float
    Quality prior for markdown heading-only chunks (UPG-12.1).

QUALITY_GENERATED : float
    Quality prior for machine-generated files (UPG-12.1).

QUALITY_VECTR_CONFIG : float
    Quality prior for vectr's own config files (UPG-12.1).

QUALITY_TEST_DEPRIORITISED : float
    Quality prior for test files (UPG-12.1).

QUALITY_DOC_PROSE : float
    Quality prior for documentation prose chunks (UPG-12.1).

QUALITY_SHORT_PENALTY : float
    Quality prior for chunks with very few meaningful lines (UPG-12.1).

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
QUALITY_HEADING_ONLY: float = float(_qp_cfg["heading_only"])
QUALITY_GENERATED: float = float(_qp_cfg["generated"])
QUALITY_VECTR_CONFIG: float = float(_qp_cfg["vectr_config"])
QUALITY_TEST_DEPRIORITISED: float = float(_qp_cfg["test_deprioritised"])
QUALITY_DOC_PROSE: float = float(_qp_cfg["doc_prose"])
QUALITY_SHORT_PENALTY: float = float(_qp_cfg["short_penalty"])
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
