"""Workspace-relative path rendering for MCP text output (UPG-RELATIVE-PATH-RENDER).

Every rendered search result, symbol-graph line, evict-hint entry, and re-fetch
key used to carry the full ABSOLUTE workspace root — 100% redundant within a
response (the root is fixed) and a measurable share of the payload (9% of a
default search, 26% of low-confidence pointer mode, 42% of evict_hint). The
renderers now print the absolute root once in a header and render every path
relative to it; chunk ids and vectr_fetch keys become relative too, and
``vectr_fetch`` resolves a relative id back against the daemon's own root
(``resolve_chunk_id``), while still accepting the absolute ids existing sessions
already hold.
"""
from __future__ import annotations

import os


def workspace_relpath(path: str, root: str) -> str:
    """Render an indexed absolute *path* as workspace-relative for display.

    Falls back to the original path when *root* is empty, *path* is empty, or
    *path* lies outside *root* (rendering a ``../../..`` escape would be noisier
    and less honest than the absolute path it came from). Deterministic; no I/O.
    """
    if not root or not path:
        return path
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        # e.g. different drives on Windows — no relative form exists.
        return path
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return path  # outside the workspace — keep the honest absolute path
    return rel


def resolve_chunk_id(chunk_id: str, root: str) -> str:
    """Resolve a possibly-relative chunk id to the absolute id the index stores.

    A chunk id is ``<path>:<start>-<end>``. The new canonical rendering emits a
    workspace-RELATIVE ``<path>``; existing sessions hold ABSOLUTE ids. This
    accepts BOTH: an already-absolute path (or an id with no root to resolve
    against) is returned unchanged; a relative path is joined onto *root* so the
    ``file_path:start-end`` key matches what ``fetch_chunks`` looks up. The
    line-range suffix is split on the LAST colon so a path containing a colon is
    not mangled.
    """
    if not root or not chunk_id:
        return chunk_id
    path, sep, line_range = chunk_id.rpartition(":")
    if not sep:
        # No line-range suffix — treat the whole thing as a path.
        path, line_range = chunk_id, ""
    if os.path.isabs(path):
        return chunk_id
    abs_path = os.path.normpath(os.path.join(root, path))
    return f"{abs_path}:{line_range}" if sep else abs_path
