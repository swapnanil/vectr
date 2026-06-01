"""Tests for main.py CLI commands (multi-instance registry integration)."""
from __future__ import annotations

import argparse
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
        "path": "/project/a",
        "port": 8765,
        "all": False,
        "force": False,
        "query": None,
        "n": 10,
        "language": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _registry_with(tmp_path, entries: dict) -> InstanceRegistry:
    reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
    for ws_hash_key, entry in entries.items():
        reg.register(ws_hash_key, entry["workspace"], entry["port"], entry["pid"])
    return reg


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
            m.cmd_start(_make_args(path=ws, port=8765))

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
            m.cmd_start(_make_args(path=ws, port=8765))

        mock_do_start.assert_called_once_with(ws, 8765, wh)

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
            m.cmd_start(_make_args(path=ws, port=8765))

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
            m.cmd_restart(_make_args(path=ws, port=8765))

        mock_stop.assert_called_once_with(12345)
        mock_do_start.assert_called_once()

    def test_restart_with_no_existing_entry_just_starts(self, tmp_path):
        reg = InstanceRegistry(registry_path=tmp_path / "instances.json")

        with patch("main.InstanceRegistry", return_value=reg), \
             patch("agent.instance_registry._port_is_free", return_value=True), \
             patch("main._stop_server") as mock_stop, \
             patch("main._write_workspace_config"), \
             patch("main._do_start") as mock_do_start:
            m.cmd_restart(_make_args(path="/project/a", port=8765))

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
        assert "actual code block" in content or "real code" in content or "code block" in content, (
            "CLAUDE.md must instruct agent to store code blocks, not prose descriptions"
        )
        assert "re-read" in content or "re-reading" in content, (
            "CLAUDE.md must explain that code notes prevent re-reading files"
        )

    def test_claude_md_has_recall_usage_guidance(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "fill gaps" in content or "directly" in content, (
            "CLAUDE.md must tell agent to work from recalled notes directly, use search only to fill gaps"
        )

    def test_claude_md_not_overwritten_if_present(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("custom")
        m._write_workspace_config(str(tmp_path), 8765)
        assert (tmp_path / "CLAUDE.md").read_text() == "custom"

    def test_settings_json_created_if_missing(self, tmp_path):
        m._write_workspace_config(str(tmp_path), 8765)
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.exists()
        import json
        data = json.loads(settings.read_text())
        assert data.get("enableAllProjectMcpServers") is True


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
