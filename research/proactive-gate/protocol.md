# Proactive-context merge gate — protocol of record

Gate for merging `feature/experimental-godMode` ("Proactive context":
`vectr proxy` on 8785, deterministic injection, artifact + exact-match response
caches) into vectr `main`. Directive: the branch never merges without this gate
passing and explicit user go-ahead.

Branch state 2026-07-12: REBASED onto main (user-directed; rebase, not merge)
and force-pushed — tip `569c480`, 15 ahead / 0 behind origin/main, linear
history. Suite on rebased state: 2423 pass (+2 known worktree-artifact ragas
failures, pre-existing). Standing instruction: rebase on main before every
future push of this branch. Eval daemon+proxy rebuilt from the rebased tip;
injection probe re-verified end-to-end (1 item, master switch off).
Commit map after rebase: 0d7e691→741924f, e19c703→282f84f, e2ae54a→569c480.

Design agreed with user 2026-07-12: Sonnet arms, all required permissions,
extremely complex task on the Apache Camel corpus.

## Environment (prepared, zero quota spent)

- Corpus: `/home/user/Documents/fde/vectr/tmp/poc-camel` — Apache Camel (Java),
  pinned at `a543dc64`. Build needs JDK 21+ (enforcer rule; JDK-21-only classes
  silently omitted on 17) — Temurin 21.0.11 installed user-local
  (~/Library/Java/JavaVirtualMachines) 2026-07-12. Maven 3.9.15, with
  MAVEN_ARGS pointing at the harness's clean maven-settings.xml (user-level
  settings mirror to an unreachable private Nexus).
- Daemon: godMode-build vectr (worktree venv at
  `<scratchpad>/vectr-proxy-dev/.venv/bin/vectr`) serving poc-camel on **port 8766**.
  The prior 3.1 GB index was NOT reused: its `embed_model_stamp.json` was
  missing (built before the UPG-EMBEDDER-SWAP-GRANITE stamp mechanism), and
  vectr correctly treats a missing stamp as a mismatch — collections dropped,
  full rebuild (168,972 chunks, ~2 h) with the build's default embedder
  `ibm-granite/granite-embedding-english-r2`. The persisted symbol graph was
  ALSO stale (schema_version 8, embed_model arctic — build wants
  SYMBOL_SCHEMA_VERSION 10 + granite); by design the daemon serves it during
  the rebuild window and always full-rebuilds it after the vector pass.
  GATE PRECONDITION before ANY arm runs: `/v1/status` on 8766 shows
  `last_indexed != never` + stable `total_chunks`, AND
  `sqlite3 ~/.cache/vectr/0192f96071a8/symbol_graph.sqlite
  "select value from graph_meta where key in ('schema_version','embed_model')"`
  returns 10 + ibm-granite/granite-embedding-english-r2.
- Proxy (arm C + phase 1 only): `vectr proxy` on **8785** from the same venv.
- Untouchable: 8765 (session memory), 8767 (work memory — not ours).

## Phase 1 — transparency gate (run before the A/B; cheap)

Question: does the proxy change ANY observable behavior when injection is off?

Two runs of the same trivial fixed task (e.g. "read pom.xml and name the camel
version"), same model, same workspace:
1. direct (no `ANTHROPIC_BASE_URL`)
2. through proxy with injection disabled (config off), `ANTHROPIC_BASE_URL=http://127.0.0.1:8785`

PASS requires: both complete; tool calls parse; streaming works; zero proxy 5xx;
zero fail-open events logged; auth passes through untouched (subscription OAuth
header — never logged); prompt-cache behavior comparable (cache_read tokens
nonzero on 2nd turn in both). Any divergence = STOP, fix, rerun.

## Phase 2 — value A/B

Isolates the branch's actual contribution. Both arms use the SAME godMode-build
daemon, same index, same MCP tools, same prompt, same clean checkout:

- **Arm B** — vectr MCP tools only (agent-initiated recall; today's shipped posture).
- **Arm C** — same, plus `ANTHROPIC_BASE_URL=http://127.0.0.1:8785` → proactive
  injection + org-wide caches.

Vanilla (no vectr) arm A is optional if a spare quota window exists; prior
benchmarks already cover it.

Pacing (revised 2026-07-12, user on Claude Max): quota-window spacing is no
longer required — run arms back-to-back, but strictly ONE at a time (shared
daemon + laptop; concurrent heavy jobs contaminate wall-clock and can alter
agent behavior). Matrix of record: task 1 = 3× arm A (vanilla) + 3× arm C;
task 2 (timebox) = 1× A2 + 1× C2; all `--model sonnet`. Arm B is retired
(its single run showed the MCP-only posture behaves vanilla: 0 vectr calls);
treat deltas under ~15% as noise. Lesson encoded from the 2026-06-20 eval:
short/easy tasks tie — task 2 exists because sonnet-5 made task 1 easy.

Seeded-C addendum (user-approved 2026-07-12): measured voluntary note-taking
is ~zero across all runs (0/1/0/0 notes), so cold C arms have nothing to
inject — they test transparency/no-harm, not value. AFTER the base matrix,
run 1–2 **C-seeded** runs: pre-load the 8766 daemon with a realistic
session-1 note set, then run the same task-1 prompt through the proxy.
Honesty constraint on the seed set: notes may contain only facts a prior
exploration session would plausibly have stored (file/symbol locations,
semantics like the successor/predecessor relation, the guard site) — NEVER
the solution plan or diff. Derive them from what real exploration
transcripts actually surfaced. Value read: seeded-C vs cold-C/A on
re-discovery tool calls, tokens, wall-clock.

## The task (identical prompt both arms — `task-prompt.md`)

Bring the Resequencer EIP's `reverse` option to **stream mode** in Apache Camel.
Today `reverse` exists only in batch mode; `.stream().reverse()` throws
`IllegalStateException` (ResequenceDefinition.java:264). A correct fix is
genuinely cross-cutting: model config (`StreamResequencerConfig` + `@Metadata`),
fluent-DSL guard removal, `ResequenceReifier.createStreamResequencer` comparator
wiring, and the subtle part — the stream engine's successor/predecessor
semantics (`DefaultExchangeComparator` defines successor as seqno+1; reversed
ordering must invert that relation, not just `compare()`). Forces sustained
exploration across camel-core-model / -reifier / -processor / camel-support.

Acceptance (objective):
1. `tests/StreamResequencerReverseGateTest.java` (harness-provided, copied into
   `core/camel-core/src/test/java/org/apache/camel/processor/`) passes:
   `mvn -pl core/camel-core test -Dtest=StreamResequencerReverseGateTest`
2. Gate test file byte-unmodified (sha256 recorded by runner).
3. Regression set green: `mvn -pl core/camel-core test -Dtest='*Resequencer*'`
4. Diff scope review: changes confined to resequencer-related files (manual).

## Commands (pinned)

- Model: `--model sonnet` — MUST verify the resolved model id in the transcript
  is the Sonnet 4.6 line (2026-06 lesson: `--model opus` once resolved to 4.7).
- Permissions: `--dangerously-skip-permissions` (runs confined to poc-camel;
  no prompts mid-run).
- Launch authority: user authorized autonomous firing 2026-07-12 ("once camel
  completes embedding, start the eval, dont wait for my go ahead") — the
  orchestrator fires `run-arm.sh` once the index precondition passes. Arms
  still run one per 5-hour quota window.

One-time prep before phase 1 (warms the maven cache; ~10–20 min first run):
`cd poc-camel && mvn -q -pl core/camel-core -am install -DskipTests`

## Per-arm reset (runner enforces)

1. `git reset --hard a543dc64` + `git clean -fd` excluding vectr-generated
   editor configs (`.mcp.json`, `.cursor/`, `.vscode/`), then regenerate configs
   via the godMode-build vectr so AGENTS.md guidance is identical both arms.
2. Wipe working memory: notes count on 8766 must be 0 at arm start
   (`vectr forget` all + `/v1/status` check) — equal memory starting state.
3. Copy the gate test in; record its sha256.
4. C arms only: start proxy (no `--no-inject`), verify
   `/__vectr_proxy/health`, and rely on run-arm.sh's seeded-probe assert for
   the end-to-end injection path. History: run C-20260712-124925 went 0/71
   injected because `proactive.enabled` (default false) used to gate the
   proxy channel too and the daemon lacked `VECTR_PROACTIVE=true`; branch
   commit e19c703 makes launching the proxy the consent for the proxy channel
   (hooks still need the master switch), so a post-fix daemon needs no env.
   The probe stays as the authoritative end-to-end check either way.

## Metrics per arm

- Outcome: acceptance 1–4 above (pass/fail each).
- Cost: wall-clock, turns, tool calls, tokens in/out (transcript JSON), number
  of compactions observed.
- Adoption: vectr tool calls made, notes stored/recalled (8766 audit/status).
- Arm C proxy: injections served, artifact-cache hits, response-cache hits,
  fail-open events (must be 0), 5xx (must be 0).

## Abort criteria

Daemon or proxy dies mid-arm; agent stalls >20 min without tool calls; maven
environment failure unrelated to the change. An aborted arm is discarded and
rerun in a fresh window, not patched mid-flight.

## Verdict rule

- Phase 1 fails → branch blocked on transparency bugs regardless of value.
- Phase 2: arm C must match arm B on acceptance AND show a clear cost/adoption
  win (fewer re-discovery tool calls / tokens / wall-clock) to justify merging
  the proxy surface. Tie or regression → hold the branch, log findings as UPGs.

## After a PASS verdict (merge sequence, user-mandated 2026-07-12)

A gate pass is necessary but NOT sufficient. In order:
1. Bring main into the branch — DONE 2026-07-12 via rebase (user-directed):
   tip 569c480, 15 ahead / 0 behind, suite green on rebased state. If main
   moves again before the verdict, rebase again and re-run the suite.
2. Present gate results + rebased-state test results to the user.
3. Merge ONLY on explicit user approval. No approval, no merge.

## Non-graded launches (run log; entries logged post-hoc from artifacts, 2026-07-18)

- **A-20260712-180615-INVALID-POSTURE**: first A launch, invalidated at
  the launch-posture gate (suffix assigned at the time). The retained
  surface is self-contradicting for a vanilla arm — `proxy-status.json`
  and `daemon-status.json` present alongside the preflight's "no vectr
  surface" assertion. Superseded the same evening by the clean
  A-20260712-185136. Excluded from every pooled number in the paper.
- The three C preflight aborts (C-20260713-000926 store-clear defect;
  C-20260713-011040 and C-20260713-013327 REST-starvation availability
  bug) each carry their diagnosis in-directory as `abort-note.txt`;
  both defects were logged and fixed as product work.
