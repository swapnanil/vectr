# Compaction-decay probe (brain-memory follow-up to BM-4)

BM-4 scoped "memory survives compaction" out of the measured claims: a single
auto-compaction writes a rich continuation summary that carries task
continuity by itself (H2 finished green with zero post-compaction memory ops).
The open question is **repeated** compaction — the one-chat-per-project usage
pattern (weeks on one conversation; field report: claude-code issue #34556,
59 compactions in 26 days). Under k-fold re-summarization, in-context facts
must re-win selection at every pass (geometric survival), drift semantically
(pass k sees only pass k-1's paraphrase), and compete for a fixed summary
budget. Externalized memory is immune by construction. This probe measures
that delta at k >= 10.

## Design

One long mechanical task on the pinned Apache Camel checkout, sized to force
>= 10 auto-compactions: read 48 large tracked source files fully (~134k lines,
~1.6M tokens of tool results) and append per-file audit entries to
`audit-report.md`. Ten synthetic operational facts (`DECAY-FACTS.md`, fixture
written by the runner) are read once in Phase 0 — epoch 1 — and must be
reproduced verbatim in the final phase, hundreds of turns and many compactions
later. Facts are unguessable tokens (e.g. `QK-4417`, `maroon-otter-19`), so
recall cannot be faked from priors. The prompt forbids subagents (context
offloading would defeat the probe) and forbids copying the facts to disk
before the final phase (that would measure DIY file memory, not retention).

| arm | build | memory surface | facts |
|---|---|---|---|
| **N** | — | none (no MCP, strict-mcp-config, pristine AGENTS.md) | in-context only (Phase 0 read) |
| **M** | main vectr | `vectr init --hooks` + 10 facts seeded as `task`/`high` notes | in-context AND externalized |

`task`/`high` notes fire at session-start including every compact-resume (the
trigger ledger resets at each compaction), so arm M's facts re-inject
deterministically after every compaction — the product mechanism under test.
Injection at these surfaces is index-tier (the note's title line + id, with
recall for expansion); the seeds are authored title-less one-liners so the
derived title — the content first line — carries the full fact value into
every injection. Runner asserts the channel live pre-launch (session-start
hook probe must return a seeded fact).

Model: `--model haiku` — cheap and fast; the probe measures the harness
memory channel, not model capability. Same prompt both arms. One run at a
time.

## Metrics

- `compactions` — count of `compact_boundary` events; < 10 marks the run
  UNDERPOWERED. v4.1 raises k two ways: `CLAUDE_CODE_AUTO_COMPACT_WINDOW=
  100000` — the CLI honors this env var but clamps it to a 100k-token floor
  (verified in the 2.1.211 binary: `Math.max(1e5, value)`), roughly halving
  the stock ~190k threshold — and a corpus extended from 48 to 64 files
  (~160k lines). `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` looked promising in the
  binary but is a NO-OP on this path (N4 compacted at pre_tokens=200556
  with pct=20 set). Honest caveat: the compressed window lowers
  per-compaction content pressure vs production; the phenomenon under test —
  recursive re-summarization (summary k sees only summary k-1's carry) — is
  preserved.
- `final fact score` — how many of the 10 fact tokens appear in
  `audit-report.md`'s closing section (exact token match).
- `survival curve` — for each post-compaction continuation summary in the
  transcript, how many fact tokens it still carries verbatim. This measures
  the decay directly, per compaction, from the summaries themselves.
- Standard hygiene from run-arm.sh: MCP posture from the init event,
  disk-memory audit, wall vs duration_ms, resolved model id.

## Run log

- **decay-N-20260717-005930-INVALID-SHORTCUT**: first N fire. Haiku
  defeated the forcing function legitimately — it generated a shell
  script extracting `wc -l`/`head`/`tail` per file, producing the full
  audit report with only 6 files actually Read and ZERO compactions
  (260s, 23 turns, $0.52). Lesson: any extractable-summary task is
  gameable through shell tools; token flow through context must be
  enforced at the tool level. v2 prompt adds hard tool constraints
  (Read-only file access, no Bash/scripts, Write/Edit-only reporting)
  and the grader now prints per-tool call counts with a
  `Bash>2 => SHORTCUT SUSPECT` line.
- **decay-N-20260717-010533 (v2, INVALID — sampling + fabrication)**:
  tool-compliant (Bash=0) but haiku SAMPLED — 8 of 48 files read, slice
  reads (`limit=50-300`), context peaked at 146k, zero compactions — and
  the report still contained all 48 entries: **40 audit entries
  fabricated from filenames** (e.g. "First member: class
  HL723ConverterLoader" for a file never opened). Two lessons: (a) the
  agent harness itself trains Read-frugality, so a single-prompt "read
  everything" obligation loses to sampling; (b) unverifiable deliverables
  get satisfied cosmetically — a compliance observation that mirrors the
  paper's content-plane thesis at task level. v3 responds with: one
  session driven through 14 sequential `-p --resume` phases (bounded
  per-phase asks; ecologically the one-chat-per-project shape; hooks
  refire per prompt for arm M), a verifiable anti-sampling deliverable
  (every type declaration + line number, graded against regex ground
  truth as declaration-recall), and the fact obligation stated ONLY in
  phase 0 — the final phase asks for the facts without restating them
  (the pre-registered harder probe; the v2 design's standing obligation
  in the live prompt is gone).

- **decay-N-20260717-013455 (v3, N3 — COMPLIANT but endpoint CONTAMINATED
  by harness file restoration; k=2 UNDERPOWERED)**: first compliant fire —
  14/14 phases, 276 turns, $5.25, Read=243/Bash=1, declaration recall 0.91
  over all 48 files (the run's original 0.00 was a grader artifact: the agent
  wrote '## File N: <path>' headers, the parser expected '### N.'). Final
  fact score 10/10 — but NOT via summary survival. The session file shows
  Claude Code (2.1.211) performs post-compaction FILE RESTORATION: after the
  continuation summary it re-attaches recently-read files — large files as
  `compact_file_reference` path pointers, small files with FULL VERBATIM
  CONTENT. DECAY-FACTS.md (16 lines) came back whole right after compaction 1
  (session line 290); the agent's next message recited all 10 facts and then
  rehearsed them in 22 assistant messages across the remaining phases, which
  is why summary 2 carried 10/10. Three findings survive: (1) **summary 1
  dropped all 10 fact values** while asserting "Memorization: Maintained all
  10 DECAY-FACTS" — total drop at k=1, measured, with the summarizer
  confabulating its own memory health; (2) `-p --resume` preserves compaction
  state across calls (cumulative_dropped_tokens accounting, no immediate
  re-compact), so the resume-loop design is sound; (3) the harness already
  ships deterministic post-compaction restoration for FILE state — there is
  no analogous tier for memory (direct evidence for the memory-mode
  proposal, and it silently rescued this run). v4: fixture deleted after
  phase 0 + grader scans the session file for restoration contamination
  (the restore may be cache-sourced); `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20`
  compacts at ~20% of the window (threshold = min(floor(window*pct/100),
  window-13000)) for k≈12 at unchanged corpus and cost; tolerant report
  parser; early-fact-write flag; rehearsal-count metric.

- **decay-N-20260717-022537-INVALID-OVERRIDE-NOOP (v4, N4 — killed at
  phase 4)**: v4 mechanics verified live (fixture deleted after phase 0,
  preflight logged) but `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20` did not move
  the trigger — compaction 1 fired at `pre_tokens=200556` (stock threshold).
  Killed after phase 4 (~$1.5) rather than complete another underpowered
  run. Canary + binary tracing established the working lever:
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is honored but floored at 100k tokens.
  v4.1 = window 100000 + corpus extended 48 → 64 files (next-largest
  non-generated-DSL sources), phase count parametrized from the file list.

- **decay-N-20260717-024049-INVALID-THRASH-GUARD (v4.1, N5 — killed in
  phase 4)**: the window override WORKS — k=13 by phase 3 (vs k=2 across all
  of N3) — but at the 100k window the CLI's autocompact THRASH GUARD aborts
  calls: default 25k-token Reads refill the window within 3 turns of a
  compact, and after 3 consecutive refills the call dies with is_error
  ("Autocompact is thrashing: the context refilled to the limit within 3
  turns of the previous compact, 3 times in a row... Try reading in smaller
  chunks, or use /clear to start fresh."). Phases 3 and 4 both aborted this
  way (~$3.3 spent). The guard message is itself a finding: the harness
  explicitly recognizes repeated compaction as a failure mode and its only
  remedies are smaller reads or throwing the session away. Also observed
  live: post-compaction, the agent Read audit-report.md to re-orient — the
  pointer-only `compact_file_reference` driving exactly the class of
  cross-compaction re-read BM-5 measured. v4.2 =
  `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=8000` (default 25000): refill
  takes ~9 turns, guard can't trip, token throughput and k are unchanged.

- **decay-N-20260717-030703 (v4.2, N6 — VALID survival curve at k=108;
  endpoint contaminated by session-transcript archaeology)**: full completion
  — 18/18 phases, 64 files, 1235 turns, $21.26, 3.8h wall, **k=108
  compactions**, zero thrash aborts (the 8k read cap works), declaration
  recall 0.93 over 64 files, audit compliance OK. **The survival curve is the
  decay measurement: summary 1 carried 0/10 fact tokens — total drop at the
  FIRST compaction, replicating N3 — and 106 of 108 summaries carried 0/10.**
  The only nonzero summaries (30 and 31, both 10/10) directly follow an
  out-of-band re-injection: disoriented about which phase it was in, the
  agent ran grep over ITS OWN session JSONL under `~/.claude/projects/` and
  the matching lines happened to carry the phase-0 fact content back into
  context. A fact set re-injected before summary 30 survived exactly one
  further re-summarization (31) and was gone by 32 — facts must re-win
  selection at every pass and lost by the second. File-restoration
  contamination: none (fixture deletion worked; all 7 restoration events
  carried 0/10 tokens). The 10/10 endpoint is NOT summary survival: in the
  final phase, with the facts absent from context and from every recent
  summary, the agent deliberately mined the session file — grep for
  "operational facts|F0:|F1:..." over the JSONL, then over the persisted
  `tool-results/*.txt` — recovered all 10, stashed them in a scratchpad file
  (`phase18_decay_facts.md`), and wrote the section. Tool compliance
  collapsed: Bash=259 (constraints RESTATED verbatim in every audit-phase
  prompt; first violation only after 15 clean compactions; 38 calls =
  `.claude/projects` archaeology, 150 = corpus/orientation shell, 69 =
  report ops). Under heavy compaction the agent deliberately breaks live
  constraints to reconstruct lost state from whatever persistence exists —
  the thesis observed in vivo: given no sanctioned memory tier, the model
  hand-builds one out of the harness's own disk artifacts (escape #4, after
  shell-extraction, fabrication, and file-restoration). Practical
  consequence: in-context-only is unenforceable on a real harness — every
  harness persists state on disk, and a sterile endpoint would need more
  than tool bans (Read-based archaeology stays open). Reported as: survival
  curve VALID, endpoint contaminated-by-self-rescue.

- **decay-M-20260717-095728 / -102120 (two M preflight aborts, no agent
  launched, ~$0)**: first — the arm's initial 8766 liveness check ran
  immediately after the git reset, whose file churn starved the daemon's
  REST surface past a single 10s curl; fixed by retrying up to 3 min
  (the post-reset quiet loop already tolerated this later in preflight).
  Second (kept as INVALID-TITLE-SHADOWED-INDEX) — a real finding:
  session-start/prompt-submit hook injection is INDEX-TIER by design
  (recall hierarchy: ~100-char title line + note id, "use
  vectr_recall(note_id=N) to expand"; full bodies ship on pre-tool-use
  path triggers and on recall). The seeds' explicit
  `title: "Operational fact FN"` fully shadowed the values out of the
  injection — the hook fired 10/10 but carried no fact token, failing the
  channel assert. Earlier H-arm probes passed only because those seeds
  were title-less: the derived title (content first line) carried the
  matched string. Fix: seeds authored title-less — the ecologically
  normal `vectr_remember` shape — so the value rides the index line into
  every compact-resume injection; verified live before relaunch (a
  single title-less seed's session-start probe carries QK-4417). Product
  observation logged as UPG-INDEX-TIER-TITLE-SHADOWING in tasks.md.

- **decay-M-20260717-191201-INVALID-THRASH-GUARD-M-OVERHEAD (killed in
  phase 2, ~$1)**: first full M fire cleared preflight (title-less seeds
  verified in-channel) but N5's thrash guard returned at the M arm's own
  numbers — phase 1's call aborted after 14 turns. Boundary telemetry:
  both arms compact across the same ~57k-token span (pre ~68-74k, post
  ~9-13k), but M crosses it in ~4 turns vs N6's ~7-9, because the memory
  arm carries fixed context the N arm doesn't: MCP tool schemas loaded
  per resumed call, AGENTS.md vectr guidance, per-prompt hook
  injections. At the 8k read cap that lands on the guard's "refilled
  within 3 turns, 3× in a row" edge. The overhead is the product's real
  context cost — priced by this misfire at roughly three Read-turns per
  compaction cycle. v4.3 (M only): `READ_MAX_TOKENS=6000` → ~9 turns
  per cycle at the same span, guard margin restored, k unchanged (span
  unchanged). The read cap is a harness lever, not task semantics, so
  workload symmetry holds (same files, same prompts, same window).

- **decay-M-20260717-192249 (arm M v4.3, GATED VALID, $28.94)**: k=138
  compactions, 19 phase results, 1489 turns, wall 22267s (includes an
  11.0-min gap 17:37-17:48Z — battery-out hibernation; macOS restored
  all processes; session continuous, no re-sent phase). **Final fact
  score 10/10** with airtight provenance: zero fact-targeted shell
  commands in 379 Bash calls, disk memory empty, no early fact write,
  and the final summary (#138) carried 0/10 fact tokens — the only
  available source was the session-start memory injection at the final
  resume (last injection pair, 10/10 tokens), transcribed into the
  report with Edit. The daemon audit ledger records 157 session-start
  deliveries (138 compact-resume + 19 phase launches), each carrying
  10/10 fact tokens at ~300 tokens per delivery; 139 of them are also
  recorded in the session file as hook_success/hook_additional_context
  pairs (launch-time resume payloads are not recorded as session
  events). Index-tier prompt-submit fires carried 3/10 (9x) and 1/10
  (6x). The grader's "RUN INVALID for the in-context-only
  premise" line is the N-arm attachment rule misfiring on the
  treatment: every fact-carrying attachment is hook_success /
  hook_additional_context (the memory channel); no file-restoration
  attachment carried facts. Summary curve is bimodal with a regime
  change — summaries 1-3 0/10, first 10/10 at #4, 53 of summaries
  4-66 at 10/10, then #67-138 all 0/10 (72 consecutive) despite facts
  present in context every cycle: the summary channel is unreliable
  independent of fact availability, and the endpoint no longer
  depends on it.
  **Workload fidelity is the other half of the story**: declaration
  recall 0.70 vs N6's 0.93 (both re-graded after fixing a grader
  section-binding bug — the original 0.00/SAMPLING-SUSPECT line bound
  sections only via the header line and missed this run's
  "File N: Basename" + "**Path:**" format; N6's number is unchanged
  by the fix). The deficit is task-state decay, not sampling: phases
  6 and 9 were never audited, the agent invented an arithmetically
  self-consistent phase numbering ("Phase 52: Files 203-206" …
  "Phase 57: Files 223-226"), re-audited earlier files under it
  (duplicate Calculator.java entry), hunted a nonexistent
  "Files 249-308" spec with 9 vectr_recall calls, 37 orientation
  greps of its own session file, and 3 forbidden Explore subagent
  calls, and lost the true phase-15/16 tail (the last audit phase was
  thrash-guard-killed at 24 turns, runner line "phase 16: claude
  exit 1"). Summaries propagated the false task model across
  compactions. Read: the memory tier protected exactly what was
  seeded (10 facts, perfect at k=138); the task state nobody seeded
  decayed catastrophically; and the guard-forced 6k read cap is an
  M-only operating point that carries an asterisk on the 0.70-vs-0.93
  comparison.

- **Post-review forensics addendum (2026-07-18)**: independent
  re-verification for the paper corrected two numbers in the entry
  above (logged 2026-07-17 as "~95 injection pairs" and "#112-138 all
  0/10"; verified against the daemon ledger and grade curve as
  157 deliveries / 139 session-recorded pairs, and #67-138 — 72
  consecutive) and added three findings: (a) of M's 321 Bash tool
  results exactly one — an orientation grep for phase-10 task state
  over its own session file, turn 2760 — incidentally echoed fact
  tokens back into context, 60+ boundaries before the endpoint;
  (b) the spec-hunt left the corpus: the agent twice Read the memory
  system's own design doc off the operator's disk (the path first
  appears in the agent's own thinking with no recorded source; the
  file carries no fact tokens; both reads are in the audit ledger);
  (c) the format-robust regrade is now published per-run as
  `decay-grade-v4-regrade.txt`, and sanitized daemon audit-log
  excerpts as `daemon-audit-excerpt.log` (H1/H2/V/decay-M; the
  CS-era daemon log was not retained). H-run trigger evaluations:
  40 (H1) / 35 (H2) — one in-run gotcha fire each plus the harness's
  pre-launch probe; zero false alarms.

## Reading the result

- N's survival curve is the decay measurement: fact tokens present in summary
  k, as k grows. The final score is the endpoint.
- M's final score should be 10/10 with the mechanism visible in the daemon
  audit log (session-start fires at each compact-resume); its survival curve
  measures how much the summarizer bothers to carry facts that are also
  externally guaranteed.
- If N scores 10/10 at k >= 10, the honest conclusion is that current
  summarizers protect explicitly-tasked facts well and the decay claim needs
  a harder probe (facts NOT restated as a final-phase obligation in the
  prompt); report it as such, do not shop for a different metric.
- Any product defect surfaced (hook miss, injection failure) => UPG item
  first; re-run after fix if it invalidated the arm.
