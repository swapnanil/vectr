# Tier 1: model-in-the-loop micro A/B drivers

Design: `.claude/bench-vectr-vs-bash/plan.md`, "Tier 1" section. Unlike Tier 0
(`benchmarks/vs_bash/run_tier0.py`), a real `claude -p` agent session is in the loop for both
drivers below -- neither is a scripted replay. This directory holds two drivers, T1b
(`run_t1b.py`) and T1c (`run_t1c.py`); see the matching section for each.

## NEVER run `claude -p` from automation without sentinel (applies to BOTH T1b and T1c)

Both `run_t1b.py` and `run_t1c.py` spawn real `claude -p` sessions, which burn the user's Claude
Code quota. Every subagent, hook, or CI job that touches either driver **must only ever pass
`--dry-run`**. A real (non-dry-run) invocation is sentinel's call to make, after reviewing the
composed commands a dry run printed. This mirrors the repo-wide rule that coder/benchmark agents
never run `claude -p` themselves (see the coder agent brief).

## Tier 1b: locate-vs-search steering

A real `claude -p` agent session makes the tool choice itself -- this measures which tool
(`vectr_locate`, `vectr_search`, `vectr_trace`, or plain `Bash`/`Grep`/`Read`) a real model reaches
for on a name-a-symbol question, and at what token cost. It is **observational, one arm, no
ablation**: there is no "correct tool" pre-registered per task (`expected_tool` is always `null`
in the task file).

### Files

- **`tasks_t1b_<corpus>.jsonl`** -- 6 pre-registered name-a-symbol tasks per corpus, one JSON
  object per line: `{"id", "corpus", "category", "symbol_hint", "prompt", "expected_tool": null}`.
  `category` is one of `exact_qualified_name`, `bare_method_name`, `slightly_off_name`,
  `where_defined_and_what_it_does` -- task-authoring metadata only, never read by the driver's
  runtime logic. `symbol_hint` is likewise authoring metadata (the symbol the prompt is naming or
  near-naming), for a human to later judge tool-choice sensibility; the driver never branches on
  it. Prompts are written from general knowledge of the corpus, without looking up the real
  symbol's file/line (honesty rule), and are disjoint from every symbol already used in
  `../tasks_<corpus>.jsonl` (the Tier-0 task set for the same corpus).
- **`run_t1b.py`** -- the driver. For each task, composes a `claude -p` invocation (model pinned
  via `--model sonnet` by default, `--dangerously-skip-permissions`, `--output-format
  stream-json --verbose`, a generated `--mcp-config` pointing at the live daemon's
  streamable-http endpoint, `--max-turns 8`) and either prints it (`--dry-run`) or spawns it and
  parses the transcript.

### Honesty rules

- No query-content branching anywhere in the driver: the preamble, the `claude -p` flags, and the
  transcript parser are identical for every task regardless of what that task's prompt says
  (`.claude/HEURISTIC-DIRECTIVE.md` R5). The only per-task variation is the prompt text itself.
- Tasks are pre-registered before any live run and never edited to make a tool-choice outcome
  look better.
- Tool-choice counting reads the `claude` CLI's own `tool_use` event `name` field -- it does not
  infer or guess anything from the task's prompt text.
- `tiktoken` is a proxy tokenizer (same `cl100k_base` encoder Tier 0 uses) -- report deltas, not
  absolute-token claims.
- Losses (agent picks a worse tool than vectr would have wanted, or vectr costs more tokens than
  the bash-native path) are reported as losses, same as Tier 0.

### Running

```
# Daemon: port 8798 only, indexed from tmp/vectr-accept-django (warm cache reused).
/opt/homebrew/bin/vectr start tmp/vectr-accept-django --port 8798

# ALWAYS start here -- prints the 6 composed commands + daemon preflight, spawns nothing:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1b.py --dry-run

# Real run (sentinel-gated; burns quota) -- one task at a time to start:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1b.py --tasks B01

# Real run, full set, tagged as a smoke test:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1b.py --smoke
```

Transcripts and the aggregate JSON land in
`results/vectr-vs-bash/<corpus>/<vectr-sha>/t1b/` (gitignored -- regenerable run output, same
convention as Tier 0's `results/vectr-vs-bash/<corpus>/<vectr-sha>/`):

- `mcp_config.json` -- the generated `--mcp-config` file for that run.
- `<task_id>_<timestamp>.jsonl` -- the raw stream-json event transcript for one task's session
  (one JSON object per line, as emitted by the CLI, each stamped with a `_t` wall-clock arrival
  time by the driver).
- `t1b_<smoke|run>_<timestamp>.json` -- the aggregate: per-task tool-choice sequence, tool call
  counts, tokens-by-tool (tiktoken proxy), `n_results` values from any `vectr_search` calls, wall
  time, and totals across all scored tasks.

### Assumptions about `claude -p --output-format stream-json` (verify before trusting live numbers)

`run_t1b.py`'s transcript parser (`parse_transcript`) assumes the same event shapes
`benchmarks/harness/eval_v2.py` already parses successfully from a live multi-turn
`--input-format stream-json` session: `assistant` events carry `message.content` as a list of
blocks including `tool_use` (`id`, `name`, `input`); the following `user` event carries a
matching `tool_result` block keyed by `tool_use_id`; exactly one terminal `result` event carries
`num_turns`, `total_cost_usd`, `duration_ms`, `result`, `is_error`. This driver's invocation
differs from `eval_v2.py` in one respect it has NOT been live-verified against: it passes the
prompt as a single `-p "<text>"` argument (no `--input-format stream-json` stdin protocol, no
multi-message session) rather than eval_v2's persistent multi-turn stdin loop. Run one real
`--tasks B01` session and inspect the transcript JSONL before trusting an aggregate across all 6.
This same parser is reused unchanged by `run_t1c.py` (see below) -- the caveat applies there too.

## Tier 1c: two-arm exploration benchmark

Genuine two-arm A/B (unlike T1b, which is single-arm and observational). For each pre-registered
"explain how `<subsystem>` works" exploration task -- the multi-step case Tier 0 can't fully
simulate -- a `claude -p` session runs **twice**:

- **vectr arm**: cwd = `tmp/poc-camel` (override: `--fixture-root`), a `--mcp-config` pointing at
  the live daemon's streamable-http endpoint (`--port 8800` by default) plus `--strict-mcp-config`
  so vectr is the *only* MCP server the session can see.
- **bash arm**: cwd = `~/.cache/vectr/bench/poc-camel-bash` (override: `--bash-fixture-root`), an
  empty `mcpServers` config plus `--strict-mcp-config` so **no** MCP tools exist at all -- the
  agent has only its native Bash/Grep/Read/Glob toolset.

The only difference between the two composed invocations is which tools exist and the session's
working directory. The prompt/preamble (`_PREAMBLE` in `run_t1c.py`) is one shared string object
used to compose every invocation for both arms -- it deliberately never names a specific tool
surface (e.g. it does not say "you have vectr's tools"), because that would be untrue for the bash
arm; each session discovers its own real tool list from the CLI itself. This is both the honesty
design and `.claude/HEURISTIC-DIRECTIVE.md` R5 compliance: no query-content or arm-content
branching anywhere in the driver -- same honesty rules as T1b above (pre-registered tasks never
edited to make an outcome look better, tool-choice counting reads only the CLI's own event data,
tiktoken is a proxy tokenizer).

Corpus: camel (Java, large, unfamiliar -- the corpus vectr should have the clearest advantage on;
see `tasks_t1c_camel.jsonl`, pre-registered sentinel-authored, 6 tasks, `expected_tool: null`).

### Files

- **`tasks_t1c_camel.jsonl`** -- 6 pre-registered exploration tasks, one JSON object per line:
  `{"id", "corpus", "category", "subsystem_hint", "prompt", "expected_tool": null}`. `category` is
  always `exploration`; `subsystem_hint` is authoring metadata only (never read by the driver's
  runtime logic).
- **`run_t1c.py`** -- the driver. Reuses `run_t1b.py`'s CLI resolution, session-spawn, and
  transcript-parsing code paths unchanged (`CLAUDE_BIN`, `run_claude_session`, `parse_transcript`,
  `load_jsonl`) via a direct import rather than re-implementing them, so both T1c arms run through
  the exact same spawn/parse machinery T1b already exercises.

### Preflights

- **vectr arm**: the fixture root exists (sanity check for the session cwd) and the live daemon's
  `/v1/health` + `/v1/status` are reachable and healthy (read-only GETs only -- this driver never
  starts/stops/restarts a daemon). `/v1/status`'s `total_chunks` is reported; a count below 100k is
  a non-fatal warning (camel indexes to ~176k chunks).
- **bash arm**: the fixture root exists, is a git repo, and carries **none** of
  `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, `.cursor/`, `.codex/`, `GEMINI.md` at its root -- any one
  present is a purity failure (an agent could otherwise find vectr- or AI-agent-authored guidance
  even with zero MCP tools available).
- In `--dry-run`, any preflight failure (missing fixture, down daemon, purity failure) is a
  loudly-printed, non-fatal warning -- dry-run's job is to compose and print every invocation
  regardless. In a real run, every preflight failure is fatal.

### Metrics

Per (task, arm) session, parsed from the stream-json event stream (never inferred from a task's
prompt text): the full tool-call sequence and per-tool counts, `vectr_search`'s `n_results`
argument from every call (explicitly flagged `n_results_defaulted: true` when the argument was
omitted -- the T1a n-distribution metric folded in here), Bash/Grep/Read/Glob counts, usage tokens
in/out (from the terminal `result` event), wall seconds, turn count, and the full final-answer
text. **This driver computes no score or verdict** -- T1c is scored by sentinel review of the
recorded `final_answer` text against the two arms' transcripts, not by anything `run_t1c.py`
decides.

### Running

```
# ALWAYS start here -- prints all 12 (6 tasks x 2 arms) composed commands + both arms'
# preflights, spawns nothing:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1c.py --dry-run

# Real run (sentinel-gated; burns quota, x2 the sessions of T1b) -- one task, one arm at a time:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1c.py --tasks C01 --arms vectr

# Real run, full set, tagged as a smoke test:
./.venv/bin/python benchmarks/vs_bash/tier1/run_t1c.py --smoke
```

Transcripts and the aggregate JSON land in `results/vectr-vs-bash/camel/<vectr-sha>/t1c/`
(gitignored -- regenerable run output, same convention as T1b/Tier 0):

- `mcp_config_vectr.json` / `mcp_config_bash.json` -- the generated `--mcp-config` file for each
  arm.
- `<task_id>_<arm>_<timestamp>.jsonl` -- the raw stream-json event transcript for one
  (task, arm) session.
- `t1c_<smoke|run>_<timestamp>.json` -- the aggregate: per-(task, arm) rows (tool sequence, tool
  counts, `n_results` values, usage tokens, wall time, final answer text) plus per-arm totals.

### T1c results (2026-07-21, sentinel-scored, full readout in the results dir)

12 sessions ran (6 tasks x 2 arms, sonnet, `--max-turns 30`). **Bash arm won 29/30 vs 20/30**:
the vectr arm lost C01 to max-turns exhaustion (no answer) and C03 to an API rate-limit stall
(external), and -- the headline -- **chose to call vectr tools in only 2 of 6 sessions** despite
having them available. With all on-disk coaching stripped for arm purity, three of the four
vectr-arm answers were produced with zero vectr calls. The one fully vectr-driven session (C04)
matched the bash arm's answer quality with ~40% fewer tool calls at equal wall time, and the one
scoring error found anywhere (C02 bash: type-converter fallback ladder ordered wrong) was on the
bash side while C02 vectr got it right. All 8 `vectr_search` calls set `n_results` explicitly
(3-8, median 5) -- the T1a default-n question is resolved. Conclusion carried into Tier 2:
adoption configuration (bare MCP vs agent-instruction coaching vs hook injection) is the
first-class variable; bare MCP alone does not produce usage.

### T2 results (2026-07-22, sentinel-scored, full readout in the results dir)

8 sessions ran (4 pre-registered seeded-bugfix tasks x 2 arms, sonnet, `--max-turns 40`,
vectr-as-shipped arm = on-disk init artifacts + hooks + `.mcp.json`). Gate = the upstream fix's
own test, reactor-paired, driver-side. **Bash arm won 4/4 gates vs vectr's 3/4**: the vectr arm's
one loss (T2-01) was max-turns exhaustion with *no fix written* (41 turns of exploration), while
bash passed the same task at exactly the 40-turn ceiling; both arms found the correct file on every
task, zero test-file edits, and one bash session recreated the upstream fix byte-for-byte (T2-02,
0-byte delta). Adoption with shipped artifacts: vectr invoked in 3/4 sessions (vs 2/6 in T1c with
coaching stripped) but shallowly -- status + 1-6 searches, zero locate/trace/remember. The run's
biggest finding is environmental: across all 8 sessions, 25 maven invocations produced **zero
observed BUILD FAILUREs** -- every invocation was single-module (false-passing against installed
`~/.m2` artifacts on cross-module tasks) and piped/quieted (masking exit codes), so no agent ever
had a reliable red/green loop; the reactor-paired driver gate was the only honest referee.
Conclusion: adoption depth plus missing operational feedback-loop knowledge is the binding
constraint, not retrieval quality -- direct evidence for hook-injected operational memoization.
