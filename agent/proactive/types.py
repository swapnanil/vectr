"""Core value types for the proactive-context engine (UPG-PRO).

All types are plain data. The matcher produces `Candidate`s; the gate turns a
set of candidates into one `InjectionResult`. `ProactiveWindow` is the
normalised, source-agnostic input both delivery seams (hooks, proxy) build.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Provenance rank breaks score ties deterministically: structural exact matches
# outrank semantic notes, which outrank code-search hits. Lower is stronger.
PROVENANCE_RANK: dict[str, int] = {
    "note_structural": 0,
    "symbol_def": 1,
    "note_semantic": 1,
    "code_semantic": 2,
}


@dataclass(frozen=True)
class Candidate:
    """One concrete thing that could be injected, already rendered.

    `line` is the self-describing, provenance-tagged text the model sees (e.g.
    "note #12 (gotcha, anchored to resolver.py): drops on scope exit"). The gate
    only orders/dedups/budgets/joins these; it never rewrites or reasons about
    `line`, so packing stays additive and deterministic.
    """

    kind: str            # note_structural | note_semantic | symbol_def | code_semantic
    line: str            # rendered, self-describing injected text
    score: float         # 1.0 for exact structural; cosine/hybrid otherwise
    anchor_id: str       # stable dedup id, e.g. "note:12" / "chunk:foo.py:1-9"
    is_structural: bool  # True => bypasses the semantic similarity floor

    @property
    def provenance_rank(self) -> int:
        return PROVENANCE_RANK.get(self.kind, 9)


@dataclass
class ProactiveWindow:
    """Normalised, in-memory view of the recent conversation.

    Built from a proxied request body (proxy seam) or a transcript tail (hook
    seam, future). Never persisted. `text` is the assembled query; `file_paths`
    and `symbols` are deterministic structural anchors extracted from tool
    traffic — no free-text path/identifier guessing.
    """

    text: str = ""
    file_paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.text.strip() or self.file_paths or self.symbols)


@dataclass(frozen=True)
class InjectionResult:
    """The gate's decision for one delivery moment.

    `context` is the packed block to inject ("" means inject nothing). The
    metadata fields are audit-safe: ids and scores, never conversation text or
    note bodies.
    """

    context: str
    item_count: int
    anchor_ids: tuple[str, ...]
    scores: tuple[float, ...]

    @staticmethod
    def empty() -> "InjectionResult":
        return InjectionResult(context="", item_count=0, anchor_ids=(), scores=())

    def is_empty(self) -> bool:
        return self.item_count == 0 or not self.context
