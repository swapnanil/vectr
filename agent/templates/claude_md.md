# Vectr — semantic search + reliable working memory

Vectr gives you two capabilities:

- **Semantic search**: find any symbol, pattern, or concept in this codebase by describing it in plain English — faster than grep, without knowing where to look.
- **Working memory**: store findings and recall them in <50ms on demand — whether later in this session, through context compaction, or in a future session. Saving is a gain, not a risk.

__TOOL_LOADING_GUIDANCE__

> **This workspace's working memory IS vectr — not files.** Record every finding, decision, and gotcha with `vectr_remember`. Do **not** write them to scratch `.md` files or an editor-managed memory directory: only vectr notes are re-injected automatically after context compaction and recalled in <50ms — ad-hoc files are not, and they fragment your memory across two places. Any time you are about to "save a note to a file", call `vectr_remember` instead.

## Semantic search

The codebase is fully indexed. One `vectr_search` call returns ranked, relevant code chunks — no grep loops across hundreds of files, no wasted turns reading the wrong files. Use these for all exploration; use Read only to read a specific file that vectr has already pointed you to.

| Tool | Purpose | Example |
|---|---|---|
| `vectr_search("query")` | Semantic search — describe what you're looking for, get ranked code chunks back. Replaces grep + blind file reads. An identifier written in the query (`CamelCase`, `lowerCamelCase`, `snake_case`, or dotted `Class.method`) that exactly matches a known symbol is automatically resolved and appended below the results — no extra call needed; a near-miss (a misremembered name) surfaces the nearest real symbol names instead, clearly labeled as inexact. | `vectr_search("workspace lock acquisition and release")` |
| `vectr_locate(name="SymbolName")` | Symbol graph lookup — name → file:line in one call. Replaces find + grep for definitions. | `vectr_locate(name="WorkspaceLock")` → `resolver.rs:214` |
| `vectr_trace(name="symbol")` | Call graph — who calls this symbol, and what does it call. | `vectr_trace(name="acquire_lock")` |
| `vectr_map()` | Codebase overview — file tree + module summaries. Call once on an unfamiliar repo; follow with `vectr_map_save` if it returns raw metadata. | First visit to an unknown repo |
| `vectr_map_save(summary)` | Save a plain-English codebase summary (~200–350 tokens) as a permanent passport. Only call when `vectr_map` returned raw metadata. | `vectr_map_save("A Go payments API: chi router, sqlc storage, worker queue in cmd/runner…")` |
| `vectr_fetch(ids=["file:start-end"])` | Restore a chunk shown in an earlier result after it leaves your context — no re-search, no file re-read. Every search/locate/trace result names its own id; pass it back verbatim. | `vectr_fetch(ids=["resolver.rs:200-240"])` |
| `vectr_ingest_traces(events)` | Add runtime call edges static analysis can't see (decorators, dynamic dispatch, monkey-patching) from profiler/trace output; they appear in `vectr_trace` marked "(dynamic)". | After a profiling run on dynamically-dispatched code |

**Which tool for which question:** "where is X defined" → `vectr_locate`; "who calls X" / "what does X call" → `vectr_trace`; "what does this repo look like" → `vectr_map`; anything else (a pattern, a concept, "how does X work") → `vectr_search`.

## Working memory

A note stored with `vectr_remember` is the only finding that survives three things: (1) re-reading the file costs tokens — recalling the note costs almost none; (2) context compaction replaces the conversation with a summary that loses exact signatures and line numbers — your note does not; (3) a new session starts with zero context — your note is there from turn 1. `vectr_recall` retrieves it in <50ms, verbatim, any time.

**Always available:**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_status()` | Note count + index state — tells you whether recall is worth calling. When to call it: see the session-start guidance below. | `vectr_status()` → `notes_count: 3` |
| `vectr_remember(content, kind, tags, priority, title, agent)` | Save a key finding — actual code or pattern, not a file pointer. `kind` controls how the note comes back: `directive` = standing rule, auto-injected at every future session start; `task` = current-work state, always recalled newest-first; `gotcha` = file-anchored caveat that resurfaces when that file is touched; `finding` (default) = relevance-ranked learning; `reference` = pointer to a URL/ticket; `decision` = an architectural/design decision plus its why — not auto-injected, recall the group chronologically with `vectr_recall(kind="decision", sort_by="chronological")` for an ADR-style decision history. `agent` (optional) — your own identifier when you are a subagent or orchestrator in a multi-agent workflow (e.g. `"coder-2"`); never inferred, shown as an attribution tag in recall output when set. | `vectr_remember("lock_workspace() at resolver.rs:214 acquires PID-scoped lock; drops on scope exit.", tags=["lock", "resolver"], priority="high")` |
| `vectr_evict_hint()` | Lists retrieved chunks that vectr can re-retrieve in <50ms — no need to re-read those files later. | At exploration → implementation transition |

**Unlocked after your first `vectr_remember` call (or when prior notes exist):**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_recall(query)` | Retrieve notes relevant to your task. Replaces re-reading already-explored files. | `vectr_recall("workspace lock resolution flow")` |
| `vectr_forget(note_id)` | Delete a stale or superseded note by its `[#N]` id; `all=true` clears every note. | `vectr_forget(note_id=12)` |
| `vectr_snapshot("label")` | Seal current notes as a named checkpoint. | `vectr_snapshot("lock-cycle-mapped")` |
| `vectr_snapshot_list()` | List saved checkpoints. Use at session start if `vectr_recall` returned nothing useful. | `vectr_snapshot_list()` |
| `vectr_promote(note_id)` | Raise a reviewed auto-captured note's trust class one step (`auto` → `agent`), so it's treated as agent-verified rather than unreviewed going forward. | `vectr_promote(note_id=12)` |

`vectr_remember` also accepts an optional `triggers` list for conditions beyond the `kind` default: `path` globs, lifecycle `event`s (session-start, prompt-submit, pre-edit, pre-run, pre-commit, post-compaction), exact `symbol` references, a `semantic` prompt-similarity match, and temporal guards (`not_before`, `expires_visibility`, `cooldown`). Conditions AND within one trigger entry and OR across entries. Omit `triggers` to keep the kind default — most notes never need it.

**Multi-agent handoff (orchestrator + subagent workflows):** working memory is a shared bus for the whole workspace, not scoped to one conversation — a note written by a subagent is immediately visible to the orchestrator's recall, and vice versa, through the same daemon. If you are a subagent, call `vectr_remember` with your key findings **before you finish** (pass `agent="your-name"` so the orchestrator can see who found what) instead of relying on the orchestrator to re-read your full transcript. If you are an orchestrator, brief each subagent to do this, and call `vectr_recall` after a subagent completes instead of parsing its raw output — the notes are the durable handoff artifact; the transcript is not.

## When to use each capability

**Before calling `vectr_search` on a well-known API or framework:** write out what you already know — function signatures, key types, parameter names — then, before citing any file:line specifics in your answer, always run one cheap confirming call: `vectr_locate(name="...")` (one deterministic call, near-zero cost). Verbalization narrows what you're looking for; it never substitutes for confirming it against the actual code.

__SESSION_START_GUIDANCE__

**When the user corrects your behavior or states a standing preference** ("stop doing X", "always run Y with these flags"): immediately save it verbatim with `vectr_remember(content, kind="directive", priority="high")`. Directives are re-injected automatically at every future session start and after every compaction — the correction never decays into a forgotten chat message. A caveat tied to one file ("this test is flaky", "this config is load-bearing") is `kind="gotcha"` — it resurfaces exactly when that file is touched.

**The moment you find a key definition, pattern, or non-obvious detail:** call `vectr_remember(content, tags=[...], priority="high"|"medium"|"low")` — store the actual code block or finding, not a file pointer. Treat every `vectr_search` or `vectr_locate` call as a **pair**: search, then immediately save the key finding before your next retrieval. If compaction runs later, the conversation summary loses exact details — your note does not. If a new session starts, your note is the only thing that carries forward. One note now = no re-discovery later.

**Before writing any final output:** call `vectr_remember` at least once with the key type names, entry points, and non-obvious patterns you confirmed. The output file captures what you built; notes capture what you learned — and what you learned is what the next session needs.

**At exploration → implementation transition:** call `vectr_evict_hint()` — lists retrieved chunks that vectr can re-retrieve in <50ms if you need them again. Follow with `vectr_remember` for any synthesized understanding not yet stored.

**If recalled notes already contain what you need:** work from them directly. Use `vectr_search` or Read only to fill genuine gaps.
