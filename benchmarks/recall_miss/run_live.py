#!/usr/bin/env python3
"""OPT-IN script mode: run the recall-miss harness against a read-only
SNAPSHOT of a real working-memory database (UPG-RECALL-MISS-FLOOR part (a)).

Never touches the source database directly — every operation here reads
from `--source-cache-dir` and writes only under `--snapshot-dir`, which
this script REQUIRES to resolve under `<repo>/tmp/` (the fixture-isolation
rule: only ever point live-daemon-adjacent copies there, so the always-on
daemon's own `.vectrignore`/`.gitignore` skip it and macOS won't evict it).
The source database is opened with sqlite3's online backup API (safe under
a concurrent writer, WAL-consistent) — this script never opens the live
path for writing, and `store.recall()` on a snapshot only ever mutates the
SNAPSHOT's `last_accessed` column, never the source's.

The hand-labeled query set (`--labels`) is intentionally NOT part of this
repo: a label file for a real personal/team working-memory database
necessarily contains that database's own note content and topics, which is
exactly the private data this tool must never ship. Point `--labels` at a
local, gitignored JSON file you maintain yourself. Schema:

    [
      {"query": "...", "relevant_note_ids": [123, 456],
       "basis": "why these notes answer this query", "source": "live-session"},
      ...
    ]

Usage:
    python3 benchmarks/recall_miss/run_live.py \\
        --source-cache-dir ~/.cache/vectr/<hash> \\
        --snapshot-dir tmp/recall-miss-live \\
        --workspace /path/to/workspace \\
        --labels tmp/recall-miss-live/labels.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import LabeledQuery, NoteInfo, evaluate_recall  # noqa: E402


def _require_under_tmp(snapshot_dir: Path) -> Path:
    resolved = snapshot_dir.resolve()
    tmp_root = (REPO_ROOT / "tmp").resolve()
    if tmp_root not in resolved.parents and resolved != tmp_root:
        raise SystemExit(
            f"--snapshot-dir must resolve under {tmp_root} (fixture-isolation rule), "
            f"got {resolved}"
        )
    return resolved


def _sqlite_backup(src_path: Path, dst_path: Path) -> None:
    """Consistent, WAL-safe, read-only copy via sqlite3's own backup API —
    never a raw file copy, which could race a concurrent writer."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def snapshot_source(source_cache_dir: Path, snapshot_dir: Path) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "chroma").mkdir(parents=True, exist_ok=True)

    _sqlite_backup(source_cache_dir / "working_context.sqlite", snapshot_dir / "working_context.sqlite")
    _sqlite_backup(
        source_cache_dir / "chroma" / "chroma.sqlite3",
        snapshot_dir / "chroma" / "chroma.sqlite3",
    )
    # HNSW segment shards (data_level0.bin etc, one directory per collection
    # segment) aren't sqlite — best-effort plain copy. Not transactionally
    # tied to the chroma.sqlite3 backup above; a note embedded in the exact
    # instant of this copy could be missing from the snapshot's vectors.
    # Acceptable for a point-in-time measurement script, never for anything
    # that writes back.
    for child in (source_cache_dir / "chroma").iterdir():
        if child.name in ("chroma.sqlite3", "chroma.sqlite3-wal", "chroma.sqlite3-shm"):
            continue
        dst_child = snapshot_dir / "chroma" / child.name
        if child.is_dir():
            shutil.copytree(child, dst_child, dirs_exist_ok=True)
        else:
            shutil.copy2(child, dst_child)


def build_snapshot_store(snapshot_dir: Path):
    import chromadb
    from agent.working_context_store import WorkingContextStore
    from agent.config import EMBEDDING_DEFAULT_MODEL
    from agent.indexer._types import get_embed_provider

    provider = get_embed_provider(EMBEDDING_DEFAULT_MODEL)
    client = chromadb.PersistentClient(path=str(snapshot_dir / "chroma"))
    return WorkingContextStore(
        str(snapshot_dir), embed_fn=provider.embed, embed_query_fn=provider.embed_query,
        notes_chroma_client=client,
    )


def load_labels(labels_path: Path, snapshot_dir: Path) -> tuple[list[LabeledQuery], dict[str, NoteInfo]]:
    """Load a hand-labeled query set and resolve each `relevant_note_ids`
    entry against the snapshot.

    Every resolved note must be NON-superseded: `WorkingContextStore.recall()`
    excludes `valid_until IS NOT NULL` rows by default (superseded notes),
    and this harness calls `recall()` unmodified. A label that points at a
    superseded note can never be hit under normal recall — it would register
    as a permanent miss regardless of retrieval quality, silently inflating
    the measured miss rate rather than reflecting a real recall failure. Fail
    loudly here rather than let that invalidate the number downstream.
    """
    raw = json.loads(labels_path.read_text())
    conn = sqlite3.connect(f"file:{snapshot_dir / 'working_context.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    queries: list[LabeledQuery] = []
    key_to_note: dict[str, NoteInfo] = {}
    for entry in raw:
        keys = tuple(str(nid) for nid in entry["relevant_note_ids"])
        queries.append(LabeledQuery(
            query=entry["query"], relevant_keys=keys, basis=entry.get("basis", ""),
            source=entry.get("source", "live-session"),
        ))
        for nid in entry["relevant_note_ids"]:
            key = str(nid)
            if key in key_to_note:
                continue
            row = conn.execute(
                "SELECT kind, content, superseded_by, superseded_by_note_id "
                "FROM notes WHERE note_id = ?",
                (nid,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"labels reference note_id={nid} not present in the snapshot")
            if row["superseded_by"] or row["superseded_by_note_id"]:
                raise SystemExit(
                    f"labels reference note_id={nid}, which is SUPERSEDED "
                    f"(superseded_by={row['superseded_by']!r}, "
                    f"superseded_by_note_id={row['superseded_by_note_id']!r}). "
                    "recall() excludes superseded notes by default, so this label "
                    "can never be hit -- fix the label to point at the current note "
                    "before measuring, do not weaken this check."
                )
            key_to_note[key] = NoteInfo(note_id=nid, kind=row["kind"], content=row["content"])
    conn.close()
    return queries, key_to_note


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-cache-dir", required=True, type=Path)
    ap.add_argument("--snapshot-dir", required=True, type=Path)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--skip-snapshot", action="store_true", help="Reuse an existing --snapshot-dir instead of re-copying the source.")
    args = ap.parse_args()

    snapshot_dir = _require_under_tmp(args.snapshot_dir)
    if not args.skip_snapshot:
        snapshot_source(args.source_cache_dir, snapshot_dir)

    store = build_snapshot_store(snapshot_dir)
    queries, key_to_note = load_labels(args.labels, snapshot_dir)
    report = evaluate_recall(store, args.workspace, queries, key_to_note, k=args.k)
    print(report.format_text(title="Live-corpus recall-miss harness (snapshot, read-only)"))


if __name__ == "__main__":
    main()
