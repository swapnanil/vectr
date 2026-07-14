"""
Trigger engine wave 1 (TRIGGER-ENGINE, bm2-design-skeleton.md §1/§2/§3/§5).

vectr's working memory is not just a note store — each memory declares WHEN it
is relevant to resurface, so the caller LLM does not have to guess or ask for
it. This module is the pure, deterministic core of that mechanism: P (path),
E (event), S (symbol), and M (semantic) trigger primitives, T (temporal)
modifiers, composition (conjunction within one trigger, disjunction across a
note's `triggers[]`), kind-default bundles, one shared total order (fire
precedence, injection ordering, and budget eviction all reuse the SAME
function), a per-session fire ledger, and the two-tier injection budget/pack.

Hard invariant (no-query-heuristics rule): every function here operates ONLY
on a memory's own declared trigger data plus tool/lifecycle state that the
CALLER already resolved (event name, a workspace-relative file path, a
caller-resolved symbol-graph set, a precomputed semantic-match boolean, a
clock reading). Nothing in this module ever reads a user prompt or query
string, parses text, or touches a vector — there is no such parameter
anywhere below; S is exact set-membership against a symbol the caller already
resolved, and M is a single precomputed boolean the caller already derived
from a cosine-vs-threshold check (agent/working_context_store/_store.py's
`fire()` — the only place a prompt or a vector is ever touched).

Write-time contradiction detection (bm2-design-skeleton.md §8) remains
out of scope this wave.

Live delivery surface (TRIGGER-ENGINE wave 2a): `evaluate_note()`/`fire()`
below, `WorkingContextStore.fire()`/`fire_and_format()`
(agent/working_context_store/_store.py), and `VectrService.fire_triggers()`/
`fire_and_recall()` (app/service.py) are wired into the live hook pipeline.
`main.py`'s `cmd_hook` maps its own hook-name vocabulary (`session-start`,
`user-prompt-submit`, `pre-tool-use`, `pre-compact`) onto this module's
EVENT_VALUES (`session-start`, `prompt-submit`, `pre-edit`, `pre-run`,
`pre-commit`, `post-compaction`) and calls `/v1/recall` (boot mode, now
engine-driven) and `/v1/trigger/reset` (PreCompact) — see `cmd_hook`'s
docstring for the exact mapping table and rationale. `pre-run`/`pre-commit`
have no live hook caller yet (no lifecycle moment maps to them this wave —
"declared but inert", never an error, bm2-design-skeleton.md §2) — a note
with an explicit `triggers=[{"event": "pre-run"}]` override simply never
fires today, exactly like an unrecognised kind falls back to its default.
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
from agent.working_context_store._types import DEFAULT_SCOPE, EVENT_VALUES, WorkingNote

# Kinds that inject full-text (design doc §3 two-tier budget); every other
# kind injects its index-tier one-liner. Not config.yaml: this is the kind
# taxonomy's own injection policy, the same closed-vocabulary category as
# VALID_KINDS/EVENT_VALUES, not a tunable weight.
FULL_TEXT_KINDS: tuple[str, ...] = ("directive", "gotcha")


# ---------------------------------------------------------------------------
# Trigger validation (write-time)
# ---------------------------------------------------------------------------

def validate_trigger(trigger: dict) -> None:
    """Raise ValueError if `trigger` is not a well-formed P/S/M/E/T primitive.

    A trigger must declare at least one of 'path' (P), 'event' (E), 'symbol'
    (S — TRIGGER-ENGINE wave 2b, bm2-design-skeleton.md §2), or 'semantic'
    (M — wave 2b, §8) — T (not_before/expires_visibility/cooldown) is a
    modifier only and can never fire a trigger by itself.

    'symbol' names a code symbol resolved at fire time against the pre-built
    code symbol graph (the same store `vectr_locate`/`vectr_trace` use) — it
    matches when that symbol is defined in, or referenced by, the file
    targeted by the current lifecycle moment.

    'semantic', when true, declares the M axis: at prompt-submit, the note
    matches when cosine(activity embedding, note's own stored embedding) is
    at or above a fixed per-kind threshold (config.yaml
    `memory_triggers.semantic.theta_by_kind`) — the caller (which has the
    embedder) computes that boolean; this module never touches the vector or
    the prompt text itself (no-query-heuristics rule).

    Both compose with 'path'/'event' under the SAME conjunction rule those
    two already use (every declared axis in one trigger dict must ALL
    match); a trigger may declare at most one value per axis by
    construction (each axis is a single dict key)."""
    if not isinstance(trigger, dict):
        raise ValueError(
            "each trigger must be an object with 'path', 'event', 'symbol', and/or 'semantic' keys"
        )
    path = trigger.get("path")
    event = trigger.get("event")
    symbol = trigger.get("symbol")
    semantic = trigger.get("semantic")
    if path is None and event is None and symbol is None and semantic is None:
        raise ValueError(
            "a trigger must declare at least one of 'path', 'event', 'symbol', or 'semantic' — "
            "T (not_before/expires_visibility/cooldown) is a modifier only "
            "and never fires alone"
        )
    if path is not None and not isinstance(path, str):
        raise ValueError("trigger 'path' must be a glob string")
    if event is not None and event not in EVENT_VALUES:
        raise ValueError(f"trigger 'event' must be one of: {', '.join(EVENT_VALUES)}")
    if symbol is not None and (not isinstance(symbol, str) or not symbol):
        raise ValueError("trigger 'symbol' must be a non-empty string naming a code symbol")
    if semantic is not None and not isinstance(semantic, bool):
        raise ValueError("trigger 'semantic' must be a boolean")
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

def default_bundle_for_kind(
    kind: str,
    anchors: list[list[str]] | None,
    priority: str | None = None,
) -> list[dict]:
    """The trigger bundle a note gets when it declares no explicit `triggers[]`
    override, computed fresh at evaluation time (never baked into storage, so
    a future default-bundle change applies retroactively).

    - directive: fires at session-start AND post-compaction (must-never-miss),
                 at ANY priority — matches the legacy `boot_recall()`'s own
                 unfiltered directive query.
    - task:      fires at session-start (current-work state), until closed —
                 but ONLY at priority=='high' (TRIGGER-ENGINE wave 2a fix):
                 the legacy `boot_recall()` this bundle replaces filters task
                 notes with `AND priority = 'high'` in SQL; a medium/low
                 priority task gets NO default trigger here (an explicit
                 `triggers[]` override can still make it fire — this only
                 narrows the IMPLICIT default to match what boot_recall()
                 has always surfaced, not a new restriction on the note
                 itself). `priority` is a caller-resolved note property
                 (never a query string) — same category as `anchors` below.
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
        return [{"event": "session-start"}] if priority == "high" else []
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


def _trigger_matches(
    trigger: dict,
    event: str | None,
    path_candidates: tuple[str, ...] | None,
    *,
    resolved_symbols: frozenset[str] | None = None,
    semantic_matched: bool | None = None,
) -> tuple[bool, str]:
    """Conjunction check for ONE trigger's declared P/E/S/M primitives
    against the current lifecycle state. Returns (matched, human-readable
    description) — the description feeds the one-line fire explanation.

    `path_candidates` is every equivalent form of the current lifecycle's
    target file the CALLER already resolved — e.g. the path exactly as given
    plus its workspace-relative form (`WorkingContextStore.fire()` computes
    both; this module never touches a filesystem or a workspace root, so it
    never resolves paths itself). The P primitive matches the trigger's glob
    `path` pattern against ANY candidate — a real hook sends an ABSOLUTE
    file_path while triggers are naturally authored workspace-relative (a
    gotcha's default bundle generates them straight from anchors), so
    matching only one form would silently never fire a relatively-anchored
    trigger against a real hook event. `None`/empty means no file path this
    call — a trigger declaring 'path' then deterministically does not match.

    `resolved_symbols` is the set of symbol names defined in or referenced by
    the current lifecycle's target file — resolved ONCE per `fire()` call by
    the caller (a single symbol-graph lookup, TRIGGER-ENGINE wave 2b §2),
    never here: this function only ever does an O(1) set-membership check, no
    graph access. `resolved_symbols=None` means the symbol graph is
    unavailable this call (a memory-only daemon, a warm-up window before the
    graph finishes building, or no `file_path` was given) — a trigger
    declaring 'symbol' then deterministically does not match; never an
    error, never a fuzzy/near-miss guess (exact-resolution only, per the
    no-query-heuristics rule's carve-out for symbol-graph lookups).

    `semantic_matched` is the precomputed cosine(activity, note_vector) >=
    theta[kind] boolean for THIS note — computed once by the caller from ONE
    per-call activity embedding and the note's own already-stored vector
    (TRIGGER-ENGINE wave 2b §8); this function never sees a vector or prompt
    text, only the boolean outcome. `None` means "not evaluated this call"
    (no embedder attached, embedder still warming up, or the note declares
    no semantic axis) — a trigger declaring 'semantic' then deterministically
    does not match; never an error."""
    path_pattern = trigger.get("path")
    want_event = trigger.get("event")
    want_symbol = trigger.get("symbol")
    want_semantic = trigger.get("semantic")
    if path_pattern is None and want_event is None and want_symbol is None and want_semantic is None:
        return False, ""  # malformed (should have been rejected by validate_trigger)

    if path_pattern is not None:
        if not path_candidates or not any(
            fnmatch.fnmatch(candidate, path_pattern) for candidate in path_candidates
        ):
            return False, ""
    if want_event is not None and want_event != event:
        return False, ""
    if want_symbol is not None:
        if resolved_symbols is None or want_symbol not in resolved_symbols:
            return False, ""
    if want_semantic:
        if semantic_matched is not True:
            return False, ""

    parts = []
    if path_pattern is not None:
        parts.append(f"path {path_pattern}")
    if want_symbol is not None:
        parts.append(f"symbol {want_symbol}")
    if want_semantic:
        parts.append("semantic")
    desc = " + ".join(parts) if parts else ""
    if want_event is not None:
        desc = f"{desc} at {want_event}" if desc else f"event {want_event}"
    return True, desc


def scope_permits(
    note: WorkingNote,
    *,
    session_id: str | None = None,
    branch: str | None = None,
    file_path: str | tuple[str, ...] | None = None,
) -> tuple[bool, str]:
    """Whether `note`'s declared `scope` (bm2-design-skeleton.md §1) permits it
    to be considered at all for the given lifecycle context. Operates ONLY on
    the note's own stored scope/session_id/branch/anchors plus caller-resolved
    lifecycle state (a session id, a branch name, a file path) — never a query
    string (no-query-heuristics rule).

    "workspace" (default) and "repo" are true no-ops this wave — see
    SCOPE_VALUES in _types.py for why "repo" isn't yet a real cross-store
    scope. "session" and "branch" need the caller to supply the matching
    lifecycle value; if the caller doesn't supply one (e.g. plain recall()
    with no branch context), the note is excluded rather than guessed open.

    "path-subtree" checks `file_path` against the directory of each declared
    anchor path. `file_path` accepts either a single path string or a tuple
    of equivalent candidate forms for the SAME file (mirroring
    `evaluate_note`'s `file_path`/`_trigger_matches`'s P primitive, e.g. the
    real hook path as given plus its workspace-relative form, both computed
    by the caller — this module never resolves or normalizes a path itself)
    — the subtree matches if ANY candidate falls under ANY declared anchor's
    directory. A note with no anchors, or given no candidate at all, is
    excluded (there is no declared subtree to match against, or nothing to
    match it against)."""
    scope = note.scope or DEFAULT_SCOPE
    if scope in ("workspace", "repo"):
        return True, ""
    if scope == "session":
        if session_id and note.session_id and session_id == note.session_id:
            return True, ""
        return False, "scope 'session' — not the writing session"
    if scope == "branch":
        if branch and note.branch and branch == note.branch:
            return True, ""
        return False, f"scope 'branch' — recorded on {note.branch or 'unknown'!r}, current is {branch or 'unknown'!r}"
    if scope == "path-subtree":
        if isinstance(file_path, str):
            candidates: tuple[str, ...] = (file_path,)
        elif file_path:
            candidates = tuple(file_path)
        else:
            candidates = ()
        if not candidates or not note.anchors:
            return False, "scope 'path-subtree' — no file path or no declared anchor subtree"
        for raw_candidate in candidates:
            candidate = raw_candidate.replace("\\", "/").lstrip("./")
            for anchor in note.anchors:
                if not anchor or not anchor[0]:
                    continue
                anchor_path = str(anchor[0]).replace("\\", "/").lstrip("./")
                anchor_dir = anchor_path.rsplit("/", 1)[0] if "/" in anchor_path else ""
                if candidate == anchor_path or (anchor_dir and (candidate == anchor_dir or candidate.startswith(anchor_dir + "/"))):
                    return True, ""
        return False, "scope 'path-subtree' — file not under the note's declared subtree"
    return True, ""  # unrecognised scope value never blocks


def evaluate_note(
    note: WorkingNote,
    *,
    event: str | None = None,
    file_path: str | tuple[str, ...] | None = None,
    now: float | None = None,
    session_id: str | None = None,
    branch: str | None = None,
    resolved_symbols: frozenset[str] | None = None,
    semantic_matched: bool | None = None,
) -> FireResult:
    """Deterministic, total (never raises for well-formed input), linear-in-
    triggers evaluation of whether `note` fires for the given lifecycle state.

    A tombstoned note (`valid_until` set — explicitly superseded, §1) never
    fires. A note whose declared `scope` does not permit this lifecycle
    context (`scope_permits()`, §1) never fires either — checked before the
    trigger loop, since an out-of-scope note has nothing to evaluate.
    Otherwise: explicit `triggers[]` fully replace the kind's default
    bundle; an empty/absent `triggers[]` falls back to
    `default_bundle_for_kind()`. Each trigger in the (possibly default)
    bundle is tried in order; the FIRST one whose P/E/S conjunction matches
    AND whose T modifiers (not_before, cooldown) do not withhold it wins — OR
    composition across the list. `not_before` and `cooldown` withhold a fire
    outright; `expires_visibility` never withholds — it only marks the result
    `faded` for ranking (T is a modifier, and a modifier only gates through
    not_before/cooldown; visibility fade is a ranking signal, not a gate).

    `file_path` accepts either a single path string (the common/legacy case —
    a caller with only one path form) or a tuple of equivalent candidate
    forms for the SAME file (e.g. `WorkingContextStore.fire()` passes
    `(as_given, workspace_relative)` — see its docstring). The FULL candidate
    tuple is forwarded to both path-matching consumers: the P primitive
    (`_trigger_matches` docstring) and `scope_permits()`'s `path-subtree`
    check (its own docstring) each match against ANY candidate — an absolute
    hook path and a workspace-relative anchor/pattern are both given a
    chance, never just the first form. The one-line fire explanation never
    touches the resolved path text at all (it renders the trigger's own
    declared glob pattern, e.g. "path src/api/**"), so no convention is
    needed there. This module still never resolves or normalizes a path
    itself, that is entirely the caller's job (purity invariant).

    `resolved_symbols` (TRIGGER-ENGINE wave 2b) is passed straight through to
    `_trigger_matches()` for the S primitive — see its docstring; this
    function never touches the symbol graph itself, only a caller-resolved
    set of names. `semantic_matched` (wave 2b, §8) is likewise passed
    straight through for the M primitive — a single precomputed boolean for
    this note, never a vector or prompt text."""
    if now is None:
        now = time.time()

    if isinstance(file_path, str) or file_path is None:
        path_candidates: tuple[str, ...] | None = (file_path,) if file_path is not None else None
    else:
        path_candidates = tuple(file_path) if file_path else None

    if note.valid_until is not None:
        return FireResult(note.note_id, False, "superseded — a tombstoned memory never fires")

    permitted, scope_reason = scope_permits(note, session_id=session_id, branch=branch, file_path=path_candidates)
    if not permitted:
        return FireResult(note.note_id, False, scope_reason)

    triggers = note.triggers if note.triggers else default_bundle_for_kind(note.kind, note.anchors, note.priority)
    if not triggers:
        return FireResult(note.note_id, False, "no triggers declared for this note/kind")

    for idx, trig in enumerate(triggers):
        matched, desc = _trigger_matches(
            trig, event, path_candidates,
            resolved_symbols=resolved_symbols, semantic_matched=semantic_matched,
        )
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
    quietly reorder trigger-fire precedence.

    kind='task' exception (UPG-TASK-NOTE-INJECTION-RECENCY, matching
    recall()/recall_for_path()'s own SQL ordering for this kind): every note
    that fires together in one `fire()` call is stamped with the SAME
    `last_fired` reading (one shared clock per evaluation, by design — see
    `WorkingContextStore.fire()`), which silently ties `last_used` between
    two task notes the moment BOTH have ever fired once — collapsing to the
    ascending note_id tie-break below, which is backwards for "current
    checkpoint" state (a task note is current-work state, not a
    relevance-ranked learning; an older task note must never outrank a newer
    one). So kind='task' orders on note_id DESCENDING directly — an
    immutable, monotonic recency proxy immune to the shared-clock tie —
    instead of last_used. Every other kind is unaffected."""
    try:
        kind_rank = MEMORY_TRIGGER_KIND_PRIORITY.index(note.kind)
    except ValueError:
        kind_rank = len(MEMORY_TRIGGER_KIND_PRIORITY)
    try:
        priority_rank = MEMORY_TRIGGER_PRIORITY_RANK.index(note.priority)
    except ValueError:
        priority_rank = len(MEMORY_TRIGGER_PRIORITY_RANK)
    if note.kind == "task":
        return (kind_rank, priority_rank, 0.0, -note.note_id)
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
    end needs no explicit handling here.

    Also tracks the per-session CUMULATIVE injection spend (§3): the
    per-session token cap bounds the total tokens injected across every
    `fire_triggers`/`fire_and_format` call in one session, not each call in
    isolation. `record_spend()` is called once per delivery with however many
    tokens that delivery actually packed; `remaining_budget()` is what the
    next delivery has left to spend. `reset()` also zeroes the spend —
    compaction makes the whole budget available again, consistent with
    previously-fired memories becoming re-eligible."""

    def __init__(self) -> None:
        self._fired: dict[int, set[int]] = {}
        self._spent_tokens: int = 0

    def eligible(self, note_id: int, trigger_index: int) -> bool:
        return trigger_index not in self._fired.get(note_id, set())

    def record_fire(self, note_id: int, trigger_index: int) -> None:
        self._fired.setdefault(note_id, set()).add(trigger_index)

    def remaining_budget(self) -> int:
        return max(0, MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP - self._spent_tokens)

    def record_spend(self, tokens: int) -> None:
        self._spent_tokens += max(0, tokens)

    def reset(self) -> None:
        self._fired.clear()
        self._spent_tokens = 0


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


def pack_injection(
    items: list[tuple[WorkingNote, str, str]],
    *,
    budget: int | None = None,
) -> list[PackedItem]:
    """Two-tier budget pack: directive/gotcha prefer full text, every other
    kind injects its index-tier one-liner. `items` is
    [(note, full_text, index_text), ...] in any order; this function sorts
    by the shared `total_order_key` and packs greedily, spending the given
    `budget` (defaults to the full per-session cap when omitted/None — a
    single call in isolation, e.g. a direct unit-test call or a fresh
    session's first delivery). A memory is NEVER partially truncated — it
    injects whole (subject to its own per-injection cap, else it drops to its
    index-tier line), or is evicted entirely if even the index-tier line
    does not fit. Eviction is always from the BOTTOM of the shared total
    order — the lowest-precedence notes are the ones dropped first. The
    moment any item is evicted for not fitting even at the index tier,
    packing STOPS entirely: nothing lower-precedence is ever allowed to
    ship while something higher-precedence was dropped, even if it would
    have fit in the leftover budget.

    Passing the session ledger's `remaining_budget()` here is what makes the
    per-session cap CUMULATIVE across every `fire_triggers`/`fire_and_format`
    call in one session (§3) rather than a fresh allowance each call."""
    ordered = sorted(items, key=lambda triple: total_order_key(triple[0]))
    budget = MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP if budget is None else max(0, budget)
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
                break  # evicted — stop packing so nothing lower-precedence backfills

        packed.append(PackedItem(note_id=note.note_id, text=text, tier=tier))
        budget -= tokens
    return packed
