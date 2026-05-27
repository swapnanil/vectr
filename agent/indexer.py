"""AST-aware code chunking and embedding pipeline."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

import chromadb
import numpy as np


# ---------------------------------------------------------------------------
# Language extension mapping
# ---------------------------------------------------------------------------

LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

EXCLUDED_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "dist", "build", ".build", ".next", ".nuxt", "target", "out",
    "coverage", ".coverage", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class CodeChunk:
    chunk_id: str
    content: str
    file_path: str
    language: str
    node_type: str
    start_line: int
    end_line: int
    symbol_name: str


# ---------------------------------------------------------------------------
# Embedding provider protocol
# ---------------------------------------------------------------------------

class EmbedProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedProvider:
    """Uses sentence-transformers (no API key). Default: Snowflake/snowflake-arctic-embed-m-v1.5."""

    def __init__(self, model_name: str = "Snowflake/snowflake-arctic-embed-m-v1.5") -> None:
        from sentence_transformers import SentenceTransformer
        cache_dir = Path.home() / ".cache" / "vectr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = SentenceTransformer(
            model_name,
            cache_folder=str(cache_dir),
            trust_remote_code=True,
            device="cpu",
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()


class VoyageEmbedProvider:
    """Uses Voyage AI code embedding model (requires VOYAGE_API_KEY)."""

    def __init__(self, model_name: str = "voyage-code-2") -> None:
        import voyageai  # type: ignore
        self._client = voyageai.Client()
        self._model = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._client.embed(texts, model=self._model)
        return result.embeddings


class OpenAIEmbedProvider:
    """Uses OpenAI embedding model (requires OPENAI_API_KEY)."""

    def __init__(self, model_name: str = "text-embedding-3-small") -> None:
        from openai import OpenAI
        self._client = OpenAI()
        self._model = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]


def get_embed_provider(model_spec: str) -> EmbedProvider:
    """Factory: parse VECTR_EMBED_MODEL and return the right provider."""
    if model_spec.startswith("voyage"):
        return VoyageEmbedProvider(model_spec)
    if model_spec.startswith("openai/"):
        return OpenAIEmbedProvider(model_spec.split("/", 1)[1])
    return LocalEmbedProvider(model_spec)


# ---------------------------------------------------------------------------
# Tree-sitter AST chunker
# ---------------------------------------------------------------------------

_PARSER_CACHE: dict[str, object] = {}


def _get_parser(language: str):
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    try:
        from tree_sitter import Language, Parser
        if language == "python":
            import tree_sitter_python as ts_lang
        elif language == "javascript":
            import tree_sitter_javascript as ts_lang
        elif language == "typescript":
            import tree_sitter_typescript as ts_lang
            parser = Parser(Language(ts_lang.language_typescript()))
            _PARSER_CACHE[language] = parser
            return parser
        elif language == "go":
            import tree_sitter_go as ts_lang
        elif language == "rust":
            import tree_sitter_rust as ts_lang
        elif language == "java":
            import tree_sitter_java as ts_lang
        else:
            return None
        parser = Parser(Language(ts_lang.language()))
        _PARSER_CACHE[language] = parser
        return parser
    except Exception:
        return None


_MAX_CHUNK_LINES = 150   # hard cap — prevents single huge chunks diluting embeddings
_CLASS_HEADER_LINES = 40  # lines kept for class-level chunk (sig + docstring + attrs)

# Node types that represent class declarations (handled specially — emit header + recurse)
_CLASS_NODE_TYPES = {"class_definition", "class_declaration"}

# Node types that represent top-level code units worth indexing per language
_CHUNK_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "function_expression", "arrow_function", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "function_expression", "arrow_function", "class_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration"},
    "rust": {"function_item", "impl_item"},
    "java": {"method_declaration", "class_declaration"},
}

_SYMBOL_FIELD: dict[str, str] = {
    "python": "name",
    "javascript": "name",
    "typescript": "name",
    "go": "name",
    "rust": "name",
    "java": "name",
}


def _extract_symbol_name(node, language: str, code_bytes: bytes) -> str:
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


def _get_leading_comments(lines: list[str], start_line: int) -> str:
    """Return the comment/decorator block immediately preceding start_line (1-indexed)."""
    collected: list[str] = []
    i = start_line - 2  # 0-indexed line just above the node
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith(("#", "//", "*", "/**", "@")):
            collected.insert(0, lines[i])
            i -= 1
        elif not stripped:
            i -= 1  # skip blank separator lines
        else:
            break
    return "\n".join(collected)


def _collect_chunks_ast(
    node,
    code_bytes: bytes,
    lines: list[str],
    language: str,
    file_path: str,
    target_types: set[str],
    results: list[CodeChunk],
    class_context: str = "",
) -> None:
    if node.type in target_types:
        start = node.start_point[0]  # 0-indexed
        end = node.end_point[0]
        raw = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        symbol = _extract_symbol_name(node, language, code_bytes)

        # Prepend leading comments/decorators (stripped from AST node but semantically important)
        leading = _get_leading_comments(lines, start + 1)

        # Prepend class context so method chunks are self-contained for the embedder
        context_prefix = f"# class: {class_context}\n" if class_context else ""

        parts = [p for p in [leading, context_prefix + raw] if p]
        content = "\n".join(parts)

        # Cap very long chunks — class bodies can be thousands of lines
        is_class = node.type in _CLASS_NODE_TYPES
        cap = _CLASS_HEADER_LINES if is_class else _MAX_CHUNK_LINES
        content_lines = content.splitlines()
        if len(content_lines) > cap:
            content = "\n".join(content_lines[:cap])

        chunk_id = f"{file_path}:{start + 1}-{end + 1}"
        results.append(CodeChunk(
            chunk_id=chunk_id,
            content=content,
            file_path=file_path,
            language=language,
            node_type=node.type,
            start_line=start + 1,
            end_line=end + 1,
            symbol_name=symbol,
        ))

        if is_class:
            # Also recurse into the class body so methods get their own chunks with context
            for child in node.children:
                _collect_chunks_ast(child, code_bytes, lines, language, file_path,
                                    target_types, results, class_context=symbol)
        return  # don't recurse further for non-class nodes (avoids duplicate nested defs)

    for child in node.children:
        _collect_chunks_ast(child, code_bytes, lines, language, file_path,
                            target_types, results, class_context=class_context)


def _fallback_window_chunks(lines: list[str], file_path: str, language: str) -> list[CodeChunk]:
    """Sliding-window chunker for files with no tree-sitter grammar.

    Window=200, overlap=50:
    - 200 lines captures ~3-5 typical functions worth of context — large enough for
      a coherent embedding, small enough that unrelated code doesn't dilute it.
      (100 lines is too small for classes; 500 lines degrades embedding quality.)
    - 50-line overlap ensures a function starting near the tail of one window is
      fully present in the next window, so it's never split across chunk boundaries.
    """
    window, overlap = 200, 50
    chunks: list[CodeChunk] = []
    i = 0
    while i < len(lines):
        end = min(i + window, len(lines))
        content = "\n".join(lines[i:end])
        chunk_id = f"{file_path}:{i + 1}-{end}"
        chunks.append(CodeChunk(
            chunk_id=chunk_id,
            content=content,
            file_path=file_path,
            language=language,
            node_type="window",
            start_line=i + 1,
            end_line=end,
            symbol_name="",
        ))
        i += window - overlap
    return chunks


def chunk_file(file_path: str) -> list[CodeChunk]:
    """Parse a file and return AST-aware chunks (falls back to windows)."""
    path = Path(file_path)
    ext = path.suffix.lower()
    language = LANG_BY_EXT.get(ext, "")

    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = code.splitlines()
    if not lines:
        return []

    if language:
        parser = _get_parser(language)
        if parser:
            code_bytes = code.encode("utf-8")
            tree = parser.parse(code_bytes)
            target_types = _CHUNK_NODE_TYPES.get(language, set())
            results: list[CodeChunk] = []
            _collect_chunks_ast(tree.root_node, code_bytes, lines, language, file_path, target_types, results)
            if results:
                return results
            # no top-level symbols found → fall through to windows

    # fallback
    lang_label = language or path.suffix.lstrip(".") or "text"
    return _fallback_window_chunks(lines, file_path, lang_label)


# ---------------------------------------------------------------------------
# Indexer: manages ChromaDB collection
# ---------------------------------------------------------------------------

_FILE_BATCH_SIZE = 64     # used by index_file() — single-file watcher path
_EMBED_BATCH_SIZE = 256   # texts per model.encode() call — larger = better BLAS utilisation
_UPSERT_BATCH_SIZE = 100  # rows per ChromaDB upsert — SQLite variable limit is 999; 6 fields×100=600
_CHUNK_WORKERS = min(8, os.cpu_count() or 4)  # parallel chunking workers


class CodeIndexer:
    def __init__(
        self,
        workspace_root: str,
        embed_model: str = "Snowflake/snowflake-arctic-embed-m-v1.5",
        db_path: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._embed_provider = get_embed_provider(embed_model)
        self.embed_model = embed_model

        db_dir = Path(db_path) if db_path else Path.home() / ".cache" / "vectr" / "db" / self._workspace_hash()
        db_dir.mkdir(parents=True, exist_ok=True)

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
        self._last_indexed: float = 0.0
        self._indexed_files: set[str] = set()

    def _workspace_hash(self) -> str:
        return hashlib.md5(str(self.workspace_root).encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_workspace(self, gitignore_patterns: list[str] | None = None) -> tuple[int, int]:
        """Walk workspace, index all supported files. Returns (files_indexed, chunks_total).

        Three-phase pipeline:
          1. Parallel chunking   — ThreadPoolExecutor, tree-sitter releases GIL
          2. Global batch embed  — 256-chunk batches across all files (vs 64 per-file)
          3. Incremental skip    — files unchanged since last index are skipped via mtime cache
        """
        from integrations.workspace_detect import should_index_file, get_gitignore_patterns

        patterns = gitignore_patterns or get_gitignore_patterns(str(self.workspace_root))

        # Collect candidate files
        all_files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.workspace_root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if should_index_file(str(fpath), patterns):
                    all_files.append(fpath)

        # Incremental: split into files to index vs unchanged files to skip
        mtime_cache = self._load_mtime_cache()
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

        # Phase 2: delete stale chunks for re-indexed files (no-op for brand-new files)
        for fpath_str in new_mtimes:
            if fpath_str in mtime_cache:  # previously indexed → delete old chunks
                try:
                    existing = self._collection.get(where={"file_path": fpath_str})
                    if existing["ids"]:
                        self._collection.delete(ids=existing["ids"])
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

        self._indexed_files.add(file_path)
        return len(chunks)

    def delete_file(self, file_path: str) -> None:
        """Remove all chunks belonging to a file."""
        try:
            existing = self._collection.get(where={"file_path": file_path})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
            self._indexed_files.discard(file_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # mtime cache — tracks file modification times for incremental indexing
    # ------------------------------------------------------------------

    def _mtime_cache_path(self) -> Path:
        db_dir = Path.home() / ".cache" / "vectr" / "db" / self._workspace_hash()
        return db_dir / "index_cache.json"

    def _load_mtime_cache(self) -> dict[str, float]:
        path = self._mtime_cache_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_mtime_cache(self, cache: dict[str, float]) -> None:
        path = self._mtime_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass

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

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. Public method for external callers."""
        return self._embed_provider.embed([text])[0]

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
    ) -> dict:
        where = {"language": language} if language else None
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, max(1, self._collection.count())),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
