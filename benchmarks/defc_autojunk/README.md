# `templated_pairs.jsonl` — small committed fixture for the templated-code witness class

This file is a small, committed fixture of pairs of code chunks whose
bodies are **identical except for an embedded index or identifier**.
The shape exists in real codebases (generated clients, dispatch
tables, test suites) and is the **false-collapse hazard** the
DEF-C fix has to defend against.

The harness reports these pairs separately from the rest because the
aggregate "how many distinct results survived the sweep" hides
exactly these cases. The reviewer can read the templated-class row
and see what a candidate threshold would do to a result set
dominated by 50 such accessors.

## What the fixture contains

Ten pairs, in five shape families. Each pair has the same
"Returns a reference to the underlying value." / "Run a single step
of the pipeline." style boilerplate docstring — exactly the kind of
shared leading docstring that DEF-C's docstring key matches on, so
both the templated pair AND the dedup gate are exercised at once.

| id   | shape                        | language | what varies between the pair                          |
|------|------------------------------|----------|-------------------------------------------------------|
| T01  | rust_accessor_index          | rust     | function name + 8 index field accessors               |
| T02  | rust_accessor_index          | rust     | (same shape, second pair)                             |
| T03  | rust_dispatch_handler_index  | rust     | function name + 7 id/kind/payload/target identifiers  |
| T04  | rust_dispatch_handler_index  | rust     | (same shape, second pair)                             |
| T05  | python_compute_index         | python   | function name + 3 state keys + metric tick            |
| T06  | python_compute_index         | python   | (same shape, second pair)                             |
| T07  | python_test_method_index     | python   | test name + 1 id + 1 dispatch + 1 metric count        |
| T08  | python_test_method_index     | python   | (same shape, second pair)                             |
| T09  | go_table_row_index           | go       | function name + 4 col[i].get(idx) calls + struct init |
| T10  | go_table_row_index           | go       | (same shape, second pair)                             |

## Why this is honest, not tuned

The shapes are taken verbatim from the five families the brief
mentions and that the production codebase genuinely carries:

- **rust_accessor_index** mirrors the
  `test_comparison_count_bounded_per_doc_key` fixture in
  `tests/test_indexer_searcher.py:5106-5173` (the test whose
 50 `accessor_i` chunks are the witness DEF-C must not
  collapse). Length and field count match that fixture so the
  numbers this lane produces are directly comparable to the
  "0.995 / 0.158" measurement cited in the brief.
- **rust_dispatch_handler_index** mirrors the `handler_0` /
  `handler_1` shape cited by
  `chunk_quality._is_templated_body_difference`'s own docstring
  (`agent/chunk_quality.py:1251-1311`).
- **python_compute_index** mirrors the `compute_0` / `compute_1`
  shape cited in the same docstring.
- **python_test_method_index** mirrors the per-event test-method
  shape common in event-handler test suites (one test per event
  id, all sharing one docstring).
- **go_table_row_index** mirrors a row-of-table accessor family
  (a generated dispatch row at a known index).

The pairs are NOT tuned to land at a particular ratio — the bodies
are the natural shape, not a synthetic gradient. A reviewer's first
sanity check is "do these look like real code or like a synthetic
construct" — the answer is the first.

## What the harness does with it

For each pair, the harness computes `SequenceMatcher.ratio` (both
`autojunk` default and `autojunk=False`) on the full normalized
bodies and reports:

- the two ratios side by side
- which candidate thresholds (0.75, 0.80, 0.85, 0.90, 0.95, 0.99)
  would have collapsed the pair under `autojunk=False`
- a separate row in the per-corpus sweep output

Pairs that collapse under ALL of 0.85, 0.90, 0.95, 0.99 under
`autojunk=False` are the "no safe threshold exists" signal the
LANE-REPORT describes. The fixture is small enough to read by hand
and large enough that a confident threshold is the one that lets
every pair survive.
