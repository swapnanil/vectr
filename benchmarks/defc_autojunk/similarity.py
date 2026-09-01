"""Pair-similarity and templated-body utilities for the DEF-C autojunk
harness.

This module is small and dependency-free (only `difflib` and the
chunk_quality helpers) so a reviewer can read it end to end and
verify the math. The harness script (`harness.py`) imports these
helpers; the offline templated-fixture analysis
(`templated_analysis.py`) imports them too; the unit tests
(`tests/test_similarity.py`) test them directly.

`pair_similarity(a, b)` — the two ratios on the same two chunk
contents, exactly the call `_is_near_duplicate_body` makes on the
DEF-C site, with `autojunk` flipped:

  - DEFAULT — `difflib.SequenceMatcher(None, x, y).ratio()`, the
    broken metric the comment at agent/searcher.py:348-363 records.
  - CORRECTED — `difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()`,
    the call shape the disabled sibling
    `_is_content_near_duplicate` (agent/searcher.py:429-436) already
    uses.

Both branches normalize the inputs through
`chunk_quality.normalized_content` so the comparison is the same
text the searcher actually feeds into SequenceMatcher (whitespace-
collapsed lowercase).

`is_templated_pair(a, b)` — the same predicate
`chunk_quality._is_templated_body_difference` uses to flag
"bodies differ only in digits" pairs. A pair flagged here is a
false-collapse hazard: char-similarity will say "near-duplicate"
but the bodies are genuinely distinct symbols. The harness reports
these pairs separately from the rest.

`classify_pair(a, b, default_ratio, corrected_ratio, thresholds)` —
a small per-pair classification: which candidate thresholds under
autojunk=False would collapse this pair, and is it a templated
hazard? Used by both the templated analysis and the real-dedup
replay so the per-pair output is consistent.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Iterable

from agent.chunk_quality import (
    normalized_content,
    _is_templated_body_difference,
)


# Both ratios are deterministic functions of (a, b); caching them on
# (a, b) would not change correctness, only avoid re-computing
# SequenceMatcher for pairs that are reported in both pieces. Kept as
# plain function calls — the cross-piece savings are tiny and the
# behaviour is more reviewable as a function.
def pair_similarity(a: str, b: str) -> tuple[float, float]:
    """Return (default_ratio, corrected_ratio) for the two chunk contents.

    `a` and `b` are raw chunk contents; both branches normalize
    through `normalized_content` first so the comparison is the same
    text the searcher actually feeds into SequenceMatcher
    (whitespace-collapsed lowercase, never the raw chunk bytes).

    default_ratio is the metric `_is_near_duplicate_body` ships with
    today — the call site at agent/searcher.py:364-366 uses
    difflib's default `autojunk=True` and understates similarity
    on any chunk longer than 200 chars. corrected_ratio is what
    the same call returns with `autojunk=False`, the call shape
    the disabled sibling `_is_content_near_duplicate` already uses.

    Returns a 2-tuple of floats in [0, 1] (SequenceMatcher's
    `ratio()` is already bounded). Two identical bodies return
    (1.0, 1.0); a pair that difflib's autojunk flags as "junk"
    returns a default ratio far below its corrected ratio —
    exactly the case the brief calls out as the DEF-C defect.
    """
    a_norm = normalized_content(a)
    b_norm = normalized_content(b)
    default_ratio = difflib.SequenceMatcher(None, a_norm, b_norm).ratio()
    corrected_ratio = difflib.SequenceMatcher(
        None, a_norm, b_norm, autojunk=False,
    ).ratio()
    return default_ratio, corrected_ratio


def is_templated_pair(a: str, b: str) -> bool:
    """True if the two bodies differ only in digits — the false-collapse
    hazard for char-similarity. Delegates to
    `chunk_quality._is_templated_body_difference` so this harness
    and the searcher share a single definition of "templated
    pair". A pair this returns True for is genuinely distinct
    symbols (`handler_0` vs `handler_1`) that char-similarity
    will read as near-duplicates. The harness reports such pairs
    separately from the rest of the pair set.
    """
    a_norm = normalized_content(a)
    b_norm = normalized_content(b)
    return _is_templated_body_difference(a_norm, b_norm)


@dataclass
class PairClassification:
    """Per-pair classification shared by the templated and replay
    pieces. Fields are deliberately the same shape both pieces
    emit so a reviewer reading the per-corpus output can compare
    templated pairs and real-dedup pairs without re-mapping keys.

    `thresholds` is the explicit list of candidate thresholds under
    `autojunk=False` the reviewer wants the sweep over. Defaults to
    a defensible midpoint sweep (see `DEFAULT_THRESHOLDS`); the
    harness CLI lets the reviewer override.
    """
    pair_id: str
    source: str                    # "templated" | "replay"
    default_ratio: float
    corrected_ratio: float
    is_templated: bool
    would_collapse: dict[str, bool]  # threshold -> collapses-under-autojunk-False

    def collapsing_thresholds(self) -> list[str]:
        """The thresholds under `autojunk=False` that would collapse this
        pair. Returns the labels in the same order as `DEFAULT_THRESHOLDS`
        so a reviewer can read a row top-to-bottom and see the
        threshold at which the pair starts collapsing."""
        out: list[str] = []
        for t in DEFAULT_THRESHOLDS:
            if self.would_collapse.get(t, False):
                out.append(t)
        return out


# The default sweep. Spans the 0.75 (DEF-C's current value) up to 0.99
# (effectively byte-identical) in uneven steps — tight near the
# decision boundary (0.75 -> 0.80 -> 0.85 -> 0.90), looser above
# (0.95, 0.99). A reviewer who wants finer resolution can override
# via the harness CLI. The "label" is what shows up in the output
# (a string, never a float, so JSON/CSV readers don't lose
# precision).
DEFAULT_THRESHOLDS: tuple[str, ...] = (
    "0.75", "0.80", "0.85", "0.90", "0.95", "0.99",
)


def classify_pair(
    pair_id: str,
    source: str,
    a: str,
    b: str,
    thresholds: Iterable[str] = DEFAULT_THRESHOLDS,
) -> PairClassification:
    """Compute the two ratios and the per-threshold collapse map for
    one pair. Used by both pieces; identical input contract so the
    per-pair rows are directly comparable.
    """
    default_ratio, corrected_ratio = pair_similarity(a, b)
    templated = is_templated_pair(a, b)
    collapse_map: dict[str, bool] = {}
    for t in thresholds:
        try:
            t_f = float(t)
        except ValueError:
            # Threshold label isn't a number — record as non-collapse
            # so the row is still emitted, but never silently True.
            collapse_map[t] = False
            continue
        collapse_map[t] = corrected_ratio >= t_f
    return PairClassification(
        pair_id=pair_id,
        source=source,
        default_ratio=default_ratio,
        corrected_ratio=corrected_ratio,
        is_templated=templated,
        would_collapse=collapse_map,
    )
