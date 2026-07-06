"""Incremental file watcher: re-indexes only changed files."""
from __future__ import annotations

import fnmatch
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from agent.config import WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S
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
        self._excluded_file_globs = self._collect_excluded_globs()
        self._excluded_regexes = self._collect_excluded_regexes()
        # path -> ObservedWatch returned by Observer.schedule(), so a watch can
        # be individually unscheduled when its dir is added to .vectrignore
        # mid-session (UPG-13.3).
        self._watched_dirs: dict[str, object] = {}
        # path -> last-seen mtime, for detecting edits to top-level loose files
        # during the periodic rescan (UPG-13.1) — see _rescan_top_level().
        self._top_level_file_mtimes: dict[str, float] = {}
        self._running = False
        self._rescan_timer: threading.Timer | None = None

    def _collect_excluded_dirs(self) -> set[str]:
        """Built-in excluded dir names plus every root's .vectrignore entries.

        Nested excluded dirs (e.g. a per-package __pycache__ inside a watched
        top-level source dir) still generate events that reach this process, so
        they're filtered here at the Python layer. Top-level excluded dirs
        (.venv, node_modules, .git, tmp, ...) are handled more cheaply — see
        start(): they're never given a native watch at all.
        """
        from integrations.workspace_detect import get_vectrignore_dirs
        excluded = set(EXCLUDED_DIRS)
        for root in self._indexer.all_roots:
            try:
                excluded |= get_vectrignore_dirs(str(root))
            except Exception:
                pass
        return excluded

    def _collect_excluded_globs(self) -> set[str]:
        """File glob patterns (e.g. "*.generated.py") from every root's .vectrignore (UPG-13.3)."""
        from integrations.workspace_detect import get_vectrignore_file_globs
        globs: set[str] = set()
        for root in self._indexer.all_roots:
            try:
                globs |= set(get_vectrignore_file_globs(str(root)))
            except Exception:
                pass
        return globs

    def _is_indexable(self, path: str) -> bool:
        return Path(path).suffix.lower() in LANG_BY_EXT

    def _collect_excluded_regexes(self) -> dict[str, list[re.Pattern]]:
        """Compiled `re:` path-regex entries per root's .vectrignore (UPG-EXCLUDE-REGEX).

        Kept per-root — unlike the flattened dir/glob sets — because a regex
        is matched against a path relative to its own workspace root, so a
        pattern from one root's .vectrignore must never be evaluated against
        another root's tree.
        """
        from integrations.workspace_detect import get_vectrignore_regexes
        regexes: dict[str, list[re.Pattern]] = {}
        for root in self._indexer.all_roots:
            try:
                root_regexes = get_vectrignore_regexes(str(root))
            except Exception:
                root_regexes = []
            if root_regexes:
                regexes[str(root)] = root_regexes
        return regexes

    def _is_excluded(self, path: str) -> bool:
        """True if a path is under an excluded dir, matches an excluded file
        glob, or matches an excluded path regex.

        Directory and regex matching are scoped to path components/paths
        BELOW a workspace root. Matching the absolute prefix too would
        wrongly exclude an entire workspace that merely lives under a dir
        named like an excluded one — e.g. any repo checked out under /tmp on
        Linux (the prefix contains 'tmp'), or a path containing
        'build'/'target'. (Paths outside every root fall back to all parts.)
        """
        p = Path(path)
        if self._excluded_file_globs and any(
            fnmatch.fnmatch(p.name, pat) for pat in self._excluded_file_globs
        ):
            return True
        for root in self._indexer.all_roots:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts) & self._excluded_dirs:
                return True
            root_regexes = self._excluded_regexes.get(str(root))
            if root_regexes and any(rx.search(rel.as_posix()) for rx in root_regexes):
                return True
            return False
        return bool(set(p.parts) & self._excluded_dirs)

    def _is_vectrignore_path(self, path: str) -> bool:
        """True if `path` is a workspace root's own .vectrignore file (UPG-13.3)."""
        p = Path(path)
        if p.name != ".vectrignore":
            return False
        return any(p.parent == Path(root) for root in self._indexer.all_roots)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_vectrignore_path(event.src_path):
            self._refresh_vectrignore()
            return
        if self._is_indexable(event.src_path) and not self._is_excluded(event.src_path):
            self._debounce.schedule(event.src_path, "modify")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_vectrignore_path(event.src_path):
            self._refresh_vectrignore()
            return
        if self._is_indexable(event.src_path) and not self._is_excluded(event.src_path):
            self._debounce.schedule(event.src_path, "create")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            # A watched top-level dir was removed — drop it so a later
            # re-creation of the same name gets re-scheduled (UPG-13.1).
            self._watched_dirs.pop(event.src_path, None)
            return
        if self._is_indexable(event.src_path):
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

    # ------------------------------------------------------------------
    # Watch scheduling (UPG-13.1)
    #
    # The Observer never watches a workspace root directly. On macOS,
    # watchdog's FSEvents backend always subscribes to a path's ENTIRE
    # subtree at the OS level — the `recursive` flag is enforced only by
    # filtering events in the Python emitter *after* they're delivered, so a
    # "non-recursive" watch on a root still incurs the full native event
    # volume (and CPU cost) of everything underneath it, including huge
    # excluded siblings like .venv/node_modules/.git. Confirmed empirically:
    # churning thousands of files in an excluded dir under a non-recursively
    # watched root still delivered 100% of those events into the emitter;
    # the same churn produced ZERO events once the root itself was left
    # unwatched and only the included sibling directory was scheduled.
    # So exclusion only works by never scheduling a watch whose path is an
    # ancestor of an excluded directory — hence per-top-level-included-dir
    # scheduling below, with a lightweight periodic scan (not a live watch)
    # of the root's own direct children to pick up new top-level entries.
    # ------------------------------------------------------------------

    def _top_level_included_dirs(self, root: Path) -> list[Path]:
        """This root's top-level directories that are not excluded."""
        dirs: list[Path] = []
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return dirs
        for entry in entries:
            if entry.is_dir() and entry.name not in self._excluded_dirs:
                dirs.append(entry)
        return dirs

    def _schedule_dir(self, path: Path) -> None:
        key = str(path)
        if key in self._watched_dirs or self._observer is None:
            return
        watch = self._observer.schedule(self, key, recursive=True)
        self._watched_dirs[key] = watch

    def _rescan_top_level(self) -> None:
        """Shallow poll of each root's direct children (UPG-13.1/13.3).

        Detects new top-level directories (schedules a watch for them) and
        changes to top-level loose files (schedules a debounced index), without
        ever stat-ing anything below the top level — cheap regardless of repo
        size, and immune to the FSEvents native-recursion issue above since it
        never registers a watch on the root.
        """
        for root in self._indexer.all_roots:
            root = Path(root)
            try:
                entries = list(root.iterdir())
            except OSError:
                continue
            for entry in entries:
                key = str(entry)
                if entry.is_dir():
                    if entry.name in self._excluded_dirs:
                        continue
                    self._schedule_dir(entry)
                elif self._is_indexable(key) and not self._is_excluded(key):
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        continue
                    prev = self._top_level_file_mtimes.get(key)
                    self._top_level_file_mtimes[key] = mtime
                    if prev is not None and mtime > prev:
                        self._debounce.schedule(key, "modify")

        if self._running:
            self._rescan_timer = threading.Timer(
                WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S, self._rescan_top_level
            )
            self._rescan_timer.daemon = True
            self._rescan_timer.start()

    def _refresh_vectrignore(self) -> None:
        """Re-read .vectrignore and re-schedule watches to match (UPG-13.3)."""
        self._excluded_dirs = self._collect_excluded_dirs()
        self._excluded_file_globs = self._collect_excluded_globs()
        self._excluded_regexes = self._collect_excluded_regexes()
        self._reschedule_watches()

    def _reschedule_watches(self) -> None:
        """Bring active watches in line with the current excluded-dirs set."""
        if self._observer is None:
            return
        for root in self._indexer.all_roots:
            root = Path(root)
            included = {str(d) for d in self._top_level_included_dirs(root)}
            for path, watch in list(self._watched_dirs.items()):
                if Path(path).parent != root:
                    continue
                if path not in included:
                    try:
                        self._observer.unschedule(watch)
                    except Exception:
                        pass
                    del self._watched_dirs[path]
            for path in included:
                self._schedule_dir(Path(path))

    def start(self) -> None:
        self._observer = Observer()
        for root in self._indexer.all_roots:
            for d in self._top_level_included_dirs(Path(root)):
                self._schedule_dir(d)
        self._observer.start()
        self._running = True
        self._rescan_top_level()

    def stop(self) -> None:
        self._running = False
        if self._rescan_timer is not None:
            self._rescan_timer.cancel()
        self._debounce.cancel_all()
        if self._observer:
            self._observer.stop()
            self._observer.join()
