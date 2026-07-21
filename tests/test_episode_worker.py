"""Tests for agent/episode_worker.py (memoization-l1-capture-design §2).

The detached PostToolUse worker: reads its argv[1] payload file, deletes it,
truncates stdout/stderr to the config cap, POSTs to the daemon, and never
raises regardless of failure mode — the hook's foreground process already
returned by the time this runs, so nothing is watching this process's exit
status or output."""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

from agent import episode_worker
from agent.config import EPISODES_CLIENT_TRUNCATE_CHARS


def _write_envelope(tmp_path, port, payload) -> str:
    path = tmp_path / "vectr-episode-test.json"
    path.write_text(json.dumps({"port": port, "payload": payload}))
    return str(path)


class TestPayloadFileHandling:
    def test_reads_envelope_deletes_file_and_posts(self, tmp_path, monkeypatch):
        path = _write_envelope(tmp_path, 8765, {"tool": "bash", "command": "ls",
                                                 "stdout_tail": "ok", "stderr_tail": ""})
        monkeypatch.setattr(sys, "argv", ["episode_worker.py", path])
        with patch("httpx.post") as mock_post:
            episode_worker.main()
        assert not (tmp_path / "vectr-episode-test.json").exists()
        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
        assert url == "http://localhost:8765/v1/episode"
        assert kwargs["json"]["command"] == "ls"

    def test_no_argv_path_is_a_silent_noop(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["episode_worker.py"])
        with patch("httpx.post") as mock_post:
            episode_worker.main()  # must not raise
        mock_post.assert_not_called()

    def test_missing_file_is_a_silent_noop(self, tmp_path):
        with patch("sys.argv", ["episode_worker.py", str(tmp_path / "does-not-exist.json")]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()  # must not raise
            mock_post.assert_not_called()

    def test_malformed_json_file_is_a_silent_noop_and_still_deletes(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        with patch("sys.argv", ["episode_worker.py", str(path)]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
            mock_post.assert_not_called()
        assert not path.exists()

    def test_non_dict_envelope_is_a_silent_noop(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text(json.dumps([1, 2, 3]))
        with patch("sys.argv", ["episode_worker.py", str(path)]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
            mock_post.assert_not_called()

    def test_missing_port_or_payload_is_a_silent_noop(self, tmp_path):
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps({"port": 8765}))  # no "payload"
        with patch("sys.argv", ["episode_worker.py", str(path)]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
            mock_post.assert_not_called()

    def test_non_int_port_is_a_silent_noop(self, tmp_path):
        path = _write_envelope(tmp_path, "not-a-port", {"tool": "bash"})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
            mock_post.assert_not_called()


class TestTruncation:
    def test_stdout_and_stderr_truncated_to_config_cap(self, tmp_path):
        long_text = "x" * (EPISODES_CLIENT_TRUNCATE_CHARS + 500)
        path = _write_envelope(tmp_path, 8765, {"tool": "bash", "stdout_tail": long_text,
                                                 "stderr_tail": long_text})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
        posted = mock_post.call_args.kwargs["json"]
        assert len(posted["stdout_tail"]) == EPISODES_CLIENT_TRUNCATE_CHARS
        assert len(posted["stderr_tail"]) == EPISODES_CLIENT_TRUNCATE_CHARS

    def test_under_cap_text_is_untouched(self, tmp_path):
        path = _write_envelope(tmp_path, 8765, {"tool": "bash", "stdout_tail": "short", "stderr_tail": ""})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
        posted = mock_post.call_args.kwargs["json"]
        assert posted["stdout_tail"] == "short"


class TestPostFailureNeverRaises:
    def test_connection_error_is_swallowed(self, tmp_path):
        path = _write_envelope(tmp_path, 8765, {"tool": "bash"})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post", side_effect=OSError("connection refused")):
                episode_worker.main()  # must not raise

    def test_timeout_is_swallowed(self, tmp_path):
        import httpx
        path = _write_envelope(tmp_path, 8765, {"tool": "bash"})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
                episode_worker.main()  # must not raise
