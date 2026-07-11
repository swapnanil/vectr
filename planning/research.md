# Proactive participation — research

Status: research of record for the experimental proactive-participation workstream
(internal codename "god mode"; the codename never appears in product copy — see
`design.md` §Naming). Scope: verify, with evidence, whether and how vectr can move from
pull-based (agent asks) to proactive (the right note / search result arrives when it is
relevant), in vectr's **localhost / solo** mode only.

Honesty bar matches `security-features-design.md`: every claim below is tagged with its
evidence, and unknowns are labelled as unknowns. Nothing is oversold. File:line references
point into this worktree (`feature/experimental-godMode`, from `main@97f6dca`). Web sources
are dated to the day they were fetched (2026-07-11).

No content from any real conversation transcript on this machine appears anywhere in this
document. Only on-disk **schema** (field names, value types, enum values, naming rules) and
fully synthetic examples are recorded.

---

## 0. Executive summary — verdict per channel

The feature has two halves that must not be conflated:

- **Intelligence** — *what* to proactively say. Computed by matching a rolling window of the
  live conversation against (1) the note store and (2) the code index, plus deterministic
  structural matches (file paths / symbol names appearing in tool traffic). This half is
  almost entirely buildable today and reuses machinery vectr already ships.
- **Delivery** — *when and how* the intelligence reaches the model or the user. This is the
  constrained half; it is entirely gated by what Claude Code exposes.

| # | Channel | Exists today? | Model-facing? | Determinism | Verdict |
|---|---------|---------------|---------------|-------------|---------|
| 1 | `UserPromptSubmit` hook → `additionalContext` | **Yes** (vectr already uses it) | Yes | Deterministic, event-fired | **ADOPT — Phase 1 core.** Upgrade its intelligence from single-prompt to rolling-window. |
| 2 | `SessionStart` hook → `additionalContext` | **Yes** (vectr uses it for boot) | Yes | Deterministic, event-fired | **ADOPT — Phase 1.** Already the boot channel; add compaction re-prime awareness. |
| 3 | `PreToolUse` hook → `additionalContext` | **Yes** (vectr uses it for gotchas) | Yes | Deterministic, event-fired | **ADOPT — Phase 1.** Richest structural signal (file path + tool). Broaden beyond Edit\|Write. |
| 4 | `PostToolUse` hook → `additionalContext` / `updatedToolOutput` | **Yes, unused by vectr** | Yes | Deterministic, event-fired | **ADOPT — Phase 1.** The highest-frequency mid-loop injection point available synchronously. |
| 5 | `systemMessage` (user-facing) on the above hooks | **Yes** | No (user only) | Deterministic | **ADOPT — Phase 1, sparingly.** The "surface to the user" path. |
| 6 | Async out-of-band push into a live session (SendMessage / MCP server→client / Agent SDK inject) | **No sanctioned CLI path** | n/a | n/a | **REJECT for v1.** Open upstream requests; not available in the Claude Code CLI. Revisit if upstream lands it. |
| 7 | Direct transcript-file injection (write synthetic JSONL) | n/a | No | n/a | **REJECT — confirmed dead end.** The live session holds context in-process and never re-reads the file. |
| 8 | `ANTHROPIC_BASE_URL` localhost API proxy (the literal "in the middle") | **Yes, supported** | Yes | Deterministic, per-request | **DEFER — Phase 3.** Highest power, highest risk (cache, key handling, availability). Design it, ship it last. |

**Headline finding.** A proactive feature with real product value is buildable in Phase 1
using only sanctioned, already-wired hook channels — because hooks provide **delivery** (the
sanctioned moments injection is allowed) while a rolling-window read of the transcript
provides **intelligence** (a far richer understanding than the single hook payload vectr
matches against today). Truly autonomous, mid-loop, un-prompted push is **not available**
today; the honest v1 is event-synchronous.

**Second headline finding.** Semantic search over the note store — called out in the brief
as a likely prerequisite gap — **already exists and ships today**. `WorkingContextStore.recall`
embeds the query and does cosine search over a ChromaDB `working_memory` collection with a
similarity floor. The real prerequisite work is narrower: matching against a *rolling
conversation window* (not a single query string) and against the *code index*, and exposing
scored results to a gating layer. See §3.

---

## 1. Stream source — Claude Code session transcripts

**Method.** On-disk structure of Claude Code transcripts on this machine was surveyed at the
schema level only (field names, types, enum values, file-naming rules, write cadence). No
message content was read into this document.

### 1.1 Layout and naming

- Root: `~/.claude/projects/`.
- One directory per workspace, named by a **slug** = the workspace absolute path with every
  path separator and dot collapsed to `-` (leading `/` → leading `-`, each `/` → `-`, each
  `.` → `-`). Alphanumerics preserved. The encoding is **lossy** (a literal `-` and a `/`/`.`
  are indistinguishable), so it cannot be reliably reversed — but it is deterministic in the
  **forward** direction, which is all vectr needs (vectr knows the workspace absolute path and
  can compute the slug).
  - Synthetic example: cwd `/srv/app` → slug dir `-srv-app`.
- Inside a slug dir: `<session-id>.jsonl` transcripts, where `<session-id>` is a lowercase
  UUIDv4. The filename stem equals the `sessionId` recorded inside the file (verified: 0
  mismatches across all files sampled).
- Optional sibling `<session-id>/` sidecar directory holds `tool-results/` (offloaded large
  tool outputs, `<id>.txt`) and `subagents/` (`agent-<hex>.jsonl` + `agent-<hex>.meta.json`).
- A `memory/` directory of `*.md` files may also exist (unrelated to transcripts).
- No per-project `config.json` / `summary.json`; session titling lives inside the JSONL as
  dedicated record types (`ai-title`, `last-prompt`).

### 1.2 JSONL record schema

One JSON object per line, heterogeneous, discriminated by a top-level `type` field.

- **`type` enum observed:** `user`, `assistant`, `system`, `attachment`,
  `file-history-snapshot`, `ai-title`, `last-prompt`, `mode`, `pr-link`, `frame-link`,
  `queue-operation`. (Older builds also emit `summary`; not observed on this build.)
- **`user` / `assistant` records** carry: `type`, `uuid`, `parentUuid` (string, or `null` at
  roots), `sessionId`, `message` (nested Anthropic Messages object), `timestamp` (ISO-8601),
  `cwd`, `gitBranch`, `version`, `slug`, `userType` (`external`), `isSidechain` (bool),
  plus role-specific fields (`user` also has `isMeta`, `isCompactSummary`, `toolUseResult`,
  `permissionMode`, `promptId`; `assistant` also has `requestId`, model/attribution/error
  fields).
- **`message.content[]` block types:** `text` (`text`), `thinking` (`thinking` + `signature`
  — assistant reasoning **is** present), `tool_use` (`id`, `name`, `input`), `tool_result`
  (`tool_use_id`, `content`, `is_error`), `image`, `document`, `fallback`.
- **`message.usage`** (assistant): `input_tokens`, `output_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`, …
- **Threading:** `uuid` → `parentUuid` forms a parent-linked chain; `logicalParentUuid` links
  across compaction boundaries; `sessionId` ties records to the file.

The fields vectr's matching engine needs are exactly the durable, load-bearing ones:
`type`, `message.role`, `message.content[]` (`text` / `tool_use.name` / `tool_use.input` /
`tool_result.content`), `cwd`, `sessionId`, `timestamp`, `uuid`/`parentUuid`. These are
stable across builds. The volatile fields (metadata record types, attribution) can be
ignored.

### 1.3 Write cadence and tail viability

- **Live streaming append — safe to tail.** Records are line-delimited and appended as each
  event completes; in the actively-modified transcript the newest record timestamp tracked
  the file mtime within seconds.
- Timestamps are non-decreasing for the large majority of adjacent records; the out-of-order
  minority comes from interleaved sidechain/subagent records and metadata records that carry
  no timestamp. A reader should thread by `uuid`/`parentUuid` (or simply take the last N
  *conversational* records) rather than assume strict time monotonicity.
- **Records per turn:** a bare turn ≈ 2 records; each tool call adds a `tool_use` and a
  `tool_result` record, so tool-heavy turns produce tens of records (observed average ~33
  records per human turn in a tool-heavy session).

### 1.4 Active-session identification

- Primary signal: **newest mtime** among the `.jsonl` files in the relevant slug dir(s).
- No lock / PID / `.active` marker exists on disk (searched; none found).
- Corroborating signals: the `cwd` field inside records (match to the daemon's workspace),
  `gitBranch`, and recency of the same-UUID sidecar dir.
- **Robust rule for vectr:** compute the slug(s) for the daemon's `workspace_root` (and any
  extra roots of a multi-root workspace), take the newest-mtime `.jsonl` under them, and
  confirm by reading its most recent record's `cwd`.
- **Simpler path that sidesteps identification entirely (Phase 1):** the Claude Code hook
  payload already contains `transcript_path` for the session that fired the hook. When vectr
  is invoked *from a hook*, it does not need to discover the active session — it is handed the
  exact file. See §2.1 and the design's on-demand-window decision.

### 1.5 Other local harnesses (docs-level only)

Claude Code is the v1 target and these internal docs may say so. Other agent harnesses expose
comparable local transcript streams to varying degrees; a survey at product-integration depth
is out of scope for this doc. The design isolates a **stream-adapter interface** so a second
harness can be added later without touching the matching or gating layers, but no non-Claude
adapter is specified here. [UNKNOWN — deferred, not blocking.]

---

## 2. Injection / participation channels — evidence and verdicts

### 2.1 Synchronous hooks (channels 1–5) — REAL, first-class, partly already wired

**Evidence (official):** Claude Code hooks reference, https://code.claude.com/docs/en/hooks
(fetched 2026-07-11).

- A hook returns `hookSpecificOutput.additionalContext` to inject text **into the model's
  context** (wrapped in a `<system-reminder>`; not shown as a visible chat entry). It returns
  a top-level `systemMessage` to show a string **to the user** (the model does not see it).
  Both, plus plain stdout, are capped at **10,000 characters**.
- `additionalContext` (model-facing) is supported on **SessionStart, UserPromptSubmit,
  PreToolUse, PostToolUse** (and PostToolUseFailure / PostToolBatch / Stop / SubagentStop).
  Session-teardown / notification events (SessionEnd, PreCompact, PostCompact, Notification,
  FileChanged) can only return `systemMessage`.
- `UserPromptSubmit` input (stdin JSON): `session_id`, `prompt_id`, **`transcript_path`**,
  `cwd`, `permission_mode`, `hook_event_name`, `prompt`. **Timeout: 30 s default** for this
  event; on timeout the output (incl. `additionalContext`) is discarded and the prompt
  reaches the model without it. Exit 2 blocks/erases the prompt.
- `SessionStart` runs on `startup | resume | clear | compact` matchers; supports
  `additionalContext` and can also set `initialUserMessage`, `sessionTitle`, `watchPaths`.
- `PreToolUse` can inject `additionalContext`, rewrite the call (`updatedInput`), or block
  (`permissionDecision: deny` with a reason surfaced to the model). `PostToolUse` can inject
  `additionalContext` **and** replace the tool output (`updatedToolOutput`) before the model
  sees it.

**What vectr already ships (verified in this worktree):**

- `main.py:523` `_write_claude_hooks` installs four hook groups into
  `<workspace>/.claude/settings.json`: `SessionStart` (matcher `startup|resume|clear|compact`),
  `UserPromptSubmit` (no matcher), `PreToolUse` (matcher `Edit|Write`), `PreCompact`
  (matcher `manual|auto`). Each calls `vectr hook <event>`.
- `main.py:1261` `cmd_hook` handles them: SessionStart → boot recall (directives + high-prio);
  UserPromptSubmit → semantic recall keyed to `event["prompt"]` with
  `_HOOK_RECALL_LIMIT=3` / `_HOOK_MIN_SIMILARITY=0.35` (`main.py:40-41`), `detail=index`;
  PreToolUse → gotcha recall keyed to `tool_input.file_path` (structural); PreCompact →
  snapshot. Output envelope is `_emit_hook_context` (`main.py:1222`), which prints
  `{"hookSpecificOutput":{"hookEventName":…,"additionalContext":…}}` and prefixes a
  "don't also self-recall" notice for SessionStart/UserPromptSubmit.
- **`PostToolUse` is not wired** (grep: zero occurrences in product code). This is the largest
  untapped sanctioned channel — it fires after *every* tool call (many per turn), giving a
  post-hoc structural signal (which file was just read, which symbol was just grepped).

**Verdict:** Channels 1–5 are the paved road. The gap is not the channels — it is (a) the
intelligence they carry (single-payload today vs rolling-window) and (b) an unused high-value
event (PostToolUse). Both are additive extensions of code that already exists.

### 2.2 Async out-of-band push into a live session (channel 6) — NOT AVAILABLE

**Verdict: no sanctioned way for an external process to push a message/context into an
already-running Claude Code CLI session.** Every "push" is either fired by the user's own
action (a hook) or requires you to be the host program (Agent SDK). This is a well-supported
finding, not a gap in the research.

- **SendMessage / agent-teams inbox** is internal to Claude Code's own agents (messages land
  in `.claude/teams/<id>/inbox/` and are injected into the *receiving agent's* conversation).
  There is no documented API for an **external** process to write that inbox and have it
  delivered. The capability is an **open** community request:
  - GH anthropics/claude-code **#27441** (OPEN) — "allow external processes to send prompts to
    a running Claude Code session". https://github.com/anthropics/claude-code/issues/27441
  - GH **#53049** (CLOSED 2026-05-07) — confirms `--print`/`--continue` create a **new/forked**
    session and lose live in-memory context. https://github.com/anthropics/claude-code/issues/53049
- **MCP server-initiated messages → the model:** MCP defines server→client primitives
  (`notifications/message`, `notifications/progress`, `sampling`, `elicitation`;
  https://modelcontextprotocol.io/specification/2025-11-25). But in Claude Code:
  `notifications/message` is **received and never injected into the model's context**;
  `sampling` is **not implemented** (GH **#1785** OPEN, updated 2026-05-20,
  https://github.com/anthropics/claude-code/issues/1785); `progress` ignored; consolidated in
  GH **#31893** (https://github.com/anthropics/claude-code/issues/31893). **Elicitation IS
  implemented** (~v2.1.76) but it surfaces a dialog to the **user**, not context to the model
  — not a model-injection channel. vectr's own `/mcp` surface is request/response JSON-RPC
  (`app/routes.py:367`, dispatched in `integrations/mcp_server/_dispatch.py`) — it has no
  server-push transport either.
- **Agent SDK streaming input:** the SDK's streaming-input mode feeds messages via an async
  generator the **host app** controls (https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode).
  You *can* architect an external inject by awaiting a socket in that generator — but only
  inside your own SDK-hosted program, **not** into the interactive Claude Code CLI a user runs.
  [REAL only for SDK-hosted hosts; NOT-AVAILABLE for the CLI target.]
- **Notification / FileChanged hooks** are event-fired and can only return `systemMessage`
  (user-facing) — they cannot inject `additionalContext` into the model.

**Consequence for design:** the only *today-workable* "async-ish" pattern is indirect and
still event-bound — an external process writes a file/inbox, and the **next** `UserPromptSubmit`
hook reads it and injects. That is exactly the limitation #27441 calls out; it is not
autonomous mid-loop delivery. v1 must therefore be **event-synchronous** (see design).

### 2.3 Direct transcript-file injection (channel 7) — REJECTED, dead end

**Hypothesis:** write a synthetic `assistant`/`user` record into the session `.jsonl` and have
the model pick it up. **Verdict: dead end.** Evidence:

- The `.jsonl` is an **append-only sink** that Claude Code writes; there is no evidence it is
  read back mid-session. A running session holds its conversation state in-process and
  constructs each API request from that in-memory state, not by re-parsing the transcript.
  (Corroborated indirectly by #53049: resuming/continuing reconstructs from the file only when
  a **new** process starts — never for the live one.)
- Writing another process's append-only log is also a corruption/race hazard (interleaved
  partial lines) with no upside.
- Even the read-back-on-resume path would only matter after a restart, by which point
  SessionStart hooks (channel 2) already re-prime deterministically — a supported path that
  makes file injection redundant.

No further investment. Documented and closed.

### 2.4 `ANTHROPIC_BASE_URL` localhost proxy (channel 8) — REAL but deferred

**Verdict: officially supported and the most powerful channel, but the highest-risk; design
it, ship it last (Phase 3).**

- **Supported:** `ANTHROPIC_BASE_URL` "Override the API endpoint to route requests through a
  proxy or gateway" (https://code.claude.com/docs/en/env-vars, /authentication; fetched
  2026-07-11). Applies to the CLI, the VS Code extension, and the Agent SDK. Auth precedence
  includes `ANTHROPIC_AUTH_TOKEN` (Bearer, for gateways), `ANTHROPIC_API_KEY` (`X-Api-Key`),
  and `apiKeyHelper`. `ANTHROPIC_CUSTOM_HEADERS` adds arbitrary headers.
- **Gotchas (official):**
  - MCP tool search is **disabled by default** when `ANTHROPIC_BASE_URL` is a non-first-party
    host; re-enable with `ENABLE_TOOL_SEARCH=true` *if the proxy forwards `tool_reference`
    blocks*.
  - Remote Control is disabled on non-`api.anthropic.com` base URLs (≥ v2.1.196).
  - The proxy must speak the Anthropic `/v1/messages` **SSE streaming** shape end-to-end
    (streaming + `tool_use` passthrough).
- **Prompt-cache interaction (the load-bearing constraint)** —
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching (fetched 2026-07-11):
  up to **4 `cache_control` breakpoints**; prefix hierarchy `tools → system → messages`;
  "changes at each level invalidate that level and all subsequent levels" — **modifying tool
  defs invalidates the entire cache.** A cache read walks back up to 20 blocks for a matching
  prefix hash; longest match wins. **A proxy that injects per-request-varying context must
  append it AFTER the last cache breakpoint (as the newest content), never prepend into
  `tools`/top-of-`system`** — otherwise every turn is a fresh, uncached write. The injected
  block is itself uncached and reprocessed each turn: a small deliberate cost vs. blowing away
  a large cached prefix.
- **Availability / failure mode:** a proxy on the request path means **proxy down = agent
  down**. It needs a transparent bypass (fail-open passthrough) and must never store or log
  the API key it forwards.
- **Prior-art caution:** LiteLLM's message-mutation hook (`async_pre_call_hook`) is reported
  **bypassed on the `/v1/messages` endpoint** (GH BerriAI/litellm **#27518**, OPEN, filed
  2026-05-09; https://github.com/BerriAI/litellm/issues/27518) — i.e. a naive LiteLLM-in-front
  approach may not run the injection hook at all for Claude Code's wire shape. A custom,
  minimal Anthropic-shaped proxy is more reliable for this specific job than adopting LiteLLM.

This channel is the literal realisation of "vectr sits between user and agent". It is
deferred because Phases 1–2 deliver most of the value at a fraction of the risk, and because
its correctness bar (streaming passthrough, cache discipline, key hygiene, bypass-on-failure)
is materially higher.

---

## 3. Notes-store retrieval — reality today

**Finding: semantic search over the note store already exists and ships.** The brief flagged
this as a likely prerequisite gap; it is largely already built.

Verified in `agent/working_context_store/_store.py`:

- **Notes are embedded at write time.** `remember` (`:181`) embeds note content with
  `self._embed_fn` and upserts the vector into a ChromaDB `working_memory` collection
  (`:259-266`), keyed by `note_id`.
- **Recall is semantic-first.** `recall(query=…)` (`:273`) takes the semantic path when a
  query and the embedding collection are available (`:316-327`): it calls `_semantic_recall`
  (`:399`), which embeds the query with `self._embed_query_fn` (asymmetric document/query
  embedding — `:429`), runs `working_memory.query(query_embeddings=…)`, applies a
  **`min_similarity` cutoff** (`1 - cosine_distance ≥ floor`, UPG-5.1, `:440-447`), then
  fetches the surviving note rows from SQLite with metadata filters (kind/priority/tags/age).
  **SQL `LIKE` is only a fallback** when no query is given or embeddings are unavailable
  (`:329`).
- **Embed-model migration** is handled (`_reconcile_embed_model_stamp`, `:504+`): if the
  collection's stamped embed model differs from the current one, notes are re-embedded in
  place before serving — so mixing embedding spaces cannot silently corrupt recall.
- **Similarity floor is already the gating primitive.** The `UserPromptSubmit` hook already
  passes `min_similarity=0.35`, so an off-topic prompt injects nothing. This is a numeric
  threshold, not keyword logic — exactly the shape the no-query-heuristics rule requires.

**So the real prerequisite work is narrower than "build semantic notes-search":**

1. **Rolling-window query.** `recall` matches one `query` string. Proactive matching needs a
   query assembled from the last N conversational records (user text + assistant reasoning
   summary + recent tool names/inputs), not a single user prompt. This is an input-assembly
   task on top of the existing recall path — not a new retrieval engine.
2. **Scored results to a gating layer.** `_semantic_recall` computes distances internally but
   the recall API returns notes, not scores. The gating layer needs the per-note similarity to
   apply budgets/thresholds/dedup. Small surface addition (return scores; already computed).
3. **Code-index matches too.** Proactive matching should also surface code chunks (via the
   existing `/v1/search`) and symbol definitions (via `/v1/locate` on symbols seen in tool
   traffic). These endpoints exist; they just aren't called from the hook path today.
4. **First-class MCP exposure (optional).** `vectr_recall(query=…)` already exposes semantic
   notes-search over MCP. No new tool is strictly required, but a thin "match against this
   text blob" entry point simplifies the hook/daemon call. [Design decides; see tasks.]

---

## 4. Prior art

Two camps: **API-proxy** middleware (sit on the wire) and **hook** integrations (plug into the
harness lifecycle). For Claude Code specifically, the community has overwhelmingly chosen
hooks — which matches this doc's Phase-1 recommendation.

**Hook-side (Claude Code-native context injectors):**

- **claude-rag** — "first open-source RAG plugin for Claude Code"; uses `UserPromptSubmit` +
  `PostToolUse` + `Stop` hooks (not a proxy); hybrid vector+BM25 over past sessions, injected
  silently into the prompt. https://github.com/ThisisYoYoDev/claude-rag / https://clauderag.io/
- **claude-mem** — captures + compresses session activity, injects relevant context into future
  sessions via hooks. https://github.com/thedotmack/claude-mem
- **rag-cli** — local Chroma RAG plugin, hooks `UserPromptSubmit`. https://github.com/ItMeDiaTech/rag-cli
- Roundup: https://milvus.io/blog/claude-code-context-management-tools.md

These validate the mechanism (hook `additionalContext` injection is the working pattern) and
also validate vectr's differentiator: vectr already has a **structural** layer (symbol graph,
file-anchored gotchas) that pure-RAG plugins lack, and a **similarity floor** that pure-RAG
plugins mostly lack (they inject on every turn regardless of relevance).

**Proxy-side:**

- **LiteLLM proxy** — `async_pre_call_hook` can mutate `messages` pre-call
  (https://docs.litellm.ai/docs/proxy/call_hooks) but is reported bypassed on `/v1/messages`
  (GH #27518, §2.4). Cautionary, not adopt-as-is.
- **mem0** — a "lightweight proxy layer" / memory service that extracts facts and injects
  relevant memories into future prompts; OpenAI-compatible wrap. https://github.com/mem0ai/mem0
- **Letta / MemGPT** — a stateful agent *runtime* with always-injected core-memory blocks +
  archival search; an alternative host, not a transparent proxy under Claude Code.
  https://github.com/letta-ai/letta

**In-vectr prior art.** vectr's MCP layer already does a small, sanctioned form of proactive
prompting: `integrations/mcp_server/_session.py` tracks calls-since-save per session and packs
a "remember nudge" into tool responses (`_should_nudge_remember`, `_remember_nudge_text`).
This is additive, deterministic response-packing — the same discipline the gating policy must
follow — and a precedent to point to.

---

## 5. Open unknowns (labelled)

1. **Multi-root workspace → slug mapping.** A `.code-workspace` with several roots may map to
   several project slugs; robust "which transcript is this daemon's session" across multi-root
   setups is not fully specified. Phase 1 sidesteps it by using the hook-supplied
   `transcript_path`; a continuous watcher (future) would need this solved. [UNKNOWN — bounded.]
2. **Assistant-reasoning inclusion.** `thinking` blocks are present in the transcript; whether
   embedding them improves match precision or adds noise is untested. To be settled by a small
   offline experiment before enabling by default. [UNKNOWN — experiment queued in tasks.]
3. **Injection-precision measurement.** "Did the agent *use* what was injected" has no direct
   signal; it must be inferred (subsequent `vectr_fetch` of an injected chunk id, opening an
   injected file, referencing an injected note). The proxy of "use" needs validation. [UNKNOWN.]
4. **Transcript schema drift.** The `type`/content-block schema is stable today but is not a
   public contract and can change across Claude Code releases. The stream adapter must parse
   defensively (unknown record/block types skipped, never fatal). [KNOWN RISK — mitigated by design.]
5. **PostToolUse volume vs. usefulness.** PostToolUse fires on every tool call; whether
   per-tool injection is net-positive or becomes noise is an open economics question, gated by
   the budget/cooldown design and to be measured. [UNKNOWN — kill-criteria apply.]
6. **Non-Claude harness adapters.** Deliberately out of scope; the adapter interface is
   designed for it but no second adapter is specified. [DEFERRED.]

---

## 6. Sources

Claude Code / Anthropic (fetched 2026-07-11):
- Hooks reference — https://code.claude.com/docs/en/hooks
- Env vars — https://code.claude.com/docs/en/env-vars
- Authentication / LLM gateway — https://code.claude.com/docs/en/authentication , /llm-gateway
- Agent SDK streaming vs single mode — https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode
- Sub-agents — https://code.claude.com/docs/en/sub-agents
- Prompt caching — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- MCP spec — https://modelcontextprotocol.io/specification/2025-11-25

GitHub issues (status as of 2026-07-11):
- anthropics/claude-code #27441 (OPEN), #53049 (CLOSED 2026-05-07), #1785 (OPEN), #31893, #7108 (CLOSED)
- BerriAI/litellm #27518 (OPEN)

Prior-art tools:
- claude-rag https://github.com/ThisisYoYoDev/claude-rag · claude-mem https://github.com/thedotmack/claude-mem
- rag-cli https://github.com/ItMeDiaTech/rag-cli · mem0 https://github.com/mem0ai/mem0 · Letta https://github.com/letta-ai/letta
- LiteLLM call hooks https://docs.litellm.ai/docs/proxy/call_hooks

vectr worktree (`feature/experimental-godMode`):
- `main.py:40-41` hook thresholds · `:523` `_write_claude_hooks` · `:1222` `_emit_hook_context` · `:1261` `cmd_hook` · `:872` bind hardcoded `127.0.0.1`
- `agent/working_context_store/_store.py:181` `remember` · `:259` embed-at-write · `:273` `recall` · `:399` `_semantic_recall` · `:440` similarity floor
- `app/routes.py` endpoints `/v1/search :79`, `/v1/recall :278`, `/v1/remember :252`, `/v1/locate :206`, `/v1/trace :231`, `/mcp :367`
- `agent/watcher.py` watchdog `Observer` + debounce (reusable tail pattern)
- `integrations/mcp_server/_session.py` remember-nudge (in-vectr response-packing precedent)
- `../security-features-design.md` (docs repo) — team-mode / bind-guard / audit-log boundary
