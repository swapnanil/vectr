# Tier 0: vectr vs bash-native retrieval

Deterministic, zero-LLM-quota cost-to-answer replay. Design: `.claude/bench-vectr-vs-bash/plan.md`
("Tier 0" section). This directory holds only the Tier-0 assets; Tier 1 (model-in-the-loop
micro A/B) and Tier 2 (full agentic head-to-head) are separate, larger-quota phases not
started here.

## Files

- **`tasks_<corpus>.jsonl`** — pre-registered task queries + bash-arm recipes, one JSON object
  per line. Committed BEFORE any gold span is looked up (honesty rule — see git history: the
  recipes commit predates the gold commit for every corpus).
- **`gold_<corpus>.jsonl`** — gold file:line spans + literal "must contain" snippets, curated by
  opening the real corpus checkout after the recipes commit. Includes curation notes for any
  surprise discovered by actually running a recipe (e.g. a typo that stayed grep-discoverable by
  accidental substring containment) — such notes are never used to retroactively edit the
  already-committed recipe.
- **`run_tier0.py`** — the driver. Runs both arms for a task list and scores hit@call-k against
  gold, then writes a SHA-stamped JSON result file.

## `tasks_<corpus>.jsonl` schema

```json
{
  "id": "T01",
  "corpus": "django",
  "archetype": 1,                    // 1-10, see plan.md's 10 query archetypes
  "archetype_name": "known_symbol_lookup",
  "query": "...",                    // the NL query text, as an agent would phrase it
  "symbol": "QuerySet.get",          // locate/trace target for archetypes 1,2,3,6,10; else null
  "symbol_typo": null,               // archetype 3 only: the misremembered form issued to locate
  "canonical_symbol_guess": null,    // archetype 3 only: curator's intended real name (no file/line — not gold)
  "query_2": null,                   // archetype 9 only: the natural second-hop NL query
  "bash_recipe": ["grep -rn ...", "..."],   // one shell command string per call
  "bash_recipe_rationale": "..."     // why these commands, written from the query text alone
}
```

## `gold_<corpus>.jsonl` schema

```json
{
  "id": "T01",
  "expect_absence": false,           // true for archetype-5 (absent-topic) tasks: no gold span exists
  "gold_spans": [{"file": "...", "symbol": "...", "start_line": N, "end_line": M}],
  "must_contain_any": ["literal snippet 1", "literal snippet 2"],  // substring match against
                                                                    // accumulated captured text;
                                                                    // ANY one snippet counts as a hit
  "notes": "..."
}
```

`must_contain_any` snippets are literal substrings taken from the real source at the gold
location, verified to actually appear in the accumulated output a recipe/call-plan produces (not
just assumed) — see the `notes` field per task for cases where this required dropping an
initially-planned snippet (e.g. a grep context window too narrow to reach a docstring).

## vectr-arm call plan (fixed per archetype, `ARCHETYPE_PLAN` in `run_tier0.py`)

| Archetype | Tool sequence |
|---|---|
| 1 known-symbol lookup | `locate` -> fallback `search` (only if locate reports no symbol) |
| 2 qualified name | `locate` -> fallback `search` |
| 3 misremembered/typo | `locate` (typo form) -> fallback `search` (original query) |
| 4 NL concept | `search` |
| 5 absent topic | `search` |
| 6 who-calls-X | `trace` (direction=callers) |
| 7 stack-trace literal | `search` |
| 8 doc/howto | `search` |
| 9 cross-file flow | `search` -> `search` (second hop, `query_2`) |
| 10 structural | `locate` -> fallback `search` (n=8) |

This mapping is fixed once per archetype (task-authoring metadata, like the existing `corpus`
field in `benchmarks/acceptance/product_cases.jsonl`), never derived from a task's query text at
run time — see `.claude/HEURISTIC-DIRECTIVE.md` R5.

## Running

```
# Daemon: port 8798/8799 only, indexed from tmp/vectr-accept-<corpus> (warm cache reused).
/opt/homebrew/bin/vectr start tmp/vectr-accept-django --port 8798

# Smoke test a few tasks:
./.venv/bin/python benchmarks/vs_bash/run_tier0.py --port 8798 --corpus django --tasks T01,T08,T11 --smoke

# Full scoring run (all tasks) -- gated on sentinel review of the gold set first:
./.venv/bin/python benchmarks/vs_bash/run_tier0.py --port 8798 --corpus django
```

Results land in `results/vectr-vs-bash/<corpus>/<vectr-sha>/tier0_<smoke|run>_<timestamp>.json`
(gitignored — regenerable run output, not source, same convention as
`benchmarks/harness/results/`).

## Metrics per task

- `calls` — MCP `tools/call` count (vectr) or subprocess spawn count (bash); the MCP session's
  one-time `tools/list` handshake is measured once and reported separately, never charged per
  task.
- `tokens` — tiktoken `cl100k_base` proxy count of the accumulated captured text (report deltas
  between arms, not absolute-token claims — cl100k is a proxy, not the caller model's real
  tokenizer).
- `wall_ms` — summed wall-clock across the arm's calls.
- `hit_at_call` — smallest 1-indexed call at which the accumulated text contains any
  `must_contain_any` snippet; `null` if never hit.
- `answered` — `hit_at_call is not None`.
- For `expect_absence` tasks: no `hit_at_call`/`answered` (no positive gold text exists);
  `nonempty_calls` reports how much false-positive noise each arm produced while confirming
  absence instead.
