"""Cache-root hygiene: prune empty per-workspace hash dirs (UPG-CACHE-LITTER).

The cache root ~/.cache/vectr accumulates one <md5(workspace)[:12]> directory
per workspace vectr has ever resolved a DB path for. A real service writes its
notes DB into that dir at construction, so a live/used workspace's dir is never
empty; but path resolutions that never build a service (probes, mapping scripts)
used to leave an empty dir behind. This module removes those empty dirs safely.

Safety (hard rail R1): never remove a dir that belongs to a live instance, and
never touch the reserved `db/` (legacy DB layout) or `models/` (embedding-model
cache) subdirs. An empty dir cannot belong to a live instance, but the protected
set is honored regardless as belt-and-suspenders.
"""
from __future__ import annotations

import re
from pathlib import Path

# A per-workspace cache dir is named md5(path)[:12] — exactly 12 hex chars. This
# pattern is what distinguishes a workspace dir from the reserved `db`/`models`
# subdirs, so only genuine workspace dirs are ever considered for removal.
_HASH_DIR_RE = re.compile(r"^[0-9a-f]{12}$")


def _is_empty_dir(path: Path) -> bool:
    try:
        next(path.iterdir())
        return False
    except StopIteration:
        return True
    except OSError:
        return False  # unreadable → treat as non-empty (never remove)


def find_empty_cache_dirs(
    cache_root: Path, protected_slugs: frozenset[str] = frozenset()
) -> list[Path]:
    """Return the sorted list of empty per-workspace hash dirs under cache_root.

    Covers both the top-level ~/.cache/vectr/<hash> layout and the legacy
    ~/.cache/vectr/db/<hash> layout. Skips reserved subdirs, non-hash-named
    dirs, non-empty dirs, and any dir in `protected_slugs`.
    """
    if not cache_root.is_dir():
        return []
    candidates: list[Path] = []
    # Top-level <hash> dirs.
    for child in cache_root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not _HASH_DIR_RE.match(name):
            continue  # excludes `db`, `models`, and any non-hash entry
        if name in protected_slugs:
            continue
        if _is_empty_dir(child):
            candidates.append(child)
    # Legacy db/<hash> layout.
    db_root = cache_root / "db"
    if db_root.is_dir():
        for child in db_root.iterdir():
            if not child.is_dir():
                continue
            if not _HASH_DIR_RE.match(child.name):
                continue
            if child.name in protected_slugs:
                continue
            if _is_empty_dir(child):
                candidates.append(child)
    return sorted(candidates)


def prune_empty_cache_dirs(
    cache_root: Path,
    protected_slugs: frozenset[str] = frozenset(),
    dry_run: bool = False,
) -> list[Path]:
    """Remove (or, with dry_run, just list) empty per-workspace cache dirs.

    Returns the dirs removed (or that would be removed). A dir that becomes
    non-empty between the scan and the rmdir is left in place — rmdir only
    succeeds on an empty dir, so the removal can never clobber real data.
    """
    empties = find_empty_cache_dirs(cache_root, protected_slugs)
    if dry_run:
        return empties
    removed: list[Path] = []
    for d in empties:
        try:
            d.rmdir()  # fails if not empty — safe by construction
            removed.append(d)
        except OSError:
            continue
    return removed


def live_instance_slugs() -> frozenset[str]:
    """Cache-dir slugs (md5(workspace)[:12]) of every live registered instance.

    Recomputed from each live instance's workspace PATH rather than trusting the
    registry key, since the registry hashes with sha256 while cache dirs use md5
    (agent.instance_registry.workspace_hash vs app.service._cache_dir_slug)."""
    try:
        from agent.instance_registry import InstanceRegistry, _is_pid_alive
        from app.service import _cache_dir_slug
    except Exception:
        return frozenset()
    slugs: set[str] = set()
    try:
        for entry in InstanceRegistry().list_all().values():
            if _is_pid_alive(entry.get("pid", -1)):
                ws = entry.get("workspace")
                if ws:
                    slugs.add(_cache_dir_slug(ws))
    except Exception:
        return frozenset()
    return frozenset(slugs)
