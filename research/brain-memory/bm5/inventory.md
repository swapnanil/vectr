# BM-5 transcript inventory

Raw material for the re-exploration measurement. All paths relative to
`tools/vectr/docs/proactive-gate/results/`. Updated 2026-07-13; append new
gate runs as they complete.

| Transcript | Arm | Task | Status | Notes |
|---|---|---|---|---|
| B-20260712-121951 | B (MCP only) | task 1 stream-reverse | graded | first B run |
| B-20260712-165508 | B (MCP only) | task 1 | graded PASS, 938s | B rerun of record |
| A-20260712-180615-INVALID-POSTURE | A | task 1 | INVALID | posture fail; exclude from headline, usable for analyzer testing only |
| A-20260712-185136 | A (vanilla) | task 1 | graded PASS, 3445s (sleep-corrected) | 142 turns; heaviest run |
| A-20260712-224905 | A (vanilla) | task 1 | graded PASS, 1519s | model/reifier-layer solution |
| A-20260712-233814 | A (vanilla) | task 1 | in flight | add when graded |
| C-20260712-124925 | C (proxy) | task 1 | invalid for value (0/71 injected — pre-fix daemon) | still valid for re-exploration analysis |
| C-20260713-005502 | C (proxy) | task 1 | added 2026-07-14 (grading status: see gate log) | usable for re-exploration analysis |
| C-20260713-015112 | C (proxy) | task 1 | added 2026-07-14 (grading status: see gate log) | heaviest C re-explorer (R1=23, share 29.1%) |
| CS-20260713-035443 | CS (seeded) | task 1 | added 2026-07-14; seeds-at-launch.jsonl present | 2 compactions; all 6 R1 cross-compaction |
| phase1-* | — | trivial | transparency probes | too small; exclude |

Smoke-test results (analyzer v0, 2026-07-13): compaction detection verified
(1 per graded run, matches grading); C-124925 shows R1=3 all cross-compaction
(R2=3) — post-compaction recovery waste, the class thesis §6 predicts.

FULL-INVENTORY RESULTS (analyzer v1, 2026-07-14): see
`measurement-note.md` (headline: 61 R1 / 24 R2 — 39% of re-reads are
cross-compaction / ~78.4k wasted result tokens / pooled share 10.9%,
concentrated in compaction-crossing runs). Per-run JSON: v1-results.json;
v0 baseline preserved in v0-baseline.json. The v0 compound-Bash floor is
closed (12 hidden reads recovered); remaining floors in definition.md
honesty ledger. R4 cross-session re-derivation still needs paired-session
data.
