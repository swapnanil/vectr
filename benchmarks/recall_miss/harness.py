"""Recall-miss measurement harness (UPG-RECALL-MISS-FLOOR, part (a) only).

Framing (user direction 2026-08-09): `MEMORY.md`-style unconditional
injection does not have BETTER retrieval than vectr's semantic recall — it
has NO retrieval, which only scales to a ~40-line file. At hundreds of
notes that strategy is unavailable, so "make semantic matching better" is
the wrong fix; the real fix is a floor that does not depend on matching at
all (Tier 0 unconditional injection, deterministic anchor/tag/symbol
channels — tracked separately as UPG-RECALL-MISS-FLOOR (b)/(c), NOT part of
this module). Before any of that is tuned, the miss rate has to be a
number, not a feeling — every claim about it to date, including the
sentinel's own, was qualitative.

This module is a pure measurement layer. It calls the REAL
`WorkingContextStore.recall()` (the exact function every `vectr_recall` MCP
call goes through) unmodified, with a real query string, and diffs the
returned note ids against a hand-labeled relevant set. It adds no
query-content classification, reranking, or gating of its own anywhere —
see the module docstring in fixture_corpus.py for why the labels
themselves are not allowed to be circular either.

Two ways to build the (queries, key_to_note) inputs this module consumes:
  - fixture_corpus.py: a small, synthetic, committed corpus for CI
    (tests/test_recall_miss_harness.py runs it deterministically).
  - run_live.py: an opt-in script mode against a read-only snapshot of a
    real note database, with a hand-labeled query file supplied by the
    operator (never committed — see run_live.py's module docstring for why).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NoteInfo:
    """The subset of a stored note a labeled query set needs to reference
    and a miss report needs to render — captured by the corpus builder (or
    read back from a snapshot) at data-prep time, never re-derived from a
    recall() result."""
    note_id: int
    kind: str
    content: str


@dataclass(frozen=True)
class LabeledQuery:
    """One hand-labeled (query, relevant-note-set) pair.

    `relevant_keys` names stable corpus keys (fixture_corpus.py) or, in
    live mode, `str(note_id)` — never a raw autoincrement id baked into
    this dataclass directly, since fixture note_ids are only known after
    insertion.

    `basis` records IN PLAIN ENGLISH why each note in `relevant_keys` was
    judged relevant to this query — the load-bearing, human-auditable part
    of the label (design requirement: labels must be explicit, committed,
    and auditable with the basis written down, never auto-generated from
    the retriever being measured).

    `source` documents provenance of the query text itself: "synthetic"
    (authored for this fixture), "mined-transcript" (sanitized, adapted
    from a real session transcript), or "live-session" (used verbatim in
    an opt-in live-corpus run, never committed).
    """
    query: str
    relevant_keys: tuple[str, ...]
    basis: str
    source: str = "synthetic"


@dataclass(frozen=True)
class MissRecord:
    query: str
    note_key: str
    note_id: int
    kind: str
    content_snippet: str
    basis: str


@dataclass
class RecallMissReport:
    k: int
    n_queries: int
    n_queries_scored: int  # queries with >=1 labeled-relevant note
    total_relevant: int
    total_hits: int
    macro_recall_at_k: float  # mean of per-query (hits / relevant) — each query weighted equally
    micro_recall_at_k: float  # total_hits / total_relevant — each relevant note weighted equally
    misses: list[MissRecord] = field(default_factory=list)
    misses_by_kind: dict[str, int] = field(default_factory=dict)
    hits_by_kind: dict[str, int] = field(default_factory=dict)

    def format_text(self, title: str = "Recall-miss harness result") -> str:
        lines = [
            f"=== {title} (k={self.k}) ===",
            f"queries: {self.n_queries} total, {self.n_queries_scored} scored "
            f"(>=1 labeled-relevant note)",
            f"relevant notes: {self.total_relevant}, hit: {self.total_hits}, "
            f"missed: {self.total_relevant - self.total_hits}",
            f"macro recall@{self.k} (mean per-query): {self.macro_recall_at_k:.3f}",
            f"micro recall@{self.k} (pooled over notes): {self.micro_recall_at_k:.3f}",
            "",
            "misses by kind:",
        ]
        all_kinds = sorted(set(self.misses_by_kind) | set(self.hits_by_kind))
        if not all_kinds:
            lines.append("  (no labeled-relevant notes were scored)")
        for kind in all_kinds:
            miss_n = self.misses_by_kind.get(kind, 0)
            hit_n = self.hits_by_kind.get(kind, 0)
            total = miss_n + hit_n
            rate = miss_n / total if total else 0.0
            lines.append(f"  {kind:12s} missed {miss_n}/{total} ({rate:.0%})")
        if self.misses:
            lines.append("")
            lines.append("miss detail:")
            for m in self.misses:
                lines.append(
                    f"  query={m.query!r} -> MISSED note_id={m.note_id} kind={m.kind} "
                    f"key={m.note_key!r} basis={m.basis!r} "
                    f"content={m.content_snippet!r}"
                )
        return "\n".join(lines)


def evaluate_recall(
    store,
    workspace: str,
    queries: list[LabeledQuery],
    key_to_note: dict[str, NoteInfo],
    k: int = 10,
) -> RecallMissReport:
    """Run every labeled query through `store.recall(workspace, query=...,
    limit=k)` and score the real result against the hand-labeled relevant
    set.

    A query whose `relevant_keys` is empty is excluded from both recall
    averages (there is nothing to recall, so it is not a miss OR a hit —
    counting it either way would silently move the headline number) but is
    still counted in `n_queries`.

    Raises KeyError if a query references a key absent from `key_to_note`
    — a labeling bug (a stale key after the corpus changed), not a
    retrieval-side condition to swallow.
    """
    total_relevant = 0
    total_hits = 0
    per_query_recalls: list[float] = []
    misses: list[MissRecord] = []
    misses_by_kind: dict[str, int] = {}
    hits_by_kind: dict[str, int] = {}

    for lq in queries:
        relevant_infos: list[tuple[str, NoteInfo]] = []
        for key in lq.relevant_keys:
            if key not in key_to_note:
                raise KeyError(
                    f"labeled query {lq.query!r} references unknown note key {key!r} "
                    "— the corpus and label set have drifted apart"
                )
            relevant_infos.append((key, key_to_note[key]))
        if not relevant_infos:
            continue

        returned = store.recall(workspace, query=lq.query, limit=k)
        returned_ids = {n.note_id for n in returned}

        relevant_ids = {info.note_id for _key, info in relevant_infos}
        hit_ids = relevant_ids & returned_ids
        total_relevant += len(relevant_ids)
        total_hits += len(hit_ids)
        per_query_recalls.append(len(hit_ids) / len(relevant_ids))

        for key, info in relevant_infos:
            if info.note_id in hit_ids:
                hits_by_kind[info.kind] = hits_by_kind.get(info.kind, 0) + 1
            else:
                misses_by_kind[info.kind] = misses_by_kind.get(info.kind, 0) + 1
                misses.append(MissRecord(
                    query=lq.query,
                    note_key=key,
                    note_id=info.note_id,
                    kind=info.kind,
                    content_snippet=info.content[:160],
                    basis=lq.basis,
                ))

    n_scored = len(per_query_recalls)
    macro = sum(per_query_recalls) / n_scored if n_scored else 0.0
    micro = total_hits / total_relevant if total_relevant else 0.0

    return RecallMissReport(
        k=k,
        n_queries=len(queries),
        n_queries_scored=n_scored,
        total_relevant=total_relevant,
        total_hits=total_hits,
        macro_recall_at_k=macro,
        micro_recall_at_k=micro,
        misses=misses,
        misses_by_kind=misses_by_kind,
        hits_by_kind=hits_by_kind,
    )
