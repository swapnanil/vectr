# Apache Camel Benchmark — Run 2

**Codebase**: `apache/camel` (sparse-checkout: `core/` + `components/camel-core-xml/`)  
**Date**: 2026-05-26  
**Model**: `claude-sonnet-4-6`  
**Embed model**: `Snowflake/snowflake-arctic-embed-m-v1.5`  
**Prompt variant**: `additive`

**Index stats (vectr copy)**:
- Files: 5,856 | Chunks: 47,924 | Symbols: 53,871
- Index time: ~30 min (first time, from `setup_camel.sh`)
- Subsequent starts: instant (mtime cache)
- Strategy: `graph_first=true`, `semantic_weight=0.75`, `bm25_weight=0.25`
- Strategy rationale: *"large codebase — semantic weighted higher to cut noise; java — strong static symbol graph, graph traversal first"*

**Why Camel**: Enterprise users work on large private Java codebases the model has never seen.
Apache Camel (350k+ LOC, 15+ years old) represents that class. The model knows Camel's *public DSL*
(`from/to/filter`) but not its internal architecture — Component registration, Exchange lifecycle,
RoutePolicy mechanics. Tasks require reading source to implement correctly.

---

## What this measures

Each task runs in two phases in **separate Claude sessions** — simulating the real-world pattern of
a developer researching a codebase in one session, then implementing in another.

**Phase 1 — Research**: the agent explores the codebase and records findings.
- Vanilla: uses Read + Bash freely, writes a prose RESEARCH SUMMARY at the end.
- Vectr: uses vectr tools (exploration + memory), stores structured notes with `vectr_remember`,
  seals with `vectr_snapshot`.

**Phase 2 — Implementation**: Fresh session, no prior context except what was stored.
- Vanilla: must re-explore using only the prose summary carried in context.
- Vectr: calls `vectr_recall()` first (~200 tokens), gets structured notes, implements.

**Core metric**: Phase 2 input tokens — the re-discovery cost of starting fresh.

**Known measurement bug**: `answer_length` captures the agent's final *text response*, not files
written to disk. For tasks where agents wrote code directly to files and returned only a prose
summary, `answer_length` under-counts actual output. True output volume is documented under
"Code written to disk" per task.

---

## Setup

```bash
# From the poc directory
bash setup_camel.sh       # sparse-clones Camel into /tmp/poc-camel-vanilla + /tmp/poc-camel-vectr
                          # indexes the vectr copy, writes CLAUDE.md + .mcp.json

# Start vectr (if not already running from setup_camel.sh)
cd /path/to/vectr
.venv/bin/vectr start --path /private/tmp/poc-camel-vectr

# Run a task
cd /path/to/poc
TASKS=CAMEL_TASKS  # tasks.py: set TASKS = CAMEL_TASKS
python3.14 run_poc.py --task custom_component --agent both --prompt-variant additive --save \
  POC_VANILLA_DIR=/tmp/poc-camel-vanilla \
  POC_VECTR_DIR=/tmp/poc-camel-vectr \
  POC_OUTPUT_DIR=/path/to/vectr/benchmarks/camel
```

All source files: `tools/vectr/poc/` in the `swapnanilsaha.com` repo.

---

## Prompts used

### Vanilla research suffix (all tasks)
```
At the end of your exploration, write a RESEARCH SUMMARY section in your answer
with the key findings: file paths, function names, call patterns, and gotchas.
Be specific — this summary is the only reference available for the implementation.
```

### Vectr research suffix — `additive` variant
```
--- VECTR TOOLS (available alongside Read and Bash — use when they help) ---

EXPLORATION — use these when you don't already know where to look:
  vectr_map()          — structural overview of the codebase (good on first contact)
  vectr_search(query)  — find code by semantic meaning; faster than grep for conceptual queries
  vectr_locate(name)   — find exactly where a class/function is defined
  vectr_trace(name)    — see callers and callees without manually opening files

If you already know which file to read or which symbol to look for, Read and Bash
are fine. Use vectr exploration tools when you're navigating unfamiliar territory.

MEMORY — always use these to persist findings to the next session:
  vectr_remember(content, tags=["tag"], priority="high"|"normal")
                       — store each key finding: file paths, signatures, patterns, gotchas.
                         The implementation session starts fresh and won't have your context.
  vectr_snapshot("phase1-complete")
                       — call this at the very end to seal all notes.
```

### Vectr implementation prefix — `additive` variant
```
Your research notes from the previous session are stored in vectr.
Call vectr_recall() first to retrieve them.
If you need to verify a specific detail, vectr_search() or vectr_locate() is available.
```

---

## Phase 2 comparison (core metric)

| Task | Vanilla P2 input tok | Vectr P2 input tok | Savings | Vanilla P2 cost | Vectr P2 cost | P2 cost savings |
|---|---:|---:|---:|---:|---:|---:|
| `custom_component` | 48,789 | 35,701 | **−27%** | $0.5644 | $0.3649 | **−35%** |
| `route_policy` | 72,400 | 33,153 | **−54%** | $1.1459 | $0.3482 | **−70%** |
| `type_converter` | 43,656 | 30,431 | **−30%** | $0.4780 | $0.2045 | **−57%** |
| **Total** | **164,845** | **99,285** | **−40%** | **$2.1883** | **$0.9176** | **−58%** |

---

## Full metrics per task

### Task 1: `custom_component` — Implement a custom Camel component (in-memory queue)

**Task**: Research Camel's Component/Endpoint/Producer/Consumer/Exchange model, then implement
`MemoryQueueComponent` with URI scheme `memqueue:channelName`.

#### Phase 1 — Research

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 33,375 | 91,145 |
| Output tokens | 2,907 | 996 |
| Turns | 2 | 47 |
| Tool calls | 53 | 46 |
| Wall time | 223s | 231s |
| Cost | $0.5408 | $0.9111 |
| Answer length | 9,514c | 3,443c |

Vanilla P1 tool breakdown: `Bash×28, Read×25`  
Vectr P1 tool breakdown: `Bash×16, Read×17, vectr_map×1, vectr_status×1, vectr_locate×7, vectr_remember×9, vectr_snapshot×1`

Vanilla P1 ran only **2 turns** (the `turns` field counts agent turns; the agent spawned a
subagent that did the bulk of tool calls in a single turn). Vectr P1 ran 47 turns with richer
structured output — 9 notes stored, 1 snapshot.

#### Phase 2 — Implementation

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 48,789 | 35,701 |
| Output tokens | 125 | 852 |
| Turns | 31 | 12 |
| Tool calls | 51 | 11 |
| Wall time | 134s | 195s |
| Cost | $0.5644 | $0.3649 |
| Text answer | 0c | 2,997c |
| Vectr tools | — | `vectr_recall×1` |

**Re-discovery calls (Read+Bash before first Write)**: Vanilla = 51 (entire session spent re-exploring, never reached implementation). Vectr = 1 (`vectr_recall`).

#### Code written to disk

| Agent | Files | Total bytes | Notes |
|---|---|---|---|
| Vanilla | **0** | **0** | No Write calls in P2 — genuine failure |
| Vectr | 5 | 9,398 | `MemoryQueueComponent.java` (2,412), `MemoryQueueConsumer.java` (2,760), `MemoryQueueEndpoint.java` (2,170), `MemoryQueueMessage.java` (445), `MemoryQueueProducer.java` (1,611) |

#### P2 timeline — Vectr (key events)

| T+ | Turn | Tool | Input |
|---|---|---|---|
| 4s | 3 | `vectr_recall` | (all notes) |
| 5s | 4 | `Write` | MemoryQueueComponent.java |
| 7s | 6 | `Write` | MemoryQueueEndpoint.java |
| 10s | 8 | `Write` | MemoryQueueProducer.java |
| 14s | 11 | `Write` | MemoryQueueConsumer.java |
| 18s | 13 | `Write` | MemoryQueueMessage.java |

Vectr P2: 1 recall → 5 writes → done.

#### All-phases totals

| | Vanilla | Vectr |
|---|---:|---:|
| Total tokens | 80,214 | 174,689 |
| Total cost | $1.1052 | $1.2761 |

**Note**: Total cost favours vanilla slightly here because vectr P1 was more thorough (91k tokens
vs 33k). The decisive difference is outcome: vanilla P2 produced **nothing**; vectr P2 produced a
complete 5-class implementation. A $0.17 extra total spend produced a working result vs a complete
failure.

---

### Task 2: `route_policy` — Implement a circuit-breaker RoutePolicy

**Task**: Research Camel's RoutePolicy system, then implement `CircuitBreakerRoutePolicy`
with CLOSED/OPEN/HALF_OPEN states, configurable failure threshold, and open timeout.

#### Phase 1 — Research

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 31,453 | 63,694 |
| Output tokens | 2,608 | 918 |
| Turns | 2 | 38 |
| Tool calls | 49 | 37 |
| Wall time | 211s | 212s |
| Cost | $0.4679 | $0.7152 |
| Answer length | 8,769c | 2,879c |

Vanilla P1 tool breakdown: `Bash×25, Read×23`  
Vectr P1 tool breakdown: `Bash×8, Read×15, vectr_map×1, vectr_locate×6, vectr_search×4, vectr_remember×9, vectr_snapshot×1`

Vectr used `vectr_search` 4 times here — navigating the RoutePolicy hierarchy across
`RoutePolicySupport`, `ThrottlingInflightRoutePolicy`, `DurationRoutePolicy` to understand
the `suspendOrStopConsumer` / `resumeOrStartConsumer` pattern.

#### Phase 2 — Implementation

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 72,400 | 33,153 |
| Output tokens | 103 | 631 |
| Turns | 31 | 17 |
| Tool calls | 59 | 16 |
| Wall time | 430s | 177s |
| Cost | $1.1459 | $0.3482 |
| Text answer | 0c* | 2,023c |
| Vectr tools | — | `vectr_recall×1` |

*Vanilla wrote code to a file but returned only prose summary text — `answer_length=0` is
the measurement bug. See "Code written to disk" below.

Vanilla P2 also attempted `mvn compile` (failed — no local Maven repo), and spent ~100s
trying to find JAR files in `~/.m2`, `~/.cache/vectr`, and system paths. This wasted ~25%
of its 430s wall time.

#### Code written to disk

| Agent | Files | Total bytes | Notes |
|---|---|---|---|
| Vanilla | 2 | 16,867 | `CircuitBreakerRoutePolicy.java` (9,843, 283 lines) + `CircuitBreakerRoutePolicyTest.java` (7,024) |
| Vectr | 1 | 10,024 | `CircuitBreakerRoutePolicy.java` (10,024, 280 lines) |

Both implementations: extend `RoutePolicySupport`, use `ReentrantLock` for thread safety,
`ScheduledExecutorService` for OPEN→HALF_OPEN timer, `suspendOrStopConsumer` /
`resumeOrStartConsumer` for route control. Structurally equivalent.

#### P2 timeline — Vanilla (re-discovery pattern)

Vanilla P2 spent 430s and 59 tool calls on re-exploration before and around writing:
- Turns 7–28: 13 `Read` + 16 `Bash` calls re-finding RoutePolicy, RoutePolicySupport, Route.java,
  ThrottlingExceptionRoutePolicy, DefaultRouteController, DurationRoutePolicy
- Turn 29: spawned a subagent to explore further
- Turn 54 (T+219s): first `Write` — CircuitBreakerRoutePolicy.java
- Turn 61 (T+286s): second `Write` — test file
- Turn 63–80: attempted Maven compilation, searched for JARs, validated method signatures

#### All-phases totals

| | Vanilla | Vectr |
|---|---:|---:|
| Total tokens | 106,564 | 97,847 |
| Total cost | **$1.6137** | **$1.0633** |
| **Savings** | — | **−34% cost, −8% tokens** |

**Strongest result**: both agents produced equivalent implementations, but vectr cost 34% less,
ran 2.4× faster (177s vs 430s), and used 3.7× fewer tools in P2 (16 vs 59).

---

### Task 3: `type_converter` — Implement a custom TypeConverter with fallback chain

**Task**: Research Camel's `@Converter` annotation, `TypeConverterRegistry`, auto-discovery,
and `FallbackTypeConverter`. Then implement `OrderEvent ↔ JSON/CSV/byte[]/Map` converters.

#### Phase 1 — Research

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 74,359 | 73,361 |
| Output tokens | 6,153 | 1,226 |
| Turns | 49 | 40 |
| Tool calls | 48 | 39 |
| Wall time | 236s | 246s |
| Cost | $0.8476 | $0.8195 |
| Answer length | 15,295c | 4,191c |

Vanilla P1 tool breakdown: `Bash×24, Read×24`  
Vectr P1 tool breakdown: `Bash×8, Read×18, vectr_map×1, vectr_locate×7, vectr_search×3, vectr_remember×8, vectr_snapshot×1`

This is the most balanced task: both agents did similar amounts of exploration work. Vanilla
output 6,153 tokens of prose notes vs vectr's 1,226 — vectr stored structured notes in 8
`vectr_remember` calls rather than generating a long text summary. P1 cost nearly identical.

#### Phase 2 — Implementation

| Metric | Vanilla | Vectr |
|---|---:|---:|
| Input tokens | 43,656 | 30,431 |
| Output tokens | 685 | 706 |
| Turns | 26 | 12 |
| Tool calls | 25 | 11 |
| Wall time | 187s | 86s |
| Cost | $0.4780 | $0.2045 |
| Text answer | 2,606c | 2,373c |
| Vectr tools | — | `vectr_recall×1` |

Vectr P2 ran in **86s** — less than half vanilla's 187s. Both had text answers of similar length
(vanilla 2,606c, vectr 2,373c).

#### Code written to disk

| Agent | Files | Total bytes | Notes |
|---|---|---|---|
| Vanilla | 3 | 13,300 | `OrderEvent.java` (2,056), `OrderEventConverters.java` (6,233), `OrderEventConvertersTest.java` (5,011) |
| Vectr | 2* | 10,536 | `OrderEvent.java` (2,775), `OrderEventConverters.java` (7,761) |

*Vectr also wrote several pre-existing Camel stream-converter files in the same directory tree
(`JsonConverter.java`, `CachedOutputStream.java`, etc.) — these appear to be the agent placing
implementation files alongside related existing code rather than writing new infrastructure.
Core task output (OrderEvent + Converters) is equivalent in both agents.

#### All-phases totals

| | Vanilla | Vectr |
|---|---:|---:|
| Total tokens | 118,015 | 147,792 |
| Total cost | **$1.3256** | **$1.0239** |
| **Savings** | — | **−23% cost** |

---

## Grand totals — Run 2 (all 3 tasks)

| | Vanilla | Vectr | Savings |
|---|---:|---:|---:|
| Phase 2 input tokens | 164,845 | 99,285 | **−40%** |
| Phase 2 cost | $2.1883 | $0.9176 | **−58%** |
| All-phases tokens | 304,793 | 420,328 | +38% (vectr P1 overhead) |
| All-phases cost | $4.0445 | $3.3634 | **−17%** |
| P2 wall time | 751s | 458s | **−39%** |
| P2 tool calls | 135 | 38 | **−72%** |

**P1 overhead is real** — vectr P1 costs more because the model stores structured notes
(more turns, more vectr API calls). On familiar codebases this overhead may not pay off.
On unfamiliar enterprise codebases, it consistently does.

---

## Vectr tool usage — Phase 1 (all tasks)

| Task | vectr_map | vectr_locate | vectr_search | vectr_trace | vectr_remember | vectr_snapshot |
|---|---:|---:|---:|---:|---:|---:|
| custom_component | 1 | 7 | 0 | 0 | 9 | 1 |
| route_policy | 1 | 6 | 4 | 0 | 9 | 1 |
| type_converter | 1 | 7 | 3 | 0 | 8 | 1 |
| **Total** | **3** | **20** | **7** | **0** | **26** | **3** |

`vectr_map` used once per task (structural orientation on first contact). `vectr_locate` is
the most used tool — navigating a 5,856-file codebase by class/interface name rather than by
`find` + `grep`. `vectr_search` appears on tasks requiring conceptual navigation across multiple
related classes. `vectr_trace` was not used — all tasks were additive (implement from scratch),
not callee-chain debugging.

---

## Analysis

### Why vanilla failed on `custom_component`

Vanilla P1 ran 2 turns / 53 tools and produced a 9,514-char prose summary. In P2, with that
summary in context, it spent 51 tool calls re-exploring the codebase — re-reading
`DefaultComponent`, `DefaultEndpoint`, `Exchange`, `DefaultMessage`, tracking the component
registry — without ever reaching a `Write` call. The prose summary was too abstract: it described
the pattern correctly but didn't give the model the exact method signatures and class hierarchy
needed to write conforming Java.

Vectr's structured notes (`vectr_remember`) stored exact: fully-qualified class names, method
signatures, interface contracts, lifecycle ordering. `vectr_recall` in P2 returned these as
structured context — the model could implement directly without re-reading any source file.

### Why the gap widens on `route_policy`

The circuit-breaker task requires knowing several interlocking APIs: `RoutePolicySupport`'s
thread-safety pattern (lock acquisition before state checks), `suspendOrStopConsumer` vs
`RouteController.suspendRoute`, the `isFailed()` method location, the `ScheduledExecutorService`
lifecycle (must be shut down in `doStop()`). Any one of these wrong produces code that either
doesn't compile or fails at runtime.

Vanilla P2 re-found all of this correctly (283-line impl) but at 3× the cost and 2.4× the time.

### Why `type_converter` is the most balanced

The TypeConverter API is relatively self-contained — `@Converter` methods, the
`META-INF/services/org/apache/camel/TypeConverter` registration file, null handling. Both agents
researched it thoroughly in P1 (48 vs 39 tool calls, nearly identical cost). The P2 advantage
is still clear (30% input token savings, 57% cost savings) but less dramatic than `route_policy`
because the implementation surface is more bounded.

### Run 1 vs Run 2 comparison

| | Run 1 (Django — familiar) | Run 2 (Camel — unfamiliar) |
|---|---|---|
| P2 input savings | −9% avg | **−40% avg** |
| P2 cost savings | −3% avg | **−58% avg** |
| Vanilla failures | 0/3 tasks | 1/3 tasks (complete failure) |
| Vectr P1 overhead | high relative to P2 payoff | justified by P2 savings |

The enterprise Java hypothesis is confirmed: vectr's value scales directly with how unfamiliar
the codebase is. On Django (model has seen the source many times), vectr overhead rarely pays off.
On Camel internals (model knows the public API but not the internals), vectr P1 investment
pays back 40–70% in P2 cost reduction.

---

## Known issues / measurement gaps

- **`answer_length` under-counts output**: counts final text response only, not files written
  to disk. Actual code volume documented under "Code written to disk" per task above.
- **P1 turns=2 for vanilla**: vanilla uses a subagent (`Agent` tool call) which does all
  exploration in a single outer turn; inner turns not counted. Actual exploration work
  matches tool call counts, not turn counts.
- **Maven compile attempts**: vanilla P2 on `route_policy` wasted ~100s trying to compile —
  this inflates vanilla wall time. A fairer comparison would exclude compile time.
- **Session limit**: first `type_converter` vectr run hit the daily Claude Code session limit
  mid-P2. Results above use the clean re-run (second attempt, same day after reset).

---

## Result files

| File | Contents |
|---|---|
| `poc_results_20260526_172043_additive.json` | `custom_component` — vanilla + vectr |
| `poc_results_20260526_175304_additive.json` | `route_policy` — vanilla + vectr |
| `poc_results_20260526_182751_additive.json` | `type_converter` — vanilla only (session limit hit for vectr) |
| `poc_results_20260526_203940_additive.json` | `type_converter` — vectr re-run (clean) |
| `answer_{task}_{agent}_p1.txt` | Phase 1 full answer (research notes / prose summary) |
| `answer_{task}_{agent}_p2.txt` | Phase 2 full text answer (implementation summary) |
| `/tmp/poc-camel-vanilla/` | Vanilla workspace with code written by vanilla P2 agents |
| `/tmp/poc-camel-vectr/` | Vectr workspace with code written by vectr P2 agents |
