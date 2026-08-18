# Recall-miss harness (UPG-RECALL-MISS-FLOOR)

Measures how often `WorkingContextStore.recall(query=...)` — the exact call
every `vectr_recall` MCP invocation goes through — fails to surface a note a
human/reviewer has judged relevant, and which note `kind`s the misses fall
on. This is measurement only: part (a) of UPG-RECALL-MISS-FLOOR. Nothing in
this directory tunes ranking or adds any floor/injection mechanism — see
`docs/tasks.md` for parts (b) (Tier 0 unconditional injection) and (c)
(deterministic anchor/tag/symbol channels), which are separate, gated on
this number existing.

## Files
- **`harness.py`** — core measurement logic: `LabeledQuery`, `NoteInfo`,
  `evaluate_recall()`, `RecallMissReport`. Calls the real `recall()`
  unmodified and diffs its result against a hand-labeled relevant set —
  adds no query-content classification anywhere.
- **`fixture_corpus.py`** — committed, synthetic, sanitized note corpus
  (~30 notes across all 7 `VALID_KINDS`) plus a hand-labeled query set. A
  subset of the query TEXT (never note content) is adapted from real
  `vectr_recall` calls mined from this workspace's own session transcripts;
  every note body was authored for this fixture. Every label's `basis` is
  a plain-English sentence auditable against the corpus in the same file.
- **`mine_prompts.py`** — structural-only transcript miner (assistant
  tool_use blocks -> a vectr tool's own `query` argument); produces
  candidate query strings for a human to review and label, never labels
  anything itself.
- **`run_fixture.py`** — runs the harness against the fixture corpus with
  the real production embedder by default (`--dummy` for the fast
  deterministic hash embedder used by the CI regression test). At fixture
  scale (~30 notes, one clearly-worded label per note) the real-embedder
  run scores 1.000/1.000 recall@k — a ceiling effect from the corpus being
  too small and too unambiguous to separate a working retriever from a
  broken one, not a meaningful recall number. Never quote it as a recall
  result; use `run_live.py` against a real, larger corpus for that.
- **`run_live.py`** — OPT-IN script mode: snapshots a real working-memory
  database read-only (sqlite backup API, never a raw copy of a live WAL
  file) under `<repo>/tmp/`, then runs the harness against an operator-
  supplied, gitignored labels file. Never commits or prints raw note
  content beyond what a label's own basis/query already names.

## Regression test
`tests/test_recall_miss_harness.py` pins `evaluate_recall()`'s own
recall@k / miss-grouping arithmetic against a scripted store (no embedder,
no I/O) and separately smoke-tests the fixture corpus end-to-end through a
real `WorkingContextStore` with a deterministic dummy embedder — proving
the harness's wiring against the real store type without a model download.
Runs in the default `pytest -q` suite (lives under `tests/`, not
`benchmarks/`, specifically so it is not opt-in).

## Live-corpus measurement (opt-in, not part of CI)
```
python3 benchmarks/recall_miss/run_live.py \
    --source-cache-dir ~/.cache/vectr/<workspace-hash> \
    --snapshot-dir tmp/recall-miss-live \
    --workspace /path/to/workspace \
    --labels tmp/recall-miss-live/labels.json
```
`tmp/` is gitignored and `.vectrignore`d — the always-on daemon skips it.
The labels file is never committed (see `run_live.py`'s module docstring
for why: it necessarily contains a real database's own note topics).
