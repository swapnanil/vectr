# Changelog

## 1.0.3 — 2026-07-11

- Added the MCP Registry ownership marker (`mcp-name: io.github.swapnanil/vectr`) to the README as an HTML comment — required by registry.modelcontextprotocol.io to validate that the PyPI package and the registry entry belong to the same publisher.
- `server.json` added for the Official MCP Registry listing (`pypi` package, `streamable-http` transport at `http://localhost:{port}/mcp`).
- README version badge and version line corrected (still said 1.0.0).

## 1.0.2 — 2026-07-11

- PyPI project page now renders the README (`readme` was missing from package metadata), plus license, author, and project URL metadata.
- Removed the vestigial `LLM_MODEL` env var, `agent/llm_client.py`, and the dead `model` field it echoed into `/v1/health`, `/v1/index`, `/v1/search`, and `/v1/status` responses. Vectr makes no LLM calls; the field had no consumers.
- `.env.example` updated to the shipped defaults: `ibm-granite/granite-embedding-english-r2` embedding model; removed the unused `ANTHROPIC_API_KEY` entry.
- `.vectrignore` untracked (repository-local indexing config, not product content).

## 1.0.1 — 2026-07-11

- Package description now covers both halves of the product: semantic codebase search and persistent working memory. The 1.0.0 description mentioned only the indexer.
- Install instructions updated to `pip install vectr` (PyPI release) in README, extension README, and docs page.
- Personal development configuration untracked from the repository (`CLAUDE.md`, `.mcp.json`, `.cursor/mcp.json`); local absolute paths in benchmark data and harness scripts genericized.

## 1.0.0 — 2026-07-08

**Semantic search**
- Hybrid dense (`ibm-granite/granite-embedding-english-r2`, local and overridable) + BM25 retrieval, weighted by codebase size/documentation characteristics.
- Dual-vector indexing: a body-stripped "purpose" vector (signature + docstring) alongside the full-body vector, closing the embedding-dilution gap where a symbol's own doc paraphrase of a query fails to surface it in the dense pool.
- Symbol graph with call edges, import chains, and HTTP route extraction (Flask/FastAPI/Express/Spring); `vectr_locate` (5 fallback strategies) and `vectr_trace` for callers/callees.
- AST-aware chunking across 7 languages (Python, JavaScript, TypeScript, Go, Rust, Java, Zig) plus C and C++; all other file types fall back to overlapping window chunking.

**Working memory**
- Five note kinds (`directive`, `task`, `gotcha`, `finding`, `reference`), each with distinct injection semantics — directives fire unconditionally, gotchas resurface when their anchored file is touched.
- Two-tier `vectr_recall`: a token-bounded index by default, full note bodies on request (`note_id=` or `detail='full'`).
- Session-start boot injection of directives and high-priority tasks, recency-ordered.
- `vectr_snapshot` / `vectr_snapshot_list` to checkpoint and browse working-memory state.
- Multi-agent shared memory: workspace-scoped notes act as a shared bus for orchestrator/subagent handoff, with explicit (never inferred) `agent` attribution on `vectr_remember`.

**Context relief**
- `vectr_fetch`: deterministic, byte-verbatim re-fetch of a previously-seen chunk by id, with a truncation warning when the index's own storage cap capped the original chunk.
- `vectr_evict_hint`, with recency-ordered re-fetch ids, so the calling model knows what it can safely drop from context.
- Remember nudges with dual-gate escalation (chunk count and token count) so a light reminder scales into a stronger banner only once both thresholds are crossed.

**Editor integration**
- Zero-config MCP setup: auto-written config for Claude Code, Cursor, and VS Code / GitHub Copilot; manual config documented for Windsurf, Cline, and Continue.
- Auto-generated `CLAUDE.md` guidance template describing the tool surface and when to use each tool.
- Claude Code session hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PreCompact`) for automatic recall injection, with an injection-observability status line.

**Ops**
- `--memory-only` daemon mode: working memory + hooks without code indexing or the file watcher, for actively-edited or performance-sensitive projects.
- File watcher burst governor and RSS self-limit to bound resource use during large or rapid file-change bursts.
- Multi-workspace instance registry (`vectr status --all`, `vectr stop --all`) and per-workspace status surfaces.
