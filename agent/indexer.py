"""AST-aware code chunking and embedding pipeline."""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

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
    """Uses sentence-transformers with BAAI/bge-base-en-v1.5 (no API key)."""

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5") -> None:
        from sentence_transformers import SentenceTransformer
        cache_dir = Path.home() / ".cache" / "vectr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = SentenceTransformer(
            model_name,
            cache_folder=str(cache_dir),
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


def _collect_chunks_ast(
    node,
    code_bytes: bytes,
    lines: list[str],
    language: str,
    file_path: str,
    target_types: set[str],
    results: list[CodeChunk],
) -> None:
    if node.type in target_types:
        start = node.start_point[0]  # 0-indexed
        end = node.end_point[0]
        content = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        symbol = _extract_symbol_name(node, language, code_bytes)
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
        return  # don't recurse into nested definitions — they'll be redundant

    for child in node.children:
        _collect_chunks_ast(child, code_bytes, lines, language, file_path, target_types, results)


def _fallback_window_chunks(lines: list[str], file_path: str, language: str) -> list[CodeChunk]:
    """200-line windows with 50-line overlap for unsupported languages."""
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

_BATCH_SIZE = 64  # embed and upsert in batches to avoid memory spikes


class CodeIndexer:
    def __init__(
        self,
        workspace_root: str,
        embed_model: str = "BAAI/bge-base-en-v1.5",
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
            metadata={"hnsw:space": "cosine"},
        )
        self._last_indexed: float = 0.0
        self._indexed_files: set[str] = set()

    def _workspace_hash(self) -> str:
        return hashlib.md5(str(self.workspace_root).encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_workspace(self, gitignore_patterns: list[str] | None = None) -> tuple[int, int]:
        """Walk workspace, index all supported files. Returns (files_indexed, chunks_total)."""
        from integrations.workspace_detect import should_index_file, get_gitignore_patterns

        patterns = gitignore_patterns or get_gitignore_patterns(str(self.workspace_root))
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.workspace_root):
            # prune excluded dirs in-place
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if should_index_file(str(fpath), patterns):
                    files.append(fpath)

        for f in files:
            self.index_file(str(f))

        self._last_indexed = time.time()
        return len(self._indexed_files), self._collection.count()

    def index_file(self, file_path: str) -> int:
        """Chunk and embed a single file. Returns number of chunks indexed."""
        chunks = chunk_file(file_path)
        if not chunks:
            return 0

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
        for i in range(0, len(documents), _BATCH_SIZE):
            batch = documents[i: i + _BATCH_SIZE]
            all_embeddings.extend(self._embed_provider.embed(batch))
        assert len(all_embeddings) == len(ids), (
            f"Embed provider returned {len(all_embeddings)} embeddings for {len(ids)} chunks"
        )

        for i in range(0, len(ids), _BATCH_SIZE):
            self._collection.upsert(
                ids=ids[i: i + _BATCH_SIZE],
                documents=documents[i: i + _BATCH_SIZE],
                metadatas=metadatas[i: i + _BATCH_SIZE],
                embeddings=all_embeddings[i: i + _BATCH_SIZE],
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
        """Return (ids, documents, metadatas) for all stored chunks — used by BM25 index."""
        result = self._collection.get(include=["documents", "metadatas"])
        return result["ids"], result["documents"], result["metadatas"]

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
