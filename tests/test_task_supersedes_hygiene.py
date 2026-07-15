"""
UPG-TASK-SUPERSEDES-HYGIENE — a memory-hygiene NUDGE, never a lifecycle
change: kind="task" notes never decay, auto-supersede, or auto-expire.

Coverage:
  - WorkingContextStore.stale_task_summary(): state-based count + oldest id
    (kind + age + tombstone status only, never content).
  - VectrService.stale_task_summary() / status(): wraps the store, exposed
    unconditionally, (0, None) in search-only mode.
  - vectr_status MCP output: warning line appended above the configured
    threshold, absent below it and when notes are superseded.
  - /v1/status REST: same fields surfaced through StatusResponse.
  - Config-driven: mutating the threshold changes behaviour without any
    code change.
"""
from __future__ import annotations

import time

import pytest


def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


def _backdate(store, note_id, age_days):
    cutoff = time.time() - age_days * 86400
    with store._conn() as conn:
        conn.execute(
            "UPDATE notes SET created_at = ? WHERE note_id = ?", (cutoff, note_id)
        )


# ---------------------------------------------------------------------------
# Store-level: WorkingContextStore.stale_task_summary()
# ---------------------------------------------------------------------------

class TestStaleTaskSummaryStore:
    def test_no_task_notes_returns_zero_and_none(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (0, None)

    def test_recent_task_note_not_counted(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        store.remember(ws, "current work", kind="task")
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (0, None)

    def test_old_task_note_counted(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, "stale checkpoint", kind="task")
        _backdate(store, note_id, age_days=10)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (1, note_id)

    def test_oldest_id_is_earliest_created_at(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        older_id = store.remember(ws, "oldest checkpoint", kind="task")
        _backdate(store, older_id, age_days=20)
        newer_id = store.remember(ws, "newer stale checkpoint", kind="task")
        _backdate(store, newer_id, age_days=10)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert count == 2
        assert oldest_id == older_id

    def test_superseded_old_task_note_not_counted(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        old_id = store.remember(ws, "old checkpoint", kind="task")
        _backdate(store, old_id, age_days=10)
        # New task note explicitly supersedes the old one — old is tombstoned
        # (valid_until set) and must stop counting toward staleness.
        store.remember(ws, "current checkpoint", kind="task", supersedes=old_id)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (0, None)

    def test_forgotten_old_task_note_not_counted(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        old_id = store.remember(ws, "old checkpoint", kind="task")
        _backdate(store, old_id, age_days=10)
        store.forget(ws, old_id)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (0, None)

    def test_non_task_kind_notes_not_counted(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        gotcha_id = store.remember(ws, "old gotcha", kind="gotcha")
        finding_id = store.remember(ws, "old finding", kind="finding")
        _backdate(store, gotcha_id, age_days=30)
        _backdate(store, finding_id, age_days=30)
        count, oldest_id = store.stale_task_summary(ws, min_age_days=7)
        assert (count, oldest_id) == (0, None)

    def test_workspace_isolation(self, tmp_path) -> None:
        store = _store(tmp_path)
        other_ws = str(tmp_path) + "-other"
        note_id = store.remember(other_ws, "stale in other workspace", kind="task")
        _backdate(store, note_id, age_days=30)
        count, oldest_id = store.stale_task_summary(str(tmp_path), min_age_days=7)
        assert (count, oldest_id) == (0, None)


# ---------------------------------------------------------------------------
# Service-level: VectrService.stale_task_summary() / status()
# ---------------------------------------------------------------------------

class TestServiceStaleTaskSummary:
    def _make_service(self, tmp_path, monkeypatch, **kwargs):
        from unittest.mock import patch
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path), **kwargs)
        return svc

    def test_zero_when_no_stale_tasks(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        assert svc.stale_task_summary() == (0, None)

    def test_reflects_backdated_task_notes(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        note_id = svc._context_store.remember(
            svc._workspace_root, "stale checkpoint", kind="task"
        )
        _backdate(svc._context_store, note_id, age_days=10)
        assert svc.stale_task_summary() == (1, note_id)

    def test_status_dict_includes_stale_task_fields(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch)
        status = svc.status()
        assert "stale_task_count" in status
        assert "stale_task_oldest_id" in status
        assert status["stale_task_count"] == 0
        assert status["stale_task_oldest_id"] is None

    def test_search_only_mode_returns_zero_and_none(self, tmp_path, monkeypatch) -> None:
        svc = self._make_service(tmp_path, monkeypatch, search_only=True)
        assert svc.stale_task_summary() == (0, None)


# ---------------------------------------------------------------------------
# MCP-level: vectr_status warning line (mocked svc.status(), UPG-9x pattern)
# ---------------------------------------------------------------------------

class TestMCPStatusStaleTaskWarning:
    def _base_status(self, **overrides):
        status = {
            "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model", "workspace_root": "/repo",
            "symbol_count": 0, "notes_count": 0, "languages": [],
            "grammars_unavailable": [],
        }
        status.update(overrides)
        return status

    def _call(self, status_dict):
        from unittest.mock import MagicMock
        from integrations.mcp_server._dispatch import handle_tools_call

        svc = MagicMock()
        svc.status.return_value = status_dict
        svc.count_notes.return_value = status_dict.get("notes_count", 0)
        svc.suggest_instruction_style.return_value = "additive"
        svc._eviction_advisor = MagicMock()

        result = handle_tools_call("vectr_status", {}, svc, session_id="test")
        return result["content"][0]["text"]

    def test_warning_present_above_threshold(self) -> None:
        from agent.config import MEMORY_HYGIENE_STALE_TASK_WARN_COUNT
        text = self._call(self._base_status(
            stale_task_count=MEMORY_HYGIENE_STALE_TASK_WARN_COUNT,
            stale_task_oldest_id=42,
        ))
        assert "WARNING" in text
        assert "#42" in text
        assert "task note" in text.lower()

    def test_warning_absent_below_threshold(self) -> None:
        from agent.config import MEMORY_HYGIENE_STALE_TASK_WARN_COUNT
        text = self._call(self._base_status(
            stale_task_count=MEMORY_HYGIENE_STALE_TASK_WARN_COUNT - 1,
            stale_task_oldest_id=42,
        ))
        assert "task note(s) are older than" not in text

    def test_warning_absent_when_count_zero(self) -> None:
        text = self._call(self._base_status(stale_task_count=0, stale_task_oldest_id=None))
        assert "task note(s) are older than" not in text

    def test_warning_absent_when_key_missing_entirely(self) -> None:
        """Backward-compat: an older mock/status dict without the new keys
        must not error and must not warn (defaults to 0)."""
        text = self._call(self._base_status())
        assert "task note(s) are older than" not in text

    def test_warning_mentions_supersedes_and_forget(self) -> None:
        from agent.config import MEMORY_HYGIENE_STALE_TASK_WARN_COUNT
        text = self._call(self._base_status(
            stale_task_count=MEMORY_HYGIENE_STALE_TASK_WARN_COUNT,
            stale_task_oldest_id=7,
        ))
        assert "supersedes" in text
        assert "vectr_forget" in text


# ---------------------------------------------------------------------------
# Config-driven: mutating the threshold changes behaviour, not the code
# ---------------------------------------------------------------------------

class TestConfigDrivenThreshold:
    def _call(self, status_dict):
        from unittest.mock import MagicMock
        from integrations.mcp_server._dispatch import handle_tools_call

        svc = MagicMock()
        svc.status.return_value = status_dict
        svc.count_notes.return_value = status_dict.get("notes_count", 0)
        svc.suggest_instruction_style.return_value = "additive"
        svc._eviction_advisor = MagicMock()

        result = handle_tools_call("vectr_status", {}, svc, session_id="test")
        return result["content"][0]["text"]

    def test_lowering_threshold_makes_same_count_warn(self, monkeypatch) -> None:
        import integrations.mcp_server._dispatch as dispatch_mod

        status = {
            "indexed_files": 1, "total_chunks": 1,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model", "workspace_root": "/repo",
            "symbol_count": 0, "notes_count": 0, "languages": [],
            "grammars_unavailable": [], "stale_task_count": 1, "stale_task_oldest_id": 3,
        }
        # Default config (3) — 1 stale task must not warn.
        assert "task note(s) are older than" not in self._call(status)

        # Lower the threshold to 1 — the same count of 1 must now warn.
        monkeypatch.setattr(dispatch_mod, "MEMORY_HYGIENE_STALE_TASK_WARN_COUNT", 1)
        assert "task note(s) are older than" in self._call(status)

    def test_warning_age_text_reflects_config(self, monkeypatch) -> None:
        import integrations.mcp_server._dispatch as dispatch_mod

        monkeypatch.setattr(dispatch_mod, "MEMORY_HYGIENE_STALE_TASK_WARN_COUNT", 1)
        monkeypatch.setattr(dispatch_mod, "MEMORY_HYGIENE_STALE_TASK_WARN_AGE_DAYS", 30)
        status = {
            "indexed_files": 1, "total_chunks": 1,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model", "workspace_root": "/repo",
            "symbol_count": 0, "notes_count": 0, "languages": [],
            "grammars_unavailable": [], "stale_task_count": 1, "stale_task_oldest_id": 3,
        }
        text = self._call(status)
        assert "30 days" in text


# ---------------------------------------------------------------------------
# REST-level: /v1/status
# ---------------------------------------------------------------------------

class TestRestStatusStaleTask:
    @staticmethod
    def _reaffirm_real_service(svc) -> None:
        """`real_service_client` sets app.state.service = svc only once, the
        first time the session-scoped fixture is constructed. A LATER test
        anywhere in the session using the `client`/`client_real_memory` mock
        fixtures (conftest.py) reassigns the same shared `app.state.service`
        to its own MagicMock and never restores it — so a REST test relying
        on `real_service_client` after one of those has run would silently
        exercise the wrong (mocked) service. Reaffirm the real instance
        defensively before issuing requests, regardless of test order."""
        from api import app
        app.state.service = svc

    def test_status_route_surfaces_stale_task_fields(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._reaffirm_real_service(svc)
        client.post("/v1/memory/clear", json={})

        resp = client.get("/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stale_task_count"] == 0
        assert data["stale_task_oldest_id"] is None

    def test_status_route_reflects_backdated_task_note(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        self._reaffirm_real_service(svc)
        client.post("/v1/memory/clear", json={})

        note_id = svc._context_store.remember(ws, "stale checkpoint", kind="task")
        _backdate(svc._context_store, note_id, age_days=10)

        resp = client.get("/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stale_task_count"] == 1
        assert data["stale_task_oldest_id"] == note_id

        # Clean up so later session-scoped tests aren't affected.
        client.post("/v1/memory/clear", json={})
