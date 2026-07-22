"""
Dataclasses and constants for the working-context store.
"""
from __future__ import annotations

from dataclasses import dataclass


# Memory kinds (UPG-9.3). Mirrors the disk-memory split that makes CLAUDE.md
# (unconditional directives) distinct from MEMORY.md (relevance-ranked learnings):
#   directive — must-never-miss rules; injected unconditionally at SessionStart.
#   task      — current-work context; injected in the SessionStart boot set.
#   gotcha    — file/path-anchored caveats; injected at PreToolUse + semantic recall.
#   finding   — relevance-ranked learnings; injected per-prompt at UserPromptSubmit.
#   reference — pointers (URLs/tickets); surfaced on demand only.
#   decision  — an architectural/design decision plus its why (UPG-DECISION-
#               TIMELINE). NOT boot-privileged (no default trigger bundle, see
#               default_bundle_for_kind() in trigger_engine.py — same
#               evaluation-time bucket as finding/reference); accrues over a
#               project's life and is meant to be recalled as a group with
#               `vectr_recall(kind="decision", sort_by="chronological")`,
#               yielding an ADR-like decision timeline for free.
#   operational — an env/process/build fact (build quirks, CI gotchas,
#               feedback-loop knowledge) that is not anchored to a single code
#               file the way `gotcha` is (UPG-MEMORY-STATE-MACHINE §5.1).
#               Primary surface is prompt-time semantic matching
#               (UserPromptSubmit, embeddings only); a secondary PreToolUse
#               command-family surface is opt-in via an explicit `triggers[]`
#               override (§5.2). Always carries a last-confirmed/drift verdict
#               (§4.4) rather than the plain provenance framing other kinds get.
VALID_KINDS: tuple[str, ...] = (
    "directive", "task", "gotcha", "finding", "reference", "decision", "operational",
)
DEFAULT_KIND = "finding"

# Trigger engine wave 1 (TRIGGER-ENGINE, bm2-design-skeleton.md §2/§5) — closed
# protocol vocabularies, not tunable thresholds, so these live beside VALID_KINDS
# as plain constants rather than in config.yaml (same reasoning as VALID_KINDS
# itself: a caller either uses one of these values or the write is rejected;
# there is nothing here for an operator to retune).
#
# EVENT_VALUES: the E trigger primitive's enum. Every value is a real lifecycle
# moment; none has a live hook caller into fire()/evaluate_note() yet this wave
# (see agent/trigger_engine.py's module docstring for the exact wiring status)
# — an event with no live caller is simply never evaluated, never an error
# ("declared but inert" per the design doc, not a defect).
EVENT_VALUES: tuple[str, ...] = (
    "session-start", "prompt-submit", "pre-edit", "pre-run", "pre-commit", "post-compaction",
)

# SCOPE_VALUES: how broadly a memory's declared triggers apply.
# "workspace" (default) is a true no-op — today's unfiltered behaviour.
# "repo" is currently a no-op identical to "workspace": true cross-worktree/
# cross-clone sharing needs the store keyed by git-common-dir instead of by
# workspace path, which is a larger storage change (see
# UPG-TRIGGER-SCOPE-REPO-CROSSSTORE); recorded faithfully at write time so the
# value round-trips once that lands. "session", "branch", and "path-subtree"
# are enforced (TRIGGER-ENGINE wave 2a, `scope_permits()` in
# agent/trigger_engine.py):
#   - "session": enforced in BOTH fire() and recall()/recall_for_path() —
#     an ephemeral note must never surface outside its writing session via
#     any path, not just trigger firing.
#   - "branch": enforced ONLY in fire() — bounds ambient trigger noise (a
#     branch-bound task stops nagging on the wrong branch); a deliberate
#     vectr_recall query is never blocked by branch, and this avoids a git
#     subprocess call on the hot, frequently-called plain recall() path.
#   - "path-subtree": enforced in fire() (against the note's declared
#     anchors[]) and in recall_for_path() (file_path is already a parameter
#     there — free); not enforced in the general query-based recall(), which
#     has no file context to filter against.
# Notes written before this wave carry scope="workspace" (the dataclass
# default) and are therefore unaffected — this is backward compatible.
SCOPE_VALUES: tuple[str, ...] = ("workspace", "repo", "path-subtree", "branch", "session")
DEFAULT_SCOPE = "workspace"

# KIND_DEFAULT_SCOPES (UPG-TRIGGER-SCOPE-KIND-DEFAULTS, bm2-design-skeleton.md
# §1's Default bundles table): the scope a note gets WHEN ITS WRITE OMITS
# `scope` entirely (None) -- an explicitly passed scope (including explicit
# "workspace") always wins verbatim and never consults this table. Resolved
# and BAKED INTO THE ROW at write time by `WorkingContextStore.remember()`
# (unlike `default_bundle_for_kind()` in trigger_engine.py, which is
# deliberately evaluation-time so trigger-bundle changes apply
# retroactively -- scope is different: a note's scope is part of its
# identity/visibility contract, not a re-computable ranking input, so
# retroactivity is deliberately avoided here; a note written before this
# table existed keeps whatever scope it was actually written with). Only the
# two kinds the design skeleton's table assigns a non-default scope appear
# here; every other kind (including any future kind) keeps DEFAULT_SCOPE.
# Same rationale as VALID_KINDS/SCOPE_VALUES just above for living beside
# them as a plain constant rather than in config.yaml: this is a closed
# protocol mapping from the design doc, not an operator-tunable weight --
# there is nothing here for an operator to retune.
KIND_DEFAULT_SCOPES: dict[str, str] = {
    "task": "branch",
    "gotcha": "repo",
    "operational": "repo",
}

# PROVENANCE_VALUES: trust/endorsement class (bm2-design-skeleton.md §5).
# "human" = a person recorded or endorsed this; only imperative directive
# framing is ever allowed for it. "agent" (default for vectr_remember) = an AI
# session recorded this; rendered as memory-to-verify. "auto" = captured by a
# mechanism with no reviewing judgment; weakest framing, and never allowed on
# kind="directive" (rejected at write time — an unreviewed standing rule is a
# contradiction in terms).
PROVENANCE_VALUES: tuple[str, ...] = ("human", "agent", "auto")
DEFAULT_PROVENANCE = "agent"


@dataclass
class WorkingNote:
    note_id: int
    workspace: str
    content: str
    tags: list[str]
    priority: str          # "high" | "medium" | "low"
    created_at: float
    last_accessed: float
    session_id: str | None = None
    decay_score: float = 1.0
    kind: str = DEFAULT_KIND  # directive | task | gotcha | finding | reference | decision (UPG-9.3, UPG-DECISION-TIMELINE)
    # team/shared notes tri-key model
    author_id: str = ""              # developer/agent identifier
    author_trust_score: float = 1.0  # Bayesian weight per contributor (0.0–1.0)
    valid_from: float = 0.0          # bi-temporal: when the note became valid
    valid_until: float | None = None # bi-temporal: None = still valid; float = superseded
    code_hash: str = ""              # sha256[:16] of the anchored code block at write time
    superseded_by: str | None = None  # author_id that superseded this note (code_hash conflict path)
    superseded_at: float | None = None
    title: str = ""                    # short label for index-tier display (UPG-RECALL-HIERARCHY)
    # Trigger engine wave 1 (TRIGGER-ENGINE) — additive memory-object fields.
    triggers: list[dict] = None        # type: ignore[assignment]  # explicit P/E/T trigger overrides; [] = use the kind's default bundle
    provenance: str = DEFAULT_PROVENANCE  # human | agent | auto (bm2-design-skeleton.md §5)
    scope: str = DEFAULT_SCOPE            # workspace | repo | path-subtree | branch | session
    anchors: list[list[str]] = None    # type: ignore[assignment]  # [[path, content_hash_at_write_or_None], ...]
    supersedes: int | None = None      # note_id THIS note explicitly tombstones at write time
    superseded_by_note_id: int | None = None  # reciprocal: set on the OLD note by the explicit-supersedes path (distinct from `superseded_by`'s code_hash-conflict/author_id semantics)
    last_fired: float | None = None    # last time the trigger engine actually fired this note (T:cooldown + total-order tie-break)
    branch: str = ""                   # git branch recorded at write time when scope=="branch" (TRIGGER-ENGINE wave 2a); "" for every other scope or when git is unavailable

    def __post_init__(self) -> None:
        if self.triggers is None:
            self.triggers = []
        if self.anchors is None:
            self.anchors = []


@dataclass
class SnapshotEntry:
    snapshot_id: str
    workspace: str
    label: str
    notes: list[WorkingNote]
    retrieved_chunks: list[dict]   # {file, lines, symbol, content} of what was in context
    created_at: float
