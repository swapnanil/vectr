"""Tests for agent/watcher.py — _DebounceTimer and CodeWatcher."""
from __future__ import annotations

import logging
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
    # Real CodeIndexer.index_file/delete_file return int chunk counts (see
    # agent/indexer/_core.py) — the batch worker's per-batch chunk-count
    # diagnostic does arithmetic on these return values, so the mock must
    # return the real type, not an unconfigured MagicMock.
    indexer.index_file.return_value = 0
    indexer.delete_file.return_value = 0
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
        "/ws/archive.zip",
        "/ws/debug.log",
    ])
    def test_non_code_extensions_not_indexable(self, watcher, path):
        # UPG-11.3: .txt is now indexed as prose doc; removed from this list.
        # .log, .csv, .png, .zip remain unsupported.
        assert watcher._is_indexable(path) is False

    @pytest.mark.parametrize("path", [
        "/ws/howto.txt",
        "/ws/docs/readme.rst",
    ])
    def test_txt_and_rst_are_indexable(self, watcher, path):
        # UPG-11.3: .txt and .rst are now indexed as prose (F2 fix)
        assert watcher._is_indexable(path) is True

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
# CodeWatcher — _is_excluded (UPG-8.1: watcher honors excluded dirs)
# ---------------------------------------------------------------------------

class TestIsExcluded:
    @pytest.mark.parametrize("path", [
        "/ws/node_modules/pkg/index.js",
        "/ws/target/debug/build.rs",
        "/ws/.venv/lib/utils.py",
        "/ws/build/out.py",
        "/ws/sub/__pycache__/mod.py",
    ])
    def test_builtin_excluded_dirs(self, path):
        watcher = CodeWatcher(_mock_indexer())
        assert watcher._is_excluded(path) is True

    @pytest.mark.parametrize("path", [
        "/ws/src/main.py",
        "/ws/app/service.py",
        "/ws/lib.rs",
    ])
    def test_normal_paths_not_excluded(self, path):
        watcher = CodeWatcher(_mock_indexer())
        assert watcher._is_excluded(path) is False

    def test_vectrignore_dir_excluded(self, tmp_path):
        # A dir named in .vectrignore (e.g. tmp) must be excluded for the
        # running instance's watcher, not just the initial index walk.
        (tmp_path / ".vectrignore").write_text("tmp\nfixtures\n", encoding="utf-8")
        watcher = CodeWatcher(_mock_indexer(str(tmp_path)))
        assert watcher._is_excluded(str(tmp_path / "tmp" / "clone" / "main.py")) is True
        assert watcher._is_excluded(str(tmp_path / "fixtures" / "data.py")) is True
        assert watcher._is_excluded(str(tmp_path / "src" / "main.py")) is False

    def test_workspace_under_excluded_named_prefix_not_excluded(self, tmp_path):
        # Regression (CI on Linux): a workspace whose ABSOLUTE path contains an
        # excluded dir name (e.g. a repo under /tmp, or .../build/proj) must not
        # have all its files excluded. Only parts BELOW the root count.
        from pathlib import Path
        root = tmp_path / "tmp" / "build" / "proj"   # prefix has 'tmp' AND 'build'
        root.mkdir(parents=True)
        indexer = MagicMock()
        indexer.workspace_root = str(root)
        indexer.all_roots = [Path(root)]
        watcher = CodeWatcher(indexer)
        assert watcher._is_excluded(str(root / "src" / "main.py")) is False
        # but a real excluded dir *inside* the workspace still excludes
        assert watcher._is_excluded(str(root / "node_modules" / "x.js")) is True

    def test_vectrignore_applied_across_extra_roots(self, tmp_path):
        # Extra roots each contribute their own .vectrignore entries.
        from pathlib import Path
        extra = tmp_path / "extra"
        extra.mkdir()
        (extra / ".vectrignore").write_text("tmp\n", encoding="utf-8")
        indexer = MagicMock()
        indexer.workspace_root = str(tmp_path)
        indexer.all_roots = [Path(tmp_path), Path(extra)]
        watcher = CodeWatcher(indexer)
        assert watcher._is_excluded(str(extra / "tmp" / "poc" / "lib.rs")) is True

    def test_gitignore_filename_glob_excluded(self, tmp_path):
        # UPG-REST-STARVATION diagnostics: a .gitignore glob (not present in
        # .vectrignore) must exclude a matching file for the live watcher,
        # the same way should_index_file already excludes it for a full
        # reindex — a gitignored build artifact must never enter the live
        # watch/index set either.
        (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
        watcher = CodeWatcher(_mock_indexer(str(tmp_path)))
        assert watcher._is_excluded(str(tmp_path / "run.log")) is True
        assert watcher._is_excluded(str(tmp_path / "src" / "main.py")) is False

    def test_gitignore_directory_pattern_excluded(self, tmp_path):
        (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")
        watcher = CodeWatcher(_mock_indexer(str(tmp_path)))
        assert watcher._is_excluded(str(tmp_path / "dist" / "bundle.js")) is True

    def test_gitignore_scoped_per_root_not_cross_applied(self, tmp_path):
        # A .gitignore pattern from one root must not be evaluated against a
        # different (sibling) root's tree — mirrors the existing per-root
        # regex isolation test (test_regex_scoped_per_root_not_cross_applied).
        from pathlib import Path
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_b / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        indexer = MagicMock()
        indexer.workspace_root = str(root_a)
        indexer.all_roots = [Path(root_a), Path(root_b)]
        watcher = CodeWatcher(indexer)
        assert watcher._is_excluded(str(root_b / "mod.pyc")) is True
        assert watcher._is_excluded(str(root_a / "mod.pyc")) is False

    def test_gitignore_parity_with_bulk_index_walk(self, tmp_path):
        # The exact parity contract: a file should_index_file() would skip
        # for a full reindex must also be excluded by the live watcher — the
        # two paths share one matching predicate (matches_gitignore_pattern)
        # precisely so this can never drift.
        from integrations.workspace_detect import get_gitignore_patterns, should_index_file
        (tmp_path / ".gitignore").write_text("*.generated.js\n", encoding="utf-8")
        target = tmp_path / "out.generated.js"
        watcher = CodeWatcher(_mock_indexer(str(tmp_path)))
        assert should_index_file(
            str(target), get_gitignore_patterns(str(tmp_path)), workspace_root=str(tmp_path)
        ) is False
        assert watcher._is_excluded(str(target)) is True


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

    def test_excluded_dir_file_ignored(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event("/ws/node_modules/pkg/index.js"))
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

    def test_excluded_dir_file_ignored(self):
        # The runaway-reindex case: a clone under tmp/ creates source files.
        indexer = _mock_indexer("/ws")
        from pathlib import Path
        # Simulate .vectrignore-derived exclusion by injecting the dir name.
        watcher = CodeWatcher(indexer)
        watcher._excluded_dirs.add("tmp")
        watcher._debounce = MagicMock()
        watcher.on_created(_mock_event("/ws/tmp/poc-clone/src/main.py"))
        watcher._debounce.schedule.assert_not_called()


class TestOnDeleted:
    # UPG-REST-STARVATION: deletes now flow through the same debounce/burst/
    # single-worker pipeline as create/modify instead of calling
    # indexer.delete_file() + a synchronous searcher refresh immediately on
    # the FSEvents callback thread (which, at scale, meant one full-corpus
    # BM25 rebuild per deleted file — the root cause of the reported
    # multi-minute REST starvation during a large revert/delete storm).

    def test_indexable_file_schedules_debounced_delete(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_deleted(_mock_event("/ws/old.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/old.py", "delete")
        indexer.delete_file.assert_not_called()  # deferred until the debounce fires

    def test_delete_calls_searcher_refresh_after_debounce(self):
        indexer = _mock_indexer()
        refresh = MagicMock()
        watcher = CodeWatcher(indexer, searcher_refresh_fn=refresh)
        watcher._debounce = _DebounceTimer(0.05, watcher._handle_change)
        watcher.on_deleted(_mock_event("/ws/old.py"))
        time.sleep(0.2)
        indexer.delete_file.assert_called_once_with("/ws/old.py")
        refresh.assert_called_once()

    def test_delete_no_refresh_fn_no_error(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer, searcher_refresh_fn=None)
        watcher._debounce = _DebounceTimer(0.05, watcher._handle_change)
        watcher.on_deleted(_mock_event("/ws/old.py"))  # should not raise
        time.sleep(0.2)
        indexer.delete_file.assert_called_once_with("/ws/old.py")

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

    def test_excluded_dir_file_not_scheduled(self):
        # UPG-WATCH-REVERT-CHURN: exclusion must be symmetric between
        # indexing and de-indexing — a delete under an excluded dir (e.g. a
        # nested build/venv directory regenerated in bulk) was previously
        # scheduled into the watcher pipeline even though the file was never
        # indexed in the first place, inflating burst/batch volume for no
        # benefit.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_deleted(_mock_event("/ws/node_modules/pkg/index.js"))
        watcher._debounce.schedule.assert_not_called()


class TestOnMoved:
    def test_rename_into_place_indexes_dest(self):
        # The case that mattered: atomic save / Write tool creates a file via
        # rename, so the new path must be scheduled for indexing. The old
        # (indexable) src name is scheduled for removal too — UPG-REST-
        # STARVATION routes it through the same debounced pipeline as dest.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/new_module.py", "/ws/dest.py"))
        watcher._debounce.schedule.assert_any_call("/ws/dest.py", "move")
        watcher._debounce.schedule.assert_any_call("/ws/new_module.py", "delete")

    def test_temp_rename_indexes_only_dest(self):
        # Editor atomic save: temp file (non-indexable) renamed onto real .py
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/.main.py.swp.tmp", "/ws/main.py"))
        watcher._debounce.schedule.assert_called_once_with("/ws/main.py", "move")
        indexer.delete_file.assert_not_called()  # temp src was never indexed

    def test_rename_schedules_delete_for_old_src_and_move_for_dest(self):
        # UPG-REST-STARVATION: the old src path is scheduled through the same
        # debounce/burst pipeline as the new dest path, not deleted
        # synchronously on the FSEvents callback thread.
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/old.py", "/ws/renamed.py"))
        watcher._debounce.schedule.assert_any_call("/ws/old.py", "delete")
        watcher._debounce.schedule.assert_any_call("/ws/renamed.py", "move")
        indexer.delete_file.assert_not_called()  # deferred until the debounce fires

    def test_src_and_dest_debounce_trigger_searcher_refresh(self):
        indexer = _mock_indexer()
        refresh = MagicMock()
        watcher = CodeWatcher(indexer, searcher_refresh_fn=refresh)
        watcher._debounce = _DebounceTimer(0.05, watcher._handle_change)
        watcher.on_moved(_mock_move_event("/ws/old.py", "/ws/new.py"))
        time.sleep(0.2)
        indexer.delete_file.assert_called_once_with("/ws/old.py")
        indexer.index_file.assert_called_once_with("/ws/new.py")
        assert refresh.call_count == 2  # one per debounced change firing

    def test_non_indexable_dest_not_scheduled(self):
        # dest (.csv) is never scheduled; src (.py, indexable) still gets a
        # debounced delete for its old chunks.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/page.py", "/ws/data.csv"))
        watcher._debounce.schedule.assert_called_once_with("/ws/page.py", "delete")

    def test_excluded_dest_not_scheduled(self):
        # dest under an excluded dir is never scheduled; src (.py, indexable,
        # not excluded) still gets a debounced delete for its old chunks.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/x.py", "/ws/node_modules/pkg/y.js"))
        watcher._debounce.schedule.assert_called_once_with("/ws/x.py", "delete")

    def test_excluded_src_not_scheduled_for_delete(self):
        # UPG-WATCH-REVERT-CHURN: symmetric with the dest-side exclusion
        # check above — a move whose OLD path lived under an excluded dir
        # must not schedule a delete for it, even though the (non-excluded)
        # dest path is still scheduled as usual.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_moved(_mock_move_event("/ws/node_modules/pkg/x.js", "/ws/y.js"))
        watcher._debounce.schedule.assert_called_once_with("/ws/y.js", "move")

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
    def test_start_schedules_included_top_level_dirs_only(self, tmp_path):
        # UPG-13.1: the workspace root itself must NEVER be scheduled -- on
        # macOS FSEvents, a watch on an ancestor of an excluded dir (e.g.
        # .venv, node_modules) still delivers every native event from that
        # subtree regardless of the `recursive` flag, which is exactly the
        # CPU storm this fix eliminates. Only non-excluded top-level dirs
        # get their own watch.
        (tmp_path / "src").mkdir()
        (tmp_path / "app").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / "node_modules").mkdir()

        indexer = MagicMock()
        indexer.workspace_root = str(tmp_path)
        indexer.all_roots = [tmp_path]
        watcher = CodeWatcher(indexer)

        mock_observer = MagicMock()
        with patch("agent.watcher.Observer", return_value=mock_observer):
            watcher.start()
            watcher.stop()  # cancels the real background rescan Timer start() armed

        scheduled_paths = {c.args[1] for c in mock_observer.schedule.call_args_list}
        assert scheduled_paths == {str(tmp_path / "src"), str(tmp_path / "app")}
        assert str(tmp_path) not in scheduled_paths
        assert str(tmp_path / ".venv") not in scheduled_paths
        assert str(tmp_path / "node_modules") not in scheduled_paths
        mock_observer.start.assert_called_once()

    def test_start_creates_observer(self):
        watcher = CodeWatcher(_mock_indexer())
        mock_observer = MagicMock()
        with patch("agent.watcher.Observer", return_value=mock_observer):
            watcher.start()
            watcher.stop()
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


# ---------------------------------------------------------------------------
# CodeWatcher — _rescan_top_level (UPG-13.1: new top-level dirs / loose files)
# ---------------------------------------------------------------------------

def _watcher_with_mock_observer(root):
    indexer = MagicMock()
    indexer.workspace_root = str(root)
    indexer.all_roots = [root]
    watcher = CodeWatcher(indexer)
    watcher._observer = MagicMock()
    return watcher


class TestRescanTopLevel:
    def test_rescan_schedules_new_top_level_dir(self, tmp_path):
        (tmp_path / "src").mkdir()
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._rescan_top_level()  # single-shot: _running is False by default
        watcher._observer.schedule.reset_mock()

        (tmp_path / "newpkg").mkdir()
        watcher._rescan_top_level()

        scheduled = {c.args[1] for c in watcher._observer.schedule.call_args_list}
        assert str(tmp_path / "newpkg") in scheduled

    def test_rescan_does_not_reschedule_already_watched_dir(self, tmp_path):
        (tmp_path / "src").mkdir()
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._rescan_top_level()
        watcher._observer.schedule.reset_mock()

        watcher._rescan_top_level()  # nothing new
        watcher._observer.schedule.assert_not_called()

    def test_rescan_skips_excluded_top_level_dir(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._rescan_top_level()
        scheduled = {c.args[1] for c in watcher._observer.schedule.call_args_list}
        assert str(tmp_path / ".venv") not in scheduled

    def test_rescan_detects_top_level_file_change(self, tmp_path):
        import os

        f = tmp_path / "main.py"
        f.write_text("a")
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._debounce = MagicMock()

        watcher._rescan_top_level()  # first sighting: primes mtime, no event
        watcher._debounce.schedule.assert_not_called()

        # Bump mtime deterministically (no reliance on filesystem timestamp
        # granularity or real sleeps).
        future = (f.stat().st_mtime) + 5
        os.utime(f, (future, future))

        watcher._rescan_top_level()
        watcher._debounce.schedule.assert_called_once_with(str(f), "modify")

    def test_rescan_ignores_non_indexable_top_level_file(self, tmp_path):
        f = tmp_path / "notes.csv"
        f.write_text("a")
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._debounce = MagicMock()
        watcher._rescan_top_level()
        assert str(f) not in watcher._top_level_file_mtimes

    def test_rescan_does_not_reschedule_timer_when_not_running(self, tmp_path):
        watcher = _watcher_with_mock_observer(tmp_path)
        assert watcher._running is False
        watcher._rescan_top_level()
        assert watcher._rescan_timer is None  # single-shot: no repeating Timer armed

    def test_rescan_reschedules_timer_when_running(self, tmp_path):
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._running = True
        try:
            watcher._rescan_top_level()
            assert watcher._rescan_timer is not None
        finally:
            watcher._running = False
            if watcher._rescan_timer is not None:
                watcher._rescan_timer.cancel()


# ---------------------------------------------------------------------------
# CodeWatcher — live .vectrignore updates (UPG-13.3)
# ---------------------------------------------------------------------------

class TestVectrignoreLiveUpdate:
    def test_vectrignore_modify_event_refreshes_excluded_dirs(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        watcher = _watcher_with_mock_observer(tmp_path)
        assert "vendor" not in watcher._excluded_dirs

        (tmp_path / ".vectrignore").write_text("vendor\n", encoding="utf-8")
        watcher.on_modified(_mock_event(str(tmp_path / ".vectrignore")))

        assert "vendor" in watcher._excluded_dirs

    def test_vectrignore_edit_unschedules_newly_excluded_dir(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._rescan_top_level()
        assert str(tmp_path / "vendor") in watcher._watched_dirs

        (tmp_path / ".vectrignore").write_text("vendor\n", encoding="utf-8")
        watcher.on_modified(_mock_event(str(tmp_path / ".vectrignore")))

        assert str(tmp_path / "vendor") not in watcher._watched_dirs
        watcher._observer.unschedule.assert_called_once()

    def test_vectrignore_edit_schedules_newly_included_dir(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        (tmp_path / ".vectrignore").write_text("vendor\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._rescan_top_level()
        assert str(tmp_path / "vendor") not in watcher._watched_dirs

        (tmp_path / ".vectrignore").write_text("# nothing excluded now\n", encoding="utf-8")
        watcher.on_modified(_mock_event(str(tmp_path / ".vectrignore")))

        assert str(tmp_path / "vendor") in watcher._watched_dirs

    def test_vectrignore_created_event_also_refreshes(self, tmp_path):
        watcher = _watcher_with_mock_observer(tmp_path)
        (tmp_path / ".vectrignore").write_text("vendor\n", encoding="utf-8")
        watcher.on_created(_mock_event(str(tmp_path / ".vectrignore")))
        assert "vendor" in watcher._excluded_dirs

    def test_non_root_vectrignore_ignored(self, tmp_path):
        # A .vectrignore that isn't at any workspace root's own top level
        # (e.g. inside a subdirectory) must not be treated as the live-config
        # file -- it's just an ordinary (non-indexable) file event.
        watcher = _watcher_with_mock_observer(tmp_path)
        nested = tmp_path / "src" / ".vectrignore"
        assert watcher._is_vectrignore_path(str(nested)) is False


# ---------------------------------------------------------------------------
# CodeWatcher — .vectrignore file glob patterns (UPG-13.3)
# ---------------------------------------------------------------------------

class TestVectrignoreFileGlobs:
    def test_glob_pattern_excludes_matching_file(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("*.generated.py\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        assert watcher._is_excluded(str(tmp_path / "models.generated.py")) is True

    def test_glob_pattern_does_not_exclude_non_matching_file(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("*.generated.py\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        assert watcher._is_excluded(str(tmp_path / "models.py")) is False

    def test_on_modified_skips_glob_excluded_file(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("*.generated.py\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event(str(tmp_path / "schema.generated.py")))
        watcher._debounce.schedule.assert_not_called()


# ---------------------------------------------------------------------------
# CodeWatcher — .vectrignore `re:` path regex patterns (UPG-EXCLUDE-REGEX)
# ---------------------------------------------------------------------------

class TestVectrignoreRegex:
    def test_regex_excludes_matching_path(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("re:legacy/.*\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        assert watcher._is_excluded(str(legacy / "handler.py")) is True

    def test_regex_does_not_exclude_sibling(self, tmp_path):
        (tmp_path / ".vectrignore").write_text("re:legacy/.*\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        assert watcher._is_excluded(str(src / "main.py")) is False

    def test_regex_scoped_per_root(self, tmp_path):
        # A `re:` pattern from one root's .vectrignore must not be evaluated
        # against a different (sibling) root's tree.
        from pathlib import Path
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_b / ".vectrignore").write_text("re:legacy/.*\n", encoding="utf-8")
        indexer = MagicMock()
        indexer.workspace_root = str(root_a)
        indexer.all_roots = [Path(root_a), Path(root_b)]
        watcher = CodeWatcher(indexer)
        assert watcher._is_excluded(str(root_b / "legacy" / "old.py")) is True
        assert watcher._is_excluded(str(root_a / "legacy" / "old.py")) is False

    def test_on_modified_skips_regex_excluded_file(self, tmp_path):
        # Uses a supported extension (.py) so the assertion actually exercises
        # the regex exclusion path, not just the unrelated _is_indexable check.
        (tmp_path / ".vectrignore").write_text(r"re:.*_backup\.py$" + "\n", encoding="utf-8")
        watcher = _watcher_with_mock_observer(tmp_path)
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event(str(tmp_path / "schema_backup.py")))
        watcher._debounce.schedule.assert_not_called()

    def test_vectrignore_modify_event_refreshes_regexes(self, tmp_path):
        watcher = _watcher_with_mock_observer(tmp_path)
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        assert watcher._is_excluded(str(legacy / "handler.py")) is False

        (tmp_path / ".vectrignore").write_text("re:legacy/.*\n", encoding="utf-8")
        watcher.on_modified(_mock_event(str(tmp_path / ".vectrignore")))

        assert watcher._is_excluded(str(legacy / "handler.py")) is True


# ---------------------------------------------------------------------------
# CodeWatcher — end-to-end CPU-storm regression (UPG-13.1 acceptance)
#
# Uses the REAL watchdog Observer (not mocked) so this exercises the actual
# OS-level watch topology, not just the Python-side filtering logic covered
# above. Guards against reintroducing a recursive watch on the workspace
# root, which is what caused the reported CPU storm.
# ---------------------------------------------------------------------------

class TestCpuStormRegressionRealObserver:
    def test_excluded_dir_churn_never_reaches_index_file(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        excluded = tmp_path / "node_modules"
        excluded.mkdir()

        indexer = MagicMock()
        indexer.workspace_root = str(tmp_path)
        indexer.all_roots = [tmp_path]

        watcher = CodeWatcher(indexer)
        watcher._debounce = _DebounceTimer(0.1, watcher._handle_change)
        watcher.start()
        try:
            # Heavy churn under the excluded top-level dir -- this is the
            # pytest/.pyc-write / git-churn scenario from the bug report.
            for i in range(50):
                (excluded / f"churn{i}.js").write_text("x")
            time.sleep(0.4)  # well past the 0.1s debounce window
            indexer.index_file.assert_not_called()

            # A real edit under an INCLUDED dir must still be picked up.
            (src / "main.py").write_text("def f(): pass\n")
            time.sleep(0.4)
            indexer.index_file.assert_called_once_with(str(src / "main.py"))
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# CodeWatcher — burst coalescing + single bounded worker + self-limit
# (UPG-WATCHER-PRESSURE-GOVERNOR)
# ---------------------------------------------------------------------------

class TestBurstCoalescing:
    def test_below_threshold_uses_per_file_debounce(self):
        # Default burst_files_threshold (8): a couple of distinct edits stay
        # on the per-file _debounce path, never entering burst mode.
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        watcher.on_modified(_mock_event("/ws/a.py"))
        watcher.on_modified(_mock_event("/ws/b.py"))
        watcher._debounce.schedule.assert_has_calls(
            [call("/ws/a.py", "modify"), call("/ws/b.py", "modify")]
        )
        assert watcher._burst_mode is False

    def test_burst_threshold_triggers_coalesced_batch(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        with patch("agent.watcher.WATCHER_BURST_FILES_THRESHOLD", 3), \
             patch("agent.watcher.WATCHER_BURST_QUIET_SECONDS", 0.05):
            for i in range(5):
                watcher.on_modified(_mock_event(f"/ws/f{i}.py"))

            # Crossing the threshold cancels the per-file timers and switches
            # to burst mode — nothing has been indexed yet, waiting for quiet.
            assert watcher._burst_mode is True
            indexer.index_file.assert_not_called()

            time.sleep(0.25)  # well past the 0.05s quiet window

        assert {c.args[0] for c in indexer.index_file.call_args_list} == {
            f"/ws/f{i}.py" for i in range(5)
        }
        assert watcher._burst_mode is False

    def test_delete_burst_coalesces_into_one_batch_and_one_refresh(self):
        # UPG-REST-STARVATION: this is the actual reported failure mode — a
        # large delete/revert storm (e.g. reverting one agent session's edits
        # across thousands of files) must coalesce into ONE batch pass with
        # ONE searcher refresh, not N synchronous per-file deletes each
        # triggering its own full-corpus BM25 rebuild on the FSEvents
        # callback thread.
        indexer = _mock_indexer()
        refresh = MagicMock()
        watcher = CodeWatcher(indexer, searcher_refresh_fn=refresh)
        with patch("agent.watcher.WATCHER_BURST_FILES_THRESHOLD", 3), \
             patch("agent.watcher.WATCHER_BURST_QUIET_SECONDS", 0.05):
            for i in range(5):
                watcher.on_deleted(_mock_event(f"/ws/f{i}.py"))

            assert watcher._burst_mode is True
            indexer.delete_file.assert_not_called()

            time.sleep(0.25)  # well past the 0.05s quiet window

        assert {c.args[0] for c in indexer.delete_file.call_args_list} == {
            f"/ws/f{i}.py" for i in range(5)
        }
        assert watcher._burst_mode is False
        refresh.assert_called_once()

    def test_second_storm_during_running_batch_queues_into_one_pending_batch(self):
        indexer = _mock_indexer()
        started = threading.Event()
        release = threading.Event()

        def slow_index_file(path):
            started.set()
            release.wait(2)
            return 0

        indexer.index_file.side_effect = slow_index_file
        watcher = CodeWatcher(indexer)

        watcher._submit_batch({"/ws/a.py"})
        assert started.wait(1), "first batch never started"
        with watcher._batch_lock:
            assert watcher._batch_running is True

        # A second coalesced batch becomes ready while the first is still
        # running — it must merge into the ONE pending set, not spawn a
        # second worker.
        watcher._submit_batch({"/ws/b.py", "/ws/c.py"})
        with watcher._batch_lock:
            assert watcher._pending_batch == {"/ws/b.py", "/ws/c.py"}
            assert watcher._batch_running is True

        release.set()
        deadline = time.monotonic() + 2
        while indexer.index_file.call_count < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert {c.args[0] for c in indexer.index_file.call_args_list} == {
            "/ws/a.py", "/ws/b.py", "/ws/c.py"
        }
        with watcher._batch_lock:
            assert watcher._batch_running is False
            assert watcher._pending_batch == set()

    def test_rss_limit_defers_batch_and_logs_one_warning(self, caplog):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        watcher._rss_reader = lambda: 999_999.0

        with patch("agent.watcher.WATCHER_MAX_RSS_MB", 10), \
             caplog.at_level(logging.WARNING, logger="agent.watcher"):
            watcher._maybe_run_or_defer({"/ws/a.py"})

        indexer.index_file.assert_not_called()
        assert watcher._burst_mode is True
        assert watcher._burst_pending == {"/ws/a.py"}
        warnings = [r for r in caplog.records if "deferring" in r.message]
        assert len(warnings) == 1

        # Stop the timer this armed so it can't fire during/after the test.
        with watcher._lock:
            if watcher._burst_quiet_timer is not None:
                watcher._burst_quiet_timer.cancel()
                watcher._burst_quiet_timer = None

    def test_rss_limit_clears_runs_batch_on_next_quiet_window(self):
        indexer = _mock_indexer()
        watcher = CodeWatcher(indexer)
        rss = {"value": 999_999.0}
        watcher._rss_reader = lambda: rss["value"]

        with patch("agent.watcher.WATCHER_MAX_RSS_MB", 10):
            watcher._maybe_run_or_defer({"/ws/a.py"})
        indexer.index_file.assert_not_called()
        with watcher._lock:
            if watcher._burst_quiet_timer is not None:
                watcher._burst_quiet_timer.cancel()
                watcher._burst_quiet_timer = None

        # Pressure clears; the next quiet window runs the deferred batch.
        rss["value"] = 0.0
        with patch("agent.watcher.WATCHER_MAX_RSS_MB", 10):
            watcher._on_burst_quiet()

        deadline = time.monotonic() + 2
        while indexer.index_file.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        indexer.index_file.assert_called_once_with("/ws/a.py")

    def test_watcher_status_fields_present(self):
        watcher = CodeWatcher(_mock_indexer())
        status = watcher.watcher_status()
        assert set(status) == {
            "watcher_burst_mode", "watcher_pending_files",
            "watcher_batch_running", "watcher_last_batch_duration_ms",
        }
        assert status["watcher_burst_mode"] is False
        assert status["watcher_pending_files"] == 0
        assert status["watcher_batch_running"] is False
        assert status["watcher_last_batch_duration_ms"] == 0

    def test_watcher_status_reflects_pending_and_burst_mode(self):
        watcher = CodeWatcher(_mock_indexer())
        watcher._debounce = MagicMock()
        with patch("agent.watcher.WATCHER_BURST_FILES_THRESHOLD", 100):
            watcher.on_modified(_mock_event("/ws/a.py"))
            watcher.on_modified(_mock_event("/ws/b.py"))
        status = watcher.watcher_status()
        assert status["watcher_pending_files"] == 2
        assert status["watcher_burst_mode"] is False
