# BM-4 — native-channel validation arms (protocol addendum)

Extends `protocol.md` (the proxy-gate pilot, matrix CLOSED per
`matrix-verdict.md`) into BM-4's controlled evaluation
(`../brain-memory/brain-memory-tasks.md` §BM-4). Authorized by the user
2026-07-16: run after the reviewer/coder loop completed clean of memory-mode
blockers (it did — wave-4 merged at vectr main `18311ed`, CI green), before
the memory-mode proposal/PR.

## What changed since the pilot, and why these arms

The pilot proved no-harm + mechanism + "injection begets engagement" using the
**godMode proxy** (deterministic injection spliced into API traffic). What the
memory-mode proposal actually ships is the **native channel** on vectr main:
Claude Code hooks (`vectr init --hooks` → SessionStart / UserPromptSubmit /
PreToolUse / PreCompact → `vectr hook <event>`) plus the per-memory trigger
engine (BM-2 design; landed waves 1/2a/2b + fix waves). That channel has never
run inside an arm. BM-4 tier 1 closes exactly that gap, on the identical task,
corpus, model, and prompt as the pilot — so every pilot run stays reusable as
comparison data (explore-once-reuse; quota rule).

## Arms

| arm | build | delivery | store at start |
|---|---|---|---|
| **H** | main (global editable) | native hooks + trigger engine; no proxy | seeded, `seeds-task1-triggered.jsonl` |
| **V** | main (global editable) | none — MCP tools only, voluntary recall | seeded, same file |
| (pilot A) | — | none, no vectr at all | empty |
| (pilot B) | godMode | MCP tools only, voluntary | empty |
| (pilot CS) | godMode | proxy injection | seeded, `seeds-task1.jsonl` |

Seeds: `seeds-task1-triggered.jsonl` is byte-identical in note CONTENT to the
pilot's `seeds-task1.jsonl` (honesty constraint unchanged: facts a prior
exploration session would plausibly store, never plan/diff). The only delta is
delivery metadata: the gotcha note carries an explicit
`triggers: [{"path": "**/ResequenceDefinition.java"}]` so the cue-anchored
P-primitive fires at PreToolUse on the anchored file. The findings keep their
kind-default delivery (prompt-submit semantic recall over the
`hooks.min_similarity` floor). One seed set, three channels exercised.

## Comparisons this buys

1. **H vs V** — the delivery channel, with knowledge held constant. This is
   the memory-mode claim itself: voluntary access to the same notes vs
   deterministic injection of them.
2. **H vs pilot CS** — native hook channel vs proxy channel, seeded both
   sides: does the shipped mechanism reproduce the pilot's injection effect?
3. **H/V vs pilot A (n=3)** — end-to-end value against vanilla on the same
   task.
4. **V vs pilot B** — seeded-voluntary vs cold-voluntary: does having notes
   in the store change voluntary adoption at all? (Pilot's durable finding
   predicts: no.)

## Run plan (headline-first, quota-aware, one at a time)

2× H, then 1× V. Extend only if the spread demands it and quota allows.
`--model sonnet` pinned (verify resolved id in the transcript). Arms never run
concurrently with each other or any other quota consumer.

## Preflight (before the first arm; runner re-asserts per arm)

1. Daemon: `cd poc-camel && vectr start --port 8766` from the GLOBAL main
   build. Index must REUSE the pilot's cache (`~/.cache/vectr/0192f96071a8`,
   ~169k chunks; no schema/embedder change since — verify `last_indexed !=
   never`, stable `total_chunks`, no rebuild in progress).
2. Runner asserts per arm: hooks present (H) / absent (V) in
   `.claude/settings.json`; notes wiped then seeded to exactly 4; gate test
   fingerprinted; MCP posture proven from the transcript init event.
3. H-only live probes through the REAL hook binary before launch:
   prompt-submit must inject the seeded findings; pre-tool-use on
   `ResequenceDefinition.java` must fire the path-triggered gotcha. Either
   probe empty → ABORT (the arm would be measuring a dead channel).

## Metrics

Same as `protocol.md` (acceptance 1–4, transcript `duration_ms`/turns/cost/
tool calls/greps, vectr adoption ops, disk-memory audit) plus, for H: injected
contexts observed in the transcript's hook events, and which channel delivered
them (boot / prompt-submit / pre-tool-use). False-alarm injections (notes
injected on turns where they were irrelevant) counted by transcript read.

## Verdict rule

- H must match pilot acceptance (gate test + regression green, gate test
  byte-unmodified) — the native channel may not COST correctness.
- Mechanism: ≥1 pre-tool-use gotcha fire during the real run (not just the
  probe) for the cue-anchored claim; prompt-submit injections observed for
  the floor claim.
- Value read (directional, honestly priced at this n): H vs V on re-discovery
  tool calls / turns / cost; consistency with the pilot's CS-vs-A direction.
- Any product defect surfaced → UPG item first (bugs are product tasks),
  rerun after fix if the defect invalidated the arm.

## Non-graded launches (run log; entries logged post-hoc from artifacts, 2026-07-18)

- **H-20260716-102250-ABORTED-PREFLIGHT**: aborted at the hook-probe
  preflight step — `preflight.txt` ends after seeding with no
  "native channel live" assertion; `hook-probe-prompt.json` retained;
  no transcript, no agent run, no quota spent.
- **H-20260716-102744-ABORTED-USERPAUSE**: preflight passed (both hook
  probes live) and the agent launched; the operator paused the run
  shortly after launch. Partial transcript retained, never graded; the
  arm re-ran clean the same day as H-20260716-225420.
