# Vectr — semantic search + reliable working memory

Vectr gives you two capabilities:

- **Semantic search**: find any symbol, pattern, or concept in this codebase by describing it in plain English — faster than grep, without knowing where to look.
- **Working memory**: store findings and recall them in <50ms on demand — whether later in this session, through `/compact`, or in a future session. Saving is a gain, not a risk.

> **Loading the tools first.** Vectr's tools may be deferred behind a tool-search step. If `vectr_search` / `vectr_locate` / `vectr_remember` are not directly callable yet, load them once with `ToolSearch("select:mcp__vectr__vectr_search,mcp__vectr__vectr_locate,mcp__vectr__vectr_remember,mcp__vectr__vectr_status,mcp__vectr__vectr_recall")`, then call them **as tools**. Never run an `mcp__vectr__*` name as a shell/bash command — that is not an executable and always fails.

> **This workspace's working memory IS vectr — not files.** Record every finding, decision, and gotcha with `vectr_remember`. Do **not** write them to scratch `.md` files or a `~/.claude` memory directory: only vectr notes are re-injected automatically after `/compact` and recalled in <50ms — ad-hoc files are not, and they fragment your memory across two places. Any time you are about to "save a note to a file", call `vectr_remember` instead.

## Semantic search — 6 tools

The codebase is fully indexed. One `vectr_search` call returns ranked, relevant code chunks — no grep loops across hundreds of files, no wasted turns reading the wrong files. Use these for all exploration; use Read only to read a specific file that vectr has already pointed you to.

| Tool | Purpose | Example |
|---|---|---|
| `vectr_search("query")` | Semantic search — describe what you're looking for, get ranked code chunks back. Replaces grep + blind file reads. An identifier written in the query (`CamelCase`, `lowerCamelCase`, `snake_case`, or dotted `Class.method`) that exactly matches a known symbol is automatically resolved and appended below the results — no extra call needed; a near-miss (a misremembered name) surfaces the nearest real symbol names instead, clearly labeled as inexact. | `vectr_search("workspace lock acquisition and release")` |
| `vectr_locate(name="SymbolName")` | Symbol graph lookup — name → file:line in one call. Replaces find + grep for definitions. | `vectr_locate(name="WorkspaceLock")` → `resolver.rs:214` |
| `vectr_trace(name="symbol")` | Call graph — who calls this symbol, and what does it call. | `vectr_trace(name="acquire_lock")` |
| `vectr_map()` | Codebase overview — file tree + module summaries. Call once on an unfamiliar repo; follow with `vectr_map_save` if it returns raw metadata. | First visit to an unknown repo |
| `vectr_map_save(summary)` | Save a plain-English codebase summary (~200–350 tokens) as a permanent passport. Only call when `vectr_map` returned raw metadata. | `vectr_map_save("uv is a Rust-based Python package manager…")` |
| `vectr_fetch(ids=["file:start-end"])` | Restore a chunk shown in an earlier result after it leaves your context — no re-search, no file re-read. Every search/locate/trace result names its own id; pass it back verbatim. | `vectr_fetch(ids=["resolver.rs:200-240"])` |

**Which tool for which question:** "where is X defined" → `vectr_locate`; "who calls X" / "what does X call" → `vectr_trace`; "what does this repo look like" → `vectr_map`; anything else (a pattern, a concept, "how does X work") → `vectr_search`.

## Working memory — 7 tools

A note stored with `vectr_remember` is the only finding that survives three things: (1) re-reading the file costs tokens — recalling the note costs almost none; (2) `/compact` replaces the conversation with a summary that loses exact signatures and line numbers — your note does not; (3) a new session starts with zero context — your note is there from turn 1. `vectr_recall` retrieves it in <50ms, verbatim, any time.

**Always available:**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_status()` | Note count + index state. **Always call first at session start.** | `vectr_status()` → `notes_count: 3` → call `vectr_recall` |
| `vectr_remember(content, tags, priority)` | Save a key finding — actual code or pattern, not a file pointer. | `vectr_remember("lock_workspace() at resolver.rs:214 acquires PID-scoped lock; drops on scope exit.", tags=["lock", "resolver"], priority="high")` |
| `vectr_evict_hint()` | Lists retrieved chunks that vectr can re-retrieve in <50ms — no need to re-read those files later. | At exploration → implementation transition |

**Unlocked after your first `vectr_remember` call (or when prior notes exist):**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_recall(query)` | Retrieve notes relevant to your task. Replaces re-reading already-explored files. | `vectr_recall("workspace lock resolution flow")` |
| `vectr_forget(note_id)` | Delete a stale or superseded note by its `[#N]` id; `all=true` clears every note. | `vectr_forget(note_id=12)` |
| `vectr_snapshot("label")` | Seal current notes as a named checkpoint. | `vectr_snapshot("lock-cycle-mapped")` |
| `vectr_snapshot_list()` | List saved checkpoints. Use at session start if `vectr_recall` returned nothing useful. | `vectr_snapshot_list()` |

## When to use each capability

**Before calling `vectr_search` on a well-known API or framework:** write out what you already know — function signatures, key types, parameter names — then, before citing any file:line specifics in your answer, always run one cheap confirming call: `vectr_locate(name="...")` (1 call, ~160 tokens, ~0.02s). Verbalization narrows what you're looking for; it never substitutes for confirming it against the actual code.

__SESSION_START_GUIDANCE__

**The moment you find a key definition, pattern, or non-obvious detail:** call `vectr_remember(content, tags=[...], priority="high"|"medium"|"low")` — store the actual code block or finding, not a file pointer. Treat every `vectr_search` or `vectr_locate` call as a **pair**: search, then immediately save the key finding before your next retrieval. If `/compact` runs later, the conversation summary loses exact details — your note does not. If a new session starts, your note is the only thing that carries forward. One note now = no re-discovery later.

**Before writing any final output:** call `vectr_remember` at least once with the key type names, entry points, and non-obvious patterns you confirmed. The output file captures what you built; notes capture what you learned — and what you learned is what the next session needs.

**At exploration → implementation transition:** call `vectr_evict_hint()` — lists retrieved chunks that vectr can re-retrieve in <50ms if you need them again. Follow with `vectr_remember` for any synthesized understanding not yet stored.

**If recalled notes already contain what you need:** work from them directly. Use `vectr_search` or Read only to fill genuine gaps.
