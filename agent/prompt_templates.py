"""Loader for vectr's bundled prompt/config templates (UPG-PROMPTS-AS-DATA).

`main.py`'s IDE-integration writers (CLAUDE.md, session-start guidance,
`.mcp.json` variants, the Cursor rules frontmatter, the hook
no-double-recall line) used to embed these as module-level Python string
constants. They are plain text/markdown/JSON content with `.format()`
placeholders, not code, so they live in `agent/templates/` and are loaded
here instead — keeping prompt copy editable without touching Python and
letting non-engineers review/tune it as data.

Resolved as a plain path relative to this module's own `__file__` first —
this is exactly what an installed wheel unpacks to (a real directory under
site-packages; pip has not shipped zip-safe eggs in years — see
`tests/test_prompt_templates.py::TestPackagingIncludesTemplates`, which
builds the real wheel and confirms `agent/templates/*` land there as plain
files) and exactly what the repository checkout already is, so it costs
nothing beyond `pathlib`, already loaded before any vectr code runs. Only
if that direct path is ever absent (e.g. some exotic zipapp/zip-import
packaging this project doesn't currently produce, where `__file__` isn't a
real filesystem path) do we fall back to `importlib.resources`, imported
lazily right here so its considerably heavier transitive cost
(`inspect`/`zipfile`/`tempfile` — see UPG-HOOK-SUBPROCESS-IMPORT-TAX) is
paid only in that fallback, never on every `load_template()` call. Results
are cached since these files are read repeatedly (every `vectr init` /
IDE-config write, every `vectr hook <event>` subprocess) but never change
within a process lifetime.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@cache
def load_template(name: str) -> str:
    """Read and return the exact text content of `agent/templates/<name>`.

    Caching is safe because these are static, packaged files — not
    per-workspace or per-request state.
    """
    direct = _TEMPLATES_DIR / name
    if direct.is_file():
        return direct.read_text(encoding="utf-8")
    import importlib.resources as _ilr
    resource = _ilr.files("agent").joinpath("templates").joinpath(name)
    return resource.read_text(encoding="utf-8")
