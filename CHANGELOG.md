# Changelog

## 1.1.0 — 2026-07-11

Security and shared-deployment features. All opt-in; with none of the new
environment variables or flags set, behavior is unchanged — local, keyless,
zero-config stays the default.

### Authentication
- API key comparison now uses a constant-time comparison (`hmac.compare_digest`)
  instead of `!=`, removing a response-timing side channel. `VECTR_API_KEY`
  protects both the REST `/v1` routes and the `/mcp` endpoint; `/v1/health`
  stays open for liveness probes; the key is never echoed in responses or logs.
- New `vectr key` command prints a fresh high-entropy key (stdout) with usage
  guidance (stderr); vectr never persists it. Generated keys never start with
  `-` (a leading dash made `--api-key <key>` parse as a flag and fail); the
  usage guidance shows the always-safe `--api-key=<key>` form.
- When `VECTR_API_KEY` is set at start time, the editor MCP configs vectr
  writes (`.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`) include the
  `X-Api-Key` header so the editor keeps reaching its own authenticated daemon.
  These files then hold the key in plaintext; the CLI and docs warn to treat
  them as secrets and keep them out of shared version control.

### Team mode (shared central instance)
- `vectr start --host <addr>` selects the daemon bind address (default
  `127.0.0.1`). A non-loopback bind refuses to start unless `VECTR_API_KEY` is
  set — a network-reachable index is never served unauthenticated.
- New `vectr connect --url <url> [--api-key <key>] [--label <name>]` configures
  the local editor to use a remote vectr instance: writes the MCP configs with
  the auth header (and optional `X-Vectr-Client` attribution label) and the
  guidance block, spawning no local daemon. Working memory on the central
  instance is shared: a note one connected agent stores, every other can
  recall. Search results reference the server's indexed checkout.
- The client label attributes notes (author) and audit-log lines.
- Notes DB connections set an SQLite busy timeout so concurrent writes from
  multiple clients wait for the lock instead of failing.

### Encryption at rest
- `VECTR_ENCRYPT_KEY` now also encrypts note titles (previously plaintext, and
  derived from the first content line — leaking note content) and snapshot
  payloads (previously plaintext JSON embedding full decrypted note text).
  Reads are tolerant: pre-existing plaintext rows keep working.
- The passphrase can be stored in the OS keychain (service `vectr`, username
  `encrypt-key`) instead of the environment; the env var wins when both exist.
- New `VECTR_ENCRYPT_DISABLE_NOTE_VECTORS=1` omits note embedding vectors (a
  lossy plaintext projection of note text) for the strictest posture; recall
  falls back to exact-text matching.
- Honest boundary, unchanged: the code index (chunk text + vectors) is NOT
  encrypted — documented in docs/data-handling.md and the README.

### Data handling and retention
- The audit log is now strictly opt-in: `VECTR_AUDIT_LOG` unset means nothing
  is recorded (previously it silently defaulted to `~/.vectr/audit.log`,
  recording every recall query undisclosed). When enabled it also records
  INDEX and SEARCH events, and rotating-handler initialization is now
  race-free under concurrent writes.
- Purge is now complete: `vectr_forget(all=true)` / `POST /v1/forget` /
  `/v1/memory/clear` also delete the workspace's snapshots (whose payloads
  embed note contents), and `vectr forget --all` additionally clears snapshots
  and note embedding vectors across all workspaces. Previously all of these
  deleted only the notes table.
- `VECTR_NOTES_TTL_DAYS` (existing startup TTL purge) is now documented and
  covered by tests; unset = notes are kept until deleted.
- New data-handling policy: docs/data-handling.md — what vectr stores, where,
  plaintext vs encrypted, retention, deletion, team-mode caveats.

### Filesystem
- `~/.cache/vectr/` and `~/.vectr/` (and per-workspace subdirectories) are
  created owner-only (0700) on POSIX systems; existing directories are
  tightened at startup.

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
