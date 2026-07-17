# Phase 1 — transparency gate: PASS (2026-07-12)

Runs: `results/phase1-direct-20260712-115427` vs `results/phase1-proxy-20260712-120106`.
Same model both sides (`claude-sonnet-5` — the `--model sonnet` alias as of
2026-07-12; note: NOT the Sonnet 4.6 line named at design time, alias moved).

| criterion | direct | proxy (`--no-inject`) | verdict |
|---|---|---|---|
| completes with correct answer | 4.21.0-SNAPSHOT | 4.21.0-SNAPSHOT | PASS |
| tool calls parse / streaming works | yes (2 turns) | yes (3 turns) | PASS |
| proxy upstream 5xx | n/a | 0 (`upstream_errors`) | PASS |
| fail-open events | n/a | 0 (`inject_bypassed_error`) | PASS |
| injection disabled honored | n/a | `injected: 0`, `inject_skipped: 4` | PASS |
| prompt cache alive | cache_read 77,322 | cache_read 141,895 | PASS |
| auth pass-through | works (run completed against real API) | works | PASS |

Turn/cost variance (2 vs 3 turns, $0.19 vs $0.50, cache_creation 27k vs 74k)
is run-to-run model stochasticity — with injection off and `injected: 0` the
proxy forwards request bodies untouched, so it cannot have added tokens.

Phase 2 may proceed: arm B (MCP only, proxy stopped), then arm C in a later
quota window (proxy restarted WITHOUT `--no-inject`).
