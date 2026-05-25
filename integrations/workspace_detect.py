"""Workspace root detection and .gitignore pattern parsing."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from agent.indexer import LANG_BY_EXT

_SUPPORTED_EXTS = set(LANG_BY_EXT.keys())

_ALWAYS_SKIP = {
    ".git", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", "coverage",
}


def find_workspace_root(start_path: str) -> str:
    """Walk up from start_path looking for a .git directory. Falls back to start_path."""
    p = Path(start_path).resolve()
    if p.is_file():
        p = p.parent
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return str(p)


def get_gitignore_patterns(workspace_root: str) -> list[str]:
    """Read .gitignore and return a list of glob patterns."""
    gi = Path(workspace_root) / ".gitignore"
    if not gi.exists():
        return []
    patterns: list[str] = []
    for line in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def should_index_file(file_path: str, gitignore_patterns: list[str]) -> bool:
    """Return True if the file should be indexed."""
    path = Path(file_path)

    if path.suffix.lower() not in _SUPPORTED_EXTS:
        return False

    # skip directories that are always excluded
    for part in path.parts:
        if part in _ALWAYS_SKIP:
            return False

    # check gitignore patterns
    name = path.name
    rel = str(path)
    for pattern in gitignore_patterns:
        if fnmatch.fnmatch(name, pattern):
            return False
        if fnmatch.fnmatch(rel, pattern):
            return False
        # directory pattern: "dist/" matches any path with dist as a component
        if pattern.endswith("/"):
            if pattern.rstrip("/") in path.parts:
                return False

    return True
