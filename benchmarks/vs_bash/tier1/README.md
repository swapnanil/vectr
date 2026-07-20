# Tier 1b: locate-vs-search steering

Model-in-the-loop micro A/B. Design: `.claude/bench-vectr-vs-bash/plan.md`, "Tier 1" section
(T1b). Unlike Tier 0 (`benchmarks/vs_bash/run_tier0.py`), a real `claude -p` agent session makes
the tool choice itself -- this measures which tool (`vectr_locate`, `vectr_search`,
`vectr_trace`, or plain `Bash`/`Grep`/`Read`) a real model reaches for on a name-a-symbol
question, and at what token cost. It is **observational, one arm, no ablation**: there is no
"correct tool" pre-registered per task (`expected_tool` is always `null` in the task file).

## NEVER run `claude -p` from automation without sentinel

`run_t1b.py` spawns real `claude -p` sessions, which burn the user's Claude Code quota. Every
subagent, hook, or CI job that touches this driver **must only ever pass `--dry-run`**. A real
(non-dry-run) invocation is sentinel's call to make, after reviewing the composed commands a
dry run printed. This mirrors the repo-wide rule that coder/benchmark agents never run `claude -p`
themselves (see the coder agent brief).

## Files

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

## Honesty rules

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

## Running

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

## Assumptions about `claude -p --output-format stream-json` (verify before trusting live numbers)

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
