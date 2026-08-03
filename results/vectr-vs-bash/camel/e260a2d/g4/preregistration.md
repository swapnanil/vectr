# G4 — Pre-registered A/B: seeded operational memory vs. the single-module false-pass loop

Status: PRE-REGISTERED (sentinel, 2026-07-22). **Frozen before any live session.** After the
first live session starts, nothing in §3–§6 may change; any protocol deviation is reported in
the results readout as a deviation, never silently absorbed. Metrics and thresholds are never
softened after the fact (design-doc §7 rule).

Parent: `memoization-l1-capture-design.md` §7 (gate G4). Baseline evidence: the T2 readout
(`benchmarks/vs_bash/tier1/README.md`, "T2 results") — across 8 sessions and 25 maven
invocations, **zero** observed BUILD FAILUREs: every invocation was single-module
(false-passing against installed `~/.m2` artifacts on cross-module tasks) and piped/quieted.
No agent ever had an honest red/green loop; the driver's reactor-paired gate was the only
honest referee.

---

## 1. Hypothesis

Delivering one operational-kind note carrying the reactor-gate knowledge — via the shipped
serving surfaces (PreToolUse command-family lane + prompt-time semantic) — changes agent
verification behavior: the memory arm performs honest (reactor-paired or equivalent)
verification that the control arm does not.

This is an **adoption/serving** experiment, not a retrieval one. Injected text can be ignored
(T1c: available ≠ used; T2: shipped artifacts produced only shallow usage), so measuring
whether delivered memory changes behavior is a real question, not circularity.

Explicitly NOT claimed at this N: gate pass-rate improvement (underpowered), efficiency deltas
(reported descriptively only).

## 2. Task (frozen)

**T2-02** from `benchmarks/vs_bash/tier1/tasks_t2_camel.jsonl`, verbatim — the simple-language
logical-operator parser bug. `fix_sha 55d5f843…`, `gate_modules
core/camel-core-languages,core/camel-core`, `gate_test SimplePredicateParserLogicalTest`.

Why this task:
- The trap is live: the upstream fix touches only `core/camel-core-languages`
  (`SimplePredicateParser.java`, `ast/LogicalExpression.java`) while the gate test lives in
  `core/camel-core` (verified against the fix commit, 2026-07-22). A single-module
  `-pl core/camel-core` test run compiles against the installed `~/.m2`
  camel-core-languages artifact and never sees the agent's fix.
- It isolates verification behavior from exploration difficulty: in the original T2 run both
  arms produced a correct fix on this task (the bash arm byte-identical to upstream); T2-01 by
  contrast conflated with max-turns exhaustion.

## 3. Design (frozen)

- **Arms**: both arms are run_t2.py's **vectr-as-shipped** configuration (on-disk init
  artifacts + hooks + `.mcp.json`, live daemon on port 8800, `--strict-mcp-config`). The ONLY
  difference:
  - **Arm M (memory)**: exactly one note seeded into the bench workspace before the session
    (§4); `notes_count == 1` verified pre-session.
  - **Arm C (control)**: zero notes; `notes_count == 0` verified pre-session.
- **N = 3 sessions per arm, 6 total.** Fixed in advance; no optional stopping, no extension,
  no dropping sessions to improve a readout.
- **Order**: deterministic alternation M1, C1, M2, C2, M3, C3 (spreads time-of-day and quota
  effects; no randomization).
- **Model/composition**: `--model sonnet`, `--max-turns 40`, the same preamble, disallowed
  tools, JAVA_HOME/MAVEN_ARGS session env, and command composition as `run_t2.py` (T2
  conventions unchanged). One vectr SHA for all 6 sessions (main, ≥ `2fec1c4`); no vectr
  code change between sessions.
- **Between sessions**: fixture reset to seeded-bug state (run_t2.py's `reset_fixture` +
  seed patch re-apply), ALL notes cleared from the bench workspace and verified (then arm-M
  seeding where due). Episode/arc stores are quarantined (injection-inert) and are NOT
  cleared; their counts are recorded before/after each session.
- **Quota**: sessions run one at a time, sentinel-supervised, batched as the shared 5-hour
  window allows; the experiment may span days. N stays 6 regardless.

## 4. Seeded memory (frozen, verbatim)

Seeded via the bench daemon's remember surface (REST `POST /v1/remember` or MCP
`vectr_remember`), exactly:

- `kind="operational"`, `priority="high"`, `title="Maven multi-module verification"`,
  `tags=["maven","build","verification"]`, provenance default (`agent`), scope default,
  no anchors.
- `triggers=[{"event": "prompt-submit", "semantic": true}, {"command": "*mvn*"}]`
  (explicit bundle: prompt-time semantic + the PreToolUse command-family lane; `*mvn*`
  fnmatch-matches every normalized maven verb — `mvn test`, `./mvnw test`, `./mvnw install`, …).
- `content` (generic operational knowledge; deliberately contains NO task specifics, NO gate
  module names, NO hint about the bug):

  > In this multi-module Maven repo, a single-module test run (`-pl <module>`, or cd into the
  > module) compiles against previously installed artifacts from `~/.m2` — a change made in
  > another module is invisible to it, so a green single-module run does NOT verify a
  > cross-module change. To honestly verify a change in module A with a test in module B, run
  > from the repo root: `./mvnw -pl <moduleA>,<moduleB> test -Dtest=<TestClass>` (list BOTH
  > modules so A is rebuilt from source in the same reactor), or select module B with `-am`.
  > And read the result: check the exit status or the final BUILD SUCCESS/FAILURE line —
  > don't discard it behind `-q` piped into `tail`.

**Plumbing pre-check (before the first live session, quota-free, run once):** seed the note in
a scratch pass, feed a synthetic PreToolUse Bash payload (`./mvnw test`, synthetic
session_id) through `vectr hook pre-tool-use`, and confirm the note text is emitted; confirm
prompt-time behavior the same way; then clear notes. This verifies the arm mechanism exists —
it is arm construction, not a measurement, and is never repeated mid-experiment.

## 5. Outcomes and metrics (frozen definitions)

All metrics are computed by a deterministic transcript parser over the recorded stream-json
sessions (agent-issued `Bash` `tool_input.command` strings and Edit/Write paths — tool-call
data, R5-sanctioned; never task-prompt content). The parser is written and reviewed against
this section before the first live session; after that it may only be fixed for crashes, with
the fix documented — all raw transcripts are retained so every metric is re-derivable.

Definitions. A **maven invocation** is any Bash command invoking `mvn`/`mvnw` (any path
prefix, transparent wrappers like `timeout`/`env` stripped). Its **module scope** = the
`-pl`/`--projects` value(s), presence of `-am`/`--also-make`, and effective cwd (a `cd X &&`
prefix or session cwd). A **test-phase** invocation runs goal `test` or later
(`verify`/`install`/`package` without `-DskipTests`). It **covers the gate test** when
`-Dtest` is absent or its value (surefire glob semantics) matches
`SimplePredicateParserLogicalTest`.

**Primary outcome (binary per session): honest-verification event** — the session contains at
least one of:

- (a) a test-phase invocation covering the gate test with `-pl` including BOTH
  `core/camel-core-languages` and `core/camel-core` (any order);
- (b) same, with `-pl` including `core/camel-core` AND `-am` present;
- (c) same, with no module selection, run from the repo root (full reactor);
- (d) two-step: an invocation that builds+installs `core/camel-core-languages` from source
  (`install` reaching that module) and completes successfully, followed later in the session
  by a test-phase invocation of `core/camel-core` covering the gate test;
- (e) self-authored honest check: a test-phase invocation of a module that both (i) contains
  the agent's own source-fix edits and (ii) contains a test file the agent created or edited
  this session, with that module compiled from source (single-module allowed — same module).

**Secondary metrics (reported, no thresholds):**

1. False-pass events: count of test-phase invocations of `core/camel-core` with no `-am`, no
   `core/camel-core-languages` in scope, and no prior successful fresh install of it — the
   trap firing.
2. Honest red observed: any maven invocation whose recorded output shows a genuine test
   failure or BUILD FAILURE (the driver's reporting markers).
3. Driver `gate_pre` / `gate_post` (unchanged T2 gate: reactor-paired, driver-side, exit code
   only).
4. Arm-M delivery integrity: whether the seeded note's text appears injected in the session
   transcript, and via which surface (command lane vs. prompt-time).
5. Piped/quieted fraction of maven invocations (`-q`, output piped) — descriptive.
6. Tokens in/out, turns, wall seconds, vectr tool-call counts — descriptive.

## 6. Decision rule (frozen)

- **SUPPORTED** iff BOTH: (i) ≥ 2 of 3 Arm-M sessions contain an honest-verification event,
  AND (ii) (#M honest) − (#C honest) ≥ 2.
- **Anything else → NOT SUPPORTED.** No third bucket, no "directionally positive".
- Intent-to-treat: an Arm-M session where the note was not delivered still counts against the
  arm (delivery is part of the product). Delivery-adjusted numbers may be reported alongside,
  clearly labeled, but the decision uses as-run sessions.
- Exclusions: ONLY external infrastructure kills (API stall with no completed assistant turn,
  daemon crash, machine sleep) — the session is discarded and rerun in the same arm slot.
  Max-turns exhaustion is an outcome, never an exclusion (counts as no-honest-verification
  unless one occurred before exhaustion).

## 7. Execution protocol

1. Build the driver extension (`run_t2.py` G4 mode or `run_g4.py` reusing its machinery):
   arms memory/control per §3, per-session note clear/seed/verify, alternation order, §5
   parser. Same honesty rules as the T2 driver (no query-content branching; identical flags
   and templates for both arms; subagents only ever `--dry-run` — live runs are
   sentinel-personal).
2. Plumbing pre-check (§4). Dry-run of all 6 composed commands reviewed by sentinel.
3. Live sessions in §3 order, one at a time, fixture verified clean before each.
4. Readout: per-session metric table + decision-rule application, published to
   `results/vectr-vs-bash/camel/<vectr-sha>/g4/`; summary lands in the tier1 README beside
   the T1c/T2 readouts. Losses and NOT SUPPORTED are reported exactly as they land.

The L3 distiller is NOT involved: Arm M's note is seeded manually per §4. (G4 tests the
serving half; the distiller has its own design and gates.)
