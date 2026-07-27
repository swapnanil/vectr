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


# Structural-match evidence tiers (UPG-PROXY-INJECT-PRECISION). Populated only
# on `kind="note_structural"` candidates (see matcher.py's
# `_structural_note_candidate`); every other candidate leaves `Candidate.
# structural_tier` at its default `None`. Each label names HOW a structural
# match was found — a declared anchor, a content mention in a kind=gotcha
# note, or a content mention in any other allowed kind — a property of the
# match itself, never a read of query/window content. `STRUCTURAL_TIER_
# MENTION` (Tier C, the weakest) is what the gate's per-event weak-item cap
# (`max_weak_structural_items`) counts against.
STRUCTURAL_TIER_DECLARED_ANCHOR = "declared_anchor"
STRUCTURAL_TIER_GOTCHA_MENTION = "gotcha_mention"
STRUCTURAL_TIER_MENTION = "mention"


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
    score: float         # tiered for structural (see structural_tier); cosine/hybrid otherwise
    anchor_id: str       # stable dedup id, e.g. "note:12" / "chunk:foo.py:1-9"
    is_structural: bool  # True => a structural (path-anchored) match; still
                          # subject to the gate's similarity floor like any
                          # other candidate — see agent/proactive/gate.py
    state: str = "active"  # folded note-lifecycle state ("active" |
                          # "superseded" | "revoked") for a note candidate, or
                          # CANDIDATE_STATE_NOT_APPLICABLE for a non-note
                          # candidate (UPG-PROXY-AUDIT-DURABLE). Metadata
                          # carried through to the audit line only — never
                          # used to reorder or reweight the candidate itself.
    structural_tier: str | None = None  # one of the STRUCTURAL_TIER_* labels
                          # above for a structural candidate, None otherwise —
                          # see agent/proactive/gate.py's weak-item cap.
                          # Independent of `state`: the tier describes HOW the
                          # match was found, the state describes what the note
                          # currently IS — a revoked note keeps its tier and so
                          # still competes for a slot as a deterrent.

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
    # Positionally aligned with anchor_ids/scores WHEN POPULATED (the gate
    # builds all three from the same `selected` list, so they cannot drift).
    # Empty means "lifecycle state not transported for this result" — never
    # "no revoked items"; see agent/proactive/provider.py, which reconstructs
    # a result from the daemon's `/v1/proactive` response and cannot know it.
    states: tuple[str, ...] = ()

    @staticmethod
    def empty() -> "InjectionResult":
        return InjectionResult(context="", item_count=0, anchor_ids=(), scores=(), states=())

    def is_empty(self) -> bool:
        return self.item_count == 0 or not self.context
