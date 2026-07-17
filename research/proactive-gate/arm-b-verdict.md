# Arm B — vectr MCP tools only: graded (2026-07-12)

Run: `results/B-20260712-121951`. Model verified in transcript: `claude-sonnet-5`
(same `--model sonnet` alias as phase 1). Started 12:19, finished 12:39 local.

## Acceptance (protocol §Phase 2)

| criterion | result | notes |
|---|---|---|
| 1. gate test passes | **PASS** | `mvn -pl core/camel-core test -Dtest=StreamResequencerReverseGateTest` green |
| 2. gate test byte-unmodified | **PASS (with caveat)** | sha256 mismatch, but the full diff is a Javadoc re-wrap of lines 27–28 (identical words, different line breaks) — camel's formatter-maven-plugin side effect during the agent's own `mvn test`. Zero functional change; acceptance run executed on the re-wrapped file. |
| 3. regression `*Resequencer*` green | **PASS** | 22 tests pass, 2 pre-existing skips (`ResequencerEngineTest`) |
| 4. diff scope confined | **PASS** | streamConfig.json, ResequenceDefinition, StreamResequencerConfig, ResequenceReifier, generated ModelParser/ModelWriter/YamlModelWriter (+1 line each), new `ReverseExpressionResultComparator` (decorator: flips `compare`, swaps `predecessor`↔`successor`). AGENTS.md +68 is the harness's own `vectr init` regeneration, not the agent. |

## Cost / adoption metrics (baseline for arm C)

| metric | value |
|---|---|
| wall clock | 798 s (13.3 min); API time 566 s |
| turns | 63 (121 assistant events) |
| compactions | 1 |
| tokens (sonnet-5, authoritative result event) | out 50,736; cache_read 6,990,500; cache_create 191,403; uncached in 8,590 |
| cost | $4.03 |
| tool calls | Bash 31, Read 19, Edit 7, TodoWrite 2, ToolSearch 1 (for TodoWrite), Write 1 |
| **vectr tool calls** | **0** |
| **notes stored/recalled** | **0 / 0** (`notes_count: 0` on 8766 after run) |

## The headline finding

The vectr MCP server was **connected** (init event lists all 10 `mcp__vectr__*`
tools) and the workspace CLAUDE.md/AGENTS.md carried the full vectr guidance
section (30 mentions) — and the agent still made **zero** vectr calls across a
63-turn, 1-compaction, cross-cutting task, solving it with Bash/grep + Read
alone. Arm B, the shipped agent-initiated posture, behaved exactly like a
vanilla arm.

This is the cleanest datapoint yet for the adoption half of the value equation
(retrieval quality × adoption × good use): with a strong model, prompt-surface
guidance alone produced zero adoption. It is precisely the failure mode the
godMode proxy exists to remove — injection makes recall deterministic instead
of LLM-choice.

Consequence for the A/B readout: arm C's question is now sharp — does
proactive injection (a) reduce cost (turns / cache_create / wall) or (b)
improve nothing while adding proxy risk? Note the task tied at acceptance in
the 2026-06-20 eval when easy; sonnet-5 made this task easy (13 min). Deltas
under ~15% remain noise per protocol.

## Arm C prerequisites (next 5-hour quota window)

1. Stop any leftover proxy; start WITH injection:
   `<scratchpad>/vectr-proxy-dev/.venv/bin/vectr proxy --daemon-port 8766 --port 8785`
2. Verify `/__vectr_proxy/health` responds and injection is enabled.
3. `./run-arm.sh C`
4. Grade identically; then Phase-2 verdict per protocol §Verdict rule.

Window arithmetic: the window containing arm B opened ≤12:19, so it resets
≤17:19. Any time ≥17:30 local is guaranteed fresh.
