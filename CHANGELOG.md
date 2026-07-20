# Changelog

## Unreleased

### Working memory
- New `vectr_resume` MCP tool (16th) + `vectr resume` CLI + `GET /v1/resume`:
  the most recent task note, the latest snapshot, and open gotchas with file
  anchors in one deterministic call, selected by the same shared helper
  session-start injection uses. Config: `behavior.resume.max_gotchas`
  (default 5).
- New note kind `decision` and recall sort `chronological` (oldest-first,
  dated index lines): architectural decisions accrue as notes and recall as
  an ADR-style timeline. Decisions are not auto-injected at session start.

### Hygiene
- Two staleness tests pinned file mtimes explicitly, fixing a CI-only
  coarse-clock flake.

## 1.4.0 — 2026-07-20

Search-correctness fixes, leaner MCP responses, and acceptance-corpus hardening.

### Search correctness
- Zig: struct- and enum-scoped `const`/`var` declarations are now extracted as
  symbol-graph members (previously silently dropped; function-locals stay
  excluded). Symbol schema v12.
- Qualified `Class.method` locate now resolves modifier-prefixed class
  declarations (`public class`, `export default class`, `export abstract
  class`, …) across Java/TypeScript/JavaScript.
- The low-confidence "may be unrelated" banner no longer false-fires on
  high-confidence paraphrase matches: new config key
  `ranking.notfound_floor.ce_override_min_relevance` (default 0.70) suppresses
  the zero-vocabulary trigger when the top result's cross-encoder relevance is
  confidently high. Genuine misses still banner; set above 1.0 to disable.
- `class_importance` no longer counts barrel re-export lines (`export { X }
  from`, `export * from`) as usage, removing a display-order inversion in
  re-export-heavy JS/TS codebases.
- `ranking.class_importance.lambda` default raised 0.25 → 0.35 after a
  full-corpus regression audit (recovers two known ranking regressions with
  zero regressions elsewhere).

### Leaner MCP responses
- All MCP text output renders workspace-relative paths, with the absolute
  root printed once per response header. `vectr_fetch` accepts both relative
  (new canonical) and absolute (back-compat) chunk ids. The absolute-path
  prefix previously accounted for ~9% of a default search response, ~26% of
  pointer mode, and ~42% of `vectr_evict_hint`.
- `vectr_evict_hint` renders each chunk once, in id-ready form
  `relpath:start-end  (symbol)`, with a single fetch template — ~70% of the
  old payload was duplicate serialization.
- Low-confidence pointer mode is now actually slim: the duplicated
  symbol-graph section is deduped against the pointer list and the re-fetch
  footer is dropped.
- `vectr_remember`'s tool schema trimmed (−317 tokens on tools/list) with no
  parameter, enum, default, or required-field changes.

### Hygiene
- Acceptance corpus cases now carry an `embed_model_stamp`; the harness
  reports stamp/embedder mismatches (informational) so a future embedder swap
  cannot silently stale-ify verified labels.
- README version/tool counts refreshed (15 MCP tools); generated-guidance
  template now lists `vectr_promote` and documents `vectr_remember`'s
  `triggers` argument.
- `scripts/release.sh`: one-command tag + push + GitHub release.

## 1.3.0 — 2026-07-18

Per-memory trigger engine, stdio MCP transport, instant memory readiness.

### Per-memory trigger engine
- `vectr_remember` accepts `triggers`: explicit per-note conditions for when
  a note resurfaces — `path` globs, lifecycle `event`s (session-start,
  prompt-submit, pre-edit, pre-run, pre-commit, post-compaction), exact
  `symbol` references resolved against the code symbol graph, `semantic`
  prompt-similarity with a fixed per-kind threshold, and temporal guards
  (`not_before`, `expires_visibility`, `cooldown`). Conditions AND within an
  entry and OR across entries; omitting `triggers` keeps the kind defaults.
- Trigger evaluation is wired into the live hook pipeline (session-start /
  prompt-submit / pre-tool-use / pre-compact) with a per-session fire
  ledger, cumulative injection budgets, scope enforcement, and
  double-injection prevention against the legacy relevance path.
- Provenance classes on notes with framing gates: auto-captured content is
  injected as epistemic memory, never as imperative instruction; forged
  human-provenance writes are rejected at both MCP and store boundaries.
- New `vectr_promote` MCP tool (the 15th): raise a reviewed auto-captured
  note's trust class one step (`auto` → `agent`). Promotion to `human` is
  reserved for user-side surfaces; the full chain remains available on REST.
- Kind-default scopes are resolved at write time; path triggers match both
  absolute and workspace-relative forms.

### Transport and readiness
- New foreground stdio MCP transport: `vectr mcp-stdio`.
- Two-phase service init: memory tools are live from process start on every
  transport, and warm-up notes are vector-backfilled when the embedder
  attaches — remember/recall no longer wait on model load.
- `vectr hook <event>` runs a stdlib-only fast path, cutting the per-hook
  subprocess import tax.

### Availability and correctness
- Embedding provider's torch thread pool is capped and MCP tool dispatch
  runs off the event loop; a full-workspace index or embed burst no longer
  starves concurrent requests.
- Fixed a shutdown-vs-init race in two-phase startup.
- `VECTR_WORKSPACE` pointing at a nonexistent path now fails loudly instead
  of silently indexing nothing.
- `vectr_map`'s raw-metadata path walks only indexed files (no more venv
  walks); the workspace fingerprint scan honors indexer exclusions.
- Injection packing stops at the first eviction and never backfills
  lower-ranked items; the per-turn recall relevance floor is config-driven
  (default 0.72).

### Tooling and display
- One displayed score scale per result set; resolved note scope is surfaced
  back to the caller; `vectr_status` nudges about stale task notes; the
  instruction-style label is renamed `memory-first`; a failing acceptance
  case can no longer crash the harness run.

### Research artifacts
- New `research/` directory: published evaluation artifacts for the
  brain-memory work — controlled-matrix and forced-compaction decay-probe
  protocols, graders, and complete run archives with per-directory READMEs.
  Not part of the PyPI package.

## 1.2.0 — 2026-07-13

Proactive context injection and daemon availability under load. All new
behavior is opt-in; with no new flags or consent given, behavior is
unchanged.

### Proactive context (new, opt-in)
- New `vectr proxy` command: a localhost, Anthropic-API-shaped proxy that
  deterministically injects relevant working-memory notes into an AI
  agent's request context when their trigger conditions match — no reliance
  on the agent voluntarily calling recall. Injection is consent-gated (a
  proactive master switch plus per-launch consent for the proxy channel)
  and fully observable: injection counts and end-to-end state are exposed
  in `/v1/status` and rendered in the proxy banner.
- Scored recall behind a new `/v1/proactive` endpoint; injections are
  budgeted, deduplicated, and fail open (an injection-path error never
  blocks the underlying request — it is logged and bypassed).
- Org-wide artifact cache and exact-match response cache wired into the
  proactive path.

### Daemon availability
- `/v1/index` now runs off the event loop (threadpool), so a full-workspace
  index call no longer blocks every other request for its duration.
- New `reindex_in_progress` field in `/v1/status`: a lock-free, always-cheap
  signal that bulk index work (an explicit index or the watcher's coalesced
  batch) is running right now.
- Per-language index statistics no longer trigger a full metadata scan on
  every status call while the index is changing; chunk totals read the
  vector store's native count directly.

### Watcher
- Live file events now honor `.gitignore` exactly like the bulk indexer
  (previously only `.vectrignore` was consulted on create/modify/delete/move
  events, so a gitignored file could enter the index through a live edit).

### Fixes
- `vectr forget --all` sweeps the current cache layout, not only the legacy
  nested layout.
- The proxy banner's status probe retries with a longer timeout instead of
  reporting a transient failure.

## 1.1.1 — 2026-07-12

### Indexing
- Indexing now streams embeddings into the vector store batch by batch
  instead of accumulating every embedding for the whole workspace in memory
  before writing. Both embedding passes (content and purpose vectors)
  previously held full-workspace embedding lists concurrently — gigabytes of
  memory on large workspaces, pushing memory-constrained machines into swap
  and slowing the second pass several-fold while it paged. Peak memory for
  this step is now one embedding batch. Indexed content is unchanged: chunk
  ids, documents, metadata, and embedding values are identical, so existing
  indexes remain valid and no reindex is required.

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

### Proactive context (experimental, off by default)
- New `vectr proxy` command runs a localhost Anthropic-shaped API proxy the
  agent harness targets with `ANTHROPIC_BASE_URL`. It forwards `/v1/messages`
  (and everything else) to the real API transparently — streaming SSE and
  tool_use pass through byte-for-byte, and the upstream API key is forwarded
  untouched and never stored or logged. Localhost-only: a non-loopback bind is
  refused. Bypass it at any time by unsetting `ANTHROPIC_BASE_URL`.
- When injection is enabled and the workspace daemon is running, the proxy
  appends deterministic proactive context (matched working-memory notes /
  structural matches) **after the last prompt-cache breakpoint**, so existing
  cache prefixes are never invalidated. Injection is fail-open: if the
  intelligence layer errors or exceeds a tight time budget, the request is
  forwarded unmodified; a proxy that cannot reach upstream returns an honest
  upstream-shaped error.
- Injection triggering is deterministic — structural exact file-path matches +
  a numeric cosine floor + additive packing — with a per-request item/char
  budget and per-session dedup/cooldown. No keyword/regex classification of
  conversation content anywhere.
- New `POST /v1/proactive` daemon route returns packed context for an assembled
  window (used by the proxy); scored recall (`recall_scored`) surfaces the
  per-note cosine similarity the semantic path already computes.
- Org-wide artifact cache (`proactive.cache`, off by default): caches
  `/v1/search` and scored-recall results keyed by exact identity + the current
  index epoch, so a re-index or note change invalidates automatically. On a
  team instance the cache is shared by every connected client. Exact-match
  local LLM-response caching in the proxy (`proactive.cache.response_cache`,
  off by default) serves a cached response only for a byte-identical request
  within a TTL; semantic-similarity response caching is deliberately not
  offered (see the design doc's cache-safety analysis).
- `vectr status` gains proactive-injection counts and artifact-cache metrics
  (hits/misses/hit-rate/entries/estimated tokens saved) when either is active.
  A `PROACTIVE_INJECT` audit event records metadata only (channel, item count,
  anchor ids) — never conversation text or note bodies.
- Config: new `proactive:` block in `config.yaml`; env overrides under the
  `VECTR_PROACTIVE*` prefix. All off by default; with nothing set, behavior is
  unchanged.

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
