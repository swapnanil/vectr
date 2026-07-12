"""Incremental file watcher: re-indexes only changed files."""
from __future__ import annotations

import fnmatch
import logging
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from agent.config import (
    WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S,
    WATCHER_BURST_FILES_THRESHOLD,
    WATCHER_BURST_QUIET_SECONDS,
    WATCHER_MAX_RSS_MB,
)
from agent.indexer import CodeIndexer, LANG_BY_EXT, EXCLUDED_DIRS

logger = logging.getLogger(__name__)


def _default_rss_mb() -> float | None:
    """This process's peak resident set size in MB, or None if unavailable.

    Uses the stdlib `resource` module (no extra dependency) — unavailable on
    Windows, in which case the self-limit degrades to a no-op (never blocks a
    batch). `ru_maxrss` is a PEAK value, monotonically non-decreasing for the
    life of the process — see `watcher.max_rss_mb` in config.yaml for why that
    is an acceptable (deliberately conservative) reading for this guard.
    """
    try:
        import resource
        import sys
    except ImportError:
        return None
    try:
        ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    # macOS reports ru_maxrss in bytes; Linux reports it in KB.
    return ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else ru_maxrss / 1024


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
        self._gitignore_patterns = self._collect_gitignore_patterns()
        # path -> ObservedWatch returned by Observer.schedule(), so a watch can
        # be individually unscheduled when its dir is added to .vectrignore
        # mid-session (UPG-13.3).
        self._watched_dirs: dict[str, object] = {}
        # path -> last-seen mtime, for detecting edits to top-level loose files
        # during the periodic rescan (UPG-13.1) — see _rescan_top_level().
        self._top_level_file_mtimes: dict[str, float] = {}
        self._running = False
        self._rescan_timer: threading.Timer | None = None

        # ------------------------------------------------------------------
        # Burst coalescing + single bounded worker (UPG-WATCHER-PRESSURE-GOVERNOR)
        # ------------------------------------------------------------------
        # Injectable so tests can simulate memory pressure without touching
        # the real process; defaults to the stdlib-only reader above.
        self._rss_reader = _default_rss_mb
        self._lock = threading.Lock()
        # Distinct paths currently outstanding on the per-file _debounce timer
        # (mirrors _debounce._pending's keys — tracked separately so this
        # logic works even when a test replaces self._debounce with a mock).
        self._debounce_pending_paths: set[str] = set()
        self._burst_mode = False
        # Paths collected while in burst mode, waiting for repo-wide silence.
        self._burst_pending: set[str] = set()
        self._burst_quiet_timer: threading.Timer | None = None
        # path -> most-recently-scheduled action ("modify"/"create"/"move"/
        # "delete"). Needed because burst mode collapses `_debounce_pending_paths`
        # and `_burst_pending` into bare path sets (the per-file `_debounce`
        # timers carrying the action are cancelled on entering burst — see
        # `_schedule_change`), so the eventual batch worker must look the
        # action back up per path rather than assuming "index" (UPG-REST-
        # STARVATION: delete events now flow through this same pipeline
        # instead of running a synchronous full-corpus BM25 rebuild per
        # event). Entries are popped once consumed, bounding this dict's size
        # to "currently pending", not the lifetime of the daemon.
        self._pending_actions: dict[str, str] = {}

        # Single bounded worker for watcher-triggered batch re-index — never
        # parallel with itself; a batch that arrives while one is already
        # running merges into this one pending set instead of starting a
        # second worker (bounded: one pending batch, not one per event).
        self._batch_lock = threading.Lock()
        self._batch_running = False
        self._pending_batch: set[str] = set()
        self._batch_thread: threading.Thread | None = None
        self._last_batch_duration_s = 0.0

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

    def _collect_gitignore_patterns(self) -> dict[str, list[str]]:
        """Raw .gitignore glob lines per workspace root.

        Kept per-root — like `_collect_excluded_regexes` — because a pattern
        from one root's .gitignore must never be evaluated against another
        root's tree. `should_index_file` (the bulk `index_workspace()` walk)
        already honors .gitignore; without this, a live create/modify event
        for a file matching only a .gitignore pattern (not .vectrignore)
        would still be picked up by the watcher and indexed, even though a
        full reindex would have excluded it — and a live delete event for
        such a file would never fire in the first place, since watchdog
        itself has no ignore-file awareness, so nothing prunes it from the
        index once it slips in this way.
        """
        from integrations.workspace_detect import get_gitignore_patterns
        patterns: dict[str, list[str]] = {}
        for root in self._indexer.all_roots:
            try:
                root_patterns = get_gitignore_patterns(str(root))
            except Exception:
                root_patterns = []
            if root_patterns:
                patterns[str(root)] = root_patterns
        return patterns

    def _is_excluded(self, path: str) -> bool:
        """True if a path is under an excluded dir, matches an excluded file
        glob or .gitignore pattern, or matches an excluded path regex.

        Directory and regex matching are scoped to path components/paths
        BELOW a workspace root. Matching the absolute prefix too would
        wrongly exclude an entire workspace that merely lives under a dir
        named like an excluded one — e.g. any repo checked out under /tmp on
        Linux (the prefix contains 'tmp'), or a path containing
        'build'/'target'. (Paths outside every root fall back to all parts.)

        Applied identically for every event kind (create/modify/delete/move)
        via `on_created`/`on_modified`/`on_deleted`/`on_moved`, so a file the
        bulk indexer would never index is also never scheduled by a live
        event, and a chunk it never gained is never spuriously scheduled for
        deletion either — see `_collect_gitignore_patterns`.
        """
        from integrations.workspace_detect import matches_gitignore_pattern

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
            root_gitignore = self._gitignore_patterns.get(str(root))
            if root_gitignore and matches_gitignore_pattern(p, root_gitignore):
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
            self._schedule_change(event.src_path, "modify")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_vectrignore_path(event.src_path):
            self._refresh_vectrignore()
            return
        if self._is_indexable(event.src_path) and not self._is_excluded(event.src_path):
            self._schedule_change(event.src_path, "create")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            # A watched top-level dir was removed — drop it so a later
            # re-creation of the same name gets re-scheduled (UPG-13.1).
            self._watched_dirs.pop(event.src_path, None)
            return
        # Routed through the same debounce/burst/single-worker pipeline as
        # create/modify (UPG-REST-STARVATION) — a large delete storm (e.g. a
        # revert that removes thousands of files) now coalesces into one
        # bounded batch instead of firing `delete_file()` + a full-corpus
        # `refresh_bm25()` rebuild synchronously, once per file, on this
        # FSEvents callback thread. `_is_excluded` is also checked here now,
        # matching on_created/on_modified: a delete under an excluded dir
        # (e.g. a nested build/venv directory regenerated in bulk) never
        # touches the index in the first place, so scheduling its removal is
        # pure wasted batch volume — see `_is_excluded`'s docstring for why
        # this is a path-membership check, not a query-side heuristic.
        if self._is_indexable(event.src_path) and not self._is_excluded(event.src_path):
            self._schedule_change(event.src_path, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        # A rename/move fires here — NOT on_created or on_modified. Editors and
        # tools that save atomically (write a temp file, then rename it into
        # place) land in this path, so without on_moved a freshly created file
        # is never indexed until it is later edited in place.
        if event.is_directory:
            return
        src = getattr(event, "src_path", None)
        dest = getattr(event, "dest_path", None)
        # The old path leaves the index (if it was something we indexed) —
        # same debounced/batched delete path as on_deleted (UPG-REST-STARVATION).
        if src and self._is_indexable(src) and not self._is_excluded(src):
            self._schedule_change(src, "delete")
        # The new path is indexed like a create/modify (debounced) unless excluded.
        if dest and self._is_indexable(dest) and not self._is_excluded(dest):
            self._schedule_change(dest, "move")

    def _handle_change(self, path: str, action: str = "modify") -> None:
        with self._lock:
            self._debounce_pending_paths.discard(path)
            self._pending_actions.pop(path, None)
        if action == "delete":
            self._indexer.delete_file(path)
        else:
            self._indexer.index_file(path)
        if self._searcher_refresh:
            self._searcher_refresh()

    # ------------------------------------------------------------------
    # Burst coalescing + single bounded worker (UPG-WATCHER-PRESSURE-GOVERNOR)
    #
    # Below `watcher.burst_files_threshold` distinct pending paths, every path
    # still uses its own independent per-file `_debounce` timer — unchanged
    # behaviour. Crossing the threshold cancels every outstanding per-file
    # timer and switches to collecting paths into one set that fires as a
    # single batch re-index after `watcher.burst_quiet_seconds` of repo-wide
    # silence — an edit storm then costs one batch pass, not N independent
    # re-embeds. The batch itself always runs on a single bounded worker: a
    # new batch that becomes ready while one is still running merges into one
    # pending set rather than starting a second worker.
    # ------------------------------------------------------------------

    def _schedule_change(self, path: str, action: str) -> None:
        enter_burst = False
        with self._lock:
            # Most-recent action wins — a delete scheduled for a path that
            # still has a pending create/modify (or vice versa) overwrites it
            # here; the per-file `_debounce.schedule()` call below separately
            # cancels-and-replaces that path's pending Timer, so the two stay
            # consistent below the burst threshold too.
            self._pending_actions[path] = action
            if self._burst_mode:
                self._burst_pending.add(path)
                enter_burst = True  # still in burst mode — reset the quiet timer below
            else:
                self._debounce.schedule(path, action)
                self._debounce_pending_paths.add(path)
                if len(self._debounce_pending_paths) > WATCHER_BURST_FILES_THRESHOLD:
                    self._debounce.cancel_all()
                    self._burst_pending |= self._debounce_pending_paths
                    self._debounce_pending_paths = set()
                    self._burst_mode = True
                    enter_burst = True
        if enter_burst:
            self._reset_burst_quiet_timer()

    def _reset_burst_quiet_timer(self) -> None:
        with self._lock:
            if self._burst_quiet_timer is not None:
                self._burst_quiet_timer.cancel()
            timer = threading.Timer(WATCHER_BURST_QUIET_SECONDS, self._on_burst_quiet)
            timer.daemon = True
            self._burst_quiet_timer = timer
            timer.start()

    def _on_burst_quiet(self) -> None:
        with self._lock:
            paths = self._burst_pending
            self._burst_pending = set()
            self._burst_quiet_timer = None
            if not paths:
                self._burst_mode = False
                return
        self._maybe_run_or_defer(paths)

    def _maybe_run_or_defer(self, paths: set[str]) -> None:
        """Hand a coalesced set of paths to the single bounded worker, unless
        the self-limit RSS gate is tripped (`watcher.max_rss_mb`) — in which
        case the batch is deferred to the next quiet window and ONE warning
        line is logged. Paths are never dropped, only retried later."""
        if self._rss_over_limit():
            rss = self._rss_reader()
            logger.warning(
                "watcher: RSS %.0fMB exceeds watcher.max_rss_mb=%d — deferring "
                "batch re-index of %d file(s) to the next quiet window",
                rss or 0.0, WATCHER_MAX_RSS_MB, len(paths),
            )
            with self._lock:
                self._burst_pending |= paths
                self._burst_mode = True
            self._reset_burst_quiet_timer()
            return
        with self._lock:
            self._burst_mode = False
        self._submit_batch(paths)

    def _rss_over_limit(self) -> bool:
        if WATCHER_MAX_RSS_MB <= 0:
            return False
        try:
            rss_mb = self._rss_reader()
        except Exception:
            return False
        return rss_mb is not None and rss_mb > WATCHER_MAX_RSS_MB

    def _submit_batch(self, paths: set[str]) -> None:
        with self._batch_lock:
            if self._batch_running:
                self._pending_batch |= paths
                return
            self._batch_running = True
        self._launch_batch_thread(paths)

    def _launch_batch_thread(self, paths: set[str]) -> None:
        def run() -> None:
            t0 = time.monotonic()
            with self._lock:
                actions = {p: self._pending_actions.pop(p, "modify") for p in paths}
            indexed_files = deleted_files = 0
            indexed_chunks = deleted_chunks = 0
            for p in sorted(paths):
                try:
                    if actions.get(p) == "delete":
                        deleted_chunks += self._indexer.delete_file(p)
                        deleted_files += 1
                    else:
                        indexed_chunks += self._indexer.index_file(p)
                        indexed_files += 1
                except Exception:
                    logger.exception("watcher: batch re-index failed for %s", p)
                # Cooperative yield between files (UPG-REST-STARVATION requirement
                # #1's "explicit yields") — this worker already runs off the
                # request-handling thread, but ceding the GIL between files gives
                # request handlers a scheduling opportunity on every iteration of
                # a large batch rather than only between embed sub-batches deep
                # inside index_file/delete_file.
                time.sleep(0)
            if self._searcher_refresh:
                self._searcher_refresh()
            with self._batch_lock:
                self._last_batch_duration_s = time.monotonic() - t0
                self._batch_running = False
                next_paths = self._pending_batch
                self._pending_batch = set()
            # UPG-WATCH-REVERT-CHURN diagnostics: one line per debounced churn
            # job with its size, so a large watcher-driven reindex is visible
            # in the log instead of a silent multi-minute gap.
            logger.info(
                "watcher: churn batch done — %d files ingested (%d chunks re-embedded), "
                "%d files de-indexed (%d chunks deleted), %.1fs",
                indexed_files, indexed_chunks, deleted_files, deleted_chunks,
                self._last_batch_duration_s,
            )
            if next_paths:
                self._maybe_run_or_defer(next_paths)

        thread = threading.Thread(target=run, daemon=True)
        self._batch_thread = thread
        thread.start()

    def watcher_status(self) -> dict:
        """Observability fields for `status` (UPG-WATCHER-PRESSURE-GOVERNOR):
        so runaway edit-stream churn is visible instead of silent."""
        with self._lock:
            burst_mode = self._burst_mode
            pending = len(self._debounce_pending_paths) + len(self._burst_pending)
        with self._batch_lock:
            batch_running = self._batch_running
            pending += len(self._pending_batch)
            last_duration_ms = int(self._last_batch_duration_s * 1000)
        return {
            "watcher_burst_mode": burst_mode,
            "watcher_pending_files": pending,
            "watcher_batch_running": batch_running,
            "watcher_last_batch_duration_ms": last_duration_ms,
        }

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
                        self._schedule_change(key, "modify")

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
        self._gitignore_patterns = self._collect_gitignore_patterns()
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
        with self._lock:
            if self._burst_quiet_timer is not None:
                self._burst_quiet_timer.cancel()
                self._burst_quiet_timer = None
        self._debounce.cancel_all()
        if self._observer:
            self._observer.stop()
            self._observer.join()
