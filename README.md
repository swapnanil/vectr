# Vectr
> Your AI coding assistant is reading the wrong files. Vectr fixes that.

Part of the [llm-tools suite](https://swapnanilsaha.com/tools/) by [Swapnanil Saha](https://swapnanilsaha.com)

## What it solves

When Claude Code, Cursor, or Copilot needs to find relevant code, they run `ripgrep` and read entire files — consuming hundreds of tokens of irrelevant code every single query. On a 10,000-file codebase, this fills context windows fast and multiplies API costs. Vectr indexes your codebase once using code-specific embeddings and an AST-aware chunker, then serves only the 5–10 most relevant code chunks per query via MCP. The AI assistant gets the signal, not the noise.

**No API key required for basic operation.** The default embedding model runs locally.

## Quick start

**Option A — local (recommended for individual developers)**

```bash
python3 -m venv ~/.vectr-env
source ~/.vectr-env/bin/activate   # Windows: ~/.vectr-env/Scripts/activate
pip install git+https://github.com/swapnanil/vectr
cd /path/to/your/project
vectr start
```

No `.env` required. Vectr downloads the embedding model once (~550MB), indexes your codebase, and writes MCP config for Cursor and Claude Code automatically.

> **First use:** After `vectr start` completes, restart Claude Code or Cursor once so they pick up the new MCP configuration. You won't need to do this again for the same workspace.

**Option B — Docker (for servers or CI)**

```bash
git clone https://github.com/swapnanil/vectr
cd vectr
# VECTR_TARGET_PATH points to the directory you want to index
VECTR_TARGET_PATH=/path/to/your/project docker-compose up api
```

No `.env` required — local embedding is the default, no API key needed.

## Connect to your AI assistant

**Claude Code** — add to `.claude/settings.json` in your workspace:
```json
{
  "mcpServers": {
    "vectr": { "type": "http", "url": "http://localhost:8765/mcp" }
  }
}
```

**Cursor** — add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "vectr": { "url": "http://localhost:8765/mcp" }
  }
}
```

`vectr start` writes these automatically. Restart Claude Code or Cursor once after first run to load the new config.

## CLI usage

```bash
# Start daemon and index current directory
vectr start --path .

# Index a specific directory
vectr index --path /path/to/project

# Search semantically
vectr search "how are JWT tokens validated"
vectr search "database connection pool" --language typescript --n 5

# Check status
vectr status

# Stop daemon
vectr stop
```

## API usage

```bash
# Search
curl -X POST http://localhost:8765/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "JWT token validation", "n_results": 5}'

# MCP tool call (how Cursor / Claude Code use Vectr)
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
2. **High-quality embeddings** — `BAAI/bge-base-en-v1.5` bridges concept-to-code vocabulary (e.g. "JWT validation" → `verify_jwt_token`). Exact symbol names are covered by BM25.
3. **Hybrid search** — vector similarity (semantic) + BM25 (keyword) combined. Symbol names and exact strings still surface.
4. **Incremental watching** — `watchdog` re-indexes only changed files on save. The index stays fresh without full re-runs.
5. **MCP protocol** — any MCP-compatible tool can query Vectr without custom plugins.

## Supported languages

Python · JavaScript · TypeScript · Go · Rust · Java
(All others: indexed via 200-line windows with 50-line overlap)

## Cost

| Mode | Cost |
|---|---|
| Local embedding (default) | **$0.00** — one-time ~270MB model download, cached at `~/.cache/vectr/` |
| Re-index 10k files (first run) | ~10 minutes on CPU |
| Incremental re-index per changed file | ~0.5 seconds |
| Cloud (voyage-code-2) | ~$0.24 to index 10k files; fractions of a cent per query |
| Optional LLM summaries (claude-sonnet-4-6) | ~$0.003/file — 1,000 files ≈ $3 one-time |

## VS Code extension

**Prerequisites**: Node.js 18+ (for building only — not needed for the Python daemon itself)

Build the extension to get automatic daemon startup on workspace open:

```bash
cd vscode-extension
npm install
npm run compile
# Then install in VS Code: Extensions → "Install from VSIX"
```

The extension shows indexing status in the status bar and auto-starts the daemon when you open any workspace that has Vectr installed.

## Built with

Python 3.14 · FastAPI · sentence-transformers · ChromaDB · tree-sitter · watchdog · Anthropic SDK / OpenAI SDK · Docker

## Author

Swapnanil Saha · [swapnanilsaha.com](https://swapnanilsaha.com)
