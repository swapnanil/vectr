# Vectr

> **Semantic search and persistent memory for AI code editors.**

[![CI](https://github.com/swapnanil/vectr/actions/workflows/ci.yml/badge.svg)](https://github.com/swapnanil/vectr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)

Vectr gives AI code editors two things they lack: **semantic codebase search** and **persistent working memory** — both served over MCP with zero configuration.

Your AI editor forgets everything. Vectr doesn't.

---

## The problem

Every time an AI code editor starts a task, it re-reads the same files it read yesterday. On an unfamiliar codebase it runs ripgrep, reads entire files hunting for the right function, and fills its context window with noise. In a long session it loses findings from turn 1 by turn 40. Across sessions it starts over from zero.

Vectr breaks the re-discovery loop:

- **One index** → semantic search over your whole codebase in <20ms  
- **One recall call** → structured notes from any prior session, verbatim, in <50ms  
- **Survives `/compact`** → notes are persisted to disk, not stored in context

**No API key required.** The embedding model runs locally.

---

## Benchmarks — CPython internals sprint (6 tasks, 2 agents)

The benchmark simulates a week of feature work on an unfamiliar C codebase (CPython internals). One research session stores findings with `vectr_remember`; six isolated implementation sessions each open cold and call `vectr_recall`.

**Implementation sessions only — 6 tasks combined:**

| Metric | Vanilla | Vectr | Delta |
|---|---|---|---|
| Cost | $2.50 | $1.97 | **−21%** |
| Wall time | 17.6 min | 13.5 min | **−24%** |
| Turns | 123 | 94 | **−24%** |
| Read + Bash calls | 102 | 62 | **−39%** |

**Per-task re-discovery (Read+Bash before first write):**

| Task | Vanilla | Vectr | Delta |
|---|---|---|---|
| `debug_gc_finalizer` | 16 | 6 | −62% |
| `feature_dict_pop_last` | 13 | 3 | −77% |
| `cross_session_set_cartesian` | 23 | 9 | −61% |
| `debug_descriptor_priority` | 6 | 6 | 0% |
| `cross_session_bytes_find_all` | 13 | 2 | −85% |
| `cross_session_list_rotate` | 21 | 16 | −24% |

**Research vs implementation cost breakdown:**

The research phase (paid once to build notes) costs more for vectr (+94%) because it stores rich code stubs and function signatures via `vectr_remember`. The implementation phases (which repeat every task) cost less because `vectr_recall` replaces file re-discovery. The research overhead breaks even after ~8 tasks of note reuse.

| Phase | Vanilla | Vectr | Why |
|---|---|---|---|
| Research (1 session, paid once) | $1.36 | $2.63 | Vectr stores notes — more output tokens |
| Impl (6 sessions, repeating) | $2.50 | $1.97 | Notes replace re-discovery |
| Total sprint | $3.86 | $4.60 | Inverts to net gain after ~8 tasks |

Earlier runs on Apache Camel (Java, 5,856 files): **−58% impl cost · −72% impl tool calls · −39% wall time.**

Full results: [`benchmarks/`](benchmarks/)

---

## Quick start

**Local (recommended)**

```bash
python3.14 -m venv ~/.vectr-env
source ~/.vectr-env/bin/activate   # Windows: ~/.vectr-env/Scripts/activate
pip install git+https://github.com/swapnanil/vectr
cd /path/to/your/project
vectr start
```

**Requires Python 3.14+.** To install:
- macOS: `brew install python@3.14`
- Ubuntu/Debian: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.14 python3.14-venv`
- Windows: [python.org/downloads](https://www.python.org/downloads/)

`vectr start` returns immediately. Indexing runs in the background — run `vectr status` to check progress. On first run the embedding model downloads once (~440 MB). Restart your AI code editor once to pick up the new MCP config.

**Docker (CI/servers)**

```bash
git clone https://github.com/swapnanil/vectr
cd vectr
docker-compose up api
```

Exposes port 8765. Docker does not auto-write IDE config files — use local install for IDE integration.

---

## Connect to your AI code editor

`vectr start` writes the MCP config for your editor automatically. Restart your editor once.

| Editor | Config written automatically |
|---|---|
| Claude Code | `.claude/settings.json` |
| Cursor | `.cursor/mcp.json` |
| VS Code / GitHub Copilot | `.vscode/mcp.json` |
| Windsurf, Cline, Continue | See manual setup below |

<details>
<summary>Manual setup</summary>

**Claude Code** — `.claude/settings.json`:
```json
{ "mcpServers": { "vectr": { "type": "http", "url": "http://localhost:8765/mcp" } } }
```

**Cursor** — `.cursor/mcp.json`:
```json
{ "mcpServers": { "vectr": { "url": "http://localhost:8765/mcp" } } }
```

**VS Code / GitHub Copilot** (1.99+) — `.vscode/mcp.json`:
```json
{ "servers": { "vectr": { "type": "http", "url": "http://localhost:8765/mcp" } } }
```

**Windsurf** — `~/.codeium/windsurf/mcp_settings.json`:
```json
{ "mcpServers": { "vectr": { "serverUrl": "http://localhost:8765/mcp" } } }
```

**Continue.dev** — `.continue/config.json`:
```json
{ "mcpServers": [{ "name": "vectr", "transport": { "type": "http", "url": "http://localhost:8765/mcp" } }] }
```

</details>

---

## How it works

1. **AST-aware chunking** — tree-sitter parses each file and splits at function/class/method boundaries. No chunk breaks mid-logic.
2. **Code embeddings** — `Snowflake/snowflake-arctic-embed-m-v1.5` maps natural-language queries to code symbols ("JWT validation" → `verify_jwt_token`). BM25 handles exact symbol names.
3. **Hybrid search** — vector similarity + BM25 combined, weighted by codebase characteristics (large/unfamiliar → semantic-heavy; small/well-documented → BM25-heavy).
4. **Symbol graph** — call edges, import chains, and HTTP routes (Flask/FastAPI/Express/Spring) are extracted and stored. `vectr_locate` uses 5 fallback strategies: exact match → suffix → same-module → unique-name → import-chain → fuzzy (edit distance ≤ 2).
5. **Working memory** — `vectr_remember` stores structured notes to SQLite + ChromaDB. `vectr_recall` does semantic search over notes — not SQL LIKE — so multi-word queries always find relevant context.
6. **MCP protocol** — 12 tools served over HTTP. Any MCP-compatible AI code editor connects without plugins.

---

## 13 MCP tools

`vectr start` writes a `CLAUDE.md` into your workspace with this table and usage guidance — your AI code editor knows which tool to reach for without being prompted.

**Search tools** — retrieve code from the index:

| Situation | Tool |
|---|---|
| You know a concept or behaviour, not a name | `vectr_search("description")` |
| You know a symbol name, not its file | `vectr_locate("SymbolName")` — 5 fallback strategies, optional `caller_file` |
| You need callers / callees of a symbol | `vectr_trace("symbol_name")` |
| You need an architectural overview | `vectr_map()` |
| You want to save a synthesised map summary | `vectr_map_save(summary)` |
| You have runtime call data to inject | `vectr_ingest_traces([{caller, callee}])` |
| You need index health / note count | `vectr_status()` |

**Memory tools** — store and recall across sessions:

| Situation | Tool |
|---|---|
| Notes exist from a prior session | `vectr_recall(query)` — semantic vector search, not substring match |
| You found something worth preserving | `vectr_remember(content, tags, priority)` |
| End of a long session, want a checkpoint | `vectr_snapshot("label")` |
| Looking for a prior checkpoint | `vectr_snapshot_list()` |
| Context is filling up | `vectr_evict_hint()` — identifies chunks vectr can re-retrieve |
| Notes are stale after a large refactor | `vectr_forget(note_id=N)` per note, or `vectr_forget(all=true)` to clear |

---

## CLI reference

```bash
vectr start                           # index + start daemon for current dir
vectr start --path /project/api       # specific workspace
vectr status                          # index health, chunk count, notes count
vectr status --all                    # all running instances
vectr stop --path /project/api        # stop one instance
vectr stop --all                      # stop all instances
vectr index --path .                  # re-index without restarting daemon
vectr init --path .                   # write CLAUDE.md + MCP config without starting
vectr init --exclude vendor           # exclude directories from indexing
vectr forget --path .                 # delete all working-memory notes
```

---

## Excluding paths

Create `.vectrignore` in your project root (same syntax as `.gitignore`):

```
vendor/
node_modules/
*.pb.go
dist/
```

Or pass `--exclude` at init time:

```bash
vectr init --exclude vendor --exclude dist
```

Exclusions apply to both the initial index walk **and** the live file watcher, so
adding a directory to `.vectrignore` stops a running instance from re-indexing it.
The next index also **prunes** any chunks already stored for now-excluded (or
deleted) files — you don't have to rebuild from scratch. If you ever need a clean
rebuild (e.g. after changing the embedding model), force one:

```bash
vectr index --path . --force      # ignore the incremental cache, re-embed everything
```

---

## Supported languages

| Language | Chunking | Symbol graph |
|---|---|---|
| Python | AST (functions, classes) | ✓ |
| JavaScript | AST (functions, classes, arrow fns) | ✓ |
| TypeScript | AST | ✓ |
| Go | AST | ✓ |
| Rust | AST | ✓ |
| Java | AST | ✓ |
| Zig | AST | ✓ |
| All others | 200-line windows, 50-line overlap | — |

HTTP routes (Flask/FastAPI decorators, Express `app.get()`, Spring `@GetMapping`) are extracted as symbols and searchable via `vectr_locate("GET /api/users")`.

---

## Cost

| | Cost |
|---|---|
| Embedding model | $0.00 — one-time ~440 MB download, cached at `~/.cache/vectr/` |
| Re-index (10k files, first run) | ~10 min on CPU; <5 sec on subsequent runs (mtime cache) |
| Incremental re-index per changed file | ~0.5 sec |
| vectr_search / vectr_recall | $0.00 — local inference only |

---

## Security

Vectr v1 is designed for a **solo developer on a personal machine**.

- MCP server binds to `127.0.0.1` only — not reachable from other hosts
- CORS restricted to localhost origins
- Each workspace gets its own isolated DB directory, port, and process
- No API key authentication in v1 — any local process can query
- Index and notes persist locally in `~/.cache/vectr/`

Multi-user, authentication, and encryption at rest are out of scope for v1.

---

## When vectr can hurt

**Stale notes after codebase churn** — notes store file paths at write time. After a large refactor, `vectr_recall` will flag changed referenced files with `[STALE]`. Re-verify before acting, delete the stale note with `vectr_forget(note_id=N)`, or clear everything with `vectr_forget(all=true)`.

**Over-retrieval on a well-known API** — if the model already knows a framework deeply from training (React hooks, Django ORM), vectr's research overhead may exceed savings. The benchmark shows 0% improvement on `debug_descriptor_priority` — a task where the model's training knowledge was sufficient to navigate without notes.

---

## Built with

Python 3.14 · FastAPI · sentence-transformers · tree-sitter · ChromaDB · BM25 · Docker

## Author

Swapnanil Saha · [swapnanilsaha.com](https://swapnanilsaha.com)
