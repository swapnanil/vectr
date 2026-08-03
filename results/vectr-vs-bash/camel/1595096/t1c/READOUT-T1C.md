# T1c Readout — two-arm exploration A/B, camel corpus (2026-07-21)

Setup: camel checkout @ `a543dc64` both arms; driver @ vectr main `1595096`; model sonnet;
`--max-turns 30` (12 proved insufficient in the C01 smoke — both arms hit the ceiling with empty
answers); 12 scored sessions (6 tasks x 2 arms) + the 2-session smoke. Arms differ ONLY in
available tools + cwd: three-axis guidance-purity preflight enforced on both fixtures (no
vectr-owned config, no appended CLAUDE.md/AGENTS.md blocks, no `.claude` settings hooks, no git
hooks). The vectr arm receives vectr exclusively via `--mcp-config` pointing at the live daemon
(176,382 chunks). Prompts byte-identical across arms.

## Scores (sentinel-scored 0–5 against corpus-verified flows)

| Task | vectr arm | bash arm |
|---|---|---|
| C01 route lifecycle | **0** — `error_max_turns` @31 turns, no answer ($1.23 spent) | **5** — complete chain, subagent-assisted |
| C02 type converters | **5** — incl. correct `doConvertTo` ladder | **4** — ladder order wrong (fallbacks listed last; actual: cached → fallback → assignable → Object-wildcard → miss, verified in source) |
| C03 SEDA handoff | **0** — `rate_limit_event` ~17s in, session stalled (external cause) | **5** — complete, accurate |
| C04 dead letter channel | **5** — best of arm; the only session that truly used vectr | **5** — equally strong |
| C05 choice/when routing | **5** (zero vectr calls) | **5** |
| C06 context startup | **5** (zero vectr calls) | **5** |
| **Total** | **20/30 (4/6 answered)** | **29/30 (6/6 answered)** |

**The bash arm won T1c.** Reported as a loss, per the honesty rules.

## Findings

1. **Adoption is the binding constraint — now quantified in a controlled A/B.** With every
   on-disk coaching surface stripped (the purity design), Sonnet chose to use vectr in only 2 of
   6 vectr-arm sessions (C01 partially: 4 locates then drifted to Bash/Read; C04 fully). Three of
   the four vectr-arm successes made **zero** vectr calls — for those sessions the vectr arm was
   just the bash arm with unused tools. Retrieval quality cannot matter if the tools are not
   invoked. This is direct experimental evidence for the hook-injection / CLAUDE.md-coaching
   strategy (UPG-9.x): the surfaces stripped for purity are precisely what create adoption.
2. **When vectr was used, it held its own.** C04/vectr matched the bash arm's 5/5 quality with
   ~40% fewer tool calls (15 vs 26: 3 searches n=8,3,3 + 2 locates + 7 Reads vs 8 Bash + 17 Reads
   + 1 Agent) at equal wall time (181s vs 174s). n=1 — no strong claim, but no efficiency loss.
3. **T1a's n_results question is resolved.** All 8 `vectr_search` calls across smoke + run set
   `n_results` explicitly (smoke: 5,5,5,5,3; run: 8,3,3). Zero defaulted. Median 5. Combined with
   T1b (locate-first 6/6, n≤3), agents consistently choose small explicit n.
4. **ToolSearch deferral tax.** Sessions that used vectr paid 1–2 turns loading deferred MCP
   schemas (`ToolSearch` x2 in both C01/vectr and C04/vectr) before the first vectr call.
5. **Failure-mode cost asymmetry.** Both vectr-arm losses burned full session cost with zero
   scoreable output. Max-turns exhaustion produced no wrap-up answer (C01: $1.23, 31 turns,
   nothing).

## Caveats

- n=1 per cell; qualitative readout, no statistical claims.
- Claude Code's `Agent` tool appeared in 4 sessions (C01/C02/C04 bash, C02 vectr). Subagent
  delegation bypasses the parent-level `--max-turns` cap (C01/bash: 63 tool calls at `turns=2`)
  and confounds turn counts; transcript tool lists do include subagent calls.
- C03/vectr's loss is environmental (API rate limit mid-run) — attributed to the environment,
  not vectr, but kept in the arm totals since both arms ran sequentially under identical
  conditions and only this session stalled.
- `rate_limit_event` appears in at least 2 vectr-arm transcripts (sequential same-quota-window
  runs).

## Harness gaps found (logged as tasks)

- **Driver timeout not enforced on stall**: C03/vectr went silent at ~17s; the driver sat 1274s
  against a 600s `--timeout` and reported `is_error=False` (no result event). Only the
  `usage_unparsed` flag caught it. `run_claude_session` needs a hard kill at `timeout_s` and a
  `timed_out`/missing-result flag that forces `is_error`.
- Subagent tool calls could be tagged separately from parent-session calls in the summary.

## Tier-1 close

- T1a: folded into T1c; resolved (finding 3).
- T1b: done earlier — locate-first 6/6, small n (note #383).
- T1c: this readout.

**Tier-1 verdict:** the retrieval surface works and is used competently when invoked; invocation
itself is the bottleneck. Tier-2 (end-to-end fix-a-bug / add-a-feature) should treat adoption
configuration (bare MCP vs CLAUDE.md coaching vs hook injection) as a first-class experimental
axis, not a nuisance variable.

**Tier-2 is NOT started** — user directive (2026-07-21): pause after Tier-1 close; explicit go
required.
