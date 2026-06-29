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
VALID_KINDS: tuple[str, ...] = ("directive", "task", "gotcha", "finding", "reference")
DEFAULT_KIND = "finding"


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
    kind: str = DEFAULT_KIND  # directive | task | gotcha | finding | reference (UPG-9.3)
    # team/shared notes tri-key model
    author_id: str = ""              # developer/agent identifier
    author_trust_score: float = 1.0  # Bayesian weight per contributor (0.0–1.0)
    valid_from: float = 0.0          # bi-temporal: when the note became valid
    valid_until: float | None = None # bi-temporal: None = still valid; float = superseded
    code_hash: str = ""              # sha256[:16] of the anchored code block at write time
    superseded_by: str | None = None  # author_id that superseded this note
    superseded_at: float | None = None
    title: str = ""                    # short label for index-tier display (UPG-RECALL-HIERARCHY)


@dataclass
class SnapshotEntry:
    snapshot_id: str
    workspace: str
    label: str
    notes: list[WorkingNote]
    retrieved_chunks: list[dict]   # {file, lines, symbol, content} of what was in context
    created_at: float
