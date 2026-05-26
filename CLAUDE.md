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
.venv/bin/python3.14 -m pytest tests/ --ignore=tests/test_integration.py
```

### HTML / blog files
Do not edit `canonical-blog.md`, GitHub Pages files, or any HTML. These are updated only when vectr is feature-complete.

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
.venv/bin/python3.14 -m pytest tests/ --ignore=tests/test_integration.py

# Run integration tests (requires real model download ~440MB)
.venv/bin/python3.14 -m pytest -m integration tests/test_integration.py
```
