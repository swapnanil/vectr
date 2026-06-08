# Vectr Benchmarks

Two-phase benchmark measuring vectr's cross-session memory value on real codebases.

**Design**: Phase 1 (Research) → Phase 2 (Implementation, fresh session).
- Vanilla: explores in P1, writes prose summary; P2 opens cold and must re-discover.
- Vectr: explores in P1, stores structured notes with `vectr_remember`/`vectr_snapshot`; P2 calls `vectr_recall()` and implements directly.

**Core metric**: Phase 2 input tokens and cost — the re-discovery tax of starting fresh.

---

## Run 1 — Django (familiar codebase)

**Path**: `django/`  
**Codebase**: `django/django` — ~3,020 files, ~39,267 chunks  
**Prompt variant**: `additive`

| Task | Vectr P2 token savings | Vectr P2 cost savings | Verdict |
|---|---:|---:|---|
| `custom_field` (deep ORM internals) | −24% | −60% | vectr wins |
| `rate_limit_middleware` (simple middleware) | −3% | ~0% | neutral |
| `async_signals` (well-known API) | +16% | worse | vanilla wins |

**Finding**: Vectr helps in proportion to re-discovery cost. On tasks where the model already has training-data coverage (Django signals), P1 overhead exceeds P2 savings. On complex multi-file internals (`custom_field`), vectr is decisive.

**Prompt A/B test** (run on `async_signals`):

| Variant | P1 cost | P2 cost | Total |
|---|---:|---:|---:|
| Vanilla baseline | $0.275 | $0.095 | $0.370 |
| `forced` ("MANDATORY, DO NOT use Read/Bash") | $0.824 | $0.132 | $0.956 |
| `memory-only` (only remember/recall, no exploration guidance) | $0.716 | $0.491 | $1.207 |
| `additive` (tools described with when-to-use criteria, model decides) | $0.561 | $0.182 | **$0.743** |

`additive` is the winner and the current default. `memory-only` is counter-intuitively worst: without vectr exploration tools available in P2, the model falls back to 21 raw Read/Bash calls instead of 2 targeted vectr lookups.

---

## Run 2 — Apache Camel (unfamiliar enterprise Java)

**Path**: `camel/`  
**Codebase**: `apache/camel` sparse-checkout (`core/` + `components/camel-core-xml/`) — 5,856 files, 47,924 chunks, 53,871 symbols  
**Prompt variant**: `additive`  
**Rationale**: Enterprise users work on private Java codebases the model has never seen. Camel's public DSL is in training data; its internal architecture (Component registration, Exchange lifecycle, RoutePolicy mechanics) is not.

### Per-task results

#### `custom_component` — MemoryQueueComponent (Component/Endpoint/Producer/Consumer)

| | Vanilla | Vectr |
|---|---:|---:|
| P1 cost | $0.54 | $0.91 |
| P1 tool calls | 53 | 46 |
| P2 input tokens | 48,789 | 35,701 |
| P2 tool calls | 51 | 11 |
| P2 wall time | 134s | 195s |
| P2 cost | $0.56 | $0.36 |
| **P2 output** | **0 bytes (failure)** | **9,398 bytes — 5 files** |

Vanilla spent all 51 P2 tool calls re-reading Component/Endpoint/Exchange internals and produced nothing. Vectr: 1 `vectr_recall` → 5 writes → complete implementation.

#### `route_policy` — CircuitBreakerRoutePolicy (CLOSED/OPEN/HALF_OPEN state machine)

| | Vanilla | Vectr |
|---|---:|---:|
| P1 cost | $0.47 | $0.72 |
| P1 tool calls | 49 | 37 |
| P2 input tokens | 72,400 | 33,153 |
| P2 tool calls | 59 | 16 |
| P2 wall time | 430s | **177s** |
| P2 cost | $1.15 | **$0.35** |
| P2 output | 283-line impl (written to file) | 280-line impl (written to file) |

Both produced structurally equivalent implementations. Vanilla wasted ~100s attempting Maven compilation. Vectr: **3× cheaper, 2.4× faster**.

#### `type_converter` — @Converter methods, OrderEvent ↔ JSON/CSV/byte[]/Map

| | Vanilla | Vectr |
|---|---:|---:|
| P1 cost | $0.85 | $0.82 |
| P1 tool calls | 48 | 39 |
| P2 input tokens | 43,656 | 30,431 |
| P2 tool calls | 25 | 11 |
| P2 wall time | 187s | **86s** |
| P2 cost | $0.48 | **$0.20** |
| P2 output | 3 files (+ test) | 2 files |

Most balanced task — P1 costs nearly identical. Vectr P2 still **57% cheaper, 2.2× faster**.

### Grand totals — Camel Run 2

| Metric | Vanilla | Vectr | Savings |
|---|---:|---:|---:|
| P2 input tokens | 164,845 | 99,285 | **−40%** |
| P2 cost | $2.19 | $0.92 | **−58%** |
| P2 tool calls | 135 | 38 | **−72%** |
| P2 wall time | 751s | 458s | **−39%** |
| All-phases cost | $4.04 | $3.36 | **−17%** |

---

## Run 3 — CPython internals (unfamiliar C codebase, multi-session)

**Path**: `cpython/`  
**Codebase**: CPython sparse checkout — `Python/` (~120 files), `Objects/` (~50 files), `Include/` (headers). ~170 C source files.  
**Prompt variant**: `additive`  
**Design**: 1 shared research session → 6 isolated implementation sessions (each a fresh `claude -p`, zero prior context). This simulates a week of feature work where research is paid once and impl sessions reuse recalled notes.

### Research vs implementation cost breakdown

Research overhead is a one-time investment. Implementation savings repeat with every task.

| Phase | Vanilla | Vectr | Delta | Why |
|---|---:|---:|---:|---|
| Research (1 session, paid once) | $1.36 | $2.63 | +94% | Vectr stores rich notes via `vectr_remember` — more output tokens |
| Implementation (6 sessions, each repeating) | $2.50 | $1.97 | **−21%** | Recalled notes replace file re-discovery in 4 of 6 tasks |
| Total sprint | $3.86 | $4.60 | +19% | Research overhead dominates at 6 tasks |

The +19% total headline **inverts to a net gain** after ~8–10 tasks reusing the same notes. Research is paid once; every additional impl session recalling those notes is pure saving.

### Implementation sessions — all 6 tasks combined

| Metric | Vanilla | Vectr | Delta |
|---|---:|---:|---:|
| Cost | $2.50 | $1.97 | **−21%** |
| Wall time | 17.6 min | 13.5 min | **−24%** |
| Turns | 123 | 94 | **−24%** |
| Read + Bash calls | 102 | 62 | **−39%** |

### Per-task re-discovery (Read+Bash before first write)

| Task | Vanilla | Vectr | Delta | `vectr_recall` fired |
|---|---:|---:|---:|---|
| `debug_gc_finalizer` | 16 | 6 | **−62%** | no |
| `feature_dict_pop_last` | 13 | 3 | **−77%** | yes |
| `cross_session_set_cartesian` | 23 | 9 | **−61%** | yes |
| `debug_descriptor_priority` | 6 | 6 | 0% | no |
| `cross_session_bytes_find_all` | 13 | 2 | **−85%** | yes |
| `cross_session_list_rotate` | 21 | 16 | **−24%** | yes |

`debug_descriptor_priority` (0%) is the honest outlier: the model has strong training coverage of Python's descriptor protocol and navigated directly without needing notes. Vectr's advantage is proportional to how unfamiliar the code is.

### Vectr tool usage (impl sessions only)

| Tool | Count |
|---|---:|
| `vectr_status` | 5 |
| `vectr_recall` | 4 |
| `vectr_search` | 1 |
| `vectr_locate` / `vectr_trace` | 0 |

`vectr_recall` doing the heavy lifting — not `vectr_search`. When research notes contain exact function signatures and code stubs, impl sessions recall rather than re-explore. `vectr_search` fired once as a targeted top-up for a detail not covered by notes.

**Finding**: The B9 semantic recall fix (vector search instead of SQL LIKE) is the single most impactful change across all CPython runs. Pre-fix: `vectr_recall` returned 0 results on multi-word queries; vectr cost was equal or higher than vanilla on every task. Post-fix: vectr Read+Bash is below vanilla on 5 of 6 tasks.

See `cpython/README.md` for the full task breakdown and methodology.

---

## Key patterns

**Pattern 1: Vectr value scales with codebase unfamiliarity.**  
Django (model has training coverage) → mixed results. Camel internals (model has never seen) → consistent −40–58% P2 savings. CPython C internals → −21% impl cost, −39% R+B across 6 tasks, but 0% on one task where model training knowledge was sufficient. The stronger the genuine re-discovery pressure, the more `vectr_recall` pays off.

**Pattern 2: Structured notes beat prose summaries.**  
Vanilla P1 produces large prose summaries (8,000–15,000 chars). These help — but not enough for P2 to skip re-exploration on unfamiliar code. Vectr stores exact class names, method signatures, interface contracts, and lifecycle ordering in structured notes. P2 can implement directly from these without re-reading any source.

**Pattern 3: P1 overhead is real but amortised on complex tasks.**  
Vectr P1 costs more (more turns, vectr API calls). On tasks where P2 would do significant re-discovery (complex internals, unfamiliar codebase), the P2 savings are 3–5× the P1 overhead. On simple tasks on familiar code, they are not.

**Pattern 4: Without cross-session memory, the context window tax compounds.**  
Vanilla P2 doesn't just cost more — it can fail entirely. On `custom_component`, 51 tool calls of re-exploration consumed the context budget before implementation began. Vectr P2 had 11 tool calls total.

---

## Vectr tool usage in Phase 1

| Task | vectr_map | vectr_locate | vectr_search | vectr_trace | vectr_remember | vectr_snapshot |
|---|---:|---:|---:|---:|---:|---:|
| `custom_component` | 1 | 7 | 0 | 0 | 9 | 1 |
| `route_policy` | 1 | 6 | 4 | 0 | 9 | 1 |
| `type_converter` | 1 | 7 | 3 | 0 | 8 | 1 |

`vectr_locate` is the dominant exploration tool — navigating 5,856 files by class name is far cheaper than `find + grep`. `vectr_search` appears on tasks requiring conceptual navigation across related classes. `vectr_trace` was not used — all tasks were additive (implement from scratch), not call-chain debugging.

---

## Files

| Run | Path | Result files |
|---|---|---|
| Run 1 (Django) | `django/` | `README.md`, `poc_results_*.json`, `answer_*_p{1,2}.txt` |
| Run 2 (Camel) | `camel/` | `README.md`, `poc_results_*.json`, `answer_*_p{1,2}.txt` |
| Run 3 (CPython) | `cpython/` | `README.md`, `run3_*.json` |

POC source: `/Users/swapnanil.s/Documents/swapnanilsaha.com/tools/vectr/poc/`
