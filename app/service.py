"""Business logic: owns the indexer, searcher, watcher, and memory layer lifecycle."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.config import STRATEGY_DEFAULT_BM25_WEIGHT, STRATEGY_DEFAULT_SEMANTIC_WEIGHT

logger = logging.getLogger(__name__)

_DB_DIR_ENV = "VECTR_DB_DIR"


def _default_db_dir(workspace_root: str) -> str:
    """Store DB files in ~/.cache/vectr/<workspace-hash>/"""
    import hashlib
    slug = hashlib.md5(workspace_root.encode()).hexdigest()[:12]
    db_dir = Path.home() / ".cache" / "vectr" / slug
    db_dir.mkdir(parents=True, exist_ok=True)
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


class VectrService:
    """Singleton-style service. Create once at startup; shared via FastAPI app state."""

    def __init__(
        self,
        workspace_root: str,
        port: int = 8765,
        extra_roots: list[str] | None = None,
        memory_only: bool = False,
        search_only: bool = False,
    ) -> None:
        from agent.indexer import CodeIndexer
        from agent.searcher import CodeSearcher
        from agent.watcher import CodeWatcher
        from agent.cartographer import PassportStore
        from agent.working_context_store import WorkingContextStore
        from agent.symbol_graph import SymbolGraph
        from agent.eviction_advisor import EvictionAdvisor
        from integrations.vscode_bridge import configure_all
        from integrations.workspace_detect import find_workspace_root

        self._workspace_root = find_workspace_root(workspace_root)
        self._extra_roots: list[str] = list(extra_roots or [])
        self._port = port
        # Default is an asymmetric embedding model — search queries must be embedded
        # via the provider's embed_query (registered "query" prompt), never embed().
        # See agent/indexer/_types.py:LocalEmbedProvider for the query/document split.
        self._embed_model = os.getenv("VECTR_EMBED_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
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
        self._db_dir = db_dir

        logger.info("Initialising Vectr for workspace: %s (db: %s)", self._workspace_root, db_dir)

        # L3 — content retrieval (existing)
        # db_path scopes ChromaDB under the same configured db_dir as all other stores
        self._indexer = CodeIndexer(
            self._workspace_root,
            embed_model=self._embed_model,
            db_path=str(Path(db_dir) / "chroma"),
            extra_roots=self._extra_roots,
        )
        self._searcher = CodeSearcher(self._indexer)
        self._watcher = CodeWatcher(self._indexer, searcher_refresh_fn=self._searcher.refresh_bm25)

        # L1 — codebase passport (AI-written, stored by vectr_map_save)
        self._passport_store = PassportStore(db_dir)

        # L2 — symbol graph
        self._symbol_graph = SymbolGraph(db_dir)
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

        # Memory layer — semantic recall enabled via the same embedder + ChromaDB client
        # used by the code index, so no extra model load or second DB process.
        # In search-only mode the store is never constructed at all (UPG-SEARCH-ONLY-MODE):
        # no notes DB file and no 'working_memory' Chroma collection are created for a
        # workspace that never writes a note.
        self._context_store: WorkingContextStore | None = None
        if not self._search_only:
            self._context_store = WorkingContextStore(
                db_dir,
                embed_fn=self._indexer.embed_texts,
                embed_query_fn=self._indexer.embed_query_batch,
                notes_chroma_client=self._indexer.chroma_client,
            )

        # Session eviction advisor
        self._eviction_advisor = EvictionAdvisor(
            eviction_threshold_tokens=int(os.getenv("VECTR_EVICT_THRESHOLD", "4000"))
        )

        self._indexing = False
        self._index_thread: threading.Thread | None = None
        self._index_lock = threading.Lock()

        # Per-tool call counters — tracked across all callers (parent + sub-agents)
        # so benchmark tooling can read accurate counts via GET /v1/call_counts.
        self._call_counts: dict[str, int] = {}
        self._call_counts_lock = threading.Lock()

        # Adaptive strategy — computed after first index, defaults until then
        from agent.strategy_selector import RetrievalStrategy
        self._strategy: RetrievalStrategy | None = None

        for root in self._indexer.all_roots:
            configure_all(str(root), port)

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

    def start_background_index(self) -> None:
        """Kick off workspace indexing in a background thread.

        In memory-only mode, the index thread and file watcher are skipped.
        In search-only mode, indexing and the watcher run normally, but the
        notes-TTL purge is skipped — there is no context store to purge.
        Otherwise, the notes-TTL purge runs so expired notes are cleaned up at
        startup.
        """
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
        self._watcher.stop()

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
            return files, chunks, elapsed

    def search(
        self, query: str, n_results: int = 10, language: str | None = None
    ) -> tuple[list, int]:
        """Returns (SearchResult list, query_time_ms). Also records for eviction tracking."""
        sem_w = self._strategy.semantic_weight if self._strategy else STRATEGY_DEFAULT_SEMANTIC_WEIGHT
        results, query_ms = self._searcher.search(
            query, n_results=n_results, language=language, semantic_weight=sem_w
        )
        self._eviction_advisor.record_results(results)
        return results, query_ms

    def indexed_languages(self) -> list[str]:
        """Distinct languages actually present in the index (UPG-3.1)."""
        return self._indexer.indexed_languages()

    def route_query(self, query: str):
        """Classify a query and return a RoutingDecision."""
        from agent.query_router import route
        base_sem = self._strategy.semantic_weight if self._strategy else STRATEGY_DEFAULT_SEMANTIC_WEIGHT
        return route(query, base_semantic_weight=base_sem)

    def search_routed(
        self, query: str, n_results: int = 10, language: str | None = None
    ) -> tuple[list, int, object, list, list]:
        """
        Returns (results, query_ms, routing_decision, augmented_symbols, trace_results).
        Uses QueryRouter to classify the query and blend in L2 results when appropriate.
        """
        decision = self.route_query(query)

        results, query_ms = self._searcher.search(
            query, n_results=n_results, language=language,
            semantic_weight=decision.semantic_weight,
        )
        self._eviction_advisor.record_results(results)

        aug_symbols: list = []
        aug_trace: list = []

        if decision.also_run_symbol_lookup:
            import re as _re
            _STOPWORDS = {
                "where", "what", "which", "find", "show", "locate", "define",
                "defined", "implement", "implemented", "used", "using", "with",
                "the", "this", "that", "from", "into", "does", "call", "calls",
                "have", "look", "like", "how", "when", "about", "does",
            }
            # Collect candidate terms: CamelCase identifiers, snake_case, and
            # plain words ≥4 chars that aren't stopwords — try each until we get hits
            terms: list[str] = []
            for m in _re.finditer(r'\b([A-Z][a-zA-Z0-9]{2,}|[a-z_][a-z_0-9]{3,})\b', query):
                t = m.group(1)
                if t.lower() not in _STOPWORDS:
                    terms.append(t)
            # also try individual meaningful words as a fallback
            for w in query.split():
                w = w.strip("?.,!").lower()
                if len(w) >= 4 and w not in _STOPWORDS and w not in [t.lower() for t in terms]:
                    terms.append(w)

            aug_symbols = []
            for term in terms:
                candidates = self._symbol_graph.locate(self._workspace_root, term, limit=5)
                if candidates:
                    aug_symbols = candidates
                    break
            # snippets are already populated by locate() — no LLM call needed

            if decision.also_run_trace and aug_symbols:
                trace_result = self._symbol_graph.trace(
                    self._workspace_root, aug_symbols[0].name, direction="callers", limit=10
                )
                aug_trace = trace_result.get("callers", [])

        return results, query_ms, decision, aug_symbols, aug_trace

    @property
    def last_indexed(self) -> str:
        """Single source of truth for the last-indexed timestamp string.

        Used by both `status()` and the `/v1/health` route so the two
        endpoints never disagree on freshness (UPG-8.2).
        """
        last_ts = self._indexer.last_indexed_ts
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
        return {
            "indexed_files": self._indexer.indexed_file_count,
            "total_chunks": self._indexer.total_chunks,
            "last_indexed": self.last_indexed,
            "embed_model": self._embed_model,
            "workspace_root": self._workspace_root,
            "symbol_count": self._symbol_graph.symbol_count(self._workspace_root),
            "languages": self._language_coverage(),
            "notes_count": self.count_notes(),
            "grammars_unavailable": missing,
            "mode": mode,
            **self._symbol_graph_status(),
            **strategy_info,
        }

    def _symbol_graph_status(self) -> dict:
        """Symbol-graph build trust signals for `status` (UPG-8.7): whether the
        persisted graph is complete (no files failed extraction) and built by the
        current toolchain. Lets a benchmark/user confirm locate/trace coverage is
        trustworthy rather than a silently-partial graph."""
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
        """
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
    ) -> int:
        self._require_memory_layer()
        return self._context_store.remember(
            workspace=self._workspace_root,
            content=content,
            tags=tags,
            priority=priority,
            session_id=session_id,
            kind=kind,
            title=title,
        )

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
    ) -> str:
        self._require_memory_layer()
        # Single-note expand: note_id overrides everything else (UPG-RECALL-HIERARCHY).
        if note_id is not None:
            note = self._context_store.get_note(self._workspace_root, note_id)
            if note is None:
                return f"Note #{note_id} not found."
            stale = self._context_store.check_staleness([note], self._workspace_root)
            return self._context_store.format_notes_for_llm([note], stale_warnings=stale, detail="full")

        # Boot mode (UPG-9.2): unconditional directive + high-task set for
        # harness-injected recall. Ignores query/tags/priority/kind/limit and
        # returns "" (never the "no notes" placeholder) so a SessionStart hook
        # injects nothing on a fresh workspace rather than noise.
        # Boot always renders index tier (directives at full, tasks at index) — see below.
        if boot:
            notes = self._context_store.boot_recall(self._workspace_root)
            if not notes:
                return ""
            stale = self._context_store.check_staleness(notes, self._workspace_root)
            # Directives carry imperative text that matters verbatim — render full.
            # High-priority tasks and others render as index (token-bounded).
            directive_notes = [n for n in notes if n.kind == "directive"]
            other_notes = [n for n in notes if n.kind != "directive"]
            parts: list[str] = []
            if directive_notes:
                parts.append(self._context_store.format_notes_for_llm(
                    directive_notes, stale_warnings=stale, detail="full"))
            if other_notes:
                parts.append(self._context_store.format_notes_for_llm(
                    other_notes, stale_warnings=stale, detail="index"))
            return "\n".join(parts)

        # Path-anchored mode (UPG-9.6): notes recorded against a specific file,
        # for the PreToolUse gotcha hook. Returns "" when none, so editing a file
        # with no recorded caveat injects nothing.
        if file_path:
            notes = self._context_store.recall_for_path(
                self._workspace_root, file_path, kind=kind, limit=limit)
            if not notes:
                return ""
            stale = self._context_store.check_staleness(notes, self._workspace_root)
            return self._context_store.format_notes_for_llm(notes, stale_warnings=stale, detail=detail)

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
        )
        stale = self._context_store.check_staleness(notes, self._workspace_root)
        return self._context_store.format_notes_for_llm(notes, stale_warnings=stale, detail=detail)

    def forget_note(self, note_id: int) -> bool:
        self._require_memory_layer()
        return self._context_store.forget(self._workspace_root, note_id)

    def forget_all(self) -> int:
        self._require_memory_layer()
        return self._context_store.forget_all(self._workspace_root)

    def snapshot_session(self, label: str, session_id: str | None = None) -> str:
        self._require_memory_layer()
        return self._context_store.snapshot(
            workspace=self._workspace_root,
            label=label,
            retrieved_chunks=self._eviction_advisor.as_chunk_dicts(),
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

    def eviction_hint(self) -> str:
        return self._eviction_advisor.eviction_hint()

    def auto_eviction_hint(self) -> str:
        """Gated per-response footer (UPG-7.1) — fires only on fresh context-
        pressure escalation, not every response. Used by the MCP search/locate/
        trace auto-append; the explicit vectr_evict_hint tool uses eviction_hint()."""
        return self._eviction_advisor.auto_eviction_hint()

    def should_evict(self) -> bool:
        return self._eviction_advisor.should_evict()

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
