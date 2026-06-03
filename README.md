# Vectr
> Your AI coding assistant is reading the wrong files. Vectr fixes that.

Part of the [llm-tools suite](https://swapnanilsaha.com/tools/) by [Swapnanil Saha](https://swapnanilsaha.com)

## What it solves

When an AI code editor needs to find relevant code, it runs `ripgrep` and reads entire files — consuming hundreds of tokens of irrelevant code every single query. On a 10,000-file codebase, this fills context windows fast and multiplies API costs. Vectr indexes your codebase once using code-specific embeddings and an AST-aware chunker, then serves only the 5–10 most relevant code chunks per query via MCP. The AI editor gets the signal, not the noise.

**No API key required for basic operation.** The default embedding model runs locally.

## Quick start

**Option A — local (recommended for individual developers)**

```bash
python3.14 -m venv ~/.vectr-env
source ~/.vectr-env/bin/activate   # Windows: ~/.vectr-env/Scripts/activate
pip install git+https://github.com/swapnanil/vectr
cd /path/to/your/project
vectr start
```

**Requires Python 3.14+.** Check with `python3 --version`. To install:
- **macOS**: `brew install python@3.14`
- **Ubuntu/Debian**: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt-get install python3.14 python3.14-venv`
- **Windows**: download from [python.org/downloads](https://www.python.org/downloads/)

No `.env` required. Vectr downloads the embedding model once (~440MB), indexes your codebase, and writes MCP config for your AI code editor automatically.

> **First use:** `vectr start` returns immediately — indexing runs in the background. On the very first run it downloads the embedding model (~440MB, takes a few minutes on a slow connection). Run `vectr status --path .` to check progress; search works once `indexed_files > 0`. If `indexed_files` stays at 0 for more than 10 minutes, check the daemon log shown in the `vectr start` output. Restart your AI code editor once to pick up the new MCP configuration.

**Option B — Docker (for servers or CI, not local IDE use)**

```bash
git clone https://github.com/swapnanil/vectr
cd vectr
docker-compose up api
```

The container exposes port 8765. No `.env` required — local embedding is the default. Note: the Docker path does not auto-write IDE config files. Use Option A for local IDE integration; use Docker for CI pipelines or remote search endpoints.

## Connect to your AI assistant

`vectr start` writes the config files below automatically. Restart your AI code editor once after first run to load the new MCP server.

**Claude Code** — auto-written to `.claude/settings.json`
**Cursor** — auto-written to `.cursor/mcp.json`
**VSCode / GitHub Copilot** — auto-written to `.vscode/mcp.json`

<details>
<summary>Manual setup (if auto-write didn't work)</summary>

**Claude Code** — `.claude/settings.json`:
```json
{
  "mcpServers": {
    "vectr": { "type": "http", "url": "http://localhost:8765/mcp" }
  }
}
```

**Cursor** — `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "vectr": { "url": "http://localhost:8765/mcp" }
  }
}
```

**VSCode / GitHub Copilot** (1.99+) — `.vscode/mcp.json`:
```json
{
  "servers": {
    "vectr": { "type": "http", "url": "http://localhost:8765/mcp" }
  }
}
```

**Windsurf (Codeium)** — add to `~/.codeium/windsurf/mcp_settings.json`:
```json
{
  "mcpServers": {
    "vectr": { "serverUrl": "http://localhost:8765/mcp" }
  }
}
```

**Continue.dev** — add to `.continue/config.json` under `mcpServers`:
```json
{
  "mcpServers": [{
    "name": "vectr",
    "transport": { "type": "http", "url": "http://localhost:8765/mcp" }
  }]
}
```

**Cline** — add via the Cline MCP settings UI, or edit `cline_mcp_settings.json` in VSCode's global storage.

</details>

## CLI usage

```bash
# Start daemon for current directory (returns immediately — runs in background)
vectr start --path .

# Multiple IDE windows: each workspace gets its own port automatically
vectr start --path /project/api
vectr start --path /project/frontend

# List all running instances
vectr status --all

# Stop the instance serving a specific workspace
vectr stop --path /project/api

# Stop all running instances
vectr stop --all

# Restart a workspace
vectr restart --path /path/to/project

# Delete all working-memory notes for a workspace (after a large refactor, or to start fresh)
vectr forget --path /path/to/project

# Write CLAUDE.md + .mcp.json + .claude/settings.json without starting the server
vectr init --path /path/to/project

# Index a specific directory
vectr index --path /path/to/project

# Search semantically
vectr search "how are JWT tokens validated"
vectr search "database connection pool" --language typescript --n 5
```

## API usage

```bash
# Search
curl -X POST http://localhost:8765/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "JWT token validation", "n_results": 5}'

# MCP tool call (how AI code editors use Vectr)
curl -X POST http://localhost:8765/mcp/tools/call \
  -H "Content-Type: application/json" \
  -d '{"name": "vectr_search", "arguments": {"query": "authentication middleware"}}'
```

## Input / Output

**Query**: `"how are JWT tokens validated"`

**Response**:
```json
{
  "results": [
    {
      "file": "src/auth/middleware.py",
      "lines": "42-67",
      "symbol": "verify_jwt_token",
      "language": "python",
      "score": 0.94,
      "content": "def verify_jwt_token(token: str) -> dict:\n    try:\n        payload = jwt.decode(token, settings.SECRET_KEY, ...)"
    }
  ],
  "query_time_ms": 18,
  "chunks_searched": 14523
}
```

AI assistants get file path, line numbers, function name, and the exact code — nothing else.

## How it works

1. **AST-aware chunking** — `tree-sitter` parses each file and splits at function/class boundaries. No chunk ever breaks mid-logic.
2. **High-quality embeddings** — `Snowflake/snowflake-arctic-embed-m-v1.5` bridges concept-to-code vocabulary (e.g. "JWT validation" → `verify_jwt_token`). Exact symbol names are covered by BM25.
3. **Hybrid search** — vector similarity (semantic) + BM25 (keyword) combined. Symbol names and exact strings still surface.
4. **Working memory** — `vectr_remember` stores structured notes (key files, edge cases, what's still missing) to a persistent SQLite store. The LLM drops explored code from context and recalls it on demand in <50ms — whether later in the same session or in a future one. Re-exploration is never needed.
5. **MCP protocol** — any MCP-compatible AI code editor can query Vectr without custom plugins.

## MCP tools

Vectr exposes 11 tools to your AI code editor. Each is tuned for a specific situation:

| You know… | You need… | Tool |
|---|---|---|
| A concept or behaviour (no name) | Code related to it | `vectr_search("description")` |
| A symbol name, not its file | Where it's defined | `vectr_locate("SymbolName")` |
| A symbol name | Who calls it / what it calls | `vectr_trace("symbol_name")` |
| Nothing about the codebase | Architectural overview | `vectr_map()` |
| vectr_map returned raw metadata | Save your synthesised summary | `vectr_map_save(summary)` |
| Prior work exists on this codebase | Recall notes (same or prior session) | `vectr_recall()` after `vectr_status` shows notes_count > 0 |
| A key finding to preserve | Store it, drop from context, recall on demand | `vectr_remember(content)` |
| End of a long session | Seal all notes as a checkpoint | `vectr_snapshot("label")` |
| Looking for a prior checkpoint | List all saved checkpoints | `vectr_snapshot_list()` |
| Context window is filling up | Find chunks safe to drop | `vectr_evict_hint()` |
| Notes are stale or wrong | Clear all working memory | `vectr_forget()` |
| — | Check index health / chunk count | `vectr_status()` |

The `vectr init` / `vectr start` commands write a `CLAUDE.md` with this table into your workspace, so your AI assistant always knows which tool to reach for.

## When vectr can hurt

Vectr's working memory trades accuracy for speed. Two situations where it can produce wrong answers:

**Stale notes after codebase churn** — `vectr_remember` notes store file paths and line numbers at the time they were written. If you refactor significantly, a note might reference a deleted function or a path that has moved. `vectr_recall` automatically flags notes whose referenced files have changed (`[STALE]` marker). When you see this warning: re-verify the note before acting on it.

**Over-reliant recall on a refactored codebase** — if the codebase has changed substantially since the notes were written, `vectr_recall` may return confident-sounding notes that no longer reflect reality. The fix: after a large refactor, run `vectr_remember` again for the affected areas, or use `vectr_forget` to discard outdated notes.

## Benchmarks

Two-phase benchmark: Phase 1 (Research) stores notes with `vectr_remember`; Phase 2 (Implementation) opens cold, calls `vectr_recall()`, and implements. Vanilla Phase 2 re-reads from scratch.

**Run 1 — Django (familiar codebase, 3,020 files):**

| Task | P2 token savings | P2 cost savings |
|---|---:|---:|
| `custom_field` (deep ORM internals) | −24% | −60% |
| `rate_limit_middleware` | −3% | ~0% |
| `async_signals` (well-known API) | +16% | worse |

Finding: vectr helps where re-discovery is hard. On APIs the model already knows from training, P1 overhead exceeds savings.

**Run 2 — Apache Camel (enterprise Java, 5,856 files, unfamiliar internals):**

| Task | Vanilla P2 | Vectr P2 | P2 cost savings |
|---|---|---|---:|
| `custom_component` | $0.56, **0 bytes produced** | $0.36, 9,398 bytes (5 files) | −35% |
| `route_policy` | $1.15, 430s, 59 tools | $0.35, 177s, 16 tools | −70% |
| `type_converter` | $0.48, 187s | $0.20, 86s | −57% |
| **Totals** | **$2.19, 135 P2 tools** | **$0.92, 38 P2 tools** | **−58%** |

Grand total P2 savings: **−40% input tokens · −58% cost · −72% tool calls · −39% wall time.**

The mechanism: `vectr_recall()` returns structured notes in ~200 tokens, replacing hundreds of re-discovery tool calls. On `custom_component`, vanilla spent all 51 P2 tool calls re-exploring and produced nothing; vectr used 1 recall + 5 writes.

Full results: [`benchmarks/`](benchmarks/)

## Supported languages

Python · JavaScript · TypeScript · Go · Rust · Java
(All others: indexed via 200-line windows with 50-line overlap)

## Cost

| Mode | Cost |
|---|---|
| Local embedding (default) | **$0.00** — one-time ~440MB model download, cached at `~/.cache/vectr/` |
| Re-index 10k files (first run) | ~10 minutes on CPU |
| Incremental re-index per changed file | ~0.5 seconds |
| `vectr_map` codebase summary | Your AI editor writes it once — cost is your editor's API usage, not vectr's |

## Security scope

Vectr v1 is designed for a **solo developer on a personal machine**. The codebase index and working notes are stored locally, never transmitted externally, and isolated per workspace. It is not designed for multi-user environments, shared servers, or enterprise deployments without additional hardening.

**What this means in practice:**
- The MCP server binds to `127.0.0.1` only — not reachable from other hosts on the network
- CORS is restricted to `localhost` and `127.0.0.1` origins — browser-based requests from external pages are rejected
- Each workspace gets its own isolated DB directory, port, and daemon process
- No API key authentication in v1 — any local process can query the MCP server
- Index data and working notes persist locally in `~/.cache/vectr/` indefinitely

If you need multi-user access, authentication, or encryption at rest, those are enterprise requirements out of scope for v1.

## Built with

Python 3.14 · FastAPI · sentence-transformers · tree-sitter · ChromaDB · Docker

## Author

Swapnanil Saha · [swapnanilsaha.com](https://swapnanilsaha.com)
