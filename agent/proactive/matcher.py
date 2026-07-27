"""Matching engine for proactive context (UPG-PRO-4).

Given a `ProactiveWindow`, run every enabled matcher UNCONDITIONALLY and return
the union of scored `Candidate`s. No matcher branches on window *content* to
decide whether to run — which matchers are active is a static config toggle,
never a runtime read of the conversation (the no-query-heuristics hard rule).

The matchers do not implement retrieval themselves; they call a `MatchSource`,
which the daemon backs with the existing recall / path-anchor / search paths.
The honest shipped subset is:
  M1 structural_note  — exact file-path -> anchored note (score 1.0)
  M3 semantic_note    — cosine note recall above the similarity floor
  M4 code_semantic    — hybrid code search (opt-in; needs a built index)
M2 (symbol-definition via locate) is defined by the design but deferred here;
adding it is another matcher + threshold, not a content classifier.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from agent.proactive.types import (
    CANDIDATE_STATE_NOT_APPLICABLE,
    Candidate,
    ProactiveWindow,
)
from agent.working_context_store._store import (
    _ANTI_MEMORY_TEMPLATE,
    _date_str,
    _note_title,
    _path_boundary_match,
)
from agent.working_context_store._types import WorkingNote


@runtime_checkable
class MatchSource(Protocol):
    """Retrieval surface the matchers consume. The daemon implements this over
    its in-process store/searcher; tests implement it with real return types."""

    def structural_notes(self, file_paths: list[str]) -> list[WorkingNote]:
        ...

    def semantic_notes(
        self, text: str, min_similarity: float, limit: int
    ) -> list[tuple[WorkingNote, float]]:
        ...

    def code_search(self, text: str, n_results: int) -> list:
        ...

    def note_states(self, notes: list[WorkingNote]) -> dict[int, dict]:
        """Batched fold of note-lifecycle state for the given notes (UPG-
        MEMORY-STATE-MACHINE), keyed by note_id -> {"state": "active"|
        "superseded"|"revoked", "reason": str|None, "actor": str|None,
        "ts": float|None} — see `WorkingContextStore.note_event_states()`.
        A note_id absent from the returned dict means active. Called ONCE
        per `match()` invocation for the union of every note the other
        matchers found, never per-note."""
        ...


def _one_line(text: str) -> str:
    """Collapse to a single whitespace-normalised line (injected context is
    line-oriented, and a note body may span many lines)."""
    return " ".join(text.split())


def _cap(text: str, max_chars: int) -> str:
    text = _one_line(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def _raw_summary(note: WorkingNote) -> str:
    if note.title and note.title.strip():
        return note.title.strip()
    for line in note.content.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _revoked_summary(note: WorkingNote, state: dict) -> str:
    """Deterrent rendering for a revoked note (UPG-PROXY-REVOKED-LEAK):
    reuses the shared `_ANTI_MEMORY_TEMPLATE` (agent/working_context_store/
    _store.py) rather than inventing separate deterrent copy — the same
    wording a revoked note gets on the multi-line `/v1/recall` surface
    (`_format_full_block()`), so this single-line proxy injection agrees
    with it.

    The template's own trailing "Do not re-derive this from other sources
    without verification." clause is relocated to the FRONT of the
    rendered text — a physical substring of the template's own output, not
    new wording — so that `_cap()`'s right-truncation can only ever cut
    the tail (the quoted raw claim) and can never remove the deterrent
    warning itself. `rpartition(". ")` finds this split point exactly:
    the deterrent sentence has no internal ". " of its own, so whatever
    occurs in `reason`/`summary` text, the LAST ". " in the formatted
    string is always the boundary right before it."""
    revoked_date = _date_str(state["ts"]) if state.get("ts") else _date_str(note.created_at)
    reason = state.get("reason") or "no reason given"
    anti_memory = _ANTI_MEMORY_TEMPLATE.format(
        created_date=_date_str(note.created_at),
        revoked_date=revoked_date,
        reason=reason,
        summary=_note_title(note),
    )
    prefix, sep, clause = anti_memory.rpartition(". ")
    if not sep:
        return anti_memory
    return f"{clause} {prefix}."


def _note_summary(note: WorkingNote, state: dict | None = None) -> str:
    """The candidate line's summary text. A note whose folded lifecycle
    state (UPG-MEMORY-STATE-MACHINE) is "revoked" renders through the
    anti-memory deterrent instead of its raw content — a revoked note
    still injects (catching an agent about to re-assert a wrong belief is
    the highest-value injection), it just never injects as raw fact."""
    if state and state.get("state") == "revoked":
        return _revoked_summary(note, state)
    return _raw_summary(note)


def _kind_label(note: WorkingNote, state: dict | None) -> str:
    """The kind marker for the candidate line: "REVOKED" takes precedence
    over the bare kind name so the injected line clearly flags a revoked
    note rather than reading like an ordinary kind label."""
    if state and state.get("state") == "revoked":
        return "REVOKED"
    return note.kind


def _state_label(state: dict | None) -> str:
    """The folded lifecycle state to carry onto the `Candidate` for the audit
    line (UPG-PROXY-AUDIT-DURABLE). Mirrors `note_event_states()`'s own
    documented contract: a note absent from the fold (or with no fold result
    at all) is "active" — the same default every other renderer here uses."""
    if state and state.get("state"):
        return state["state"]
    return "active"


def _structural_note_candidate(
    note: WorkingNote,
    anchor: str,
    is_declared_anchor: bool,
    max_chars: int,
    state: dict | None = None,
) -> Candidate:
    summary = _note_summary(note, state)
    kind_label = _kind_label(note, state)
    relation = "anchored to" if is_declared_anchor else "mentions"
    line = _cap(
        f"note #{note.note_id} ({kind_label}, {relation} {anchor}): {summary}", max_chars
    )
    return Candidate(
        kind="note_structural",
        line=line,
        score=1.0,
        anchor_id=f"note:{note.note_id}",
        is_structural=True,
        state=_state_label(state),
    )


def _semantic_note_candidate(
    note: WorkingNote, score: float, max_chars: int, state: dict | None = None
) -> Candidate:
    summary = _note_summary(note, state)
    kind_label = _kind_label(note, state)
    line = _cap(f"note #{note.note_id} ({kind_label}): {summary}", max_chars)
    return Candidate(
        kind="note_semantic",
        line=line,
        score=float(score),
        anchor_id=f"note:{note.note_id}",
        is_structural=False,
        state=_state_label(state),
    )


def _code_candidate(result, max_chars: int) -> Candidate:
    file_path = getattr(result, "file_path", "") or ""
    lines = getattr(result, "lines", "") or ""
    symbol = getattr(result, "symbol_name", "") or ""
    score = float(getattr(result, "score", 0.0) or 0.0)
    chunk_id = getattr(result, "chunk_id", "") or f"{file_path}:{lines}"
    first = ""
    for ln in (getattr(result, "content", "") or "").splitlines():
        if ln.strip():
            first = ln.strip()
            break
    where = f"{file_path}:{lines}" if lines else file_path
    label = f" ({symbol})" if symbol else ""
    line = _cap(f"search hit {where}{label}: {first}", max_chars)
    return Candidate(
        kind="code_semantic",
        line=line,
        score=score,
        anchor_id=f"chunk:{chunk_id}",
        is_structural=False,
        state=CANDIDATE_STATE_NOT_APPLICABLE,
    )


class ProactiveMatcher:
    """Runs the enabled matchers and returns the union of scored candidates."""

    def __init__(
        self,
        source: MatchSource,
        *,
        min_similarity: float,
        max_chars_per_event: int,
        structural_note: bool = True,
        semantic_note: bool = True,
        code_search: bool = False,
        note_limit: int = 10,
        code_limit: int = 5,
    ) -> None:
        self._source = source
        self._min_similarity = min_similarity
        self._max_chars = max_chars_per_event
        self._structural_note = structural_note
        self._semantic_note = semantic_note
        self._code_search = code_search
        self._note_limit = note_limit
        self._code_limit = code_limit

    def match(self, window: ProactiveWindow) -> list[Candidate]:
        candidates: list[Candidate] = []

        # M1 — structural file match: exact file path -> anchored notes (1.0).
        structural_notes: list[WorkingNote] = []
        if self._structural_note and window.file_paths:
            try:
                structural_notes = self._source.structural_notes(window.file_paths)
            except Exception:
                structural_notes = []

        # M3 — semantic note match: cosine recall above the similarity floor.
        semantic_scored: list[tuple[WorkingNote, float]] = []
        if self._semantic_note and window.text.strip():
            try:
                semantic_scored = self._source.semantic_notes(
                    window.text, self._min_similarity, self._note_limit
                )
            except Exception:
                semantic_scored = []

        # UPG-PROXY-REVOKED-LEAK: fold lifecycle state for the UNION of every
        # note either matcher found, ONCE — never per-note, never per-matcher
        # — before rendering a single candidate line.
        #
        # Two distinct failure modes, deliberately handled differently,
        # because conflating them reintroduces the very defect this fixes:
        #
        #   (a) The source does not implement note_states() at all (an older
        #       fake or a backend predating this protocol method). That is a
        #       STATIC property of the source, checked with getattr() and not
        #       by catching AttributeError — every note is active, which is
        #       exactly note_event_states()'s documented contract for an
        #       absent note_id. Backward compatible, renders raw content.
        #
        #   (b) The source implements it but the CALL FAILS (e.g. the
        #       note_events table is unreadable mid-migration). Lifecycle
        #       state is then UNKNOWN, and "unknown" must never be rendered
        #       as "active" — that would put a revoked note's raw content
        #       back into injected context as apparent fact, which is the
        #       defect. Fail CLOSED: drop every note candidate for this
        #       window. M4 code search is unaffected (it carries no note
        #       lifecycle), so the caller degrades to code context only
        #       rather than to wrong memory.
        matched_notes: dict[int, WorkingNote] = {}
        for note in structural_notes:
            matched_notes.setdefault(note.note_id, note)
        for note, _score in semantic_scored:
            matched_notes.setdefault(note.note_id, note)
        note_states: dict[int, dict] = {}
        _states_fn = getattr(self._source, "note_states", None)
        if _states_fn is not None:
            try:
                note_states = _states_fn(list(matched_notes.values()))
            except Exception:
                structural_notes = []
                semantic_scored = []
                note_states = {}

        if self._structural_note and window.file_paths:
            anchors = {p: Path(p).name or p for p in window.file_paths}
            for note in structural_notes:
                state = note_states.get(note.note_id)
                if state and state.get("state") == "superseded":
                    # Already excluded via valid_until in the normal path;
                    # defensive skip if one still reaches the renderer —
                    # never render a superseded note's raw content as an
                    # active fact.
                    continue
                found = _first_anchor(note, anchors)
                anchor, is_declared_anchor = found if found is not None else ("file", True)
                candidates.append(
                    _structural_note_candidate(
                        note, anchor, is_declared_anchor, self._max_chars, state
                    )
                )

        if self._semantic_note and window.text.strip():
            for note, score in semantic_scored:
                state = note_states.get(note.note_id)
                if state and state.get("state") == "superseded":
                    continue
                candidates.append(
                    _semantic_note_candidate(note, score, self._max_chars, state)
                )

        # M4 — semantic code match: hybrid search hits (opt-in; needs an index).
        if self._code_search and window.text.strip():
            try:
                results = self._source.code_search(window.text, self._code_limit)
            except Exception:
                results = []
            for result in results:
                candidates.append(_code_candidate(result, self._max_chars))

        return candidates


def _first_anchor(note: WorkingNote, anchors: dict[str, str]) -> tuple[str, bool] | None:
    """Return (label, is_declared_anchor) for the display anchor of the
    first window file this note is actually about.

    UPG-PROXY-SUBSTRING-ANCHOR/2b: the note's DECLARED `anchors` column is
    checked first — an exact match there is the strongest signal, and the
    caller may honestly say "anchored to X". Only when no declared anchor
    matches does this fall back to a boundary-matched content mention,
    using the SAME `_path_boundary_match()` rule `recall_for_path()` uses
    (shared, not forked) — the caller must render that case as "mentions
    X", a materially weaker provenance claim than "anchored to X". Returns
    None when the note is about none of the window's files."""
    declared = {a[0] for a in (note.anchors or []) if a}
    for _path, base in anchors.items():
        if base and (base in declared or _path in declared):
            return base, True
    content = note.content or ""
    for _path, base in anchors.items():
        if base and _path_boundary_match(content, base):
            return base, False
    return None
