# BM-5 — Re-exploration waste: operational definition (v0 → v1)

Foundation doc, prepared 2026-07-12. The literature admits no rigorous
number exists for re-exploration waste (thesis §6); this defines one
measurably, over Claude Code transcript JSONL files. `analyzer.py` in this
directory implements v0; the inventory of available transcripts is in
`inventory.md`.

## Events counted as re-exploration (v0)

- **R1 — intra-session re-read**: a read of a file whose content was
  already read this session (Read tool, or a read-only Bash command on the
  same path), where the ranges overlap (absent offset/limit = whole file).
- **R2 — cross-compaction re-read**: R1 where the prior read happened
  before a compaction boundary and the repeat after it. Subset of R1,
  reported separately — it prices compaction recovery.
- **R3 — repeated search**: a search with the same normalized key repeated
  (Grep: pattern+path+glob; Glob: pattern; vectr_search: normalized query;
  vectr_locate: name; read-only Bash: exact normalized command).

## Exclusions (what is NOT waste)

- A re-read of a file **after the agent itself edited it** (Edit / Write /
  NotebookEdit / a Bash command matched as mutating that path) —
  verification reads are legitimate. The edit resets the file's read
  ledger.
- Disjoint range reads of the same file (different offset windows).
- Reads of build/test output artifacts (surefire XML etc.) are counted but
  tagged `artifact` so they can be excluded in reporting.

## Metrics (per transcript, and aggregated per arm)

- counts: R1, R2, R3; total tool calls; exploration calls (reads+searches)
- **wasted result tokens**: sum of tool_result sizes for repeat calls,
  chars/4 proxy (v0 honesty caveat: proxy, not tokenizer-exact)
- **re-exploration share**: (R1+R3) / exploration calls
- v1 (not v0): wall-time attribution (needs sleep-safe clocks); R4
  cross-session re-derivation (needs session pairing).

## v1 extensions (implemented 2026-07-14; results in measurement-note.md)

- **Compound Bash commands split** quote-aware into segments on
  `&& || ; |`; each segment classified independently — reads inside
  compounds enter the read ledger (v0 treated compounds as opaque).
- **Bash-side mutations reset ledgers**: `> / >>`, `sed -i`, `tee`,
  `mv/cp/rm` reset the target path's read ledger AND purge search keys
  naming that path — a search re-run against an edited file is
  verification, mirroring the v0 exclusion for reads.
- **R3 keys are per read-only segment**, with verification semantics:
  pipeline-position segments (after `|`) are filters, not retrievals;
  `git diff` is excluded entirely (output tracks working-tree state —
  repetition during an edit loop is progress-checking); bare
  argument-less listing commands are not keyed (cwd aliasing).
- Commands containing `$( )` or backticks stay opaque (v0 behavior).

## Honesty ledger

- chars/4 is a proxy; direction and relative arm comparison are what we
  claim, not absolute token counts.
- Bash path extraction is heuristic (documented in analyzer header);
  v1 closes the compound-command gap but substitution stays opaque and
  cwd-relative aliasing is unresolved → numbers remain a floor, higher
  than v0's.
- Same-file re-reads with *narrowing* intent (whole file, then a specific
  range) count as waste; arguable — flagged in output as `narrowing` for
  sensitivity analysis.
- Waste attribution is conservative in v1: a call's result size counts
  only when every classified segment of the call is a repeat.
