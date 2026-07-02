"""CodeIndexer: ChromaDB-backed index orchestration (chunking + embed + upsert)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import chromadb
import numpy as np

from agent.chunk_quality import build_purpose_text
from agent.config import DUAL_VECTOR_ENABLED as _DUAL_VECTOR_ENABLED
from agent.indexer._constants import (
    EXCLUDED_DIRS,
    _FILE_BATCH_SIZE,
    _EMBED_BATCH_SIZE,
    _UPSERT_BATCH_SIZE,
    _CHUNK_WORKERS,
    _MTIME_CACHE_SCHEMA_KEY,
    INDEXING_SCHEMA_VERSION,
)
from agent.indexer._chunking import chunk_file
from agent.indexer._types import CodeChunk

logger = logging.getLogger(__name__)


class CodeIndexer:
    def __init__(
        self,
        workspace_root: str,
        embed_model: str = "Snowflake/snowflake-arctic-embed-m-v1.5",
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
        self._last_indexed: float = 0.0
        self._indexed_files: set[str] = set()
        self._lang_cache: tuple[int, dict[str, dict[str, int]]] | None = None  # (chunk_count, per-lang stats) — UPG-3.1/3.3

        # Deferred: look up get_embed_provider through the package namespace so that
        # test-time monkeypatching of agent.indexer.get_embed_provider is honoured
        # (identical to the original flat-module behaviour where the function lived
        # in the same module namespace that patches target).
        import agent.indexer as _idx
        self._embed_provider = _idx.get_embed_provider(embed_model)

    def _workspace_hash(self) -> str:
        return hashlib.md5(str(self.workspace_root).encode()).hexdigest()[:12]

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
        )

        # Collect candidate files across all roots; each root gets its own
        # gitignore/vectrignore patterns so per-project exclusions are respected.
        all_files: list[Path] = []
        for root in self.all_roots:
            root_patterns = gitignore_patterns or get_gitignore_patterns(str(root))
            vectrignore_dirs = get_vectrignore_dirs(str(root))
            all_excluded = EXCLUDED_DIRS | vectrignore_dirs
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in all_excluded and not d.startswith(".")]
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    if should_index_file(str(fpath), root_patterns, extra_excluded_dirs=vectrignore_dirs):
                        all_files.append(fpath)

        should_index_paths = {str(f) for f in all_files}

        # Reconcile: drop chunks for files no longer indexable (excluded via
        # .vectrignore/.gitignore, deleted, or moved out of all roots). Without
        # this, editing .vectrignore stops *new* indexing but leaves the old
        # chunks in the collection forever. (UPG-8.4)
        mtime_cache = self._load_mtime_cache()
        pruned = self._prune_orphaned_chunks(should_index_paths, mtime_cache)

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
            logger.info("All %d files up to date — nothing to re-index", len(all_files))
            self._last_indexed = time.time()
            return len(self._indexed_files), self._collection.count()

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
            return len(self._indexed_files), self._collection.count()

        # Phase 2: delete stale chunks for re-indexed files (no-op for brand-new files).
        # Under force, mtime_cache is empty, so delete by file_path unconditionally
        # to avoid leaving stale chunks whose ids no longer match (UPG-8.6). The
        # purpose collection (ARCH-4) is keyed by the same file_path metadata, so
        # its stale entries are dropped alongside the body collection's.
        for fpath_str in new_mtimes:
            if force or fpath_str in mtime_cache:  # previously indexed → delete old chunks
                try:
                    existing = self._collection.get(where={"file_path": fpath_str})
                    if existing["ids"]:
                        self._collection.delete(ids=existing["ids"])
                except Exception:
                    pass
                try:
                    existing_p = self._purpose_collection.get(where={"file_path": fpath_str})
                    if existing_p["ids"]:
                        self._purpose_collection.delete(ids=existing_p["ids"])
                except Exception:
                    pass

        # Phase 3: global batched embed + upsert
        # Embed in large batches (256) for BLAS efficiency, upsert in smaller batches (100)
        # to stay within SQLite's 999-variable limit (6 metadata fields × 100 rows = 600).
        ids = [c.chunk_id for c in all_chunks]
        documents = [c.content for c in all_chunks]
        metadatas = [
            {
                "file_path": c.file_path,
                "language": c.language,
                "node_type": c.node_type,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
            }
            for c in all_chunks
        ]

        total = len(ids)
        all_embeddings: list[list[float]] = []
        for i in range(0, total, _EMBED_BATCH_SIZE):
            batch_docs = documents[i: i + _EMBED_BATCH_SIZE]
            all_embeddings.extend(self._embed_provider.embed(batch_docs))
            if i % (10 * _EMBED_BATCH_SIZE) == 0 and i > 0:
                logger.info("  embedded %d/%d chunks...", i, total)

        for i in range(0, total, _UPSERT_BATCH_SIZE):
            self._collection.upsert(
                ids=ids[i: i + _UPSERT_BATCH_SIZE],
                documents=documents[i: i + _UPSERT_BATCH_SIZE],
                metadatas=metadatas[i: i + _UPSERT_BATCH_SIZE],
                embeddings=all_embeddings[i: i + _UPSERT_BATCH_SIZE],
            )

        self._upsert_purpose_vectors(all_chunks)

        logger.info("Indexed %d chunks from %d files", total, len(to_index))

        # Persist mtime cache
        mtime_cache.update(new_mtimes)
        self._save_mtime_cache(mtime_cache)

        self._last_indexed = time.time()
        return len(self._indexed_files), self._collection.count()

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

        ids = [c.chunk_id for c in chunks]
        documents = [c.content for c in chunks]
        metadatas = [
            {
                "file_path": c.file_path,
                "language": c.language,
                "node_type": c.node_type,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
            }
            for c in chunks
        ]

        # Embed in batches
        all_embeddings: list[list[float]] = []
        for i in range(0, len(documents), _FILE_BATCH_SIZE):
            batch = documents[i: i + _FILE_BATCH_SIZE]
            all_embeddings.extend(self._embed_provider.embed(batch))
        assert len(all_embeddings) == len(ids), (
            f"Embed provider returned {len(all_embeddings)} embeddings for {len(ids)} chunks"
        )

        for i in range(0, len(ids), _FILE_BATCH_SIZE):
            self._collection.upsert(
                ids=ids[i: i + _FILE_BATCH_SIZE],
                documents=documents[i: i + _FILE_BATCH_SIZE],
                metadatas=metadatas[i: i + _FILE_BATCH_SIZE],
                embeddings=all_embeddings[i: i + _FILE_BATCH_SIZE],
            )

        self._upsert_purpose_vectors(chunks)

        self._indexed_files.add(file_path)
        return len(chunks)

    def delete_file(self, file_path: str) -> None:
        """Remove all chunks belonging to a file (body + purpose collections)."""
        try:
            existing = self._collection.get(where={"file_path": file_path})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
            self._indexed_files.discard(file_path)
        except Exception:
            pass
        try:
            existing_p = self._purpose_collection.get(where={"file_path": file_path})
            if existing_p["ids"]:
                self._purpose_collection.delete(ids=existing_p["ids"])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Purpose vectors (ARCH-4 dual-vector pool entry)
    # ------------------------------------------------------------------

    def _upsert_purpose_vectors(self, chunks: list[CodeChunk]) -> None:
        """Embed + upsert the body-stripped purpose text for symbol chunks.

        Skipped entirely when `DUAL_VECTOR_ENABLED` is False (config, default
        on) — reduces to the pre-ARCH-4 body-only index. Non-symbol chunks
        (`build_purpose_text` returns None) are not written — the purpose
        collection stays a strict subset of the body collection's ids.
        """
        if not _DUAL_VECTOR_ENABLED or not chunks:
            return
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        for c in chunks:
            purpose_text = build_purpose_text(c.content, c.symbol_name, c.node_type, c.language)
            if purpose_text is None:
                continue
            ids.append(c.chunk_id)
            documents.append(purpose_text)
            metadatas.append({
                "file_path": c.file_path,
                "language": c.language,
                "node_type": c.node_type,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
            })
        if not ids:
            return

        total = len(ids)
        all_embeddings: list[list[float]] = []
        for i in range(0, total, _EMBED_BATCH_SIZE):
            all_embeddings.extend(self._embed_provider.embed(documents[i: i + _EMBED_BATCH_SIZE]))

        for i in range(0, total, _UPSERT_BATCH_SIZE):
            self._purpose_collection.upsert(
                ids=ids[i: i + _UPSERT_BATCH_SIZE],
                documents=documents[i: i + _UPSERT_BATCH_SIZE],
                metadatas=metadatas[i: i + _UPSERT_BATCH_SIZE],
                embeddings=all_embeddings[i: i + _UPSERT_BATCH_SIZE],
            )

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
    # Orphan pruning — reconcile the collection against the current walk
    # ------------------------------------------------------------------

    def _collection_file_paths(self) -> set[str]:
        """Distinct file_path values currently stored in the collection.

        Metadata-only paginated scan (no documents/embeddings loaded)."""
        _PAGE = 1000
        paths: set[str] = set()
        offset = 0
        while True:
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
    # Stats
    # ------------------------------------------------------------------

    @property
    def total_chunks(self) -> int:
        return self._collection.count()

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

        The ground truth for what the index actually contains — derived from chunk
        metadata, not a fixed allow-list. One metadata-only paginated scan, cached
        against the live chunk count so it only rescans when the index changes.
        UPG-3.1 (which languages exist) and UPG-3.3 (per-language coverage +
        symbol availability) both read from this single source.
        """
        count = self.total_chunks
        if self._lang_cache is not None and self._lang_cache[0] == count:
            return self._lang_cache[1]
        _PAGE = 1000
        chunks: dict[str, int] = {}
        files: dict[str, set[str]] = {}
        offset = 0
        while True:
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
        stats = {
            lang: {"files": len(files.get(lang, ())), "chunks": n}
            for lang, n in chunks.items()
        }
        self._lang_cache = (count, stats)
        return stats

    def indexed_languages(self) -> list[str]:
        """Distinct, sorted `language` values currently in the collection (UPG-3.1)."""
        return sorted(self.indexed_language_stats())

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. Public method for external callers."""
        return self._embed_provider.embed([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the configured embed provider."""
        return self._embed_provider.embed(texts)

    @property
    def chroma_client(self):
        """The underlying ChromaDB client — shared with the working memory collection."""
        return self._client

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
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, max(1, self._collection.count())),
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
        count = self._purpose_collection.count()
        if count == 0:
            return {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        if languages:
            where = {"language": {"$in": list(languages)}}
        elif language:
            where = {"language": language}
        else:
            where = None
        return self._purpose_collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, max(1, count)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

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
