"""Loader for vectr's bundled prompt/config templates (UPG-PROMPTS-AS-DATA).

`main.py`'s IDE-integration writers (CLAUDE.md, session-start guidance,
`.mcp.json` variants, the Cursor rules frontmatter, the hook
no-double-recall line) used to embed these as module-level Python string
constants. They are plain text/markdown/JSON content with `.format()`
placeholders, not code, so they live in `agent/templates/` and are loaded
here instead — keeping prompt copy editable without touching Python and
letting non-engineers review/tune it as data.

Resolved via ``importlib.resources`` (not a ``cwd``-relative ``open()``) so
this works identically whether vectr runs from the repository checkout or
as an installed wheel (global binary), and results are cached since these
files are read repeatedly (every `vectr init` / IDE-config write) but never
change within a process lifetime.
"""
from __future__ import annotations

import importlib.resources as _ilr
from functools import cache


@cache
def load_template(name: str) -> str:
    """Read and return the exact text content of `agent/templates/<name>`.

    Caching is safe because these are static, packaged files — not
    per-workspace or per-request state.
    """
    resource = _ilr.files("agent").joinpath("templates").joinpath(name)
    return resource.read_text(encoding="utf-8")
