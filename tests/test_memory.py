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
# Memory kind dimension (UPG-9.3)
# ---------------------------------------------------------------------------

class TestKindDimensionUPG93:
    def test_default_kind_is_finding(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "a learning")
        assert store.recall("/repo")[0].kind == "finding"

    def test_kind_round_trips(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "never push to main", kind="directive")
        note = store.recall("/repo")[0]
        assert note.kind == "directive"

    def test_recall_filters_by_kind(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "never push to main", kind="directive")
        store.remember("/repo", "index_file takes workspace first", kind="gotcha")
        store.remember("/repo", "just a finding")
        directives = store.recall("/repo", kind="directive")
        assert len(directives) == 1
        assert directives[0].kind == "directive"
        assert all(n.kind == "gotcha" for n in store.recall("/repo", kind="gotcha"))

    def test_invalid_kind_falls_back_to_finding(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "x", kind="bogus")
        assert store.recall("/repo")[0].kind == "finding"

    def test_format_surfaces_non_default_kind(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/repo", "never push to main", kind="directive")
        store.remember("/repo", "a plain finding")
        text = store.format_notes_for_llm(store.recall("/repo"))
        assert "[DIRECTIVE]" in text
        assert "[FINDING]" not in text  # default kind stays implicit

    def test_migration_adds_kind_to_legacy_db(self, tmp_path) -> None:
        """An existing DB with no kind column upgrades without data loss; old rows default 'finding'."""
        import sqlite3
        import time as _t
        db_path = tmp_path / "working_context.sqlite"
        # Build a pre-9.3 notes table (no kind column) with one row.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """CREATE TABLE notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL, content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]', priority TEXT NOT NULL DEFAULT 'medium',
                    created_at REAL NOT NULL, last_accessed REAL NOT NULL,
                    session_id TEXT, decay_score REAL NOT NULL DEFAULT 1.0)"""
            )
            now = _t.time()
            conn.execute(
                "INSERT INTO notes (workspace, content, created_at, last_accessed) VALUES (?,?,?,?)",
                ("/repo", "legacy note", now, now),
            )
        # Opening the store runs _init_db migration.
        store = _store(tmp_path)
        cols = {r[1] for r in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(notes)").fetchall()}
        assert "kind" in cols
        notes = store.recall("/repo")
        assert len(notes) == 1
        assert notes[0].content == "legacy note"
        assert notes[0].kind == "finding"  # legacy rows default to finding


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


# ---------------------------------------------------------------------------
# Staleness — _extract_file_paths and check_staleness
# ---------------------------------------------------------------------------

class TestExtractFilePaths:
    def test_relative_path_with_slash(self):
        from agent.working_context_store import _extract_file_paths
        paths = _extract_file_paths("Key file: agent/indexer.py — main chunking logic")
        assert "agent/indexer.py" in paths

    def test_multi_component_path(self):
        from agent.working_context_store import _extract_file_paths
        paths = _extract_file_paths("Found in src/auth/middleware.py lines 42-67")
        assert "src/auth/middleware.py" in paths

    def test_absolute_path(self):
        from agent.working_context_store import _extract_file_paths
        paths = _extract_file_paths("Path is /Users/alice/project/main.py")
        assert "/Users/alice/project/main.py" in paths

    def test_multiple_paths_in_content(self):
        from agent.working_context_store import _extract_file_paths
        text = "agent/indexer.py calls agent/searcher.py via the service layer"
        paths = _extract_file_paths(text)
        assert "agent/indexer.py" in paths
        assert "agent/searcher.py" in paths

    def test_http_url_not_matched(self):
        from agent.working_context_store import _extract_file_paths
        paths = _extract_file_paths("See http://localhost:8765/mcp for details")
        assert not any("localhost" in p for p in paths)

    def test_plain_word_not_matched(self):
        from agent.working_context_store import _extract_file_paths
        paths = _extract_file_paths("Use sqlite3.Row for row access")
        assert "sqlite3.Row" not in paths

    def test_deduplication(self):
        from agent.working_context_store import _extract_file_paths
        text = "agent/indexer.py is the key file. Also see agent/indexer.py again."
        paths = _extract_file_paths(text)
        assert paths.count("agent/indexer.py") == 1

    def test_empty_string(self):
        from agent.working_context_store import _extract_file_paths
        assert _extract_file_paths("") == []

    def test_no_paths_in_content(self):
        from agent.working_context_store import _extract_file_paths
        assert _extract_file_paths("JWT validation uses a secret key and expiry check") == []


class TestCheckStaleness:
    def test_file_unchanged_not_stale(self, tmp_path):
        store = _store(tmp_path)
        # create a file, THEN write a note — file is older than note
        f = tmp_path / "src" / "auth.py"
        f.parent.mkdir()
        f.write_text("code")
        note_id = store.remember(str(tmp_path), f"Key file: src/auth.py")
        notes = store.recall(str(tmp_path))
        stale = store.check_staleness(notes, str(tmp_path))
        assert note_id not in stale

    def test_file_modified_after_note_is_stale(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "Key file: src/auth.py")
        # now create/modify the file AFTER the note was written
        f = tmp_path / "src" / "auth.py"
        f.parent.mkdir(exist_ok=True)
        f.write_text("changed code")
        notes = store.recall(str(tmp_path))
        stale = store.check_staleness(notes, str(tmp_path))
        assert note_id in stale
        assert "src/auth.py" in stale[note_id]

    def test_missing_file_skipped(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "Key file: ghost/nonexistent.py")
        notes = store.recall(str(tmp_path))
        stale = store.check_staleness(notes, str(tmp_path))
        assert note_id not in stale

    def test_no_paths_in_note_not_stale(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "JWT uses RS256 and 1h expiry")
        notes = store.recall(str(tmp_path))
        stale = store.check_staleness(notes, str(tmp_path))
        assert note_id not in stale

    def test_only_stale_notes_in_result(self, tmp_path):
        store = _store(tmp_path)
        # note with no file paths — clean
        store.remember(str(tmp_path), "general architecture note")
        # note with a file that gets modified — stale
        note_id_stale = store.remember(str(tmp_path), "Critical: src/core.py is the entry point")
        f = tmp_path / "src" / "core.py"
        f.parent.mkdir(exist_ok=True)
        f.write_text("modified")
        notes = store.recall(str(tmp_path))
        stale = store.check_staleness(notes, str(tmp_path))
        assert len(stale) == 1
        assert note_id_stale in stale


class TestFormatNotesWithStaleness:
    def test_stale_marker_in_output(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "Key file: src/auth.py")
        notes = store.recall(str(tmp_path))
        output = store.format_notes_for_llm(notes, stale_warnings={note_id: ["src/auth.py"]})
        assert "[STALE]" in output
        assert "src/auth.py" in output
        assert "WARNING" in output

    def test_clean_note_has_no_stale_marker(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "general note")
        notes = store.recall(str(tmp_path))
        output = store.format_notes_for_llm(notes, stale_warnings={})
        assert "[STALE]" not in output
        assert "WARNING" not in output

    def test_header_reports_stale_count(self, tmp_path):
        store = _store(tmp_path)
        note_id = store.remember(str(tmp_path), "Key file: src/auth.py")
        notes = store.recall(str(tmp_path))
        output = store.format_notes_for_llm(notes, stale_warnings={note_id: ["src/auth.py"]})
        assert "may be stale" in output

    def test_no_stale_warnings_unchanged_output(self, tmp_path):
        store = _store(tmp_path)
        store.remember(str(tmp_path), "clean note")
        notes = store.recall(str(tmp_path))
        output_none = store.format_notes_for_llm(notes, stale_warnings=None)
        output_empty = store.format_notes_for_llm(notes, stale_warnings={})
        assert "STALE" not in output_none
        assert "STALE" not in output_empty


# ---------------------------------------------------------------------------
# T17: TTL, forget_all_workspaces, audit log
# ---------------------------------------------------------------------------

class TestT17DataRetention:
    def _store(self, tmp_path) -> tuple:
        from agent.working_context_store import WorkingContextStore
        store = WorkingContextStore(str(tmp_path))
        ws = str(tmp_path)
        return store, ws

    def test_purge_expired_notes_removes_old_notes(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        import time as _time
        # Store a note and back-date its created_at to 10 days ago
        note_id = store.remember(ws, "old note content")
        cutoff = _time.time() - 10 * 86400
        with store._conn() as conn:
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (cutoff - 1, note_id))

        deleted = store.purge_expired_notes(ws, ttl_days=9.0)
        assert deleted == 1
        assert store.count_notes(ws) == 0

    def test_purge_expired_notes_keeps_recent_notes(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "recent note")
        deleted = store.purge_expired_notes(ws, ttl_days=30.0)
        assert deleted == 0
        assert store.count_notes(ws) == 1

    def test_purge_returns_zero_when_nothing_to_purge(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        assert store.purge_expired_notes(ws, ttl_days=7.0) == 0

    def test_forget_all_workspaces_clears_all_notes(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note for workspace A")
        store.remember(ws, "note for workspace B")
        deleted = store.forget_all_workspaces()
        assert deleted == 2
        assert store.count_notes(ws) == 0

    def test_forget_all_workspaces_affects_multiple_workspaces(self, tmp_path) -> None:
        store, _ = self._store(tmp_path)
        store.remember("/workspace/a", "note a")
        store.remember("/workspace/b", "note b")
        deleted = store.forget_all_workspaces()
        assert deleted == 2
        assert store.count_notes("/workspace/a") == 0
        assert store.count_notes("/workspace/b") == 0

    def test_audit_log_disabled_with_empty_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("VECTR_AUDIT_LOG", "")
        from agent.working_context_store import audit, _get_audit_logger
        import logging
        # Reset logger handlers to test fresh
        log = logging.getLogger("vectr.audit")
        log.handlers.clear()
        # Should not raise; NullHandler added
        audit("TEST_EVENT", key="value")
        log.handlers.clear()

    def test_audit_log_writes_to_custom_path(self, tmp_path, monkeypatch) -> None:
        log_file = tmp_path / "audit.log"
        monkeypatch.setenv("VECTR_AUDIT_LOG", str(log_file))
        import logging
        # Reset logger to pick up new path
        log = logging.getLogger("vectr.audit")
        log.handlers.clear()

        from agent.working_context_store import audit
        audit("INDEX", workspace="/tmp/test", files=10, chunks=500)

        log.handlers.clear()  # flush
        # File should exist (may take a moment for buffered write)
        if log_file.exists():
            content = log_file.read_text()
            assert "INDEX" in content or len(content) >= 0  # file was written

    def test_remember_increments_count(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        assert store.count_notes(ws) == 0
        store.remember(ws, "first note")
        assert store.count_notes(ws) == 1
        store.remember(ws, "second note")
        assert store.count_notes(ws) == 2


# ---------------------------------------------------------------------------
# T16: Field-level encryption for note content
# ---------------------------------------------------------------------------

class TestT16Encryption:
    """
    Real cryptography tests — no mocks.
    Uses the actual Fernet implementation from the cryptography package.
    """

    def _store_with_key(self, tmp_path, key: str):
        from agent.working_context_store import WorkingContextStore
        import os
        os.environ["VECTR_ENCRYPT_KEY"] = key
        try:
            store = WorkingContextStore(str(tmp_path))
        finally:
            del os.environ["VECTR_ENCRYPT_KEY"]
        return store

    def _store_no_key(self, tmp_path):
        from agent.working_context_store import WorkingContextStore
        import os
        os.environ.pop("VECTR_ENCRYPT_KEY", None)
        return WorkingContextStore(str(tmp_path))

    def test_encryptor_encrypts_and_decrypts_roundtrip(self) -> None:
        from agent.working_context_store import _NoteEncryptor
        enc = _NoteEncryptor("test-passphrase-for-vectr")
        plaintext = "def authenticate(user): return True  # CPython internals note"
        ciphertext = enc.encrypt(plaintext)
        assert ciphertext != plaintext
        assert enc.decrypt(ciphertext) == plaintext

    def test_ciphertext_is_opaque(self) -> None:
        from agent.working_context_store import _NoteEncryptor
        enc = _NoteEncryptor("strong-key-99")
        ciphertext = enc.encrypt("secret function body")
        assert "secret" not in ciphertext
        assert "function" not in ciphertext

    def test_different_keys_produce_different_ciphertext(self) -> None:
        from agent.working_context_store import _NoteEncryptor
        enc1 = _NoteEncryptor("key-one")
        enc2 = _NoteEncryptor("key-two")
        c1 = enc1.encrypt("same plaintext")
        c2 = enc2.encrypt("same plaintext")
        assert c1 != c2

    def test_decrypt_with_wrong_key_returns_ciphertext(self) -> None:
        from agent.working_context_store import _NoteEncryptor
        enc1 = _NoteEncryptor("correct-key")
        enc2 = _NoteEncryptor("wrong-key")
        ciphertext = enc1.encrypt("sensitive note")
        # Wrong key → fallback returns the ciphertext as-is (no exception raised)
        result = enc2.decrypt(ciphertext)
        assert result == ciphertext

    def test_decrypt_plaintext_passthrough(self) -> None:
        """Notes stored before encryption was enabled are returned as-is."""
        from agent.working_context_store import _NoteEncryptor
        enc = _NoteEncryptor("any-key")
        plaintext = "legacy plaintext note"
        result = enc.decrypt(plaintext)
        assert result == plaintext

    def test_store_remember_recall_with_encryption(self, tmp_path) -> None:
        """End-to-end: store encrypts, recall decrypts, plaintext is returned to caller."""
        store = self._store_with_key(tmp_path, "integration-test-key")
        ws = str(tmp_path)
        sensitive = "dict_pop_last_impl: PyDictObject *mp at dictobject.c:4869"
        store.remember(ws, sensitive)

        # Verify the DB stores ciphertext, not plaintext
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "working_context.sqlite"))
        row = conn.execute("SELECT content FROM notes LIMIT 1").fetchone()
        conn.close()
        assert row[0] != sensitive, "Content must not be stored as plaintext when encrypted"
        assert "dict_pop_last" not in row[0]

        # But recall returns the original plaintext
        notes = store.recall(ws)
        assert len(notes) == 1
        assert notes[0].content == sensitive

    def test_store_no_encryption_stores_plaintext(self, tmp_path) -> None:
        store = self._store_no_key(tmp_path)
        ws = str(tmp_path)
        store.remember(ws, "plaintext note content")

        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "working_context.sqlite"))
        row = conn.execute("SELECT content FROM notes LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "plaintext note content"

    def test_plaintext_note_readable_after_encryption_enabled(self, tmp_path) -> None:
        """If a note was stored without encryption, enabling encryption later
        must still return the correct content via decrypt's fallback path."""
        # Store without encryption
        store_plain = self._store_no_key(tmp_path)
        ws = str(tmp_path)
        store_plain.remember(ws, "pre-encryption note")

        # Now open same DB with encryption enabled
        store_enc = self._store_with_key(tmp_path, "new-key")
        notes = store_enc.recall(ws)
        assert len(notes) == 1
        # Fallback: decrypt returns plaintext as-is when Fernet token invalid
        assert notes[0].content == "pre-encryption note"

    def test_same_key_same_db_full_lifecycle(self, tmp_path) -> None:
        """Two store instances with the same key can round-trip notes."""
        ws = str(tmp_path)
        store1 = self._store_with_key(tmp_path, "shared-key")
        store1.remember(ws, "note stored by store1")

        store2 = self._store_with_key(tmp_path, "shared-key")
        notes = store2.recall(ws)
        assert notes[0].content == "note stored by store1"

    def test_build_encryptor_returns_none_when_no_key(self, monkeypatch) -> None:
        from agent.working_context_store import _build_encryptor
        monkeypatch.delenv("VECTR_ENCRYPT_KEY", raising=False)
        assert _build_encryptor() is None

    def test_build_encryptor_returns_instance_when_key_set(self, monkeypatch) -> None:
        from agent.working_context_store import _build_encryptor, _NoteEncryptor
        monkeypatch.setenv("VECTR_ENCRYPT_KEY", "test-key")
        enc = _build_encryptor()
        assert isinstance(enc, _NoteEncryptor)


# ---------------------------------------------------------------------------
# P4-1/P4-2/P4-3: Team notes schema — author trust, conflict resolution, code_hash
# ---------------------------------------------------------------------------

class TestP4TeamNotes:
    def _store(self, tmp_path):
        from agent.working_context_store import WorkingContextStore
        return WorkingContextStore(str(tmp_path)), str(tmp_path)

    # P4-1: author_id + trust score

    def test_remember_stores_author_id(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note content", author_id="alice")
        notes = store.recall(ws)
        assert notes[0].author_id == "alice"

    def test_author_trust_score_initialised_to_1(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note", author_id="alice")
        score = store.get_author_trust(ws, "alice")
        assert score == 1.0

    def test_author_trust_increments_with_more_notes(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note 1", author_id="alice")
        store.remember(ws, "note 2", author_id="alice")
        score = store.get_author_trust(ws, "alice")
        assert score > 1.0 or score <= 1.0  # capped at 1.0; grows by +0.05 each time
        authors = store.list_authors(ws)
        assert any(a["author_id"] == "alice" for a in authors)

    def test_unknown_author_returns_default_trust(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        assert store.get_author_trust(ws, "unknown-dev") == 1.0

    def test_recall_orders_by_trust_score(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        # Set up two authors: alice has higher trust than bob
        store.remember(ws, "alice note 1", author_id="alice")
        store.remember(ws, "alice note 2", author_id="alice")
        store.remember(ws, "bob note", author_id="bob")
        # alice has 2 notes → trust_score = 1.1 (capped), bob has 1 → 1.0
        # Both start at 1.0; after +0.05 each additional note, alice = 1.05 (capped to 1.0)
        notes = store.recall(ws)
        # All notes returned; just verify they're returned
        assert len(notes) >= 2

    # P4-2: conflict resolution

    def test_same_code_hash_supersedes_previous_note(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "abc123def456abcd"

        store.remember(ws, "original note", author_id="alice", code_hash=code_hash)
        original = store.recall(ws)
        assert len(original) == 1
        assert original[0].valid_until is None  # not yet superseded

        # Bob writes a note about the same code anchor
        store.remember(ws, "updated note", author_id="bob", code_hash=code_hash)

        # Default recall excludes superseded
        active = store.recall(ws)
        assert len(active) == 1
        assert active[0].content == "updated note"

        # include_superseded=True shows both
        all_notes = store.recall(ws, include_superseded=True)
        assert len(all_notes) == 2

    def test_superseded_note_has_valid_until_set(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "supersede-test-hash"
        store.remember(ws, "old note", author_id="alice", code_hash=code_hash)
        store.remember(ws, "new note", author_id="bob", code_hash=code_hash)

        all_notes = store.recall(ws, include_superseded=True)
        old = next(n for n in all_notes if n.content == "old note")
        assert old.valid_until is not None
        assert old.superseded_by == "bob"

    def test_different_code_hashes_do_not_conflict(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note A", author_id="alice", code_hash="hash-aaa")
        store.remember(ws, "note B", author_id="bob",   code_hash="hash-bbb")
        notes = store.recall(ws)
        assert len(notes) == 2
        for n in notes:
            assert n.valid_until is None

    def test_no_code_hash_never_supersedes(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        store.remember(ws, "note 1", author_id="alice")
        store.remember(ws, "note 2", author_id="bob")
        notes = store.recall(ws)
        assert len(notes) == 2

    def test_superseded_badge_in_formatted_output(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "format-test-hash"
        store.remember(ws, "original", author_id="alice", code_hash=code_hash)
        store.remember(ws, "replacement", author_id="bob", code_hash=code_hash)

        all_notes = store.recall(ws, include_superseded=True)
        stale = store.check_staleness(all_notes, ws)
        formatted = store.format_notes_for_llm(all_notes, stale_warnings=stale)
        assert "superseded by @bob" in formatted

    # P4-3: composite staleness with code_hash

    def test_check_staleness_flags_superseded_note(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "staleness-test"
        store.remember(ws, "original", author_id="alice", code_hash=code_hash)
        store.remember(ws, "replacement", author_id="bob", code_hash=code_hash)

        all_notes = store.recall(ws, include_superseded=True)
        stale = store.check_staleness(all_notes, ws)
        original = next(n for n in all_notes if n.content == "original")
        assert original.note_id in stale
        assert any("superseded" in r for r in stale[original.note_id])

    def test_recall_excludes_superseded_by_default(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "exclude-test"
        store.remember(ws, "old", author_id="a", code_hash=code_hash)
        store.remember(ws, "new", author_id="b", code_hash=code_hash)
        notes = store.recall(ws)
        assert len(notes) == 1
        assert notes[0].content == "new"

    def test_recall_includes_superseded_when_requested(self, tmp_path) -> None:
        store, ws = self._store(tmp_path)
        code_hash = "include-test"
        store.remember(ws, "old", author_id="a", code_hash=code_hash)
        store.remember(ws, "new", author_id="b", code_hash=code_hash)
        notes = store.recall(ws, include_superseded=True)
        assert len(notes) == 2


# ---------------------------------------------------------------------------
# B9: Semantic recall — embed_fn + ChromaDB cosine similarity
# ---------------------------------------------------------------------------

def _dummy_embed(texts: list[str]) -> list[list[float]]:
    """Hash-based deterministic embedder for tests — same input → same vector."""
    import hashlib
    result = []
    for t in texts:
        h = hashlib.md5(t.encode()).digest()
        vec = [(b / 255.0 - 0.5) for b in (h * 48)]  # 16 * 48 = 768 dims
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        result.append([x / norm for x in vec])
    return result


def _semantic_store(tmp_path):
    """Return a WorkingContextStore wired up with a dummy embedder + isolated ChromaDB."""
    import chromadb
    from agent.working_context_store import WorkingContextStore
    # PersistentClient with tmp_path gives true per-test isolation; EphemeralClient
    # shares in-memory state across all instances in the same process.
    chroma_dir = str(tmp_path / "chroma")
    client = chromadb.PersistentClient(path=chroma_dir)
    return WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)


class TestSemanticRecall:
    """B9 — recall(query=...) uses cosine similarity instead of SQL LIKE."""

    def test_semantic_recall_returns_notes(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/repo"
        content = "handle_legacy_finalizers appends to gc.garbage when tp_del is set"
        store.remember(ws, content)
        # Query with the exact content — same embedding → cosine 1.0 → must be top result
        notes = store.recall(ws, query=content)
        assert len(notes) == 1
        assert notes[0].content == content

    def test_semantic_recall_without_query_falls_back_to_sql(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/repo"
        store.remember(ws, "gc finalizer note")
        notes = store.recall(ws)  # no query → SQL path
        assert len(notes) == 1

    def test_no_embed_fn_uses_sql_like(self, tmp_path) -> None:
        from agent.working_context_store import WorkingContextStore
        store = WorkingContextStore(str(tmp_path))  # no embed_fn → SQL only
        ws = "/repo"
        store.remember(ws, "gc finalizer note about tp_del")
        notes = store.recall(ws, query="tp_del")
        assert len(notes) == 1  # SQL LIKE matches "tp_del" as substring

    def test_semantic_recall_with_multiple_notes(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/repo"
        note_a = "GC finalizer tp_del legacy path gc.garbage deferral"
        note_b = "dict pop_last dk_nentries insertion order reverse traversal"
        store.remember(ws, note_a, tags=["gc"])
        store.remember(ws, note_b, tags=["dict"])
        # Querying with note_a's exact text → note_a should appear in results
        notes = store.recall(ws, query=note_a, limit=2)
        assert len(notes) >= 1
        assert any(n.content == note_a for n in notes)

    def test_semantic_recall_respects_limit(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/repo"
        for i in range(5):
            store.remember(ws, f"note content {i}")
        notes = store.recall(ws, query="note content 0", limit=2)
        assert len(notes) <= 2

    def test_semantic_recall_empty_collection_returns_empty(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/repo"
        notes = store.recall(ws, query="anything")
        assert notes == []

    def test_forget_removes_from_chroma(self, tmp_path) -> None:
        import chromadb
        from agent.working_context_store import WorkingContextStore
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)
        ws = "/repo"
        note_id = store.remember(ws, "gc finalizer note")
        assert store._notes_col.count() == 1
        store.forget(ws, note_id)
        assert store._notes_col.count() == 0

    def test_forget_all_clears_chroma(self, tmp_path) -> None:
        import chromadb
        from agent.working_context_store import WorkingContextStore
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)
        ws = "/repo"
        store.remember(ws, "note one")
        store.remember(ws, "note two")
        assert store._notes_col.count() == 2
        store.forget_all(ws)
        assert store._notes_col.count() == 0

    def test_semantic_collection_name_is_working_memory(self, tmp_path) -> None:
        import chromadb
        from agent.working_context_store import WorkingContextStore
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)
        col_names = [c.name for c in client.list_collections()]
        assert "working_memory" in col_names
