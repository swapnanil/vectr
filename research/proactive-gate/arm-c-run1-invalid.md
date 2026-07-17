# Arm C run 1 — INVALID as a value test (2026-07-12)

Run: `results/C-20260712-124925`, fired 12:49 on the user's explicit "run it
now" (same quota window as arm B). Model `claude-sonnet-5`. The run is invalid
for the Phase-2 value question but productive: it surfaced one harness gap and
two real branch bugs.

## What happened

The task itself completed fine — acceptance green, `*Resequencer*` regression
green, gate-test sha mismatch again the benign formatter Javadoc re-wrap (same
two comment lines as arm B, full diff verified), diff scope confined to
resequencer files (StreamResequencer.java approach this time, plus mbean +
catalog json — 11 files).

But the proxy metrics tell the real story: `requests: 71, injected: 0,
inject_skipped: 43, inject_bypassed_error: 23, upstream_errors: 0`.

## Root cause of `injected: 0` — harness gap (mine)

The daemon-side **master opt-in** gates everything:
`app/service.py::proactive_context` → `ProactiveSettings.from_env()` →
`enabled = _env_bool("VECTR_PROACTIVE", PROACTIVE_ENABLED)` and
`config.yaml` ships `proactive.enabled: false`. Daemon 8766's environment
(verified via `ps eww`) has no `VECTR_PROACTIVE` — so every `/v1/proactive`
call returned empty context in ~4 ms (`processing_ms: 0`, matching a live
probe) and the proxy counted it as `inject_skipped`.

The proxy startup banner "Injection : on" reflects only the proxy-side
`proxy.inject` flag — different process, different switch. Preflight verified
the wrong layer. run-arm.sh now seeds a probe note and requires
`/v1/proactive` to return non-empty packed context before an arm C run.

## Branch bugs found (must fix before rerun; the gate did its job)

1. **UPG-PROXY-BUDGET-40MS** — `config.yaml proactive.proxy.inject_budget_ms:
   40`, but the provider's own httpx timeout is 0.5 s
   (`DaemonInjectionProvider timeout_s=0.5`). The outer
   `asyncio.wait_for(budget=40ms)` (proxy.py:233) always trips before the
   provider's graceful-empty path can engage, so any daemon latency >40 ms is
   miscounted as `inject_bypassed_error` — 23/71 requests during the run,
   plausibly during watcher re-index bursts as the agent edited files
   (`watcher_last_batch_duration_ms: 187702` on this corpus). The budget must
   exceed the provider timeout with headroom (or derive one from the other).
2. **UPG-PROXY-SILENT-BYPASS** — proxy.py:237-238 swallows the exception and
   bumps the counter with **no log line**; the proxy log contains zero
   diagnostics for 23 fail-opens. Root-causing required reading source. Each
   bypass should log exception class + elapsed ms (never request bodies/auth).

## Also observed

- **Adoption flip vs arm B (stochastic, N=1)**: this agent DID use vectr
  MCP unprompted — locate ×6, search ×2, status ×1, remember ×1 (1 note) —
  with injection contributing nothing (0 injected). Same setup as arm B
  otherwise. Reinforces that N=1 adoption deltas are noise.
- Cost (not comparable as a value datapoint, recorded for completeness):
  wall 2301 s (claude-reported duration 1030 s — agent parked long maven
  builds in background tasks), 85 turns, 1 compaction, $4.94, out 73.5k,
  cache_read 7.53M. Arm B: 798 s, 63 turns, $4.03, out 50.7k.
- Transparency held under injection-attempt load: 0 upstream errors, all 23
  failures failed open, agent unharmed, task passed.
- Stale `.claude/settings.json` in poc-camel pointed `mcpServers` at dead port
  8802; `.mcp.json` (8766) won — cleaned up, `enableAllProjectMcpServers:
  true` retained (it is what auto-approves the MCP server in headless runs).

## Rerun plan (arm C run 2, next quota window)

1. Coder fixes the two branch bugs on `feature/experimental-godMode`
   (budget default + bypass logging), suite green.
2. Restart daemon 8766 from the updated worktree venv **with
   `VECTR_PROACTIVE=true`**; restart proxy (no `--no-inject`).
3. run-arm.sh C — the new preflight probe asserts end-to-end injection.
4. Grade vs arm B baseline; require `inject_bypassed_error: 0` per protocol.
