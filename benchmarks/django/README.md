# Django Benchmark — Run 1

**Codebase**: `django/django` (shallow clone, ~3,020 files, ~39,267 chunks)  
**Date**: 2026-05-26  
**Model**: `claude-sonnet-4-6`  
**Embed model**: `Snowflake/snowflake-arctic-embed-m-v1.5`  
**First-time index time**: ~21 min  
**Subsequent starts**: instant (mtime cache)

---

## What this measures

Each task runs in two phases in **separate Claude sessions** — simulating the real-world
pattern of a developer researching a codebase in one session, then implementing in another.

**Phase 1 — Research**: Claude explores the codebase and records findings.
- Vanilla: uses Read + Bash freely, writes a prose RESEARCH SUMMARY at the end.
- Vectr: uses vectr tools (exploration + memory), stores structured notes with
  `vectr_remember`, seals with `vectr_snapshot`.

**Phase 2 — Implementation**: Fresh session, no prior context except what was stored.
- Vanilla: must re-explore using only the prose summary carried in context.
- Vectr: calls `vectr_recall()` first (~200 tokens), gets structured notes, implements.

**Core metric**: Phase 2 input tokens — the re-discovery cost of starting fresh.

---

## Setup

```bash
# From the poc directory
bash setup_repo.sh        # clones Django into /tmp/poc-django-vanilla + /tmp/poc-django-vectr
                          # indexes the vectr copy, writes CLAUDE.md + .mcp.json

# Start vectr server pointing at Django
cd /path/to/vectr
.venv/bin/vectr start --path /private/tmp/poc-django-vectr

# Run a task
cd /path/to/poc
python3.14 run_poc.py --task custom_field --agent both --prompt-variant additive --save
```

All source files: `tools/vectr/poc/` in the `swapnanilsaha.com` repo.

---

## Prompts used

### Vanilla research suffix
```
At the end of your exploration, write a RESEARCH SUMMARY section in your answer
with the key findings: file paths, function names, call patterns, and gotchas.
Be specific — this summary is the only reference available for the implementation.
```

### Vectr `forced` variant — research suffix
```
--- VECTR TOOL USAGE — MANDATORY ---
You have vectr MCP tools. Use them for ALL code exploration.
DO NOT use Read or Bash to browse source files — use vectr tools instead.

EXPLORATION tools (replace file reads and grep):
  vectr_map()              — start here: structural overview of the codebase
  vectr_search(query)      — find relevant code by meaning (replaces grep + reading files)
  vectr_locate(name)       — find exactly where a class/function/method is defined
  vectr_trace(name)        — see what calls a symbol and what it calls (call graph)

MEMORY tools (use throughout, not just at the end):
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
                           — store each finding immediately after you make it
  vectr_snapshot("phase1-complete")
                           — call this when research is done to seal all notes

Mandatory workflow:
  1. vectr_map()  2. vectr_search/locate  3. vectr_trace  4. vectr_remember  5. vectr_snapshot
```

### Vectr `memory-only` variant — research suffix
```
--- VECTR MEMORY ---
You have vectr memory tools. Use them to preserve findings for the implementation session.
Explore the codebase however you prefer (Read, Bash, or vectr search tools).

After each key finding, store it immediately:
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
At the end: vectr_snapshot("phase1-complete")
```

### Vectr `additive` variant — research suffix (CANONICAL — now default)
```
--- VECTR TOOLS (available alongside Read and Bash — use when they help) ---

EXPLORATION — use when you don't already know where to look:
  vectr_map()          — structural overview (good on first contact)
  vectr_search(query)  — find code by semantic meaning; faster than grep for conceptual queries
  vectr_locate(name)   — find exactly where a class/function is defined
  vectr_trace(name)    — see callers and callees without manually opening files

If you already know which file to read or which symbol to look for, Read and Bash are fine.

MEMORY — always use to persist findings to the next session:
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
  vectr_snapshot("phase1-complete")  — call at the very end
```

### Vectr implementation prefix (all variants)
```
Your research notes from the previous session are stored in vectr.
Call vectr_recall() first to retrieve them.
If you need to verify a specific detail, vectr_search() or vectr_locate() is available.
```

---

## Run 1 — Main results (forced variant, both agents)

### Phase 2 comparison (implementation phase — the core metric)

| Task | Agent | P2 Input tok | P2 Tools | P2 Time | P2 Cost |
|---|---|---:|---:|---:|---:|
| custom_field | vanilla | 39,267 | 38 | 517s | $0.554 |
| custom_field | **vectr** | **29,696** | **17** | **207s** | **$0.223** |
| | *savings* | *−24.4%* | *−55%* | *−60%* | *−60%* |
| rate_limit_middleware | vanilla | 34,441 | 11 | 171s | $0.309 |
| rate_limit_middleware | **vectr** | **33,437** | **7** | 189s | **$0.308** |
| | *savings* | *−2.9%* | *−36%* | *+10%* | *−0.1%* |
| async_signals | vanilla | 25,164 | 3 | **37s** | **$0.095** |
| async_signals | vectr | 29,134 | 4 | 64s | $0.132 |
| | *savings* | *+15.8% worse* | *worse* | *worse* | *worse* |

### Phase 1 vectr tool usage

| Task | vectr_map | vectr_search | vectr_locate | vectr_trace | vectr_remember | vectr_snapshot |
|---|---:|---:|---:|---:|---:|---:|
| custom_field | 1 | 4 | 1 | 0 | 1 | 0 |
| rate_limit_middleware | 1 | 14 | 3 | 0 | 7 | 1 |
| async_signals | 1 | 10 | 5 | 1 | 11 | 1 |

### All-phases totals

| Task | Vanilla total | Vectr total | Token Δ | Cost Δ |
|---|---:|---:|---:|---:|
| custom_field | 67,677 / $0.939 | 85,321 / $0.803 | +26% tok | −15% cost |
| rate_limit_middleware | 91,853 / $0.818 | 91,328 / $0.716 | −1% tok | −13% cost |
| async_signals | 65,450 / $0.371 | 115,417 / $0.956 | +76% tok | +158% cost |
| **Grand total** | **224,980 / $2.128** | **292,066 / $2.475** | **+30%** | **+16%** |

### Source files
| Task | Raw JSON |
|---|---|
| custom_field | `poc_results_20260526_114414.json` |
| rate_limit_middleware | `poc_results_20260526_124350.json` |
| async_signals (forced) | `poc_results_20260526_125519.json` |
| Summary | `summary_run1_django.json` |

---

## Prompt variant A/B test — async_signals

Run on `async_signals` alone to find the optimal instruction framing. Vectr-only
(vanilla baseline reused from Run 1).

| Variant | P1 in-tok | P1 tools (vectr/std) | P1 cost | P2 in-tok | P2 tools (vectr/std) | P2 cost | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| vanilla | 36,418 | 0 / 17 | $0.275 | 25,164 | 0 / 3 | $0.095 | $0.370 |
| `forced` | 82,590 | 29 / 3 | $0.824 | 29,134 | 2 / 2 | $0.132 | $0.956 |
| `memory-only` | 53,501 | 19 / 21 | $0.716 | 41,957 | 2 / 21 | $0.491 | **$1.207** |
| `additive` | 49,494 | 17 / 8 | $0.561 | 32,528 | 4 / 2 | $0.182 | **$0.743** |

**Key insight — why `memory-only` is the worst variant:**  
Without vectr verification tools in the P2 prompt, the model fell back to 21 Read/Bash
calls in P2 instead of 2 targeted `vectr_locate` calls. Offering the tool with guidance
produces better behaviour than hiding it.

**`additive` is the winner:** same low P2 std-tool count (2) as `forced`, 40% lower
P1 cost, because the model skips forced exploration on code it already knows.

| Variant | P1 vectr exploration tools | P1 std tools | P2 std tools | Interpretation |
|---|---:|---:|---:|---|
| `forced` | 28 | 3 | 2 | Forced over-exploration in P1; clean P2 |
| `memory-only` | 5 | 21 | 21 | Model used Read/Bash for both phases |
| `additive` | 16 | 8 | 2 | Model chose vectr where useful; clean P2 |

### Source files
| Variant | Raw JSON |
|---|---|
| `memory-only` | `poc_results_20260526_154712_memory-only.json` |
| `additive` | `poc_results_20260526_154415_additive.json` |

---

## Analysis

### Where vectr wins

**`custom_field`** is the clearest win: −24% tokens, −55% tools, −60% time and cost in P2.
The task requires exploring deep ORM internals (Field, contribute_to_class, deconstruct,
validators) across multiple files. Vanilla P2 needed 38 tool calls to re-explore;
vectr needed 17.

### Where vectr is neutral

**`rate_limit_middleware`**: near-zero P2 difference (−3% tokens, −0.1% cost). The task
is self-contained — Django's middleware protocol lives in a few well-documented files.
Vanilla's P1 prose summary was sufficient. Vectr's structured notes gave no edge.

### Where vectr hurts (familiar API)

**`async_signals`**: vanilla P2 needed only 3 tools in 37s. The model knows the Django
signals API from training well enough to implement without exploration. Any vectr P1
overhead — whether forced or additive — cannot be recovered in P2.

This validates the core hypothesis: **vectr helps most when the model genuinely needs
to explore unfamiliar internals**. For well-known APIs, vanilla wins.

### Instruction framing matters as much as the tools themselves

`memory-only` was the worst variant despite sounding minimal, because it caused the
model to re-explore in P2 via Read/Bash instead of using targeted vectr lookups.
The `additive` framing — "use when faster, model decides" — is optimal: it prevents
forced over-exploration while keeping efficient verification available in P2.

---

## Open questions → Run 2

1. Do vectr exploration tools provide genuine value on a codebase the **model doesn't know**?
   (`async_signals` showed vectr hurts on familiar code — does it win on unfamiliar code?)
2. Does the `additive` variant naturally produce more vectr_search usage on unfamiliar code
   without any mandates?
3. What is the quality difference in P2 output — not just tokens, but correctness?
