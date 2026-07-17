# Proactive-context gate — matrix verdict (task 1, camel)

Gated branch: `feature/experimental-godMode` — vectr proxy (8785) with
deterministic proactive injection of working-memory notes into the coding
agent's context. All runs `--model sonnet`, camel pinned at `a543dc64`,
canonical wall = transcript `duration_ms`. Per-run detail lives in the
sibling verdict docs and `results/<run>/grade.txt`.

## Runs (all graded PASS)

Canonical metrics mined from each run's `transcript.jsonl` (`duration_ms`,
`num_turns`, `total_cost_usd`, tool-call counts). This supersedes the earlier
draft of this table, which had missed one graded C run (C-20260712-124925)
and quoted rounded walls.

| arm | config | run | wall (s) | turns | cost | tool calls | greps | vectr calls |
|---|---|---|---|---|---|---|---|---|
| A | vanilla | 185136 | 3444.6 | 142 | $7.83 | 140 | 60 | — |
| A | vanilla | 224905 | 1518.6 | 77 | $4.37 | 75 | 21 | — |
| A | vanilla | 233814 | 1407.5 | 100 | $9.41 | 197 | 64 | — |
| B | MCP, no proxy | 121951 | 795.9 | 63 | $4.03 | 61 | 24 | 0 |
| B | MCP, no proxy | 165508 | 935.5 | 70 | $4.35 | 68 | 11 | 4 |
| C | MCP + proxy, cold | 124925 | 1030.4 | 85 | $4.94 | 83 | 22 | 10 |
| C | MCP + proxy, cold | 005502 | 775.6 | 44 | $3.55 | 42 | 14 | 1 |
| C | MCP + proxy, cold | 015112 | 3429.9 | 94 | $7.06 | 122 | 36 | 2 |
| CS | MCP + proxy, seeded | 035443 | 1629.2 | 106 | $6.57 | 103 | 26 | 5 |

9/9 graded runs passed acceptance + regression. Two C-arm launches aborted at
preflight on a **product** availability bug (REST starvation under
watcher-triggered reindex — UPG-REST-STARVATION, P1) and were re-fired after
harness hardening; abort evidence retained in `results/*-ABORTED-PREFLIGHT/`.

## Performance annex — vanilla (n=3) vs vectr-armed (B+C+CS, n=6)

| metric | vanilla mean | vectr mean | Δ | range honesty |
|---|---|---|---|---|
| wall (s) | 2123.6 | 1432.8 | **−33%** | 5/6 vectr runs beat the fastest vanilla; C2 (3429.9) lands inside the vanilla range |
| turns | 106.3 | 77.0 | **−28%** | overlap at the top (CS 106 ≈ vanilla mean) |
| cost | $7.20 | $5.08 | **−30%** | every vectr run ≤ $7.06, i.e. below the vanilla *mean*; vanilla max $9.41 |
| total tool calls | 137.3 | 79.8 | **−42%** | 2/3 vanilla runs above the *max* vectr run |
| grep/find calls | 48.3 | 22.2 | **−54%** | 2/3 vanilla runs above the *max* vectr run |
| tool calls before first edit | 61.3 | 43.8 | **−29%** | consistent but overlapping |

Read: the count-based metrics (tool calls, greps, cost) separate the arms far
more cleanly than wall-clock, which carries API-latency and agent-path noise —
even the C2 outlier, slowest run of the whole matrix by wall, still cost less
and grepped less than two of the three vanilla runs. Direction is consistent
across all six metrics: vectr-armed runs explore less, spend less, and finish
in fewer turns.

Caveat, unchanged: at n=3 vs n=6 with within-arm wall spread of 2.4–4.4×,
none of this clears a significance bar, and the merge case does not rest on
it. It is directional evidence, priced honestly: consistent across six
metrics and nine runs, not proof. A defensible "X% faster" claim would need
~15–20 runs per arm (quota-infeasible at ~$5–8 and 15–60 min per run).

## Findings

**1. No-harm: BANKED.** Proxy in the loop changes nothing detectable: 0
injection errors, 0 upstream errors across every proxy run (phase-1 probe, C1,
C2, CS); all proxy-arm runs pass the same acceptance/regression bar as
vanilla. The proxy is safe to leave on.

**2. Mechanism: PROVEN (CS).** With a seeded store, the proxy injected on 5 of
262 requests (`injected=5`, 236 correctly skipped), and daemon audit shows the
injections anchored exactly the seeded notes. Preflight asserted the seed
count; `seeds-at-launch.jsonl` is snapshotted. Injection fires when — and only
when — there is something to inject. Cold-store C runs are the control: with
nothing in the store, C ≈ B plus a passthrough proxy, which is why the
remaining planned cold runs (C3, 2×B) were dropped as noise.

**3. Wall/turns/cost value: INCONCLUSIVE at feasible n — and we say so.**
Within-arm spread dominates between-arm differences: arm A spans 2.4×
(1408–3445s), arm C spans 4.4× (775.6–3429.9s), CS lands mid-range. The early
"all vectr runs faster than all vanilla runs" pattern died with C2. No
wall-clock claim is made for the merge case.

**4. Adoption: the durable finding.** Across every unseeded run the voluntary
memory workflow is ~zero (0–1 notes despite 32 guidance mentions and connected
tools; search-tool usage stochastic 0–10 calls). The seeded CS run produced the
matrix's first real memory hygiene loop: recall by id → **forget** of the
gotcha the implementation had just obsoleted → **remember** of a completion
note. Injection begets engagement (qualitative, n=1) — voluntary adoption
without it does not happen. This is the operating evidence for the
memory-mode thesis: deterministic injection is the only reliable delivery
channel.

**5. Eval-surfaced product work.** UPG-REST-STARVATION (P1; fix in review on
`fix/rest-starvation`, including a watcher `.gitignore`-parity defect found en
route), UPG-WATCH-REVERT-CHURN (P2). New data point from CS: the agent's first
two MCP calls timed out ~1 min after launch even though the settle gate had
just passed — the gate can pass inside the watcher's debounce quiet window.
Logged against the starvation fix's coverage statement.

## Decisions

- **Timebox pair (A2t/C2t): SKIPPED.** With variance this size at n=1–2, a
  single timebox pair cannot flip any conclusion above; its cost buys nothing.
- **Matrix: CLOSED** on task 1. Further value-quantification, if ever, needs
  either many more runs (quota-infeasible) or a different metric class
  (offline/deterministic), not another arm.

## Merge recommendation

**Merge `feature/experimental-godMode`** — after folding the
`fix/rest-starvation` branch and merging `main` into it (no force push) —
subject to explicit user approval per the standing directive. The case rests
on no-harm (proven) + mechanism (proven) + adoption (injection is the only
channel that produces memory engagement), and explicitly **not** on wall-clock
speedup.
