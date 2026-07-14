"""Tests for main.py CLI commands (multi-instance registry integration)."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from agent.instance_registry import InstanceRegistry, workspace_hash
import main as m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        # Multi-root args (start/restart/watch): workspace=positional, paths=--path list
        "workspace": None,
        "paths": None,
        # Legacy single-path used by stop/status/init/forget
        "path": "/project/a",
        "port": 8765,
        "all": False,
        "force": False,
        "query": None,
        "n": 10,
        "language": None,
        "reset_config": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _mode_gated_response(error: str, detail: str, status_code: int = 503):
    """Build a MagicMock httpx.Response whose .raise_for_status() raises an
    HTTPStatusError carrying the SAME structured body app/routes.py's mode
    gates return, e.g. {"detail": {"error": "memory_only_mode", "detail": "..."}}.
    """
    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"detail": {"error": error, "detail": detail}}
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        str(status_code), request=MagicMock(), response=mock_resp,
    )
    return mock_resp


def _registry_with(tmp_path, entries: dict) -> InstanceRegistry:
    reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
    for ws_hash_key, entry in entries.items():
        reg.register(ws_hash_key, entry["workspace"], entry["port"], entry["pid"])
    return reg


@pytest.fixture(autouse=True)
def _stub_version_skew_probe(monkeypatch):
    """Every daemon-talking subcommand now also probes /v1/status to compare
    version stamps (UPG-CLI-DAEMON-VERSION-SKEW, `_check_version_skew`).
    Default that probe to "daemon unreachable" here so the many existing
    tests in this file that mock only `httpx.post` (not `httpx.get`) never
    make a real network call against a possibly-live local vectr daemon on
    the same port. Tests that specifically exercise the probe (TestCmdStatus,
    TestCheckVersionSkew) patch `httpx.get` themselves within their own
    `with patch(...)` block, which overrides this default for their scope.
    """
    import httpx
    monkeypatch.setattr(
        httpx, "get",
        MagicMock(side_effect=httpx.ConnectError("stubbed — no real daemon calls in unit tests")),
    )


# ---------------------------------------------------------------------------
# cmd_start
# ---------------------------------------------------------------------------

class TestCmdStart:
    def test_noop_if_workspace_already_live(self, tmp_path, capsys):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._is_pid_alive", return_value=True), \
             patch("main._write_workspace_config"):
            m.cmd_start(_make_args(paths=[ws], port=8765))

        err = capsys.readouterr().err
        assert "already running" in err

    def test_starts_and_registers_new_instance(self, tmp_path):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            m.cmd_start(_make_args(paths=[ws], port=8765))

        mock_do_start.assert_called_once_with(
            ws, 8765, wh, extra_roots=[], memory_only=False, search_only=False, workspace_explicit=True,
            code_workspace_file=None, host="127.0.0.1", no_ide_config=False,
        )

    def test_prunes_dead_entries_before_starting(self, tmp_path):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 99999)  # dead entry

        prune_called = []

        original_prune = reg.prune_dead
        def recording_prune():
            prune_called.append(True)
            original_prune()

        reg.prune_dead = recording_prune

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start"):
            m.cmd_start(_make_args(paths=[ws], port=8765))

        assert prune_called, "prune_dead was not called"


# ---------------------------------------------------------------------------
# cmd_stop
# ---------------------------------------------------------------------------

class TestCmdStop:
    def test_stop_single_workspace(self, tmp_path):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop:
            m.cmd_stop(_make_args(path=ws, **{"all": False}))

        mock_stop.assert_called_once_with(12345)
        assert reg.get(wh) is None

    def test_stop_prints_message_when_no_instance(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False):
            m.cmd_stop(_make_args(path="/project/a", **{"all": False}))

        err = capsys.readouterr().err
        assert "No registered instance" in err

    def test_stop_all_stops_every_instance(self, tmp_path):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register("aaa000000000", "/project/a", 8765, 111)
        reg.register("bbb000000000", "/project/b", 8766, 222)

        stopped_pids = []

        def fake_stop(pid, **_):
            stopped_pids.append(pid)
            return True

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("main._stop_server", side_effect=fake_stop):
            m.cmd_stop(_make_args(**{"all": True}))

        assert sorted(stopped_pids) == [111, 222]
        assert reg.list_all() == {}

    def test_stop_all_noop_when_empty(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg):
            m.cmd_stop(_make_args(**{"all": True}))

        err = capsys.readouterr().err
        assert "No running" in err

    def test_stop_positional_workspace(self, tmp_path):
        # UPG-CLI-STOP-PATH-POSITIONAL: `vectr stop <workspace>` must work like
        # start/restart's positional, not just --path.
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop:
            m.cmd_stop(_make_args(workspace=ws, path="/should/be/ignored", **{"all": False}))

        mock_stop.assert_called_once_with(12345)
        assert reg.get(wh) is None

    def test_stop_path_flag_still_works_without_positional(self, tmp_path):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop:
            m.cmd_stop(_make_args(workspace=None, path=ws, **{"all": False}))

        mock_stop.assert_called_once_with(12345)
        assert reg.get(wh) is None

    def test_stop_positional_wins_over_path_flag(self, tmp_path):
        # Positional and --path both given (pointing at different workspaces):
        # positional must win, exactly like start/restart's `_resolve_workspace_roots`.
        ws_positional = "/project/positional"
        ws_flag = "/project/flag"
        wh = workspace_hash(ws_positional)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws_positional, 8765, 12345)
        reg.register(workspace_hash(ws_flag), ws_flag, 8766, 54321)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop:
            m.cmd_stop(_make_args(workspace=ws_positional, path=ws_flag, **{"all": False}))

        mock_stop.assert_called_once_with(12345)
        assert reg.get(wh) is None
        assert reg.get(workspace_hash(ws_flag)) is not None

    def test_stop_positional_code_workspace_file_uses_first_root(self, tmp_path):
        ws_file = tmp_path / "project.code-workspace"
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        ws_file.write_text(
            json.dumps({"folders": [{"path": str(root_a)}, {"path": str(root_b)}]}),
            encoding="utf-8",
        )
        wh = workspace_hash(str(root_a))
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, str(root_a), 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop:
            m.cmd_stop(_make_args(workspace=str(ws_file), **{"all": False}))

        mock_stop.assert_called_once_with(12345)
        assert reg.get(wh) is None


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_status_all_lists_instances(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register("aaa000000000", "/project/a", 8765, 111)
        reg.register("bbb000000000", "/project/b", 8766, 222)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"indexed_files": 100, "total_chunks": 500}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(**{"all": True}))

        out = capsys.readouterr().out
        assert "/project/a" in out
        assert "/project/b" in out
        assert "8765" in out
        assert "8766" in out

    def test_status_all_empty(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True):
            m.cmd_status(_make_args(**{"all": True}))

        out = capsys.readouterr().out
        assert "No running" in out

    def test_single_instance_shows_mode_line(self, tmp_path, capsys):
        """UPG-CLI-STATUS-MODE: the Mode line is new — /v1/status already
        returns `mode`, but cmd_status silently dropped it."""
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Mode          : full" in out
        assert "Indexed files : 10" in out

    def test_memory_only_mode_rewords_indexed_rows(self, tmp_path, capsys):
        """UPG-CLI-STATUS-MODE: in memory-only mode, files/chunks/last-indexed
        must not be rendered as live counts — 'Last indexed: never' and a
        0-files-with-nonzero-chunks row are misleading when indexing is
        intentionally off, not merely pending."""
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 0, "total_chunks": 5148,
            "last_indexed": "never", "embed_model": "granite", "mode": "memory-only",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Mode          : memory-only" in out
        assert "Last indexed  : never" not in out
        assert "Indexed files :" not in out
        assert "disabled in memory-only mode" in out
        assert "5148" in out

    def test_shows_code_workspace_file_when_started_from_one(self, tmp_path, capsys):
        """UPG-CLI-STATUS-MODE: the workspace line shows the originating
        .code-workspace file (recorded at start/restart time) instead of just
        the primary folder, when the instance was launched from one."""
        ws_file = str(tmp_path / "proj.code-workspace")
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111, code_workspace_file=ws_file)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 1, "total_chunks": 1,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        assert ws_file in capsys.readouterr().out

    def test_shows_extra_roots_when_no_code_workspace_file(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111, extra_roots=["/other/root"])

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 1, "total_chunks": 1,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        assert "/other/root" in capsys.readouterr().out

    def test_status_all_shows_mode_per_instance(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register("aaa000000000", "/project/a", 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"indexed_files": 100, "total_chunks": 500, "mode": "search-only"}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(**{"all": True}))

        out = capsys.readouterr().out
        assert "Mode      : search-only" in out

    def test_single_instance_connect_error_says_not_listening(self, tmp_path, capsys):
        """UPG-CLI-START-READY-RACE: nothing listening on the port at all is
        a distinct, more confident message than 'listening but not ready'."""
        import httpx
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", side_effect=httpx.ConnectError("refused")), \
             pytest.raises(SystemExit) as exc_info:
            m.cmd_status(_make_args(path=str(tmp_path)))

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "not listening" in err

    def test_single_instance_timeout_says_listening_but_not_ready(self, tmp_path, capsys):
        """UPG-CLI-START-READY-RACE: a connection that times out (as opposed
        to being refused) means a process IS listening — likely still inside
        FastAPI lifespan startup loading the embedder — which must not read
        as 'not running', and must not crash with an unhandled exception
        (previously only httpx.ConnectError was caught here)."""
        import httpx
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", side_effect=httpx.ReadTimeout("timed out")), \
             pytest.raises(SystemExit) as exc_info:
            m.cmd_status(_make_args(path=str(tmp_path)))

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "listening" in err
        assert "not responding yet" in err

    def test_single_instance_http_status_error_shows_daemon_detail(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=_mode_gated_response("some_error", "some detail", 500)), \
             pytest.raises(SystemExit) as exc_info:
            m.cmd_status(_make_args(path=str(tmp_path)))

        assert exc_info.value.code == 1
        assert "some detail" in capsys.readouterr().err

    def test_status_all_distinguishes_not_listening_from_not_ready(self, tmp_path, capsys):
        import httpx
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register("aaa000000000", "/project/a", 8765, 111)
        reg.register("bbb000000000", "/project/b", 8766, 222)

        def _get(url, **kwargs):
            if ":8765" in url:
                raise httpx.ConnectError("refused")
            raise httpx.ReadTimeout("timed out")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("httpx.get", side_effect=_get):
            m.cmd_status(_make_args(**{"all": True}))

        out = capsys.readouterr().out
        assert "not listening" in out
        assert "not responding yet" in out

    def test_watcher_backlog_line_shown_when_pending(self, tmp_path, capsys):
        # UPG-WATCHER-PRESSURE-GOVERNOR: `vectr status` surfaces watcher
        # backlog so runaway edit-stream churn is visible, not silent.
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
            "watcher_burst_mode": True, "watcher_pending_files": 14,
            "watcher_batch_running": False, "watcher_last_batch_duration_ms": 220,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Watcher" in out
        assert "14" in out
        assert "burst mode" in out

    def test_watcher_backlog_line_absent_when_quiet(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
            "watcher_burst_mode": False, "watcher_pending_files": 0,
            "watcher_batch_running": False, "watcher_last_batch_duration_ms": 0,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Watcher" not in out

    def test_hook_injection_line_shown_with_counts(self, tmp_path, capsys):
        # UPG-HOOK-INJECT-OBSERVABILITY: `vectr status` surfaces per-hook-kind
        # injection counts so a human can tell hooks are actually firing.
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
            "hook_injection_counts": {"SessionStart": 3, "PreToolUse": 2},
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Hook injections" in out
        assert "SessionStart 3" in out
        assert "PreToolUse 2" in out

    def test_hook_injection_line_absent_when_no_injections(self, tmp_path, capsys):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(workspace_hash(str(tmp_path)), str(tmp_path), 8765, 111)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "workspace_root": str(tmp_path), "indexed_files": 10, "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z", "embed_model": "granite", "mode": "full",
            "hook_injection_counts": {},
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("httpx.get", return_value=mock_resp):
            m.cmd_status(_make_args(path=str(tmp_path)))

        out = capsys.readouterr().out
        assert "Hook injections" not in out


# ---------------------------------------------------------------------------
# cmd_restart
# ---------------------------------------------------------------------------

class TestCmdRestart:
    def test_restart_stops_existing_and_starts_fresh(self, tmp_path):
        ws = "/project/a"
        wh = workspace_hash(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        reg.register(wh, ws, 8765, 12345)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("agent.instance_registry._is_pid_alive", return_value=True), \
             patch("main._stop_server") as mock_stop, \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            m.cmd_restart(_make_args(paths=[ws], port=8765))

        mock_stop.assert_called_once_with(12345)
        mock_do_start.assert_called_once()

    def test_restart_with_no_existing_entry_just_starts(self, tmp_path):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._stop_server") as mock_stop, \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            m.cmd_restart(_make_args(paths=["/project/a"], port=8765))

        mock_stop.assert_not_called()
        mock_do_start.assert_called_once()


# ---------------------------------------------------------------------------
# _write_workspace_config — port injection
# ---------------------------------------------------------------------------

class TestWriteWorkspaceConfig:
    def test_mcp_json_uses_correct_port(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8999)
        content = (tmp_path / ".mcp.json").read_text()
        assert "8999" in content
        assert "8765" not in content

    def test_mcp_json_updated_when_port_changes(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8999)
        content = (tmp_path / ".mcp.json").read_text()
        assert "8999" in content

    def test_cursor_mcp_json_created_with_correct_port(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8900)
        content = (tmp_path / ".cursor" / "mcp.json").read_text()
        assert "8900" in content
        # Cursor format has no "type" key
        assert '"type"' not in content

    def test_vscode_mcp_json_created_with_servers_key(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8901)
        content = (tmp_path / ".vscode" / "mcp.json").read_text()
        assert "8901" in content
        assert '"servers"' in content
        # VSCode format uses "servers", not "mcpServers"
        assert '"mcpServers"' not in content

    def test_cursor_mcp_json_updated_when_port_changes(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8999)
        content = (tmp_path / ".cursor" / "mcp.json").read_text()
        assert "8999" in content

    def test_vscode_mcp_json_updated_when_port_changes(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8999)
        content = (tmp_path / ".vscode" / "mcp.json").read_text()
        assert "8999" in content

    def test_claude_md_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert (tmp_path / "CLAUDE.md").exists()

    def test_claude_md_contains_conditional_recall_guidance(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "vectr_status" in content, "CLAUDE.md must reference vectr_status as the existence check before recall"
        assert "notes_count" in content, "CLAUDE.md must mention notes_count so agent knows when to skip recall"
        assert "vectr_recall" in content, "CLAUDE.md must mention vectr_recall"
        assert "prior work" in content or "continuing" in content, (
            "CLAUDE.md must frame recall as conditional on continuing prior work"
        )

    def test_claude_md_encourages_code_in_notes(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "actual code" in content or "code block" in content, (
            "CLAUDE.md must instruct agent to store actual code, not file pointers or prose"
        )
        assert "file pointer" in content or "re-read" in content or "re-reading" in content, (
            "CLAUDE.md must explain why notes beat re-reading files (file pointer or re-reading)"
        )

    def test_claude_md_has_recall_usage_guidance(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "fill gaps" in content or "directly" in content, (
            "CLAUDE.md must tell agent to work from recalled notes directly, use search only to fill gaps"
        )

    def test_claude_md_existing_content_preserved_block_appended(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("custom")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "custom" in content
        assert "<!-- vectr-start -->" in content
        assert "<!-- vectr-end -->" in content

    def test_claude_md_teaches_toolsearch_load_upg101(self, tmp_path):
        # UPG-10.1: vectr tools may be deferred behind ToolSearch; CLAUDE.md must
        # teach the load step + that they're called as tools, never as bash.
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "ToolSearch" in content
        assert "select:mcp__vectr__" in content
        assert ("shell" in content.lower() or "bash" in content.lower())

    def test_claude_md_memory_directive_beats_files_upg102(self, tmp_path):
        # UPG-10.2: firmer framing — vectr IS the working memory; do not write
        # ad-hoc memory files (must win over Claude Code's built-in auto-memory).
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "working memory IS vectr" in content
        assert "vectr_remember" in content
        # explicitly steers off the host's built-in file-memory directory
        # (editor-agnostic wording — UPG-INSTRUCTION-VET V3)
        assert "editor-managed memory directory" in content

    def test_settings_json_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.exists()
        import json
        data = json.loads(settings.read_text())
        assert data.get("enableAllProjectMcpServers") is True

    def test_seeds_default_vectrignore_when_missing(self, tmp_path):
        # UPG-13.2: a fresh workspace gets a .vectrignore pre-populated with
        # the standard non-indexable dirs on `vectr start`/`vectr init`.
        m._write_workspace_config(str(tmp_path), 8765)
        vectrignore = tmp_path / ".vectrignore"
        assert vectrignore.exists()
        content = vectrignore.read_text(encoding="utf-8")
        for expected_dir in ("node_modules", ".venv", "__pycache__", ".git", "dist"):
            assert expected_dir in content

    def test_never_overwrites_existing_vectrignore(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("my_custom_exclude\n", encoding="utf-8")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert content == "my_custom_exclude\n"
        assert "node_modules" not in content

    def test_rerunning_start_does_not_touch_seeded_vectrignore(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        first = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        m._write_workspace_config(str(tmp_path), 8999)  # e.g. a port change re-run
        second = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# --no-ide-config opt-out (UPG-CLI-WRITES-DISCLOSURE)
# ---------------------------------------------------------------------------

class TestNoIdeConfigOptOut:
    def test_ide_config_disabled_false_when_no_marker(self, tmp_path):
        assert m._ide_config_disabled(str(tmp_path)) is False

    def test_ide_config_disabled_true_after_persist(self, tmp_path):
        m._persist_ide_config_disabled(str(tmp_path))
        assert m._ide_config_disabled(str(tmp_path)) is True
        assert (tmp_path / ".vectr" / "ide_config").read_text(encoding="utf-8").strip() == "disabled"

    def test_maybe_write_calls_through_by_default(self, tmp_path):
        with patch("main._write_workspace_config") as mock_write:
            m._maybe_write_workspace_config(str(tmp_path), 8765, _make_args())
        mock_write.assert_called_once_with(str(tmp_path), 8765, search_only=False)

    def test_maybe_write_skips_and_persists_with_flag(self, tmp_path, capsys):
        with patch("main._write_workspace_config") as mock_write:
            m._maybe_write_workspace_config(str(tmp_path), 8765, _make_args(no_ide_config=True))
        mock_write.assert_not_called()
        assert (tmp_path / ".vectr" / "ide_config").exists()
        assert "Skipped IDE config" in capsys.readouterr().err

    def test_maybe_write_skips_when_already_persisted_without_flag(self, tmp_path, capsys):
        m._persist_ide_config_disabled(str(tmp_path))
        with patch("main._write_workspace_config") as mock_write:
            m._maybe_write_workspace_config(str(tmp_path), 8765, _make_args())
        mock_write.assert_not_called()
        assert "disabled" in capsys.readouterr().err.lower()

    def test_cmd_init_no_ide_config_flag_skips_writes_end_to_end(self, tmp_path):
        m.cmd_init(_make_args(path=str(tmp_path), no_ide_config=True))
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / ".mcp.json").exists()
        assert (tmp_path / ".vectr" / "ide_config").exists()

    def test_cmd_start_honors_persisted_optout_without_repeating_flag(self, tmp_path):
        ws = str(tmp_path)
        m._persist_ide_config_disabled(ws)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config") as mock_write, \
             patch("main._do_start"):
            m.cmd_start(_make_args(paths=[ws], port=8765))
        mock_write.assert_not_called()

    def test_start_help_discloses_ide_config_writes(self, capsys):
        with pytest.raises(SystemExit):
            with patch("sys.argv", ["vectr", "start", "--help"]):
                m.main()
        out = capsys.readouterr().out
        assert "--no-ide-config" in out
        assert "CLAUDE.md" in out


# ---------------------------------------------------------------------------
# vectr init: provisional-port disclosure (UPG-CLI-SMALL-UX)
# ---------------------------------------------------------------------------

class TestInitProvisionalPort:
    def test_no_registered_instance_prints_provisional_port_note(self, tmp_path, capsys) -> None:
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._maybe_write_workspace_config"), \
             patch("main._apply_exclude_args"):
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path)))
        err = capsys.readouterr().err
        assert "provisional" in err.lower()
        assert "8765" in err

    def test_registered_instance_prints_no_provisional_note(self, tmp_path, capsys) -> None:
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._maybe_write_workspace_config"), \
             patch("main._apply_exclude_args"):
            MockReg.return_value.get.return_value = {"port": 8999}
            m.cmd_init(_make_args(path=str(tmp_path)))
        err = capsys.readouterr().err
        assert "provisional" not in err.lower()


# ---------------------------------------------------------------------------
# _daemon_error_detail / _handle_daemon_call_error (UPG-CLI-MEMONLY-CRASH)
# ---------------------------------------------------------------------------

class TestDaemonErrorDetail:
    def test_extracts_nested_detail_string(self):
        mock_resp = _mode_gated_response("memory_only_mode", "vectr is in memory-only mode...")
        exc = mock_resp.raise_for_status.side_effect
        assert m._daemon_error_detail(exc) == "vectr is in memory-only mode..."

    def test_falls_back_to_status_code_on_unstructured_body(self):
        import httpx
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.side_effect = ValueError("not json")
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)
        assert "500" in m._daemon_error_detail(exc)


class TestHandleDaemonCallError:
    def test_connect_error_exits_one_with_not_running_message(self, capsys):
        import httpx
        with pytest.raises(SystemExit) as exc_info:
            m._handle_daemon_call_error(httpx.ConnectError("down"), 8765)
        assert exc_info.value.code == 1
        assert "not running" in capsys.readouterr().err.lower()

    def test_http_status_error_exits_one_with_server_detail(self, capsys):
        mock_resp = _mode_gated_response("memory_only_mode", "vectr is in memory-only mode...")
        exc = mock_resp.raise_for_status.side_effect
        with pytest.raises(SystemExit) as exc_info:
            m._handle_daemon_call_error(exc, 8765)
        assert exc_info.value.code == 1
        assert "memory-only mode" in capsys.readouterr().err

    def test_unrecognised_exception_reraises(self):
        with pytest.raises(ValueError):
            m._handle_daemon_call_error(ValueError("unrelated"), 8765)


# ---------------------------------------------------------------------------
# _check_version_skew (UPG-CLI-DAEMON-VERSION-SKEW)
# ---------------------------------------------------------------------------

class TestCheckVersionSkew:
    def test_mismatch_prints_exactly_one_stderr_line(self, capsys):
        with patch("main.compute_version_stamp", return_value="1.0.0+local1"):
            m._check_version_skew(8765, daemon_status={"version_stamp": "1.0.0+old0000"})
        err = capsys.readouterr().err
        lines = [l for l in err.splitlines() if l.strip()]
        assert len(lines) == 1
        assert "daemon on port 8765 is running older code" in lines[0]
        assert "1.0.0+old0000" in lines[0]
        assert "1.0.0+local1" in lines[0]
        assert "vectr restart" in lines[0]

    def test_matching_stamps_print_nothing(self, capsys):
        with patch("main.compute_version_stamp", return_value="1.0.0+abc1234"):
            m._check_version_skew(8765, daemon_status={"version_stamp": "1.0.0+abc1234"})
        assert capsys.readouterr().err == ""

    def test_daemon_stamp_missing_prints_nothing(self, capsys):
        with patch("main.compute_version_stamp", return_value="1.0.0+abc1234"):
            m._check_version_skew(8765, daemon_status={})
        assert capsys.readouterr().err == ""

    def test_local_stamp_unavailable_prints_nothing(self, capsys):
        with patch("main.compute_version_stamp", return_value=""):
            m._check_version_skew(8765, daemon_status={"version_stamp": "1.0.0+abc1234"})
        assert capsys.readouterr().err == ""

    def test_never_raises_when_daemon_is_unreachable(self, capsys):
        """No `daemon_status` given -> probes /v1/status itself; a down
        daemon must never surface as an exception or block the caller."""
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("down")):
            m._check_version_skew(8765)  # must not raise
        assert capsys.readouterr().err == ""

    def test_probes_status_itself_when_no_daemon_status_given(self, capsys):
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"version_stamp": "1.0.0+remote1"}
        with patch("httpx.get", return_value=mock_resp), \
             patch("main.compute_version_stamp", return_value="1.0.0+local22"):
            m._check_version_skew(8765)
        err = capsys.readouterr().err
        assert "1.0.0+remote1" in err
        assert "1.0.0+local22" in err


class TestVersionSkewWiring:
    """Confirms every daemon-talking subcommand calls the ONE shared
    `_check_version_skew` helper — the comparison/printing logic itself is
    never copy-pasted per subcommand (tested in isolation above)."""

    def test_cmd_index_calls_check_version_skew(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"indexed_files": 1, "total_chunks": 1, "processing_ms": 1, "model": "m"}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), force=False, port=8765)
            m.cmd_index(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_search_calls_check_version_skew(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [], "query_time_ms": 1, "chunks_searched": 0}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="x", n=10, language=None, port=8765)
            m.cmd_search(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_fetch_calls_check_version_skew(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [], "note": None}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["a.py:1-5"], port=8765)
            m.cmd_fetch(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_remember_calls_check_version_skew(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"note_id": 1, "message": "Stored note #1.", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="x", tags=None, priority="medium",
                                      path=str(tmp_path), port=8765)
            m.cmd_remember(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_recall_calls_check_version_skew(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"notes": "", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="x", tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            m.cmd_recall(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_forget_calls_check_version_skew(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": 1}
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), port=8765)
            m.cmd_forget(args)
        mock_check.assert_called_once_with(8765)

    def test_cmd_status_reuses_fetched_status_payload_not_a_second_probe(self, tmp_path):
        """cmd_status already fetched /v1/status for its own display — the
        version-skew check must reuse that payload (`daemon_status=`) rather
        than firing a redundant second HTTP call."""
        mock_resp = MagicMock()
        status_data = {
            "workspace_root": str(tmp_path), "indexed_files": 1, "total_chunks": 1,
            "last_indexed": "2026-01-01T00:00:00Z", "mode": "full", "embed_model": "m",
            "version_stamp": "1.0.0+abc1234",
        }
        mock_resp.json.return_value = status_data
        mock_resp.raise_for_status.return_value = None
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.get", return_value=mock_resp), \
             patch("main._check_version_skew") as mock_check:
            MockReg.return_value.get.return_value = {"port": 8765, "pid": 1, "workspace": str(tmp_path)}
            args = argparse.Namespace(path=str(tmp_path), port=8765, all=False)
            m.cmd_status(args)
        mock_check.assert_called_once_with(8765, daemon_status=status_data)


# ---------------------------------------------------------------------------
# cmd_index / cmd_search (UPG-CLI-MEMONLY-CRASH)
# ---------------------------------------------------------------------------

class TestCmdIndex:
    def test_posts_to_index_endpoint_and_prints_result(self, tmp_path, capsys):
        import argparse
        from unittest.mock import patch, MagicMock

        # Real IndexResponse shape (app/models.py): indexed_files, total_chunks,
        # processing_ms, model.
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "indexed_files": 3, "total_chunks": 12, "processing_ms": 40, "model": "org/embed-model",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), force=False, port=8765)
            m.cmd_index(args)

        call_url = mock_post.call_args[0][0]
        assert "/v1/index" in call_url
        out = capsys.readouterr().out
        assert "3" in out
        assert "12" in out
        assert "40" in out

    def test_prints_human_text_not_raw_json_and_omits_model_field(self, tmp_path, capsys) -> None:
        """UPG-CLI-SMALL-UX: `vectr index` used to print `json.dumps(...)`
        verbatim, including the irrelevant embedding-model-name `model` field.
        Output must be human text, and must not surface `model` at all."""
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "indexed_files": 7, "total_chunks": 55, "processing_ms": 1234,
            "model": "sentence-transformers/some-embedding-model",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), force=False, port=8765)
            m.cmd_index(args)

        out = capsys.readouterr().out
        assert "{" not in out  # not raw JSON
        assert "some-embedding-model" not in out
        assert "model" not in out.lower()
        assert "7" in out and "55" in out and "1234" in out

    def test_connect_error_exits_nonzero_with_clean_message(self, tmp_path, capsys):
        import argparse
        import httpx
        from unittest.mock import patch

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), force=False, port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_index(args)

        assert exc_info.value.code == 1
        assert "not running" in capsys.readouterr().err.lower()

    def test_memory_only_gate_prints_detail_instead_of_crashing(self, tmp_path, capsys):
        """UPG-CLI-MEMONLY-CRASH: indexing against a memory-only daemon (which
        503s /v1/index) must print the server's clean detail message and exit
        1 — not raise an unhandled httpx.HTTPStatusError traceback."""
        import argparse
        from unittest.mock import patch

        mock_resp = _mode_gated_response("memory_only_mode", "vectr is in memory-only mode...")

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), force=False, port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_index(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "memory-only mode" in err
        assert "Traceback" not in err


class TestCmdSearch:
    def test_posts_to_search_endpoint_and_prints_results(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"file": "a.py", "lines": "1-5", "score": 0.9, "symbol": "foo", "language": "python",
                 "content": "def foo(): pass"},
            ],
            "query_time_ms": 5,
            "chunks_searched": 10,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="foo function", n=5, language=None, port=8765)
            m.cmd_search(args)

        call_url = mock_post.call_args[0][0]
        assert "/v1/search" in call_url
        assert "a.py" in capsys.readouterr().out

    def test_connect_error_exits_nonzero_with_clean_message(self, tmp_path, capsys, monkeypatch):
        import argparse
        import httpx
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="foo", n=5, language=None, port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_search(args)

        assert exc_info.value.code == 1
        assert "not running" in capsys.readouterr().err.lower()

    def test_search_only_gate_prints_detail_instead_of_crashing(self, tmp_path, capsys, monkeypatch):
        """UPG-CLI-MEMONLY-CRASH companion: search against a search-only-mode
        daemon succeeds (search is always on there); the crash class this
        guards is the same handler used when a mode gate 503s search itself
        (e.g. a future gate, or a misconfigured instance)."""
        import argparse
        from unittest.mock import patch

        mock_resp = _mode_gated_response("some_gate_mode", "vectr declined this request...")

        monkeypatch.chdir(tmp_path)
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="foo", n=5, language=None, port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_search(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "vectr declined this request" in err
        assert "Traceback" not in err

    def test_low_confidence_prints_cli_form_banner(self, tmp_path, capsys, monkeypatch):
        """UPG-CLI-SEARCH-FLOOR: the CLI must render the EXISTING
        low_confidence signal (already computed server-side, already
        exposed on SearchResponse) — before this fix it was silently
        dropped, so a nonsense query printed formatted results
        indistinguishable from a real hit. The CLI banner must not mention
        `vectr_locate` — no such subcommand exists."""
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"file": "a.py", "lines": "1-5", "score": 0.05, "symbol": None,
                 "language": "python", "content": "garbage"},
            ],
            "query_time_ms": 5,
            "chunks_searched": 10,
            "low_confidence": True,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="asdkjfasdlkfj", n=5, language=None, port=8765)
            m.cmd_search(args)

        err = capsys.readouterr().err
        assert "Low confidence" in err
        assert "vectr_locate" not in err

    def test_confident_result_prints_no_banner(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"file": "a.py", "lines": "1-5", "score": 0.95, "symbol": "foo",
                 "language": "python", "content": "def foo(): pass"},
            ],
            "query_time_ms": 5,
            "chunks_searched": 10,
            "low_confidence": False,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="foo function", n=5, language=None, port=8765)
            m.cmd_search(args)

        assert "Low confidence" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_fetch (UPG-CTX-EVICT)
# ---------------------------------------------------------------------------

class TestCmdFetch:
    def test_posts_to_fetch_endpoint_and_prints_content(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": "a.py:1-5", "found": True, "file_path": "a.py", "lines": "1-5",
                 "symbol": "foo", "language": "python", "content": "def foo(): pass"},
            ],
            "note": None,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["a.py:1-5"], port=8765)
            m.cmd_fetch(args)

        call_url = mock_post.call_args[0][0]
        assert "/v1/fetch" in call_url
        assert mock_post.call_args[1]["json"] == {"ids": ["a.py:1-5"]}
        out = capsys.readouterr().out
        assert "a.py:1-5" in out
        assert "def foo(): pass" in out

    def test_storage_capped_symbol_prints_truncation_warning(self, tmp_path, capsys, monkeypatch):
        """UPG-FETCH-TRUNCATION-SILENT: `vectr fetch` must carry the same
        truncation warning as the MCP vectr_fetch tool — REST/CLI is its own
        renderer, not automatically covered by the MCP-layer fix."""
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        stored_content = "\n".join(f"    line {i}" for i in range(45))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": "eviction_advisor.py:55-429", "found": True,
                 "file_path": "eviction_advisor.py", "lines": "55-429",
                 "symbol": "EvictionAdvisor", "language": "python",
                 "content": stored_content},
            ],
            "note": None,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["eviction_advisor.py:55-429"], port=8765)
            m.cmd_fetch(args)

        out = capsys.readouterr().out
        assert "content capped at ~2000 chars" in out
        assert "Read(" in out and "offset=54" in out and "limit=375" in out

    def test_complete_small_chunk_prints_no_truncation_warning(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": "a.py:1-5", "found": True, "file_path": "a.py", "lines": "1-5",
                 "symbol": "foo", "language": "python", "content": "def foo():\n    pass"},
            ],
            "note": None,
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["a.py:1-5"], port=8765)
            m.cmd_fetch(args)

        out = capsys.readouterr().out
        assert "content capped" not in out
        assert "Read(" not in out

    def test_missing_id_prints_not_found_to_stderr(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch, MagicMock

        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"id": "gone.py:1-5", "found": False}],
            "note": "One or more requested chunks were not found — the file "
                    "may have changed since indexing.",
        }
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["gone.py:1-5"], port=8765)
            m.cmd_fetch(args)

        err = capsys.readouterr().err
        assert "gone.py:1-5" in err
        assert "not found" in err

    def test_connect_error_exits_nonzero_with_clean_message(self, tmp_path, capsys, monkeypatch):
        import argparse
        import httpx
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["a.py:1-5"], port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_fetch(args)

        assert exc_info.value.code == 1
        assert "not running" in capsys.readouterr().err.lower()

    def test_memory_only_gate_prints_detail_instead_of_crashing(self, tmp_path, capsys, monkeypatch):
        import argparse
        from unittest.mock import patch

        mock_resp = _mode_gated_response("memory_only_mode", "vectr is in memory-only mode...")

        monkeypatch.chdir(tmp_path)
        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(ids=["a.py:1-5"], port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_fetch(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "vectr is in memory-only mode" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# cmd_forget
# ---------------------------------------------------------------------------

class TestCmdForget:
    def test_forget_calls_memory_clear_endpoint(self, tmp_path):
        import httpx
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": 3}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), port=8765)
            m.cmd_forget(args)

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "/v1/memory/clear" in call_url

    def test_forget_all_sweeps_current_and_legacy_cache_layouts(
        self, tmp_path, monkeypatch, capsys
    ):
        # Regression: --all used to glob only the legacy ~/.cache/vectr/db/<hash>/
        # layout, deleting nothing for workspaces on the current
        # ~/.cache/vectr/<hash>/ layout while still reporting success.
        import argparse
        from agent.working_context_store import WorkingContextStore

        monkeypatch.setenv("HOME", str(tmp_path))
        current = tmp_path / ".cache" / "vectr" / "aaaa1111bbbb"
        legacy = tmp_path / ".cache" / "vectr" / "db" / "cccc2222dddd"
        for d in (current, legacy):
            d.mkdir(parents=True)
            WorkingContextStore(str(d)).remember("/ws", f"note under {d}")

        args = argparse.Namespace(path=str(tmp_path), port=8765, all=True)
        m.cmd_forget(args)

        out = capsys.readouterr().out
        assert "Deleted 2 working-memory notes" in out
        for d in (current, legacy):
            assert WorkingContextStore(str(d)).forget_all_workspaces() == 0

    def test_forget_prints_deleted_count(self, tmp_path, capsys):
        import httpx
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": 7}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(path=str(tmp_path), port=8765)
            m.cmd_forget(args)

        out = capsys.readouterr().out
        assert "7" in out


# ---------------------------------------------------------------------------
# cmd_remember / cmd_recall (UPG-9.1)
# ---------------------------------------------------------------------------

class TestCmdRemember:
    def test_posts_to_remember_endpoint_with_content(self, tmp_path):
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"note_id": 1, "message": "Stored note #1.", "processing_ms": 2}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="lock flow at resolver.rs:214", tags=["lock"],
                                      priority="high", path=str(tmp_path), port=8765)
            m.cmd_remember(args)

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "/v1/remember" in call_url
        payload = mock_post.call_args[1]["json"]
        assert payload["content"] == "lock flow at resolver.rs:214"
        assert payload["priority"] == "high"
        assert payload["tags"] == ["lock"]

    def test_prints_server_message(self, tmp_path, capsys):
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"note_id": 7, "message": "Stored note #7.", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="x", tags=None, priority="medium",
                                      path=str(tmp_path), port=8765)
            m.cmd_remember(args)

        assert "Stored note #7." in capsys.readouterr().out

    def test_connect_error_exits_nonzero(self, tmp_path):
        import argparse
        import httpx
        from unittest.mock import patch

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="x", tags=None, priority="medium",
                                      path=str(tmp_path), port=8765)
            with pytest.raises(SystemExit) as exc:
                m.cmd_remember(args)
            assert exc.value.code == 1

    def test_agent_flag_included_in_payload(self, tmp_path):
        """UPG-SUBAGENT-MEMORY: --agent reaches the /v1/remember payload."""
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"note_id": 3, "message": "Stored note #3.", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="found the bug", tags=None, priority="medium",
                                      path=str(tmp_path), port=8765, agent="coder-2")
            m.cmd_remember(args)

        payload = mock_post.call_args[1]["json"]
        assert payload["agent"] == "coder-2"

    def test_agent_flag_omitted_when_not_set(self, tmp_path):
        """Absent --agent leaves the payload unchanged (backwards compatible)."""
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"note_id": 4, "message": "Stored note #4.", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(content="no agent here", tags=None, priority="medium",
                                      path=str(tmp_path), port=8765)
            m.cmd_remember(args)

        payload = mock_post.call_args[1]["json"]
        assert "agent" not in payload


class TestCmdRecall:
    def test_prints_notes_to_stdout(self, tmp_path, capsys):
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"notes": "[#1] lock flow at resolver.rs:214", "processing_ms": 3}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp) as mock_post:
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="lock flow", tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            m.cmd_recall(args)

        call_url = mock_post.call_args[0][0]
        assert "/v1/recall" in call_url
        assert mock_post.call_args[1]["json"]["query"] == "lock flow"
        assert "resolver.rs:214" in capsys.readouterr().out

    def test_empty_notes_prints_nothing(self, tmp_path, capsys):
        import argparse
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"notes": "", "processing_ms": 1}
        mock_resp.raise_for_status.return_value = None

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query=None, tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            m.cmd_recall(args)

        assert capsys.readouterr().out == ""

    def test_daemon_down_follows_standard_error_contract(self, tmp_path, capsys):
        """UPG-CLI-RECALL-EXITCODE: the human `vectr recall` subcommand follows
        the same error contract as every sibling (stderr message + exit 1) —
        it is NOT the hook path (hooks invoke `vectr hook <event>`, which owns
        its own never-raise resilience in cmd_hook/_fetch_recall)."""
        import argparse
        import httpx
        from unittest.mock import patch

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="lock flow", tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_recall(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "not running" in err.lower()

    def test_memory_only_and_search_only_gate_errors_still_print_detail(self, tmp_path, capsys):
        """A daemon that's up but declines the request (e.g. search-only mode
        gating /v1/recall's 503) must also print the clean server detail and
        exit 1, not crash with a raw HTTPStatusError traceback."""
        import argparse
        import httpx
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "detail": {"error": "search_only_mode", "detail": "vectr is in search-only mode..."},
        }
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=mock_resp,
        )

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", return_value=mock_resp):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="lock flow", tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_recall(args)

        assert exc_info.value.code == 1
        assert "search-only mode" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# SessionStart hook + vectr init --hooks (UPG-9.4)
# ---------------------------------------------------------------------------

class TestCmdHookSessionStart:
    def _run(self, stdin_json: str, recall_text: str, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value=recall_text):
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        return capsys.readouterr().out

    def test_emits_additionalcontext_envelope_with_boot_notes(self, monkeypatch, capsys):
        out = self._run('{"cwd": "/project/a", "source": "startup"}',
                        "[1] [DIRECTIVE] never push to main", monkeypatch, capsys)
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "never push to main" in payload["hookSpecificOutput"]["additionalContext"]

    def test_announces_no_double_recall_upg115(self, monkeypatch, capsys):
        """UPG-11.5 — SessionStart injection must tell the model not to also
        self-call vectr_recall for the same notes (the double-dip found in the
        eval v2 N=1 audit)."""
        out = self._run('{"cwd": "/project/a", "source": "startup"}',
                        "[1] [DIRECTIVE] never push to main", monkeypatch, capsys)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "vectr_recall" in ctx
        assert ctx.index(m._HOOK_NO_DOUBLE_RECALL_LINE) < ctx.index("never push to main"), (
            "no-double-recall notice must come before the injected notes"
        )

    def test_uses_boot_payload(self, monkeypatch, capsys):
        """The SessionStart hook must request the unconditional boot set."""
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/project/a"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        # UPG-HOOK-INJECT-OBSERVABILITY: hook_event is the wire that lets the
        # daemon count this as a SessionStart injection.
        assert mock_fetch.call_args[0][1] == {"boot": True, "hook_event": "SessionStart"}

    def test_emits_nothing_when_no_notes(self, monkeypatch, capsys):
        """A fresh workspace injects nothing — no empty envelope, just silence."""
        out = self._run('{"cwd": "/project/a"}', "", monkeypatch, capsys)
        assert out.strip() == ""

    def test_no_registered_daemon_injects_nothing(self, monkeypatch, capsys):
        """If this workspace has no registered daemon, inject nothing — never fall
        back to a default port that may serve an UNRELATED workspace's memory."""
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/project/a"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="leaked notes") as mock_fetch:
            MockReg.return_value.get.return_value = None  # no instance for this workspace
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        mock_fetch.assert_not_called()        # must not even query a daemon
        assert capsys.readouterr().out.strip() == ""

    def test_never_raises_on_bad_stdin(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value=""):
            MockReg.return_value.get.return_value = {"port": 8765}
            # Must not raise even with garbage stdin.
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert capsys.readouterr().out.strip() == ""

    # TRIGGER-ENGINE wave 2a: session_id threading + post-compaction merge.
    def test_threads_session_id_into_boot_payload(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(
            '{"cwd": "/project/a", "session_id": "abc-123"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert mock_fetch.call_args[0][1]["session_id"] == "abc-123"

    def test_omits_session_id_from_payload_when_absent(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/project/a"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert "session_id" not in mock_fetch.call_args[0][1]

    def test_compact_source_merges_post_compaction_into_events(self, monkeypatch, capsys):
        """The compact-source SessionStart call is the deterministic first
        delivery point after PreCompact's reset — it must additionally cover
        post-compaction-only triggers, not just session-start."""
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(
            '{"cwd": "/project/a", "source": "compact"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert mock_fetch.call_args[0][1]["events"] == ["session-start", "post-compaction"]

    def test_non_compact_source_does_not_merge_post_compaction(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(
            '{"cwd": "/project/a", "source": "startup"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert "events" not in mock_fetch.call_args[0][1]


class TestCmdHookUserPromptSubmit:
    def _run(self, stdin_json: str, recall_text: str, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value=recall_text) as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="user-prompt-submit"))
        return capsys.readouterr().out, mock_fetch

    def test_emits_userpromptsubmit_envelope_with_recalled_notes(self, monkeypatch, capsys):
        out, _ = self._run('{"cwd": "/p", "prompt": "fix the workspace lock"}',
                           "[1] lock_workspace() at resolver.rs:214", monkeypatch, capsys)
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "resolver.rs:214" in payload["hookSpecificOutput"]["additionalContext"]

    def test_announces_no_double_recall_upg115(self, monkeypatch, capsys):
        """UPG-11.5 — same notice on UserPromptSubmit's per-turn injection."""
        out, _ = self._run('{"cwd": "/p", "prompt": "fix the workspace lock"}',
                           "[1] lock_workspace() at resolver.rs:214", monkeypatch, capsys)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert m._HOOK_NO_DOUBLE_RECALL_LINE in ctx

    def test_no_announce_when_no_notes(self, monkeypatch, capsys):
        """Empty injection stays empty — the notice is not tacked onto nothing."""
        out, _ = self._run('{"cwd": "/p", "prompt": "unrelated"}', "", monkeypatch, capsys)
        assert out.strip() == ""

    def test_recalls_with_prompt_as_query_and_cutoff(self, monkeypatch, capsys):
        _, mock_fetch = self._run('{"cwd": "/p", "prompt": "lock flow"}', "x", monkeypatch, capsys)
        payload = mock_fetch.call_args[0][1]
        assert payload["query"] == "lock flow"
        assert "min_similarity" in payload      # relevance cutoff applied (UPG-5.1)
        assert payload["limit"] == m._HOOK_RECALL_LIMIT

    def test_empty_prompt_injects_nothing(self, monkeypatch, capsys):
        out, mock_fetch = self._run('{"cwd": "/p", "prompt": "   "}', "x", monkeypatch, capsys)
        mock_fetch.assert_not_called()
        assert out.strip() == ""

    def test_offtopic_recall_empty_injects_nothing(self, monkeypatch, capsys):
        """When the cutoff withholds everything, the hook injects nothing."""
        out, _ = self._run('{"cwd": "/p", "prompt": "unrelated"}', "", monkeypatch, capsys)
        assert out.strip() == ""

    def test_sends_hook_event_userpromptsubmit(self, monkeypatch, capsys):
        """UPG-HOOK-INJECT-OBSERVABILITY: this is the wire that lets the daemon
        count a UserPromptSubmit injection — without it, counters never increment."""
        _, mock_fetch = self._run('{"cwd": "/p", "prompt": "lock flow"}', "x", monkeypatch, capsys)
        assert mock_fetch.call_args[0][1]["hook_event"] == "UserPromptSubmit"

    # TRIGGER-ENGINE wave 2a
    def test_always_merges_prompt_submit_event(self, monkeypatch, capsys):
        _, mock_fetch = self._run('{"cwd": "/p", "prompt": "lock flow"}', "x", monkeypatch, capsys)
        assert mock_fetch.call_args[0][1]["events"] == ["prompt-submit"]

    def test_threads_session_id_into_payload(self, monkeypatch, capsys):
        _, mock_fetch = self._run(
            '{"cwd": "/p", "prompt": "lock flow", "session_id": "abc-123"}', "x", monkeypatch, capsys)
        assert mock_fetch.call_args[0][1]["session_id"] == "abc-123"

    def test_omits_session_id_from_payload_when_absent(self, monkeypatch, capsys):
        _, mock_fetch = self._run('{"cwd": "/p", "prompt": "lock flow"}', "x", monkeypatch, capsys)
        assert "session_id" not in mock_fetch.call_args[0][1]


class TestCmdHookPreToolUse:
    def _run(self, stdin_json: str, recall_text: str, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value=recall_text) as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-tool-use"))
        return capsys.readouterr().out, mock_fetch

    def test_emits_gotcha_for_edited_file(self, monkeypatch, capsys):
        stdin = '{"cwd": "/p", "tool_name": "Edit", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}}'
        out, mock_fetch = self._run(stdin, "[1] [GOTCHA] index_file takes workspace first", monkeypatch, capsys)
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "index_file takes workspace first" in payload["hookSpecificOutput"]["additionalContext"]
        sent = mock_fetch.call_args[0][1]
        assert sent["file_path"] == "/p/agent/symbol_graph.py"
        assert sent["kind"] == "gotcha"
        # UPG-HOOK-INJECT-OBSERVABILITY: the wire that lets the daemon count
        # this as a PreToolUse injection.
        assert sent["hook_event"] == "PreToolUse"

    def test_emits_gotcha_for_read_file(self, monkeypatch, capsys):
        """UPG-HOOK-INJECT-OBSERVABILITY (c): file-path extraction is generic
        (tool_input.file_path) regardless of tool name — Read must surface a
        recorded gotcha exactly like Edit/Write. Which tool names actually
        reach this hook is a matcher decision in `_write_claude_hooks`, not a
        content-based branch here."""
        stdin = '{"cwd": "/p", "tool_name": "Read", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}}'
        out, mock_fetch = self._run(stdin, "[1] [GOTCHA] index_file takes workspace first", monkeypatch, capsys)
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "index_file takes workspace first" in payload["hookSpecificOutput"]["additionalContext"]
        sent = mock_fetch.call_args[0][1]
        assert sent["file_path"] == "/p/agent/symbol_graph.py"

    def test_no_double_recall_notice_on_pretooluse(self, monkeypatch, capsys):
        """UPG-11.5's notice is scoped to SessionStart/UserPromptSubmit only —
        PreToolUse gotcha injection is targeted, not a recall substitute."""
        stdin = '{"cwd": "/p", "tool_name": "Edit", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}}'
        out, _ = self._run(stdin, "[1] [GOTCHA] index_file takes workspace first", monkeypatch, capsys)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert m._HOOK_NO_DOUBLE_RECALL_LINE not in ctx

    def test_no_file_path_injects_nothing(self, monkeypatch, capsys):
        out, mock_fetch = self._run('{"cwd": "/p", "tool_name": "Bash", "tool_input": {}}', "x", monkeypatch, capsys)
        mock_fetch.assert_not_called()
        assert out.strip() == ""

    def test_unrelated_file_no_gotcha_injects_nothing(self, monkeypatch, capsys):
        stdin = '{"cwd": "/p", "tool_input": {"file_path": "/p/README.md"}}'
        out, _ = self._run(stdin, "", monkeypatch, capsys)
        assert out.strip() == ""

    # TRIGGER-ENGINE wave 2a
    def test_threads_session_id_into_payload(self, monkeypatch, capsys):
        stdin = ('{"cwd": "/p", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}, '
                  '"session_id": "abc-123"}')
        _, mock_fetch = self._run(stdin, "x", monkeypatch, capsys)
        assert mock_fetch.call_args[0][1]["session_id"] == "abc-123"

    def test_omits_session_id_from_payload_when_absent(self, monkeypatch, capsys):
        stdin = '{"cwd": "/p", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}}'
        _, mock_fetch = self._run(stdin, "x", monkeypatch, capsys)
        assert "session_id" not in mock_fetch.call_args[0][1]


class TestCmdHookPreCompact:
    def test_snapshots_with_trigger_in_label_and_emits_nothing(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/p", "trigger": "auto"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._post_snapshot", return_value=True) as mock_snap:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-compact"))
        # snapshot taken with a label carrying the trigger; no context injected.
        label = mock_snap.call_args[0][1]
        assert "auto" in label and label.startswith("pre-compact-")
        assert capsys.readouterr().out.strip() == ""

    def test_snapshot_failure_does_not_raise(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/p", "trigger": "manual"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._post_snapshot", return_value=False):
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-compact"))  # must not raise

    # TRIGGER-ENGINE wave 2a (§3 "cleared on compaction")
    def test_calls_trigger_reset_when_session_id_present(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(
            '{"cwd": "/p", "trigger": "auto", "session_id": "abc-123"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._post_snapshot", return_value=True), \
             patch("main._post_trigger_reset", return_value=True) as mock_reset:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-compact"))
        mock_reset.assert_called_once_with(8765, "abc-123")

    def test_does_not_call_trigger_reset_when_session_id_absent(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/p", "trigger": "auto"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._post_snapshot", return_value=True), \
             patch("main._post_trigger_reset", return_value=True) as mock_reset:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-compact"))
        mock_reset.assert_not_called()

    def test_trigger_reset_failure_does_not_raise(self, monkeypatch, capsys):
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO(
            '{"cwd": "/p", "trigger": "auto", "session_id": "abc-123"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._post_snapshot", return_value=True), \
             patch("main._post_trigger_reset", return_value=False):
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="pre-compact"))  # must not raise


class TestHookInstanceResolution:
    """Multi-instance safety: cwd → registry → correct port, no hardcoded port."""

    def test_two_instances_resolve_to_their_own_ports(self, tmp_path):
        from unittest.mock import patch
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        reg.register(workspace_hash(str(a.resolve())), str(a.resolve()), 9001, 111)
        reg.register(workspace_hash(str(b.resolve())), str(b.resolve()), 9002, 222)
        with patch("main.InstanceRegistry", return_value=reg):
            assert m._resolve_hook_instance(str(a))["port"] == 9001
            assert m._resolve_hook_instance(str(b))["port"] == 9002

    def test_walks_up_from_subdirectory(self, tmp_path):
        from unittest.mock import patch
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        root = tmp_path / "proj"
        sub = root / "src" / "deep"
        sub.mkdir(parents=True)
        reg.register(workspace_hash(str(root.resolve())), str(root.resolve()), 9111, 333)
        with patch("main.InstanceRegistry", return_value=reg):
            assert m._resolve_hook_instance(str(sub))["port"] == 9111

    def test_unregistered_workspace_resolves_none(self, tmp_path):
        from unittest.mock import patch
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        with patch("main.InstanceRegistry", return_value=reg):
            assert m._resolve_hook_instance(str(tmp_path / "nope")) is None


class TestFetchRecallResilience:
    def test_returns_empty_on_connect_error(self):
        import httpx
        from unittest.mock import patch
        with patch("httpx.post", side_effect=httpx.ConnectError("down")):
            assert m._fetch_recall(8765, {"boot": True}) == ""


class TestInitHooks:
    def test_writes_sessionstart_hook(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        m._write_claude_hooks(str(tmp_path))
        data = json.loads(settings.read_text())
        groups = data["hooks"]["SessionStart"]
        assert len(groups) == 1
        assert groups[0]["matcher"] == "startup|resume|clear|compact"
        assert groups[0]["hooks"][0]["command"] == "vectr hook session-start"

    def test_writes_userpromptsubmit_hook_without_matcher(self, tmp_path):
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        groups = data["hooks"]["UserPromptSubmit"]
        assert len(groups) == 1
        assert "matcher" not in groups[0]   # UserPromptSubmit has no matcher
        assert groups[0]["hooks"][0]["command"] == "vectr hook user-prompt-submit"

    def test_writes_pretooluse_hook_with_edit_write_read_matcher(self, tmp_path):
        """UPG-HOOK-INJECT-OBSERVABILITY (c): matcher extended to Read so
        gotcha injection also fires on file-reading tools, not only edits."""
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        groups = data["hooks"]["PreToolUse"]
        assert len(groups) == 1
        assert groups[0]["matcher"] == "Edit|Write|Read"
        assert groups[0]["hooks"][0]["command"] == "vectr hook pre-tool-use"

    def test_writes_precompact_hook(self, tmp_path):
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        groups = data["hooks"]["PreCompact"]
        assert len(groups) == 1
        assert groups[0]["matcher"] == "manual|auto"
        assert groups[0]["hooks"][0]["command"] == "vectr hook pre-compact"

    def test_sessionstart_compact_matcher_enables_post_compact_reinject(self, tmp_path):
        """UPG-9.7's re-inject path = the SessionStart `compact` matcher (UPG-9.4)."""
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "compact" in data["hooks"]["SessionStart"][0]["matcher"]

    def test_preserves_existing_settings(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"enableAllProjectMcpServers": true}\n')
        m._write_claude_hooks(str(tmp_path))
        data = json.loads(settings.read_text())
        assert data["enableAllProjectMcpServers"] is True
        assert "SessionStart" in data["hooks"]

    def test_idempotent_no_duplicate_groups(self, tmp_path):
        m._write_claude_hooks(str(tmp_path))
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert len(data["hooks"]["SessionStart"]) == 1

    def test_keeps_user_hooks(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "hooks": {"SessionStart": [
                {"matcher": "startup", "hooks": [{"type": "command", "command": "my-own-hook"}]}
            ]}
        }))
        m._write_claude_hooks(str(tmp_path))
        groups = json.loads(settings.read_text())["hooks"]["SessionStart"]
        cmds = [h["command"] for g in groups for h in g["hooks"]]
        assert "my-own-hook" in cmds
        assert "vectr hook session-start" in cmds

    def test_reset_removes_vectr_hooks_only(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enableAllProjectMcpServers": True,
            "hooks": {"SessionStart": [
                {"matcher": "startup", "hooks": [{"type": "command", "command": "my-own-hook"}]}
            ]}
        }))
        m._write_claude_hooks(str(tmp_path))   # adds vectr group
        m._remove_vectr_hooks(str(tmp_path))   # removes only vectr group
        data = json.loads(settings.read_text())
        assert data["enableAllProjectMcpServers"] is True
        cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
        assert cmds == ["my-own-hook"]


# ---------------------------------------------------------------------------
# CLAUDE.md hook-aware session-start guidance (UPG-11.5)
# ---------------------------------------------------------------------------

class TestClaudeMdHookAwareGuidance:
    """When Claude Code hooks are installed, CLAUDE.md must stop telling the
    model to self-call vectr_recall at session start — the hook already
    injects notes automatically (UPG-11.5). Without hooks, unchanged."""

    def test_without_hooks_default_guidance_unchanged(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert 'call `vectr_recall(query="<your task>")`' in content
        # The hooks-aware splice must be absent (its distinctive first phrase —
        # "auto-injected" alone is no longer a valid proxy: the kind-taxonomy
        # row uses the word for directive notes in both variants).
        assert "your working-memory notes are auto-injected automatically" not in content

    def test_with_hooks_installed_uses_hook_aware_guidance(self, tmp_path):
        m._write_claude_hooks(str(tmp_path))          # hooks installed first
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "your working-memory notes are auto-injected automatically" in content
        assert 'call `vectr_recall(query="<your task>")`' not in content
        # vectr_recall is still documented — just redirected to on-demand use.
        assert "on-demand deep-dive" in content
        assert "note_id" in content


class TestInstructionVetV3ToolLoadingSplice:
    """UPG-INSTRUCTION-VET V3: the deferred-tool loading blockquote
    (ToolSearch, mcp__vectr__ tool-name prefix) is host-specific mechanics —
    spliced into CLAUDE.md only. Every other IDE config surface gets the
    shared body without it, and no placeholder residue."""

    def test_claude_md_contains_tool_loading_guidance(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "ToolSearch" in content
        assert "mcp__vectr__vectr_fetch" in content  # V6: fetch in the select list
        assert "__TOOL_LOADING_GUIDANCE__" not in content

    def test_cursor_rules_omit_tool_loading_guidance(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / ".cursor" / "rules" / "vectr.mdc").read_text()
        assert "ToolSearch" not in content
        assert "mcp__vectr__" not in content
        assert "__TOOL_LOADING_GUIDANCE__" not in content

    def test_append_only_ide_configs_omit_tool_loading_guidance(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# My agents\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "AGENTS.md").read_text()
        assert "ToolSearch" not in content
        assert "mcp__vectr__" not in content
        assert "__TOOL_LOADING_GUIDANCE__" not in content

    def test_claude_md_teaches_correction_capture(self, tmp_path):
        # UPG-CORRECTION-CAPTURE: user corrections must be stored as directives.
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert 'kind="directive"' in content
        assert "corrects your behavior" in content

    def test_init_hooks_single_run_produces_hook_aware_config(self, tmp_path):
        """`vectr init --hooks` writes hooks AND config in one run — ordering
        inside cmd_init must not race so CLAUDE.md still reflects hooks."""
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path), hooks=True))
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "auto-injected" in content
        assert 'call `vectr_recall(query="<your task>")`' not in content
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "SessionStart" in settings["hooks"]

    def test_init_hooks_single_run_preserves_mcp_flag(self, tmp_path):
        """UPG-11.5 reordered hooks-then-config; settings.json must still end
        up with enableAllProjectMcpServers even though hooks wrote the file
        first (regression guard for the reorder)."""
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path), hooks=True))
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert settings["enableAllProjectMcpServers"] is True
        assert "SessionStart" in settings["hooks"]

    def test_init_without_hooks_flag_unchanged(self, tmp_path):
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path), hooks=False))
        content = (tmp_path / "CLAUDE.md").read_text()
        assert 'call `vectr_recall(query="<your task>")`' in content
        assert not (tmp_path / ".claude" / "settings.json").exists() or \
            "hooks" not in json.loads((tmp_path / ".claude" / "settings.json").read_text())

    def test_other_ide_files_stay_default_even_with_hooks(self, tmp_path):
        """Claude Code hooks don't reach other editors — AGENTS.md/cursor rules
        keep the self-recall guidance regardless of hook installation."""
        (tmp_path / "AGENTS.md").write_text("Existing\n")
        m._write_claude_hooks(str(tmp_path))
        m._write_workspace_config(str(tmp_path), 8765)
        agents_content = (tmp_path / "AGENTS.md").read_text()
        assert 'call `vectr_recall(query="<your task>")`' in agents_content
        cursor_content = (tmp_path / ".cursor" / "rules" / "vectr.mdc").read_text()
        assert 'call `vectr_recall(query="<your task>")`' in cursor_content

    def test_render_claude_md_hooks_installed_helper(self, tmp_path):
        """Unit-level: _hooks_installed reads back the settings written by
        _write_claude_hooks, no config generation needed to exercise it."""
        assert m._hooks_installed(str(tmp_path)) is False
        m._write_claude_hooks(str(tmp_path))
        assert m._hooks_installed(str(tmp_path)) is True


# ---------------------------------------------------------------------------
# TestMergeSafeInit
# ---------------------------------------------------------------------------

class TestMergeSafeInit:

    # --- CLAUDE.md ---

    def test_claude_md_created_with_vectr_block_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "<!-- vectr-start -->" in content
        assert "<!-- vectr-end -->" in content
        assert "vectr_status" in content

    def test_claude_md_existing_content_preserved_and_block_appended(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "@AGENTS.md" in content
        assert "<!-- vectr-start -->" in content
        assert "<!-- vectr-end -->" in content

    def test_claude_md_idempotent(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert content.count("<!-- vectr-start -->") == 1
        assert content.count("<!-- vectr-end -->") == 1

    def test_claude_md_idempotent_with_existing_user_content(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n")
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert content.count("<!-- vectr-start -->") == 1
        assert "@AGENTS.md" in content

    # --- AGENTS.md ---

    def test_agents_md_appended_if_exists(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Existing content\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "AGENTS.md").read_text()
        assert "Existing content" in content
        assert "<!-- vectr-start -->" in content

    def test_agents_md_not_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert not (tmp_path / "AGENTS.md").exists()

    def test_agents_md_idempotent(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "AGENTS.md").read_text()
        assert content.count("<!-- vectr-start -->") == 1

    # --- .cursorrules ---

    def test_cursorrules_appended_if_exists(self, tmp_path):
        (tmp_path / ".cursorrules").write_text("Existing rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / ".cursorrules").read_text()
        assert "Existing rules" in content
        assert "<!-- vectr-start -->" in content

    def test_cursorrules_not_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert not (tmp_path / ".cursorrules").exists()

    # --- .cursor/rules/vectr.mdc ---

    def test_cursor_rules_vectr_mdc_always_created(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        mdc = tmp_path / ".cursor" / "rules" / "vectr.mdc"
        assert mdc.exists()
        content = mdc.read_text()
        assert "alwaysApply: true" in content
        assert "vectr_status" in content

    def test_cursor_rules_vectr_mdc_idempotent(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        m._write_workspace_config(str(tmp_path), 8765)
        mdc = tmp_path / ".cursor" / "rules" / "vectr.mdc"
        assert mdc.read_text().count("alwaysApply: true") == 1

    # --- .github/copilot-instructions.md ---

    def test_github_copilot_instructions_appended_if_exists(self, tmp_path):
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "copilot-instructions.md").write_text("Copilot rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / ".github" / "copilot-instructions.md").read_text()
        assert "Copilot rules" in content
        assert "<!-- vectr-start -->" in content

    def test_github_copilot_instructions_not_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert not (tmp_path / ".github" / "copilot-instructions.md").exists()

    # --- GEMINI.md ---

    def test_gemini_md_appended_if_exists(self, tmp_path):
        (tmp_path / "GEMINI.md").write_text("Gemini rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "GEMINI.md").read_text()
        assert "Gemini rules" in content
        assert "<!-- vectr-start -->" in content

    def test_gemini_md_not_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert not (tmp_path / "GEMINI.md").exists()

    # --- CODEX.md ---

    def test_codex_md_appended_if_exists(self, tmp_path):
        (tmp_path / "CODEX.md").write_text("Codex rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CODEX.md").read_text()
        assert "Codex rules" in content
        assert "<!-- vectr-start -->" in content

    def test_codex_md_not_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert not (tmp_path / "CODEX.md").exists()

    # --- --reset-config ---

    def test_reset_config_removes_vectr_only_claude_md(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        assert (tmp_path / "CLAUDE.md").exists()
        with patch("main.InstanceRegistry"):
            m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_reset_config_preserves_user_content_in_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n")
        m._write_workspace_config(str(tmp_path), 8765)
        with patch("main.InstanceRegistry"):
            m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "@AGENTS.md" in content
        assert "<!-- vectr-start -->" not in content

    def test_reset_config_removes_vectr_block_from_secondary_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules\n")
        m._write_workspace_config(str(tmp_path), 8765)
        assert "<!-- vectr-start -->" in (tmp_path / "AGENTS.md").read_text()
        with patch("main.InstanceRegistry"):
            m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        content = (tmp_path / "AGENTS.md").read_text()
        assert "Rules" in content
        assert "<!-- vectr-start -->" not in content

    def test_reset_config_deletes_cursor_rules_mdc(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        mdc = tmp_path / ".cursor" / "rules" / "vectr.mdc"
        assert mdc.exists()
        with patch("main.InstanceRegistry"):
            m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        assert not mdc.exists()

    def test_reset_config_noop_when_no_vectr_block(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("custom content\n")
        with patch("main.InstanceRegistry"):
            m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        assert (tmp_path / "CLAUDE.md").read_text() == "custom content\n"


# ---------------------------------------------------------------------------
# CLAUDE.md framing — overview, 12-tool tables, rhythm trigger, gain framing
# ---------------------------------------------------------------------------

class TestClaudeMdFraming:
    """Verify the vectr block structure: overview + classified tool tables + rhythm trigger."""

    def _vectr_block(self, tmp_path) -> str:
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        start = content.index("<!-- vectr-start -->")
        end = content.index("<!-- vectr-end -->") + len("<!-- vectr-end -->")
        return content[start:end]

    def test_overview_names_both_capabilities(self, tmp_path):
        block = self._vectr_block(tmp_path)
        assert "semantic search" in block.lower()
        assert "working memory" in block.lower()

    def test_search_section_lists_all_six_tools(self, tmp_path):
        block = self._vectr_block(tmp_path)
        for tool in ("vectr_search", "vectr_locate", "vectr_trace", "vectr_map", "vectr_map_save",
                     "vectr_fetch"):
            assert tool in block, f"{tool} must appear in the search section"

    def test_memory_section_lists_all_seven_tools(self, tmp_path):
        block = self._vectr_block(tmp_path)
        for tool in ("vectr_status", "vectr_recall", "vectr_remember", "vectr_forget",
                     "vectr_evict_hint", "vectr_snapshot", "vectr_snapshot_list"):
            assert tool in block, f"{tool} must appear in the memory section"

    def test_vectr_forget_present(self, tmp_path):
        """vectr_forget was absent from the old CLAUDE.md — must now appear."""
        block = self._vectr_block(tmp_path)
        assert "vectr_forget" in block

    def test_tool_tables_include_example_column(self, tmp_path):
        block = self._vectr_block(tmp_path)
        assert "| Example |" in block or "| Example" in block, (
            "Tool tables must include an Example column"
        )

    def test_rhythm_trigger_pairs_search_and_save(self, tmp_path):
        """Mid-task trigger must be rhythm-based (search→save pair), not a subjective qualifier."""
        block = self._vectr_block(tmp_path)
        assert "pair" in block.lower(), (
            "CLAUDE.md must frame vectr_search + vectr_remember as a pair, not a conditional"
        )

    def test_gain_framing_present(self, tmp_path):
        """Agent must be told saving is a gain (notes survive /compact and future sessions)."""
        block = self._vectr_block(tmp_path)
        assert "gain" in block.lower() or "risk" in block.lower(), (
            "CLAUDE.md must frame saving as a gain, not as losing content"
        )

    def test_sr_rag_verbalization_guidance_present(self, tmp_path):
        """CLAUDE.md must instruct agents to verbalize parametric knowledge before searching (SR-RAG)."""
        block = self._vectr_block(tmp_path)
        assert "verbali" in block.lower(), (
            "CLAUDE.md must include SR-RAG guidance: write out known facts before calling vectr_search"
        )

    def test_verbalization_requires_bounded_confirmation(self, tmp_path):
        """UPG-VERBALIZE-CONFIRM: verbalizing parametric knowledge must never license a
        zero-tool-call answer — the template must always send the agent back through
        one cheap confirming call (vectr_locate) before it cites file:line specifics."""
        block = self._vectr_block(tmp_path)
        assert "always run one cheap confirming call" in block
        assert 'vectr_locate(name="' in block

    def test_verbalization_drops_unsourced_stat(self, tmp_path):
        """The unsourced '26-40%' figure justified skipping tool calls on familiar
        frameworks entirely — must be removed, not merely reworded."""
        block = self._vectr_block(tmp_path)
        assert "26" not in block, "unsourced 26-40% verbalization stat must not ship in CLAUDE.md"

    def test_tool_table_examples_are_keyword_explicit(self, tmp_path):
        """vectr_locate/vectr_trace examples must use name=... — a bare positional
        string ('SymbolName') trained a live failed tool call (F40-class)."""
        block = self._vectr_block(tmp_path)
        assert 'vectr_locate("' not in block and "vectr_locate('" not in block
        assert 'vectr_trace("' not in block and "vectr_trace('" not in block
        assert 'vectr_locate(name=' in block
        assert 'vectr_trace(name=' in block


# ---------------------------------------------------------------------------
# cmd_watch
# ---------------------------------------------------------------------------

class TestCmdWatch:
    def _make_watch_args(self, path: str) -> argparse.Namespace:
        return argparse.Namespace(path=path)

    def test_watch_indexes_workspace_on_startup(self, tmp_path, capsys):
        mock_indexer = MagicMock()
        mock_indexer.index_workspace.return_value = (5, 42)
        mock_watcher = MagicMock()

        with patch("agent.indexer.CodeIndexer", return_value=mock_indexer), \
             patch("agent.watcher.CodeWatcher", return_value=mock_watcher), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            m.cmd_watch(self._make_watch_args(str(tmp_path)))

        mock_indexer.index_workspace.assert_called_once()

    def test_watch_starts_and_stops_watcher(self, tmp_path):
        mock_indexer = MagicMock()
        mock_indexer.index_workspace.return_value = (1, 2)
        mock_watcher = MagicMock()

        with patch("agent.indexer.CodeIndexer", return_value=mock_indexer), \
             patch("agent.watcher.CodeWatcher", return_value=mock_watcher), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            m.cmd_watch(self._make_watch_args(str(tmp_path)))

        mock_watcher.start.assert_called_once()
        mock_watcher.stop.assert_called_once()

    def test_watch_prints_start_guidance(self, tmp_path, capsys):
        mock_indexer = MagicMock()
        mock_indexer.index_workspace.return_value = (3, 15)
        mock_watcher = MagicMock()

        with patch("agent.indexer.CodeIndexer", return_value=mock_indexer), \
             patch("agent.watcher.CodeWatcher", return_value=mock_watcher), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            m.cmd_watch(self._make_watch_args(str(tmp_path)))

        err = capsys.readouterr().err
        assert "vectr start" in err, "cmd_watch must mention 'vectr start' to guide users towards MCP"

    def test_watch_reports_indexed_file_count(self, tmp_path, capsys):
        mock_indexer = MagicMock()
        mock_indexer.index_workspace.return_value = (7, 99)
        mock_watcher = MagicMock()

        with patch("agent.indexer.CodeIndexer", return_value=mock_indexer), \
             patch("agent.watcher.CodeWatcher", return_value=mock_watcher), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            m.cmd_watch(self._make_watch_args(str(tmp_path)))

        err = capsys.readouterr().err
        assert "7" in err and "99" in err, "cmd_watch must print file and chunk counts"


# ---------------------------------------------------------------------------
# UPG-WS-ROOT-MISDETECT: an explicitly-given workspace path must always win
# over the git-toplevel walk-up — `vectr start <path>` on a .git-less
# subdirectory of a git repo must never silently index the enclosing repo.
# ---------------------------------------------------------------------------

class TestIsExplicitWorkspace:
    def test_true_for_positional_workspace_arg(self):
        args = argparse.Namespace(workspace="/some/dir", paths=None)
        assert m._is_explicit_workspace(args) is True

    def test_true_for_path_flag(self):
        args = argparse.Namespace(workspace=None, paths=["/some/dir"])
        assert m._is_explicit_workspace(args) is True

    def test_false_when_neither_given(self):
        args = argparse.Namespace(workspace=None, paths=None)
        assert m._is_explicit_workspace(args) is False

    def test_false_when_paths_is_empty_list(self):
        args = argparse.Namespace(workspace=None, paths=[])
        assert m._is_explicit_workspace(args) is False


class TestCodeWorkspaceFileArg:
    def test_returns_resolved_path_for_code_workspace_positional(self, tmp_path):
        ws_file = tmp_path / "proj.code-workspace"
        ws_file.write_text('{"folders": [{"path": "."}]}')
        args = argparse.Namespace(workspace=str(ws_file))
        assert m._code_workspace_file_arg(args) == str(ws_file.resolve())

    def test_none_for_plain_directory_positional(self, tmp_path):
        args = argparse.Namespace(workspace=str(tmp_path))
        assert m._code_workspace_file_arg(args) is None

    def test_none_when_no_workspace_arg(self):
        args = argparse.Namespace(workspace=None)
        assert m._code_workspace_file_arg(args) is None


class TestWarnIfEnclosingRepo:
    def test_warns_when_nested_inside_enclosing_git_repo(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "sub" / "project"
        nested.mkdir(parents=True)

        m._warn_if_enclosing_repo(str(nested))

        err = capsys.readouterr().err
        assert str(tmp_path) in err
        assert str(nested) in err

    def test_no_warning_when_workspace_is_its_own_repo_root(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()

        m._warn_if_enclosing_repo(str(tmp_path))

        assert capsys.readouterr().err == ""

    def test_no_warning_when_no_git_repo_anywhere(self, tmp_path, capsys):
        nested = tmp_path / "sub"
        nested.mkdir()

        m._warn_if_enclosing_repo(str(nested))

        assert capsys.readouterr().err == ""


class TestWaitForDaemonReady:
    """UPG-CLI-START-READY-RACE: `_do_start` must not print success before
    the daemon is actually reachable."""

    def test_returns_true_immediately_when_already_alive(self):
        with patch("main._is_server_alive", return_value=(True, "/ws")), \
             patch("main.time.sleep") as mock_sleep:
            assert m._wait_for_daemon_ready(8765, 111) is True
        mock_sleep.assert_not_called()

    def test_polls_until_alive(self):
        responses = [(False, None), (False, None), (True, "/ws")]
        with patch("main._is_server_alive", side_effect=responses), \
             patch("main._is_pid_alive", return_value=True), \
             patch("main.time.sleep") as mock_sleep:
            assert m._wait_for_daemon_ready(8765, 111) is True
        assert mock_sleep.call_count == 2

    def test_returns_false_early_when_process_already_dead(self):
        """Must not wait out the full poll-timeout window for a process that
        has already exited — that process will never start responding."""
        with patch("main._is_server_alive", return_value=(False, None)), \
             patch("main._is_pid_alive", return_value=False), \
             patch("main.time.sleep") as mock_sleep:
            assert m._wait_for_daemon_ready(8765, 111) is False
        mock_sleep.assert_not_called()

    def test_returns_false_after_timeout_when_never_alive_but_process_lives(self):
        with patch("main._is_server_alive", return_value=(False, None)), \
             patch("main._is_pid_alive", return_value=True), \
             patch("main.time.monotonic", side_effect=[0.0, 0.1, 100.0]), \
             patch("main.time.sleep"):
            assert m._wait_for_daemon_ready(8765, 111) is False


class TestDoStartReadinessBranches:
    """UPG-CLI-START-READY-RACE: `_do_start`'s printed message must reflect
    whether the daemon actually became reachable, not just that Popen()
    returned."""

    def _patched_popen(self):
        def _mock_popen(cmd, env, **kwargs):
            proc = MagicMock()
            proc.pid = 99999
            return proc
        return _mock_popen

    def test_prints_success_when_daemon_becomes_ready(self, tmp_path, capsys):
        ws = str(tmp_path)
        wh = workspace_hash(ws)
        with patch("subprocess.Popen", side_effect=self._patched_popen()), \
             patch("main.InstanceRegistry") as MockReg, \
             patch("main._migrate_legacy_files"), \
             patch("main._wait_for_daemon_ready", return_value=True), \
             patch("builtins.open", MagicMock()):
            MockReg.return_value.register = MagicMock()
            m._do_start(ws, 8765, wh)

        err = capsys.readouterr().err
        assert "Vectr started" in err
        assert "not responding yet" not in err
        assert "failed to start" not in err

    def test_prints_still_loading_message_when_not_ready_but_process_alive(self, tmp_path, capsys):
        ws = str(tmp_path)
        wh = workspace_hash(ws)
        with patch("subprocess.Popen", side_effect=self._patched_popen()), \
             patch("main.InstanceRegistry") as MockReg, \
             patch("main._migrate_legacy_files"), \
             patch("main._wait_for_daemon_ready", return_value=False), \
             patch("main._is_pid_alive", return_value=True), \
             patch("builtins.open", MagicMock()):
            MockReg.return_value.register = MagicMock()
            m._do_start(ws, 8765, wh)

        err = capsys.readouterr().err
        assert "has not" in err and "responding yet" in err
        assert "Poll readiness with: vectr status" in err

    def test_prints_failed_message_when_process_exited(self, tmp_path, capsys):
        ws = str(tmp_path)
        wh = workspace_hash(ws)
        with patch("subprocess.Popen", side_effect=self._patched_popen()), \
             patch("main.InstanceRegistry") as MockReg, \
             patch("main._migrate_legacy_files"), \
             patch("main._wait_for_daemon_ready", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("builtins.open", MagicMock()):
            MockReg.return_value.register = MagicMock()
            m._do_start(ws, 8765, wh)

        err = capsys.readouterr().err
        assert "failed to start" in err


class TestDoStartExplicitEnvConstruction:
    """_do_start must propagate VECTR_WORKSPACE_EXPLICIT to the daemon
    process only when the caller resolved an explicit path."""

    def _mock_popen_factory(self, captured_env: dict):
        def _mock_popen(cmd, env, **kwargs):
            captured_env.update(env)
            proc = MagicMock()
            proc.pid = 99999
            return proc
        return _mock_popen

    def test_explicit_flag_adds_env_var(self, tmp_path):
        ws = str(tmp_path)
        wh = workspace_hash(ws)
        captured_env: dict = {}

        with patch("subprocess.Popen", side_effect=self._mock_popen_factory(captured_env)), \
             patch("main.InstanceRegistry") as MockReg, \
             patch("main._migrate_legacy_files"), \
             patch("builtins.open", MagicMock()):
            MockReg.return_value.register = MagicMock()
            m._do_start(ws, 8765, wh, workspace_explicit=True)

        assert captured_env.get("VECTR_WORKSPACE_EXPLICIT") == "1"

    def test_default_does_not_add_env_var(self, tmp_path):
        ws = str(tmp_path)
        wh = workspace_hash(ws)
        captured_env: dict = {}

        with patch("subprocess.Popen", side_effect=self._mock_popen_factory(captured_env)), \
             patch("main.InstanceRegistry") as MockReg, \
             patch("main._migrate_legacy_files"), \
             patch("builtins.open", MagicMock()):
            MockReg.return_value.register = MagicMock()
            m._do_start(ws, 8765, wh)

        assert captured_env.get("VECTR_WORKSPACE_EXPLICIT", "") != "1"

    def test_cmd_start_positional_workspace_threads_explicit_true(self, tmp_path):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            args = argparse.Namespace(workspace=str(tmp_path), paths=None, port=8765)
            m.cmd_start(args)

        _, call_kwargs = mock_do_start.call_args
        assert call_kwargs.get("workspace_explicit") is True

    def test_cmd_start_default_cwd_is_not_explicit(self, tmp_path, monkeypatch):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        monkeypatch.chdir(tmp_path)

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            args = argparse.Namespace(workspace=None, paths=None, port=8765)
            m.cmd_start(args)

        _, call_kwargs = mock_do_start.call_args
        assert call_kwargs.get("workspace_explicit") is False

    def test_cmd_start_threads_code_workspace_file_to_do_start(self, tmp_path):
        """UPG-CLI-STATUS-MODE: starting from a .code-workspace file records
        that file's path so `vectr status` can show it later."""
        folder = tmp_path / "proj"
        folder.mkdir()
        ws_file = tmp_path / "proj.code-workspace"
        ws_file.write_text(json.dumps({"folders": [{"path": "proj"}]}))
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            args = argparse.Namespace(workspace=str(ws_file), paths=None, port=8765)
            m.cmd_start(args)

        _, call_kwargs = mock_do_start.call_args
        assert call_kwargs.get("code_workspace_file") == str(ws_file.resolve())

    def test_cmd_start_plain_directory_has_no_code_workspace_file(self, tmp_path):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            args = argparse.Namespace(workspace=str(tmp_path), paths=None, port=8765)
            m.cmd_start(args)

        _, call_kwargs = mock_do_start.call_args
        assert call_kwargs.get("code_workspace_file") is None


# ---------------------------------------------------------------------------
# Multi-root workspace support
# ---------------------------------------------------------------------------

class TestMultiRoot:
    def test_resolve_single_path(self, tmp_path):
        args = argparse.Namespace(workspace=None, paths=[str(tmp_path)], port=8765)
        roots = m._resolve_workspace_roots(args)
        assert roots == [str(tmp_path)]

    def test_resolve_multiple_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir(); dir_b.mkdir()
        args = argparse.Namespace(workspace=None, paths=[str(dir_a), str(dir_b)], port=8765)
        roots = m._resolve_workspace_roots(args)
        assert roots == [str(dir_a), str(dir_b)]

    def test_resolve_code_workspace_file(self, tmp_path):
        dir_a = tmp_path / "proj_a"
        dir_b = tmp_path / "proj_b"
        dir_a.mkdir(); dir_b.mkdir()
        ws_file = tmp_path / "myworkspace.code-workspace"
        ws_file.write_text(
            f'{{"folders": [{{"path": "{dir_a}"}}, {{"path": "{dir_b}"}}]}}'
        )
        args = argparse.Namespace(workspace=str(ws_file), paths=None, port=8765)
        roots = m._resolve_workspace_roots(args)
        assert str(dir_a) in roots
        assert str(dir_b) in roots
        assert roots.index(str(dir_a)) < roots.index(str(dir_b))

    def test_resolve_code_workspace_relative_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        ws_file = tmp_path / "test.code-workspace"
        ws_file.write_text('{"folders": [{"path": "a"}]}')
        args = argparse.Namespace(workspace=str(ws_file), paths=None, port=8765)
        roots = m._resolve_workspace_roots(args)
        assert str(dir_a) in roots

    def test_parse_code_workspace(self, tmp_path):
        dir_a = tmp_path / "alpha"
        dir_b = tmp_path / "beta"
        dir_a.mkdir(); dir_b.mkdir()
        ws_file = tmp_path / "multi.code-workspace"
        ws_file.write_text(
            f'{{"folders": [{{"path": "{dir_a}"}}, {{"path": "{dir_b}"}}]}}'
        )
        roots = m._parse_code_workspace(str(ws_file))
        assert str(dir_a) in roots
        assert str(dir_b) in roots

    def test_indexer_extra_roots_property(self, tmp_path):
        from agent.indexer import CodeIndexer
        dir_a = tmp_path / "a"; dir_a.mkdir()
        dir_b = tmp_path / "b"; dir_b.mkdir()
        indexer = CodeIndexer(str(dir_a), extra_roots=[str(dir_b)])
        assert len(indexer.all_roots) == 2
        assert indexer.all_roots[0] == dir_a.resolve()
        assert indexer.all_roots[1] == dir_b.resolve()

    def test_indexer_all_roots_default_is_primary_only(self, tmp_path):
        from agent.indexer import CodeIndexer
        indexer = CodeIndexer(str(tmp_path))
        assert indexer.all_roots == [tmp_path.resolve()]

    def test_multi_root_indexes_files_from_all_roots(self, tmp_path):
        from agent.indexer import CodeIndexer
        dir_a = tmp_path / "a"; dir_a.mkdir()
        dir_b = tmp_path / "b"; dir_b.mkdir()
        (dir_a / "mod_a.py").write_text("def func_a(): pass")
        (dir_b / "mod_b.py").write_text("def func_b(): pass")
        indexer = CodeIndexer(str(dir_a), extra_roots=[str(dir_b)])
        files, _ = indexer.index_workspace()
        assert files == 2, f"expected 2 files across both roots, got {files}"
        paths = indexer.indexed_file_paths
        assert any("mod_a" in p for p in paths)
        assert any("mod_b" in p for p in paths)

    def test_cmd_start_multi_path_passes_extra_roots(self, tmp_path):
        dir_a = tmp_path / "a"; dir_a.mkdir()
        dir_b = tmp_path / "b"; dir_b.mkdir()
        ws_a = str(dir_a)
        ws_b = str(dir_b)
        wh = workspace_hash(ws_a)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            m.cmd_start(_make_args(paths=[ws_a, ws_b], port=8765))

        mock_do_start.assert_called_once_with(
            ws_a, 8765, wh, extra_roots=[ws_b], memory_only=False, search_only=False, workspace_explicit=True,
            code_workspace_file=None, host="127.0.0.1", no_ide_config=False,
        )


class TestResolveWorkspaceRootsEnvValidation:
    """UPG-WORKSPACE-ENV-VALIDATE: a typo'd VECTR_WORKSPACE must fail loudly
    (SystemExit, non-zero) rather than silently falling back to cwd
    detection. Only exercised when the caller gave no explicit --path/
    positional workspace arg — that's the fallback branch these tests target.
    """

    def _no_explicit_args(self) -> argparse.Namespace:
        return argparse.Namespace(workspace=None, paths=None, port=8765)

    def test_nonexistent_env_path_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        bad_path = str(tmp_path / "typo-path")
        monkeypatch.setenv("VECTR_WORKSPACE", bad_path)
        with pytest.raises(SystemExit) as excinfo:
            m._resolve_workspace_roots(self._no_explicit_args())
        assert excinfo.value.code != 0
        assert bad_path in capsys.readouterr().err

    def test_unset_env_falls_back_to_cwd_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VECTR_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        roots = m._resolve_workspace_roots(self._no_explicit_args())
        assert roots == [str(tmp_path.resolve())]

    def test_valid_env_path_resolves_unchanged(self, tmp_path, monkeypatch):
        real_dir = tmp_path / "real-workspace"
        real_dir.mkdir()
        monkeypatch.setenv("VECTR_WORKSPACE", str(real_dir))
        roots = m._resolve_workspace_roots(self._no_explicit_args())
        assert roots == [str(real_dir.resolve())]


# ---------------------------------------------------------------------------
# --exclude on `init` and `start` (UPG-EXCLUDE-REGEX)
# ---------------------------------------------------------------------------

class TestExcludeFlag:
    """Same repeatable append-to-.vectrignore semantics on both subcommands:
    a bare directory name, a file glob, or a `re:<pattern>` path regex. An
    invalid `re:` pattern must exit non-zero and write nothing at all."""

    def test_init_writes_plain_dir(self, tmp_path):
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path), exclude=["vendor"]))
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert "vendor" in content

    def test_init_writes_regex_entry(self, tmp_path):
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            m.cmd_init(_make_args(path=str(tmp_path), exclude=["re:legacy/.*"]))
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert "re:legacy/.*" in content

    def test_init_invalid_regex_exits_and_writes_nothing(self, tmp_path):
        # cmd_init seeds a DEFAULT .vectrignore as part of workspace config
        # (unrelated to --exclude) — so "writes nothing" here means the bad
        # --exclude entry itself must never be appended, not that the file
        # can't exist at all.
        with patch("main.InstanceRegistry") as MockReg:
            MockReg.return_value.get.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_init(_make_args(path=str(tmp_path), exclude=["re:(unclosed"]))
        assert exc_info.value.code != 0
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert "unclosed" not in content

    def test_start_writes_regex_entry(self, tmp_path):
        ws = str(tmp_path)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start"):
            m.cmd_start(_make_args(paths=[ws], port=8765, exclude=["re:legacy/.*"]))
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert "re:legacy/.*" in content

    def test_start_writes_plain_dir_unchanged(self, tmp_path):
        ws = str(tmp_path)
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._is_pid_alive", return_value=False), \
             patch("main._is_pid_alive", return_value=False), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._write_workspace_config"), \
             patch("main._do_start"):
            m.cmd_start(_make_args(paths=[ws], port=8765, exclude=["vendor"]))
        content = (tmp_path / ".vectrignore").read_text(encoding="utf-8")
        assert "vendor" in content

    def test_start_invalid_regex_exits_before_any_registry_access(self, tmp_path):
        ws = str(tmp_path)
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._do_start") as mock_do_start:
            with pytest.raises(SystemExit) as exc_info:
                m.cmd_start(_make_args(paths=[ws], port=8765, exclude=["re:[unclosed"]))
        assert exc_info.value.code != 0
        assert not (tmp_path / ".vectrignore").exists()
        MockReg.assert_not_called()
        mock_do_start.assert_not_called()


class TestTopLevelDescription:
    """UPG-CLI-DESC: `vectr -h` must describe BOTH capabilities (search AND
    working memory), not just "codebase indexer" — the prior wording gave
    no indication the CLI/MCP surface has a memory half at all."""

    def test_help_mentions_both_search_and_memory(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["vectr", "--help"]):
                m.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        header = out.split("positional arguments")[0]
        assert "memory" in header.lower()
        assert "search" in header.lower()
        # Editor-agnostic: no product/editor names in the top-level description.
        assert "Claude" not in header

    def test_recall_help_no_longer_says_for_hooks_only(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["vectr", "--help"]):
                m.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "recall" in out
        assert "for hooks" not in out

    def test_fastapi_app_description_mentions_both_capabilities(self) -> None:
        import api
        assert "memory" in api.app.description.lower()
        assert "search" in api.app.description.lower()


# ---------------------------------------------------------------------------
# Authentication: key generation + editor-config header emission
# ---------------------------------------------------------------------------

class TestMcpHeaderInjection:
    """Unit tests for the header-injection helpers used by both the local
    authenticated-config path and `vectr connect`."""

    def test_no_headers_returns_config_unchanged(self) -> None:
        original = m._MCP_JSON.format(port=8765)
        assert m._inject_mcp_headers(original, {}) == original

    def test_headers_added_to_mcpservers_entry(self) -> None:
        out = m._inject_mcp_headers(m._MCP_JSON.format(port=8765), {"X-Api-Key": "k"})
        data = json.loads(out)
        assert data["mcpServers"]["vectr"]["headers"] == {"X-Api-Key": "k"}
        assert data["mcpServers"]["vectr"]["url"].endswith(":8765/mcp")

    def test_headers_added_to_servers_entry_vscode(self) -> None:
        out = m._inject_mcp_headers(m._VSCODE_MCP_JSON.format(port=8765), {"X-Api-Key": "k"})
        data = json.loads(out)
        assert data["servers"]["vectr"]["headers"] == {"X-Api-Key": "k"}

    def test_auth_headers_builder(self) -> None:
        assert m._mcp_auth_headers() == {}
        assert m._mcp_auth_headers(api_key="k") == {"X-Api-Key": "k"}
        assert m._mcp_auth_headers(api_key="k", client_label="alice") == {
            "X-Api-Key": "k",
            "X-Vectr-Client": "alice",
        }


class TestAuthConfigWriters:
    """When VECTR_API_KEY is set, the local editor MCP configs must carry the
    key header so the editor can still reach its own authenticated daemon."""

    def test_no_key_writes_header_free_config(self, tmp_path) -> None:
        with patch.dict(os.environ, {"VECTR_API_KEY": ""}, clear=False):
            m._write_workspace_config(str(tmp_path), 8765)
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        assert "headers" not in mcp["mcpServers"]["vectr"]

    def test_key_set_emits_header_in_all_three_configs(self, tmp_path) -> None:
        with patch.dict(os.environ, {"VECTR_API_KEY": "team-secret"}, clear=False):
            m._write_workspace_config(str(tmp_path), 8765)
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        vscode = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert mcp["mcpServers"]["vectr"]["headers"]["X-Api-Key"] == "team-secret"
        assert cursor["mcpServers"]["vectr"]["headers"]["X-Api-Key"] == "team-secret"
        assert vscode["servers"]["vectr"]["headers"]["X-Api-Key"] == "team-secret"


class TestBindGuard:
    """Non-loopback binds require authentication (team / central instance)."""

    def test_is_loopback_host(self) -> None:
        assert m._is_loopback_host("127.0.0.1")
        assert m._is_loopback_host("localhost")
        assert m._is_loopback_host("::1")
        assert m._is_loopback_host("  LOCALHOST  ")
        assert not m._is_loopback_host("0.0.0.0")
        assert not m._is_loopback_host("192.168.1.10")

    def test_enforce_blocks_nonloopback_without_key(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("VECTR_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc:
            m._enforce_bind_auth("0.0.0.0")
        assert exc.value.code == 1
        assert "refusing to bind" in capsys.readouterr().err.lower()

    def test_enforce_allows_nonloopback_with_key(self, monkeypatch) -> None:
        monkeypatch.setenv("VECTR_API_KEY", "k")
        m._enforce_bind_auth("0.0.0.0")  # no SystemExit

    def test_enforce_allows_loopback_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("VECTR_API_KEY", raising=False)
        m._enforce_bind_auth("127.0.0.1")
        m._enforce_bind_auth("localhost")  # no SystemExit

    def test_cmd_start_refuses_nonloopback_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("VECTR_API_KEY", raising=False)
        args = argparse.Namespace(memory_only=False, search_only=False, host="0.0.0.0")
        with pytest.raises(SystemExit) as exc:
            m.cmd_start(args)
        assert exc.value.code == 1

    def test_do_start_binds_requested_host(self, tmp_path, monkeypatch) -> None:
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kwargs) -> None:
                captured["cmd"] = cmd
                self.pid = 4321

        monkeypatch.setattr(m.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(m, "_wait_for_daemon_ready", lambda port, pid: True)
        monkeypatch.setattr(m, "_migrate_legacy_files", lambda: None)
        monkeypatch.setattr(m, "InstanceRegistry", lambda: MagicMock())

        m._do_start(str(tmp_path), 18999, "hash123", host="0.0.0.0")
        cmd = captured["cmd"]
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"


class TestCmdConnect:
    """`vectr connect` writes remote MCP config with headers, spawns nothing."""

    def test_normalize_mcp_url(self) -> None:
        assert m._normalize_mcp_url("http://h:8765") == "http://h:8765/mcp"
        assert m._normalize_mcp_url("http://h:8765/") == "http://h:8765/mcp"
        assert m._normalize_mcp_url("http://h:8765/mcp") == "http://h:8765/mcp"
        assert m._normalize_mcp_url("http://h:8765/mcp/") == "http://h:8765/mcp"

    def test_connect_writes_remote_configs_with_headers(self, tmp_path) -> None:
        args = argparse.Namespace(
            url="http://central:8765", api_key="team-key", label="alice", path=str(tmp_path),
        )
        m.cmd_connect(args)
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        entry = mcp["mcpServers"]["vectr"]
        assert entry["url"] == "http://central:8765/mcp"
        assert entry["headers"]["X-Api-Key"] == "team-key"
        assert entry["headers"]["X-Vectr-Client"] == "alice"
        vscode = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert vscode["servers"]["vectr"]["headers"]["X-Api-Key"] == "team-key"
        cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert cursor["mcpServers"]["vectr"]["url"] == "http://central:8765/mcp"

    def test_connect_api_key_falls_back_to_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("VECTR_API_KEY", "env-key")
        args = argparse.Namespace(
            url="http://central:8765/mcp", api_key="", label="", path=str(tmp_path),
        )
        m.cmd_connect(args)
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        assert mcp["mcpServers"]["vectr"]["headers"]["X-Api-Key"] == "env-key"

    def test_connect_no_label_writes_no_client_header(self, tmp_path) -> None:
        args = argparse.Namespace(
            url="http://central:8765", api_key="k", label="", path=str(tmp_path),
        )
        m.cmd_connect(args)
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        assert "X-Vectr-Client" not in mcp["mcpServers"]["vectr"]["headers"]

    def test_connect_does_not_spawn_daemon(self, tmp_path, monkeypatch) -> None:
        spawned = {"popen": False}
        monkeypatch.setattr(
            m.subprocess, "Popen",
            lambda *a, **k: spawned.__setitem__("popen", True),
        )
        args = argparse.Namespace(
            url="http://central:8765", api_key="k", label="", path=str(tmp_path),
        )
        m.cmd_connect(args)
        assert spawned["popen"] is False

    def test_connect_writes_claude_md_guidance(self, tmp_path) -> None:
        args = argparse.Namespace(
            url="http://central:8765", api_key="k", label="", path=str(tmp_path),
        )
        m.cmd_connect(args)
        assert (tmp_path / "CLAUDE.md").exists()


class TestCmdKey:
    def test_key_command_prints_urlsafe_token_to_stdout(self, capsys) -> None:
        args = argparse.Namespace()
        m.cmd_key(args)
        captured = capsys.readouterr()
        key = captured.out.strip()
        # secrets.token_urlsafe(32) → ~43 URL-safe chars, one line on stdout.
        assert len(key) >= 40
        assert "\n" not in key
        assert all(c.isalnum() or c in "-_" for c in key)
        # Usage guidance goes to stderr, never the key generator's stdout.
        assert "vectr connect" in captured.err

    def test_key_command_generates_distinct_keys(self, capsys) -> None:
        m.cmd_key(argparse.Namespace())
        first = capsys.readouterr().out.strip()
        m.cmd_key(argparse.Namespace())
        second = capsys.readouterr().out.strip()
        assert first != second

    def test_key_never_starts_with_dash(self, capsys, monkeypatch) -> None:
        # A leading '-' makes `--api-key <key>` parse as a flag; cmd_key must
        # regenerate until the first character is safe.
        import secrets

        vals = iter(["-Ld0PGVoOJdtIPCtvsRBQVfMEHzSY1FJ6uk3Q9y1AbM",
                     "sAfEkEy0OJdtIPCtvsRBQVfMEHzSY1FJ6uk3Q9y1AbM"])
        monkeypatch.setattr(secrets, "token_urlsafe", lambda n: next(vals))
        m.cmd_key(argparse.Namespace())
        key = capsys.readouterr().out.strip()
        assert not key.startswith("-")
        assert key == "sAfEkEy0OJdtIPCtvsRBQVfMEHzSY1FJ6uk3Q9y1AbM"
