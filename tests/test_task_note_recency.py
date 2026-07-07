"""Tests for UPG-TASK-NOTE-INJECTION-RECENCY.

kind='task' notes are current-work state, not relevance-ranked learnings.
The default (no-query) recall ordering and the SessionStart boot-injection
path must order task notes strictly newest-first (created_at DESC, note_id
DESC tie-break), ignoring author_trust_score/decay_score entirely — an older
task note that happens to carry a higher trust/decay score must never
outrank the newer checkpoint. Every other kind keeps the pre-existing
deterministic trust/decay ordering (UPG-RECALL-ORDER-CHURN, ec79301).

This is a kind-based structural ordering rule (a note PROPERTY), not a
query-side heuristic: no query content is ever inspected.
"""
from __future__ import annotations

import sqlite3
import time

import pytest


def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


def _bump_trust_and_decay(tmp_path, note_id: int, trust: float, decay: float) -> None:
    """Directly manipulate a note's trust/decay columns to simulate the
    reported live scenario: an older note that happens to carry a higher
    trust/decay score than a newer one."""
    with sqlite3.connect(str(tmp_path / "working_context.sqlite")) as conn:
        conn.execute(
            "UPDATE notes SET author_trust_score = ?, decay_score = ? WHERE note_id = ?",
            (trust, decay, note_id),
        )


# ---------------------------------------------------------------------------
# recall() default (no-query) SQL path
# ---------------------------------------------------------------------------

class TestRecallTaskRecencyOverride:
    def test_newer_task_note_outranks_older_higher_trust_task_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        old_id = store.remember("/ws", "old pause note", kind="task", priority="high")
        new_id = store.remember("/ws", "current checkpoint", kind="task", priority="high")
        # Simulate the live-witness scenario: the OLDER note carries higher
        # trust/decay than the newer one.
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.5, decay=0.5)

        notes = store.recall("/ws", kind="task")
        ids = [n.note_id for n in notes]
        assert ids.index(new_id) < ids.index(old_id), (
            "newer task note must render before an older, higher-trust task note"
        )

    def test_task_ordering_ignores_trust_and_decay_entirely(self, tmp_path) -> None:
        """Three task notes, oldest has the highest trust/decay, newest the
        lowest — recall() must still return them purely newest-first."""
        store = _store(tmp_path)
        ids_in_creation_order = []
        for i in range(3):
            nid = store.remember("/ws", f"task note {i}", kind="task", priority="high")
            ids_in_creation_order.append(nid)
        # Inversely correlate trust/decay with recency.
        _bump_trust_and_decay(tmp_path, ids_in_creation_order[0], trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, ids_in_creation_order[1], trust=0.8, decay=0.8)
        _bump_trust_and_decay(tmp_path, ids_in_creation_order[2], trust=0.1, decay=0.1)

        notes = store.recall("/ws", kind="task")
        ids = [n.note_id for n in notes]
        assert ids == list(reversed(ids_in_creation_order))

    def test_non_task_ordering_unchanged_by_trust_decay(self, tmp_path) -> None:
        """UPG-RECALL-ORDER-CHURN's pre-existing trust/decay ordering must be
        untouched for kinds other than 'task' — a higher-trust older finding
        still outranks a lower-trust newer one."""
        store = _store(tmp_path)
        old_id = store.remember("/ws", "old finding", kind="finding")
        new_id = store.remember("/ws", "new finding", kind="finding")
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.2, decay=0.2)

        notes = store.recall("/ws", kind="finding")
        ids = [n.note_id for n in notes]
        assert ids.index(old_id) < ids.index(new_id), (
            "non-task kinds must keep the pre-existing trust/decay-dominant ordering"
        )

    def test_mixed_kinds_default_recall_task_still_recency_ordered(self, tmp_path) -> None:
        """A no-query, no-kind-filter recall() mixing kinds still orders the
        task-kind subset newest-first among themselves."""
        store = _store(tmp_path)
        old_task = store.remember("/ws", "old task", kind="task", priority="high")
        new_task = store.remember("/ws", "new task", kind="task", priority="high")
        _bump_trust_and_decay(tmp_path, old_task, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_task, trust=0.3, decay=0.3)

        notes = store.recall("/ws", limit=100)
        task_ids = [n.note_id for n in notes if n.kind == "task"]
        assert task_ids.index(new_task) < task_ids.index(old_task)


# ---------------------------------------------------------------------------
# recall_for_path() shares the same kind-based override
# ---------------------------------------------------------------------------

class TestRecallForPathTaskRecencyOverride:
    def test_newer_task_note_outranks_older_higher_trust_task_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        old_id = store.remember("/ws", "old task about auth.py", kind="task", priority="high")
        new_id = store.remember("/ws", "new task about auth.py", kind="task", priority="high")
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.4, decay=0.4)

        notes = store.recall_for_path("/ws", "auth.py", kind="task")
        ids = [n.note_id for n in notes]
        assert ids.index(new_id) < ids.index(old_id)


# ---------------------------------------------------------------------------
# boot_recall() — SessionStart injection path
# ---------------------------------------------------------------------------

class TestBootRecallTaskRecency:
    def test_high_task_notes_ordered_newest_first(self, tmp_path) -> None:
        store = _store(tmp_path)
        old_id = store.remember("/ws", "stale pause note", kind="task", priority="high")
        new_id = store.remember("/ws", "current checkpoint", kind="task", priority="high")
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.3, decay=0.3)

        boot = store.boot_recall("/ws")
        task_ids = [n.note_id for n in boot if n.kind == "task"]
        assert task_ids == [new_id, old_id]

    def test_directives_still_ordered_oldest_first(self, tmp_path) -> None:
        """Directive ordering is unaffected by this fix."""
        store = _store(tmp_path)
        first_id = store.remember("/ws", "first directive", kind="directive")
        second_id = store.remember("/ws", "second directive", kind="directive")

        boot = store.boot_recall("/ws")
        directive_ids = [n.note_id for n in boot if n.kind == "directive"]
        assert directive_ids == [first_id, second_id]

    def test_directives_still_ordered_before_tasks(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember("/ws", "a task", kind="task", priority="high")
        store.remember("/ws", "a directive", kind="directive")
        boot = store.boot_recall("/ws")
        assert boot[0].kind == "directive"

    def test_task_notes_capped_at_configured_max(self, tmp_path) -> None:
        from agent.config import BOOT_MAX_TASK_NOTES
        store = _store(tmp_path)
        ids = []
        for i in range(BOOT_MAX_TASK_NOTES + 5):
            ids.append(store.remember("/ws", f"task {i}", kind="task", priority="high"))

        boot = store.boot_recall("/ws")
        task_ids = [n.note_id for n in boot if n.kind == "task"]
        assert len(task_ids) == BOOT_MAX_TASK_NOTES
        # Only the newest BOOT_MAX_TASK_NOTES survive the cap.
        assert task_ids == list(reversed(ids))[:BOOT_MAX_TASK_NOTES]


# ---------------------------------------------------------------------------
# Service-level integration — hook-injected recall renders newest task first
# ---------------------------------------------------------------------------

class TestServiceBootInjectionTaskRecency:
    def _make_service(self, tmp_path):
        from unittest.mock import patch
        from tests.conftest import _DummyEmbedProvider, _RealVectrService
        with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path), "VECTR_EMBED_MODEL": "dummy"}):
            svc = _RealVectrService(workspace_root=str(tmp_path))
        return svc

    def test_boot_injection_renders_newest_task_note_first(self, tmp_path) -> None:
        svc = self._make_service(tmp_path)
        old_id = svc.remember("stale pause note — do not resume from here", kind="task", priority="high")
        new_id = svc.remember("current checkpoint — resume from here", kind="task", priority="high")
        from agent.working_context_store import WorkingContextStore
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.2, decay=0.2)

        result = svc.recall(boot=True)
        assert result.index(f"[#{new_id}]") < result.index(f"[#{old_id}]")

    def test_bare_recall_renders_newest_task_note_first(self, tmp_path) -> None:
        """Also verified for a bare (non-boot) vectr_recall(kind='task') call —
        the caller had to expand the latest by explicit id every time before
        this fix."""
        svc = self._make_service(tmp_path)
        old_id = svc.remember("stale pause note", kind="task", priority="high")
        new_id = svc.remember("current checkpoint", kind="task", priority="high")
        _bump_trust_and_decay(tmp_path, old_id, trust=1.0, decay=1.0)
        _bump_trust_and_decay(tmp_path, new_id, trust=0.2, decay=0.2)

        result = svc.recall(kind="task", detail="index")
        assert result.index(f"[#{new_id}]") < result.index(f"[#{old_id}]")
