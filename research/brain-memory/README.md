# brain-memory — the paper and its measurements

**Start here:** [`delivery-not-storage.md`](delivery-not-storage.md) —
*Delivery, Not Storage: Cue-Anchored Working Memory as a Harness
Property for Coding Agents.*

The paper's argument in one paragraph: coding agents persist knowledge
only as documents — deliberately written, deliberately retrieved — and
models measurably fail to do the retrieving (zero memory reads in 114
turns even with a store pre-seeded with task-relevant notes). The
missing tier is cue-anchored working memory delivered by the harness
itself: memories carrying deterministic trigger conditions ({path,
symbol, semantic, event, temporal}), injected at the moments that
structure an agent's epistemic life. Evidence: a six-arm controlled
matrix (voluntary vs proxy-injected vs hook-injected delivery of the
same knowledge), a re-exploration-waste measurement (39% of re-reads
re-purchase content already paid for before a compaction boundary),
and a forced-compaction decay probe — facts held only in conversation
vanish from the first continuation summary (106/108 summaries carry
none; the deprived agent greps its own session files off disk to
rebuild them), while the same facts hook-injected from a store arrive
intact after 138 compactions.

## Contents

- [`delivery-not-storage.md`](delivery-not-storage.md) — the paper (abstract,
  design theory, mechanism, evaluation §5, related work, threats to
  validity, full references).
- [`bm5/`](bm5/) — the re-exploration-waste measurement: metric
  definition, analyzer, per-run data ([`bm5/README.md`](bm5/README.md)).

Run archives, protocols, graders, and per-run posture assertions for
every experiment are in
[`../proactive-gate/`](../proactive-gate/README.md).
