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
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_MANIFEST_PATH = Path(__file__).resolve().parent / "proxy_anchors.yaml"

# Per-glob expansion cap — guards against a pathological pattern (a
# recursive glob over a huge tree) returning an unbounded match set before
# it is ever sorted or sliced down to the caller's requested limit.
_PER_GLOB_EXPANSION_CAP = 50


@cache
def load_manifest() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """((domain_id, (glob, ...)), ...), loaded once from
    agent/proxy_anchors.yaml. Cached — the table is static packaged data,
    never per-request state (same shape as agent/outcome.py's
    `_load_markers`)."""
    direct = _MANIFEST_PATH
    if direct.is_file():
        raw = direct.read_text(encoding="utf-8")
    else:
        import importlib.resources as _ilr
        raw = _ilr.files("agent").joinpath("proxy_anchors.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return tuple(
        (str(domain["id"]), tuple(str(g) for g in domain.get("globs", [])))
        for domain in data.get("domains", [])
    )


PROXY_ANCHOR_MANIFEST_VERSION: int = 1


def suggest_proxy_anchors(workspace_root: str | Path, limit: int) -> list[str]:
    """Workspace-relative POSIX paths of proxy anchors PRESENT in
    `workspace_root`, deterministic glob presence only — no content
    inspection whatsoever.

    Walks the manifest in domain order, then declared glob order within a
    domain; within one glob's own expansion, matches are sorted
    lexicographically so the result is stable across filesystems (directory
    iteration order is not guaranteed by the OS). Duplicates (the same file
    matched by more than one glob) are dropped, keeping the first — i.e.
    manifest-order — occurrence. Entries under a `.git` directory are always
    excluded, even when a glob is recursive enough to reach one.

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

    for _domain_id, globs in load_manifest():
        for glob in globs:
            try:
                raw_matches = list(_bounded_glob(root, glob))
            except OSError:
                continue

            candidates: list[Path] = []
            for match in raw_matches:
                if ".git" in match.relative_to(root).parts:
                    continue
                try:
                    if not match.is_file():
                        continue
                except OSError:
                    continue
                candidates.append(match)

            for match in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
                rel = match.relative_to(root).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                suggestions.append(rel)
                if len(suggestions) >= limit:
                    return suggestions

    return suggestions


def _bounded_glob(root: Path, pattern: str):
    """`root.glob(pattern)`, capped at `_PER_GLOB_EXPANSION_CAP` raw matches.
    `Path.glob` is a lazy generator, so this bounds one pathological glob's
    cost (and the memory of materializing its matches) without walking the
    rest of a huge tree once the cap is hit."""
    count = 0
    for match in root.glob(pattern):
        if count >= _PER_GLOB_EXPANSION_CAP:
            return
        count += 1
        yield match
