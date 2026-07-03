"""Workspace root detection, .gitignore / .vectrignore pattern parsing."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from agent.chunk_quality import is_generated_file, is_vectr_config_file, is_build_artifact_file
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


def _is_glob_pattern(entry: str) -> bool:
    """True if a .vectrignore entry is a file glob rather than a bare dir name.

    Bare directory names (e.g. "vendor") are handled by get_vectrignore_dirs.
    Entries containing glob metacharacters (e.g. "*.generated.py") are handled
    by get_vectrignore_file_globs (UPG-13.3) — additive, so existing dir-name
    behaviour is unchanged for every entry that isn't a glob.
    """
    return any(ch in entry for ch in "*?[")


def get_vectrignore_dirs(workspace_root: str) -> set[str]:
    """Read .vectrignore and return a set of directory names to exclude.

    .vectrignore format: one directory name (or file glob, see
    get_vectrignore_file_globs) per line, # comments supported.
    Example:
        # exclude vendor and generated code
        vendor
        generated
        proto-gen
    """
    return {
        line for line in _read_ignore_lines(Path(workspace_root) / ".vectrignore")
        if not _is_glob_pattern(line)
    }


def get_vectrignore_file_globs(workspace_root: str) -> list[str]:
    """Read .vectrignore and return file glob patterns (UPG-13.3).

    An entry is treated as a glob when it contains a glob metacharacter
    (*, ?, or [) — e.g. "*.generated.py" — rather than a bare directory name.
    Additive layer on top of get_vectrignore_dirs: dir-name entries keep their
    existing directory-exclusion semantics unchanged; globs are matched against
    individual file names by callers (agent.watcher.CodeWatcher, should_index_file).
    """
    return [
        line for line in _read_ignore_lines(Path(workspace_root) / ".vectrignore")
        if _is_glob_pattern(line)
    ]


def write_default_vectrignore(workspace_root: str) -> bool:
    """Seed a fresh .vectrignore with the standard non-indexable dirs (UPG-13.2).

    Only writes when no .vectrignore exists yet for this workspace_root — an
    existing file (even an empty one, or one with unrelated content) is never
    touched, so a user's hand-authored exclusions are never clobbered.
    Returns True if a file was written, False if one already existed.
    """
    from agent.config import WORKSPACE_DEFAULT_VECTRIGNORE_DIRS

    vectrignore = Path(workspace_root) / ".vectrignore"
    if vectrignore.exists():
        return False

    lines = [
        "# vectr default excludes — generated on first `vectr start`/`vectr init`.",
        "# vectr never overwrites this file once it exists; edit freely.",
        "# One directory name or file glob (e.g. *.generated.py) per line.",
        "",
        *WORKSPACE_DEFAULT_VECTRIGNORE_DIRS,
        "",
    ]
    vectrignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


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
    workspace_root: str | None = None,
) -> bool:
    """Return True if the file should be indexed.

    Excluded directory NAMES are matched only against path components below
    workspace_root (when given) — a workspace that itself lives under an
    excluded-sounding prefix (e.g. /tmp/myproject or repo/tmp/fixture) must
    not have every one of its files excluded by its own absolute path.
    """
    path = Path(file_path)

    if path.suffix.lower() not in _SUPPORTED_EXTS:
        return False

    # UPG-1.3: never index vectr's own injected IDE-config files, nor
    # machine-generated/vendored files (lookup tables, protobuf, minified).
    # UPG-15.9: never index files inside build-artifact directories (*.egg-info,
    # *.dist-info) — they contain only file-path manifests and packaging metadata
    # with no educational content (SOURCES.txt, PKG-INFO flood BM25 on identifiers).
    if is_vectr_config_file(file_path) or is_generated_file(file_path) or is_build_artifact_file(file_path):
        return False

    excluded = _ALWAYS_SKIP | (extra_excluded_dirs or set())

    parts = path.parts
    if workspace_root:
        try:
            parts = path.resolve().relative_to(Path(workspace_root).resolve()).parts
        except ValueError:
            pass  # file outside the root — fall back to full-path parts

    for part in parts:
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
