"""Tests for agent/watcher.py — _DebounceTimer and CodeWatcher."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from agent.watcher import CodeWatcher, _DebounceTimer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_event(src_path: str, is_directory: bool = False):
    event = MagicMock()
    event.src_path = src_path
    event.is_directory = is_directory
    return event


def _mock_move_event(src_path: str, dest_path: str, is_directory: bool = False):
    event = MagicMock()
    event.src_path = src_path
    event.dest_path = dest_path
    event.is_directory = is_directory
    return event


def _mock_indexer(workspace_root: str = "/workspace"):
    from pathlib import Path
    indexer = MagicMock()
    indexer.workspace_root = workspace_root
    indexer.all_roots = [Path(workspace_root)]
    return indexer


# ---------------------------------------------------------------------------
# _DebounceTimer
# ---------------------------------------------------------------------------

class TestDebounceTimer:
    def test_fires_after_silence(self):
        fired = []
        timer = _DebounceTimer(0.05, lambda key, *a: fired.append(key))
        timer.schedule("file.py", "modify")
        time.sleep(0.15)
        assert "file.py" in fired

    def test_resets_on_repeat_call(self):
        call_times = []
        timer = _DebounceTimer(0.05, lambda key, *a: call_times.append(time.monotonic()))
        timer.schedule("file.py", "modify")
        time.sleep(0.02)
        timer.schedule("file.py", "modify")  # reset
        time.sleep(0.15)
        # Should only fire once (the second schedule)
        assert len(call_times) == 1

    def test_cancel_all_prevents_fire(self):
        fired = []
        timer = _DebounceTimer(0.05, lambda key, *a: fired.append(key))
        timer.schedule("file.py", "modify")
        timer.cancel_all()
        time.sleep(0.15)
        assert fired == []

    def test_different_keys_fire_independently(self):
        fired = []
        timer = _DebounceTimer(0.05, lambda key, *a: fired.append(key))
        timer.schedule("a.py", "modify")
        timer.schedule("b.py", "modify")
        time.sleep(0.15)
        assert "a.py" in fired
        assert "b.py" in fired

    def test_cancel_all_clears_pending(self):
        timer = _DebounceTimer(0.5, lambda *a: None)
        timer.schedule("x.py", "modify")
        assert len(timer._pending) == 1
        timer.cancel_all()
        assert len(timer._pending) == 0

    def test_args_passed_to_callback(self):
        received = []
        timer = _DebounceTimer(0.05, lambda key, action: received.append((key, action)))
        timer.schedule("main.py", "create")
        time.sleep(0.15)
        assert ("main.py", "create") in received


# ---------------------------------------------------------------------------
# CodeWatcher — _is_indexable
# ---------------------------------------------------------------------------

class TestIsIndexable:
    @pytest.fixture
    def watcher(self):
        return CodeWatcher(_mock_indexer())

    @pytest.mark.parametrize("path", [
        "/ws/src/main.py",
        "/ws/app.ts",
        "/ws/index.js",
        "/ws/server.go",
        "/ws/lib.rs",
        "/ws/Service.java",
    ])
    def test_known_code_extensions_indexable(self, watcher, path):
        assert watcher._is_indexable(path) is True

    @pytest.mark.parametrize("path", [
        "/ws/image.png",
        "/ws/data.csv",
        "/ws/notes.txt",
        "/ws/archive.zip",
    ])
    def test_non_code_extensions_not_indexable(self, watcher, path):
        assert watcher._is_indexable(path) is False

    @pytest.mark.parametrize("path", [
        "/ws/README.md",
        "/ws/docs/guide.md",
        "/ws/index.html",
    ])
    def test_markdown_and_html_are_indexable(self, watcher, path):
        assert watcher._is_indexable(path) is True

    def test_no_extension_not_indexable(self, watcher):
        assert watcher._is_indexable("/ws/Makefile") is False


# ---------------------------------------------------------------------------
# CodeWatcher — event handlers
# ---------------------------------------------------------------------------

class TestOnModified:
    def test_indexable_file_schedules_debounce(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event("/ws/main.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/main.py", "modify")

    def test_directory_event_ignored(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event("/ws/src", is_directory=True))
        watcher._debounce.schedule.assert_not_called()

    def test_non_indexable_file_ignored(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event("/ws/data.csv"))
        watcher._debounce.schedule.assert_not_called()


class TestOnCreated:
    def test_indexable_file_schedules_debounce(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_created(_mock_event("/ws/new_module.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/new_module.py", "create")

    def test_directory_event_ignored(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_created(_mock_event("/ws/newdir", is_directory=True))
        watcher._debounce.schedule.assert_not_called()

    def test_non_indexable_file_ignored(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_created(_mock_event("/ws/config.yaml"))
        watcher._debounce.schedule.assert_not_called()


class TestOnDeleted:
    def test_indexable_file_calls_delete_immediately(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher.on_deleted(_mock_event("/ws/old.py"))
        indexer.delete_file.assert_called_once_with("/ws/old.py")

    def test_delete_calls_searcher_refresh(self):
        refresh = MagicMock()
        watcher = CodeWatcher(_mock_indexer(), searcher_refresh_fn=refresh)
        watcher.on_deleted(_mock_event("/ws/old.py"))
        refresh.assert_called_once()

    def test_delete_no_refresh_fn_no_error(self):
        watcher = CodeWatcher(_mock_indexer(), searcher_refresh_fn=None)
        watcher.on_deleted(_mock_event("/ws/old.py"))  # should not raise

    def test_delete_directory_still_calls_delete(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        # Directories with indexable paths won't match _is_indexable since they
        # have no extension — but if the event has a .py path and is_directory=True,
        # the guard prevents calling delete.
        watcher.on_deleted(_mock_event("/ws/src", is_directory=True))
        indexer.delete_file.assert_not_called()

    def test_non_indexable_file_not_deleted(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher.on_deleted(_mock_event("/ws/data.csv"))
        indexer.delete_file.assert_not_called()


class TestOnMoved:
    def test_rename_into_place_indexes_dest(self):
        # The case that mattered: atomic save / Write tool creates a file via
        # rename, so the new path must be scheduled for indexing.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/new_module.py", "/ws/dest.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/dest.py", "move")

    def test_temp_rename_indexes_only_dest(self):
        # Editor atomic save: temp file (non-indexable) renamed onto real .py
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/.main.py.swp.tmp", "/ws/main.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/main.py", "move")
        indexer.delete_file.assert_not_called()  # temp src was never indexed

    def test_rename_removes_old_indexable_src(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/old.py", "/ws/renamed.py"))
        indexer.delete_file.assert_called_once_with("/ws/old.py")
        watcher._debounce.schedule.assert_called_once_with("/ws/renamed.py", "move")

    def test_src_delete_triggers_searcher_refresh(self):
        refresh = MagicMock()
        watcher = CodeWatcher(_mock_indexer(), searcher_refresh_fn=refresh)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/old.py", "/ws/new.py"))
        refresh.assert_called_once()

    def test_non_indexable_dest_not_scheduled(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/page.py", "/ws/data.csv"))
        watcher._debounce.schedule.assert_not_called()

    def test_directory_move_ignored(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/src", "/ws/src2", is_directory=True))
        watcher._debounce.schedule.assert_not_called()
        indexer.delete_file.assert_not_called()


# ---------------------------------------------------------------------------
# CodeWatcher — _handle_change
# ---------------------------------------------------------------------------

class TestHandleChange:
    def test_handle_change_calls_index_file(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._handle_change("/ws/main.py", "modify")
        indexer.index_file.assert_called_once_with("/ws/main.py")

    def test_handle_change_calls_searcher_refresh(self):
        refresh = MagicMock()
        watcher = CodeWatcher(_mock_indexer(), searcher_refresh_fn=refresh)
        watcher._handle_change("/ws/main.py", "modify")
        refresh.assert_called_once()

    def test_handle_change_no_refresh_fn_no_error(self):
        watcher = CodeWatcher(_mock_indexer(), searcher_refresh_fn=None)
        watcher._handle_change("/ws/main.py", "modify")  # should not raise


# ---------------------------------------------------------------------------
# CodeWatcher — start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStartStop:
    def test_start_creates_and_starts_observer(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)

        mock_observer = MagicMock()
        with patch("agent.watcher.Observer", return_value=mock_observer):
            watcher.start()

        mock_observer.schedule.assert_called_once_with(watcher, str(indexer.workspace_root), recursive=True)
        mock_observer.start.assert_called_once()

    def test_stop_cancels_debounce_and_stops_observer(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        mock_observer = MagicMock()
        watcher._observer = mock_observer

        watcher.stop()

        watcher._debounce.cancel_all.assert_called_once()
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()

    def test_stop_with_no_observer_no_error(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._observer = None
        watcher.stop()  # should not raise
