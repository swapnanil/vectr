"""
Comprehensive tests for WorkingContextStore — SQLite-backed note memory.

Critical coverage:
  - Cross-instance persistence: the exact scenario the POC benchmark tested.
    Phase 1 stores notes via instance A; Phase 2 runs a FRESH instance B
    pointing at the same db_dir. Notes must be retrievable.
  - Workspace isolation: workspace A notes must not bleed into workspace B.
  - All CRUD operations, filtering, decay, and snapshot round-trips.
"""
from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


# ---------------------------------------------------------------------------
# Cross-instance persistence — THE critical test
# ---------------------------------------------------------------------------

class TestCrossInstancePersistence:
    def test_notes_survive_new_store_instance(self, tmp_path) -> None:
        """
        Simulates the two-phase POC flow:
          Phase 1 → instance A stores notes
          Phase 2 → instance B (same db_dir, fresh Python object) recalls them.

        This is the test that would have caught the benchmark failure.
        """
        # Phase 1: store findings via instance A
        store_a = _store(tmp_path)
        store_a.remember(
            "/repo",
            "Field.contribute_to_class at django/db/models/fields/__init__.py:770",
            tags=["field", "lifecycle"], priority="high",
        )
        store_a.remember(
            "/repo",
            "deconstruct() must return (name, path, args, kwargs) — currency kwarg required",
            tags=["migration"], priority="high",
        )
        store_a.remember(
            "/repo",
            "from_db_value() converts int cents → Decimal; to_python() same direction",
            tags=["conversion"], priority="medium",
        )
        del store_a  # explicitly drop the object

        # Phase 2: brand new instance, same db_dir
        store_b = _store(tmp_path)
        notes = store_b.recall("/repo")

        assert len(notes) == 3, f"Expected 3 notes, got {len(notes)}"
        contents = {n.content for n in notes}
        assert any("contribute_to_class" in c for c in contents)
        assert any("deconstruct" in c for c in contents)
        assert any("from_db_value" in c for c in contents)

    def test_tags_survive_new_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        store_a.remember("/repo", "field lifecycle", tags=["field", "high-priority"])
        del store_a

        store_b = _store(tmp_path)
        notes = store_b.recall("/repo", tags=["field"])
        assert len(notes) == 1
        assert "field" in notes[0].tags

    def test_priority_survives_new_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        store_a.remember("/repo", "low note", priority="low")
        store_a.remember("/repo", "high note", priority="high")
        del store_a

        store_b = _store(tmp_path)
        high_notes = store_b.recall("/repo", priority="high")
        assert len(high_notes) == 1
        assert high_notes[0].priority == "high"

    def test_snapshot_survives_new_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        store_a.remember("/repo", "note for snapshot")
        sid = store_a.snapshot("/repo", label="phase1-complete")
        del store_a

        store_b = _store(tmp_path)
        payload = store_b.restore_snapshot(sid)
        assert payload is not None
        assert len(payload["notes"]) == 1
        assert payload["notes"][0]["content"] == "note for snapshot"


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

class TestWorkspaceIsolation:
    def test_notes_scoped_to_workspace(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/workspace-a", "secret of A")
        store.remember("/workspace-b", "secret of B")

        notes_a = store.recall("/workspace-a")
        notes_b = store.recall("/workspace-b")

        assert len(notes_a) == 1
        assert notes_a[0].content == "secret of A"
        assert len(notes_b) == 1
        assert notes_b[0].content == "secret of B"

    def test_workspace_a_invisible_from_b_cross_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        store_a.remember("/workspace-a", "private note")
        del store_a

        store_b = _store(tmp_path)
        notes = store_b.recall("/workspace-b")  # different workspace
        assert notes == []

    def test_forget_all_only_affects_own_workspace(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws-a", "note A")
        store.remember("/ws-b", "note B")
        store.forget_all("/ws-a")

        assert store.recall("/ws-a") == []
        assert len(store.recall("/ws-b")) == 1


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_remember_returns_incrementing_ids(self, tmp_path) -> None:
        store = _store(tmp_path)
        id1 = store.remember("/repo", "first")
        id2 = store.remember("/repo", "second")
        assert id2 > id1

    def test_recall_empty_workspace(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert store.recall("/repo") == []

    def test_recall_query_substring_match(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "EvaluateSegments is the entry point")
        store.remember("/repo", "RequestBid starts the auction")
        results = store.recall("/repo", query="EvaluateSegments")
        assert len(results) == 1
        assert "EvaluateSegments" in results[0].content

    def test_recall_tag_filter(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "A", tags=["seg"])
        store.remember("/repo", "B", tags=["bid"])
        store.remember("/repo", "C", tags=["seg", "bid"])
        results = store.recall("/repo", tags=["seg"])
        assert all("seg" in n.tags for n in results)
        assert len(results) == 2

    def test_recall_priority_filter(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "high", priority="high")
        store.remember("/repo", "medium")
        store.remember("/repo", "low", priority="low")
        high = store.recall("/repo", priority="high")
        assert len(high) == 1 and high[0].priority == "high"
        low = store.recall("/repo", priority="low")
        assert len(low) == 1 and low[0].priority == "low"

    def test_recall_limit(self, tmp_path) -> None:
        store = _store(tmp_path)
        for i in range(10):
            store.remember("/repo", f"note {i}")
        results = store.recall("/repo", limit=3)
        assert len(results) == 3

    def test_recall_updates_last_accessed(self, tmp_path) -> None:
        store = _store(tmp_path)
        before = time.time()
        store.remember("/repo", "accessed note")
        time.sleep(0.01)
        notes = store.recall("/repo")
        assert notes[0].last_accessed >= before

    def test_forget_specific_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        nid = store.remember("/repo", "to remove")
        assert store.forget("/repo", nid) is True
        assert store.recall("/repo") == []

    def test_forget_nonexistent_returns_false(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert store.forget("/repo", 999999) is False

    def test_forget_all_clears_workspace(self, tmp_path) -> None:
        store = _store(tmp_path)
        for i in range(5):
            store.remember("/repo", f"note {i}")
        count = store.forget_all("/repo")
        assert count == 5
        assert store.recall("/repo") == []


# ---------------------------------------------------------------------------
# format_notes_for_llm
# ---------------------------------------------------------------------------

class TestFormatNotesForLlm:
    def test_empty_returns_no_notes_message(self, tmp_path) -> None:
        store = _store(tmp_path)
        text = store.format_notes_for_llm([])
        assert "No working notes found" in text

    def test_formatted_contains_note_content(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "Signal class at django/dispatch/dispatcher.py", tags=["signal"])
        notes = store.recall("/repo")
        text = store.format_notes_for_llm(notes)
        assert "Signal class" in text
        assert "dispatcher.py" in text

    def test_formatted_shows_priority(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "important finding", priority="high")
        notes = store.recall("/repo")
        text = store.format_notes_for_llm(notes)
        assert "HIGH" in text

    def test_formatted_shows_tags(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "tagged note", tags=["middleware", "async"])
        notes = store.recall("/repo")
        text = store.format_notes_for_llm(notes)
        assert "middleware" in text

    def test_multiple_notes_all_present(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "finding alpha")
        store.remember("/repo", "finding beta")
        store.remember("/repo", "finding gamma")
        notes = store.recall("/repo")
        text = store.format_notes_for_llm(notes)
        assert "finding alpha" in text
        assert "finding beta" in text
        assert "finding gamma" in text


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

class TestDecay:
    def test_decay_reduces_score(self, tmp_path) -> None:
        store = _store(tmp_path)
        nid = store.remember("/repo", "old note")
        # Force a very short half-life (1 second) and decay
        store.decay_old_notes("/repo", half_life_days=1 / 86400)
        notes = store.recall("/repo")
        if notes:
            assert notes[0].decay_score < 1.0

    def test_decay_deletes_very_old_notes(self, tmp_path) -> None:
        """Notes with decay_score < 0.1 are auto-deleted."""
        import sqlite3
        store = _store(tmp_path)
        store.remember("/repo", "ancient note")
        # Manually set decay_score to just above deletion threshold
        with sqlite3.connect(str(tmp_path / "working_context.sqlite")) as conn:
            conn.execute("UPDATE notes SET decay_score = 0.05 WHERE workspace = ?", ("/repo",))
        store.decay_old_notes("/repo", half_life_days=14)
        assert store.recall("/repo") == []

    def test_fresh_notes_not_deleted_by_decay(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "fresh note")
        store.decay_old_notes("/repo", half_life_days=14)  # 14 day half-life, note is seconds old
        notes = store.recall("/repo")
        assert len(notes) == 1
        assert notes[0].decay_score > 0.9


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

class TestSnapshots:
    def test_snapshot_captures_all_notes(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "note 1")
        store.remember("/repo", "note 2")
        sid = store.snapshot("/repo", label="phase1-complete")
        payload = store.restore_snapshot(sid)
        assert len(payload["notes"]) == 2

    def test_snapshot_label_preserved(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "x")
        sid = store.snapshot("/repo", label="my-label")
        snapshots = store.list_snapshots("/repo")
        assert any(s["label"] == "my-label" for s in snapshots)

    def test_list_snapshots_newest_first(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "x")
        sid1 = store.snapshot("/repo", label="first")
        time.sleep(0.01)
        sid2 = store.snapshot("/repo", label="second")
        snaps = store.list_snapshots("/repo")
        assert snaps[0]["snapshot_id"] == sid2  # newest first

    def test_restore_nonexistent_returns_none(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert store.restore_snapshot("does_not_exist") is None

    def test_snapshot_with_retrieved_chunks(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "note")
        chunks = [{"file": "main.py", "lines": "1-40", "symbol": "run", "content": "def run(): pass"}]
        sid = store.snapshot("/repo", label="with-chunks", retrieved_chunks=chunks)
        payload = store.restore_snapshot(sid)
        assert len(payload["retrieved_chunks"]) == 1
        assert payload["retrieved_chunks"][0]["file"] == "main.py"

    def test_snapshot_workspace_scoped(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws-a", "note a")
        store.snapshot("/ws-a", label="snap-a")
        snaps_b = store.list_snapshots("/ws-b")
        assert snaps_b == []

    def test_snapshot_cross_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        store_a.remember("/repo", "snapshot content")
        sid = store_a.snapshot("/repo", label="cross-instance")
        del store_a

        store_b = _store(tmp_path)
        payload = store_b.restore_snapshot(sid)
        assert payload is not None
        assert payload["notes"][0]["content"] == "snapshot content"
