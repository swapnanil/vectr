"""Unit tests for `main.cmd_hook`'s "post-tool-use" branch (L1 episode
capture, memoization-l1-capture-design §2): `_build_episode_payload` and
`_spawn_episode_worker`.

This branch never prints to stdout, never awaits the daemon write, and must
never raise regardless of malformed input, a missing daemon, or a spawn
failure — the same "hook safety: never propagate" contract every other
`cmd_hook` branch already has (main.py's own try/except wraps all of them)."""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
from unittest.mock import patch

import main as m


def _registry_stub(monkeypatch, entry: dict | None):
    class _FakeRegistry:
        def get(self, _key):
            return entry

    monkeypatch.setattr(m, "InstanceRegistry", lambda *a, **k: _FakeRegistry())


class TestBuildEpisodePayload:
    def test_bash_tool_captures_command_description_and_output(self):
        event = {
            "session_id": "abc-123", "cwd": "/repo", "tool_name": "Bash",
            "tool_input": {"command": 'git commit -m "fix"', "description": "commit"},
            "tool_response": {"stdout": "ok\n", "stderr": "", "is_error": False, "exit_code": 0},
        }
        payload = m._build_episode_payload(event)
        assert payload == {
            "session_id": "abc-123", "cwd": "/repo", "tool": "bash",
            "command": 'git commit -m "fix"', "description": "commit", "file_path": None,
            "rc": 0, "is_error": False, "interrupted": False,
            "stdout_tail": "ok\n", "stderr_tail": "",
        }

    def test_edit_tool_captures_file_path_only_never_content(self):
        event = {
            "cwd": "/repo", "tool_name": "Edit",
            "tool_input": {
                "file_path": "/repo/a.py",
                "old_string": "ZzQUARANTINEOLDzZ",
                "new_string": "ZzQUARANTINENEWzZ",
            },
            "tool_response": {},
        }
        payload = m._build_episode_payload(event)
        assert payload["tool"] == "edit"
        assert payload["file_path"] == "/repo/a.py"
        assert payload["command"] is None
        assert payload["description"] is None
        # old_string/new_string (file content) never leak into the payload.
        assert "old_string" not in payload
        assert "new_string" not in payload
        dumped = json.dumps(payload)
        assert "ZzQUARANTINEOLDzZ" not in dumped
        assert "ZzQUARANTINENEWzZ" not in dumped

    def test_write_and_multiedit_tools_also_captured_as_edit(self):
        for tool_name in ("Write", "MultiEdit"):
            event = {"cwd": "/repo", "tool_name": tool_name, "tool_input": {"file_path": "/repo/b.py"}}
            payload = m._build_episode_payload(event)
            assert payload["tool"] == "edit"
            assert payload["file_path"] == "/repo/b.py"

    def test_uncaptured_tool_name_returns_none(self):
        event = {"cwd": "/repo", "tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}}
        assert m._build_episode_payload(event) is None

    def test_missing_tool_name_returns_none(self):
        assert m._build_episode_payload({"cwd": "/repo"}) is None

    def test_returncode_key_used_when_exit_code_absent(self):
        event = {
            "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "ls"},
            "tool_response": {"returncode": 1},
        }
        payload = m._build_episode_payload(event)
        assert payload["rc"] == 1

    def test_non_int_rc_is_dropped_to_none(self):
        event = {
            "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "ls"},
            "tool_response": {"exit_code": "not-an-int"},
        }
        payload = m._build_episode_payload(event)
        assert payload["rc"] is None

    def test_non_string_stdout_stderr_coerced_to_text_not_raise(self):
        event = {
            "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "ls"},
            "tool_response": {"stdout": 12345, "stderr": None},
        }
        payload = m._build_episode_payload(event)
        assert payload["stdout_tail"] == "12345"
        assert payload["stderr_tail"] == ""

    def test_malformed_tool_input_and_tool_response_shapes_do_not_raise(self):
        event = {
            "cwd": "/repo", "tool_name": "Bash",
            "tool_input": "not-a-dict", "tool_response": ["also", "not-a-dict"],
        }
        payload = m._build_episode_payload(event)
        assert payload["command"] is None
        assert payload["rc"] is None

    def test_absent_session_id_yields_none_not_empty_string(self):
        event = {"cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        payload = m._build_episode_payload(event)
        assert payload["session_id"] is None


class TestSpawnEpisodeWorker:
    def test_writes_temp_file_and_spawns_detached_child(self, monkeypatch, tmp_path):
        captured = {}

        def _fake_mkstemp(prefix="", suffix=""):
            path = tmp_path / f"{prefix}x{suffix}"
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
            return fd, str(path)

        monkeypatch.setattr(tempfile, "mkstemp", _fake_mkstemp)

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            class _P:
                pass
            return _P()

        with patch("subprocess.Popen", side_effect=_fake_popen):
            m._spawn_episode_worker(8765, {"tool": "bash", "command": "ls"})

        assert captured["cmd"][1:3] == ["-m", "agent.episode_worker"]
        written_path = captured["cmd"][3]
        with open(written_path, encoding="utf-8") as f:
            envelope = json.load(f)
        assert envelope == {"port": 8765, "payload": {"tool": "bash", "command": "ls"}}
        assert captured["kwargs"]["start_new_session"] is True
        assert captured["kwargs"]["stdin"] == m.subprocess.DEVNULL

    def test_popen_failure_is_swallowed_never_raises(self):
        with patch("subprocess.Popen", side_effect=OSError("boom")):
            m._spawn_episode_worker(8765, {"tool": "bash"})  # must not raise


class TestCmdHookPostToolUseBranch:
    def test_never_prints_to_stdout_on_captured_bash_tool(self, monkeypatch, capsys):
        _registry_stub(monkeypatch, {"port": 8765})
        stdin_json = json.dumps({
            "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "echo hi"},
            "tool_response": {"stdout": "hi\n", "exit_code": 0},
        })
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch.object(m, "_spawn_episode_worker") as spawn:
            m.cmd_hook(argparse.Namespace(hook_event="post-tool-use"))
        assert capsys.readouterr().out == ""
        spawn.assert_called_once()
        port_arg, payload_arg = spawn.call_args.args
        assert port_arg == 8765
        assert payload_arg["command"] == "echo hi"

    def test_no_daemon_registered_never_spawns_never_raises(self, monkeypatch, capsys):
        _registry_stub(monkeypatch, None)
        stdin_json = json.dumps({"cwd": "/nowhere", "tool_name": "Bash", "tool_input": {"command": "echo hi"}})
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch.object(m, "_spawn_episode_worker") as spawn:
            m.cmd_hook(argparse.Namespace(hook_event="post-tool-use"))
        assert capsys.readouterr().out == ""
        spawn.assert_not_called()

    def test_uncaptured_tool_name_never_spawns(self, monkeypatch, capsys):
        _registry_stub(monkeypatch, {"port": 8765})
        stdin_json = json.dumps({"cwd": "/repo", "tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}})
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch.object(m, "_spawn_episode_worker") as spawn:
            m.cmd_hook(argparse.Namespace(hook_event="post-tool-use"))
        assert capsys.readouterr().out == ""
        spawn.assert_not_called()

    def test_malformed_stdin_json_never_raises_exits_cleanly(self, monkeypatch, capsys):
        _registry_stub(monkeypatch, {"port": 8765})
        monkeypatch.setattr("sys.stdin", io.StringIO("{not valid json"))
        # _read_hook_stdin swallows the parse error and returns {}; cwd falls
        # back to os.getcwd(), which may or may not resolve to a registered
        # workspace — either way, nothing must raise or print.
        m.cmd_hook(argparse.Namespace(hook_event="post-tool-use"))
        assert capsys.readouterr().out == ""

    def test_spawn_raising_is_swallowed_by_cmd_hook(self, monkeypatch, capsys):
        _registry_stub(monkeypatch, {"port": 8765})
        stdin_json = json.dumps({"cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "echo hi"}})
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        with patch.object(m, "_spawn_episode_worker", side_effect=RuntimeError("boom")):
            m.cmd_hook(argparse.Namespace(hook_event="post-tool-use"))  # must not raise
        assert capsys.readouterr().out == ""
