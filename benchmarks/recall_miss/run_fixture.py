#!/usr/bin/env python3
"""Run the recall-miss harness against the committed fixture corpus
(UPG-RECALL-MISS-FLOOR part (a)).

This is the human-runnable counterpart to `tests/test_recall_miss_harness.py`
(which pins the same fixture with a deterministic dummy embedder so CI never
needs a model download). Default here is the REAL production embedder, so a
developer can eyeball what recall@k looks like on real vectors without
touching any live workspace database.

Usage:
    python3 benchmarks/recall_miss/run_fixture.py            # real embedder
    python3 benchmarks/recall_miss/run_fixture.py --dummy    # fast, no model
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_corpus import FIXTURE_QUERIES, FIXTURE_WORKSPACE, build_fixture_corpus  # noqa: E402
from harness import evaluate_recall  # noqa: E402


def _dummy_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic hash-based embedder (same shape as tests/test_memory.py's
    B9 semantic-recall fixture) — only used with --dummy, never in production."""
    import hashlib
    out = []
    for t in texts:
        h = hashlib.md5(t.encode()).digest()
        vec = [(b / 255.0 - 0.5) for b in (h * 48)]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        out.append([x / norm for x in vec])
    return out


def build_store(db_dir: Path, dummy: bool):
    import chromadb
    from agent.working_context_store import WorkingContextStore

    client = chromadb.PersistentClient(path=str(db_dir / "chroma"))
    if dummy:
        return WorkingContextStore(str(db_dir), embed_fn=_dummy_embed, notes_chroma_client=client)

    from agent.config import EMBEDDING_DEFAULT_MODEL
    from agent.indexer._types import get_embed_provider

    provider = get_embed_provider(EMBEDDING_DEFAULT_MODEL)
    return WorkingContextStore(
        str(db_dir), embed_fn=provider.embed, embed_query_fn=provider.embed_query,
        notes_chroma_client=client,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dummy", action="store_true", help="Use the fast deterministic dummy embedder instead of the real model.")
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="vectr-recall-miss-fixture-") as tmp:
        store = build_store(Path(tmp), dummy=args.dummy)
        key_to_note = build_fixture_corpus(store, FIXTURE_WORKSPACE)
        report = evaluate_recall(store, FIXTURE_WORKSPACE, FIXTURE_QUERIES, key_to_note, k=args.k)
        print(report.format_text(title="Fixture recall-miss harness" + (" (dummy embedder)" if args.dummy else " (real embedder)")))


if __name__ == "__main__":
    main()
