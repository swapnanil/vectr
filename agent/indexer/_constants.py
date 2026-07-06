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

# ---------------------------------------------------------------------------
# L3 content-index schema version — mtime-cache rebuild trigger
# ---------------------------------------------------------------------------

# Intentionally NOT in config.yaml (Tier-3): INDEXING_SCHEMA_VERSION is a
# schema-migration trigger, same category as symbol_graph.SYMBOL_SCHEMA_VERSION.
# Changing it via config would silently corrupt or force a rebuild without the
# usual version-bump safeguard.
#
# Stored as a sentinel entry in the per-workspace mtime cache (index_cache.json,
# see CodeIndexer._load_mtime_cache/_save_mtime_cache). On load, a version
# mismatch is treated as a cold cache — every file re-enters `to_index` on the
# next `index_workspace()` and is fully re-chunked/re-embedded, the same
# recovery path `force=True` already uses (UPG-8.6). This is the existing
# index-rebuild mechanism; bumping the version is how a pipeline change (new
# chunk content, new derived vector) reaches an already-indexed workspace
# without a manual cache wipe.
#
# Bump whenever chunking or embedding changes in a way that makes
# already-embedded chunks stale or incomplete relative to what a fresh index
# would produce.
INDEXING_SCHEMA_VERSION = 4  # 1: pre-ARCH-4 baseline (unversioned cache, implicit) · 2: per-symbol purpose vector added (ARCH-4) · 3: purpose-text docstring distillation changed to first-paragraph-only + capped non-Python leading-doc block (ARCH-4-DEBUG) — old purpose vectors are stale relative to a fresh index and must rebuild · 4: symbol-bearing definition chunks (class/struct/enum/interface/type-alias/function/method) exempted from UPG-1.1 trivial-drop (UPG-TRIVIAL-DROP-ALIAS-DEFS) — previously-dropped one-line alias defs now emit a chunk, changing the chunk set for affected files

# Sentinel key inside the mtime-cache JSON that carries INDEXING_SCHEMA_VERSION.
# Chosen to never collide with a real filesystem path (mtime-cache keys are
# always absolute file paths).
_MTIME_CACHE_SCHEMA_KEY = "__vectr_index_schema_version__"

# ---------------------------------------------------------------------------
# Embedding-model version stamp — vector-space safety (UPG-EMBEDDER-SWAP-GRANITE)
# ---------------------------------------------------------------------------

# A separate small JSON file (co-located with the mtime cache under the same
# per-workspace `~/.cache/vectr/db/<hash>` directory) recording the
# `CodeIndexer.embed_model` identifier that built the CURRENT contents of the
# ChromaDB collection. Kept out of the mtime-cache JSON deliberately: that
# file's mismatch handling only resets the incremental-skip state (an
# optimization), whereas an embed-model mismatch is a correctness issue —
# vectors from two different models must never coexist in one collection,
# so it must unconditionally force CodeIndexer.index_workspace()'s
# `force=True` full-rebuild path (unconditional per-file delete-then-
# reinsert), not just the softer "treat cache as cold" behaviour. A missing
# stamp file (a pre-existing index built by a vectr version that predates
# this mechanism) is treated as a mismatch too, since we cannot know what
# model produced those vectors.
_EMBED_MODEL_STAMP_FILE = "embed_model_stamp.json"
