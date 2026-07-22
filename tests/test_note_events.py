"""
Tests for the note lifecycle event log (UPG-MEMORY-STATE-MACHINE §4).

Two layers:
  - Pure `agent.working_context_store._events.fold()` — offline, no store,
    no I/O — covers tie-breaking, revert-of-revert, and the
    audit-only (stale_flagged/promoted) events that never change state.
  - `WorkingContextStore` integration — `contradicts=` on remember(),
    `revoke_note`/`reinstate_note`, the migration writes for `supersedes`/
    `promote`, anti-memory rendering, proxy-anchor drift -> `stale_flagged`,
    and `forget()`'s cascade delete.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pure fold() — offline
# ---------------------------------------------------------------------------

class TestFold:
    def test_empty_log_folds_active(self) -> None:
        from agent.working_context_store._events import fold
        result = fold([])
        assert result == {"state": "active", "reason": None, "actor": None, "ts": None}

    def test_created_alone_is_active(self) -> None:
        from agent.working_context_store._events import fold
        result = fold([{"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0}])
        assert result["state"] == "active"

    def test_revoked_after_created_is_revoked(self) -> None:
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "revoked", "actor": "agent", "reason": "wrong", "ts": 200.0},
        ]
        result = fold(events)
        assert result["state"] == "revoked"
        assert result["reason"] == "wrong"
        assert result["actor"] == "agent"
        assert result["ts"] == 200.0

    def test_reinstated_after_revoked_is_active_again(self) -> None:
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "revoked", "actor": "agent", "reason": "wrong", "ts": 200.0},
            {"id": 3, "event": "reinstated", "actor": "human", "reason": None, "ts": 300.0},
        ]
        result = fold(events)
        assert result["state"] == "active"
        assert result["actor"] == "human"

    def test_revoke_reinstate_revoke_again_lands_on_last_event(self) -> None:
        """Revert-of-revert is always legal and repeatable — the fold has no
        memory beyond the log itself, so a second revoke after a reinstate
        is indistinguishable from a first."""
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "revoked", "actor": "agent", "reason": "first reason", "ts": 200.0},
            {"id": 3, "event": "reinstated", "actor": "agent", "reason": None, "ts": 300.0},
            {"id": 4, "event": "revoked", "actor": "human", "reason": "second reason", "ts": 400.0},
        ]
        result = fold(events)
        assert result["state"] == "revoked"
        assert result["reason"] == "second reason"
        assert result["actor"] == "human"

    def test_stale_flagged_does_not_change_lifecycle_state(self) -> None:
        """stale_flagged is audit-only (§4.4 anchor drift) — orthogonal to
        the revoked/superseded/active lifecycle."""
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "stale_flagged", "actor": "system", "reason": None, "ts": 200.0},
        ]
        result = fold(events)
        assert result["state"] == "active"
        # the state-changing "created" event's actor/ts, not stale_flagged's
        assert result["actor"] == "agent"

    def test_promoted_does_not_change_lifecycle_state(self) -> None:
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "promoted", "actor": "human", "reason": None, "ts": 200.0, "payload": "human"},
        ]
        result = fold(events)
        assert result["state"] == "active"
        assert result["actor"] == "agent"

    def test_superseded_is_a_terminal_lifecycle_state(self) -> None:
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "superseded", "actor": "agent", "reason": None, "ts": 200.0, "payload": "7"},
        ]
        result = fold(events)
        assert result["state"] == "superseded"

    def test_tie_break_is_by_caller_supplied_ordering_not_ts(self) -> None:
        """fold() trusts the caller's ordering (must be sorted by monotonic
        `id`, per its docstring) rather than re-sorting by `ts` itself — two
        events can share an identical wall-clock `ts` (same call, same low-
        resolution tick) and the LAST one in iteration order still wins."""
        from agent.working_context_store._events import fold
        events = [
            {"id": 1, "event": "created", "actor": "agent", "reason": None, "ts": 100.0},
            {"id": 2, "event": "revoked", "actor": "agent", "reason": "r1", "ts": 100.0},
            {"id": 3, "event": "reinstated", "actor": "agent", "reason": None, "ts": 100.0},
        ]
        result = fold(events)
        assert result["state"] == "active"  # the LAST (id=3) event wins despite identical ts


# ---------------------------------------------------------------------------
# WorkingContextStore integration
# ---------------------------------------------------------------------------

def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


class TestContradicts:
    def test_contradicts_revokes_the_target_note(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        old_id = store.remember(ws, "the API returns a list")
        new_id = store.remember(ws, "the API actually returns a dict", contradicts=old_id)
        states = store.note_event_states(ws, [store.get_note(ws, old_id), store.get_note(ws, new_id)])
        assert states[old_id]["state"] == "revoked"
        assert states[old_id]["actor"] == "agent"
        assert f"#{new_id}" in states[old_id]["reason"]
        # the new note itself is untouched (active)
        assert states.get(new_id, {"state": "active"})["state"] == "active"

    def test_contradicts_target_stays_live_for_recall(self, tmp_path) -> None:
        """Unlike `supersedes`, a revoked note is never excluded from
        recall — it must stay a live candidate so the anti-memory deterrent
        can surface (§4.3)."""
        store, ws = _store(tmp_path), str(tmp_path)
        old_id = store.remember(ws, "old belief")
        store.remember(ws, "correction", contradicts=old_id)
        active = store.recall(ws)
        assert any(n.note_id == old_id for n in active)
        old = store.get_note(ws, old_id)
        assert old.valid_until is None  # never tombstoned, unlike supersedes

    def test_contradicts_nonexistent_note_rejected(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        with pytest.raises(ValueError):
            store.remember(ws, "a note", contradicts=999999)

    def test_contradicts_has_no_human_provenance_write_boundary_guard(self, tmp_path) -> None:
        """Deliberately distinct from `supersedes`: revoking a human note
        does not silently mute it (it stays visible in deterrent form), so
        an agent-provenance write MAY contradict a human-provenance note."""
        store, ws = _store(tmp_path), str(tmp_path)
        human_id = store.remember(ws, "a human finding", provenance="human")
        new_id = store.remember(ws, "an agent correction", contradicts=human_id, provenance="agent")
        states = store.note_event_states(ws, [store.get_note(ws, human_id)])
        assert states[human_id]["state"] == "revoked"
        assert new_id  # write succeeded


class TestRevokeReinstate:
    def test_revoke_note_appends_revoked_event(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        ok = store.revoke_note(ws, note_id, "turned out false", actor="human")
        assert ok is True
        states = store.note_event_states(ws, [store.get_note(ws, note_id)])
        assert states[note_id]["state"] == "revoked"
        assert states[note_id]["actor"] == "human"
        assert states[note_id]["reason"] == "turned out false"

    def test_revoke_note_does_not_hard_delete(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        store.revoke_note(ws, note_id, "wrong")
        assert store.get_note(ws, note_id) is not None
        active = store.recall(ws)
        assert any(n.note_id == note_id for n in active)

    def test_revoke_nonexistent_note_returns_false(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        assert store.revoke_note(ws, 999999, "reason") is False

    def test_revoke_rejects_system_actor(self, tmp_path) -> None:
        """`system` is reserved for the deterministic anchor-drift
        transition — a human/agent-initiated revoke call is always a
        judgment call, never that actor."""
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        with pytest.raises(ValueError):
            store.revoke_note(ws, note_id, "reason", actor="system")

    def test_revoke_rejects_unrecognised_actor(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        with pytest.raises(ValueError):
            store.revoke_note(ws, note_id, "reason", actor="not-a-real-actor")

    def test_reinstate_reverses_revoke(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        store.revoke_note(ws, note_id, "wrong")
        ok = store.reinstate_note(ws, note_id, actor="human", reason="verified correct after all")
        assert ok is True
        states = store.note_event_states(ws, [store.get_note(ws, note_id)])
        assert states[note_id]["state"] == "active"

    def test_reinstate_nonexistent_note_returns_false(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        assert store.reinstate_note(ws, 999999) is False

    def test_reinstate_rejects_system_actor(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        with pytest.raises(ValueError):
            store.reinstate_note(ws, note_id, actor="system")

    def test_reinstate_on_never_revoked_note_is_harmless(self, tmp_path) -> None:
        """Always legal, even as a no-op-in-spirit call — same 'just
        append, let the fold decide' philosophy as a repeat revoke."""
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        ok = store.reinstate_note(ws, note_id)
        assert ok is True
        states = store.note_event_states(ws, [store.get_note(ws, note_id)])
        assert states[note_id]["state"] == "active"


class TestSupersedesPromoteMigration:
    def test_supersedes_writes_a_superseded_event(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        old_id = store.remember(ws, "old finding")
        new_id = store.remember(ws, "corrected finding", supersedes=old_id)
        real_old = store.get_note(ws, old_id)
        states = store.note_event_states(ws, [real_old])
        assert states[old_id]["state"] == "superseded"
        assert states[old_id]["reason"] is None  # payload carries the new id, not reason
        # existing valid_until/superseded_by_note_id behaviour is unchanged
        assert real_old.valid_until is not None
        assert real_old.superseded_by_note_id == new_id

    def test_promote_writes_a_promoted_event_without_changing_lifecycle_state(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding", provenance="auto")
        store.promote(ws, note_id, "agent")
        note = store.get_note(ws, note_id)
        states = store.note_event_states(ws, [note])
        # promoted is audit-only — state stays "active", not some new value
        assert states.get(note_id, {"state": "active"})["state"] == "active"
        assert note.provenance == "agent"  # pre-existing promotion behaviour unchanged


class TestAntiMemoryRendering:
    def test_revoked_note_renders_deterrent_not_raw_content(self, tmp_path) -> None:
        """The anti-memory block substitutes a ONE-LINE summary (§4.3) for
        the note's raw content — a multi-line body's later lines must never
        render again once revoked, only the deterrent framing + a short
        summary + the revocation reason."""
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(
            ws,
            "the config lives in settings.py\n"
            "detail line that must never re-render once revoked\n"
            "another body line that must never re-render once revoked",
        )
        store.revoke_note(ws, note_id, "actually it moved to config.py")
        note = store.get_note(ws, note_id)
        rendered = store.format_notes_for_llm([note], detail="full")
        assert "detail line that must never re-render once revoked" not in rendered
        assert "another body line that must never re-render once revoked" not in rendered
        assert "Previously believed" in rendered
        assert "actually it moved to config.py" in rendered
        assert "[REVOKED]" in rendered

    def test_reinstated_note_renders_raw_content_again(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding that gets reinstated")
        store.revoke_note(ws, note_id, "temporarily doubted")
        store.reinstate_note(ws, note_id)
        note = store.get_note(ws, note_id)
        rendered = store.format_notes_for_llm([note], detail="full")
        assert "a finding that gets reinstated" in rendered
        assert "Previously believed" not in rendered

    def test_active_note_unaffected_by_anti_memory_rendering(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "an ordinary active finding")
        note = store.get_note(ws, note_id)
        rendered = store.format_notes_for_llm([note], detail="full")
        assert "an ordinary active finding" in rendered
        assert "[REVOKED]" not in rendered

    def test_revoked_note_index_line_carries_revoked_marker(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        store.revoke_note(ws, note_id, "wrong")
        note = store.get_note(ws, note_id)
        rendered = store.format_notes_for_llm([note], detail="index")
        assert "[REVOKED]" in rendered

    def test_revoked_note_still_appears_in_fire_and_format(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a directive-like finding", kind="directive", provenance="agent")
        store.revoke_note(ws, note_id, "no longer true")
        formatted, fired_ids = store.fire_and_format(ws, event="session-start")
        assert note_id in fired_ids
        assert "Previously believed" in formatted


class TestProxyAnchorDrift:
    def test_anchor_drift_appends_stale_flagged_event(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        f = tmp_path / "requirements.lock"
        f.write_text("original lock content")
        note_id = store.remember(
            ws, "the lockfile pins numpy 1.26", kind="reference", anchors=["requirements.lock"],
        )
        f.write_text("changed lock content")
        notes = store.recall(ws)
        store.check_staleness(notes, ws)
        note = store.get_note(ws, note_id)
        states = store.note_event_states(ws, [note])
        assert states[note_id]["state"] == "active"  # audit-only, not a lifecycle change

        with store._conn() as conn:
            rows = conn.execute(
                "SELECT event, actor FROM note_events WHERE workspace = ? AND note_id = ? "
                "AND event = 'stale_flagged'",
                (ws, note_id),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["actor"] == "system"

    def test_anchor_drift_verdict_line_in_rendering(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        f = tmp_path / "Dockerfile"
        f.write_text("FROM python:3.11")
        note_id = store.remember(
            ws, "build process pinned in the Dockerfile", kind="reference", anchors=["Dockerfile"],
        )
        f.write_text("FROM python:3.12")
        notes = store.recall(ws)
        stale = store.check_staleness(notes, ws)
        rendered = store.format_notes_for_llm(notes, stale_warnings=stale, detail="full")
        assert "VERDICT: anchor changed since" in rendered

    def test_repeated_check_staleness_does_not_duplicate_stale_flagged_events(self, tmp_path) -> None:
        """Edge-triggered, not level-triggered: the event fires once on the
        transition into drift, not on every subsequent check while the
        drift persists."""
        store, ws = _store(tmp_path), str(tmp_path)
        f = tmp_path / "poetry.lock"
        f.write_text("v1")
        note_id = store.remember(ws, "lockfile pin", kind="reference", anchors=["poetry.lock"])
        f.write_text("v2")
        notes = store.recall(ws)
        store.check_staleness(notes, ws)
        store.check_staleness(notes, ws)
        store.check_staleness(notes, ws)
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM note_events WHERE workspace = ? AND note_id = ? "
                "AND event = 'stale_flagged'",
                (ws, note_id),
            ).fetchall()
        assert rows[0]["n"] == 1

    def test_unchanged_anchor_never_flagged_stale(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        f = tmp_path / "Pipfile.lock"
        f.write_text("stable")
        note_id = store.remember(ws, "pinned deps", kind="reference", anchors=["Pipfile.lock"])
        notes = store.recall(ws)
        store.check_staleness(notes, ws)
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM note_events WHERE workspace = ? AND note_id = ? "
                "AND event = 'stale_flagged'",
                (ws, note_id),
            ).fetchall()
        assert rows[0]["n"] == 0


class TestForgetCascade:
    def test_forget_deletes_note_events(self, tmp_path) -> None:
        store, ws = _store(tmp_path), str(tmp_path)
        note_id = store.remember(ws, "a finding")
        store.revoke_note(ws, note_id, "wrong")
        store.forget(ws, note_id)
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM note_events WHERE workspace = ? AND note_id = ?",
                (ws, note_id),
            ).fetchall()
        assert rows[0]["n"] == 0
