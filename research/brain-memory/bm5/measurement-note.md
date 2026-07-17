# BM-5 measurement note — re-exploration waste, analyzer v1

2026-07-14. Analyzer v1 over the full transcript inventory (9 valid
gate-run transcripts + 1 invalid run used for analyzer testing only).
Definitions in `definition.md`; per-run JSON in `v1-results.json`
(v0 baseline preserved in `v0-baseline.json`).

## Headline numbers (9 valid transcripts, single coding-task family)

| Metric | Value |
|---|---|
| R1 intra-session re-reads | **61** |
| R2 cross-compaction re-reads (subset of R1) | **24 — 39% of all re-reads** |
| R3 repeated searches (post verification-purge) | 2 |
| Wasted result tokens (chars/4 proxy) | **~78,400** |
| Exploration calls | 576 |
| Pooled re-exploration share | **10.9%** (per-run range 0–29.1%) |

## The distribution is the finding

Re-exploration is not uniform noise — it concentrates:

- The three heaviest runs (A-20260712-233814, C-20260713-015112,
  CS-20260713-035443) hold **55 of 61 R1** and **all 24 R2**; each spans
  at least one compaction (two runs have two).
- Fresh single-context runs sit at or near zero (four runs with R1=0).
- The single worst run wasted ~31.6k result tokens on re-reads — on the
  order of a sixth of a context window spent re-retrieving content the
  session had already paid for.

This is the priced version of thesis §6's prediction: waste concentrates
at context-loss boundaries. R2 — a file read before compaction, re-read
after — is precisely the class a memory tier with deterministic
post-compaction injection addresses, and it accounts for 39% of observed
re-reads. Note also that an injection-equipped arm is not automatically
immune (C-20260713-015112: share 29.1%, mostly non-compaction R1 from
iterative build debugging) — injection targets the R2 class specifically,
not all re-reading.

## v0 → v1: what moving the floor changed

v1 (compound-command splitting, Bash-side mutation resets, per-segment
R3 keys, verification semantics) moved the measurement in BOTH
directions, which is the point of the exercise:

- **Recovered (false negatives removed)**: 12 file reads hidden inside
  compound commands now enter the read ledger (9 in A-20260712-185136
  alone, taking its R1 from 2 to 3); v0's whole-command R3 keys never
  matched anything (R3=0 across the board — a floor artifact).
- **Purged (false positives prevented)**: naive per-segment keys
  initially inflated R3 to 3–8 per run; spot-verification showed they
  were pipeline FILTERS (`… | grep -v /target/` repeated 8× in one run)
  and `git diff <path>` progress checks during edit loops (3× on a
  generated file). v1 excludes pipe-position segments, `git diff`, and
  bare listing commands, and resets path-matching search keys on
  mutation — leaving R3=2 genuine repeats. The purge rules are
  deterministic and documented in the analyzer header.

Both spot-verifications are reproducible: rerun the repeated-key dump in
the analyzer against B-20260712-121951 and A-20260712-233814.

## Honesty ledger (v1)

- n=9 transcripts, one task family, one agent product — directional
  evidence and arm-relative comparison, not a population estimate.
- chars/4 token proxy; relative comparisons only.
- Commands with substitution ($(), backticks) remain opaque; cwd-relative
  path aliasing unresolved — R1 remains a floor, but a higher one than v0.
- `narrowing` re-reads (whole-file then range) stay counted and flagged
  (10 across the corpus) for sensitivity analysis.
- R4 (cross-session re-derivation) is still v1-future: it needs paired
  sessions on the same task, which the gate corpus doesn't yet contain.

## Feeds

- Paper §measurement: the 39%-of-re-reads-are-cross-compaction number and
  the concentration finding.
- PR proposal: prices what post-compaction memory injection is for.
- BM-4: `reexploration_share` and `r2_cross_compaction` are the
  missed-fire joins named in the BM-2 §7 measurement map.
