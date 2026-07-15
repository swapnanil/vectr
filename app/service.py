"""Business logic: owns the indexer, searcher, watcher, and memory layer lifecycle."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.config import (
    STRATEGY_DEFAULT_BM25_WEIGHT,
    STRATEGY_DEFAULT_SEMANTIC_WEIGHT,
    SEARCH_IDENTIFIER_HINT_ENABLED,
    SEARCH_IDENTIFIER_HINT_MAX_IDENTIFIERS,
    SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS,
    SEARCH_IDENTIFIER_HINT_NEARMISS_ENABLED,
    SEARCH_IDENTIFIER_HINT_NEARMISS_MAX,
    EMBEDDING_DEFAULT_MODEL,
    EVICTION_MAX_TRACKED_SESSIONS,
    HOOKS_LOG_INJECTIONS,
    HOOKS_LOG_CHARS_PER_TOKEN,
)
from agent.eviction_advisor import EvictionAdvisor
from agent.trigger_engine import TriggerFireLedger
from agent.version_stamp import compute_version_stamp

logger = logging.getLogger(__name__)

_DB_DIR_ENV = "VECTR_DB_DIR"


def _default_db_dir(workspace_root: str) -> str:
    """Store DB files in ~/.cache/vectr/<workspace-hash>/, owner-only (0700).

    The cache holds the plaintext code index and (unless encrypted) working-
    memory notes, so both the shared parent and the per-workspace directory are
    restricted to the owner on POSIX hosts (see agent/fs_permissions.py)."""
    import hashlib
    from agent.fs_permissions import secure_dir
    slug = hashlib.md5(workspace_root.encode()).hexdigest()[:12]
    cache_root = Path.home() / ".cache" / "vectr"
    secure_dir(cache_root)
    db_dir = secure_dir(cache_root / slug)
    return str(db_dir)


_MEMORY_ONLY_MSG = (
    "vectr is in memory-only mode for this workspace — semantic search and the "
    "symbol graph are disabled; memory tools (remember/recall/snapshot) and hooks "
    "are active."
)

_SEARCH_ONLY_MSG = (
    "vectr is in search-only mode for this workspace — working-memory tools "
    "(remember/recall/forget/snapshot) are disabled; semantic search, locate, "
    "trace and map are active."
)

# UPG-STDIO-MEMORY-READY: shown by the REST/MCP-HTTP transport (see routes.py)
# for search-touching endpoints called before phase 2 (embedder/indexer/
# searcher/watcher/symbol-graph construction) finishes. Memory tools
# (remember/recall/forget/status/snapshot) never show this — they work from
# process start, on every transport, because the working-memory store is
# constructed without an embedder in phase 1. Mirrors stdio transport's own
# "_STILL_STARTING_MSG" (integrations/mcp_server/_stdio.py) for the same
# state, kept as a separate constant since the two transports' callers never
# share a code path.
_STILL_INITIALIZING_MSG = (
    "vectr is still starting up (loading the embedding model / building the "
    "workspace index) — search, locate, trace, and map are not yet available. "
    "Memory tools (remember/recall/forget/snapshot/status) are ready now. Try "
    "again in a few moments."
)

# UPG-STDIO-MEMORY-READY: appended to a vectr_recall(query=...) result when
# the call landed during the phase-2 warm-up window — the embedding model is
# still loading/downloading in the background, so this result used the
# lexical (SQL LIKE) fallback rather than semantic ranking. State-based only
# (gated on VectrService.embedder_ready, never on the query's content):
# recall() already chooses lexical vs. semantic purely from whether an
# embedder is attached, so this notice reports that same state truthfully
# rather than re-deriving it. Never appears once the embedder has attached —
# `recall(query=None)` (index/list mode) never appends it either, since
# there is no ranking to degrade.
_EMBEDDER_LOADING_RECALL_NOTICE = (
    "\n\n[semantic ranking unavailable: the embedding model is still loading "
    "or downloading in the background — these are lexical (keyword) matches "
    "only. Re-run this same vectr_recall query later for semantically-ranked "
    "results.]"
)

# UPG-CTX-EVICT: shared note appended to a vectr_fetch/`/v1/fetch` response
# whenever at least one requested id came back missing — the file most
# likely changed since indexing (edited, moved, or deleted), shifting or
# removing that chunk's id. Re-running vectr_search recovers the current
# content under a fresh id.
_FETCH_NOT_FOUND_NOTE = (
    "One or more requested ids were not found — the file likely changed "
    "since indexing (edited, moved, or deleted), which shifts or removes "
    "chunk ids. Re-run vectr_search to get current ids."
)


class VectrService:
    """Singleton-style service. Create once at startup; shared via FastAPI app state."""

    def __init__(
        self,
        workspace_root: str,
        port: int = 8765,
        extra_roots: list[str] | None = None,
        memory_only: bool = False,
        search_only: bool = False,
        workspace_explicit: bool = False,
        configure_ide: bool = True,
        defer_search_init: bool = False,
    ) -> None:
        """`defer_search_init` (UPG-STDIO-MEMORY-READY): when True, this
        constructor runs ONLY the fast, synchronous phase below — no
        embedding model load, no code indexer/searcher/watcher/symbol-graph
        construction, no IDE-config writes. Working-memory tools (remember/
        recall/forget/status/snapshot/snapshot_list) are fully usable the
        moment the constructor returns. The caller must then call
        `complete_search_init()` (typically on a background thread) to run
        the deferred phase 2. Defaults to False so every existing caller —
        including the 17+ test modules that construct this class directly —
        gets today's fully-synchronous, single-phase behavior unchanged;
        only the stdio transport (main.py) and the HTTP daemon (api.py)
        opt in, since only they need memory tools to answer before the
        embedding model finishes loading/downloading.
        """
        from agent.working_context_store import WorkingContextStore
        from integrations.workspace_detect import find_workspace_root

        # UPG-WS-ROOT-MISDETECT: an explicitly-given workspace path (CLI
        # positional arg or --path flag) always wins verbatim — it must never
        # be silently replaced by the enclosing git repo's root. The
        # git-toplevel walk-up in find_workspace_root only applies when the
        # caller gave no path at all (workspace_explicit=False, e.g. a bare
        # `vectr start` defaulting to cwd).
        if workspace_explicit:
            self._workspace_root = str(Path(workspace_root).resolve())
        else:
            self._workspace_root = find_workspace_root(workspace_root)
        self._extra_roots: list[str] = list(extra_roots or [])
        self._port = port
        self._configure_ide = configure_ide
        # Some embedding models are asymmetric — search queries must be embedded
        # via the provider's embed_query (registered "query" prompt), never embed().
        # See agent/indexer/_types.py:LocalEmbedProvider for the query/document split,
        # detected from the loaded model itself rather than hardcoded per model.
        self._embed_model = os.getenv("VECTR_EMBED_MODEL", EMBEDDING_DEFAULT_MODEL)
        # Memory-only mode: code indexing + file watcher are disabled.
        # Reads from env (propagated by _do_start) or from the constructor arg.
        self._memory_only: bool = memory_only or (os.getenv("VECTR_MEMORY_ONLY", "") == "1")
        # Search-only mode: the dual of memory-only — indexing/watcher run normally,
        # but the working-memory layer is disabled (no notes DB is created).
        # Reads from env (propagated by _do_start) or from the constructor arg.
        self._search_only: bool = search_only or (os.getenv("VECTR_SEARCH_ONLY", "") == "1")
        if self._memory_only and self._search_only:
            raise ValueError(
                "VectrService cannot run in both memory_only and search_only mode simultaneously"
            )

        db_dir = os.getenv(_DB_DIR_ENV) or _default_db_dir(self._workspace_root)
        # UPG-STDIO-MEMORY-READY: the working-memory store (below) is now the
        # FIRST thing constructed against db_dir — previously CodeIndexer ran
        # first and its ChromaDB PersistentClient happened to create the
        # directory as a side effect. `_default_db_dir` already creates it
        # via `secure_dir`, but a VECTR_DB_DIR override (e.g. a hosted
        # deployment's mounted volume) may point at a path that doesn't exist
        # yet — ensure it explicitly rather than depend on construction order.
        from agent.fs_permissions import secure_dir
        secure_dir(db_dir)
        self._db_dir = db_dir

        logger.info("Initialising Vectr for workspace: %s (db: %s)", self._workspace_root, db_dir)

        # Version stamp (UPG-CLI-DAEMON-VERSION-SKEW): computed once at daemon
        # startup and never refreshed for the lifetime of this process — the
        # whole point is to detect that *this running process* predates a
        # source upgrade the CLI now sees on disk.
        self._version_stamp = compute_version_stamp()

        # Phase-2 objects (UPG-STDIO-MEMORY-READY): None until
        # _init_search_layer() runs — inline below by default, or later via
        # complete_search_init() when defer_search_init=True. Every reader
        # of these (status(), last_indexed, search/locate/trace/fetch/map)
        # either tolerates None (memory-facing code never touches them) or
        # is itself gated on `fully_ready` by the caller (MCP dispatch / REST
        # routes), so a phase-1-only service never raises — it just reports
        # "not built yet" truthfully.
        self._indexer = None
        self._searcher = None
        self._watcher = None
        self._passport_store = None
        self._symbol_graph = None

        # Memory layer — constructed WITHOUT an embedder (UPG-STDIO-MEMORY-READY):
        # remember/recall/forget/status/snapshot work immediately, on every
        # transport, before the embedding model has loaded. Semantic ranking
        # (and the note-vector store, sharing the code index's ChromaDB
        # client) attaches once phase 2 completes — see _init_search_layer()
        # and WorkingContextStore.attach_embedder(). Until then, recall()
        # transparently uses its existing lexical SQL LIKE fallback. In
        # search-only mode the store is never constructed at all
        # (UPG-SEARCH-ONLY-MODE): no notes DB file and no 'working_memory'
        # Chroma collection are created for a workspace that never writes a note.
        self._context_store: WorkingContextStore | None = None
        if not self._search_only:
            self._context_store = WorkingContextStore(db_dir)

        # Eviction advisor (UPG-EVICT-SESSION-SCOPE): one advisor per calling
        # MCP session, so one session never sees chunks retrieved by another.
        # `self._eviction_advisor` remains the shared advisor used when no
        # session_id is known (REST callers, backwards-compat callers) — every
        # session-scoped lookup below falls back to it when session_id is None.
        self._eviction_threshold_tokens = int(os.getenv("VECTR_EVICT_THRESHOLD", "4000"))
        self._eviction_advisor = EvictionAdvisor(
            eviction_threshold_tokens=self._eviction_threshold_tokens
        )
        self._session_advisors: dict[str, EvictionAdvisor] = {}

        # Per-session trigger fire ledger (TRIGGER-ENGINE wave 1,
        # bm2-design-skeleton.md §3) — mirrors the EvictionAdvisor registry
        # immediately above. Unlike that registry there is no daemon-global
        # fallback ledger: fire-dedup is only meaningful within one session's
        # identity, so a caller with no session_id (see `_ledger_for`) simply
        # gets no suppression rather than sharing state across callers.
        self._trigger_ledgers: dict[str, TriggerFireLedger] = {}

        self._indexing = False
        self._index_thread: threading.Thread | None = None
        self._index_lock = threading.Lock()

        # Per-tool call counters — tracked across all callers (parent + sub-agents)
        # so benchmark tooling can read accurate counts via GET /v1/call_counts.
        self._call_counts: dict[str, int] = {}
        self._call_counts_lock = threading.Lock()

        # Per-hook-kind injection counters (UPG-HOOK-INJECT-OBSERVABILITY): how
        # many times each hook's recall actually returned notes to inject,
        # since this process started. Recorded from `recall(hook_event=...)`,
        # surfaced in `status()` / `vectr status`.
        self._hook_injection_counts: dict[str, int] = {}
        self._hook_injection_lock = threading.Lock()

        # Proactive context (UPG-PRO) — per-channel injection counters (like the
        # hook counters above) + a per-session dedup ledger shared across the
        # matcher/gate for the lifetime of this process. Lazily built.
        self._proactive_injection_counts: dict[str, int] = {}
        self._proactive_injection_lock = threading.Lock()
        self._proactive_ledger = None  # agent.proactive.gate.LedgerStore

        # Org-wide vectr-artifact cache (UPG-PRO caching) — off unless enabled.
        # Bumped monotonically on every note mutation so recall artifacts keyed
        # by the notes epoch invalidate the moment a note changes.
        self._notes_mutation_seq = 0
        self._notes_mutation_lock = threading.Lock()
        self._artifact_cache = None  # agent.proactive.cache.ArtifactCache
        try:
            from agent.proactive.settings import ProactiveSettings
            _pro = ProactiveSettings.from_env()
            if _pro.cache_enabled:
                from agent.proactive.cache import ArtifactCache
                self._artifact_cache = ArtifactCache(
                    max_entries=_pro.cache_max_entries,
                    ttl_seconds=_pro.cache_ttl_seconds,
                    similarity_threshold=_pro.cache_similarity_threshold,
                )
        except Exception:
            self._artifact_cache = None

        # Adaptive strategy — computed after first index, defaults until then
        from agent.strategy_selector import RetrievalStrategy
        self._strategy: RetrievalStrategy | None = None

        # Phase-2 readiness signals (UPG-STDIO-MEMORY-READY): `embedder_ready`
        # flips the moment CodeIndexer's embed provider finishes loading
        # (inside _init_search_layer, before the slower searcher/watcher/
        # symbol-graph work) — used to gate the vectr_recall lexical-fallback
        # notice. `fully_ready` flips once ALL of phase 2 has completed — used
        # to gate search/locate/trace/map/fetch across every transport. Both
        # are set before this constructor returns in the default
        # (defer_search_init=False) path, so existing callers never observe
        # a not-ready state.
        self._embedder_ready = threading.Event()
        self._fully_ready = threading.Event()
        # UPG-SHUTDOWN-INIT-RACE: set by shutdown(). Phase 2 runs on a
        # background thread for deferred callers, so shutdown can arrive
        # before or DURING _init_search_layer — this event makes a
        # not-yet-started phase 2 a no-op and a mid-flight one tear down
        # whatever it just constructed instead of orphaning it.
        self._shutdown_requested = threading.Event()

        if not defer_search_init:
            self._init_search_layer()

    def _init_search_layer(self) -> None:
        """Phase 2 (UPG-STDIO-MEMORY-READY): the slow, model-loading half of
        construction — CodeIndexer (embedding model), CodeSearcher,
        CodeWatcher, reranker warm-up, the L1 passport store, the L2 symbol
        graph, and (unless search-only) attaching the now-loaded embedder to
        the already-live working-memory store. Runs inline from __init__ by
        default; deferred callers (stdio, HTTP daemon) run it explicitly via
        `complete_search_init()`, typically on a background thread, after
        phase 1 has already made memory tools available.
        """
        from agent.indexer import CodeIndexer
        from agent.searcher import CodeSearcher
        from agent.watcher import CodeWatcher
        from agent.cartographer import PassportStore
        from agent.symbol_graph import SymbolGraph
        from integrations.vscode_bridge import configure_all

        # L3 — content retrieval (existing)
        # db_path scopes ChromaDB under the same configured db_dir as all other stores
        self._indexer = CodeIndexer(
            self._workspace_root,
            embed_model=self._embed_model,
            db_path=str(Path(self._db_dir) / "chroma"),
            extra_roots=self._extra_roots,
        )
        # The embedding model has now loaded. Attach it to the memory layer
        # immediately — before the slower searcher/watcher/symbol-graph work
        # below — so notes written during the warm-up window get semantic
        # recall, and any note missing a vector gets backfilled, as soon as
        # possible rather than only once the rest of phase 2 finishes.
        if self._context_store is not None:
            self._context_store.attach_embedder(
                embed_fn=self._indexer.embed_texts,
                embed_query_fn=self._indexer.embed_query_batch,
                notes_chroma_client=self._indexer.chroma_client,
                embed_model=self._indexer.embed_model,
            )
        self._embedder_ready.set()

        self._searcher = CodeSearcher(self._indexer)
        self._watcher = CodeWatcher(self._indexer, searcher_refresh_fn=self._searcher.refresh_bm25)
        # UPG-RERANKER-HF-NETWORK: warm the reranker at startup, alongside the
        # embedder already loaded synchronously above (CodeIndexer's constructor
        # instantiates the embed provider). This moves the cross-encoder's
        # model-load cost out of the first vectr_search call. Skipped in
        # memory-only mode: there is no code index, search is disabled, and
        # there is nothing to rerank.
        if not self._memory_only:
            self._searcher.warm_reranker()

        # L1 — codebase passport (AI-written, stored by vectr_map_save)
        self._passport_store = PassportStore(self._db_dir)

        # L2 — symbol graph
        self._symbol_graph = SymbolGraph(self._db_dir)
        # TRIGGER-ENGINE wave 2b: upgrade the memory layer to the S (symbol)
        # trigger primitive now that the symbol graph exists — mirrors the
        # attach_embedder() upgrade-after-construction above. Memory-only
        # daemons (self._context_store is not None but this phase never
        # runs) and the warm-up window before this line executes both leave
        # the store's resolver unattached, so a symbol trigger deterministically
        # does not fire until this call lands — never an error.
        if self._context_store is not None:
            self._context_store.attach_symbol_resolver(self._symbol_graph)
        # ARCH-1b: seed the searcher's importance prior from the persisted
        # symbol_importance table so a restart over an already-indexed workspace
        # ranks with importance immediately (empty until ARCH-1a has run once).
        self._searcher.set_file_importance(
            self._symbol_graph.file_importance(self._workspace_root)
        )
        # ARCH-2: seed the searcher's class-importance prior from the persisted
        # class_importance table the same way (empty until ARCH-2 has run once).
        self._searcher.set_class_importance(
            self._symbol_graph.class_importance(self._workspace_root)
        )
        # UPG-TESTPATH-FRAMEWORK-MISCLASS (F58): seed the searcher's test-framework
        # fan-in exemption map from the persisted file_fan_in table the same way
        # (empty until it has run once).
        self._searcher.set_file_fan_in(
            self._symbol_graph.file_fan_in(self._workspace_root)
        )

        # IDE config files point at this process's HTTP port — meaningless for a
        # caller that has no HTTP port (e.g. the stdio transport) and potentially
        # disruptive for a workspace it doesn't own (e.g. a hosting platform's
        # mounted container filesystem). Callers without a real port opt out.
        if self._configure_ide:
            for root in self._indexer.all_roots:
                configure_all(str(root), self._port)

        # UPG-SHUTDOWN-INIT-RACE: shutdown() raced this phase-2 run (e.g. an
        # instant client disconnect during warm-up). Tear down what was just
        # constructed rather than orphaning it — watcher.stop() and
        # indexer.close() are both idempotent, so overlapping with
        # shutdown()'s own stop/close pass is safe — and never report
        # fully_ready on a shut-down service.
        if self._shutdown_requested.is_set():
            if self._watcher is not None:
                self._watcher.stop()
            if self._indexer is not None:
                self._indexer.close()
            return

        self._fully_ready.set()

    def complete_search_init(self) -> None:
        """Run phase 2 for a service constructed with `defer_search_init=True`
        (UPG-STDIO-MEMORY-READY). Callers (stdio transport, HTTP daemon
        lifespan) invoke this once, typically on a background thread, right
        after phase 1 has already made memory tools available.

        Idempotent: a no-op if phase 2 has already completed — either because
        this was already called once, or because the service was constructed
        with `defer_search_init=False` (the default) and phase 2 already ran
        inline in `__init__`. Without this guard, a caller that (accidentally,
        or via test/dependency-injection wiring) invokes this on an
        already-`fully_ready` service would reconstruct the indexer/searcher/
        watcher a second time and leak the discarded watcher's background
        threads.

        Also a no-op after shutdown() (UPG-SHUTDOWN-INIT-RACE): a service
        that was shut down during the warm-up window must not go on to
        construct the search layer it can no longer release.
        """
        if self._shutdown_requested.is_set():
            return
        if self._fully_ready.is_set():
            return
        self._init_search_layer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def memory_only(self) -> bool:
        """True when this daemon runs in memory-only mode (no indexing/watcher)."""
        return self._memory_only

    @property
    def search_only(self) -> bool:
        """True when this daemon runs in search-only mode (no working-memory layer)."""
        return self._search_only

    @property
    def fully_ready(self) -> bool:
        """True once phase 2 (embedder/indexer/searcher/watcher/symbol-graph
        construction) has completed (UPG-STDIO-MEMORY-READY). Search-touching
        tools/endpoints (search/locate/trace/map/fetch/evict-hint) are gated
        on this across every transport. Memory tools are never gated on it —
        they work from phase 1. Always True immediately once a
        `defer_search_init=False` (the default) construction returns."""
        return self._fully_ready.is_set()

    @property
    def embedder_ready(self) -> bool:
        """True once the embedding model has loaded and been attached to the
        working-memory store (UPG-STDIO-MEMORY-READY) — flips before the rest
        of phase 2 (searcher/watcher/symbol-graph) finishes. Used to gate the
        vectr_recall lexical-fallback notice: state-based only, never
        query-content-based. Always True immediately once a
        `defer_search_init=False` (the default) construction returns."""
        return self._embedder_ready.is_set()

    def start_background_index(self) -> None:
        """Kick off workspace indexing in a background thread.

        In memory-only mode, the index thread and file watcher are skipped.
        In search-only mode, indexing and the watcher run normally, but the
        notes-TTL purge is skipped — there is no context store to purge.
        Otherwise, the notes-TTL purge runs so expired notes are cleaned up at
        startup.
        """
        if self._watcher is None:
            raise RuntimeError(
                "start_background_index() called before phase 2 completed — "
                "call complete_search_init() first (see defer_search_init)"
            )
        if self._indexing:
            return
        self._indexing = True

        # apply TTL to working notes at startup if VECTR_NOTES_TTL_DAYS is set.
        # Search-only mode has no context store — nothing to purge.
        ttl_days_str = os.getenv("VECTR_NOTES_TTL_DAYS", "")
        if ttl_days_str and self._context_store is not None:
            try:
                ttl = float(ttl_days_str)
                deleted = self._context_store.purge_expired_notes(self._workspace_root, ttl)
                if deleted:
                    logger.info("purged %d expired notes (TTL=%.1f days)", deleted, ttl)
            except (ValueError, Exception):
                logger.warning("VECTR_NOTES_TTL_DAYS is not a valid float: %r", ttl_days_str)

        if self._memory_only:
            logger.info(
                "memory-only mode: code indexing + file watcher disabled; "
                "working-memory tools + hooks active"
            )
            self._indexing = False
            return

        self._index_thread = threading.Thread(target=self._do_index, daemon=True)
        self._index_thread.start()
        self._watcher.start()

    def _do_index(self) -> None:
        with self._index_lock:
            try:
                logger.info("Starting workspace index...")
                files, chunks = self._indexer.index_workspace()
                self._searcher.refresh_bm25()
                logger.info("Indexed %d files → %d chunks", files, chunks)

                # audit index event
                from agent.working_context_store import audit as _audit
                _audit("INDEX", workspace=self._workspace_root, files=files, chunks=chunks)

                self._build_symbol_graph()
                self._refresh_strategy()

            except Exception:
                logger.exception("Indexing failed")
            finally:
                self._indexing = False

    def _refresh_strategy(self) -> None:
        try:
            from agent.strategy_selector import fingerprint, select_strategy
            fp = fingerprint(self._workspace_root, self._indexer.indexed_file_paths)
            self._strategy = select_strategy(fp)
            logger.info(
                "Retrieval strategy: sem=%.2f bm25=%.2f graph_first=%s — %s",
                self._strategy.semantic_weight,
                self._strategy.bm25_weight,
                self._strategy.graph_first,
                self._strategy.rationale,
            )
        except Exception:
            logger.exception("Strategy selection failed (non-fatal)")

    def _build_symbol_graph(self) -> None:
        """Rebuild the symbol graph from the files the indexer already walked."""
        try:
            from agent.symbol_graph import SYMBOL_LANGUAGES, available_symbol_languages
            # Warn loudly when a declared grammar is not importable in this
            # environment so the degraded state is never silent.
            missing = sorted(SYMBOL_LANGUAGES - available_symbol_languages())
            if missing:
                logger.warning(
                    "tree-sitter grammar(s) not importable: %s — locate/trace disabled for "
                    "these; reinstall vectr deps (pip install -e .) to enable",
                    ", ".join(missing),
                )

            # Reuse the indexer's file list — avoids a second expensive walk and
            # guarantees the symbol graph covers exactly the same files as the
            # vector index (same filters, same exclusions).
            file_paths = self._indexer.indexed_file_paths
            # UPG-8.7: a toolchain change (vectr upgrade / new parser / model)
            # means the persisted graph is stale; we always full-rebuild here, but
            # surface *why* so a stale graph is never silently served.
            if self._symbol_graph.is_stale(self._workspace_root, self._embed_model):
                logger.info("Symbol graph stale or toolchain changed — full rebuild")
            stats = self._symbol_graph.build_for_workspace(
                self._workspace_root, file_paths, embed_model=self._embed_model,
            )
            logger.info(
                "Symbol graph: %d symbols, %d edges across %d files (%d failed, complete=%s)",
                stats["symbols"], stats["edges"], stats["files"],
                stats["failed"], stats["complete"],
            )
            # ARCH-1b: hand the freshly-computed file-level importance (ARCH-1a) to
            # the searcher so the ranking prior reflects the current graph.
            self._searcher.set_file_importance(
                self._symbol_graph.file_importance(self._workspace_root)
            )
            # ARCH-2: same for the freshly-computed class-level importance.
            self._searcher.set_class_importance(
                self._symbol_graph.class_importance(self._workspace_root)
            )
            # UPG-TESTPATH-FRAMEWORK-MISCLASS (F58): same for the freshly-computed
            # test-framework fan-in exemption map.
            self._searcher.set_file_fan_in(
                self._symbol_graph.file_fan_in(self._workspace_root)
            )
        except Exception:
            logger.exception("Symbol graph build failed (non-fatal)")

    def save_map(self, summary: str, overwrite: bool = False) -> dict:
        """
        Persist an AI-written codebase passport.
        Called via vectr_map_save — the AI editor has synthesised the summary
        after reading the raw metadata returned by vectr_map on first call.

        Does NOT silently overwrite an existing passport (UPG-6.2): if one is
        already saved and `overwrite` is not set, the write is a no-op and the
        existing summary is returned so the caller can decide whether to
        retry with `overwrite=True`.

        Returns {"saved": bool, "existing_summary": str | None}.
        """
        existing = self._passport_store.load()
        if existing and existing.get("summary") and not overwrite:
            logger.info("Passport save skipped — one already exists and overwrite was not set")
            return {"saved": False, "existing_summary": existing["summary"]}
        self._passport_store.save_summary(summary, self._workspace_root)
        logger.info("Passport saved by AI editor (%d chars)", len(summary))
        return {"saved": True, "existing_summary": None}

    def shutdown(self) -> None:
        # UPG-STDIO-MEMORY-READY: a service constructed with
        # defer_search_init=True can be asked to shut down (e.g. stdin EOF)
        # while still in the phase-2 background-construction window, before
        # the watcher/indexer exist — nothing to stop/close yet, not an error.
        # UPG-SHUTDOWN-INIT-RACE: the event must be set BEFORE the
        # None-checks below — a phase 2 that assigns watcher/indexer after
        # this read then sees the event at its own end and tears them down
        # itself; whichever side observes the other's write cleans up.
        self._shutdown_requested.set()
        if self._watcher is not None:
            self._watcher.stop()
        # Release the indexer's ChromaDB client (and the notes store's, which
        # shares the same underlying client — see attach_embedder above).
        # Without this, every VectrService that ever reaches phase 2 leaks a
        # native worker-thread pool for the rest of the process's life.
        if self._indexer is not None:
            self._indexer.close()

    # ------------------------------------------------------------------
    # Call counters — all callers (parent + sub-agents) hit the same server,
    # so these are accurate totals regardless of how many agents spawned.
    # ------------------------------------------------------------------

    def increment_call_count(self, tool_name: str) -> None:
        with self._call_counts_lock:
            self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

    def get_call_counts(self) -> dict[str, int]:
        with self._call_counts_lock:
            return dict(self._call_counts)

    def reset_call_counts(self) -> dict[str, int]:
        with self._call_counts_lock:
            old = dict(self._call_counts)
            self._call_counts.clear()
            return old

    # ------------------------------------------------------------------
    # Hook injection counters (UPG-HOOK-INJECT-OBSERVABILITY): recorded from
    # `recall()` below, only when a hook-declared call actually returned
    # notes. A working memory system and a dead one look identical to the
    # human until this makes injection visible — read via `status()`.
    # ------------------------------------------------------------------

    def _record_hook_injection(self, hook_event: str, notes: str) -> None:
        """Count one injection for `hook_event`, and optionally log it.

        No-op when `notes` is empty — an empty-result hook call (e.g. a
        SessionStart on a fresh workspace with no directives yet) injected
        nothing, so it must not count as an injection.
        """
        if not notes:
            return
        with self._hook_injection_lock:
            self._hook_injection_counts[hook_event] = (
                self._hook_injection_counts.get(hook_event, 0) + 1
            )
        if HOOKS_LOG_INJECTIONS:
            self._append_hook_injection_log(hook_event, notes)

    def _append_hook_injection_log(self, hook_event: str, notes: str) -> None:
        """Append one line to ~/.vectr/logs/<workspace-hash>.hooks.log.

        Failure here must never break recall — any error is logged and
        swallowed, never raised.
        """
        try:
            from agent.instance_registry import workspace_hash
            log_dir = Path.home() / ".vectr" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{workspace_hash(self._workspace_root)}.hooks.log"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            tokens = len(notes) // HOOKS_LOG_CHARS_PER_TOKEN
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts}\t{hook_event}\ttokens={tokens}\n")
        except Exception:
            logger.exception("failed to write hook injection log")

    def get_hook_injection_counts(self) -> dict[str, int]:
        with self._hook_injection_lock:
            return dict(self._hook_injection_counts)

    # ------------------------------------------------------------------
    # L3 — search and index operations
    # ------------------------------------------------------------------

    def index(self, path: str, force: bool = False) -> tuple[int, int, int]:
        """Index a path. Returns (files_indexed, total_chunks, elapsed_ms)."""
        with self._index_lock:
            t0 = time.monotonic()
            target = Path(path).resolve()
            if target.is_file():
                self._indexer.index_file(str(target))
                self._searcher.refresh_bm25()
                self._symbol_graph.index_file(self._workspace_root, str(target))
                files, chunks = 1, self._indexer.total_chunks
            else:
                files, chunks = self._indexer.index_workspace(force=force)
                self._searcher.refresh_bm25()
                self._build_symbol_graph()
                self._refresh_strategy()
            elapsed = int((time.monotonic() - t0) * 1000)
            from agent.working_context_store import audit as _audit
            _audit("INDEX", workspace=self._workspace_root, files=files, chunks=chunks)
            return files, chunks, elapsed

    def _advisor_for(self, session_id: str | None) -> EvictionAdvisor:
        """Look up (or create) the EvictionAdvisor for the calling session
        (UPG-EVICT-SESSION-SCOPE). `session_id=None` (REST callers, or an MCP
        transport that never sent one) shares one daemon-global advisor —
        unchanged backwards-compat behaviour. A known session_id gets its own
        advisor so it never sees chunks another session retrieved. The
        registry is LRU-bounded to EVICTION_MAX_TRACKED_SESSIONS so a
        long-running daemon serving many short-lived sessions can't grow it
        without bound."""
        if not session_id:
            return self._eviction_advisor
        advisor = self._session_advisors.get(session_id)
        if advisor is None:
            if len(self._session_advisors) >= EVICTION_MAX_TRACKED_SESSIONS:
                oldest_id = next(iter(self._session_advisors))
                del self._session_advisors[oldest_id]
            advisor = EvictionAdvisor(eviction_threshold_tokens=self._eviction_threshold_tokens)
            self._session_advisors[session_id] = advisor
        else:
            # Refresh recency for the LRU bound above.
            del self._session_advisors[session_id]
            self._session_advisors[session_id] = advisor
        return advisor

    def _ledger_for(self, session_id: str | None) -> TriggerFireLedger | None:
        """Look up (or create) the per-session `TriggerFireLedger` (TRIGGER-
        ENGINE wave 1, bm2-design-skeleton.md §3), mirroring `_advisor_for`
        immediately above. `session_id=None` returns None rather than a
        shared fallback ledger — fire-dedup only makes sense within one
        session's identity; a caller with no session_id gets no suppression
        at all, never cross-session suppression. LRU-bounded to the same
        EVICTION_MAX_TRACKED_SESSIONS cap for the same reason as the advisor
        registry (a long-running daemon serving many short-lived sessions)."""
        if not session_id:
            return None
        ledger = self._trigger_ledgers.get(session_id)
        if ledger is None:
            if len(self._trigger_ledgers) >= EVICTION_MAX_TRACKED_SESSIONS:
                oldest_id = next(iter(self._trigger_ledgers))
                del self._trigger_ledgers[oldest_id]
            ledger = TriggerFireLedger()
            self._trigger_ledgers[session_id] = ledger
        else:
            del self._trigger_ledgers[session_id]
            self._trigger_ledgers[session_id] = ledger
        return ledger

    def reset_trigger_ledger(self, session_id: str | None) -> None:
        """Reset one session's fire-dedup ledger (TRIGGER-ENGINE
        bm2-design-skeleton.md §3: "cleared on compaction"). A caller with no
        session_id, or a session that has never fired a trigger yet, is a
        no-op."""
        if session_id and session_id in self._trigger_ledgers:
            self._trigger_ledgers[session_id].reset()

    def fire_triggers(
        self,
        event: str | None = None,
        file_path: str | None = None,
        session_id: str | None = None,
    ):
        """Live per-memory trigger evaluation (TRIGGER-ENGINE) for one
        lifecycle moment. Returns the ordered list of `agent.trigger_engine
        .FireResult` for every note that fires — see
        `WorkingContextStore.fire()` for the full contract (evaluation,
        staleness caveats folded in, the one shared total order, per-session
        dedup via `_ledger_for`, scope enforcement, and the `last_fired`
        cooldown stamp).

        Raw evaluation entry point — returns `FireResult`s, not rendered
        text; `fire_and_recall()` below is the rendering wrapper the live
        hook pipeline (main.py's `cmd_hook`) actually calls. Kept as a
        separate, still-callable capability for any caller that wants the
        unrendered results directly."""
        self._require_memory_layer()
        return self._context_store.fire(
            self._workspace_root,
            event=event,
            file_path=file_path,
            ledger=self._ledger_for(session_id),
            session_id=session_id,
        )

    def fire_and_recall(
        self,
        event: str | None = None,
        events: list[str] | None = None,
        file_path: str | None = None,
        session_id: str | None = None,
        surface: str = "mcp",
        hook_event: str | None = None,
    ) -> str:
        """Rendering wrapper around `WorkingContextStore.fire_and_format()`
        (TRIGGER-ENGINE wave 2a) — evaluate one or more lifecycle events,
        pack the fired notes against the per-session cumulative injection
        budget, and return the rendered text ready for hook injection.

        `hook_event`, if given, records the delivery via `_record_hook_injection`
        (UPG-HOOK-INJECT-OBSERVABILITY) — the same counter `recall()`'s own
        `hook_event` parameter feeds — so a trigger-fired delivery shows up
        in `status()`'s hook injection counts exactly like a semantic-recall
        hook delivery does."""
        self._require_memory_layer()
        text, _ = self._context_store.fire_and_format(
            self._workspace_root,
            event=event,
            events=events,
            file_path=file_path,
            session_id=session_id,
            ledger=self._ledger_for(session_id),
            surface=surface,
        )
        if hook_event is not None:
            self._record_hook_injection(hook_event, text)
        return text

    def search(
        self, query: str, n_results: int = 10, language: str | None = None
    ) -> tuple[list, int]:
        """Returns (SearchResult list, query_time_ms).

        Does NOT record into any eviction advisor (UPG-EVICT-SESSION-SCOPE) —
        recording happens exactly once, at render time, against the calling
        session's advisor (see MCP dispatch's vectr_search handler /
        `record_results`). A REST `/v1/search` caller has no session_id and
        gets pure retrieval with no eviction-tracking side effect.
        """
        from agent.working_context_store import audit as _audit

        # Org-wide artifact cache (UPG-PRO): identical query against the same
        # code index -> identical results. Keyed by the code index epoch, so a
        # re-index invalidates. Off unless proactive.cache is enabled. The
        # "what was queried" audit still fires on a hit (the query was issued),
        # but query_ms is reported 0 to reflect that no search actually ran.
        cache = self._artifact_cache
        key = ""
        if cache is not None:
            from agent.proactive.cache import canonical_key
            key = canonical_key(
                "search",
                {"query": query, "n_results": n_results, "language": language},
                self._index_epoch("code"),
            )
            found, cached = cache.get(key)
            if found:
                results, _ = cached
                _audit("SEARCH", workspace=self._workspace_root,
                       query=query[:200], results=len(results))
                return results, 0

        sem_w = self._strategy.semantic_weight if self._strategy else STRATEGY_DEFAULT_SEMANTIC_WEIGHT
        results, query_ms = self._searcher.search(
            query, n_results=n_results, language=language, semantic_weight=sem_w
        )
        # Audit "what was queried" (opt-in; the query text is the whole point of
        # an audit log, so it is only recorded when the operator enables one).
        _audit(
            "SEARCH", workspace=self._workspace_root,
            query=query[:200], results=len(results),
        )
        if cache is not None:
            cache.put(key, (results, query_ms))
        return results, query_ms

    def record_results(self, results: list, session_id: str | None = None) -> None:
        """Record rendered SearchResult chunks into the calling session's
        eviction advisor (UPG-EVICT-SESSION-SCOPE). Call with the exact list
        that was serialized into the tool response — not the pre-truncation
        candidate pool."""
        self._advisor_for(session_id).record_results(results)

    def record_chunk(
        self, *, file_path: str, lines: str, symbol_name: str, content: str,
        chunk_id: str = "", session_id: str | None = None,
    ) -> None:
        """Record one rendered chunk (vectr_fetch, vectr_locate snippets) into
        the calling session's eviction advisor (UPG-EVICT-SESSION-SCOPE)."""
        self._advisor_for(session_id).record(
            file_path=file_path, lines=lines, symbol_name=symbol_name,
            content=content, chunk_id=chunk_id,
        )

    def note_remembered(self, session_id: str | None = None) -> None:
        """Tell the calling session's eviction advisor that vectr_remember
        just succeeded (UPG-REMEMBER-BANNER-FATIGUE) — resets its
        chunks-since-last-remember counter so the escalated ACTION REQUIRED
        directive doesn't immediately re-fire on the next retrieval."""
        self._advisor_for(session_id).note_remembered()

    def indexed_languages(self) -> list[str]:
        """Distinct languages actually present in the index (UPG-3.1)."""
        return self._indexer.indexed_languages()

    def fetch(self, ids: list[str]) -> list[dict]:
        """Deterministic re-fetch by chunk id (UPG-CTX-EVICT part a).

        Disabled in memory-only mode — there is no code index to fetch from,
        the same guard as search/locate/trace.
        """
        if self._memory_only:
            raise RuntimeError(_MEMORY_ONLY_MSG)
        return self._indexer.fetch_chunks(ids)

    def identifier_hint_symbols(self, query: str) -> list:
        """Additive, high-precision symbol-graph hint (UPG-QUERYTYPE-REROUTE).

        Scans the RAW query for identifier-SHAPED tokens (CamelCase,
        snake_case, dotted `Class.method` — a structural transform, never a
        keyword/intent classification) and attempts EXACT symbol-graph
        resolution for each, capped at `search.identifier_hint.max_identifiers`
        candidate tokens. No fuzzy/edit-distance/prefix matching — only an
        exact-name (or exact class-qualified) hit counts, the same exactness
        `locate` uses for a full-name hit. Returns a flat, deduplicated list of
        resolved `Symbol` objects in query order, each capped to
        `search.identifier_hint.max_locations` — empty when the query has no
        identifier-shaped token or none resolves exactly. This never adjusts
        search weights or reorders/prepends anything; it is a pure addition
        appended below the L3 results by the MCP dispatch layer.
        """
        if not SEARCH_IDENTIFIER_HINT_ENABLED:
            return []
        from agent.identifier_hint import extract_identifier_tokens

        tokens = extract_identifier_tokens(query)[:SEARCH_IDENTIFIER_HINT_MAX_IDENTIFIERS]
        resolved: list = []
        seen_ids: set[int] = set()
        for token in tokens:
            result = self._symbol_graph.locate_l2(
                self._workspace_root, token, limit=SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS
            )
            if result.resolution_strategy != "exact":
                continue
            for sym in result.symbols[:SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS]:
                if sym.symbol_id in seen_ids:
                    continue
                seen_ids.add(sym.symbol_id)
                resolved.append(sym)
        return resolved

    def identifier_hint_nearmiss(self, query: str) -> list[tuple[str, list]]:
        """Additive, honestly-labeled near-miss hint (UPG-NEARMISS-SYMBOL-NAMES).

        For each identifier-shaped token attempted by `identifier_hint_symbols`
        (same tokenizer, same `max_identifiers` cap) that does NOT resolve to
        an exact symbol-graph hit, looks up the nearest existing symbol NAMES
        via `SymbolGraph.nearest_symbol_names` — the symbol graph's own cheap,
        deterministic partial-match machinery, never a semantic guess. A token
        that resolves exactly, or that has no near-miss candidate at all, is
        simply absent from the returned list (matching the "nothing appears"
        behaviour of `identifier_hint_symbols`). Capped at
        `search.identifier_hint.nearmiss_max` distinct names TOTAL across the
        whole response, not per token, so the addition stays small. Returns a
        list of `(token, [Symbol, ...])` pairs; every symbol here is inexact
        by construction — the caller (MCP dispatch) must label it as a
        near-miss, never present it as a match.
        """
        if not SEARCH_IDENTIFIER_HINT_ENABLED or not SEARCH_IDENTIFIER_HINT_NEARMISS_ENABLED:
            return []
        from agent.identifier_hint import extract_identifier_tokens

        tokens = extract_identifier_tokens(query)[:SEARCH_IDENTIFIER_HINT_MAX_IDENTIFIERS]
        pairs: list[tuple[str, list]] = []
        budget = SEARCH_IDENTIFIER_HINT_NEARMISS_MAX
        for token in tokens:
            if budget <= 0:
                break
            exact = self._symbol_graph.locate_l2(
                self._workspace_root, token, limit=SEARCH_IDENTIFIER_HINT_MAX_LOCATIONS
            )
            if exact.resolution_strategy == "exact":
                continue
            near = self._symbol_graph.nearest_symbol_names(
                self._workspace_root, token, limit=budget
            )
            if not near:
                continue
            pairs.append((token, near))
            budget -= len(near)
        return pairs

    @property
    def last_indexed(self) -> str:
        """Single source of truth for the last-indexed timestamp string.

        Used by both `status()` and the `/v1/health` route so the two
        endpoints never disagree on freshness (UPG-8.2).

        "never" before phase 2 has built the indexer (UPG-STDIO-MEMORY-READY)
        — truthful, not an error: nothing has been indexed yet.
        """
        last_ts = self._indexer.last_indexed_ts if self._indexer is not None else None
        return (
            datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if last_ts
            else "never"
        )

    def status(self) -> dict:
        from agent.symbol_graph import SYMBOL_LANGUAGES, available_symbol_languages
        # Retrieval weights + strategy fields are always present (UPG-8.2) —
        # before the first index-time fingerprint (`self._strategy`) has run,
        # fall back to the config-declared defaults rather than omitting the
        # fields. This keeps `status` output deterministic in shape from the
        # very first call, across both REST and MCP.
        if self._strategy:
            strategy_info = {
                "semantic_weight": self._strategy.semantic_weight,
                "bm25_weight": self._strategy.bm25_weight,
                "graph_first": self._strategy.graph_first,
                "recommended_embed_model": self._strategy.recommended_embed_model,
                "strategy_rationale": self._strategy.rationale,
            }
        else:
            strategy_info = {
                "semantic_weight": STRATEGY_DEFAULT_SEMANTIC_WEIGHT,
                "bm25_weight": STRATEGY_DEFAULT_BM25_WEIGHT,
                "graph_first": False,
                "recommended_embed_model": self._embed_model,
                "strategy_rationale": "default weights — no workspace fingerprint yet, index the workspace to compute one",
            }
        missing = sorted(SYMBOL_LANGUAGES - available_symbol_languages())
        if self._memory_only:
            mode = "memory-only"
        elif self._search_only:
            mode = "search-only"
        else:
            mode = "full"
        # UPG-REST-STARVATION requirement #2: a truthful, lock-free signal
        # that bulk index work is running right now — `Lock.locked()` is a
        # non-blocking read (never acquires), and `watcher_status()` reads
        # only in-memory counters/flags, so neither call can itself wait on
        # the bulk work it's reporting about. True while either an explicit
        # `index()`/startup index holds `_index_lock`, or the watcher's
        # coalesced batch worker is actively re-indexing/de-indexing.
        # UPG-STDIO-MEMORY-READY: before phase 2 has built the watcher, there
        # is truthfully nothing pending/running yet — the same all-False/zero
        # shape `CodeWatcher.watcher_status()` itself returns for a quiet
        # workspace, so `status()`'s output shape never changes across the
        # phase-1/phase-2 boundary.
        watcher_status = (
            self._watcher.watcher_status() if self._watcher is not None else {
                "watcher_burst_mode": False,
                "watcher_pending_files": 0,
                "watcher_batch_running": False,
                "watcher_last_batch_duration_ms": 0,
            }
        )
        reindex_in_progress = (
            self._index_lock.locked() or watcher_status.get("watcher_batch_running", False)
        )
        return {
            "indexed_files": self._indexer.indexed_file_count if self._indexer else 0,
            "total_chunks": self._indexer.total_chunks if self._indexer else 0,
            "last_indexed": self.last_indexed,
            "embed_model": self._embed_model,
            "workspace_root": self._workspace_root,
            "symbol_count": (
                self._symbol_graph.symbol_count(self._workspace_root)
                if self._symbol_graph is not None else 0
            ),
            "languages": self._language_coverage(),
            "notes_count": self.count_notes(),
            "grammars_unavailable": missing,
            "mode": mode,
            "version_stamp": self._version_stamp,
            "reindex_in_progress": reindex_in_progress,
            # UPG-NOTES-EMBED-MIGRATION: normally None — migration runs
            # synchronously at startup, so this only surfaces a mid-failure
            # state (e.g. the embedder was unavailable during migration).
            "notes_embed_model_mismatch": (
                self._context_store.embed_model_stamp_mismatch()
                if self._context_store is not None else None
            ),
            **self._symbol_graph_status(),
            **strategy_info,
            **watcher_status,
            "hook_injection_counts": self.get_hook_injection_counts(),
            "proactive_injection_counts": self.get_proactive_injection_counts(),
            # Effective ambient (hook-channel) master opt-in, visible BEFORE any
            # injection has happened — the counts above only appear after the
            # fact. The proxy channel injects by launch consent regardless.
            "proactive_enabled": self._proactive_master_enabled(),
            "artifact_cache": self.cache_metrics(),
            # UPG-STDIO-MEMORY-READY: additive warm-up signals. Both True
            # immediately for every existing (non-deferred) caller — a
            # deferred caller (stdio/HTTP daemon) sees `fully_ready=False`
            # (and possibly `embedder_ready=False`) only during the phase-2
            # background-construction window.
            "fully_ready": self.fully_ready,
            "embedder_ready": self.embedder_ready,
        }

    @staticmethod
    def _proactive_master_enabled() -> bool:
        from agent.proactive.settings import ProactiveSettings
        return ProactiveSettings.from_env().enabled

    def _symbol_graph_status(self) -> dict:
        """Symbol-graph build trust signals for `status` (UPG-8.7): whether the
        persisted graph is complete (no files failed extraction) and built by the
        current toolchain. Lets a benchmark/user confirm locate/trace coverage is
        trustworthy rather than a silently-partial graph.

        Returns the same "nothing built yet" shape before phase 2 has
        constructed the symbol graph (UPG-STDIO-MEMORY-READY) as it does for
        a workspace that has never been indexed."""
        if self._symbol_graph is None:
            return {"symbol_graph_complete": False, "symbol_graph_failed_files": 0}
        meta = self._symbol_graph.graph_meta(self._workspace_root)
        if not meta:
            return {"symbol_graph_complete": False, "symbol_graph_failed_files": 0}
        return {
            "symbol_graph_complete": meta.get("complete") == "1",
            "symbol_graph_failed_files": int(meta.get("failed", "0") or "0"),
        }

    def _language_coverage(self) -> list[dict]:
        """Per-language coverage + symbol availability for `status` (UPG-3.3).

        Lets the caller LLM route: `symbols=True` languages support locate/trace;
        the rest are search-only. A language declared in SYMBOL_LANGUAGES is only
        marked symbols=True when its tree-sitter grammar actually loads in this
        environment (grammar_available check) — prevents advertising locate/trace
        for a language whose grammar package is missing.
        Ordered by file count (dominant language first).

        Empty before phase 2 has built the indexer (UPG-STDIO-MEMORY-READY) —
        nothing has been walked/indexed yet, same as an empty workspace.
        """
        if self._indexer is None:
            return []
        from agent.symbol_graph import supports_symbols, grammar_available
        stats = self._indexer.indexed_language_stats()
        return [
            {
                "language": lang,
                "files": s["files"],
                "chunks": s["chunks"],
                "symbols": supports_symbols(lang) and grammar_available(lang),
            }
            for lang, s in sorted(
                stats.items(), key=lambda kv: (-kv[1]["files"], kv[0])
            )
        ]

    @property
    def total_chunks(self) -> int:
        return self._indexer.total_chunks

    # ------------------------------------------------------------------
    # L1 — codebase passport
    # ------------------------------------------------------------------

    def get_map(self) -> str:
        """
        Return codebase passport for AI consumption.
        If cached: instant ~300-token summary.
        If not: raw structural metadata + instruction to call vectr_map_save.

        Raw-metadata language detection uses the indexer's real per-language
        coverage (UPG-6.1) rather than a directory-walk extension guess.
        """
        return self._passport_store.format_for_llm(
            self._workspace_root, language_stats=self._indexer.indexed_language_stats()
        )

    # ------------------------------------------------------------------
    # L2 — symbol graph
    # ------------------------------------------------------------------

    def locate(self, name: str, limit: int = 10) -> list:
        return self._symbol_graph.locate(self._workspace_root, name, limit)

    def locate_with_snippets(self, name: str, limit: int = 10, caller_file: str | None = None):
        """Locate symbols via L2 multi-strategy resolution. Returns LocateResult. No LLM call."""
        return self._symbol_graph.locate_l2(self._workspace_root, name, limit=limit, caller_file=caller_file)

    def trace(self, name: str, direction: str = "both", limit: int = 20,
              include_builtins: bool = False) -> dict:
        return self._symbol_graph.trace(self._workspace_root, name, direction, limit, include_builtins)  # type: ignore[arg-type]

    def trace_with_snippets(self, name: str, direction: str = "both", limit: int = 20,
                            include_builtins: bool = False) -> dict:
        """Trace call graph. Caller/callee names are returned as-is; AI can locate() them for snippets.
        Builtin/stdlib callees hidden unless include_builtins (UPG-4.3)."""
        return self._symbol_graph.trace(self._workspace_root, name, direction, limit, include_builtins)  # type: ignore[arg-type]

    def ingest_traces(self, trace_events: list[dict]) -> dict:
        """Ingest runtime trace events into the symbol graph."""
        return self._symbol_graph.ingest_trace_data(self._workspace_root, trace_events)

    def format_locate(self, result, name: str = "") -> str:
        from agent.symbol_graph import LocateResult
        if isinstance(result, LocateResult):
            return self._symbol_graph.format_locate_l2_for_llm(result)
        return self._symbol_graph.format_locate_for_llm(result, name)

    def format_trace(self, trace_result: dict, name: str) -> str:
        return self._symbol_graph.format_trace_for_llm(trace_result, name)

    # ------------------------------------------------------------------
    # Memory — working context store
    # ------------------------------------------------------------------

    def _require_memory_layer(self) -> None:
        """Guard for every memory-facing method (UPG-SEARCH-ONLY-MODE): in
        search-only mode there is no context store — raise rather than hit an
        AttributeError on `self._context_store is None`. The MCP dispatch and
        REST layers intercept these calls earlier with a friendlier message;
        this is the root-cause guard for any other caller."""
        if self._search_only:
            raise RuntimeError(_SEARCH_ONLY_MSG)

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        priority: str = "medium",
        session_id: str | None = None,
        kind: str = "finding",
        title: str = "",
        agent: str = "",
        triggers: list[dict] | None = None,
        provenance: str = "agent",
        scope: str | None = None,
        anchors: list[str] | None = None,
        supersedes: int | None = None,
    ) -> int:
        """`agent` (UPG-SUBAGENT-MEMORY): optional caller-declared identifier
        for the agent/subagent authoring this note (e.g. "coder-2") — never
        inferred. Stored on the note's existing `author_id` column (already
        documented there as a "developer/agent identifier"; no schema change
        needed) and surfaced as an attribution tag in recall index lines when
        present. Absent (default "") renders exactly as before this feature.

        `triggers`/`provenance`/`scope`/`anchors`/`supersedes` (TRIGGER-ENGINE
        wave 1, bm2-design-skeleton.md §1/§2/§5): additive, all optional,
        passed straight through to `WorkingContextStore.remember()` — see its
        docstring for exact validation (raises ValueError on malformed
        triggers, an unrecognised provenance/scope, provenance="auto" on
        kind="directive", or a `supersedes` target that does not exist in
        this workspace). This method does no validation of its own so the
        store stays the single source of truth for these rules.

        `scope`: None (the default) means OMITTED — the store resolves it to
        this note's kind's default scope at write time
        (UPG-TRIGGER-SCOPE-KIND-DEFAULTS). An explicitly passed scope,
        including the literal string "workspace", always wins verbatim."""
        self._require_memory_layer()
        note_id = self._context_store.remember(
            workspace=self._workspace_root,
            content=content,
            tags=tags,
            priority=priority,
            session_id=session_id,
            kind=kind,
            title=title,
            author_id=agent,
            triggers=triggers,
            provenance=provenance,
            scope=scope,
            anchors=anchors,
            supersedes=supersedes,
        )
        self._bump_notes_epoch()
        return note_id

    def promote_note(self, note_id: int, to: str) -> bool:
        """Explicit provenance promotion (TRIGGER-ENGINE wave 1,
        bm2-design-skeleton.md §5): auto -> agent -> human, one step at a
        time. See `WorkingContextStore.promote()` for the exact contract
        (raises ValueError on an invalid/out-of-order transition; returns
        False if the note does not exist)."""
        self._require_memory_layer()
        promoted = self._context_store.promote(self._workspace_root, note_id, to)
        if promoted:
            self._bump_notes_epoch()
        return promoted

    def get_note(self, note_id: int):
        """Fetch a single note by ID (UPG-RECALL-HIERARCHY expand path)."""
        self._require_memory_layer()
        return self._context_store.get_note(self._workspace_root, note_id)

    def recall(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
        kind: str | None = None,
        boot: bool = False,
        min_similarity: float | None = None,
        file_path: str | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
        detail: str = "index",
        note_id: int | None = None,
        surface: str = "mcp",
        hook_event: str | None = None,
        session_id: str | None = None,
        events: list[str] | None = None,
    ) -> str:
        """`surface` selects the expand-hint phrasing rendered by
        `format_notes_for_llm` — 'mcp' (default: the MCP dispatch path, and
        hook-injected recall, both leave this unset since their reader is the
        editor's LLM) or 'cli' (only `cmd_recall`'s own REST request sets this
        explicitly, since its reader is a human terminal). See its docstring
        (UPG-CLI-RECALL-HINT).

        `hook_event` (UPG-HOOK-INJECT-OBSERVABILITY): set only by `vectr
        hook`'s own request — 'SessionStart' | 'UserPromptSubmit' |
        'PreToolUse'. When set and this call actually returns notes, counts
        one injection under that hook kind (see `status()`). None (the
        default — direct vectr_recall/`vectr recall` calls) records nothing.

        `session_id`/`events` (TRIGGER-ENGINE wave 2a, bm2-design-skeleton.md
        §3): the calling session's identity and the lifecycle event(s) this
        recall is standing in for. Threaded into the `boot`/`file_path`
        branches below so per-memory triggers (declared via
        `vectr_remember(triggers=..., scope=...)`) fire through the live
        engine — ledgered per session, budget-packed, scope-enforced —
        alongside (boot) or merged with (file_path) the legacy delivery
        paths those branches already provide. Both default to None, which
        reproduces today's ledger-less, budget-less, scope-unenforced
        behaviour exactly for any caller that predates this wave.
        """
        notes = self._recall_impl(
            query=query, tags=tags, priority=priority, limit=limit, kind=kind,
            boot=boot, min_similarity=min_similarity, file_path=file_path,
            max_age_days=max_age_days, sort_by=sort_by, detail=detail,
            note_id=note_id, surface=surface, session_id=session_id, events=events,
        )
        if hook_event is not None:
            self._record_hook_injection(hook_event, notes)
        return notes

    def _recall_impl(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
        kind: str | None = None,
        boot: bool = False,
        min_similarity: float | None = None,
        file_path: str | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
        detail: str = "index",
        note_id: int | None = None,
        surface: str = "mcp",
        session_id: str | None = None,
        events: list[str] | None = None,
    ) -> str:
        self._require_memory_layer()
        # Single-note expand: note_id overrides everything else (UPG-RECALL-HIERARCHY).
        if note_id is not None:
            note = self._context_store.get_note(self._workspace_root, note_id)
            if note is None:
                return f"Note #{note_id} not found."
            stale = self._context_store.check_staleness([note], self._workspace_root)
            return self._context_store.format_notes_for_llm([note], stale_warnings=stale, detail="full", surface=surface)

        # Boot mode (UPG-9.2 contract; TRIGGER-ENGINE wave 2a rewires the
        # mechanism onto the live engine). Ignores query/tags/priority/kind/
        # limit and returns "" (never the "no notes" placeholder) so a
        # SessionStart hook injects nothing on a fresh workspace rather than
        # noise. The directive/task kind-default trigger bundles (see
        # agent/trigger_engine.py's kind-default table) reproduce the old
        # `boot_recall()`'s unconditional-directive + high-task coverage
        # exactly, so `fire_and_format()` fully replaces that dump — with a
        # genuine improvement: an explicit session-start trigger declared on
        # any OTHER kind (finding/reference/gotcha) now also fires here,
        # where `boot_recall()` silently ignored it (kind-gated, not
        # trigger-gated). `events` lets a caller merge post-compaction
        # eligibility into the same boot fire (see main.py's `cmd_hook`);
        # absent that, the implicit event is `["session-start"]`.
        if boot:
            events_to_fire = events if events else ["session-start"]
            fire_text, _ = self._context_store.fire_and_format(
                self._workspace_root,
                events=events_to_fire,
                session_id=session_id,
                ledger=self._ledger_for(session_id),
                surface=surface,
            )
            return fire_text

        # Path-anchored mode (UPG-9.6 contract; TRIGGER-ENGINE wave 2a adds
        # the live engine alongside it). A gotcha with a structured trigger
        # (anchors[]/triggers[]) now fires through the engine — §3 block
        # format, provenance frame, staleness caveat, budget pack — and is
        # excluded from the legacy `recall_for_path()` pass below by
        # note_id, so the same note is never rendered twice. A gotcha with
        # no anchors (matched only by `recall_for_path()`'s path/content
        # heuristics) keeps the legacy path unchanged — the engine does not
        # cover it this wave.
        if file_path:
            fire_text, fired_ids = self._context_store.fire_and_format(
                self._workspace_root,
                event="pre-edit",
                file_path=file_path,
                session_id=session_id,
                ledger=self._ledger_for(session_id),
                surface=surface,
            )
            legacy_notes = self._context_store.recall_for_path(
                self._workspace_root, file_path, kind=kind, limit=limit, session_id=session_id)
            legacy_notes = [n for n in legacy_notes if n.note_id not in fired_ids]
            legacy_text = ""
            if legacy_notes:
                stale = self._context_store.check_staleness(legacy_notes, self._workspace_root)
                legacy_text = self._context_store.format_notes_for_llm(
                    legacy_notes, stale_warnings=stale, detail=detail, surface=surface)
            if fire_text and legacy_text:
                return fire_text + "\n\n" + legacy_text
            return fire_text or legacy_text

        # Generic query mode (TRIGGER-ENGINE wave 2a): `events`, when given,
        # is the same opt-in merge the file_path branch above always applies
        # — e.g. main.py's UserPromptSubmit hook passes events=["prompt-
        # submit"] alongside its query. Only an EXPLICIT `triggers[]`
        # override ever matches "prompt-submit" (no kind's default bundle
        # does), so this is a no-op for every note that predates an
        # explicit override; absent `events` entirely (the default — every
        # other caller: direct vectr_recall, `vectr recall`, etc.) this
        # branch behaves exactly as before the engine existed.
        #
        # `query` is forwarded here too (wave 2b, §8): a prompt-submit event
        # is the M (semantic) primitive's one fire point — this is the only
        # `fire_and_format()` call site in this file that ever has both an
        # `events` list AND the actual prompt text available together.
        fire_text, fired_ids = "", set()
        if events:
            fire_text, fired_ids = self._context_store.fire_and_format(
                self._workspace_root,
                events=events,
                query=query,
                session_id=session_id,
                ledger=self._ledger_for(session_id),
                surface=surface,
            )

        notes = self._context_store.recall(
            workspace=self._workspace_root,
            query=query,
            tags=tags,
            priority=priority,
            limit=limit,
            kind=kind,
            min_similarity=min_similarity,
            max_age_days=max_age_days,
            sort_by=sort_by,
            session_id=session_id,
        )
        if fired_ids:
            notes = [n for n in notes if n.note_id not in fired_ids]
        stale = self._context_store.check_staleness(notes, self._workspace_root)
        formatted = self._context_store.format_notes_for_llm(
            notes, stale_warnings=stale, detail=detail, surface=surface
        )
        # UPG-STDIO-MEMORY-READY: a query-bearing recall that lands before the
        # embedder has attached used the lexical SQL LIKE fallback — tell the
        # caller LLM plainly, so it knows to retry later for semantic ranking
        # rather than assuming these are the best-ranked matches available.
        # Gated purely on `embedder_ready` state (never on `query`'s content)
        # and only when a query was given at all — index/list-mode recall
        # (query=None) has no ranking to degrade, so it is never appended
        # there, and it never appears once the embedder has attached.
        if query and not self.embedder_ready:
            formatted += _EMBEDDER_LOADING_RECALL_NOTICE
        if fire_text and formatted:
            return fire_text + "\n\n" + formatted
        return fire_text or formatted

    def forget_note(self, note_id: int) -> bool:
        self._require_memory_layer()
        ok = self._context_store.forget(self._workspace_root, note_id)
        if ok:
            self._bump_notes_epoch()
        return ok

    def forget_all(self) -> int:
        self._require_memory_layer()
        n = self._context_store.forget_all(self._workspace_root)
        self._bump_notes_epoch()
        return n

    # ------------------------------------------------------------------
    # Proactive context (UPG-PRO) + org-wide artifact cache
    # ------------------------------------------------------------------

    def _bump_notes_epoch(self) -> None:
        """Advance the notes-mutation sequence so any recall artifact cached
        under the previous notes epoch can never be served after a change."""
        with self._notes_mutation_lock:
            self._notes_mutation_seq += 1

    def _index_epoch(self, scope: str) -> str:
        """Identity of the current index state for a cache scope. A change here
        invalidates every artifact keyed under it. `scope='code'` ties to the
        code index (chunks + last-indexed + embed model + version); `scope='notes'`
        ties to the notes-mutation sequence."""
        if scope == "notes":
            with self._notes_mutation_lock:
                return f"notes:{self._notes_mutation_seq}"
        return (
            f"code:{self._indexer.total_chunks}:{self.last_indexed}:"
            f"{self._embed_model}:{self._version_stamp}"
        )

    def recall_scored(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
        kind: str | None = None,
        min_similarity: float | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
    ) -> list:
        """Structured scored recall (UPG-PRO-1): list[(WorkingNote, score|None)].

        Consults the org-wide artifact cache when enabled (keyed by args + the
        current notes epoch, so a note change invalidates it). The SQL fallback
        yields None scores — never a fabricated number.
        """
        self._require_memory_layer()

        def _compute():
            return self._context_store.recall_scored(
                workspace=self._workspace_root, query=query, tags=tags,
                priority=priority, limit=limit, kind=kind,
                min_similarity=min_similarity, max_age_days=max_age_days, sort_by=sort_by,
            )

        cache = self._artifact_cache
        if cache is None:
            return _compute()
        from agent.proactive.cache import canonical_key
        args = {
            "query": query, "tags": tags, "priority": priority, "limit": limit,
            "kind": kind, "min_similarity": min_similarity,
            "max_age_days": max_age_days, "sort_by": sort_by,
        }
        key = canonical_key("recall_scored", args, self._index_epoch("notes"))
        found, cached = cache.get(key)
        if found:
            return cached
        result = _compute()
        cache.put(key, result)
        return result

    def _proactive_gate(self, settings):
        from agent.proactive.gate import LedgerStore, ProactiveGate
        if self._proactive_ledger is None:
            self._proactive_ledger = LedgerStore(settings.cooldown_items)
        return ProactiveGate(
            min_similarity=settings.min_similarity,
            max_items_per_event=settings.max_items_per_event,
            max_chars_per_event=settings.max_chars_per_event,
            cooldown_items=settings.cooldown_items,
            ledger_store=self._proactive_ledger,
        )

    def proactive_context(
        self,
        *,
        text: str = "",
        file_paths: list[str] | None = None,
        symbols: list[str] | None = None,
        session_id: str = "",
        channel: str = "proxy",
        structural_only: bool = False,
    ) -> dict:
        """Run the matcher + gate over an already-assembled window and return
        packed proactive context (UPG-PRO-7 subset serving the proxy).

        Honors the master opt-in and the memory layer being present. Returns an
        empty result (never an error) when disabled or when nothing clears the
        floor + budget, so a caller can always forward unmodified. Records a
        metadata-only PROACTIVE_INJECT audit event on a real injection.
        """
        empty = {"context": "", "item_count": 0, "anchor_ids": [], "scores": []}
        if self._search_only:
            # No working-memory layer in search-only mode; nothing to inject.
            return empty
        from agent.proactive.settings import ProactiveSettings
        settings = ProactiveSettings.from_env()
        # The master opt-in gates AMBIENT surfaces (hooks read the transcript
        # without any per-session user action). The proxy channel is different:
        # the user explicitly launched `vectr proxy` with injection enabled and
        # pointed their client at it — that launch IS the consent for this
        # channel. The daemon is localhost-only and already serves notes to
        # local callers ungated (recall/search), so honoring the proxy channel
        # here adds no exposure beyond existing endpoints.
        if not settings.enabled and channel != "proxy":
            return empty
        from agent.proactive.matcher import ProactiveMatcher
        from agent.proactive.types import ProactiveWindow

        window = ProactiveWindow(
            text=text or "",
            file_paths=list(file_paths or []),
            symbols=list(symbols or []),
        )
        if window.is_empty():
            return empty

        service = self

        class _ServiceMatchSource:
            def structural_notes(self, paths):
                seen: dict[int, object] = {}
                for p in paths:
                    try:
                        for note in service._context_store.recall_for_path(
                            service._workspace_root, p, limit=settings.max_items_per_event * 2
                        ):
                            seen.setdefault(note.note_id, note)
                    except Exception:
                        continue
                return list(seen.values())

            def semantic_notes(self, wtext, min_similarity, limit):
                scored = service.recall_scored(
                    query=wtext, limit=limit, min_similarity=min_similarity,
                )
                return [(n, s) for (n, s) in scored if s is not None]

            def code_search(self, wtext, n_results):
                if service._memory_only:
                    return []
                try:
                    results, _ms = service.search(wtext, n_results=n_results)
                    return list(results)
                except Exception:
                    return []

        matcher = ProactiveMatcher(
            _ServiceMatchSource(),
            min_similarity=settings.min_similarity,
            max_chars_per_event=settings.max_chars_per_event,
            structural_note=settings.matcher_structural_note,
            semantic_note=settings.matcher_semantic_note,
            code_search=settings.matcher_code_search,
            note_limit=max(settings.max_items_per_event * 2, settings.max_items_per_event),
        )
        candidates = matcher.match(window)
        result = self._proactive_gate(settings).select(
            candidates, session_id=session_id, structural_only=structural_only
        )
        if not result.is_empty():
            self._record_proactive_injection(channel, result)
        return {
            "context": result.context,
            "item_count": result.item_count,
            "anchor_ids": list(result.anchor_ids),
            "scores": list(result.scores),
        }

    def _record_proactive_injection(self, channel: str, result) -> None:
        with self._proactive_injection_lock:
            self._proactive_injection_counts[channel] = (
                self._proactive_injection_counts.get(channel, 0) + 1
            )
        # Metadata-only audit (design §9): ids + scores + counts, never the
        # conversation text or note bodies.
        from agent.working_context_store import audit as _audit
        _audit(
            "PROACTIVE_INJECT", workspace=self._workspace_root, channel=channel,
            items=result.item_count, anchors=",".join(result.anchor_ids),
        )

    def get_proactive_injection_counts(self) -> dict:
        with self._proactive_injection_lock:
            return dict(self._proactive_injection_counts)

    def cache_metrics(self) -> dict | None:
        """Org-wide artifact-cache metrics for `status` (None when off)."""
        if self._artifact_cache is None:
            return None
        return self._artifact_cache.metrics()

    def snapshot_session(self, label: str, session_id: str | None = None) -> str:
        self._require_memory_layer()
        return self._context_store.snapshot(
            workspace=self._workspace_root,
            label=label,
            retrieved_chunks=self._advisor_for(session_id).as_chunk_dicts(),
            session_id=session_id,
        )

    def list_snapshots(self) -> list[dict]:
        self._require_memory_layer()
        return self._context_store.list_snapshots(self._workspace_root)

    def restore_snapshot(self, snapshot_id: str) -> dict | None:
        self._require_memory_layer()
        return self._context_store.restore_snapshot(snapshot_id)

    # ------------------------------------------------------------------
    # Eviction advisor
    # ------------------------------------------------------------------

    def eviction_hint(self, session_id: str | None = None) -> str:
        return self._advisor_for(session_id).eviction_hint()

    def auto_eviction_hint(self, session_id: str | None = None) -> str:
        """Gated per-response footer (UPG-7.1) — fires only on fresh context-
        pressure escalation, not every response. Used by the MCP search/locate/
        trace auto-append; the explicit vectr_evict_hint tool uses eviction_hint().
        Reads the calling session's advisor (UPG-EVICT-SESSION-SCOPE)."""
        return self._advisor_for(session_id).auto_eviction_hint()

    def should_evict(self, session_id: str | None = None) -> bool:
        return self._advisor_for(session_id).should_evict()

    def count_notes(self) -> int:
        """Return number of active working-memory notes for this workspace.

        Search-only mode has no context store — always 0, never an error, so
        `status()` and `suggest_instruction_style()` stay usable unconditionally.
        """
        if self._context_store is None:
            return 0
        return self._context_store.count_notes(self._workspace_root)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Adaptive prompt intelligence
    # ------------------------------------------------------------------

    def suggest_instruction_style(self) -> str:
        """Return the recommended CLAUDE.md instruction variant for this workspace.

        Returns one of: "additive" | "directed" | "memory-only".

        Decision logic (priority order):
        1. File override (.vectr/style) — always wins.
        2. "memory-only"  — prior notes exist AND codebase is well-known (small
                            or familiar framework) → recall-forward session.
        3. "directed"     — large or complex unfamiliar codebase → explicit tool
                            guidance reduces wasted exploration turns.
        4. "additive"     — default; model decides based on when-to-use hints.

        Research basis: additive outperforms forced/memory-only in A/B tests
        (spec §CLAUDE.md framing choices). directed is warranted only when
        codebase is genuinely unfamiliar at implementation depth.
        """
        override = self._read_style_override()
        if override in ("additive", "directed", "memory-only"):
            return override

        notes_count = self.count_notes()
        fp = None
        if self._strategy is not None:
            try:
                from agent.strategy_selector import fingerprint as _fingerprint
                fp = _fingerprint(self._workspace_root, self._indexer.indexed_file_paths)
            except Exception:
                pass

        # Well-known frameworks: model knows these at implementation depth from training
        _KNOWN_FRAMEWORKS = {
            "django", "flask", "fastapi", "react", "nextjs", "vue", "angular",
            "express", "spring-boot", "gin", "echo", "celery",
        }
        known_codebase = (
            fp is not None
            and bool(_KNOWN_FRAMEWORKS.intersection(set(fp.detected_frameworks)))
        )

        if notes_count > 0 and (known_codebase or (fp and fp.size_class == "small")):
            return "memory-only"

        if fp is not None:
            is_large_unfamiliar = (
                fp.size_class == "large"
                and not known_codebase
            )
            is_complex = fp.complexity_class == "complex" and not known_codebase
            if is_large_unfamiliar or is_complex:
                return "directed"

        return "additive"

    def _read_style_override(self) -> str:
        """Read .vectr/style file if present."""
        style_file = Path(self._workspace_root) / ".vectr" / "style"
        try:
            return style_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
