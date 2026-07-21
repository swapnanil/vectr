"""Tests for agent/episode_store.py (memoization-l1-capture-design §2).

Structural quarantine + retention (ring buffer + TTL) for the `episodes`
table. This module has its own SQLite connection to the same db file
`WorkingContextStore` writes, but neither `agent/searcher.py` nor
`agent/working_context_store` ever imports it — see
TestNeverImportedBySearchOrRecallCode."""
from __future__ import annotations

import time

import pytest

from agent.episode_store import EpisodeStore


def _insert(store: EpisodeStore, workspace: str, *, ts: float, cmd: str = "echo hi",
            max_rows: int = 5000, ttl_days: float = 30) -> int:
    return store.insert(
        workspace,
        session_id="s1",
        ts=ts,
        cwd="/repo",
        tool="bash",
        cmd_raw=cmd,
        verb=cmd.split()[0],
        flags=[],
        args=[],
        rc=0,
        termination="normal",
        outcome="success",
        stdout_digest="",
        stderr_digest="",
        markers_matched=[],
        env_delta_names=[],
        file_path=None,
        max_rows=max_rows,
        ttl_days=ttl_days,
    )


class TestInsertAndRead:
    def test_insert_then_list(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        eid = _insert(store, "ws1", ts=time.time())
        rows = store.list_episodes("ws1")
        assert len(rows) == 1
        assert rows[0]["id"] == eid
        assert rows[0]["cmd_raw"] == "echo hi"

    def test_count_episodes(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        _insert(store, "ws1", ts=time.time())
        _insert(store, "ws1", ts=time.time())
        assert store.count_episodes("ws1") == 2

    def test_workspace_isolation(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        _insert(store, "ws1", ts=time.time())
        _insert(store, "ws2", ts=time.time())
        assert store.count_episodes("ws1") == 1
        assert store.count_episodes("ws2") == 1

    def test_json_fields_round_trip(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        store.insert(
            "ws1", session_id="s1", ts=time.time(), cwd="/repo", tool="bash",
            cmd_raw="git commit -m x", verb="git commit",
            flags=["-m=x"], args=[{"value": "1", "class": "NUM"}],
            rc=0, termination="normal", outcome="success",
            stdout_digest="", stderr_digest="",
            markers_matched=["maven.build_success"], env_delta_names=["FOO"],
            file_path=None, max_rows=5000, ttl_days=30,
        )
        row = store.list_episodes("ws1")[0]
        assert row["flags"] == ["-m=x"]
        assert row["args"] == [{"value": "1", "class": "NUM"}]
        assert row["markers_matched"] == ["maven.build_success"]
        assert row["env_delta_names"] == ["FOO"]


class TestListFilters:
    def test_filter_by_session_id(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        store.insert(
            "ws1", session_id="s1", ts=time.time(), cwd="/repo", tool="bash",
            cmd_raw="a", verb="a", flags=[], args=[], rc=0, termination="normal",
            outcome="success", stdout_digest="", stderr_digest="",
            markers_matched=[], env_delta_names=[], file_path=None,
            max_rows=5000, ttl_days=30,
        )
        store.insert(
            "ws1", session_id="s2", ts=time.time(), cwd="/repo", tool="bash",
            cmd_raw="b", verb="b", flags=[], args=[], rc=0, termination="normal",
            outcome="success", stdout_digest="", stderr_digest="",
            markers_matched=[], env_delta_names=[], file_path=None,
            max_rows=5000, ttl_days=30,
        )
        rows = store.list_episodes("ws1", session_id="s1")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "s1"

    def test_ordered_newest_first(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        now = time.time()
        _insert(store, "ws1", ts=now, cmd="first")
        _insert(store, "ws1", ts=now + 100.0, cmd="second")
        rows = store.list_episodes("ws1")
        assert [r["cmd_raw"] for r in rows] == ["second", "first"]

    def test_limit(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        now = time.time()
        for i in range(5):
            _insert(store, "ws1", ts=now + i, cmd=f"cmd{i}")
        rows = store.list_episodes("ws1", limit=2)
        assert len(rows) == 2


class TestRetention:
    def test_ring_buffer_keeps_newest_max_rows(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        now = time.time()
        for i in range(5):
            _insert(store, "ws1", ts=now + i, cmd=f"echo {i}", max_rows=3)
        assert store.count_episodes("ws1") == 3
        rows = store.list_episodes("ws1", limit=10)
        assert [r["cmd_raw"] for r in rows] == ["echo 4", "echo 3", "echo 2"]

    def test_ttl_prunes_old_rows(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        old_ts = time.time() - (40 * 86400)  # 40 days ago
        _insert(store, "ws1", ts=old_ts, cmd="old", ttl_days=30)
        _insert(store, "ws1", ts=time.time(), cmd="new", ttl_days=30)
        rows = store.list_episodes("ws1", limit=10)
        assert [r["cmd_raw"] for r in rows] == ["new"]

    def test_retention_is_per_workspace(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        now = time.time()
        for i in range(5):
            _insert(store, "ws1", ts=now + i, cmd=f"a{i}", max_rows=2)
        _insert(store, "ws2", ts=now, cmd="b0", max_rows=2)
        assert store.count_episodes("ws1") == 2
        assert store.count_episodes("ws2") == 1


class TestArcsPendingDistillGracefulDegradation:
    """Best-effort: 0 (never an error) until the arc detector's own `arcs`
    table exists — see EpisodeStore.count_arcs_pending_distill docstring for
    why this lane does not create or own that table."""

    def test_no_arcs_table_returns_zero(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        assert store.count_arcs_pending_distill("ws1") == 0

    def test_arcs_table_present_with_workspace_column_is_counted(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        with store._conn() as conn:
            conn.execute(
                "CREATE TABLE arcs (id INTEGER PRIMARY KEY, workspace TEXT)"
            )
            conn.execute("INSERT INTO arcs (workspace) VALUES ('ws1')")
        assert store.count_arcs_pending_distill("ws1") == 1
        assert store.count_arcs_pending_distill("ws2") == 0


class TestNeverImportedBySearchOrRecallCode:
    """Structural quarantine: nothing on the search/recall path can import
    episode_store — verified by static source inspection rather than a
    functional recall probe, since a functional test can only prove absence
    for the inputs it happens to try."""

    def test_searcher_module_does_not_import_episode_store(self):
        import agent.searcher as searcher_module
        source = open(searcher_module.__file__, encoding="utf-8").read()
        assert "episode_store" not in source

    def test_working_context_store_package_does_not_import_episode_store(self):
        import agent.working_context_store as wcs_package
        import pathlib
        pkg_dir = pathlib.Path(wcs_package.__file__).parent
        for py_file in pkg_dir.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            assert "episode_store" not in source, f"{py_file} imports episode_store"
