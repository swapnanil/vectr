"""Tests for the L1 episode capture write/read surface
(memoization-l1-capture-design §2): `VectrService.record_episode`/
`list_episodes`, the `POST /v1/episode` + `GET /v1/episodes` REST routes,
`vectr_status`'s `episodes_count`/`arcs_pending_distill` aggregates, and the
quarantine invariant that episode rows never surface through
`recall()`/`search()` or any hook-injected context.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _DummyEmbedProvider


def _make_real_service(tmp_path, monkeypatch):
    """Mirrors TestRecordCommitNoteIntegration._make_service in
    tests/test_commit_hook.py — a real VectrService, dummy embedder, own
    workspace root, so record_episode/list_episodes/status/recall all run
    through production code, not a mock."""
    from agent import indexer as idx_module

    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        from app.service import VectrService
        svc = VectrService(workspace_root=str(tmp_path))
    return svc


# ---------------------------------------------------------------------------
# VectrService.record_episode / list_episodes — real store integration
# ---------------------------------------------------------------------------

class TestRecordEpisodeIntegration:
    def test_bash_episode_normalizes_command_and_derives_success_outcome(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        episode_id = svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command='git commit -m "fix bug"', description="commit",
            file_path=None, rc=0, is_error=False, interrupted=False,
            stdout_tail="ok\n", stderr_tail="",
        )
        assert isinstance(episode_id, int)
        rows = svc.list_episodes()
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == episode_id
        assert row["verb"] == "git commit"
        assert row["flags"] == ["-m=fix bug"]
        assert row["outcome"] == "success"
        assert row["tool"] == "bash"

    def test_marker_beats_zero_rc_and_is_recorded_as_soft_failure(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="./mvnw test", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False,
            stdout_tail="[INFO] BUILD FAILURE\n", stderr_tail="",
        )
        row = svc.list_episodes()[0]
        assert row["outcome"] == "soft_failure"
        assert "maven.build_failure" in row["markers_matched"]

    def test_edit_episode_records_file_path_only_no_command_fields(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        target = str(tmp_path / "src" / "auth.py")
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="edit",
            command=None, description=None, file_path=target,
            rc=None, is_error=False, interrupted=False,
            stdout_tail="", stderr_tail="",
        )
        row = svc.list_episodes()[0]
        assert row["tool"] == "edit"
        assert row["file_path"] == target
        assert row["cmd_raw"] == ""
        assert row["verb"] == ""

    def test_description_field_is_never_persisted(self, tmp_path, monkeypatch):
        """R5/forward-compat: `description` is accepted on the wire but has
        no column in the episode schema (memoization-l1-capture-design §2
        point 5) — no logic anywhere branches on its content."""
        svc = _make_real_service(tmp_path, monkeypatch)
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo hi", description="a very distinctive description marker",
            file_path=None, rc=0, is_error=False, interrupted=False,
            stdout_tail="hi\n", stderr_tail="",
        )
        row = svc.list_episodes()[0]
        assert "description" not in row
        assert "a very distinctive description marker" not in str(row)

    def test_count_episodes_increments(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        assert svc.count_episodes() == 0
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo a", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo b", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        assert svc.count_episodes() == 2

    def test_list_episodes_filters_by_session_id(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo a", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        svc.record_episode(
            session_id="s2", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo b", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        rows = svc.list_episodes(session_id="s1")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "s1"


# ---------------------------------------------------------------------------
# vectr_status aggregates — episodes_count / arcs_pending_distill
# ---------------------------------------------------------------------------

class TestStatusEpisodeCounts:
    def test_episodes_count_reflects_recorded_episodes(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        assert svc.status()["episodes_count"] == 0
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command="echo a", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        assert svc.status()["episodes_count"] == 1

    def test_arcs_pending_distill_defaults_to_zero_when_no_arcs_table(self, tmp_path, monkeypatch):
        """LANE-ARC (§3) owns creating/populating the `arcs` table — this
        lane only reads a best-effort count that must never error before
        that table exists."""
        svc = _make_real_service(tmp_path, monkeypatch)
        assert svc.status()["arcs_pending_distill"] == 0


# ---------------------------------------------------------------------------
# Quarantine invariant — episodes never surface via recall()
# ---------------------------------------------------------------------------

class TestEpisodeQuarantine:
    def test_recorded_episode_never_appears_in_recall_output(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        distinctive = "ZzQUARANTINE9f3e1c2aMARKERzZ"
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command=f"echo {distinctive}", description=None, file_path=None,
            rc=0, is_error=False, interrupted=False,
            stdout_tail=f"{distinctive}\n", stderr_tail="",
        )
        notes_text = svc.recall(query=distinctive)
        assert distinctive not in notes_text

    def test_recorded_episode_never_appears_in_boot_recall(self, tmp_path, monkeypatch):
        svc = _make_real_service(tmp_path, monkeypatch)
        distinctive = "ZzQUARANTINEBOOTb7a2MARKERzZ"
        svc.record_episode(
            session_id="s1", ts=None, cwd=str(tmp_path), tool="bash",
            command=f"echo {distinctive}", description=None, file_path=None,
            rc=1, is_error=True, interrupted=False,
            stdout_tail="", stderr_tail=f"{distinctive}\n",
        )
        notes_text = svc.recall(boot=True)
        assert distinctive not in notes_text


# ---------------------------------------------------------------------------
# REST — POST /v1/episode, GET /v1/episodes
# ---------------------------------------------------------------------------

@pytest.fixture
def real_episode_client(tmp_path, monkeypatch):
    """Function-scoped TestClient over a REAL VectrService, dedicated to this
    file so episode-route tests never share state with the session-scoped
    `real_service_client` fixture other test modules rely on."""
    svc = _make_real_service(tmp_path, monkeypatch)
    from api import app
    prior = getattr(app.state, "service", None)
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            try:
                yield c, svc
            finally:
                app.state.service = prior


class TestEpisodeRoute:
    def test_happy_path_returns_episode_id_and_processing_ms(self, real_episode_client):
        client, _svc = real_episode_client
        resp = client.post(
            "/v1/episode",
            json={
                "session_id": "s1", "cwd": "/repo", "tool": "bash",
                "command": "npm test", "rc": 0, "stdout_tail": "5 passed\n",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["episode_id"], int)
        assert "processing_ms" in data

    def test_missing_tool_returns_422(self, real_episode_client):
        client, _svc = real_episode_client
        resp = client.post("/v1/episode", json={"cwd": "/repo"})
        assert resp.status_code == 422

    def test_edit_tool_with_file_path_only(self, real_episode_client):
        client, _svc = real_episode_client
        resp = client.post(
            "/v1/episode",
            json={"cwd": "/repo", "tool": "edit", "file_path": "/repo/a.py"},
        )
        assert resp.status_code == 200

    def test_search_only_mode_returns_503(self, real_episode_client):
        client, svc = real_episode_client
        # `search_only` is a read-only property on the real VectrService
        # (unlike the MagicMock used elsewhere) — flip the backing field
        # directly, mirroring how __init__ derives it.
        svc._search_only = True
        try:
            resp = client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": "echo hi"})
            assert resp.status_code == 503
            assert resp.json()["detail"]["error"] == "search_only_mode"
        finally:
            svc._search_only = False


class TestEpisodesRoute:
    def test_lists_recorded_episodes(self, real_episode_client):
        client, _svc = real_episode_client
        client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": "echo a"})
        client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": "echo b"})
        resp = client.get("/v1/episodes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["episodes"]) == 2
        assert "processing_ms" in data

    def test_filters_by_session_id(self, real_episode_client):
        client, _svc = real_episode_client
        client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": "echo a", "session_id": "s1"})
        client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": "echo b", "session_id": "s2"})
        resp = client.get("/v1/episodes", params={"session_id": "s1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["episodes"]) == 1
        assert data["episodes"][0]["session_id"] == "s1"

    def test_filters_by_limit(self, real_episode_client):
        client, _svc = real_episode_client
        for i in range(5):
            client.post("/v1/episode", json={"cwd": "/repo", "tool": "bash", "command": f"echo {i}"})
        resp = client.get("/v1/episodes", params={"limit": 2})
        assert resp.status_code == 200
        assert len(resp.json()["episodes"]) == 2

    def test_filters_by_since_and_until_ts(self, real_episode_client):
        client, svc = real_episode_client
        # Direct service call so exact ts values are controllable (the route
        # itself has no ts field it can be handed and stamps server time).
        import time
        now = time.time()
        svc.record_episode(
            session_id="s1", ts=now - 1000, cwd="/repo", tool="bash",
            command="old", description=None, file_path=None, rc=0,
            is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        svc.record_episode(
            session_id="s1", ts=now, cwd="/repo", tool="bash",
            command="new", description=None, file_path=None, rc=0,
            is_error=False, interrupted=False, stdout_tail="", stderr_tail="",
        )
        resp = client.get("/v1/episodes", params={"since_ts": now - 10})
        assert resp.status_code == 200
        cmds = [e["cmd_raw"] for e in resp.json()["episodes"]]
        assert cmds == ["new"]

    def test_search_only_mode_returns_503(self, real_episode_client):
        client, svc = real_episode_client
        svc._search_only = True
        try:
            resp = client.get("/v1/episodes")
            assert resp.status_code == 503
            assert resp.json()["detail"]["error"] == "search_only_mode"
        finally:
            svc._search_only = False
