# Benchmark harness

The code that produces the results in [`../`](../). Published here so the
methodology is fully inspectable — the numbers in the result directories
(`django/`, `cpython/`, `tigerbeetle/`, `uv/`, `camel/`) are reproducible from
this harness.

## What it does

The harness drives a real coding agent (**Claude Code**, via `claude -p
--output-format stream-json`) over a task on a real codebase, once per
configuration, and records tokens, cost, wall-time, tool calls, and the
resulting diff. Configurations differ in whether the agent has vectr's tools
and/or vectr's memory, so the deltas isolate vectr's contribution.

## Files

| File | Role |
|---|---|
| `run_poc.py` | Entry point. Builds prompts, spawns the agent, parses the stream-json event log, writes results. `--model` selects the driver model (Claude Code only). |
| `tasks.py` | Task definitions per corpus (Django, CPython, uv, TigerBeetle, Camel). |
| `quality_check.py` | Deterministic structural checks on an implementation diff (zero model calls). Secondary signal alongside execution. |
| `report.py` | Result serialization and report rendering. |
| `run_multi_repo.py` | Multi-repo retrieval-quality harness. |
| `vectr_audit_replay.py`, `vectr_corpus_replay.py`, `vectr_full_audit.py` | Offline retrieval-quality replay — score `search`/`locate`/`trace` against a live index with no agent sessions (no API/subscription cost). |
| `test_quality_check.py`, `test_multi_repo.py` | Unit tests (run in CI). |
| `setup_*.sh` | Clone + index the corpora into working directories. |

## Running

```sh
# offline retrieval quality (no agent sessions, no quota cost)
python vectr_full_audit.py

# a live agent benchmark on a corpus
python run_poc.py --run run6 --model opus --save
```

Live runs consume the Claude Code subscription quota; the offline replay
harnesses do not. See [`../README.md`](../README.md) for the result tables.
