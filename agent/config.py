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
