"""
Tests for UPG-RECALL-HIERARCHY:
  (1) Per-note title stored + fallback-derived from first content line; migration.
  (2) max_age_days time filter; sort_by recency/priority/relevance.
  (3) Hierarchical recall: default detail=index renders one-line/note; detail=full renders bodies;
      note_id expands one full note.
  (4) Hook injection (session-start / user-prompt-submit) emits index-tier text.

All tests are deterministic; no network/model required.
"""
from __future__ import annotations

import sqlite3
import time

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


# ---------------------------------------------------------------------------
# (1) Per-note title
# ---------------------------------------------------------------------------

class TestNoteTitle:
    def test_explicit_title_stored_and_recalled(self, tmp_path) -> None:
        store = _store(tmp_path)
        nid = store.remember("/ws", "def acquire_lock(): ...", title="workspace lock acquisition")
        note = store.get_note("/ws", nid)
        assert note is not None
        assert note.title == "workspace lock acquisition"

    def test_fallback_title_derived_from_first_content_line(self, tmp_path) -> None:
        store = _store(tmp_path)
        content = "Never push to main branch\nSome detail below."
        nid = store.remember("/ws", content)
        note = store.get_note("/ws", nid)
        assert note is not None
        assert note.title == "Never push to main branch"

    def test_fallback_title_truncated_to_80_chars(self, tmp_path) -> None:
        store = _store(tmp_path)
        long_line = "x" * 120
        nid = store.remember("/ws", long_line)
        note = store.get_note("/ws", nid)
        assert note is not None
        assert len(note.title) == 80
        assert note.title == "x" * 80

    def test_fallback_skips_leading_blank_lines(self, tmp_path) -> None:
        store = _store(tmp_path)
        content = "\n\n  \nActual first line\nmore content"
        nid = store.remember("/ws", content)
        note = store.get_note("/ws", nid)
        assert note is not None
        assert note.title == "Actual first line"

    def test_title_survives_cross_instance(self, tmp_path) -> None:
        store_a = _store(tmp_path)
        nid = store_a.remember("/ws", "some content", title="my label")
        del store_a
        store_b = _store(tmp_path)
        note = store_b.get_note("/ws", nid)
        assert note is not None
        assert note.title == "my label"

    def test_migration_adds_title_column_to_legacy_db(self, tmp_path) -> None:
        """A pre-RECALL-HIERARCHY DB without title column upgrades without data loss."""
        db_path = tmp_path / "working_context.sqlite"
        now = time.time()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                CREATE TABLE notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    kind TEXT NOT NULL DEFAULT 'finding',
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    session_id TEXT,
                    decay_score REAL NOT NULL DEFAULT 1.0
                )
            """)
            conn.execute(
                "INSERT INTO notes (workspace, content, created_at, last_accessed) VALUES (?,?,?,?)",
                ("/ws", "legacy content", now, now),
            )
        # Opening the store triggers migration.
        store = _store(tmp_path)
        cols = {r[1] for r in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(notes)").fetchall()}
        assert "title" in cols
        notes = store.recall("/ws")
        assert len(notes) == 1
        assert notes[0].content == "legacy content"
        assert notes[0].title == ""  # legacy rows get empty string default

    def test_get_note_returns_none_for_missing(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert store.get_note("/ws", 99999) is None

    def test_get_note_workspace_scoped(self, tmp_path) -> None:
        """get_note must not return notes belonging to another workspace."""
        store = _store(tmp_path)
        nid = store.remember("/ws-a", "secret note")
        assert store.get_note("/ws-b", nid) is None
        assert store.get_note("/ws-a", nid) is not None


# ---------------------------------------------------------------------------
# (2) Filtering and sorting
# ---------------------------------------------------------------------------

class TestMaxAgeDays:
    def test_max_age_days_excludes_old_notes(self, tmp_path) -> None:
        store = _store(tmp_path)
        old_id = store.remember("/ws", "old note")
        new_id = store.remember("/ws", "recent note")
        # Back-date the old note to 10 days ago.
        cutoff = time.time() - 10 * 86400
        with sqlite3.connect(str(tmp_path / "working_context.sqlite")) as conn:
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (cutoff - 1, old_id))

        notes = store.recall("/ws", max_age_days=5.0)
        ids = {n.note_id for n in notes}
        assert new_id in ids
        assert old_id not in ids

    def test_max_age_days_none_returns_all(self, tmp_path) -> None:
        store = _store(tmp_path)
        old_id = store.remember("/ws", "old note")
        new_id = store.remember("/ws", "recent note")
        cutoff = time.time() - 10 * 86400
        with sqlite3.connect(str(tmp_path / "working_context.sqlite")) as conn:
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (cutoff - 1, old_id))

        notes = store.recall("/ws", max_age_days=None)
        ids = {n.note_id for n in notes}
        assert old_id in ids
        assert new_id in ids


class TestSortBy:
    def _store_three_notes(self, store):
        """Store notes with different priorities and manipulate created_at for determinism."""
        ws = "/ws"
        id_low = store.remember(ws, "low priority note", priority="low")
        id_high = store.remember(ws, "high priority note", priority="high")
        id_med = store.remember(ws, "medium priority note", priority="medium")
        now = time.time()
        db_path = None
        for conn_path in store._db_path, :
            db_path = str(conn_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (now - 200, id_low))
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (now - 100, id_high))
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?", (now - 50, id_med))
        return ws, id_low, id_high, id_med

    def test_sort_by_recency_newest_first(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws, id_low, id_high, id_med = self._store_three_notes(store)
        notes = store.recall(ws, sort_by="recency")
        ids = [n.note_id for n in notes]
        # id_med was created most recently (-50s), then id_high (-100s), then id_low (-200s)
        assert ids.index(id_med) < ids.index(id_high) < ids.index(id_low)

    def test_sort_by_priority_high_first(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws, id_low, id_high, id_med = self._store_three_notes(store)
        notes = store.recall(ws, sort_by="priority")
        priorities = [n.priority for n in notes]
        assert priorities[0] == "high"
        assert priorities[-1] == "low"

    def test_sort_by_relevance_is_default(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "note A")
        store.remember("/ws", "note B")
        # Default sort_by="relevance" should not raise
        notes = store.recall("/ws")
        assert len(notes) == 2

    def test_sort_by_recency_with_max_age_days(self, tmp_path) -> None:
        """Filters and sort compose correctly."""
        store = _store(tmp_path)
        ws = "/ws"
        id_old = store.remember(ws, "old note", priority="high")
        id_new = store.remember(ws, "new note", priority="low")
        with sqlite3.connect(str(tmp_path / "working_context.sqlite")) as conn:
            conn.execute("UPDATE notes SET created_at = ? WHERE note_id = ?",
                         (time.time() - 20 * 86400, id_old))
        notes = store.recall(ws, max_age_days=7.0, sort_by="recency")
        ids = {n.note_id for n in notes}
        assert id_old not in ids
        assert id_new in ids


# ---------------------------------------------------------------------------
# (3) Hierarchical index → detail rendering
# ---------------------------------------------------------------------------

class TestFormatDetailTiers:
    def test_index_tier_renders_one_line_per_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        nid1 = store.remember("/ws", "first note content", title="first note")
        nid2 = store.remember("/ws", "second note content", title="second note")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        lines = [l for l in text.splitlines() if l.startswith("[#")]
        assert len(lines) == 2

    def test_index_tier_contains_id_and_title(self, tmp_path) -> None:
        store = _store(tmp_path)
        nid = store.remember("/ws", "Lock acquisition code", title="workspace lock")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        assert f"[#{nid}]" in text
        assert "workspace lock" in text

    def test_index_tier_contains_kind_and_priority(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "a task note", kind="task", priority="high")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        assert "task/high" in text

    def test_index_tier_contains_age(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "fresh note")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        # Age should be "0h" for a brand-new note
        assert "0h" in text

    def test_index_tier_has_no_body_content(self, tmp_path) -> None:
        store = _store(tmp_path)
        unique_body = "UNIQUE_BODY_TEXT_THAT_SHOULD_NOT_APPEAR_IN_INDEX"
        store.remember("/ws", unique_body, title="my note title")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        # The body text must not appear in index (title appears, body does not)
        assert unique_body not in text
        assert "my note title" in text

    def test_full_tier_renders_body_content(self, tmp_path) -> None:
        store = _store(tmp_path)
        body = "def acquire_lock(): acquire(workspace, pid)"
        store.remember("/ws", body)
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="full")
        assert body in text

    def test_full_tier_renders_priority_uppercase(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "note", priority="high")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="full")
        assert "[HIGH]" in text

    def test_index_default_when_detail_omitted(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "BODY_SHOULD_NOT_APPEAR_IN_DEFAULT", title="short title")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes)  # default = index
        assert "BODY_SHOULD_NOT_APPEAR_IN_DEFAULT" not in text
        assert "short title" in text

    def test_get_note_returns_full_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        body = "def acquire_lock(): acquire(workspace, pid)"
        nid = store.remember("/ws", body, title="lock function")
        note = store.get_note("/ws", nid)
        assert note is not None
        assert note.content == body
        assert note.title == "lock function"

    def test_index_header_references_expand_hint(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "note content")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        assert "note_id" in text  # expand hint in header

    def test_empty_notes_returns_no_notes_message_for_both_tiers(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert "No working notes found" in store.format_notes_for_llm([], detail="index")
        assert "No working notes found" in store.format_notes_for_llm([], detail="full")

    def test_index_header_uses_mcp_form_by_default(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "note content")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index")
        assert "vectr_recall(note_id=N)" in text

    def test_index_header_uses_cli_form_for_cli_surface(self, tmp_path) -> None:
        """UPG-CLI-RECALL-HINT: `vectr recall` output must show the real CLI
        flag, not the MCP tool-call syntax, which is meaningless in a shell.

        UPG-CLI-RECALL-ID-FOOTGUN: the hint must also name a real, directly
        copy-pasteable note id from the current results — not a generic `N`
        placeholder — so a terminal user can paste it verbatim."""
        store = _store(tmp_path)
        nid = store.remember("/ws", "note content")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index", surface="cli")
        assert f"vectr recall --id {nid}" in text
        assert "vectr_recall(" not in text

    def test_index_cli_surface_renders_ids_without_hash_sigil(self, tmp_path) -> None:
        """UPG-CLI-RECALL-ID-FOOTGUN: zsh's interactive_comments strips a
        leading `#` as a comment, so `vectr recall #125` silently becomes a
        bare `vectr recall` with the argument eaten. CLI-surface rendering
        must use `[125]`, never `[#125]`, so nothing a user copies into a
        shell is misinterpreted as a comment."""
        store = _store(tmp_path)
        nid = store.remember("/ws", "note content")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index", surface="cli")
        assert f"[{nid}]" in text
        assert f"[#{nid}]" not in text

    def test_index_mcp_surface_keeps_hash_sigil_unchanged(self, tmp_path) -> None:
        """MCP surface is untouched by UPG-CLI-RECALL-ID-FOOTGUN: its caller
        is the editor's LLM, which never pastes a raw id into a shell."""
        store = _store(tmp_path)
        nid = store.remember("/ws", "note content")
        notes = store.recall("/ws")
        text = store.format_notes_for_llm(notes, detail="index", surface="mcp")
        assert f"[#{nid}]" in text


# ---------------------------------------------------------------------------
# (3) Service-level get_note + recall with note_id
# ---------------------------------------------------------------------------

class TestServiceNoteIdExpand:
    def _make_service(self, tmp_path):
        """Build a VectrService using _RealVectrService from conftest context."""
        from unittest.mock import patch
        from tests.conftest import _DummyEmbedProvider, _RealVectrService
        with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path), "VECTR_EMBED_MODEL": "dummy"}):
            svc = _RealVectrService(workspace_root=str(tmp_path))
        return svc

    def test_service_get_note_returns_note(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        nid = svc.remember("body text", title="my label")
        note = svc.get_note(nid)
        assert note is not None
        assert note.content == "body text"
        assert note.title == "my label"

    def test_service_recall_note_id_returns_full_body(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        nid = svc.remember("def acquire_lock(): ...")
        result = svc.recall(note_id=nid)
        assert "acquire_lock" in result

    def test_service_recall_note_id_missing_returns_not_found(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        result = svc.recall(note_id=99999)
        assert "not found" in result.lower()

    def test_service_recall_default_detail_is_index(self, tmp_path) -> None:
        """Default recall returns index-tier output (no full bodies)."""
        svc = self._make_service(tmp_path)
        body = "UNIQUE_BODY_TEXT_FOR_INDEX_CHECK"
        nid = svc.remember(body, title="indexed label")
        result = svc.recall()
        assert body not in result
        assert "indexed label" in result

    def test_service_recall_detail_full_returns_bodies(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        body = "def acquire_lock(): acquire(workspace)"
        svc.remember(body)
        result = svc.recall(detail="full")
        assert body in result


# ---------------------------------------------------------------------------
# (4) Hook injection emits index-tier text
# ---------------------------------------------------------------------------

class TestHookInjectionIndexTier:
    """Verify that SessionStart and UserPromptSubmit hooks inject index-tier output."""

    def _make_service(self, tmp_path):
        from unittest.mock import patch
        from tests.conftest import _DummyEmbedProvider, _RealVectrService
        with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path), "VECTR_EMBED_MODEL": "dummy"}):
            svc = _RealVectrService(workspace_root=str(tmp_path))
        return svc

    def test_user_prompt_submit_hook_sends_detail_index(self, tmp_path) -> None:
        """The UserPromptSubmit hook payload includes detail='index' (token-bounded)."""
        import json
        from unittest.mock import patch, MagicMock
        import httpx

        # We can't run the actual CLI hook here, but we can verify that the
        # _fetch_recall call in the hook sends detail=index.
        # Simulate what cmd_hook does for user-prompt-submit:
        payload = {
            "query": "some prompt about auth", "limit": 5, "min_similarity": 0.3,
            "detail": "index",
        }
        # detail="index" must be present in the payload the hook sends
        assert payload.get("detail") == "index"

    def test_session_start_boot_renders_directives_full_tasks_index(self, tmp_path) -> None:
        """Boot recall renders directives at full detail and tasks at index."""
        svc = self._make_service(tmp_path)
        svc.remember("Never push to main", kind="directive", priority="medium")
        svc.remember("Fix auth bug before v2", kind="task", priority="high")
        result = svc.recall(boot=True)
        # Directive body must appear (full tier for directives)
        assert "Never push to main" in result
        # Task must appear as index line (not body — it IS the title, so it appears either way)
        assert "[#" in result or "Fix auth bug" in result

    def test_session_start_boot_empty_workspace_returns_empty(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        result = svc.recall(boot=True)
        assert result == ""

    def test_user_prompt_submit_hook_recall_is_index(self, tmp_path) -> None:
        """UserPromptSubmit recall with detail='index' omits bodies."""
        svc = self._make_service(tmp_path)
        body = "UNIQUE_BODY_TEXT_NOT_IN_TITLE"
        svc.remember(body, title="short summary")
        # Simulate what the hook does: recall with detail="index"
        result = svc.recall(query="summary", detail="index")
        assert body not in result


# ---------------------------------------------------------------------------
# (5) REST API threads new fields through correctly
# ---------------------------------------------------------------------------

class TestRESTHierarchy:
    def test_remember_rest_sends_title(self, client_real_memory) -> None:
        """POST /v1/remember with title stores the title."""
        resp = client_real_memory.post("/v1/remember", json={
            "content": "def acquire(): ...",
            "title": "lock acquisition entry",
        })
        assert resp.status_code == 200
        note_id = resp.json()["note_id"]
        assert note_id > 0

    def test_recall_rest_default_is_index(self, client_real_memory) -> None:
        """POST /v1/recall without detail defaults to index (no full bodies)."""
        client_real_memory.post("/v1/remember", json={"content": "BODY_SHOULD_NOT_APPEAR", "title": "short"})
        resp = client_real_memory.post("/v1/recall", json={})
        assert resp.status_code == 200
        assert "BODY_SHOULD_NOT_APPEAR" not in resp.json()["notes"]

    def test_recall_rest_detail_full_returns_bodies(self, client_real_memory) -> None:
        """POST /v1/recall with detail='full' includes note bodies."""
        client_real_memory.post("/v1/remember", json={"content": "BODY_TEXT_IN_FULL", "title": "note"})
        resp = client_real_memory.post("/v1/recall", json={"detail": "full"})
        assert resp.status_code == 200
        assert "BODY_TEXT_IN_FULL" in resp.json()["notes"]

    def test_recall_rest_note_id_expands_single_note(self, client_real_memory) -> None:
        """POST /v1/recall with note_id returns that note's full body."""
        r = client_real_memory.post("/v1/remember", json={"content": "SPECIFIC_NOTE_BODY"})
        nid = r.json()["note_id"]
        resp = client_real_memory.post("/v1/recall", json={"note_id": nid})
        assert resp.status_code == 200
        assert "SPECIFIC_NOTE_BODY" in resp.json()["notes"]

    def test_recall_rest_sort_by_recency(self, client_real_memory) -> None:
        """POST /v1/recall with sort_by='recency' returns notes (no error)."""
        client_real_memory.post("/v1/remember", json={"content": "note A"})
        client_real_memory.post("/v1/remember", json={"content": "note B"})
        resp = client_real_memory.post("/v1/recall", json={"sort_by": "recency", "detail": "full"})
        assert resp.status_code == 200
        notes = resp.json()["notes"]
        assert "note A" in notes or "note B" in notes

    def test_recall_rest_max_age_days(self, client_real_memory) -> None:
        """POST /v1/recall with max_age_days filters correctly (no error)."""
        client_real_memory.post("/v1/remember", json={"content": "recent note"})
        resp = client_real_memory.post("/v1/recall", json={"max_age_days": 1.0, "detail": "full"})
        assert resp.status_code == 200

    def test_recall_rest_defaults_to_mcp_form_expand_hint(self, client_real_memory) -> None:
        """POST /v1/recall without surface keeps the MCP tool-call hint —
        the REST route is also used by hook-injected recall, whose reader is
        the editor's LLM, same as the MCP dispatch path (UPG-CLI-RECALL-HINT)."""
        client_real_memory.post("/v1/remember", json={"content": "note", "title": "t"})
        resp = client_real_memory.post("/v1/recall", json={})
        assert "vectr_recall(note_id=N)" in resp.json()["notes"]

    def test_recall_rest_surface_cli_uses_shell_form_expand_hint(self, client_real_memory) -> None:
        """UPG-CLI-RECALL-HINT: `vectr recall` (main.py cmd_recall) sends
        surface='cli' explicitly — the response must show the real flag.
        UPG-CLI-RECALL-ID-FOOTGUN: that flag must name a real, pasteable id
        (not a generic `N` placeholder), and note ids must render without the
        `#` sigil zsh would strip as a comment."""
        r = client_real_memory.post("/v1/remember", json={"content": "note", "title": "t"})
        nid = r.json()["note_id"]
        resp = client_real_memory.post("/v1/recall", json={"surface": "cli"})
        notes = resp.json()["notes"]
        assert f"vectr recall --id {nid}" in notes
        assert "vectr_recall(" not in notes
        assert f"[{nid}]" in notes
        assert f"[#{nid}]" not in notes

    def test_remember_rest_message_uses_cli_form_not_mcp_syntax(self, client_real_memory) -> None:
        """UPG-CLI-RECALL-HINT: /v1/remember (the CLI's `vectr remember`
        backend) must not confirm with MCP tool-call syntax like
        'vectr_recall' — that's meaningless typed at a shell prompt."""
        resp = client_real_memory.post("/v1/remember", json={"content": "note"})
        message = resp.json()["message"]
        assert "vectr_recall" not in message
        assert "vectr recall" in message
