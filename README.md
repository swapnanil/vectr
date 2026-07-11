<!-- mcp-name: io.github.swapnanil/vectr -->

<p align="center">
  <img src="https://raw.githubusercontent.com/swapnanil/vectr/main/assets/banner.svg" alt="vectr — semantic codebase search + persistent working memory for AI code editors" width="560">
</p>

# Vectr

> **Semantic search and persistent memory for AI code editors.**

[![CI](https://github.com/swapnanil/vectr/actions/workflows/ci.yml/badge.svg)](https://github.com/swapnanil/vectr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![Version 1.0.3](https://img.shields.io/badge/version-1.0.3-blue.svg)](CHANGELOG.md)
[![MCP: 14 tools](https://img.shields.io/badge/MCP-14%20tools-blue.svg)](#14-mcp-tools)

Version 1.0.3 · Last updated 2026-07-11 · [CHANGELOG](CHANGELOG.md)

Vectr gives AI code editors two things they lack: **semantic codebase search** and **persistent working memory** — both served over MCP with zero configuration.

Your AI editor forgets everything. Vectr doesn't.

---

## The problem

Every time an AI code editor starts a task, it re-reads the same files it read yesterday. On an unfamiliar codebase it runs ripgrep, reads entire files hunting for the right function, and fills its context window with noise. In a long session it loses findings from turn 1 by turn 40. Across sessions it starts over from zero.

Vectr breaks the re-discovery loop:

- **One index** → semantic search over your whole codebase in <20ms
- **One recall call** → structured notes from any prior session, verbatim, in <50ms
- **Survives `/compact`** → notes are persisted to disk, not stored in context

**Measured, not hypothetical:** recalling 3 stored notes with `vectr_recall` costs 360 tokens in one tool call. Re-deriving the same three facts with grep + Read costs ~2,060 tokens across six tool calls on the same 182-file Python repo — **~5.7× fewer tokens, 6× fewer tool calls**, in under 50ms (chars/4 tokenization; full breakdown in [Measured costs, honestly](#measured-costs-honestly)). Across a 6-task CPython sprint measuring real Read+Bash calls, that recall discipline cut re-discovery by **39% overall**, with per-task reductions ranging **0%–85%** depending on how unfamiliar the code was to the model (the 0% task is one the model could already navigate from training — see [When vectr can hurt](#when-vectr-can-hurt)).

Notes are persisted to disk, not held in the conversation — they survive `/compact` and a fresh session equally; the session boundary doesn't matter.

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

Full results: [`benchmarks/`](https://github.com/swapnanil/vectr/tree/main/benchmarks)

---

## Measured costs, honestly

Per-call token cost (median, 182-file Python repo, chars/4 tokenization):

| Tool | Median tokens | Range |
|---|---:|---|
| `vectr_search` | ~2,320 | 1,437–3,091 (n=8) |
| `vectr_locate` | ~192 | — |
| `vectr_trace` | ~720 | — |
| `vectr_recall` (index tier) | ~180 | — |

The trade-off, stated plainly: for a single pointed lookup on a small, already-familiar repo, grep is cheaper — vectr's median cost across 5 single-fact tasks was **+60% more tokens** — and faster, since a `vectr_search` round-trip takes 1.7–3.6s against ~28ms for grep. Vectr doesn't win on per-call cost; it wins on tool-call count (one round-trip instead of several), answer completeness (a whole symbol back, not a partial file read), and everything in working memory — the 5.7× recall refund from the opening section compounds with every task you resume.

Fine print: the automatic eviction/reminder banners riding along on tool responses cost tokens too — an always-on re-fetch footer runs ~27 tokens, a light nudge ~89 tokens, and the escalated action-required banner (fires only after both the chunk and token thresholds are crossed without a save) scales from ~480 to ~535 tokens before it plateaus.

**When it pays off:** unfamiliar or large codebases, work you resume (later this session, after `/compact`, or in a new session), and long sessions with many turns. **When it doesn't:** a one-off grep on code you already know cold — reach for grep instead.

---

## Quick start

**Local (recommended)**

```bash
python3.14 -m venv ~/.vectr-env
source ~/.vectr-env/bin/activate   # Windows: ~/.vectr-env/Scripts/activate
pip install vectr
cd /path/to/your/project
vectr start
```

**Requires Python 3.14+.** To install:
- macOS: `brew install python@3.14`
- Ubuntu/Debian: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.14 python3.14-venv`
- Windows: [python.org/downloads](https://www.python.org/downloads/)

`vectr start` returns immediately. Indexing runs in the background — run `vectr status` to check progress. On first run the embedding model downloads once (~290 MB). Restart your AI code editor once to pick up the new MCP config.

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

| Editor | Config | Status |
|---|---|---|
| Claude Code | Auto — `.claude/settings.json`, guidance file, and session hooks (memory auto-injected at session start, per prompt, and before file read/edit) | **Verified** |
| Cursor | Auto — `.cursor/mcp.json` | Experimental |
| VS Code / GitHub Copilot | Auto — `.vscode/mcp.json` | Experimental |
| Windsurf | Manual — see below | Experimental |
| Cline | Manual — see below | Experimental |
| Continue | Manual — see below | Experimental |
| Codex CLI | — | Planned (post-v1) |

"Verified" means the full integration (config, guidance, and hooks) has been exercised end to end. "Experimental" means the MCP config is written and works, but the integration hasn't been run through the same verification pass. "Planned" means no support yet.

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
2. **Code embeddings** — `ibm-granite/granite-embedding-english-r2` (local, CPU-fast, overridable) maps natural-language queries to code symbols ("JWT validation" → `verify_jwt_token`). BM25 handles exact symbol names.
3. **Hybrid search** — vector similarity + BM25 combined, weighted by codebase characteristics (large/unfamiliar → semantic-heavy; small/well-documented → BM25-heavy).
4. **Symbol graph** — call edges, import chains, and HTTP routes (Flask/FastAPI/Express/Spring) are extracted and stored. `vectr_locate` uses 5 fallback strategies: exact match → suffix → same-module → unique-name → import-chain → fuzzy (edit distance ≤ 2).
5. **Working memory** — `vectr_remember` stores structured notes to SQLite + ChromaDB. `vectr_recall` does semantic search over notes — not SQL LIKE — so multi-word queries always find relevant context.
6. **MCP protocol** — 14 tools served over HTTP. Any MCP-compatible AI code editor connects without plugins.

---

## 14 MCP tools

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
| Notes exist from a prior session | `vectr_recall(query)` — semantic vector search, not substring match; two-tier (crisp index by default, expand one note with `note_id=N` or all bodies with `detail='full'`) |
| You found something worth preserving | `vectr_remember(content, tags, priority, kind, title, agent)` — `kind` controls injection: `directive` fires unconditionally every session, `task` carries current-work state, `gotcha` resurfaces when its file is touched, `finding` (default) is relevance-ranked, `reference` is a pointer; `title` labels the note in index output; `agent` attributes it to a subagent/orchestrator |
| Context is filling up | `vectr_evict_hint()` — identifies chunks vectr can re-retrieve, with the exact re-fetch ids |
| A chunk shown earlier has left your context | `vectr_fetch(ids=[...])` — deterministic, byte-verbatim re-fetch by id; no re-search, no file re-read; flags a truncation warning if the index itself stored a capped chunk |
| End of a long session, want a checkpoint | `vectr_snapshot("label")` |
| Looking for a prior checkpoint | `vectr_snapshot_list()` |
| Notes are stale after a large refactor | `vectr_forget(note_id=N)` per note, or `vectr_forget(all=true)` to clear |

Workspace-scoped notes double as a shared bus for multi-agent workflows: an orchestrator and its subagents all read and write the same note store, so a subagent can call `vectr_remember(..., agent="coder-2")` with its findings before finishing, and the orchestrator recalls them instead of re-reading the subagent's full transcript. The `agent` param is never inferred — it's explicit attribution, and it shows up as a tag in `vectr_recall` index output.

On editors with session hooks (see the [host-support matrix](#connect-to-your-ai-code-editor) for which ones), recall is injected automatically — directives and high-priority tasks at session start, semantic recall keyed to each prompt, and file-anchored gotchas before a read or edit — with observability via a `Hook injections` line in `vectr status`.

---

## CLI reference

```bash
vectr start                           # index + start daemon for current dir
vectr start /project/api              # positional workspace: a directory or .code-workspace file
vectr start --path /project/api       # specific workspace (repeatable, multi-root)
vectr start --memory-only             # working memory + hooks only — no code index, no watcher
vectr status                          # index health, chunk count, notes count
vectr status --all                    # all running instances
vectr stop /project/api               # stop one instance (same positional as start)
vectr stop --path /project/api        # stop one instance (equivalent --path form)
vectr stop --all                      # stop all instances
vectr index --path .                  # re-index without restarting daemon
vectr fetch src/auth.py:10-42         # re-fetch a chunk by exact id, verbatim
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
| C | AST | ✓ |
| C++ | AST | ✓ |
| Zig | AST | ✓ |
| All others | 200-line windows, 50-line overlap | — |

HTTP routes (Flask/FastAPI decorators, Express `app.get()`, Spring `@GetMapping`) are extracted as symbols and searchable via `vectr_locate("GET /api/users")`.

---

## Cost

| | Cost |
|---|---|
| Embedding model | $0.00 — one-time ~290 MB download, cached at `~/.cache/vectr/` |
| Re-index (10k files, first run) | ~10 min on CPU; <5 sec on subsequent runs (mtime cache) |
| Incremental re-index per changed file | ~0.5 sec |
| vectr_search / vectr_recall | $0.00 — local inference only |

---

## Security

The default is unchanged and stays the headline: **local, no API key, zero
config** — a solo developer on a personal machine. Out of the box, the daemon
binds to `127.0.0.1` only, CORS is restricted to localhost origins, each
workspace gets its own isolated DB directory, port, and process (owner-only
`0700` on POSIX), and the index and notes persist locally in `~/.cache/vectr/`.

Everything below is **opt-in**; enabling nothing changes nothing.

**Authentication** — set `VECTR_API_KEY` and every request to `/v1/*` and
`/mcp` must carry it (`X-Api-Key: <key>` or `Authorization: Bearer <key>`;
constant-time comparison; `/v1/health` stays open for liveness probes).
Generate a key with `vectr key`. When the key is set at start time, the editor
MCP configs vectr writes include the header automatically.

**Encryption at rest** — set `VECTR_ENCRYPT_KEY` (or store a passphrase in the
OS keychain: service `vectr`, username `encrypt-key`; requires
`pip install vectr[encryption]`) and note content, note titles, and snapshot
payloads are encrypted (Fernet, PBKDF2-derived key). Honest boundary: the
**code index is not encrypted** — the search engine needs readable chunk text
and vectors; protect it with OS full-disk encryption. Note tags/metadata stay
plaintext, and note embedding vectors (a lossy projection of note text) are
kept for semantic recall unless you set
`VECTR_ENCRYPT_DISABLE_NOTE_VECTORS=1`.

**Retention and audit** — notes are kept until you delete them; set
`VECTR_NOTES_TTL_DAYS` to auto-purge older notes at startup.
`vectr_forget(all=true)` / `vectr forget --all` delete notes, snapshots, and
note vectors — everything means everything. Set `VECTR_AUDIT_LOG=<path>` for a
rotating local log of index/search/remember/recall events (off by default; it
records query text — that is its purpose — and is never transmitted). Full
policy: [docs/data-handling.md](docs/data-handling.md).

**Team mode (shared instance)** — one central daemon can serve a team on one
repo: `VECTR_API_KEY=<key> vectr start --host 0.0.0.0` on the server (a
non-loopback bind **refuses to start without a key**), then
`vectr connect --url http://<host>:<port> --api-key <key> --label <you>` on
each client to point the editor at it. Working memory is shared: a note one
agent stores, every connected agent can recall; `--label` attributes notes and
audit lines. Plain limits: one shared key means every holder is an equal,
trusted peer (no roles, no per-user permissions); the server operator can read
everything; search results reference the **server's** checkout, which may
differ from your local tree; vectr speaks plain HTTP — put TLS at a reverse
proxy or tunnel if the network isn't trusted.

---

## When vectr can hurt

**Stale notes after codebase churn** — notes store file paths at write time. After a large refactor, `vectr_recall` will flag changed referenced files with `[STALE]`. Re-verify before acting, delete the stale note with `vectr_forget(note_id=N)`, or clear everything with `vectr_forget(all=true)`.

**Over-retrieval on a well-known API** — if the model already knows a framework deeply from training (React hooks, Django ORM), vectr's research overhead may exceed savings. The benchmark shows 0% improvement on `debug_descriptor_priority` — a task where the model's training knowledge was sufficient to navigate without notes.

---

## Built with

Python 3.14 · FastAPI · sentence-transformers · tree-sitter · ChromaDB · BM25 · Docker

## Author

Swapnanil Saha · [swapnanilsaha.com](https://swapnanilsaha.com)
