"""Workspace root detection, .gitignore / .vectrignore pattern parsing."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from agent.chunk_quality import is_generated_file, is_vectr_config_file
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


def _read_ignore_lines(path: Path) -> list[str]:
    """Read non-blank, non-comment lines from an ignore file."""
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def get_gitignore_patterns(workspace_root: str) -> list[str]:
    """Read .gitignore and return a list of glob patterns."""
    return _read_ignore_lines(Path(workspace_root) / ".gitignore")


def get_vectrignore_dirs(workspace_root: str) -> set[str]:
    """Read .vectrignore and return a set of directory names to exclude.

    .vectrignore format: one directory name per line, # comments supported.
    Example:
        # exclude vendor and generated code
        vendor
        generated
        proto-gen
    """
    return set(_read_ignore_lines(Path(workspace_root) / ".vectrignore"))


def write_vectrignore(workspace_root: str, dirs: list[str]) -> None:
    """Append directory names to .vectrignore, skipping duplicates."""
    vectrignore = Path(workspace_root) / ".vectrignore"
    existing: set[str] = set()
    lines: list[str] = []

    if vectrignore.exists():
        raw = vectrignore.read_text(encoding="utf-8", errors="ignore")
        lines = raw.splitlines()
        existing = {l.strip() for l in lines if l.strip() and not l.strip().startswith("#")}

    new_dirs = [d for d in dirs if d not in existing]
    if not new_dirs:
        return

    if lines and lines[-1].strip():
        lines.append("")  # blank separator before new entries
    lines.extend(new_dirs)
    vectrignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def should_index_file(
    file_path: str,
    gitignore_patterns: list[str],
    extra_excluded_dirs: set[str] | None = None,
) -> bool:
    """Return True if the file should be indexed."""
    path = Path(file_path)

    if path.suffix.lower() not in _SUPPORTED_EXTS:
        return False

    # UPG-1.3: never index vectr's own injected IDE-config files, nor
    # machine-generated/vendored files (lookup tables, protobuf, minified).
    if is_vectr_config_file(file_path) or is_generated_file(file_path):
        return False

    excluded = _ALWAYS_SKIP | (extra_excluded_dirs or set())

    for part in path.parts:
        if part in excluded:
            return False

    # check gitignore patterns
    name = path.name
    rel = str(path)
    for pattern in gitignore_patterns:
        if fnmatch.fnmatch(name, pattern):
            return False
        if fnmatch.fnmatch(rel, pattern):
            return False
        if pattern.endswith("/"):
            if pattern.rstrip("/") in path.parts:
                return False

    return True
