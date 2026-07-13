"""
Trigger engine wave 1 (TRIGGER-ENGINE, bm2-design-skeleton.md §1/§2/§3/§5).

vectr's working memory is not just a note store — each memory declares WHEN it
is relevant to resurface, so the caller LLM does not have to guess or ask for
it. This module is the pure, deterministic core of that mechanism: P (path)
and E (event) trigger primitives, T (temporal) modifiers, composition
(conjunction within one trigger, disjunction across a note's `triggers[]`),
kind-default bundles, one shared total order (fire precedence, injection
ordering, and budget eviction all reuse the SAME function), a per-session fire
ledger, and the two-tier injection budget/pack.

Hard invariant (no-query-heuristics rule): every function here operates ONLY
on a memory's own declared trigger data plus tool/lifecycle state that the
CALLER already resolved (event name, a workspace-relative file path, a clock
reading). Nothing in this module ever reads a user prompt or query string —
there is no such parameter anywhere below.

S (symbol) and M (semantic/embedding) trigger primitives, executable
predicates, and write-time contradiction detection are wave-2 scope
(bm2-design-skeleton.md §8) and are deliberately absent here.

Live delivery surface this wave: `evaluate_note()`/`fire()` below and
`WorkingContextStore.fire()` (agent/working_context_store/_store.py) are
complete and unit-tested, and `VectrService.fire_triggers()` (app/service.py)
wires them to the store and a per-session `TriggerFireLedger` — but no caller
invokes `fire_triggers()` yet. `main.py`'s `cmd_hook` (the live
SessionStart/UserPromptSubmit/PreToolUse/PreCompact hook surface) predates
this module and calls the pre-existing recall()/boot_recall()/snapshot() path
instead, on its own event-name vocabulary (`session-start`,
`user-prompt-submit`, `pre-tool-use`, `pre-compact` — distinct strings from
EVENT_VALUES). Wiring `fire_triggers()` into that hook surface is follow-up
work, not done here. Every EVENT_VALUES member is therefore "declared but
inert" this wave — evaluable and fully tested via direct calls to
`evaluate_note()`/`fire()`, but with no live hook caller (never an error —
bm2-design-skeleton.md §2).
"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field

from agent.config import (
    MEMORY_TRIGGER_CHARS_PER_TOKEN,
    MEMORY_TRIGGER_KIND_PRIORITY,
    MEMORY_TRIGGER_PER_INJECTION_TOKEN_CAP,
    MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP,
    MEMORY_TRIGGER_PRIORITY_RANK,
)
from agent.working_context_store._types import EVENT_VALUES, WorkingNote

# Kinds that inject full-text (design doc §3 two-tier budget); every other
# kind injects its index-tier one-liner. Not config.yaml: this is the kind
# taxonomy's own injection policy, the same closed-vocabulary category as
# VALID_KINDS/EVENT_VALUES, not a tunable weight.
FULL_TEXT_KINDS: tuple[str, ...] = ("directive", "gotcha")


# ---------------------------------------------------------------------------
# Trigger validation (write-time)
# ---------------------------------------------------------------------------

def validate_trigger(trigger: dict) -> None:
    """Raise ValueError if `trigger` is not a well-formed P/E/T primitive.

    A trigger must declare at least one of 'path' (P) or 'event' (E) — T
    (not_before/expires_visibility/cooldown) is a modifier only and can never
    fire a trigger by itself (bm2-design-skeleton.md §2)."""
    if not isinstance(trigger, dict):
        raise ValueError("each trigger must be an object with 'path' and/or 'event' keys")
    path = trigger.get("path")
    event = trigger.get("event")
    if path is None and event is None:
        raise ValueError(
            "a trigger must declare at least one of 'path' or 'event' — "
            "T (not_before/expires_visibility/cooldown) is a modifier only "
            "and never fires alone"
        )
    if path is not None and not isinstance(path, str):
        raise ValueError("trigger 'path' must be a glob string")
    if event is not None and event not in EVENT_VALUES:
        raise ValueError(f"trigger 'event' must be one of: {', '.join(EVENT_VALUES)}")
    for key in ("not_before", "expires_visibility", "cooldown"):
        value = trigger.get(key)
        if value is not None and not isinstance(value, (int, float)):
            raise ValueError(f"trigger '{key}' must be a number (epoch seconds, or seconds for cooldown)")


def validate_triggers(triggers: list[dict] | None) -> list[dict]:
    """Validate every trigger in a note's declared `triggers[]`. Returns a
    plain list (empty when `triggers` is None/empty — that note falls back to
    its kind's default bundle at evaluation time; explicit triggers[] fully
    REPLACE the default bundle, they never merge with it)."""
    if not triggers:
        return []
    for trig in triggers:
        validate_trigger(trig)
    return list(triggers)


# ---------------------------------------------------------------------------
# Kind-default bundles (bm2-design-skeleton.md §1)
# ---------------------------------------------------------------------------

def default_bundle_for_kind(kind: str, anchors: list[list[str]] | None) -> list[dict]:
    """The trigger bundle a note gets when it declares no explicit `triggers[]`
    override, computed fresh at evaluation time (never baked into storage, so
    a future default-bundle change applies retroactively).

    - directive: fires at session-start AND post-compaction (must-never-miss).
    - task:      fires at session-start (current-work state), until closed.
    - gotcha:    one path trigger per declared anchor, at pre-edit — the
                 symbol-ref half of this bundle (§1: "path-match on anchor
                 OR symbol-ref on anchor") is wave-2 (S primitive). A gotcha
                 note with NO structured anchors gets no default bundle here
                 (empty list) — it continues to be served exactly as today by
                 WorkingContextStore.recall_for_path()'s unrelated content-
                 substring match, left untouched by this engine.
    - finding/reference: wave-2 M-territory (θ-gated semantic trigger) — no
                 wave-1 default bundle. Their current relevance-rank recall()
                 injection is unchanged and unaffected by this engine.
    """
    if kind == "directive":
        return [{"event": "session-start"}, {"event": "post-compaction"}]
    if kind == "task":
        return [{"event": "session-start"}]
    if kind == "gotcha":
        return [
            {"path": anchor[0], "event": "pre-edit"}
            for anchor in (anchors or [])
            if anchor and anchor[0]
        ]
    return []


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class FireResult:
    note_id: int
    fired: bool
    explanation: str
    trigger_index: int | None = None
    # T:expires_visibility passed — the note still fired (T never blocks P/E),
    # but the caller should rank/pack it as faded (bm2-design-skeleton.md §2).
    faded: bool = False
    # Filled in by the caller (WorkingContextStore.fire()) from the existing
    # staleness machinery — never computed here, this module never touches
    # the filesystem (evaluate_note is pure).
    stale_paths: list[str] = field(default_factory=list)


def _trigger_matches(trigger: dict, event: str | None, file_path: str | None) -> tuple[bool, str]:
    """Conjunction check for ONE trigger's declared P/E primitives against
    the current lifecycle state. Returns (matched, human-readable description)
    — the description feeds the one-line fire explanation."""
    path_pattern = trigger.get("path")
    want_event = trigger.get("event")
    if path_pattern is None and want_event is None:
        return False, ""  # malformed (should have been rejected by validate_trigger)

    if path_pattern is not None:
        if not file_path or not fnmatch.fnmatch(file_path, path_pattern):
            return False, ""
    if want_event is not None and want_event != event:
        return False, ""

    if path_pattern is not None and want_event is not None:
        desc = f"path {path_pattern} at {want_event}"
    elif path_pattern is not None:
        desc = f"path {path_pattern}"
    else:
        desc = f"event {want_event}"
    return True, desc


def evaluate_note(
    note: WorkingNote,
    *,
    event: str | None = None,
    file_path: str | None = None,
    now: float | None = None,
) -> FireResult:
    """Deterministic, total (never raises for well-formed input), linear-in-
    triggers evaluation of whether `note` fires for the given lifecycle state.

    A tombstoned note (`valid_until` set — explicitly superseded, §1) never
    fires. Otherwise: explicit `triggers[]` fully replace the kind's default
    bundle; an empty/absent `triggers[]` falls back to
    `default_bundle_for_kind()`. Each trigger in the (possibly default)
    bundle is tried in order; the FIRST one whose P/E conjunction matches AND
    whose T modifiers (not_before, cooldown) do not withhold it wins — OR
    composition across the list. `not_before` and `cooldown` withhold a fire
    outright; `expires_visibility` never withholds — it only marks the result
    `faded` for ranking (T is a modifier, and a modifier only gates through
    not_before/cooldown; visibility fade is a ranking signal, not a gate)."""
    if now is None:
        now = time.time()

    if note.valid_until is not None:
        return FireResult(note.note_id, False, "superseded — a tombstoned memory never fires")

    triggers = note.triggers if note.triggers else default_bundle_for_kind(note.kind, note.anchors)
    if not triggers:
        return FireResult(note.note_id, False, "no triggers declared for this note/kind")

    for idx, trig in enumerate(triggers):
        matched, desc = _trigger_matches(trig, event, file_path)
        if not matched:
            continue
        not_before = trig.get("not_before")
        if not_before is not None and now < not_before:
            continue
        cooldown = trig.get("cooldown")
        if cooldown is not None and note.last_fired is not None and (now - note.last_fired) < cooldown:
            continue
        expires_visibility = trig.get("expires_visibility")
        faded = expires_visibility is not None and now >= expires_visibility
        return FireResult(
            note_id=note.note_id,
            fired=True,
            explanation=f"fired: trigger {idx + 1} — {desc}",
            trigger_index=idx,
            faded=faded,
        )
    return FireResult(note.note_id, False, "no declared trigger matched this event/path")


# ---------------------------------------------------------------------------
# One total order — reused for fire precedence, injection ordering, AND
# budget eviction (bm2-design-skeleton.md §2/§3/§4: "one implementation").
# ---------------------------------------------------------------------------

def total_order_key(note: WorkingNote) -> tuple[int, int, float, int]:
    """kind priority -> note priority -> last_used recency -> note_id.

    The final tie-break is note_id — immutable and monotonic — deliberately,
    following the same lesson as UPG-RECALL-ORDER-CHURN (recall()'s own
    tie-break excludes last_accessed because recall() bumps it on every read,
    which would reorder ties on the very next identical call). `last_fired`
    (set only by this engine's own fires, never by a plain vectr_recall) is
    preferred over `last_accessed` for "last_used" so a direct recall doesn't
    quietly reorder trigger-fire precedence."""
    try:
        kind_rank = MEMORY_TRIGGER_KIND_PRIORITY.index(note.kind)
    except ValueError:
        kind_rank = len(MEMORY_TRIGGER_KIND_PRIORITY)
    try:
        priority_rank = MEMORY_TRIGGER_PRIORITY_RANK.index(note.priority)
    except ValueError:
        priority_rank = len(MEMORY_TRIGGER_PRIORITY_RANK)
    last_used = note.last_fired if note.last_fired is not None else note.last_accessed
    return (kind_rank, priority_rank, -last_used, note.note_id)


# ---------------------------------------------------------------------------
# Per-session fire ledger (bm2-design-skeleton.md §3 dedup window)
# ---------------------------------------------------------------------------

class TriggerFireLedger:
    """Per-session dedup for the trigger engine.

    Once a note fires via trigger index K in this session, further
    evaluations that ALSO match trigger index K are suppressed for the rest
    of the session — "axis" is each entry in a note's own (possibly
    kind-default) `triggers[]` list, since each entry is literally one axis
    of that note's OR-composition. A DIFFERENT trigger index on the SAME
    note firing (e.g. a second gotcha anchor on a different file, or a task
    note's session-start trigger after its own compaction-reset) is a
    genuinely new reason to resurface it and is never suppressed here.

    `reset()` clears all suppression state for this session — call it on a
    compaction event (pre-compact/post-compaction resets eligibility, §3).
    A brand-new session simply gets a brand-new ledger instance, so session
    end needs no explicit handling here."""

    def __init__(self) -> None:
        self._fired: dict[int, set[int]] = {}

    def eligible(self, note_id: int, trigger_index: int) -> bool:
        return trigger_index not in self._fired.get(note_id, set())

    def record_fire(self, note_id: int, trigger_index: int) -> None:
        self._fired.setdefault(note_id, set()).add(trigger_index)

    def reset(self) -> None:
        self._fired.clear()


# ---------------------------------------------------------------------------
# Provenance framing (bm2-design-skeleton.md §5)
# ---------------------------------------------------------------------------

# Fixed protocol strings, not tunables (same category as the pre-existing
# hardcoded "[STALE]"/"WARNING: ..." strings in format_notes_for_llm) — every
# injected block's framing is deterministic on provenance+kind alone.
_HUMAN_DIRECTIVE_FRAME = "DIRECTIVE (standing rule from the user — follow it): "
_HUMAN_FRAME = "Recorded by the user: "
_AGENT_FRAME = "Memory to verify (recorded by an AI session, not human-endorsed): "
_AUTO_FRAME = "Auto-captured (weakest confidence, no reviewing judgment applied — verify before relying on this): "


def frame_prefix(provenance: str, kind: str) -> str:
    """The imperative/hedged framing prefix for one injected memory block.
    Only human-provenance directives ever render as an unhedged imperative;
    agent-provenance is framed as memory to verify; auto-provenance carries
    the weakest framing (bm2-design-skeleton.md §5). Immutable per note —
    this is a pure function of the note's own stored (provenance, kind)."""
    if provenance == "human":
        return _HUMAN_DIRECTIVE_FRAME if kind == "directive" else _HUMAN_FRAME
    if provenance == "auto":
        return _AUTO_FRAME
    return _AGENT_FRAME  # "agent" (default) and any unrecognised value


# ---------------------------------------------------------------------------
# Two-tier injection budget + pack (bm2-design-skeleton.md §3)
# ---------------------------------------------------------------------------

def token_estimate(text: str) -> int:
    return max(1, len(text) // MEMORY_TRIGGER_CHARS_PER_TOKEN)


@dataclass
class PackedItem:
    note_id: int
    text: str
    tier: str  # "full" | "index"


def pack_injection(items: list[tuple[WorkingNote, str, str]]) -> list[PackedItem]:
    """Two-tier budget pack: directive/gotcha prefer full text, every other
    kind injects its index-tier one-liner. `items` is
    [(note, full_text, index_text), ...] in any order; this function sorts
    by the shared `total_order_key` and packs greedily, spending the
    per-session cap. A memory is NEVER partially truncated — it injects
    whole (subject to its own per-injection cap, else it drops to its
    index-tier line), or is evicted entirely if even the index-tier line
    does not fit. Eviction is always from the BOTTOM of the shared total
    order — the lowest-precedence notes are the ones dropped first."""
    ordered = sorted(items, key=lambda triple: total_order_key(triple[0]))
    budget = MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP
    packed: list[PackedItem] = []
    for note, full_text, index_text in ordered:
        prefer_full = note.kind in FULL_TEXT_KINDS
        text, tier = (full_text, "full") if prefer_full else (index_text, "index")
        tokens = token_estimate(text)

        if tier == "full" and tokens > MEMORY_TRIGGER_PER_INJECTION_TOKEN_CAP:
            text, tier, tokens = index_text, "index", token_estimate(index_text)

        if tokens > budget:
            if tier == "full":
                text, tier, tokens = index_text, "index", token_estimate(index_text)
            if tokens > budget:
                continue  # evicted — does not fit even at index tier

        packed.append(PackedItem(note_id=note.note_id, text=text, tier=tier))
        budget -= tokens
    return packed
