# Arm B rerun — B-20260712-165508 (hardened harness)

Purpose (user-requested): confirm the arm-B adoption datapoint under the
harness that PROVES the config surfaces per run (commit 3639fd6), closing the
"was vectr guidance actually present?" hole found in the original run's
post-hoc audit. Runs on the rebased branch build (tip 569c480).

## Config proof (new, per-run)

- `guidance-state.txt`: `CLAUDE.md -> AGENTS.md` symlink intact, **32 vectr
  mentions in AGENTS.md at launch**; full `AGENTS.md.at-launch` +
  `mcp.json.at-launch` snapshots in the results dir.
- Init event: MCP server `vectr` **connected**, all 10 `mcp__vectr__*` tools in
  the model's direct tool list (no deferral), model `claude-sonnet-5`.
- Preflight: notes 0 at start.

## Grade — PASS (same benign-sha caveat as run 1)

| criterion | result |
|---|---|
| gate test byte-unmodified | raw FAIL; diff = the identical 2-line Javadoc rewrap by camel's formatter plugin (verified byte-diff) → benign, satisfied in substance |
| acceptance (StreamResequencerReverseGateTest) | PASS |
| regression (`*Resequencer*`) | PASS |
| diff scope | confined to resequencer model/processor/reifier + catalog/codegen wiring (11 files; AGENTS.md delta is vectr init guidance, not agent work) |

## Metrics

wall 938s · 70 turns · 1 compaction · $4.35 · out 55,109 ·
cache_read 6,632,322 · cache_create 202,591

## Adoption — the real finding, now calibrated across 3 runs

This run: **4 vectr calls** (locate ×2, search ×1, status ×1), **0 remember, 0
notes**. Across the three same-daemon unprompted runs so far:

| run | vectr calls | notes stored |
|---|---|---|
| B run 1 (121951) | 0 | 0 |
| C run 1 (124925) | 10 (locate 6, search 2, status 1, remember 1) | 1 |
| B run 2 (165508) | 4 (locate 2, search 1, status 1) | 0 |

Read: **search-tool usage is stochastic (0–10 calls run to run); memory-workflow
adoption is consistently ~zero (0–1 notes) despite 32 guidance mentions and
connected tools.** The stable headline for the claude-code memory-mode pitch is
not "agents never call vectr" — it's "agents never voluntarily adopt the memory
workflow; only deterministic injection closes that gap."
