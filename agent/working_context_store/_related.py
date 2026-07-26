"""Write-time related-notes lookup.

Surfaces the nearest EXISTING ACTIVE notes at write time so the caller LLM
can judge whether one is superseded or contradicted by the note it is about
to write. This module NEVER asserts a contradiction and NEVER mutates
anything: the research basis is that correct and incorrect notes'
semantic-similarity distributions overlap, so no threshold can separate a
contradiction from an agreement — vectr supplies the arithmetic (nearest
existing notes, by cosine similarity, already-active only), the caller
supplies the judgment.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING, TypedDict

from agent.chroma_dispatch import timed_chroma_call

if TYPE_CHECKING:
    from agent.working_context_store._store import WorkingContextStore


class RelatedNote(TypedDict):
    note_id: int
    title: str
    kind: str
    priority: str
    similarity: float
    created_at: float


def _as_plain_vector(raw: Any) -> list[float] | None:
    """Normalize one ChromaDB-returned embedding to a plain list[float].
    Depending on the installed backend, Chroma may hand back a numpy array
    or a nested python list for the same field; callers here only ever want
    one shape. Returns None for anything empty/unusable."""
    if raw is None:
        return None
    tolist = getattr(raw, "tolist", None)  # numpy arrays expose this; plain lists/tuples do not
    if callable(tolist):
        raw = tolist()
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        return None
    return [float(x) for x in raw]


def related_active_notes(
    store: "WorkingContextStore",
    workspace: str,
    note_id: int,
    *,
    limit: int,
    min_similarity: float,
) -> list[RelatedNote]:
    """The `limit` nearest EXISTING, currently-ACTIVE notes to `note_id`'s
    already-stored vector, above `min_similarity`, most-similar first.

    Called from inside the write path (`remember()`), which is itself
    already dispatched off the request event loop's single-worker chroma
    executor (see agent/chroma_dispatch.py). This function must therefore
    NEVER call `dispatch_chroma_sync`/`dispatch_chroma_async` itself: that
    executor has exactly one worker thread, so a nested dispatch issued
    from a task already running ON that worker would wait forever for a
    worker slot that only the very call doing the waiting could free.
    Every chroma call below runs directly on the calling thread, wrapped
    only in `timed_chroma_call` for the same slow-call logging every other
    chroma call site gets.

    Returns [] immediately (never raises) when there is no embedder/notes
    collection attached, when `limit <= 0`, or on ANY internal failure — a
    related-notes lookup must never break or slow-fail the write that
    triggers it.
    """
    if store._notes_col is None or store._embed_fn is None or limit <= 0:
        return []

    try:
        col = store._notes_col

        # Fast path (the normal case): reuse the vector already upserted
        # for this note at write time. Zero added inference — no re-embed.
        with timed_chroma_call("get"):
            got = col.get(ids=[str(note_id)], include=["embeddings"])
        vec: list[float] | None = None
        embeddings = (got or {}).get("embeddings")
        if embeddings is not None and len(embeddings) > 0:
            vec = _as_plain_vector(embeddings[0])

        if vec is None:
            # Documented fallback, not the normal path: the note's vector
            # wasn't found in the collection (e.g. it was written before an
            # embedder was attached). Embed its stored content once — the
            # only place in this function that adds an embed call.
            note = store.get_note(workspace, note_id)
            if note is None or not note.content:
                return []
            vec = store._embed_fn([note.content])[0]

        with timed_chroma_call("count"):
            col_count = col.count()

        with timed_chroma_call("query"):
            results = col.query(query_embeddings=[vec], n_results=min(limit + 1, col_count))

        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        raw_ids = results["ids"][0]
        distances = (results.get("distances") or [[None] * len(raw_ids)])[0]

        sim_by_id: dict[int, float] = {}
        for raw_id, dist in zip(raw_ids, distances):
            if dist is None:
                continue
            cand_id = int(raw_id)
            if cand_id == note_id:
                continue
            similarity = 1.0 - dist
            if similarity < min_similarity:
                continue
            sim_by_id[cand_id] = similarity

        if not sim_by_id:
            return []

        candidate_ids = list(sim_by_id.keys())
        placeholders = ",".join("?" * len(candidate_ids))
        with store._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM notes WHERE workspace = ? AND note_id IN ({placeholders}) "
                "AND valid_until IS NULL",
                [workspace, *candidate_ids],
            ).fetchall()

        if not rows:
            return []

        notes = [store._row_to_note(r) for r in rows]

        # Keep only notes whose folded lifecycle state is "active" — a
        # note_id absent from this dict is active (pre-migration
        # convention every existing caller of note_event_states applies).
        states = store.note_event_states(workspace, notes)
        active_notes = [
            n for n in notes
            if states.get(n.note_id, {}).get("state", "active") == "active"
        ]
        if not active_notes:
            return []

        # Lazy import: agent/working_context_store/_store.py imports
        # related_active_notes (this function) at module load time, so a
        # top-level import back of `_note_title` here would be a circular
        # import. Deferring it to call time breaks the cycle — by the time
        # this function actually runs, both modules are fully loaded.
        from agent.working_context_store._store import _note_title

        related: list[RelatedNote] = [
            RelatedNote(
                note_id=n.note_id,
                title=_note_title(n),
                kind=n.kind or "",
                priority=n.priority or "",
                similarity=sim_by_id[n.note_id],
                created_at=n.created_at,
            )
            for n in active_notes
        ]
        related.sort(key=lambda r: (-r["similarity"], r["note_id"]))
        return related[:limit]
    except Exception:
        # A related-notes failure must never break or slow-fail the write
        # path that calls this — always degrade to "nothing to offer".
        return []
