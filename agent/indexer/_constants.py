"""Language/extension mappings, exclusion sets, and batch-size constants.

Intentionally NOT in config.yaml (Tier-3): batch/worker counts are perf/
throughput constants, not user-facing tunables.  Changing them via config could
silently corrupt batch inserts (_UPSERT_BATCH_SIZE is bounded by the SQLite
999-variable limit: 6 fields × 100 = 600 ≤ 999).
"""
from __future__ import annotations

import os

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
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".zig": "zig",
    ".md": "markdown",
    ".html": "html",
    # UPG-11.3: prose documentation formats — indexed with the doc-prose quality
    # multiplier (_Q_DOC_PROSE = 0.70) so code chunks still lead on code queries
    # while docs surface for prose/howto queries. Uses window-based chunking since
    # there is no AST grammar for plain text or reStructuredText.
    ".txt": "txt",
    ".rst": "rst",
}

EXCLUDED_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "dist", "build", ".build", ".next", ".nuxt", "target", "out",
    "coverage", ".coverage", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

# Intentionally NOT in config.yaml (Tier-3): perf/throughput constants.
# _UPSERT_BATCH_SIZE=100 is bounded by SQLite's 999-variable limit (a code
# invariant: 6 fields × 100 = 600 ≤ 999); changing it via config could
# silently corrupt batch inserts.  _FILE_BATCH_SIZE/_EMBED_BATCH_SIZE are
# pure throughput levers with no behavioural effect on ranking or output.
_FILE_BATCH_SIZE = 64     # used by index_file() — single-file watcher path
_EMBED_BATCH_SIZE = 256   # texts per model.encode() call — larger = better BLAS utilisation
_UPSERT_BATCH_SIZE = 100  # rows per ChromaDB upsert — SQLite variable limit is 999; 6 fields×100=600
_CHUNK_WORKERS = min(8, os.cpu_count() or 4)  # parallel chunking workers
