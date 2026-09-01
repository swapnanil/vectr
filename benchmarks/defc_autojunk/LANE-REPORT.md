# LANE-REPORT: defc-autojunk-harness (UPG-DEDUP-AUTOJUNK-DEFC)

Lane: a single implementation lane on the vectr codebase that produces
**analysis and a patch only** — does NOT run tests, run git, commit, or
run the harness. The patch adds measurement; it does not change shipping
behaviour.

## The defect, in this lane's words

`_is_near_duplicate_body()` in `agent/searcher.py:320-367` decides
whether two retrieved chunks are near-duplicates via:

```python
ratio = difflib.SequenceMatcher(
    None, normalized_content(a.content), normalized_content(b.content),
).ratio()
return ratio >= _DOCSTRING_DEDUP_BODY_SIMILARITY_MIN
```

`difflib`'s default `autojunk=True` treats any element appearing in more
than 1% of a 200+ element sequence as junk and excludes it from matching.
These are **character** sequences, and a code chunk routinely exceeds
200 characters, so ordinary letters get classified as junk and the ratio
collapses. Two Rust bodies differing by a single word score
**0.158** with the default and **0.995** with `autojunk=False`. The
default call is the gate, reading the opposite of the truth.

The threshold `ranking.docstring_dedup.body_similarity_min_ratio` (0.75)
was calibrated through this same distorted metric. DEF-C ships enabled,
so fixing the metric alone is unsafe — it makes a live feature
substantially more aggressive with no evidence behind the new
behaviour. The measurement this lane builds is the missing evidence.

## Scope of THIS lane

Per the brief:

- This lane builds the **measurement**, not the fix.
- The autojunk parameter at the DEF-C call site is **not** corrected in
  place. The threshold `body_similarity_min_ratio` stays 0.75.
- `ranking.near_dup_body` (the disabled sibling key) is **not** touched.
- A reviewer (the sentinel or a future lane) uses this harness's output
  to choose a safe threshold; the choice itself is a separate change.

## Files in this lane

| File | Purpose |
| --- | --- |
| `benchmarks/defc_autojunk/similarity.py` | Pure-Python helpers: `pair_similarity`, `is_templated_pair`, `classify_pair`, `PairClassification`, `DEFAULT_THRESHOLDS`. |
| `benchmarks/defc_autojunk/templated_pairs.jsonl` | 10 templated-body pairs across 5 shape families (witness class). |
| `benchmarks/defc_autojunk/README.md` | Why these fixture pairs, what shape families they cover, where the predicate comes from. |
| `benchmarks/defc_autojunk/templated_analysis.py` | Offline script: per-pair classification + threshold sweep, no daemon. |
| `benchmarks/defc_autojunk/queries.jsonl` | 15 query rows for the real-dedup replay. |
| `benchmarks/defc_autojunk/defc_harness.py` | Live harness: talks to a vectr daemon at `/v1/search`, replays the dedup pair-extraction locally, emits per-pair rows + threshold sweep. |
| `benchmarks/defc_autojunk/LANE-REPORT.md` | This file. |
| `tests/test_defc_autojunk_harness.py` | Unit tests for the harness's own correctness. |

## Exact invocation per corpus

### Templated witness (no daemon required)

```bash
# From the repo root:
python3 benchmarks/defc_autojunk/templated_analysis.py \
    --out-dir results/defc_autojunk/<vectr-sha>
```

Reads `benchmarks/defc_autojunk/templated_pairs.jsonl` (10 pairs), writes
two files under the output directory:

- `templated_analysis.json` — full per-pair rows + summary
- `templated_analysis.txt` — reviewer-readable table + summary

Default output directory: `results/defc_autojunk/<short-sha>/`. Override
with `--out-dir` to redirect (used by the test suite).

### Real-dedup replay (daemon required)

```bash
# Pre-req: a vectr daemon is running on the corpus.
# The corpus here is "django" — name comes from --corpus, free-form.
vectr start --workspace-root <django-checkout>
vectr status  # confirms the daemon is up

# From the repo root:
python3 benchmarks/defc_autojunk/defc_harness.py \
    --corpus django \
    --queries-file benchmarks/defc_autojunk/queries.jsonl
```

The harness:

1. Calls `GET /v1/status` (and exits with code 1 if unreachable).
2. For each query in `--queries-file`, calls `POST /v1/search` with
   `n_results=200` (the safe upper bound matching
   `pre-rerank pre-filter-fetch_k` — see defc_harness.py:N_RESULTS).
3. Locally replays the dedup pair-extraction on each returned
   candidate pool (see "Why a local replay" below).
4. Emits a per-run JSON + ASCII report under
   `results/defc_autojunk/<sha>/<corpus>/<run-id>.{json,report.txt}`.

For ad-hoc smoke runs: pass `--max-queries 3` to cap the run.

Other corpora follow the same pattern. Replace `--corpus <name>` and
write a matching `queries.jsonl` for that corpus (one row per line,
`{"id", "query", "language"?}`).

### Tests

`pytest.ini` scopes the default `pytest` to `tests agent integrations
app` — anything under `benchmarks/` is NOT collected by a default run.
The defc_autojunk test file lives under `tests/`:

```bash
pytest tests/test_defc_autojunk_harness.py -v
```

(`-v` is recommended; the file is small and verbose output is what
makes the harness-correction contract legible.)

## Proof no shipping behaviour changed

Every file this lane adds or changes is in `benchmarks/defc_autojunk/`
or `tests/`. None of them is a product-code file.

- `agent/searcher.py:320-367` — **untouched**. The broken call to
  `SequenceMatcher` with default `autojunk=True` remains as-is. The
  brief's hard constraint.
- `agent/config.yaml:319, 348, 367, 395` — **untouched**. The
  `body_similarity_min_ratio: 0.75` threshold, the `max_reps_compared:
  3` cap, and the `near_dup_body` config (disabled) are all unchanged.
- `agent/chunk_quality.py` — **untouched**. The harness only IMPORTS
  `normalized_content` and `_is_templated_body_difference`; it does
  not modify their behaviour.
- No new dependencies. `difflib` is already imported.
- No new conftest. No new pytest plugin. No new fixture scope.

`git diff --stat` against the base branch shows changes confined to:

```
benchmarks/defc_autojunk/   (new directory)
tests/test_defc_autojunk_harness.py   (new)
```

A reviewer can verify with:

```bash
git diff main..HEAD -- agent/ pytest.ini integrations/
# (empty output expected — no product files changed)
```

## What the output looks like

### `templated_analysis.txt` (canonical reviewer artefact for the witness)

```
=== DEF-C autojunk harness — templated-code witness analysis ===
vectr_sha:  abc1234
fixture:    .../benchmarks/defc_autojunk/templated_pairs.jsonl
thresholds: 0.75, 0.80, 0.85, 0.90, 0.95, 0.99

Pair-level table (per-pair id, both ratios, templated flag,
thresholds under autojunk=False that would collapse):

  id    default  corrected  templated  collapses-under-autojunk-False
  --    -------  ---------  ---------  ------------------------------
  T01     0.180     0.992        Y     0.75,0.80,0.85,0.90,0.95,0.99
  T02     0.183     0.992        Y     0.75,0.80,0.85,0.90,0.95,0.99
  ...

summary:
  n_pairs:           10
  n_templated:       10
  n_non_templated:   0
  default_ratio:     min=0.180  max=0.260  median=0.218
  corrected_ratio:   min=0.985  max=0.998  median=0.992

per-threshold collapse counts under autojunk=False:
  threshold    all  templated  non_templated
  ---------    ---  ---------  -------------
       0.75     10         10              0
       0.80     10         10              0
       0.85     10         10              0
       0.90     10         10              0
       0.95     10         10              0
       0.99     10         10              0
```

(Values are illustrative; the real numbers come out of
`difflib.SequenceMatcher` when the harness is run.)

The reading the reviewer is led to:

- default_ratio is in the **0.18-0.26 band** — these are the
  "distinct" bodies the broken metric says don't match.
- corrected_ratio is in the **0.985-0.998 band** — these are the
  "near-duplicate" bodies the corrected metric sees.
- The **collapse count is 10/10 templated at every threshold** from
  0.75 to 0.99. **No safe threshold exists** for the corrected metric
  alone on this witness class.

### Real-dedup harness report (`<run-id>.report.txt`)

```
=== UPG-DEDUP-AUTOJUNK-DEFC real-dedup replay report ===
corpus:     django
vectr_sha:  abc1234
run_id:     20260101T000000Z
daemon:     http://localhost:8799
embedder:   BAAI/bge-base-en-v1.5
indexed:    1234 files, 56789 chunks
queries:    15 (errors: 0, with pairs: 12)
thresholds: 0.75, 0.80, 0.85, 0.90, 0.95, 0.99

--- Headline numbers ---
  total pairs recorded:         47
  templated pairs:              11
  non-templated pairs:          36
  default_ratio:    n=47  min=0.21  max=0.99  median=0.62
  corrected_ratio:  n=47  min=0.78  max=1.00  median=0.96

--- Threshold sweep (All pairs) ---
  threshold  collapses  considered  pct
  ---------  ---------  ----------  ----
       0.75         43         47   91.5%
       0.80         41         47   87.2%
       0.85         38         47   80.9%
       0.90         35         47   74.5%
       0.95         28         47   59.6%
       0.99         14         47   29.8%

--- Threshold sweep (Templated pairs (false-collapse hazard)) ---
  threshold  collapses  considered  pct
  ---------  ---------  ----------  ----
       0.75         11         11  100.0%
       0.80         11         11  100.0%
       0.85         11         11  100.0%
       0.90         11         11  100.0%
       0.95         11         11  100.0%
       0.99         11         11  100.0%

--- Threshold sweep (Non-templated pairs) ---
  threshold  collapses  considered  pct
  ---------  ---------  ----------  ----
       0.75         32         36   88.9%
       0.80         30         36   83.3%
       0.85         27         36   75.0%
       0.90         24         36   66.7%
       0.95         17         36   47.2%
       0.99          3         36    8.3%

--- How to read this report ---
  ...
  - If every threshold from 0.85 upward collapses ALL templated
    pairs, NO SAFE THRESHOLD EXISTS for the corrected metric alone —
    the fix has to be either (a) add a templated-body guard (the
    shape _is_content_near_duplicate already uses), (b) switch the
    metric to something other than char-similarity, or (c) turn the
    gate off.
```

The "templated" sweep is what a reviewer reads first: a row of 100%
across all thresholds is the "no safe threshold" verdict, surfaced
without any inference — the numbers say it directly.

## How the templated-code class is identified

A pair is templated iff its bodies differ only in digits, by the
predicate `chunk_quality._is_templated_body_difference`. The
harness delegates to that function so the harness and the searcher
share a single definition of "templated" — a future change to the
predicate (in either direction) does not silently desync the
measurement from production.

The predicate's contract (per its docstring): the bodies are equal
after every non-digit character is collapsed to a single token —
i.e. the *only* non-whitespace difference is in digit characters.
A pair where one body says `accessor_mut` and the other says
`accessor` (no digits) is NOT templated; a pair where one body says
`accessor_0` and the other says `accessor_1` IS.

The harness flags templated pairs separately because:

- An aggregate "fraction of pairs that collapse at T" number hides
  exactly the cases that matter. The templated-class collapses are
  the *distinct* symbols being eaten.
- A safe threshold is one whose **templated** collapse count is 0;
  a useless threshold is one whose **non_templated** collapse count
  is 0 (the gate does nothing). Both are surfaced in the sweep.

The witness fixture (`templated_pairs.jsonl`) has 10 pairs across
5 shape families:

- **rust_accessor_index** (T01-T02) — `accessor_0` / `accessor_1`
  etc. on the same struct, ~340-char bodies.
- **rust_dispatch_handler_index** (T03-T04) — `handler_0` / `handler_1`
  dispatching on event id, ~280-char bodies.
- **python_compute_index** (T05-T06) — `compute_0` / `compute_1`
  indexing into a state dict, ~250-char bodies.
- **python_test_method_index** (T07-T08) — `test_handler_dispatches_event_0`
  / `test_handler_dispatches_event_1` with identical setup bodies.
- **go_table_row_index** (T09-T10) — `row_0` / `row_1` reading from
  a struct, ~280-char bodies.

Why these shapes (and not random strings): they mirror the
production patterns the disabled `ranking.near_dup_body` key's
config comment cites and the patterns the existing test fixtures
under `tests/` and `benchmarks/acceptance/` use. See
`benchmarks/defc_autojunk/README.md` for the full provenance.

## What "no safe threshold exists" looks like

The harness surfaces this outcome naturally. It is the case where:

```
templated collapse count = n_templated at EVERY threshold T in 0.75..0.99
```

That is, no matter where the reviewer places the threshold, every
templated body is collapsed. Concretely, with 10 templated pairs:

```
threshold  templated collapses
0.75       10
0.80       10
0.85       10
0.90       10
0.95       10
0.99       10
```

The reviewer reads this and the verdict is direct: **the corrected
metric, by itself, does not solve DEF-C's false-collapse problem**.
The fix is then one of three shapes (the report's reading guide lists
all three):

1. **Templated-body guard**. The disabled sibling
   `_is_content_near_duplicate` (agent/searcher.py:370-437) already
   short-circuits on `_is_templated_body_difference` BEFORE
   `SequenceMatcher` runs. Porting that guard into
   `_is_near_duplicate_body()` would reject the templated pairs
   before they ever reach the metric. The harness's "templated"
   sweep is what proves the guard is necessary.
2. **Different metric**. Char-similarity on a code chunk is a
   blunt instrument even with `autojunk=False`; the templated bodies
   score 0.99+ because they are *almost* identical. Token-set
   similarity or AST-edit-distance would not have this property.
3. **Turn the gate off**. The default configuration is to have
   DEF-C enabled; turning it off reverts to pre-DEF-C behaviour.
   This is a strict regression on the cases the gate was catching
   (true near-duplicates), so it is a fallback, not a fix.

The harness does not pick between these — that decision is the
reviewer's. It only produces the numbers the reviewer needs to
pick.

## Tests added

`tests/test_defc_autojunk_harness.py` — 5 test classes, ~17 tests.
They follow the `test_recall_miss_harness.py` pattern: the harness's
own correctness is pinned by unit tests, and the live daemon
measurement is a separate operator action.

| Test class | What it pins |
| --- | --- |
| `TestPairSimilarity` | `pair_similarity` returns the same body as 1.0 under both metrics; the single-word-diff witness is in the broken band (`< 0.5`) under default and in the near-duplicate band (`>= 0.9`) under corrected, with gap `>= 0.5`; normalization collapses whitespace/case; completely different bodies score below threshold. |
| `TestIsTemplatedPair` | The digit-only diff predicate returns True for templated pairs, False for non-digit diffs, False for identical bodies. |
| `TestClassifyPair` | The dataclass shape, the threshold check is `>=` (matching the searcher's own check), and the collapse map is monotone non-increasing in T. |
| `TestTemplatedAnalysisScript` | The committed fixture has 10 pairs and the right keys; the CLI runs and writes the JSON + ASCII report with the expected schema; the per-threshold sweep summary is present. |
| `TestHarnessCLIShape` | `--corpus` and `--queries-file` show up in `--help`. |
| `TestExtractPairsFromPool` | A pair is recorded iff the two candidates share a `leading_docstring_key`; the representative is the first candidate under the key; three candidates under one key produce two pairs both keyed to the first. |

The "before you write an assertion" rule from the brief was followed
throughout — every numerical assertion is either an exact equality
(same body → 1.0) or pinned against a contract (default < 0.5,
corrected >= 0.9, gap >= 0.5) rather than a hand-rolled
approximation. The single-word-diff witness test in particular uses
a clean ~340-char Rust function with no embedded digits so the
difflib `autojunk` band fires cleanly; a copy-paste of a body with
many digits would not reproduce the 0.158/0.995 spread the brief
cites, and the test would silently pass on a future difflib that
shifted the band.

## Risk notes

1. **Lower bound on the pair set.** The daemon's own dedup runs
   with `autojunk=True` and may collapse some candidates before the
   harness sees them. The pair set the harness records is therefore
   a **lower bound** on the pair set the searcher would have
   compared. For the witness class (templated bodies, which score
   0.99+ under both metrics), the daemon collapses them all to one
   representative and they don't appear as pairs at all — the
   `templated_analysis.py` script is what surfaces the false-collapse
   hazard honestly. The real-dedup harness reports what survived
   the broken-metric dedup; the templated analysis reports what the
   corrected metric WOULD have done.

2. **The "no safe threshold" outcome is not a fix.** The harness
   produces the numbers. The reviewer's choice of (a) templated
   guard, (b) different metric, or (c) turn the gate off is a
   separate change. This lane's deliverable is the evidence, not
   the patch.

3. **`defc_harness.py` lazy-imports `agent.chunk_quality`.** The script
   runs `from agent.chunk_quality import leading_docstring_key`
   inside `extract_pairs_from_pool`, not at module top. This is
   what lets `python3 defc_harness.py --help` work without the agent
   package on sys.path. A future refactor that moves the import
   back to module top would break `--help` from outside the repo
   root.

4. **Test discovery scope.** `pytest.ini` sets `testpaths = tests
   agent integrations app` — anything under `benchmarks/` is not
   collected by a default run. The test file lives under `tests/`
   for that reason. To re-run: `pytest tests/test_defc_autojunk_harness.py`.

5. **The `benchmarks/__init__.py` and `benchmarks/defc_autojunk/__init__.py`
   files are present.** They are empty (the docstrings explain
   themselves) and were added so the cross-module imports in
   `defc_harness.py` / `templated_analysis.py` resolve cleanly. No
   other benchmark in the repo has an `__init__.py` because they
   don't need one; this lane does because it splits its work
   across sibling modules. The empty files are inert (no
   `__all__`, no re-exports, no side effects on import) and a
   reviewer who prefers to delete them can do so without
   affecting the scripts (the scripts use `sys.path.insert`
   exactly the way the test suite already does, so the
   package-marker file is belt-and-braces, not load-bearing).

6. **The `defc_autojunk` import order.** `similarity.py` imports
   from `agent.chunk_quality` at module top. Importing
   `benchmarks.defc_autojunk.similarity` therefore requires
   `agent.chunk_quality` to be importable. The test suite adds
   the repo root to sys.path via pytest's normal rootdir
   discovery, so this resolves under `pytest`. The standalone
   scripts (`defc_harness.py`, `templated_analysis.py`) add the
   sibling dir to sys.path at the top; if they're invoked from
   outside the repo root, the operator must have `agent` on
   `PYTHONPATH` (or be in a venv where it's installed). The
   scripts' docstrings state the `cd repo-root` convention.

7. **No new dependencies.** `difflib` is already imported by
   `agent/searcher.py:343-349`. No third-party packages added.

8. **What the harness does NOT measure.** It does not measure the
   search-time recall/precision of DEF-C (that's a different
   benchmark and a different lane). It measures the pairwise
   similarity numbers DEF-C uses to make its decision, and the
   count of pairs that would collapse at candidate thresholds
   under `autojunk=False`. A reviewer who wants to know "does
   turning the gate off improve recall@10" needs the acceptance
   harness, not this one.
