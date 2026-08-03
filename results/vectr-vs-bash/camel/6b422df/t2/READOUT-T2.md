# T2 Readout — two-arm seeded-bugfix benchmark (camel corpus)

Run: 2026-07-21 23:11 → 2026-07-22 ~00:30 IST. Driver `benchmarks/vs_bash/tier1/run_t2.py` @ vectr `6b422df`.
8 live sessions (4 pre-registered tasks × 2 arms, vectr-as-shipped first per task), model sonnet,
max-turns 40, session timeout 900s. Aggregate: `t2_run_20260721T231138.json`. Smoke (T2-02/vectr,
discarded by design): under `../59c9385/t2/`. Fixture verified restored after run
(`?? .vectrignore` only, HEAD a543dc648c74).

## Headline: gate results (primary metric)

| Task | vectr arm | bash arm |
|---|---|---|
| T2-01 route-template local bean | **FAIL** (no fix written; 41 turns = max-turns exhaustion, is_error) | PASS (40 turns — at the ceiling) |
| T2-02 simple-parser logical operator | PASS | PASS (**byte-exact recreation of upstream fix**, 0-byte delta) |
| T2-03 SSE streaming flush | PASS | PASS |
| T2-04 remote WrappedFile converter | PASS | PASS |

**Bash 4/4, vectr 3/4.** All 4 seeds validated (pre-gate FAIL each time); 0 task errors, 0 timeouts,
0 `gate_error` flags.

## Diff-vs-upstream scoring (sentinel review)

Capture design property: seed = reverse-applied upstream fix, so each captured diff ≡ (agent fix −
upstream fix); empty diff = exact recreation.

- Every arm edited the correct source file(s) on every task; zero test-file edits (prompt compliance clean).
- T2-01/vectr: delta = pure seed reversal → **no fix was written at all**; the session spent its 40
  turns exploring (6 vectr_search among 40 tool calls) and died mid-investigation.
- T2-01/bash: semantically equivalent variant of the upstream fix (collect renamed local-bean keys,
  re-register as template params).
- T2-02/bash byte-identical to upstream; T2-02/vectr a correct 2-file variant.
- T2-03 both arms: correct fixes; deltas dominated by the upstream commit's bundled unrelated docs
  hunk (`a2a-component.adoc`), unrecreated by both arms equally.
- T2-04 both arms: correct converter rewrites (variant implementations).

## Adoption (vectr-as-shipped arm: on-disk init artifacts + hooks + .mcp.json)

- vectr invoked in **3/4** vectr-arm sessions (T2-01: 7 calls, T2-03: 2, T2-04: 2; T2-02: 0).
  Versus T1c's 2/6 with coaching stripped → the shipped on-disk artifacts roughly double invocation.
- Usage pattern is shallow: `vectr_status` + 1–6 `vectr_search`, zero locate/trace/remember. The
  memory half was never touched in any session.
- Efficiency: no consistent tool-count advantage (vectr arm 40/19/12/21 tool calls vs bash 39/16/15/12).

## The run's biggest finding: no agent ever saw a red test

Across all 8 sessions: **25 maven invocations, 0 BUILD FAILURE observed, 100% single-module
(`-pl <one module>`), 100% piped (`| tail`) or quieted (`-q`)**.

- On cross-module tasks (T2-01, T2-02) a single-module test run resolves the *installed* `~/.m2`
  artifact instead of the modified/seeded source → **false-pass**: the seeded bug "didn't reproduce"
  and post-fix "verification" passed regardless of fix correctness. The T2-01/bash session's
  verification was hollow — its gate pass rests on fix quality alone.
- Piping through `tail` masks the exit code (pipeline exit = tail's); `-q` empties the output. So
  the failure signal is doubly invisible at the tool-stream level.
- Both arms equally affected (symmetric); the driver's reactor-paired gate stayed the honest referee.

Consequences filed:
1. **Memoization evidence (operational class)**: "single-module surefire false-passes against
   installed artifacts in this reactor — verify with `-pl <src>,<test>`" is an operational memory
   every session needed and none had. It plausibly contributed to T2-01/vectr's turn exhaustion
   (no reliable reproduce-first loop available). This is the flagship example for
   UPG-DISCOVERY-MEMOIZATION's operational scope.
2. **L1 capture design**: exit codes alone are near-useless in real agent streams (100% piped here);
   content-marker parsing (BUILD SUCCESS/FAILURE etc.) is mandatory — empirically the *common case*
   of the "exit-code-lying tools" problem, not an edge case.

## Caveats (pre-registered + observed)

- One shared fixture, sequential, vectr arm always first (order effects uncontrolled; documented at design time).
- N=4 tasks, one corpus, one model. Gate = the upstream fix's own test kept at fixed version.
- Turn ceiling binds: T2-01 hit 41 (vectr, fail) and 40 (bash, pass) — max-turns 40 is inside the
  difficulty envelope of these tasks (same lesson as T1c C01). A ceiling-insensitive replication
  would need a higher cap.
- Vectr searches returned camel code (corpus healthy, 176k chunks), but sessions used it only for
  orientation; no evidence of retrieval influencing the failing arm's outcome either way.
- One `rate_limit_event` (T2-01/bash) did not stall the session; the hard-kill watchdog was never needed.

## Verdict

On end-to-end seeded bugfixing with vectr-as-shipped, the bash arm won 4/4 vs 3/4 — the vectr arm's
loss was a turn-budget exhaustion during exploration, not a wrong fix. Search-as-shipped neither
helped nor hurt measurably on the 3 shared passes; the memory surface went unused. Combined with
T1c, the consistent conclusion: **adoption depth (and now: operational feedback-loop knowledge) is
the binding constraint, not retrieval quality** — direct evidence for the hook-injected memoization
direction (UPG-9.x / UPG-DISCOVERY-MEMOIZATION).
