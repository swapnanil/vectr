"""Incremental file watcher: re-indexes only changed files."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from agent.indexer import CodeIndexer, LANG_BY_EXT, EXCLUDED_DIRS


class _DebounceTimer:
    """Resets on each call; fires callback after silence_s seconds of inactivity."""

    def __init__(self, silence_s: float, callback) -> None:
        self._silence = silence_s
        self._callback = callback
        self._pending: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def schedule(self, key: str, *args) -> None:
        with self._lock:
            if key in self._pending:
                self._pending[key].cancel()
            t = threading.Timer(self._silence, self._callback, args=(key, *args))
            self._pending[key] = t
            t.start()

    def cancel_all(self) -> None:
        with self._lock:
            for t in self._pending.values():
                t.cancel()
            self._pending.clear()


class CodeWatcher(FileSystemEventHandler):
    def __init__(self, indexer: CodeIndexer, searcher_refresh_fn=None) -> None:
        super().__init__()
        self._indexer = indexer
        self._searcher_refresh = searcher_refresh_fn
        self._debounce = _DebounceTimer(2.0, self._handle_change)
        self._observer: Observer | None = None
        self._excluded_dirs = self._collect_excluded_dirs()

    def _collect_excluded_dirs(self) -> set[str]:
        """Built-in excluded dir names plus every root's .vectrignore entries.

        The observer watches each root recursively, so without this the watcher
        would re-index files under tmp/, target/, node_modules/, nested clones,
        etc. on every file event — the runaway re-indexing UPG-8.1 describes.
        .gitignore/.vectrignore are consulted by the initial walk but not by
        watchdog events, so we mirror the dir-exclusion rule here.
        """
        from integrations.workspace_detect import get_vectrignore_dirs
        excluded = set(EXCLUDED_DIRS)
        for root in self._indexer.all_roots:
            try:
                excluded |= get_vectrignore_dirs(str(root))
            except Exception:
                pass
        return excluded

    def _is_indexable(self, path: str) -> bool:
        return Path(path).suffix.lower() in LANG_BY_EXT

    def _is_excluded(self, path: str) -> bool:
        """True if any path component is an excluded dir (built-in or .vectrignore)."""
        return bool(set(Path(path).parts) & self._excluded_dirs)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_indexable(event.src_path) \
                and not self._is_excluded(event.src_path):
            self._debounce.schedule(event.src_path, "modify")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_indexable(event.src_path) \
                and not self._is_excluded(event.src_path):
            self._debounce.schedule(event.src_path, "create")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_indexable(event.src_path):
            self._indexer.delete_file(event.src_path)
            if self._searcher_refresh:
                self._searcher_refresh()

    def on_moved(self, event: FileSystemEvent) -> None:
        # A rename/move fires here — NOT on_created or on_modified. Editors and
        # tools that save atomically (write a temp file, then rename it into
        # place) land in this path, so without on_moved a freshly created file
        # is never indexed until it is later edited in place.
        if event.is_directory:
            return
        src = getattr(event, "src_path", None)
        dest = getattr(event, "dest_path", None)
        # The old path leaves the index (if it was something we indexed).
        if src and self._is_indexable(src):
            self._indexer.delete_file(src)
            if self._searcher_refresh:
                self._searcher_refresh()
        # The new path is indexed like a create/modify (debounced) unless excluded.
        if dest and self._is_indexable(dest) and not self._is_excluded(dest):
            self._debounce.schedule(dest, "move")

    def _handle_change(self, path: str, action: str = "modify") -> None:
        self._indexer.index_file(path)
        if self._searcher_refresh:
            self._searcher_refresh()

    def start(self) -> None:
        self._observer = Observer()
        for root in self._indexer.all_roots:
            self._observer.schedule(self, str(root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._debounce.cancel_all()
        if self._observer:
            self._observer.stop()
            self._observer.join()
