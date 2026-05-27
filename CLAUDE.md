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
