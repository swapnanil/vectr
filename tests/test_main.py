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

        mock_do_start.assert_called_once_with(ws, 8765, wh, extra_roots=[], memory_only=False)

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
        assert ".claude" in content  # explicitly steers off the built-in memory dir

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

    def test_daemon_down_is_silent_and_exits_zero(self, tmp_path, capsys):
        """Recall feeds hook-injected context; a down daemon must never break the session."""
        import argparse
        import httpx
        from unittest.mock import patch

        with patch("main.InstanceRegistry") as MockReg, \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            MockReg.return_value.get.return_value = None
            args = argparse.Namespace(query="lock flow", tags=None, priority=None,
                                      limit=10, path=str(tmp_path), port=8765)
            # No SystemExit raised → exit 0.
            m.cmd_recall(args)

        assert capsys.readouterr().out == ""


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

    def test_uses_boot_payload(self, monkeypatch, capsys):
        """The SessionStart hook must request the unconditional boot set."""
        import argparse
        from unittest.mock import patch
        monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/project/a"}'))
        with patch("main.InstanceRegistry") as MockReg, \
             patch("main._fetch_recall", return_value="x") as mock_fetch:
            MockReg.return_value.get.return_value = {"port": 8765}
            m.cmd_hook(argparse.Namespace(hook_event="session-start"))
        assert mock_fetch.call_args[0][1] == {"boot": True}

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

    def test_no_file_path_injects_nothing(self, monkeypatch, capsys):
        out, mock_fetch = self._run('{"cwd": "/p", "tool_name": "Bash", "tool_input": {}}', "x", monkeypatch, capsys)
        mock_fetch.assert_not_called()
        assert out.strip() == ""

    def test_unrelated_file_no_gotcha_injects_nothing(self, monkeypatch, capsys):
        stdin = '{"cwd": "/p", "tool_input": {"file_path": "/p/README.md"}}'
        out, _ = self._run(stdin, "", monkeypatch, capsys)
        assert out.strip() == ""


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

    def test_writes_pretooluse_hook_with_edit_write_matcher(self, tmp_path):
        m._write_claude_hooks(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        groups = data["hooks"]["PreToolUse"]
        assert len(groups) == 1
        assert groups[0]["matcher"] == "Edit|Write"
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

    def test_search_section_lists_all_five_tools(self, tmp_path):
        block = self._vectr_block(tmp_path)
        for tool in ("vectr_search", "vectr_locate", "vectr_trace", "vectr_map", "vectr_map_save"):
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

        mock_do_start.assert_called_once_with(ws_a, 8765, wh, extra_roots=[ws_b], memory_only=False)
