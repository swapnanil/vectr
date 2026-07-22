"""CodeIndexer: ChromaDB-backed index orchestration (chunking + embed + upsert)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

import chromadb
import numpy as np

from agent.chunk_quality import build_purpose_text, is_symbol_bearing_chunk
from agent.config import DUAL_VECTOR_ENABLED as _DUAL_VECTOR_ENABLED
from agent.config import EMBEDDING_DEFAULT_MODEL as _EMBEDDING_DEFAULT_MODEL
from agent.config import (
    INDEXING_VECTOR_STORE_SLOW_CALL_WARN_SECONDS as _CHROMA_SLOW_CALL_WARN_SECONDS,
)
from agent.indexer._constants import (
    EXCLUDED_DIRS,
    _FILE_BATCH_SIZE,
    _EMBED_BATCH_SIZE,
    _UPSERT_BATCH_SIZE,
    _CHUNK_WORKERS,
    _MTIME_CACHE_SCHEMA_KEY,
    INDEXING_SCHEMA_VERSION,
    _EMBED_MODEL_STAMP_FILE,
)
from agent.indexer._chunking import chunk_file
from agent.indexer._types import CodeChunk

logger = logging.getLogger(__name__)


@contextmanager
def _timed_chroma_call(op_name: str):
    """Time a single blocking vector-store call and log one WARNING if it
    exceeds the configured threshold (UPG-CHROMA-BLOCKING-EVENT-LOOP). The
    store's own internal work (e.g. compacting a large collection) can hold
    a call open far longer than its usual cost; this is the only visibility
    vectr has into that from its side. Applied uniformly to every call site,
    never conditioned on the caller or on what the call is for."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        if elapsed > _CHROMA_SLOW_CALL_WARN_SECONDS:
            logger.warning(
                "chroma %s blocked %.1fs — vector store may be compacting",
                op_name, elapsed,
            )


def _chunk_metadata(c: CodeChunk) -> dict:
    """The metadata dict stored alongside a chunk's vector (body or purpose
    collection — same shape for both). Factored out so the streaming embed/
    upsert loops (UPG-INDEX-MEM-STREAMING) build it per-batch instead of once
    per corpus."""
    return {
        "file_path": c.file_path,
        "language": c.language,
        "node_type": c.node_type,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "symbol_name": c.symbol_name,
    }


def _upsert_in_batches(
    collection,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
    embeddings: list[list[float]],
    batch_size: int,
    op_label: str = "upsert",
) -> None:
    """Upsert one embed-batch's rows into `collection` in `batch_size` slices
    (SQLite's 999-variable limit: 6 metadata fields x 100 rows = 600 <= 999).
    Bounded to a single embed-batch's worth of rows by every caller, so this
    never itself becomes an O(corpus) structure."""
    for j in range(0, len(ids), batch_size):
        with _timed_chroma_call(op_label):
            collection.upsert(
                ids=ids[j: j + batch_size],
                documents=documents[j: j + batch_size],
                metadatas=metadatas[j: j + batch_size],
                embeddings=embeddings[j: j + batch_size],
            )


class CodeIndexer:
    def __init__(
        self,
        workspace_root: str,
        embed_model: str = _EMBEDDING_DEFAULT_MODEL,
        db_path: str | None = None,
        extra_roots: list[str] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._extra_roots: list[Path] = [Path(r).resolve() for r in (extra_roots or [])]
        self.embed_model = embed_model

        db_dir = Path(db_path) if db_path else Path.home() / ".cache" / "vectr" / "db" / self._workspace_hash()
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_dir = db_dir

        self._client = chromadb.PersistentClient(path=str(db_dir))
        self._create_collections()
        self._last_indexed: float = 0.0
        self._indexed_files: set[str] = set()
        # Incrementally-maintained per-language stats (UPG-3.1/3.3,
        # UPG-REST-STARVATION). Seeded once by a full metadata scan (now done
        # eagerly below, during construction — see the call to
        # `_ensure_stats_seeded()` at the end of this method), then updated by
        # `_apply_chunk_delta` on every subsequent insert/delete instead of
        # ever re-scanning the collection — see `_ensure_stats_seeded`'s
        # docstring for why this replaced a count-keyed rescan-on-change cache
        # that re-scanned the whole collection on every call while the chunk
        # count was still changing (i.e. throughout a bulk reindex), making
        # `indexed_language_stats()` an O(corpus) operation contending with
        # the writer.
        self._stats_lock = threading.Lock()
        self._stats_seeded = False
        self._lang_chunk_counts: dict[str, int] = {}
        self._lang_files: dict[str, set[str]] = {}
        # Total chunk counts (body + purpose collections), read by
        # `total_chunks`/`total_purpose_chunks` (UPG-CHROMA-BLOCKING-EVENT-LOOP).
        # Seeded here from a real (but construction-time, always
        # off-the-event-loop) count; refreshed the same way again by
        # `_refresh_chunk_count_caches` at the end of every mutation entry
        # point (index_file/delete_file/index_workspace) — never re-read
        # from the vector store on a later request.
        self._total_chunks_cache: int = 0
        self._purpose_chunks_cache: int = 0
        self._refresh_chunk_count_caches()

        # Deferred: look up get_embed_provider through the package namespace so that
        # test-time monkeypatching of agent.indexer.get_embed_provider is honoured
        # (identical to the original flat-module behaviour where the function lived
        # in the same module namespace that patches target).
        import agent.indexer as _idx
        self._embed_provider = _idx.get_embed_provider(embed_model)

        # Seed the per-language stats cache now, during construction — which
        # always runs off the event loop serving requests, either
        # synchronously before the daemon starts accepting them or on its own
        # background thread (UPG-STDIO-MEMORY-READY's deferred-init path) —
        # so `indexed_language_stats()` (read by both `/v1/status` and
        # `/v1/map`) never performs its first-ever collection scan inside a
        # live request (UPG-CHROMA-BLOCKING-EVENT-LOOP).
        self._ensure_stats_seeded()

    def _workspace_hash(self) -> str:
        return hashlib.md5(str(self.workspace_root).encode()).hexdigest()[:12]

    def _create_collections(self) -> None:
        self._collection = self._client.get_or_create_collection(
            name="code_chunks",
            metadata={
                "hnsw:space": "cosine",
                "hnsw:construction_ef": 200,  # default 100 — denser graph, better recall
                "hnsw:search_ef": 100,         # default 10 — wider beam search at query time
                "hnsw:M": 32,                  # default 16 — more neighbours per node
            },
        )
        # ARCH-4: second collection holding the body-stripped "purpose" vector
        # (qualified signature + docstring) for symbol-bearing chunks only —
        # keyed by the SAME chunk_id as `self._collection`. get_or_create_collection
        # means an existing (pre-ARCH-4) workspace transparently gets an empty
        # purpose collection rather than an error; queries against it then return
        # no candidates until the workspace is (re)indexed, which is exactly the
        # graceful body-only fallback the spec calls for.
        self._purpose_collection = self._client.get_or_create_collection(
            name="code_chunks_purpose",
            metadata={
                "hnsw:space": "cosine",
                "hnsw:construction_ef": 200,
                "hnsw:search_ef": 100,
                "hnsw:M": 32,
            },
        )

    def _recreate_collections(self) -> None:
        """Drop and recreate both vector collections.

        ChromaDB pins a collection's embedding dimensionality at first insert
        and keeps it even after every entry is deleted, so an embedding-model
        swap to a DIFFERENT-dimension model would crash the per-file
        delete-then-reinsert rebuild (`force=True`) against the old
        collection. On an embed-model stamp mismatch the collections
        themselves must be dropped, not just their contents — this also
        guarantees no old-model vector can survive regardless of any
        per-file bookkeeping.
        """
        for name in ("code_chunks", "code_chunks_purpose"):
            try:
                self._client.delete_collection(name)
            except Exception:
                pass  # collection may not exist yet — nothing to drop
        self._create_collections()
        # Both collections are freshly (re)created and empty — no scan needed
        # to know that; mark stats seeded directly (UPG-REST-STARVATION).
        with self._stats_lock:
            self._lang_chunk_counts = {}
            self._lang_files = {}
            self._stats_seeded = True
            self._total_chunks_cache = 0
            self._purpose_chunks_cache = 0

    @property
    def all_roots(self) -> list[Path]:
        """All workspace roots: primary first, then extra roots in order."""
        return [self.workspace_root] + self._extra_roots

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_workspace(
        self, gitignore_patterns: list[str] | None = None, force: bool = False,
    ) -> tuple[int, int]:
        """Walk workspace, index all supported files. Returns (files_indexed, chunks_total).

        Three-phase pipeline:
          1. Parallel chunking   — ThreadPoolExecutor, tree-sitter releases GIL
          2. Global batch embed  — 256-chunk batches across all files (vs 64 per-file)
          3. Incremental skip    — files unchanged since last index are skipped via mtime cache

        Before indexing, chunks for files no longer in the walk set (excluded via
        .vectrignore/.gitignore, deleted, or moved out of all roots) are pruned so
        the collection never carries orphaned chunks (UPG-8.4).

        force=True ignores the mtime cache and re-chunks/re-embeds every file,
        replacing its chunks — a clean rebuild that recovers from any cache/
        collection desync without a manual cache wipe (UPG-8.6).
        """
        from integrations.workspace_detect import (
            should_index_file, get_gitignore_patterns, get_vectrignore_dirs,
            get_vectrignore_file_globs, get_vectrignore_regexes,
        )

        # Collect candidate files across all roots; each root gets its own
        # gitignore/vectrignore patterns so per-project exclusions are respected.
        all_files: list[Path] = []
        for root in self.all_roots:
            root_patterns = gitignore_patterns or get_gitignore_patterns(str(root))
            # UPG-13.3: .vectrignore file-glob entries (e.g. "*.generated.py") are
            # matched the same way as gitignore patterns — additive, on top of the
            # existing bare directory-name exclusions below.
            root_patterns = [*root_patterns, *get_vectrignore_file_globs(str(root))]
            vectrignore_dirs = get_vectrignore_dirs(str(root))
            # UPG-EXCLUDE-REGEX: `re:<pattern>` .vectrignore entries, matched
            # against each file's workspace-relative path — additive, on top
            # of the dir-name and glob exclusions above.
            vectrignore_regexes = get_vectrignore_regexes(str(root))
            all_excluded = EXCLUDED_DIRS | vectrignore_dirs
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in all_excluded and not d.startswith(".")]
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    if should_index_file(str(fpath), root_patterns, extra_excluded_dirs=vectrignore_dirs,
                                         workspace_root=str(root),
                                         extra_excluded_regexes=vectrignore_regexes):
                        all_files.append(fpath)

        should_index_paths = {str(f) for f in all_files}

        # Reconcile: drop chunks for files no longer indexable (excluded via
        # .vectrignore/.gitignore, deleted, or moved out of all roots). Without
        # this, editing .vectrignore stops *new* indexing but leaves the old
        # chunks in the collection forever. (UPG-8.4)
        mtime_cache = self._load_mtime_cache()
        pruned = self._prune_orphaned_chunks(should_index_paths, mtime_cache)

        # UPG-EMBEDDER-SWAP-GRANITE: vectors from two different embedding
        # models must never silently coexist in one collection (a same-
        # dimension model swap is exactly the silent-corruption case — cosine
        # distances between the two spaces are meaningless even though
        # ChromaDB's HNSW index accepts them without error). A stamp mismatch
        # — including a MISSING stamp, treated as a mismatch rather than a
        # match since a pre-existing index from an older vectr version
        # predates this mechanism — drops and recreates both collections
        # (ChromaDB pins dimensionality at first insert, so a different-
        # dimension model would otherwise crash the reinsert) and forces the
        # full-rebuild path: every file is re-chunked/re-embedded into the
        # fresh collections, so no old-model vector can survive.
        stored_embed_model = self._stored_embed_model()
        if stored_embed_model != self.embed_model:
            if not force:
                logger.warning(
                    "Embedding model changed (index built with %r, now configured "
                    "with %r) — forcing a full vector index rebuild so vectors "
                    "from the two models never mix in one collection",
                    stored_embed_model, self.embed_model,
                )
                force = True
            self._recreate_collections()

        if force:
            # Clean rebuild: ignore the mtime cache so every file is re-indexed,
            # and (in Phase 2) its existing chunks are deleted first. (UPG-8.6)
            mtime_cache = {}

        # Incremental: split into files to index vs unchanged files to skip
        to_index: list[tuple[Path, float]] = []
        for f in all_files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            cached = mtime_cache.get(str(f))
            if cached is None or cached != mtime:
                to_index.append((f, mtime))
            else:
                self._indexed_files.add(str(f))  # already indexed — track in memory

        if not to_index:
            if pruned:
                self._save_mtime_cache(mtime_cache)  # persist orphan removals
            self._write_embed_model_stamp()  # cheap no-op when already current
            logger.info("All %d files up to date — nothing to re-index", len(all_files))
            self._last_indexed = time.time()
            self._refresh_chunk_count_caches()
            return len(self._indexed_files), self.total_chunks

        logger.info(
            "Indexing %d/%d files (%d unchanged, skipped)...",
            len(to_index), len(all_files), len(all_files) - len(to_index),
        )

        # Phase 1: parallel chunking
        all_chunks: list[CodeChunk] = []
        new_mtimes: dict[str, float] = {}

        def _safe_chunk(item: tuple[Path, float]) -> tuple[list[CodeChunk], str, float]:
            fpath, mtime = item
            try:
                return chunk_file(str(fpath)), str(fpath), mtime
            except Exception:
                return [], str(fpath), mtime

        with ThreadPoolExecutor(max_workers=_CHUNK_WORKERS) as pool:
            futures = {pool.submit(_safe_chunk, item): item for item in to_index}
            done = 0
            for fut in as_completed(futures):
                chunks, fpath_str, mtime = fut.result()
                seen: set[str] = set()
                for c in chunks:
                    if c.chunk_id not in seen:
                        seen.add(c.chunk_id)
                        all_chunks.append(c)
                self._indexed_files.add(fpath_str)
                new_mtimes[fpath_str] = mtime
                done += 1
                if done % 50 == 0 or done == len(to_index):
                    logger.info("  chunked %d/%d files (%d chunks so far)...",
                                done, len(to_index), len(all_chunks))

        if not all_chunks:
            self._last_indexed = time.time()
            self._refresh_chunk_count_caches()
            return len(self._indexed_files), self.total_chunks

        # Phase 2: delete stale chunks for re-indexed files (no-op for brand-new files).
        # Under force, mtime_cache is empty, so delete by file_path unconditionally
        # to avoid leaving stale chunks whose ids no longer match (UPG-8.6).
        # `_delete_chunks_for_file` handles both collections (the purpose
        # collection, ARCH-4, is keyed by the same file_path metadata) and
        # applies the incremental stats delta — same helper `delete_file()`
        # and the watcher's batched deletes use (UPG-REST-STARVATION).
        phase_start = time.time()
        for fpath_str in new_mtimes:
            if force or fpath_str in mtime_cache:  # previously indexed → delete old chunks
                self._delete_chunks_for_file(fpath_str)
        logger.info("  stale-chunk sweep done: %d files in %.0fs",
                    len(new_mtimes), time.time() - phase_start)

        # Phase 3: streaming batched embed + upsert (UPG-INDEX-MEM-STREAMING).
        # Embed in large batches (256) for BLAS efficiency, upsert each embed
        # batch immediately in smaller sub-batches (100, SQLite's 999-variable
        # limit: 6 metadata fields x 100 rows = 600) before embedding the next
        # batch. Peak memory is O(_EMBED_BATCH_SIZE) embeddings, never
        # O(corpus) — a full-corpus `all_embeddings` list was the dominant
        # memory cost on large workspaces (~4 GB of Python floats at 170k
        # chunks), and doubled while the purpose pass built its own full-
        # corpus list concurrently. ids/documents/metadatas are built per
        # batch from the `all_chunks` slice rather than once for the whole
        # corpus, for the same reason.
        total = len(all_chunks)
        phase_start = time.time()
        for i in range(0, total, _EMBED_BATCH_SIZE):
            batch = all_chunks[i: i + _EMBED_BATCH_SIZE]
            batch_ids = [c.chunk_id for c in batch]
            batch_docs = [c.content for c in batch]
            batch_metas = [_chunk_metadata(c) for c in batch]
            batch_embeddings = self._embed_provider.embed(batch_docs)
            _upsert_in_batches(
                self._collection, batch_ids, batch_docs, batch_metas,
                batch_embeddings, _UPSERT_BATCH_SIZE, op_label="upsert (body)",
            )
            self._apply_chunk_delta(batch_metas, sign=1)
            if i % (10 * _EMBED_BATCH_SIZE) == 0 and i > 0:
                logger.info("  embedded %d/%d chunks...", i, total)
        logger.info("  content embed+upsert done: %d chunks in %.0fs",
                    total, time.time() - phase_start)

        self._upsert_purpose_vectors(all_chunks)

        logger.info("Indexed %d chunks from %d files", total, len(to_index))

        # Persist mtime cache
        mtime_cache.update(new_mtimes)
        self._save_mtime_cache(mtime_cache)
        self._write_embed_model_stamp()

        self._last_indexed = time.time()
        self._refresh_chunk_count_caches()
        return len(self._indexed_files), self.total_chunks

    def index_file(self, file_path: str) -> int:
        """Chunk and embed a single file. Returns number of chunks indexed."""
        chunks = chunk_file(file_path)
        if not chunks:
            return 0

        # Deduplicate by chunk_id (AST nodes on the same line range can collide in
        # minified files like jquery.min.js where many nodes share line 2-2)
        seen_ids: set[str] = set()
        deduped: list = []
        for c in chunks:
            if c.chunk_id not in seen_ids:
                seen_ids.add(c.chunk_id)
                deduped.append(c)
        chunks = deduped

        # Remove old chunks for this file before re-indexing
        self.delete_file(file_path)

        # Streaming embed + upsert (same helper as index_workspace's Phase 3,
        # UPG-INDEX-MEM-STREAMING) — a single file's chunk count is already
        # small, but reusing the shared helper keeps the two paths' upsert
        # batching identical rather than a second hand-maintained copy.
        for i in range(0, len(chunks), _FILE_BATCH_SIZE):
            batch = chunks[i: i + _FILE_BATCH_SIZE]
            batch_ids = [c.chunk_id for c in batch]
            batch_docs = [c.content for c in batch]
            batch_metas = [_chunk_metadata(c) for c in batch]
            batch_embeddings = self._embed_provider.embed(batch_docs)
            assert len(batch_embeddings) == len(batch_ids), (
                f"Embed provider returned {len(batch_embeddings)} embeddings "
                f"for {len(batch_ids)} chunks"
            )
            _upsert_in_batches(
                self._collection, batch_ids, batch_docs, batch_metas,
                batch_embeddings, _FILE_BATCH_SIZE, op_label="upsert (body)",
            )
            self._apply_chunk_delta(batch_metas, sign=1)

        self._upsert_purpose_vectors(chunks)

        self._indexed_files.add(file_path)
        self._refresh_chunk_count_caches()
        return len(chunks)

    def delete_file(self, file_path: str) -> int:
        """Remove all chunks belonging to a file (body + purpose collections).

        Returns the number of body-collection chunks removed — used by the
        watcher's batch worker for its per-batch churn diagnostic
        (UPG-WATCH-REVERT-CHURN)."""
        removed = self._delete_chunks_for_file(file_path)
        self._indexed_files.discard(file_path)
        self._refresh_chunk_count_caches()
        return removed

    def _delete_chunks_for_file(self, file_path: str) -> int:
        """Remove chunks for `file_path` from both collections and apply the
        incremental stats delta (UPG-REST-STARVATION). Internal: unlike
        `delete_file()`, does not touch `_indexed_files` — used by
        `index_workspace`'s stale-chunk sweep, where the file is about to be
        immediately re-indexed within the same call, not removed from the
        index. Returns the number of body-collection chunks removed.
        """
        removed = 0
        try:
            with _timed_chroma_call("get"):
                existing = self._collection.get(where={"file_path": file_path})
            ids = existing["ids"]
            if ids:
                with _timed_chroma_call("delete"):
                    self._collection.delete(ids=ids)
                self._apply_chunk_delta(existing.get("metadatas") or [], sign=-1)
                removed = len(ids)
        except Exception:
            pass
        try:
            with _timed_chroma_call("get"):
                existing_p = self._purpose_collection.get(where={"file_path": file_path})
            if existing_p["ids"]:
                with _timed_chroma_call("delete"):
                    self._purpose_collection.delete(ids=existing_p["ids"])
        except Exception:
            pass
        return removed

    # ------------------------------------------------------------------
    # Purpose vectors (ARCH-4 dual-vector pool entry)
    # ------------------------------------------------------------------

    def _upsert_purpose_vectors(self, chunks: list[CodeChunk]) -> None:
        """Embed + upsert the body-stripped purpose text for symbol chunks.

        Skipped entirely when `DUAL_VECTOR_ENABLED` is False (config, default
        on) — reduces to the pre-ARCH-4 body-only index. Non-symbol chunks
        (`build_purpose_text` returns None) are not written — the purpose
        collection stays a strict subset of the body collection's ids.

        Streaming, same shape as index_workspace's Phase 3 (UPG-INDEX-MEM-
        STREAMING): `build_purpose_text` is called per _EMBED_BATCH_SIZE
        batch of `chunks` (not once up front for the whole corpus), and each
        batch's embeddings are upserted immediately, keeping peak memory
        O(batch). Previously this ran while the body pass's own full-corpus
        `all_embeddings`/`documents`/`metadatas` were still live in the
        caller's frame — the two full-corpus embedding lists held concurrently
        were the dominant swap driver on large workspaces.
        """
        if not _DUAL_VECTOR_ENABLED or not chunks:
            return
        total = sum(1 for c in chunks if is_symbol_bearing_chunk(c.symbol_name, c.node_type))
        if total == 0:
            return

        phase_start = time.time()
        done = 0
        for i in range(0, len(chunks), _EMBED_BATCH_SIZE):
            batch = chunks[i: i + _EMBED_BATCH_SIZE]
            batch_ids: list[str] = []
            batch_docs: list[str] = []
            batch_metas: list[dict] = []
            for c in batch:
                purpose_text = build_purpose_text(c.content, c.symbol_name, c.node_type, c.language)
                if purpose_text is None:
                    continue
                batch_ids.append(c.chunk_id)
                batch_docs.append(purpose_text)
                batch_metas.append(_chunk_metadata(c))
            if batch_ids:
                batch_embeddings = self._embed_provider.embed(batch_docs)
                _upsert_in_batches(
                    self._purpose_collection, batch_ids, batch_docs, batch_metas,
                    batch_embeddings, _UPSERT_BATCH_SIZE, op_label="upsert (purpose)",
                )
                done += len(batch_ids)
            if i % (10 * _EMBED_BATCH_SIZE) == 0 and i > 0:
                logger.info("  purpose-embedded %d/%d symbol chunks...", done, total)
        logger.info("  purpose embed+upsert done: %d symbol chunks in %.0fs",
                    total, time.time() - phase_start)

    # ------------------------------------------------------------------
    # mtime cache — tracks file modification times for incremental indexing
    # ------------------------------------------------------------------

    def _mtime_cache_path(self) -> Path:
        # Co-locate the mtime cache with the ChromaDB dir so the two always clear
        # together. Previously this hardcoded ~/.cache/vectr/db/<hash> regardless
        # of the db_path override, so when the collection lived elsewhere (e.g. the
        # service's ~/.cache/vectr/<hash>/chroma) the cache could desync — a stale
        # cache then reported "nothing to re-index" against an empty collection,
        # leaving 0 chunks. (UPG-8.5)
        return self._db_dir / "index_cache.json"

    def _load_mtime_cache(self) -> dict[str, float]:
        path = self._mtime_cache_path()
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        # Schema-version gate: a cache written by an older INDEXING_SCHEMA_VERSION
        # (e.g. before ARCH-4's purpose vector existed) is treated as a cold
        # cache — every file falls into `to_index` on the next index_workspace()
        # and is fully re-chunked/re-embedded, the same recovery path force=True
        # uses. This is what makes a pipeline change (new derived vector, new
        # chunk content) reach an already-indexed workspace without a manual
        # cache wipe. A cache with no version key at all (pre-ARCH-4, unversioned)
        # is also treated as stale.
        stored_version = cache.pop(_MTIME_CACHE_SCHEMA_KEY, None)
        if stored_version != INDEXING_SCHEMA_VERSION:
            return {}
        return cache

    def _save_mtime_cache(self, cache: dict[str, float]) -> None:
        path = self._mtime_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(cache)
            payload[_MTIME_CACHE_SCHEMA_KEY] = INDEXING_SCHEMA_VERSION
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Embedding-model version stamp (UPG-EMBEDDER-SWAP-GRANITE) — see
    # _EMBED_MODEL_STAMP_FILE's docstring for why this is a separate file
    # from the mtime cache rather than another key inside it.
    # ------------------------------------------------------------------

    def _embed_model_stamp_path(self) -> Path:
        return self._db_dir / _EMBED_MODEL_STAMP_FILE

    def _stored_embed_model(self) -> str | None:
        """The embed_model that built the collection's CURRENT vectors, or
        None if no stamp exists yet (fresh index, or a pre-existing index
        from a vectr version that predates this mechanism — callers must
        treat None as a mismatch, not a match)."""
        path = self._embed_model_stamp_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        model = data.get("embed_model")
        return str(model) if model else None

    def _write_embed_model_stamp(self) -> None:
        path = self._embed_model_stamp_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"embed_model": self.embed_model}), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Orphan pruning — reconcile the collection against the current walk
    # ------------------------------------------------------------------

    def _collection_file_paths(self) -> set[str]:
        """Distinct file_path values currently stored in the collection.

        Metadata-only paginated scan (no documents/embeddings loaded)."""
        _PAGE = 1000
        paths: set[str] = set()
        offset = 0
        while True:
            with _timed_chroma_call("get"):
                page = self._collection.get(include=["metadatas"], limit=_PAGE, offset=offset)
            ids = page["ids"]
            if not ids:
                break
            for meta in page["metadatas"]:
                fp = meta.get("file_path")
                if fp:
                    paths.add(fp)
            offset += len(ids)
            if len(ids) < _PAGE:
                break
        return paths

    def _prune_orphaned_chunks(
        self, should_index_paths: set[str], mtime_cache: dict[str, float],
    ) -> int:
        """Delete chunks for indexed files no longer in the walk set. Returns count.

        Files become orphaned when they're newly excluded (.vectrignore/.gitignore),
        deleted, or moved out of all roots. `mtime_cache` is mutated in place so the
        pruned entries don't linger and re-trigger a phantom skip. (UPG-8.4)
        """
        try:
            indexed_paths = self._collection_file_paths()
        except Exception:
            return 0
        orphaned = indexed_paths - should_index_paths
        for path in orphaned:
            self.delete_file(path)
            mtime_cache.pop(path, None)
        if orphaned:
            logger.info("Pruned chunks for %d orphaned/excluded file(s)", len(orphaned))
        return len(orphaned)

    # ------------------------------------------------------------------
    # Stats — incrementally maintained, never a full-collection rescan on
    # the hot path (UPG-REST-STARVATION). See `_apply_chunk_delta` and
    # `_ensure_stats_seeded` for the mechanism.
    # ------------------------------------------------------------------

    def _ensure_stats_seeded(self) -> None:
        """One-time full metadata scan seeding `_lang_chunk_counts`/
        `_lang_files`, run at most once per collection generation
        (`_recreate_collections` re-arms it without a rescan, since a
        freshly (re)created collection is known-empty). Every insert/delete
        after this updates the counters directly via `_apply_chunk_delta`,
        so `indexed_language_stats()` never re-scans the collection again on
        a later call — this is what previously made a `/v1/status` call
        during a bulk reindex pay for a full paginated metadata scan of
        `self._collection` on every single call (the old cache was keyed on
        chunk count, which was still changing on every watcher write),
        directly contending with the collection the watcher was writing to.

        Now also run eagerly once at the end of `CodeIndexer.__init__`
        (always off the request event loop — construction either happens
        synchronously before the daemon starts serving requests, or on its
        own background thread), so this scan is never triggered by the
        first live request to reach `indexed_language_stats()`
        (UPG-CHROMA-BLOCKING-EVENT-LOOP). This method staying idempotent and
        safe to call redundantly (the `_stats_seeded` guard below) is what
        makes that eager call harmless.

        `total_chunks`/`total_purpose_chunks` use a SEPARATE in-memory
        counter (`_total_chunks_cache`/`_purpose_chunks_cache`), not this
        cache — see `total_chunks`'s docstring.
        """
        if self._stats_seeded:
            return
        with self._stats_lock:
            if self._stats_seeded:  # re-check inside the lock
                return
            _PAGE = 1000
            chunks: dict[str, int] = {}
            files: dict[str, set[str]] = {}
            offset = 0
            while True:
                with _timed_chroma_call("get"):
                    page = self._collection.get(include=["metadatas"], limit=_PAGE, offset=offset)
                ids = page["ids"]
                if not ids:
                    break
                for meta in page["metadatas"]:
                    lang = meta.get("language")
                    if not lang:
                        continue
                    chunks[lang] = chunks.get(lang, 0) + 1
                    fp = meta.get("file_path")
                    if fp:
                        files.setdefault(lang, set()).add(fp)
                offset += len(ids)
                if len(ids) < _PAGE:
                    break
            self._lang_chunk_counts = chunks
            self._lang_files = files
            self._stats_seeded = True

    def _apply_chunk_delta(self, metadatas: list[dict], sign: int) -> None:
        """Incrementally update the in-memory per-language stats cache from a
        batch of chunk metadata just inserted (`sign=1`) or removed
        (`sign=-1`) from the body collection. Held only around O(1) dict
        arithmetic — never around a ChromaDB or embedding call — so this
        lock is never the thing a request handler waits on
        (UPG-REST-STARVATION).
        """
        if not metadatas:
            return
        with self._stats_lock:
            for meta in metadatas:
                lang = meta.get("language")
                if not lang:
                    continue
                self._lang_chunk_counts[lang] = max(
                    0, self._lang_chunk_counts.get(lang, 0) + sign
                )
                fp = meta.get("file_path")
                if sign > 0:
                    if fp:
                        self._lang_files.setdefault(lang, set()).add(fp)
                elif fp:
                    files = self._lang_files.get(lang)
                    if files is not None:
                        files.discard(fp)

    def _refresh_chunk_count_caches(self) -> None:
        """Refresh `total_chunks`/`total_purpose_chunks` from a real, one-off
        ChromaDB `.count()` call on each collection (UPG-CHROMA-BLOCKING-
        EVENT-LOOP). Called once at the end of every mutation entry point —
        `index_file`, `delete_file`, `index_workspace` — never per individual
        upsert/delete inside them, so a bulk reindex still pays for this only
        once per call, not once per batch. Always runs on whatever thread
        performed the mutation (the indexing/watcher thread, or a request's
        off-loop dispatch-executor thread — see agent/chroma_dispatch.py) —
        never on a request's own event-loop thread, since it is only ever
        reached from inside one of those three methods.

        A real re-count (rather than incremental delta bookkeeping) is what
        makes this self-correcting: it is exactly as accurate as a live call
        would have been for anything that mutated the collection through one
        of these three methods, and it does not need special-case handling
        for a collection changed some other way (a test simulating a desync,
        an external tool) — the next call through any of the three methods
        re-syncs it regardless of how it went stale.
        """
        with _timed_chroma_call("count"):
            total = self._collection.count()
        with _timed_chroma_call("count"):
            purpose_total = self._purpose_collection.count()
        with self._stats_lock:
            self._total_chunks_cache = total
            self._purpose_chunks_cache = purpose_total

    @property
    def total_chunks(self) -> int:
        """Total chunk count in the body collection.

        Returns an in-memory counter, refreshed from a real ChromaDB count at
        construction and again at the end of every mutation entry point
        (`index_file`/`delete_file`/`index_workspace`, via
        `_refresh_chunk_count_caches`) — never a live call into the vector
        store made from this property itself. This trades bounded staleness
        (at most one in-flight mutation call, applied the moment that call
        finishes) for availability: a caller reading this must never wait on
        the vector store's own internal state, including while it is busy
        compacting a large collection — a single `/v1/status` read blocking
        behind exactly that once made every other request the daemon was
        serving unreachable (UPG-CHROMA-BLOCKING-EVENT-LOOP).
        """
        with self._stats_lock:
            return self._total_chunks_cache

    @property
    def total_purpose_chunks(self) -> int:
        """Purpose-collection counterpart of `total_chunks` (ARCH-4
        dual-vector pool entry) — same in-memory-counter treatment. Read by
        `query_vector_purpose`'s empty-collection guard instead of a live
        ChromaDB count() call."""
        with self._stats_lock:
            return self._purpose_chunks_cache

    @property
    def last_indexed_ts(self) -> float:
        return self._last_indexed

    @property
    def indexed_file_count(self) -> int:
        return len(self._indexed_files)

    @property
    def indexed_file_paths(self) -> list[str]:
        """Return a copy of all file paths currently in the index."""
        return list(self._indexed_files)

    def indexed_language_stats(self) -> dict[str, dict[str, int]]:
        """Per-language coverage in the collection: `{lang: {"files", "chunks"}}`.

        The ground truth for what the index actually contains — derived from
        chunk metadata, not a fixed allow-list. Reads the incrementally-
        maintained cache (`_ensure_stats_seeded` / `_apply_chunk_delta`) —
        O(languages present), never a re-scan of the collection, so this stays
        cheap to call even while a bulk reindex is concurrently writing to the
        same collection (UPG-REST-STARVATION). UPG-3.1 (which languages exist)
        and UPG-3.3 (per-language coverage + symbol availability) both read
        from this single source.
        """
        self._ensure_stats_seeded()
        with self._stats_lock:
            return {
                lang: {"files": len(self._lang_files.get(lang, ())), "chunks": n}
                for lang, n in self._lang_chunk_counts.items()
            }

    def indexed_languages(self) -> list[str]:
        """Distinct, sorted `language` values currently in the collection (UPG-3.1)."""
        return sorted(self.indexed_language_stats())

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search-query string. Public method for external callers.

        Uses the embed provider's query mode (`embed_query`), not `embed` — for
        asymmetric models (the default arctic-embed) this applies the model's
        registered query prompt, which is required for correct dense retrieval and
        must never be applied on the document/indexing side.
        """
        return self._embed_provider.embed_query([text])[0]

    def embed_query_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of search-query strings using the provider's query mode.

        For working-memory recall, where a query is matched against previously
        stored note embeddings — same query/document asymmetry as code search.
        """
        return self._embed_provider.embed_query(texts)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (document/indexing side) using the configured
        embed provider. Never applies a query prompt — for chunk/note content being
        stored, not for search queries."""
        return self._embed_provider.embed(texts)

    @property
    def chroma_client(self):
        """The underlying ChromaDB client — shared with the working memory collection."""
        return self._client

    def close(self) -> None:
        """Release the underlying ChromaDB client's resources (its System —
        connections, and the native worker-thread pool ChromaDB's Rust
        bindings start per client). `chromadb`'s own `.close()` is already
        idempotent, so calling this more than once (or after `__del__` has
        already run it) is safe.

        Every `CodeIndexer` — hence every real `VectrService` — otherwise
        leaks a fixed-size native thread pool for the life of the process:
        confirmed empirically, a fresh `chromadb.PersistentClient()` left
        unclosed adds ~13 OS threads that `threading.active_count()` never
        sees (native, not Python-managed) and that never get reclaimed.
        Harmless for a single short-lived process, but a real leak for any
        long-running process — or test suite — that constructs many
        `CodeIndexer` instances over its lifetime. `VectrService.shutdown()`
        calls this; `__del__` below is a safety net for callers (tests,
        scripts) that construct a `CodeIndexer` directly and let it fall out
        of scope without an explicit shutdown path."""
        client = getattr(self, "_client", None)
        if client is not None:
            client.close()

    def __del__(self) -> None:
        # Best-effort only — must never raise, especially during interpreter
        # shutdown when module globals this depends on may already be torn
        # down (attribute lookups can fail in that window).
        try:
            self.close()
        except Exception:
            pass

    def get_all_documents(self) -> tuple[list[str], list[str], list[dict]]:
        """Return (ids, documents, metadatas) for all stored chunks — used by BM25 index.

        Paginates in batches of 500 to avoid ChromaDB's SQLite variable limit (~999).
        """
        _PAGE = 500
        all_ids: list[str] = []
        all_docs: list[str] = []
        all_meta: list[dict] = []
        offset = 0
        while True:
            with _timed_chroma_call("get"):
                page = self._collection.get(
                    include=["documents", "metadatas"],
                    limit=_PAGE,
                    offset=offset,
                )
            batch_ids = page["ids"]
            if not batch_ids:
                break
            all_ids.extend(batch_ids)
            all_docs.extend(page["documents"])
            all_meta.extend(page["metadatas"])
            offset += len(batch_ids)
            if len(batch_ids) < _PAGE:
                break
        return all_ids, all_docs, all_meta

    def query_vector(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        language: str | None = None,
        languages: list[str] | None = None,
    ) -> dict:
        # `languages` (a set/list) takes precedence over a single `language`:
        # restricts the vector search to any of the given languages via an $in
        # filter. Used by the doc-intent pool reservation (UPG-15.13) to fetch
        # documentation-prose chunks that embed below the unfiltered fetch depth.
        if languages:
            where = {"language": {"$in": list(languages)}}
        elif language:
            where = {"language": language}
        else:
            where = None
        with _timed_chroma_call("query"):
            return self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, max(1, self.total_chunks)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )

    def get_chunk_cosine_similarities(
        self, query_embedding: list[float], chunk_ids: list[str]
    ) -> dict[str, float]:
        """Return cosine similarity scores for a specific set of chunk IDs.

        Used by the forced-inclusion relevance gate (UPG-11.12) to check vector
        similarity for non-compound candidates that are not in the natural pool.
        ChromaDB embeddings are L2-normalised by default, so cosine = dot product.

        Returns a dict {chunk_id: cosine_similarity}.  Chunk IDs not found in the
        collection are absent from the result (similarity treated as 0.0 by caller).
        """
        if not chunk_ids or not query_embedding:
            return {}
        try:
            with _timed_chroma_call("get"):
                batch = self._collection.get(ids=chunk_ids, include=["embeddings"])
        except Exception:
            return {}
        result: dict[str, float] = {}
        import numpy as np
        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return result
        # batch["embeddings"] may be a numpy array — avoid truthiness check on it.
        embeddings_raw = batch.get("embeddings")
        ids_raw = batch.get("ids")
        if embeddings_raw is None or ids_raw is None:
            return result
        embeddings_list = list(embeddings_raw) if not isinstance(embeddings_raw, list) else embeddings_raw
        ids_list = list(ids_raw) if not isinstance(ids_raw, list) else ids_raw
        for cid, emb_raw in zip(ids_list, embeddings_list):
            if emb_raw is None:
                continue
            emb = np.asarray(emb_raw, dtype=np.float32)
            if emb.size == 0:
                continue
            dot = float(np.dot(q, emb))
            e_norm = float(np.linalg.norm(emb))
            if e_norm == 0.0:
                continue
            result[cid] = dot / (q_norm * e_norm)
        return result

    def query_vector_purpose(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        language: str | None = None,
        languages: list[str] | None = None,
    ) -> dict:
        """Query the purpose-vector collection (ARCH-4 dual-vector pool entry).

        Mirrors `query_vector` but against `self._purpose_collection`, which only
        holds symbol-bearing chunks (see `_upsert_purpose_vectors`). A workspace
        indexed before ARCH-4 shipped has an empty purpose collection — `count()`
        guards against ChromaDB raising on a query against zero rows, so old
        indexes degrade gracefully to body-only results until reindexed.
        """
        count = self.total_purpose_chunks
        if count == 0:
            return {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        if languages:
            where = {"language": {"$in": list(languages)}}
        elif language:
            where = {"language": language}
        else:
            where = None
        with _timed_chroma_call("query"):
            return self._purpose_collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, max(1, count)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )

    def fetch_chunks(self, ids: list[str]) -> list[dict]:
        """Deterministic re-fetch by chunk id (UPG-CTX-EVICT part a).

        A direct ``self._collection.get(ids=...)`` — no embedding, no rerank,
        no ranking of any kind. Every rendered search result carries its
        chunk's exact ChromaDB id (``file_path:start_line-end_line``); this is
        the counterpart lookup that restores the full chunk from that id
        alone, so a chunk cleared from the caller's context (by harness tool-
        result eviction, ``/compact``, or an API context-editing tombstone)
        never requires a re-search or a blind file re-read.

        Returns one dict per requested id, in REQUEST order (Chroma's own
        ``get(ids=...)`` does not preserve order and silently omits ids it
        doesn't have — both are corrected here):
          {"id": ..., "found": True,  "file_path": ..., "start_line": ...,
           "end_line": ..., "symbol_name": ..., "language": ..., "content": ...}
          {"id": ..., "found": False}

        Raises ValueError if `ids` exceeds FETCH_MAX_IDS_PER_CALL.
        """
        from agent.config import FETCH_MAX_IDS_PER_CALL
        if len(ids) > FETCH_MAX_IDS_PER_CALL:
            raise ValueError(
                f"Too many ids requested ({len(ids)}) — vectr_fetch accepts at "
                f"most {FETCH_MAX_IDS_PER_CALL} per call."
            )
        if not ids:
            return []
        with _timed_chroma_call("get"):
            batch = self._collection.get(ids=ids, include=["documents", "metadatas"])
        found_ids = batch.get("ids") or []
        found_docs = batch.get("documents") or []
        found_metas = batch.get("metadatas") or []
        by_id: dict[str, tuple[str, dict]] = {
            cid: (doc, meta) for cid, doc, meta in zip(found_ids, found_docs, found_metas)
        }
        results: list[dict] = []
        for requested_id in ids:
            hit = by_id.get(requested_id)
            if hit is None:
                results.append({"id": requested_id, "found": False})
                continue
            doc, meta = hit
            results.append({
                "id": requested_id,
                "found": True,
                "file_path": meta.get("file_path", ""),
                "start_line": int(meta.get("start_line", 0)),
                "end_line": int(meta.get("end_line", 0)),
                "symbol_name": meta.get("symbol_name", ""),
                "language": meta.get("language", ""),
                "content": doc,
            })
        return results

    def get_chunk_documents(self, chunk_ids: list[str]) -> dict[str, tuple[str, dict]]:
        """Batch-fetch (document, metadata) from the body collection for given ids.

        Used to backfill body content/metadata for chunk ids that were only
        discovered via the purpose-vector query (ARCH-4) and therefore did not
        come back from the body `query_vector` call in the same search pass.
        Ids not found in the body collection are absent from the result.
        """
        if not chunk_ids:
            return {}
        try:
            with _timed_chroma_call("get"):
                batch = self._collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        except Exception:
            return {}
        ids_raw = batch.get("ids") or []
        docs_raw = batch.get("documents") or []
        metas_raw = batch.get("metadatas") or []
        return {
            cid: (doc, meta)
            for cid, doc, meta in zip(ids_raw, docs_raw, metas_raw)
        }
