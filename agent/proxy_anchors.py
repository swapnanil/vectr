"""Proxy-anchor suggestion — deterministic glob presence, zero content read.

A "proxy anchor" is a workspace file that ENCODES a process domain (how
dependencies are pinned, how CI runs, how the app is built/deployed/
containerized) rather than being the domain itself. `suggest_proxy_anchors`
answers one narrow question — "which of the files that typically encode
these processes actually exist in this workspace" — using only glob
presence against the versioned table in `agent/proxy_anchors.yaml`. It never
opens, hashes, or reads the content of any candidate file; existence alone
is the signal. There is no query, no keyword match, no judgment: given a
workspace root, the same manifest always produces the same suggestions.

This runs on the `remember()` write path, so its cost matters on every
memory write, not just on a search. Manifest globs come in two shapes:
root-scoped (e.g. `pyproject.toml`, `.github/workflows/*.yml`) that
`Path.glob` resolves without walking below the matched level, and
recursive (`**/Dockerfile`, `**/Makefile`) that need a tree walk. A
recursive glob is evaluated once against a single, bounded walk of the
tree (see `_collect_recursive_candidates`) rather than re-walking the tree
per glob — the manifest currently declares 14 recursive globs across two
domains, so evaluating each with its own unbounded `Path.glob("**/...")`
walk means 14 full tree traversals for one `remember()` call.
"""
from __future__ import annotations

import fnmatch
import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

_MANIFEST_PATH = Path(__file__).resolve().parent / "proxy_anchors.yaml"

# Per-glob expansion cap — guards against a pathological pattern (a
# recursive glob over a huge tree) returning an unbounded match set before
# it is ever sorted or sliced down to the caller's requested limit.
_PER_GLOB_EXPANSION_CAP = 50

# Recursive-glob walk bounds. A manifest glob containing "**" needs an
# actual directory walk (there is no "resolve without descending" option
# the way there is for a root-scoped glob), so the walk itself is bounded
# on three independent axes — depth, total directories visited, and which
# subtrees are entered at all — so that a populated dependency tree
# (node_modules/, a real .venv/, a native target/) cannot turn a memory
# write into a multi-hundred-millisecond filesystem scan.
#
# Root is depth 0. Files this workspace's own build/deploy process encodes
# live at the root or a couple of levels down (`docker/Dockerfile`,
# `deploy/prod/compose.yml`); nothing 8 levels deep is this workspace's
# build ritual — it is either generated output or a dependency's own tree.
_MAX_RECURSIVE_DEPTH = 3

# Shared os.scandir() call budget for one suggest_proxy_anchors() call's
# entire recursive walk (not per-glob) — bounds a pathologically wide/flat
# tree the same way _MAX_RECURSIVE_DEPTH bounds a pathologically deep one.
_MAX_DIRS_SCANNED = 2000

# Directories a recursive glob never descends into: each of these encodes a
# DEPENDENCY's or a generated artifact's build process, not this
# workspace's, so a Dockerfile/Makefile found underneath one is not a valid
# proxy anchor for this workspace regardless of how fast it could be found.
_PRUNED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "bower_components", ".venv", "venv", "env",
    "virtualenv", "site-packages", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "target",
    "build", "dist", "out", ".next", ".nuxt", ".gradle", ".m2", "Pods",
    "Carthage", ".terraform", "vendor", "third_party", ".eggs",
    "coverage", "htmlcov", ".idea", ".vscode", ".claude",
})


def _is_pruned_dir(name: str) -> bool:
    """True for any directory a recursive glob must never descend into —
    the fixed `_PRUNED_DIR_NAMES` set, plus any `*.egg-info` directory
    (Python packaging generates one per install, with a project-specific
    name `_PRUNED_DIR_NAMES` cannot enumerate in advance)."""
    return name in _PRUNED_DIR_NAMES or name.endswith(".egg-info")


@cache
def _load_manifest_data() -> dict[str, Any]:
    """Raw parsed `proxy_anchors.yaml`, cached — packaged static data, read
    at most once per process. `load_manifest()` and
    `PROXY_ANCHOR_MANIFEST_VERSION` both derive from this single parse so
    the two can never see different copies of the file."""
    direct = _MANIFEST_PATH
    if direct.is_file():
        raw = direct.read_text(encoding="utf-8")
    else:
        import importlib.resources as _ilr
        raw = _ilr.files("agent").joinpath("proxy_anchors.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(raw) or {}


@cache
def load_manifest() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """((domain_id, (glob, ...)), ...), loaded once from
    agent/proxy_anchors.yaml. Cached — the table is static packaged data,
    never per-request state (same shape as agent/outcome.py's
    `_load_markers`)."""
    data = _load_manifest_data()
    return tuple(
        (str(domain["id"]), tuple(str(g) for g in domain.get("globs", [])))
        for domain in data.get("domains", [])
    )


# Derived from proxy_anchors.yaml's own `version:` field (defaulting to 1
# if the field is absent) so the two can never silently drift apart — this
# was previously a hardcoded `1` independent of the manifest file.
PROXY_ANCHOR_MANIFEST_VERSION: int = int(_load_manifest_data().get("version", 1))


def suggest_proxy_anchors(workspace_root: str | Path, limit: int) -> list[str]:
    """Workspace-relative POSIX paths of proxy anchors PRESENT in
    `workspace_root`, deterministic glob presence only — no content
    inspection whatsoever.

    Walks the manifest in domain order, then declared glob order within a
    domain; within one glob's own match set, matches are sorted
    lexicographically so the result is stable across filesystems (directory
    iteration order is not guaranteed by the OS). Duplicates (the same file
    matched by more than one glob) are dropped, keeping the first — i.e.
    manifest-order — occurrence. Entries under a `.git` directory are always
    excluded, even when a glob is recursive enough to reach one.

    A recursive ("**") glob is matched against one shared, bounded walk of
    the tree (`_collect_recursive_candidates`), built lazily the first time
    a recursive glob is actually reached — a workspace whose root-scoped
    domains already satisfy `limit` never pays for the walk at all.

    Returns [] — never raises — for `limit <= 0`, an unreadable/missing
    `workspace_root`, or a workspace with no matches at all.
    """
    if limit <= 0:
        return []

    try:
        root = Path(workspace_root).resolve()
        if not root.is_dir():
            return []
    except OSError:
        return []

    seen: set[str] = set()
    suggestions: list[str] = []
    recursive_candidates: list[str] | None = None

    for _domain_id, globs in load_manifest():
        for glob in globs:
            try:
                if "**" in glob:
                    if recursive_candidates is None:
                        recursive_candidates = _collect_recursive_candidates(root)
                    rels = _match_recursive(recursive_candidates, glob)
                else:
                    rels = _match_root_scoped(root, glob)
            except OSError:
                continue

            for rel in sorted(rels):
                if rel in seen:
                    continue
                seen.add(rel)
                suggestions.append(rel)
                if len(suggestions) >= limit:
                    return suggestions

    return suggestions


def _match_root_scoped(root: Path, pattern: str) -> list[str]:
    """Root-scoped (non-`**`) glob match: `Path.glob` resolves this
    without walking below the matched level, so no bounding is needed
    beyond the existing per-glob expansion cap. Returns workspace-relative
    POSIX paths for regular-file matches only, excluding anything under a
    `.git` directory."""
    rels: list[str] = []
    for match in _bounded_glob(root, pattern):
        if ".git" in match.relative_to(root).parts:
            continue
        try:
            if not match.is_file():
                continue
        except OSError:
            continue
        rels.append(match.relative_to(root).as_posix())
    return rels


def _match_recursive(candidates: list[str], pattern: str) -> list[str]:
    """Match a `**`-containing manifest glob against the precomputed
    candidate list from `_collect_recursive_candidates`, capped at
    `_PER_GLOB_EXPANSION_CAP` matches — the same per-pattern match-count
    guard `_bounded_glob` applies to a root-scoped glob.

    `proxy_anchors.yaml`'s header comment documents every recursive glob as
    exactly a `**/` prefix followed by a single filename pattern. Plain
    `fnmatch.fnmatchcase` alone does not reproduce `Path.glob`'s own `**/`
    semantics — "zero or more leading directory segments" — because it
    requires a literal `/` before the filename part, so a root-level file
    would never match `**/Dockerfile` even though `Path.glob` treats it as
    a match. Matching the pattern with its `**/` prefix stripped as a
    second, equally-valid form restores that zero-directory case without
    reintroducing a query-content-conditional branch (this is a fixed
    structural property of the manifest's own glob syntax, applied
    identically to every recursive pattern, not a per-query decision)."""
    stripped = pattern[3:] if pattern.startswith("**/") else None
    matches: list[str] = []
    for rel in candidates:
        if fnmatch.fnmatchcase(rel, pattern) or (
            stripped is not None and fnmatch.fnmatchcase(rel, stripped)
        ):
            matches.append(rel)
            if len(matches) >= _PER_GLOB_EXPANSION_CAP:
                break
    return matches


def _collect_recursive_candidates(root: Path) -> list[str]:
    """Single bounded walk of `root`, run at most once per
    `suggest_proxy_anchors()` call regardless of how many recursive globs
    the manifest declares — previously each recursive glob re-walked the
    whole tree on its own (14 manifest globs meant 14 full traversals).

    Returns workspace-relative POSIX paths of regular files only (no
    directories, nothing under a pruned subtree), bounded by:
      - `_MAX_RECURSIVE_DEPTH`: root is depth 0; a directory at or beyond
        this depth is never descended into further (files it directly
        contains are still collected).
      - `_MAX_DIRS_SCANNED`: a shared `os.scandir()` call budget for the
        whole walk, so a pathologically wide/flat tree cannot make a
        single memory write scan an unbounded number of directories.
      - `_PRUNED_DIR_NAMES` / `_is_pruned_dir`: dependency, build-output,
        and VCS directories are never entered at all.

    Directory entries are sorted by name before recursion, so two calls on
    the same tree walk it in the same order and return identical lists —
    `os.scandir`'s own iteration order is filesystem-dependent, not
    guaranteed stable. Any `OSError` (e.g. a permission-denied
    subdirectory) is swallowed for that one directory only; the walk
    continues with its remaining siblings rather than aborting.
    """
    candidates: list[str] = []
    dirs_budget = [_MAX_DIRS_SCANNED]
    stack: list[tuple[Path, str, int]] = [(root, "", 0)]

    while stack:
        current, rel_prefix, depth = stack.pop()
        if dirs_budget[0] <= 0:
            break
        dirs_budget[0] -= 1

        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue

        child_dirs: list[tuple[Path, str, int]] = []
        for entry in entries:
            rel = rel_prefix + entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < _MAX_RECURSIVE_DEPTH and not _is_pruned_dir(entry.name):
                        child_dirs.append((Path(entry.path), rel + "/", depth + 1))
                    continue
                if entry.is_file(follow_symlinks=True):
                    candidates.append(rel)
            except OSError:
                continue

        # Reverse so pop() (LIFO) visits children in the sorted order they
        # were discovered — required for the walk (and therefore the
        # per-pattern match order) to be deterministic.
        stack.extend(reversed(child_dirs))

    return candidates


def _bounded_glob(root: Path, pattern: str):
    """`root.glob(pattern)`, capped at `_PER_GLOB_EXPANSION_CAP` raw
    matches. `Path.glob` is a lazy generator, so this bounds one
    root-scoped (non-`**`) pattern's cost without materializing more
    matches than will ever be used."""
    count = 0
    for match in root.glob(pattern):
        if count >= _PER_GLOB_EXPANSION_CAP:
            return
        count += 1
        yield match
