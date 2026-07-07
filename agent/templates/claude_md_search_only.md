# Vectr — semantic search

Vectr gives you fast semantic search over this codebase.

- **Semantic search**: find any symbol, pattern, or concept by describing it in plain English — faster than grep, without knowing where to look.

> **Loading the tools first.** Vectr's tools may be deferred behind a tool-search step. If `vectr_search` / `vectr_locate` are not directly callable yet, load them once with `ToolSearch("select:mcp__vectr__vectr_search,mcp__vectr__vectr_locate,mcp__vectr__vectr_status")`, then call them **as tools**. Never run an `mcp__vectr__*` name as a shell/bash command — that is not an executable and always fails.

> **This workspace runs in search-only mode.** Working-memory tools (`vectr_remember`/`vectr_recall`/`vectr_forget`/`vectr_snapshot`) are disabled for this daemon — there is no notes database to write to. Use scratch notes or the conversation itself for anything you need to remember across turns.

## Semantic search — 5 tools

The codebase is fully indexed. One `vectr_search` call returns ranked, relevant code chunks — no grep loops across hundreds of files, no wasted turns reading the wrong files. Use these for all exploration; use Read only to read a specific file that vectr has already pointed you to.

| Tool | Purpose | Example |
|---|---|---|
| `vectr_search("query")` | Semantic search — describe what you're looking for, get ranked code chunks back. Replaces grep + blind file reads. An identifier written in the query (`CamelCase`, `lowerCamelCase`, `snake_case`, or dotted `Class.method`) that exactly matches a known symbol is automatically resolved and appended below the results — no extra call needed; a near-miss (a misremembered name) surfaces the nearest real symbol names instead, clearly labeled as inexact. | `vectr_search("workspace lock acquisition and release")` |
| `vectr_locate(name="SymbolName")` | Symbol graph lookup — name → file:line in one call. Replaces find + grep for definitions. | `vectr_locate(name="WorkspaceLock")` → `resolver.rs:214` |
| `vectr_trace(name="symbol")` | Call graph — who calls this symbol, and what does it call. | `vectr_trace(name="acquire_lock")` |
| `vectr_map()` | Codebase overview — file tree + module summaries. Call once on an unfamiliar repo; follow with `vectr_map_save` if it returns raw metadata. | First visit to an unknown repo |
| `vectr_fetch(ids=["file:start-end"])` | Restore a chunk shown in an earlier result after it leaves your context — no re-search, no file re-read. Every search/locate/trace result names its own id; pass it back verbatim. | `vectr_fetch(ids=["resolver.rs:200-240"])` |

**Which tool for which question:** "where is X defined" → `vectr_locate`; "who calls X" / "what does X call" → `vectr_trace`; "what does this repo look like" → `vectr_map`; anything else (a pattern, a concept, "how does X work") → `vectr_search`.

**Before calling `vectr_search` on a well-known API or framework:** write out what you already know — function signatures, key types, parameter names — then, before citing any file:line specifics in your answer, always run one cheap confirming call: `vectr_locate(name="...")` (1 call, ~160 tokens, ~0.02s). Verbalization narrows what you're looking for; it never substitutes for confirming it against the actual code.
