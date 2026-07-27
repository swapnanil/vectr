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

# The folded note-lifecycle state (UPG-MEMORY-STATE-MACHINE) a non-note
# candidate carries — a code-search hit has no note to fold state for, and
# rendering it as "active" would honestly mislabel it as an assertion under
# lifecycle tracking. Kept as an explicit, named value (not blank/None) so an
# audit reader always sees an honest label positionally aligned with every
# other candidate's state, never a silent gap.
CANDIDATE_STATE_NOT_APPLICABLE = "n/a"


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
    is_structural: bool  # True => exact structural match (score 1.0); still
                          # subject to the gate's similarity floor like any
                          # other candidate — see agent/proactive/gate.py
    state: str = "active"  # folded note-lifecycle state ("active" |
                          # "superseded" | "revoked") for a note candidate, or
                          # CANDIDATE_STATE_NOT_APPLICABLE for a non-note
                          # candidate (UPG-PROXY-AUDIT-DURABLE). Metadata
                          # carried through to the audit line only — never
                          # used to reorder or reweight the candidate itself.

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
    metadata fields are audit-safe: ids, scores, and lifecycle states — never
    conversation text or note bodies.
    """

    context: str
    item_count: int
    anchor_ids: tuple[str, ...]
    scores: tuple[float, ...]
    states: tuple[str, ...] = ()  # positionally aligned with anchor_ids/scores

    @staticmethod
    def empty() -> "InjectionResult":
        return InjectionResult(context="", item_count=0, anchor_ids=(), scores=(), states=())

    def is_empty(self) -> bool:
        return self.item_count == 0 or not self.context
