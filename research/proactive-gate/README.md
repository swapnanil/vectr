# proactive-gate — evaluation harness and run archives

Two experiments live here, sharing one corpus (a pinned Apache Camel
checkout) and one honesty regime: every launch's posture is asserted
and archived, every failed or invalidated launch is retained and
diagnosed in the protocol run logs, and mechanism claims are gated on
daemon audit logs rather than agent transcripts.

Headline results and full analysis:
[`../brain-memory/bm6-paper-final.md`](../brain-memory/bm6-paper-final.md).

## Experiment 1 — controlled matrix (BM-4)

One naturalistic feature task (implement the `reverse` option for
Camel's stream-mode Resequencer EIP), gated by a fingerprinted
acceptance test that must pass byte-unmodified, run under six arms
that vary only the memory surface and delivery channel:

| arm | store | delivery |
|---|---|---|
| A | none | — |
| B | tools + guidance | voluntary |
| C | tools + proxy, cold store | injection (nothing to inject) |
| CS | seeded | API-proxy injection |
| H | seeded | native lifecycle hooks |
| V | seeded | voluntary (hooks stripped; control for H) |

Files: [`protocol.md`](protocol.md) (matrix protocol),
[`bm4-protocol.md`](bm4-protocol.md) (native-channel arms),
[`matrix-verdict.md`](matrix-verdict.md) and the per-arm
`arm-*-verdict.md` files (gating decisions),
[`task-prompt.md`](task-prompt.md) / [`task2-prompt.md`](task2-prompt.md)
(the exact prompts), [`run-arm.sh`](run-arm.sh) (launcher),
`seeds-task1*.jsonl` (the seeded notes), [`tests/`](tests/) (the
acceptance gate tests plus SHA), and
[`timebox-reference-impl.patch`](timebox-reference-impl.patch)
(reference implementation used only for grading).

## Experiment 2 — forced-compaction decay probe (arms N/M)

Ten synthetic operational facts are presented once, their fixture file
deleted, and a 64-file audit workload drives the session through
continuous auto-compaction. Arm N has no memory tier; arm M seeds the
same facts as store notes, hook-injected at every compact-resume.
Grading is token-level fact presence in every continuation summary
(the survival curve) and in the final report, plus provenance
forensics over the full transcript.

Files: [`decay-protocol.md`](decay-protocol.md) (design, invariants,
and the complete run log — including every invalidated launch and its
diagnosis), [`decay-probe.sh`](decay-probe.sh) (harness + grader),
[`decay-files.txt`](decay-files.txt) (audit corpus),
[`decay-seeds.jsonl`](decay-seeds.jsonl) (the ten seeds, title-less by
design — see the run log's INVALID-TITLE-SHADOWED-INDEX entry).

Gated runs: [`results/decay-N-20260717-030703`](results/decay-N-20260717-030703)
(k=108) and [`results/decay-M-20260717-192249`](results/decay-M-20260717-192249)
(k=138).

## Reading a results directory

`results/<ARM>-<YYYYMMDD>-<HHMMSS>[-<INVALID|ABORTED>-<reason>]/`

A suffix means the launch failed preflight or was invalidated at
gating; it is kept because the diagnosis is part of the record (each
one is explained in the protocol run logs). Inside a run directory:

- `transcript.jsonl(.gz)` — the agent product's stream-json output for
  every phase call.
- `session.jsonl(.gz)` — the harness session file (the full
  conversation record, including compaction boundaries and
  continuation summaries).
- `grade.txt` / `decay-grade.txt` — grader output: scores, survival
  curve, tool compliance, provenance checks.
- `*.at-launch` (`AGENTS.md`, `mcp.json`, `settings.json`),
  `guidance-state.txt`, `seeds-at-launch.jsonl`, `gate-test.sha256` /
  `decay-facts.sha256`, `daemon-status.json`, `hook-probe-*.json` —
  the launch-posture assertions.
- `preflight.txt`, `phases.txt`, `wall.txt`, `sleep-events.txt`,
  `stderr.log` — runner bookkeeping.
- `audit-report.md` (decay runs) — the artifact the agent produced;
  the final `## Operational Facts` section is the endpoint measurement.
- `disk-memory-at-end/` (decay-M) — contents of the native memory
  directory at run end (empty = no disk-memory escape).

## Provenance notes

Transcripts and session files over 1 MB are gzip-compressed
(`gunzip -k` to restore). Local paths and one corporate Maven-mirror
hostname were rewritten for privacy; the exact rewrite map is in
[`../README.md`](../README.md). `maven-settings.xml` (a mirror config
used only to speed dependency resolution) is excluded; builds
reproduce with any standard Maven mirror.
