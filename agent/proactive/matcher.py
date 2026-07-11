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

from agent.proactive.types import Candidate, ProactiveWindow
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


def _note_summary(note: WorkingNote) -> str:
    if note.title and note.title.strip():
        return note.title.strip()
    for line in note.content.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _structural_note_candidate(note: WorkingNote, anchor: str, max_chars: int) -> Candidate:
    summary = _note_summary(note)
    line = _cap(
        f"note #{note.note_id} ({note.kind}, anchored to {anchor}): {summary}", max_chars
    )
    return Candidate(
        kind="note_structural",
        line=line,
        score=1.0,
        anchor_id=f"note:{note.note_id}",
        is_structural=True,
    )


def _semantic_note_candidate(note: WorkingNote, score: float, max_chars: int) -> Candidate:
    summary = _note_summary(note)
    line = _cap(f"note #{note.note_id} ({note.kind}): {summary}", max_chars)
    return Candidate(
        kind="note_semantic",
        line=line,
        score=float(score),
        anchor_id=f"note:{note.note_id}",
        is_structural=False,
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
        if self._structural_note and window.file_paths:
            try:
                notes = self._source.structural_notes(window.file_paths)
            except Exception:
                notes = []
            anchors = {p: Path(p).name or p for p in window.file_paths}
            for note in notes:
                anchor = _first_anchor(note, anchors) or "file"
                candidates.append(
                    _structural_note_candidate(note, anchor, self._max_chars)
                )

        # M3 — semantic note match: cosine recall above the similarity floor.
        if self._semantic_note and window.text.strip():
            try:
                scored = self._source.semantic_notes(
                    window.text, self._min_similarity, self._note_limit
                )
            except Exception:
                scored = []
            for note, score in scored:
                candidates.append(
                    _semantic_note_candidate(note, score, self._max_chars)
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


def _first_anchor(note: WorkingNote, anchors: dict[str, str]) -> str | None:
    """Return the display anchor (basename) of the first window file whose
    basename appears in the note content — a deterministic substring check that
    mirrors the store's own path-anchor recall, so the injected line names the
    file the note is actually about."""
    content = note.content or ""
    for _path, base in anchors.items():
        if base and base in content:
            return base
    return None
