"""Tests for agent/episode_worker.py (memoization-l1-capture-design §2).

The detached PostToolUse worker: reads its argv[1] payload file, deletes it,
truncates stdout/stderr to the config cap, POSTs to the daemon, and never
raises regardless of failure mode — the hook's foreground process already
returned by the time this runs, so nothing is watching this process's exit
status or output."""
from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import patch

from agent import episode_worker
from agent.config import EPISODES_CLIENT_TRUNCATE_CHARS, EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S


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

    def test_truncation_keeps_the_tail_not_the_head(self, tmp_path):
        """Adversarial-review B1 follow-on fix: a failure marker near the
        end of a long output must survive truncation. `value[:CAP]` (head)
        would keep HEAD-MARKER and drop TAIL-MARKER; the correct
        `value[-CAP:]` (tail) does the opposite."""
        long_text = "HEAD-MARKER" + ("x" * (EPISODES_CLIENT_TRUNCATE_CHARS + 500)) + "TAIL-MARKER"
        path = _write_envelope(tmp_path, 8765, {"tool": "bash", "stdout_tail": long_text, "stderr_tail": ""})
        with patch("sys.argv", ["episode_worker.py", path]):
            with patch("httpx.post") as mock_post:
                episode_worker.main()
        posted = mock_post.call_args.kwargs["json"]
        assert posted["stdout_tail"].endswith("TAIL-MARKER")
        assert "HEAD-MARKER" not in posted["stdout_tail"]


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


class TestStaleTempFileSweep:
    """Adversarial-review LOW item: `_spawn_episode_worker` (main.py /
    agent/hook_cli.py) writes its payload temp file BEFORE attempting
    `subprocess.Popen`, inside a blanket try/except — if the spawn itself
    fails, no worker process ever runs to hit this module's own
    `finally: os.remove(payload_path)`, orphaning the file. This sweep is
    the only thing that ever cleans those up, so it must reliably remove
    stale ones and never touch fresh/still-in-flight ones."""

    def _age_file(self, path, age_s: float) -> None:
        stale_time = time.time() - age_s
        os.utime(path, (stale_time, stale_time))

    def test_stale_orphan_is_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        orphan = tmp_path / "vectr-episode-orphan123.json"
        orphan.write_text("{}")
        self._age_file(orphan, EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S + 60)

        episode_worker._sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)

        assert not orphan.exists()

    def test_fresh_file_is_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        fresh = tmp_path / "vectr-episode-fresh456.json"
        fresh.write_text("{}")  # mtime = now, well under the sweep age

        episode_worker._sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)

        assert fresh.exists()

    def test_non_matching_filename_is_never_touched(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        unrelated = tmp_path / "some-other-file.json"
        unrelated.write_text("{}")
        self._age_file(unrelated, EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S + 60)

        episode_worker._sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)

        assert unrelated.exists()

    def test_permission_error_on_remove_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        orphan = tmp_path / "vectr-episode-locked789.json"
        orphan.write_text("{}")
        self._age_file(orphan, EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S + 60)
        with patch("os.remove", side_effect=OSError("permission denied")):
            episode_worker._sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)
        # must not raise; file remains since the (mocked) remove failed

    def test_glob_failure_is_swallowed(self, monkeypatch):
        with patch("glob.glob", side_effect=OSError("boom")):
            episode_worker._sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)  # must not raise

    def test_main_invokes_sweep_alongside_normal_payload_handling(self, tmp_path, monkeypatch):
        """The sweep runs as a side effect of a normal worker invocation
        (main.py's own detached process), not on a separate schedule."""
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        orphan = tmp_path / "vectr-episode-other-worker.json"
        orphan.write_text("{}")
        self._age_file(orphan, EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S + 60)

        payload_path = _write_envelope(tmp_path, 8765, {"tool": "bash", "command": "ls"})
        with patch("sys.argv", ["episode_worker.py", payload_path]):
            with patch("httpx.post"):
                episode_worker.main()

        assert not orphan.exists()
