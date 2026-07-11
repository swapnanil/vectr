"""Store-level scored recall tests (UPG-PRO-1).

recall_scored surfaces the per-note cosine similarity the semantic path already
computes. The scoreless recall() must stay byte-for-byte unchanged (regression).
"""
from __future__ import annotations


def _dummy_embed(texts):
    import hashlib
    out = []
    for t in texts:
        h = hashlib.md5(t.encode()).digest()
        vec = [(b / 255.0 - 0.5) for b in (h * 48)]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        out.append([x / norm for x in vec])
    return out


def _store(tmp_path):
    import chromadb
    from agent.working_context_store import WorkingContextStore
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)


def test_recall_scored_semantic_has_scores(tmp_path):
    store = _store(tmp_path)
    ws = "/repo"
    content = "WorkspaceLock.acquire takes a PID-scoped lock and drops it on scope exit"
    store.remember(ws, content)
    scored = store.recall_scored(ws, query=content, limit=5)
    assert scored
    note, score = scored[0]
    assert note.content == content
    assert score is not None
    assert 0.0 <= score <= 1.0001  # cosine similarity, not a distance


def test_recall_scored_ordering_matches_scoreless(tmp_path):
    store = _store(tmp_path)
    ws = "/repo"
    for c in ["alpha lock note here", "beta index note here", "gamma trace note here"]:
        store.remember(ws, c)
    q = "alpha lock note here"
    scoreless = [n.note_id for n in store.recall(ws, query=q, limit=5)]
    scored = [n.note_id for (n, _s) in store.recall_scored(ws, query=q, limit=5)]
    assert scored == scoreless  # identical ordering


def test_recall_scored_sql_fallback_returns_none(tmp_path):
    store = _store(tmp_path)
    ws = "/repo"
    store.remember(ws, "a note body")
    scored = store.recall_scored(ws, query=None, limit=5)  # no query -> SQL LIKE
    assert scored
    assert all(s is None for (_n, s) in scored)  # never fabricated


def test_recall_scored_floor_excludes_below(tmp_path):
    store = _store(tmp_path)
    ws = "/repo"
    store.remember(ws, "completely unrelated content about databases")
    # A very high floor drops the nearest-but-irrelevant note.
    scored = store.recall_scored(ws, query="xylophone zebra quasar", min_similarity=0.99, limit=5)
    assert scored == []
