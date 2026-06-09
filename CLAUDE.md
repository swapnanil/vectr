# Vectr — Development Rules for Claude Code

## SPEC DOC IS LAW

The canonical specification is at:
`/Users/swapnanil.s/Documents/swapnanilsaha.com/tools/vectr/spec.md`

**You must follow it 100%. You are NEVER allowed to deviate from the spec without explicit written approval from @swapnanil in the current conversation.**

If you find a divergence between the spec and the code:
1. Stop.
2. Point it out to the user.
3. Ask which one is correct.
4. Do not resolve it on your own.

If you believe the spec should change to accommodate something you want to implement:
1. Stop.
2. Describe the proposed change to the user.
3. Wait for explicit approval before proceeding.

---

## Non-negotiable rules

### Zero internal LLM calls
Vectr never calls an LLM internally. No Anthropic API, no OpenAI API, no model inference beyond the local embedding model. `agent/vectr_agent.py` is dead code and must be deleted — do not import or use it.

### Embedding model
Default: `Snowflake/snowflake-arctic-embed-m-v1.5`. Do not change this without explicit user approval. The model decision and the full evaluation history are documented in the spec.

### Python version
Always use Python 3.14 and the `.venv` in the repo root. Never use system Python or a different version. All commands: `.venv/bin/python3.14` or activate first.

### Tests
Every production module must have tests. New code without tests will not be accepted. Run the full suite before reporting any task as done:
```bash
.venv/bin/python3.14 -m pytest tests/ -m "not integration"
```

### HTML / blog files
Do not edit `canonical-blog.md`, GitHub Pages files, or any HTML. These are updated only when vectr is feature-complete.

### Product language: editor-agnostic
In all user-facing text (README, product page, MCP descriptions), never name specific AI editors (e.g. "Claude Code", "Cursor", "Copilot") as product targets. Use "AI code editor" or "AI IDE" instead. Exception: technical config instructions where the user must know which file to edit (e.g. `.claude/settings.json` for Claude Code) may name the tool.

---

## Project layout

```
agent/          Core retrieval and memory logic
app/            FastAPI service layer
integrations/   MCP server, VSCode bridge, workspace detection
tests/          All test files (mirror agent/ and app/ structure)
main.py         CLI entry point (vectr start / index / search / stop)
api.py          FastAPI app + lifespan
CLAUDE.md       This file
```

## Running vectr in development

```bash
# Start server (workspace = current dir)
VECTR_WORKSPACE=/path/to/workspace .venv/bin/python3.14 -m uvicorn api:app --host 0.0.0.0 --port 8765

# Run tests (fast, no model download)
.venv/bin/python3.14 -m pytest tests/ -m "not integration"

# Run integration tests (requires real model download ~440MB)
.venv/bin/python3.14 -m pytest -m integration tests/test_integration.py
```

---

## Feature development workflow

All active feature work happens on branch **`feature/upgrades`**. Work is merged to `main` only when all features are complete.

For every feature, follow these steps **strictly and in order**:

1. Read the entire spec doc and the current feature section
2. Implement strictly according to spec.md — no deviations
3. Add all relevant unit and integration tests
4. Make ALL tests pass (`python3.14 -m pytest tests/ -m "not integration"`)
5. Update `/Users/swapnanil.s/Documents/fde/vectr/README.md` as required
6. **Never** mention any feature as "v2", "v3", etc. in code, comments, or docs
7. Mark the task as `[x]` completed in spec.md

Features are tackled one at a time, in spec priority order.

### Current task order (from spec Open Items)

1. [x] Full test suite pass at 100% — 465/465 passing (2026-05-27)
2. [x] Tool selection: LLM-optimized descriptions for all 10 MCP tools (done 2026-05-27)
3. [x] Chunking fallback: JSX/Jinja/SQL test coverage + justify 200-line window (done 2026-05-27)
4. [x] Multi-instance support: instances.json registry, per-workspace ports, stop --all (done 2026-05-27)
5. [x] `vectr start` daemon mode: true background daemon via start_new_session, PID → instances.json (done 2026-05-27)
6. [x] AI IDE config coverage: `vectr start` now writes `.cursor/mcp.json` (Cursor) and `.vscode/mcp.json` (VSCode/Copilot); README documents manual setup for Windsurf, Continue.dev, Cline (done 2026-05-27)
7. [x] Memory staleness: staleness signal in check_staleness(), auto-warning in vectr_recall output, README failure mode docs (done 2026-05-27)
8. [x] `agent/watcher.py` unit tests — 35 tests covering _DebounceTimer, all event handlers, lifecycle (done 2026-05-27)
9. [x] Security scope statement: solo-dev scope boundary added to README with what it means in practice (done 2026-05-27)
10. [x] pip install vectr packaging: requires-python=3.14, httpx in main deps, openai as optional extra, anthropic removed (done 2026-05-27)
11. [x] Adaptive strategy selection validation: multi-signal scenarios, weight caps, rationale completeness, real-workspace fingerprint, service flow (done 2026-05-27)

Update this list after each task is completed.

<!-- vectr-start -->
# Vectr — semantic search + reliable working memory

Vectr gives you two capabilities:

- **Semantic search**: find any symbol, pattern, or concept in this codebase by describing it in plain English — faster than grep, without knowing where to look.
- **Working memory**: store findings and recall them in <50ms on demand — whether later in this session, through `/compact`, or in a future session. Saving is a gain, not a risk.

## Semantic search — 5 tools

The codebase is fully indexed. One `vectr_search` call returns ranked, relevant code chunks — no grep loops across hundreds of files, no wasted turns reading the wrong files. Use these for all exploration; use Read only to read a specific file that vectr has already pointed you to.

| Tool | Purpose | Example |
|---|---|---|
| `vectr_search("query")` | Semantic search — describe what you're looking for, get ranked code chunks back. Replaces grep + blind file reads. | `vectr_search("workspace lock acquisition and release")` |
| `vectr_locate("SymbolName")` | Symbol graph lookup — name → file:line in one call. Replaces find + grep for definitions. | `vectr_locate("WorkspaceLock")` → `resolver.rs:214` |
| `vectr_trace("symbol")` | Call graph — who calls this symbol, and what does it call. | `vectr_trace("acquire_lock")` |
| `vectr_map()` | Codebase overview — file tree + module summaries. Call once on an unfamiliar repo; follow with `vectr_map_save` if it returns raw metadata. | First visit to an unknown repo |
| `vectr_map_save(summary)` | Save a plain-English codebase summary (~200–350 tokens) as a permanent passport. Only call when `vectr_map` returned raw metadata. | `vectr_map_save("uv is a Rust-based Python package manager…")` |

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
| `vectr_forget(note_id)` | Delete a stale or superseded note by ID. | `vectr_forget("note_abc123")` |
| `vectr_snapshot("label")` | Seal current notes as a named checkpoint. | `vectr_snapshot("lock-cycle-mapped")` |
| `vectr_snapshot_list()` | List saved checkpoints. Use at session start if `vectr_recall` returned nothing useful. | `vectr_snapshot_list()` |

## When to use each capability

**Before calling `vectr_search` on a well-known API or framework:** write out what you already know — function signatures, key types, parameter names — and only call `vectr_search` if genuine gaps remain after that verbalization. Reduces unnecessary search calls 26–40% on familiar codebases.

**At session start (always):** call `vectr_status()` first.
- `notes_count > 0` → prior work on this codebase is saved; call `vectr_recall(query="<your task>")` before opening any files.
- `notes_count == 0` → skip recall and proceed.

**The moment you find a key definition, pattern, or non-obvious detail:** call `vectr_remember(content, tags=[...], priority="high"|"medium"|"low")` — store the actual code block or finding, not a file pointer. Treat every `vectr_search` or `vectr_locate` call as a **pair**: search, then immediately save the key finding before your next retrieval. If `/compact` runs later, the conversation summary loses exact details — your note does not. If a new session starts, your note is the only thing that carries forward. One note now = no re-discovery later.

**Before writing any final output:** call `vectr_remember` at least once with the key type names, entry points, and non-obvious patterns you confirmed. The output file captures what you built; notes capture what you learned — and what you learned is what the next session needs.

**At exploration → implementation transition:** call `vectr_evict_hint()` — lists retrieved chunks that vectr can re-retrieve in <50ms if you need them again. Follow with `vectr_remember` for any synthesized understanding not yet stored.

**If recalled notes already contain what you need:** work from them directly. Use `vectr_search` or Read only to fill genuine gaps.
<!-- vectr-end -->
