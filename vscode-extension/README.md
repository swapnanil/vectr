# Vectr — Semantic Codebase Search

Zero-config local codebase indexer with persistent working memory, served over MCP to any MCP-compatible AI code editor.

This extension auto-starts the Vectr daemon when you open a workspace, so your AI code editor gets:

- **Semantic search** over your whole codebase — describe what you're looking for in plain English instead of grepping for names you don't know yet.
- **Persistent working memory** — findings stored during one session are recalled in <50ms later in that session, after context compaction, or in a future session.
- **Symbol graph** — definition lookup and caller/callee tracing without leaving the editor's tool loop.

Everything runs locally. No API key, no code leaves your machine.

## Requirements

- Python 3.14+ with the `vectr` package installed:

```bash
pip install git+https://github.com/swapnanil/vectr
```

On first run the embedding model (~440 MB) downloads once and is cached.

## Commands

| Command | What it does |
|---|---|
| `Vectr: Show Indexing Status` | Index health, chunk count, note count |
| `Vectr: Re-index Workspace` | Force a re-index without restarting the daemon |
| `Vectr: Stop Daemon` | Stop the workspace's Vectr instance |

## Settings

| Setting | Default | Description |
|---|---|---|
| `vectr.port` | `8765` | Preferred starting port (scans upward if taken) |
| `vectr.autoStart` | `true` | Start the daemon when a workspace opens |
| `vectr.embedModel` | `ibm-granite/granite-embedding-english-r2` | Local embedding model; any sentence-transformers model ID works |

## Learn more

Full documentation, benchmarks, and measured costs: [github.com/swapnanil/vectr](https://github.com/swapnanil/vectr)

MIT licensed.
