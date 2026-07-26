"""Tests for agent/working_context_store/_related.py — write-time related-
notes lookup (WorkingContextStore.related_active_notes).

Every test builds a REAL WorkingContextStore backed by a real (temp-path)
ChromaDB collection and a deterministic controlled embed_fn that maps exact
note content to a hand-picked unit vector, so cosine similarity between any
two notes is known exactly rather than incidentally derived from a hash.
This is what makes the near-duplicate/unrelated assertions real rather than
tautological.
"""
from __future__ import annotations

import math

import chromadb
import pytest

from agent.working_context_store import WorkingContextStore
from agent.working_context_store._related import RelatedNote


def _unit_vector_at_cosine(sim: float) -> list[float]:
    """A 4-d unit vector whose cosine similarity to (1, 0, 0, 0) is exactly
    `sim` (up to floating point)."""
    return [sim, math.sqrt(max(0.0, 1.0 - sim * sim)), 0.0, 0.0]


BASE_VEC = _unit_vector_at_cosine(1.0)          # (1, 0, 0, 0)
UNRELATED_VEC = [0.0, 1.0, 0.0, 0.0]             # orthogonal -> cosine sim 0.0


class _ControlledEmbedder:
    """Deterministic embed_fn: exact content string -> exact pre-registered
    unit vector. Raises KeyError for anything not explicitly registered, so
    a test can never accidentally rely on an un-controlled vector. Counts
    calls (not texts) so tests can assert zero added inference."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = dict(vectors)
        self.call_count = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [self.vectors[t] for t in texts]


def _store(tmp_path, embedder: _ControlledEmbedder) -> WorkingContextStore:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return WorkingContextStore(str(tmp_path), embed_fn=embedder, notes_chroma_client=client)


WS = "/repo"


class TestRelatedActiveNotesRelevance:
    def test_returns_near_duplicate_not_unrelated(self, tmp_path) -> None:
        base = "WorkspaceLock.acquire takes a PID-scoped lock"
        near = "WorkspaceLock.acquire grabs a PID-scoped lock"
        unrelated = "the deploy pipeline retries on a 503"
        embedder = _ControlledEmbedder({
            base: BASE_VEC,
            near: _unit_vector_at_cosine(0.97),
            unrelated: UNRELATED_VEC,
        })
        store = _store(tmp_path, embedder)
        store.remember(WS, unrelated)
        near_id = store.remember(WS, near)
        base_id = store.remember(WS, base)

        related = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.75)

        ids = [r["note_id"] for r in related]
        assert near_id in ids
        assert all(r["note_id"] != base_id for r in related)  # T7: self excluded
        # The unrelated note (cosine sim 0.0) must never appear — this is
        # the whole point of a controlled, non-hash-derived vector: the
        # assertion is checking a REAL exclusion, not a coincidence.
        assert len(related) == 1
        assert related[0]["note_id"] == near_id
        assert related[0]["similarity"] == pytest.approx(0.97, abs=1e-3)

    def test_related_note_fields(self, tmp_path) -> None:
        base = "content A"
        near = "content B"
        embedder = _ControlledEmbedder({base: BASE_VEC, near: _unit_vector_at_cosine(0.9)})
        store = _store(tmp_path, embedder)
        near_id = store.remember(WS, near, kind="gotcha", priority="high", title="B title")
        base_id = store.remember(WS, base)

        related = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)

        assert len(related) == 1
        r: RelatedNote = related[0]
        assert r["note_id"] == near_id
        assert r["title"] == "B title"
        assert r["kind"] == "gotcha"
        assert r["priority"] == "high"
        assert isinstance(r["created_at"], float)


class TestRelatedActiveNotesLifecycle:
    def test_excludes_revoked_note(self, tmp_path) -> None:
        base = "base content"
        revoked = "near content that gets revoked"
        embedder = _ControlledEmbedder({base: BASE_VEC, revoked: _unit_vector_at_cosine(0.95)})
        store = _store(tmp_path, embedder)
        revoked_id = store.remember(WS, revoked)
        base_id = store.remember(WS, base)

        # Sanity: before revocation, the candidate is offered.
        before = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)
        assert any(r["note_id"] == revoked_id for r in before)

        assert store.revoke_note(WS, revoked_id, reason="wrong") is True

        after = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)
        assert all(r["note_id"] != revoked_id for r in after)

    def test_excludes_superseded_note(self, tmp_path) -> None:
        base = "base content 2"
        superseded = "near content that gets superseded"
        successor = "totally unrelated successor content"
        embedder = _ControlledEmbedder({
            base: BASE_VEC,
            superseded: _unit_vector_at_cosine(0.95),
            successor: UNRELATED_VEC,
        })
        store = _store(tmp_path, embedder)
        superseded_id = store.remember(WS, superseded)
        base_id = store.remember(WS, base)

        before = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)
        assert any(r["note_id"] == superseded_id for r in before)

        store.remember(WS, successor, supersedes=superseded_id)

        after = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)
        assert all(r["note_id"] != superseded_id for r in after)


class TestRelatedActiveNotesFloorAndLimit:
    def _three_candidate_store(self, tmp_path):
        base = "base content 3"
        near_a = "near a"   # sim 0.90
        near_b = "near b"   # sim 0.85
        near_c = "near c"   # sim 0.80
        embedder = _ControlledEmbedder({
            base: BASE_VEC,
            near_a: _unit_vector_at_cosine(0.90),
            near_b: _unit_vector_at_cosine(0.85),
            near_c: _unit_vector_at_cosine(0.80),
        })
        store = _store(tmp_path, embedder)
        id_a = store.remember(WS, near_a)
        id_b = store.remember(WS, near_b)
        id_c = store.remember(WS, near_c)
        base_id = store.remember(WS, base)
        return store, base_id, id_a, id_b, id_c

    def test_raising_the_floor_excludes_everything(self, tmp_path) -> None:
        store, base_id, id_a, id_b, id_c = self._three_candidate_store(tmp_path)

        # 0.75 floor: all three (0.90/0.85/0.80) qualify.
        loose = store.related_active_notes(WS, base_id, limit=10, min_similarity=0.75)
        assert {r["note_id"] for r in loose} == {id_a, id_b, id_c}

        # 0.95 floor: the closest candidate is 0.90 — none qualify.
        strict = store.related_active_notes(WS, base_id, limit=10, min_similarity=0.95)
        assert strict == []

    def test_limit_caps_the_returned_count_highest_similarity_first(self, tmp_path) -> None:
        store, base_id, id_a, id_b, id_c = self._three_candidate_store(tmp_path)

        capped = store.related_active_notes(WS, base_id, limit=2, min_similarity=0.75)
        assert len(capped) == 2
        assert [r["note_id"] for r in capped] == [id_a, id_b]  # 0.90 then 0.85

    def test_limit_zero_or_negative_returns_empty(self, tmp_path) -> None:
        store, base_id, *_ = self._three_candidate_store(tmp_path)
        assert store.related_active_notes(WS, base_id, limit=0, min_similarity=0.5) == []
        assert store.related_active_notes(WS, base_id, limit=-1, min_similarity=0.5) == []


class TestRelatedActiveNotesFailSafe:
    def test_no_embedder_returns_empty_list(self, tmp_path) -> None:
        store = WorkingContextStore(str(tmp_path))  # embed_fn=None, no chroma client
        note_id = store.remember(WS, "a plain note, no embedder attached")
        assert store.related_active_notes(WS, note_id, limit=5, min_similarity=0.5) == []

    def test_collection_query_raising_returns_empty_not_exception(self, tmp_path) -> None:
        embedder = _ControlledEmbedder({"solo note": BASE_VEC})
        store = _store(tmp_path, embedder)
        note_id = store.remember(WS, "solo note")

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated chroma failure")

        store._notes_col.query = _boom  # simulate a real backend failure, not a return-shape mock

        assert store.related_active_notes(WS, note_id, limit=5, min_similarity=0.5) == []


class TestRelatedActiveNotesZeroInference:
    def test_no_embed_calls_when_vector_already_in_collection(self, tmp_path) -> None:
        base = "zero inference base"
        near = "zero inference near"
        embedder = _ControlledEmbedder({base: BASE_VEC, near: _unit_vector_at_cosine(0.9)})
        store = _store(tmp_path, embedder)
        store.remember(WS, near)
        base_id = store.remember(WS, base)

        embedder.call_count = 0  # both writes above are done; reset before the call under test
        store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)

        assert embedder.call_count == 0

    def test_fallback_embeds_exactly_once_when_vector_missing_from_collection(self, tmp_path) -> None:
        """Documented fallback path (not the normal case): if a note exists
        in SQLite but its vector isn't in the collection, the function
        embeds its content ONCE rather than returning nothing."""
        base = "fallback base content"
        near = "fallback near content"
        embedder = _ControlledEmbedder({base: BASE_VEC, near: _unit_vector_at_cosine(0.9)})
        store = _store(tmp_path, embedder)
        near_id = store.remember(WS, near)
        base_id = store.remember(WS, base)

        # Simulate a note whose vector never made it into the collection.
        store._notes_col.delete(ids=[str(base_id)])

        embedder.call_count = 0
        related = store.related_active_notes(WS, base_id, limit=5, min_similarity=0.5)

        assert embedder.call_count == 1
        assert any(r["note_id"] == near_id for r in related)
