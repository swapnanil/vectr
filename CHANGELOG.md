# Changelog

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
