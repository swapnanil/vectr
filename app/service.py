"""Business logic: owns the indexer, searcher, watcher, and memory layer lifecycle."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_DIR_ENV = "VECTR_DB_DIR"


def _default_db_dir(workspace_root: str) -> str:
    """Store DB files in ~/.cache/vectr/<workspace-hash>/"""
    import hashlib
    slug = hashlib.md5(workspace_root.encode()).hexdigest()[:12]
    db_dir = Path.home() / ".cache" / "vectr" / slug
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir)


class VectrService:
    """Singleton-style service. Create once at startup; shared via FastAPI app state."""

    def __init__(self, workspace_root: str, port: int = 8765, extra_roots: list[str] | None = None) -> None:
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
        self._embed_model = os.getenv("VECTR_EMBED_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")

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

        # Memory layer — semantic recall enabled via the same embedder + ChromaDB client
        # used by the code index, so no extra model load or second DB process.
        self._context_store = WorkingContextStore(
            db_dir,
            embed_fn=self._indexer.embed_texts,
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

    def start_background_index(self) -> None:
        """Kick off workspace indexing in a background thread."""
        if self._indexing:
            return
        self._indexing = True

        # apply TTL to working notes at startup if VECTR_NOTES_TTL_DAYS is set
        ttl_days_str = os.getenv("VECTR_NOTES_TTL_DAYS", "")
        if ttl_days_str:
            try:
                ttl = float(ttl_days_str)
                deleted = self._context_store.purge_expired_notes(self._workspace_root, ttl)
                if deleted:
                    logger.info("purged %d expired notes (TTL=%.1f days)", deleted, ttl)
            except (ValueError, Exception):
                logger.warning("VECTR_NOTES_TTL_DAYS is not a valid float: %r", ttl_days_str)

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
        except Exception:
            logger.exception("Symbol graph build failed (non-fatal)")

    def save_map(self, summary: str) -> None:
        """
        Persist an AI-written codebase passport.
        Called via vectr_map_save — the AI editor has synthesised the summary
        after reading the raw metadata returned by vectr_map on first call.
        """
        self._passport_store.save_summary(summary, self._workspace_root)
        logger.info("Passport saved by AI editor (%d chars)", len(summary))

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
        sem_w = self._strategy.semantic_weight if self._strategy else 0.70
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
        base_sem = self._strategy.semantic_weight if self._strategy else 0.70
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

    def status(self) -> dict:
        last_ts = self._indexer.last_indexed_ts
        last_str = (
            datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if last_ts
            else "never"
        )
        strategy_info = {}
        if self._strategy:
            strategy_info = {
                "semantic_weight": self._strategy.semantic_weight,
                "bm25_weight": self._strategy.bm25_weight,
                "graph_first": self._strategy.graph_first,
                "recommended_embed_model": self._strategy.recommended_embed_model,
                "strategy_rationale": self._strategy.rationale,
            }
        return {
            "indexed_files": self._indexer.indexed_file_count,
            "total_chunks": self._indexer.total_chunks,
            "last_indexed": last_str,
            "embed_model": self._embed_model,
            "workspace_root": self._workspace_root,
            "symbol_count": self._symbol_graph.symbol_count(self._workspace_root),
            "languages": self._language_coverage(),
            "notes_count": self._context_store.count_notes(self._workspace_root),
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
        the rest are search-only. Ordered by file count (dominant language first).
        """
        from agent.symbol_graph import supports_symbols
        stats = self._indexer.indexed_language_stats()
        return [
            {
                "language": lang,
                "files": s["files"],
                "chunks": s["chunks"],
                "symbols": supports_symbols(lang),
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
        """
        return self._passport_store.format_for_llm(self._workspace_root)

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

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        priority: str = "medium",
        session_id: str | None = None,
    ) -> int:
        return self._context_store.remember(
            workspace=self._workspace_root,
            content=content,
            tags=tags,
            priority=priority,
            session_id=session_id,
        )

    def recall(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
    ) -> str:
        notes = self._context_store.recall(
            workspace=self._workspace_root,
            query=query,
            tags=tags,
            priority=priority,
            limit=limit,
        )
        stale = self._context_store.check_staleness(notes, self._workspace_root)
        return self._context_store.format_notes_for_llm(notes, stale_warnings=stale)

    def forget_note(self, note_id: int) -> bool:
        return self._context_store.forget(self._workspace_root, note_id)

    def forget_all(self) -> int:
        return self._context_store.forget_all(self._workspace_root)

    def snapshot_session(self, label: str, session_id: str | None = None) -> str:
        return self._context_store.snapshot(
            workspace=self._workspace_root,
            label=label,
            retrieved_chunks=self._eviction_advisor.as_chunk_dicts(),
            session_id=session_id,
        )

    def list_snapshots(self) -> list[dict]:
        return self._context_store.list_snapshots(self._workspace_root)

    def restore_snapshot(self, snapshot_id: str) -> dict | None:
        return self._context_store.restore_snapshot(snapshot_id)

    # ------------------------------------------------------------------
    # Eviction advisor
    # ------------------------------------------------------------------

    def eviction_hint(self) -> str:
        return self._eviction_advisor.eviction_hint()

    def should_evict(self) -> bool:
        return self._eviction_advisor.should_evict()

    def count_notes(self) -> int:
        """Return number of active working-memory notes for this workspace."""
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

        notes_count = self._context_store.count_notes(self._workspace_root)
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
