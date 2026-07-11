# Proactive participation — task backlog (UPG-PRO)

Status: implementable backlog for the experimental proactive-participation feature
("Proactive context"; internal codename "god mode", never product-facing). Read `research.md`
and `design.md` first. This file is **not** the canonical `tasks.md` in the docs repo — the
orchestrator integrates these items later.

Conventions (match the existing vectr task workflow):
- Every task: implement → write/extend tests (happy path, edge, error) → run the **full** suite
  in `.venv` (`./.venv/bin/python -m pytest -q`) → update `spec.md` → update working-memory
  notes → commit on a worktree branch. A dedicated test per REST route touched; MCP-surface
  coverage where the MCP tools change; mocks return the **real** type, never `[]`/`{}` stand-ins.
- **Hard rule, every task:** no query-side keyword/regex classification, rerouting, or gating.
  Triggering = structural exact match + numeric similarity threshold + additive deterministic
  packing only. Any task that would introduce a content classifier is wrong — reshape it as a
  matcher/anchor + threshold.
- Size: S ≈ <½ day, M ≈ 1–2 days, L ≈ 3+ days for one coder agent.

## Dependency graph (Phase 1 critical path in bold)

```
UPG-PRO-1 (scored recall) ─┐
UPG-PRO-2 (window reader) ──┼─▶ UPG-PRO-4 (matchers) ─▶ UPG-PRO-5 (gate) ─▶ UPG-PRO-7 (endpoint) ─┐
UPG-PRO-3 (extractors) ─────┘                                                                       │
UPG-PRO-6 (config + localhost guard) ──────────────────────────────────────────────────────────────┤
                                                                                                    ▼
                        ┌───────────────── UPG-PRO-8 (UserPromptSubmit rolling-window) ◀────────────┤
   Phase 1 delivery ────┼───────────────── UPG-PRO-9 (PostToolUse — NEW channel) ◀──────────────────┤
                        ├───────────────── UPG-PRO-10 (PreToolUse broaden + symbol) ◀───────────────┤
                        └───────────────── UPG-PRO-11 (systemMessage, opt-in) ◀─────────────────────┘
   Cross-cutting ─────── UPG-PRO-12 (audit + status)   UPG-PRO-13 (metrics + experiment)   UPG-PRO-17 (docs)
   Phase 3 ───────────── UPG-PRO-14 (proxy skeleton) ─▶ UPG-PRO-15 (proxy inject) ─▶ UPG-PRO-16 (proxy opt-in/wiring)
   Phase 2 ───────────── UPG-PRO-WATCH (watch item; not buildable today)
```

## Summary table

| ID | Title | Phase | Size | Depends on |
|---|---|---|---|---|
| UPG-PRO-1 | Surface similarity scores from recall | 1 (prereq) | S–M | — |
| UPG-PRO-2 | Transcript window reader (stream adapter) | 1 (prereq) | M | — |
| UPG-PRO-3 | Deterministic file/symbol extractors | 1 (prereq) | S | PRO-2 |
| UPG-PRO-4 | Matching engine — 4 scored matchers | 1 | M–L | PRO-1, PRO-2, PRO-3 |
| UPG-PRO-5 | Gating policy — floor/budget/dedup/packing | 1 | M | PRO-4 |
| UPG-PRO-6 | Config block + localhost-only enforcement | 1 | S–M | — |
| UPG-PRO-7 | Daemon proactive endpoint | 1 | M | PRO-4, PRO-5, PRO-6 |
| UPG-PRO-8 | UserPromptSubmit → rolling-window | 1 | S–M | PRO-7 |
| UPG-PRO-9 | PostToolUse hook (new channel) | 1 | M | PRO-7 |
| UPG-PRO-10 | PreToolUse broaden + symbol match | 1 | S | PRO-7, PRO-4 |
| UPG-PRO-11 | User-facing systemMessage (opt-in) | 1 | S | PRO-7 |
| UPG-PRO-12 | PROACTIVE_INJECT audit + status observability | 1 | S | PRO-7; security audit |
| UPG-PRO-13 | Metrics harness + include_thinking experiment | 1 | M | PRO-7 |
| UPG-PRO-17 | spec.md + README documentation | 1 | S–M | PRO-8..12 |
| UPG-PRO-14 | Localhost Anthropic-shaped proxy skeleton | 3 | L | — |
| UPG-PRO-15 | Proxy context injection (cache-append) | 3 | M–L | PRO-14, PRO-4, PRO-5 |
| UPG-PRO-16 | Proxy opt-in + wiring + bypass story | 3 | M | PRO-14, PRO-15 |
| UPG-PRO-WATCH | Async-delivery watch item | 2 | — | upstream |

---

## Phase 1 — prerequisites

### UPG-PRO-1 — Surface similarity scores from recall
**Phase 1 (prerequisite) · Size S–M · Depends: none**

**What to build.** `_semantic_recall` already computes per-candidate cosine distance
(`agent/working_context_store/_store.py:436-447`) but discards it after the floor check. Expose
the similarity (`1 - distance`) alongside each returned note, end-to-end, so the future gating
layer can budget/threshold on it. This is the semantic-notes-search prerequisite the brief
flagged — the retrieval itself already exists; only the score is unexposed.

- Add an optional `with_scores: bool` path (or a parallel return) through `recall` /
  `_semantic_recall` returning `(WorkingNote, similarity: float)` pairs. Default behaviour
  (no scores) is byte-for-byte unchanged.
- Thread it through the service (`app/service.py`) and the `/v1/recall` route
  (`app/routes.py:278`) behind a request flag (e.g. `RecallRequest.with_scores`), adding the
  score to `RecallResponse` (`app/models.py`) as an optional field.
- Structural/SQL-LIKE path returns `None` scores (no cosine available) — documented, not faked.

**Acceptance criteria.**
- `recall(query=…, with_scores=True)` returns each note with its cosine similarity; ordering
  unchanged vs the scoreless call.
- A note below `min_similarity` is still excluded (floor precedence preserved).
- `/v1/recall` with `with_scores=true` returns scores in the response; without it, the response
  is unchanged (regression test asserts identical shape to today).
- SQL-LIKE fallback returns notes with `score=null`, never a fabricated number.
- Full suite green.

**Files:** `agent/working_context_store/_store.py`, `app/service.py`, `app/routes.py`,
`app/models.py`, tests under `tests/`.

---

### UPG-PRO-2 — Transcript window reader (stream adapter)
**Phase 1 (prerequisite) · Size M · Depends: none**

**What to build.** New `agent/proactive/adapter.py`: read the tail of a Claude Code transcript
`.jsonl` and return a bounded, in-memory rolling window of normalised `ConversationEvent`s. No
persistence, no network. Pure function of (file path, limits).

- `ConversationEvent` dataclass: `role`, `text`, `tool_name`, `tool_input`,
  `tool_result_excerpt`, `file_paths`, `symbols`, `timestamp`, `uuid`.
- `window(transcript_path, max_records=20, max_tokens≈2000) -> list[ConversationEvent]`: read
  only the last chunk of the file (seek from end / read-last-N-lines), parse the conversational
  records (`type in {user, assistant}`), extract text and `tool_use`/`tool_result` blocks.
- **Defensive parsing (mandatory):** unknown `type` / content-block types skipped, never fatal;
  partial trailing line tolerated; malformed JSON line skipped. A corrupt/absent file returns
  `[]`, never raises.
- Window bounded by both record count and an estimated token budget.
- `include_thinking` flag (default off pending UPG-PRO-13) controls whether assistant
  `thinking` blocks contribute to `text`.

**Acceptance criteria.**
- Given a synthetic multi-record JSONL fixture, returns events in conversation order with text,
  tool names, and tool inputs populated. (Fixtures are fully synthetic — no real transcript
  content in the repo.)
- Unknown record/block types and a malformed trailing line are skipped without error.
- Missing file → `[]`. Empty file → `[]`.
- Window never exceeds the configured record/token bounds regardless of file size.
- `include_thinking=False` excludes thinking text; `True` includes it.
- Full suite green.

**Files:** new `agent/proactive/__init__.py`, `agent/proactive/adapter.py`,
`tests/test_proactive_adapter.py` (+ synthetic JSONL fixtures).

**Note for the coder:** the schema field names are in `research.md` §1.2. Do not assume strict
timestamp ordering; take the last N *conversational* records.

---

### UPG-PRO-3 — Deterministic file/symbol extractors
**Phase 1 (prerequisite) · Size S · Depends: PRO-2**

**What to build.** Pure functions that pull structural anchors out of a `ConversationEvent`
deterministically, for the structural matchers.

- `file_paths_from(event) -> list[str]`: extract file paths from `tool_input` (e.g. the
  `file_path` key common to Read/Edit/Write — same field the existing PreToolUse handler uses,
  `main.py:1312`) and from obvious result fields. Exact keys only; **no regex-scanning of free
  text for path-like strings** (that would be heuristic classification).
- `symbols_from(event) -> list[str]`: extract identifier-shaped arguments from tool inputs that
  name symbols (e.g. a `pattern`/`query`/`name` argument that is a single identifier token).
  Deterministic token check (is-identifier), not semantic classification.

**Acceptance criteria.**
- Given a synthetic Read/Edit event, returns the exact `file_path`.
- Given a synthetic grep/locate event whose argument is a bare identifier, returns that symbol;
  given a free-text argument, returns nothing (no path/symbol guessing from prose).
- No regex classification of message text; extraction is keyed on known tool-input fields only.
- Full suite green.

**Files:** `agent/proactive/adapter.py` (or `agent/proactive/extract.py`), tests.

---

## Phase 1 — core engine

### UPG-PRO-4 — Matching engine (four scored matchers)
**Phase 1 · Size M–L · Depends: PRO-1, PRO-2, PRO-3**

**What to build.** New `agent/proactive/matcher.py`: given a rolling window, run **all four**
matchers unconditionally and return the union of scored `Candidate`s. No branch inspects window
content to decide which matcher to run.

- `Candidate{kind, payload, score, provenance, anchor_id}` where `kind ∈
  {note_structural, note_semantic, symbol_def, code_semantic}`.
- **M1 structural file match:** for each `file_path` in the window, exact-match against note
  file-anchors (reuse the gotcha file-anchor lookup) → candidate notes; `score=1.0`.
- **M2 structural symbol match:** for each `symbol` in the window, call the existing locate path
  (`/v1/locate` / service locate) → `symbol_def` candidate (file:line); score from locate
  confidence.
- **M3 semantic note match:** embed the assembled window text (query-side `embed_query_fn`),
  call the scored recall path from UPG-PRO-1 → `note_semantic` candidates with similarity scores.
- **M4 semantic code match:** call the existing search path (`/v1/search`) with the window text
  → `code_semantic` candidates with hybrid scores.
- Assemble the "window text" as a bounded concatenation of recent user text + assistant text
  (+ thinking if enabled) + recent tool names. Deterministic assembly, no content routing.

**Acceptance criteria.**
- All four matchers run on every call; disabling one is only via static config, never a
  content branch (assert no code path reads window text to choose a matcher).
- A window naming an anchored file yields the M1 note at score 1.0.
- A window naming a known symbol yields the M2 definition.
- A window semantically near a stored note yields it via M3 with a score ≥ floor; an off-topic
  window yields no M3 candidate.
- M4 returns code chunks with scores; below-floor chunks excluded.
- Union contains candidates from multiple matchers with distinct `provenance`; duplicates across
  matchers keep the highest score (dedup by `anchor_id` is deferred to the gate).
- Matchers reuse existing recall/search/locate/anchor code — no new retrieval engine.
- Full suite green.

**Files:** new `agent/proactive/matcher.py`, tests; touches `app/service.py` if a shared
in-process match helper is preferable to internal HTTP calls (prefer in-process service calls
over self-HTTP for latency).

---

### UPG-PRO-5 — Gating policy (floor / budget / dedup / packing)
**Phase 1 · Size M · Depends: PRO-4**

**What to build.** New `agent/proactive/gate.py`: turn scored candidates into 0..K emitted
items and pack them into one deterministic `additionalContext` string. All rules numeric.

- **Floor:** semantic candidates must clear `min_similarity`; structural (M1/M2) bypass.
- **Budget:** at most `max_items_per_event` (K) and `max_chars_per_event` (T); fill by
  (score desc, provenance rank structural>note>code, anchor_id asc). Deterministic, no RNG.
- **Dedup + cooldown:** per-session ledger of recently emitted `anchor_id`s (bounded ring /
  TTL, size `cooldown_items`); a candidate already in the ledger is dropped. Reuse the
  per-session-state pattern in `integrations/mcp_server/_session.py`.
- **Per-channel policy:** accept a channel identifier; PostToolUse defaults to structural-only
  and a tighter budget (static config, §8 of design).
- **Packing:** each emitted item rendered as a self-describing, provenance-tagged line; join in
  deterministic order. Empty selection → return empty string (so `_emit_hook_context` emits
  nothing).

**Acceptance criteria.**
- With K=3, a candidate set of 10 emits exactly the top 3 by the deterministic order; identical
  input → identical output (determinism test).
- A below-floor semantic candidate is dropped; a structural candidate at the same rank is kept.
- Re-invoking with the same session and the same top candidate within the cooldown emits it
  once, not twice (dedup test).
- PostToolUse channel with `structural_only=true` emits no semantic candidates.
- Empty/entirely-below-floor input → empty string.
- Full suite green.

**Files:** new `agent/proactive/gate.py`, tests; per-session ledger helper (new or extending
`integrations/mcp_server/_session.py`).

---

### UPG-PRO-6 — Config block + localhost-only enforcement
**Phase 1 · Size S–M · Depends: none (but gates enablement of PRO-7+)**

**What to build.** The `proactive:` config block (design §13) + the two-gate localhost
enforcement (design §10).

- Add the `proactive:` block to `agent/config.yaml` with the documented defaults
  (`enabled: false`, thresholds, budgets, channel toggles). Load it via the existing config
  loader (`agent/config.py`).
- Env overrides: `VECTR_PROACTIVE`, `VECTR_PROACTIVE_MIN_SIMILARITY`,
  `VECTR_PROACTIVE_MAX_ITEMS`, etc., following the established env-over-yaml pattern.
- **Localhost guard:** a single `proactive_enabled(bind_host, api_key) -> bool` that returns
  False (and, when `enabled=true` was explicitly set, raises a clear startup refusal) whenever
  the bind is non-loopback (team-mode signature). Coordinate with the security workstream's
  loopback-detection helper — reuse it if present rather than duplicating (design §11).

**Acceptance criteria.**
- Defaults load with `enabled=false`; a fresh workspace does nothing proactive.
- Loopback bind + `enabled=true` → proactive on. Non-loopback bind → proactive forced off; an
  explicit `enabled=true` under non-loopback → actionable refusal error.
- Env overrides beat yaml; yaml beats built-in defaults.
- Full suite green.

**Files:** `agent/config.yaml`, `agent/config.py`, `main.py` (startup wiring / refusal), tests.

---

### UPG-PRO-7 — Daemon proactive endpoint
**Phase 1 · Size M · Depends: PRO-4, PRO-5, PRO-6**

**What to build.** The endpoint a hook calls to get packed proactive context. Given the event
context (transcript_path, session_id, channel, and channel-specific fields like the current
`file_path` or `prompt`), run adapter → matcher → gate and return the packed
`additionalContext` string (+ optional `systemMessage`).

- New route, e.g. `POST /v1/proactive` (`app/routes.py`), request model in `app/models.py`
  (`transcript_path`, `session_id`, `channel`, optional `file_path`/`prompt`/`tool_name`).
- Service method wires adapter+matcher+gate in-process (no self-HTTP).
- Honors UPG-PRO-6: if proactive is disabled/refused, returns empty (never errors the hook).
- Respects the hook timeout budget — the whole call must be fast (embedding one window + a
  bounded search); add an internal soft deadline so a slow match returns empty rather than
  risking the 30s UserPromptSubmit timeout.

**Acceptance criteria.**
- A dedicated route test per the workflow: valid request → packed context; disabled → empty;
  malformed/missing transcript_path → empty, never 500.
- MCP surface unaffected (this is a hook-facing REST route, not an MCP tool).
- Latency: matching a bounded window returns well under the soft deadline in a test with a
  mocked embed/search returning the real types.
- Full suite green.

**Files:** `app/routes.py`, `app/models.py`, `app/service.py`, `agent/proactive/*`, tests
(incl. the dedicated route test).

---

## Phase 1 — delivery (hook channel adapters)

### UPG-PRO-8 — UserPromptSubmit → rolling-window
**Phase 1 · Size S–M · Depends: PRO-7**

**What to build.** Replace the single-prompt recall in the `user-prompt-submit` branch of
`cmd_hook` (`main.py:1288-1302`) with a call to `/v1/proactive` using the hook-supplied
`transcript_path` (rolling window) plus the `prompt`. Keep the "don't also self-recall" prefix
(`_HOOK_NO_DOUBLE_RECALL_LINE`).

**Acceptance criteria.**
- The hook now injects context matched to the last several turns, not only the current prompt
  (verified with a synthetic transcript where the relevant note matches an earlier turn, not the
  latest prompt).
- When proactive is disabled, the hook falls back to today's behaviour or injects nothing
  (decide + document; must never regress the existing per-turn recall for non-proactive users).
- Hook still exits 0 on any error; never breaks the session.
- Full suite green.

**Files:** `main.py` (`cmd_hook`), tests (`tests/test_hook_injection_observability.py` and a
new proactive-hook test).

---

### UPG-PRO-9 — PostToolUse hook (new channel)
**Phase 1 · Size M · Depends: PRO-7**

**What to build.** Wire the currently-unused PostToolUse event (research §2.1).

- `_write_claude_hooks` (`main.py:523`): add a PostToolUse hook group (matcher covering
  read/search/edit tools — a static matcher list, not content logic) calling
  `vectr hook post-tool-use`.
- `cmd_hook`: new `post-tool-use` branch — read the tool name/result + transcript_path, call
  `/v1/proactive` with `channel=post_tool_use` (structural-only by default), inject the packed
  structural candidates (notes anchored to the file just touched; definition of a symbol just
  grepped). Emit only `additionalContext`; do **not** use `updatedToolOutput`.
- Add `post-tool-use` to the argparse `choices` (`main.py:1935`).

**Acceptance criteria.**
- Fresh `vectr init --hooks` writes a PostToolUse group into `.claude/settings.json`.
- After a synthetic Read of a file with an anchored gotcha, the handler emits that note; with no
  anchor and structural-only, it emits nothing.
- Tight budget/cooldown honored (no repeat within the window; PostToolUse never floods).
- Handler exits 0 on error.
- Full suite green.

**Files:** `main.py` (`_write_claude_hooks`, `cmd_hook`, argparse), tests.

---

### UPG-PRO-10 — PreToolUse broaden + symbol match
**Phase 1 · Size S · Depends: PRO-7, PRO-4**

**What to build.** Broaden the PreToolUse matcher beyond `Edit|Write` (static matcher list) and
route it through `/v1/proactive` so it can emit both file-anchored notes (M1) and symbol
definitions (M2) when the tool input names a symbol, not only gotchas.

**Acceptance criteria.**
- PreToolUse fires on the broadened tool set; still structural (exact file/symbol match), no
  content classification.
- A pre-grep event naming a known symbol emits its definition; an unknown symbol emits nothing.
- Existing gotcha-by-file behaviour preserved.
- Full suite green.

**Files:** `main.py` (`_write_claude_hooks`, `cmd_hook`), tests.

---

### UPG-PRO-11 — User-facing systemMessage (opt-in)
**Phase 1 · Size S · Depends: PRO-7**

**What to build.** When `proactive.user_facing_systemMessage=true` (default false), emit a
concise `systemMessage` (user-facing) on high-confidence structural injections, e.g. "vectr
surfaced 2 notes for <file>" — so the human sees vectr participating. Rate-limited; model-facing
`additionalContext` remains the primary path.

**Acceptance criteria.**
- Default off: no `systemMessage` emitted (byte-for-byte as today).
- On: a one-line, content-free-of-secrets summary is emitted alongside the `additionalContext`;
  never includes note bodies or conversation text.
- Rate-limited so it does not fire every tool call.
- Full suite green.

**Files:** `main.py` (`_emit_hook_context` extension to also carry `systemMessage`), tests.

---

## Phase 1 — cross-cutting

### UPG-PRO-12 — PROACTIVE_INJECT audit + status observability
**Phase 1 · Size S · Depends: PRO-7; coordinate with security audit workstream**

**What to build.** Record each proactive injection as a `PROACTIVE_INJECT` event in the
existing opt-in audit log (`agent/working_context_store/_audit.py`, `VECTR_AUDIT_LOG`),
**metadata only** (channel, item count, anchor ids, scores) — never conversation text or note
bodies (design §9). Extend `vectr status` proactive-injection counts (build on
`_hook_injection_line`, `main.py:1397`).

**Acceptance criteria.**
- With audit off (default): nothing logged.
- With audit on: a `PROACTIVE_INJECT` line per injection with metadata only; assert no
  conversation text / note body appears in the log.
- `vectr status` shows proactive injection counts when any have fired; terse (no zero line)
  otherwise.
- Full suite green.

**Files:** `agent/working_context_store/_audit.py`, status wiring in `main.py`/service, tests.
Coordinate the audit event-type enum with the security workstream (design §11).

---

### UPG-PRO-13 — Metrics harness + include_thinking experiment
**Phase 1 · Size M · Depends: PRO-7**

**What to build.** Offline instrumentation for the success metrics + kill criteria (design §8,
research §5), and the experiment that decides `include_thinking`'s default.

- **Metrics** (computed offline from synthetic/recorded runs, no live quota): injection
  precision (did the agent subsequently `vectr_fetch` an injected chunk id / open an injected
  file / reference an injected note), token overhead per session, time-to-relevant-context vs
  the pull-based baseline, duplicate-suppression rate.
- **include_thinking experiment:** measure match precision with vs without assistant `thinking`
  in the window on a synthetic fixture set; set the default accordingly.
- **Kill-criteria thresholds** documented as measurable gates (e.g. abandon/redesign if
  injection precision < X% while token overhead > Y%).

**Acceptance criteria.**
- A repeatable offline harness produces the four metrics from a fixture set (fully synthetic
  transcripts — no real content committed).
- The include_thinking default is set from measured precision, with the result recorded in a
  vectr note + spec.
- Kill-criteria thresholds are written down and checkable.
- Full suite green.

**Files:** `benchmarks/` (new proactive harness), `agent/config.yaml` (thinking default), tests.

---

### UPG-PRO-17 — Documentation (spec.md + README)
**Phase 1 · Size S–M · Depends: PRO-8..PRO-12**

**What to build.** Document the feature in `spec.md` and the README with the honesty bar of the
security doc: what it does, that it is localhost-only + off by default + embeddings-only + no
transcript persistence, the config surface, and the "when it can hurt" caveats. Editor-agnostic
copy; the codename never appears.

**Acceptance criteria.**
- `spec.md` has a proactive-context section covering architecture, config, privacy, and limits.
- README documents opt-in, localhost-only, data-handling, and the kill switch.
- No product-facing use of the codename; no editor names in product copy beyond where
  technically required (hook mechanics).

**Files:** `spec.md` (docs repo — via orchestrator), `README.md`.

---

## Phase 2 — async delivery (watch item)

### UPG-PRO-WATCH — Async-delivery watch item
**Phase 2 · not buildable today**

Track upstream sanctioned async delivery into a running Claude Code CLI session (GH #27441,
#53049, MCP sampling #1785 — research §2.2). **No implementation until a channel lands.** If it
lands: reuse the continuous stream tail (design §3) + the existing gate; add one channel
adapter. Do not build speculatively. Owner: revisit each vectr release.

---

## Phase 3 — localhost API proxy

### UPG-PRO-14 — Localhost Anthropic-shaped proxy skeleton
**Phase 3 · Size L · Depends: none (independent of P1 delivery)**

**What to build.** A minimal localhost proxy (`agent/proactive/proxy.py`, its own port/process)
that Claude Code can target via `ANTHROPIC_BASE_URL`. Forwards `/v1/messages` to the real
Anthropic API, preserving the SSE streaming + `tool_use` passthrough shape. **Custom, not
LiteLLM** (LiteLLM's message hook is bypassed on `/v1/messages` — research §2.4).

**Hard requirements (design §6 Phase 3):**
- **Key hygiene:** forward the caller's key/token untouched; never store, never log it.
- **Fail-open:** on any proxy error, transparently pass the original request through; proxy down
  must not mean agent down.
- Handle `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` precedence and `ANTHROPIC_CUSTOM_HEADERS`.

**Acceptance criteria.**
- A request through the proxy reaches upstream and streams back unmodified (no injection yet);
  streaming + tool_use blocks pass through intact.
- Key never appears in any log or persisted store (assert).
- Upstream failure / proxy fault → transparent passthrough or clean bypass; documented.
- Off by default; only active when explicitly opted in.
- Full suite green (proxy tests use a mocked upstream returning the real Anthropic wire types).

**Files:** new `agent/proactive/proxy.py`, tests.

---

### UPG-PRO-15 — Proxy context injection (cache-append)
**Phase 3 · Size M–L · Depends: PRO-14, PRO-4, PRO-5**

**What to build.** In the proxy, run the matcher+gate over the request's full canonical
conversation and **append** the packed context block **after the last `cache_control`
breakpoint** (never into `tools`/top-of-`system`), so the cached prefix is never invalidated
(research §2.4).

**Acceptance criteria.**
- The injected block is appended as newest content; a golden test asserts the pre-existing
  `cache_control` breakpoints and the tools/system prefix are byte-unchanged.
- Injection reuses the same matcher+gate as the hook path (no second matching engine).
- Below-budget / empty selection → request forwarded unchanged.
- Full suite green.

**Files:** `agent/proactive/proxy.py`, reuse `agent/proactive/matcher.py` + `gate.py`, tests.

---

### UPG-PRO-16 — Proxy opt-in + wiring + bypass story
**Phase 3 · Size M · Depends: PRO-14, PRO-15**

**What to build.** Opt-in config for the proxy, a `vectr` command / docs to set
`ANTHROPIC_BASE_URL` at the proxy, the non-first-party-host caveats (`ENABLE_TOOL_SEARCH`,
Remote-Control-disabled), and the documented bypass/failure story. Localhost-only + team-mode
refusal (same gates as UPG-PRO-6) apply to the proxy too.

**Acceptance criteria.**
- Opt-in flag gates the proxy; off by default.
- Docs cover the base-URL wiring, the tool-search/remote-control caveats, and how to bypass
  (unset the base URL) if the proxy misbehaves.
- Team-mode / non-loopback refusal enforced.
- Full suite green.

**Files:** `main.py` (CLI/wiring), `README.md`/`spec.md` (docs), `agent/config.yaml`, tests.

---

## Notes for the orchestrator

- **Pick-up order:** UPG-PRO-1 and UPG-PRO-2 are independent and can start in parallel cold.
  UPG-PRO-3 follows PRO-2. The Phase-1 critical path is 1→4→5→7→(8,9,10). PRO-6 can land any
  time before PRO-7.
- **Coordination points with the security workstream** (design §11): (a) the loopback-detection
  helper, (b) the audit event-type enum for `PROACTIVE_INJECT`. Share, don't fork.
- **Semantic-notes-search is NOT a from-scratch build** — UPG-PRO-1 only surfaces already-computed
  scores. The bulk of the intelligence layer is window assembly (PRO-2/3) + reusing existing
  recall/search/locate (PRO-4).
- Phase 2 has no code tasks by design; Phase 3 is independent and can be scheduled after Phase 1
  proves the economics via UPG-PRO-13.

---

## Phase-3 implementation status (branch `feature/experimental-godMode`)

Phase 3 (the localhost proxy + injection + org-wide caching) is **implemented**. Because Phase 3
was built before the Phase-1 engine landed, the minimal honest subset of the shared engine was
built alongside it rather than the full Phase-1 backlog. Exact status:

### UPG-PRO-14 — proxy skeleton — **DONE**
`agent/proactive/proxy.py`. Custom Anthropic-shaped proxy (not LiteLLM). Transparent
pass-through of streaming SSE + tool_use (byte-exact relay via `httpx` `aiter_raw`) and
non-streaming; forwards the upstream key untouched (never stored/logged; excluded from the
hop-by-hop strip only, i.e. always passed through); fail-open on upstream error (honest
upstream-shaped 502) and on injection error/timeout; localhost-only listener (refuses a
non-loopback bind); off by default. `vectr proxy` CLI (`main.py`) wires it with the documented
bypass/caveat story.

### UPG-PRO-15 — proxy injection (cache-append) — **DONE**
`agent/proactive/request_window.py` (`append_context_block` + `cache_prefix_signature`) appends
injected context after the last `cache_control` breakpoint, never mutating/reordering earlier
messages; a golden test asserts the protected prefix is byte-identical with and without
injection. Injection reuses the single matcher+gate engine (`matcher.py` + `gate.py`) via the
daemon `POST /v1/proactive` route — no second matching engine. Empty/over-budget selection →
request forwarded unchanged.

### UPG-PRO-16 — proxy opt-in + wiring + bypass — **DONE**
Opt-in via config (`proactive.proxy`) + the `vectr proxy` command; localhost-only + team-mode
refusal enforced; the non-first-party-host caveats (`ENABLE_TOOL_SEARCH`, Remote-Control) and
the unset-`ANTHROPIC_BASE_URL` bypass are printed on start and documented in README.

### Phase-1 subset built to serve Phase 3 (not the full Phase-1 backlog)
- **UPG-PRO-1 (scored recall) — DONE** at the store (`recall_scored` + `_semantic_recall(return_scores)`)
  and service level; powers the semantic-note matcher. The `/v1/recall` route `with_scores` flag
  is **deferred** (the proxy path does not need it; the injection source uses `service.recall_scored`
  directly).
- **UPG-PRO-4 (matchers) — PARTIAL/DONE for the subset.** M1 structural file→note and M3 semantic
  note ship as the default injection source; M4 code-search ships behind a static toggle
  (`proactive.matchers.code_search`, default off). **M2 (symbol-definition via locate) is deferred**
  — it is another matcher + threshold, not a content classifier.
- **UPG-PRO-5 (gate) — DONE** (`gate.py`): floor / budget / dedup+cooldown / deterministic pack.
- **UPG-PRO-6 (config + localhost guard) — DONE** (`settings.py`, `config.yaml`, `config.py`):
  `proactive:` block + `VECTR_PROACTIVE*` env overrides + two-gate `proactive_enabled` /
  `enforce_proactive_bind` reusing the enterpriseV1 `_is_loopback_host` helper.
- **UPG-PRO-7 (daemon endpoint) — DONE** as `POST /v1/proactive` (structured window in → packed
  context out), the minimal subset the proxy needs. The **window assembly** for the proxy is done
  in-process from the request body (`request_window.assemble_window`), not from a transcript file —
  the proxy already has the full canonical conversation, so the transcript-tail reader (UPG-PRO-2/3)
  is **not needed for the proxy** and remains a Phase-1 (hook-path) task.

### New capability: org-wide caching (design §14)
- **Artifact cache — DONE** (`agent/proactive/cache.py` `ArtifactCache`; wired into `service.search`
  and `service.recall_scored`). Exact-identity keying + index-epoch invalidation (code epoch;
  notes-mutation sequence). Approximate-reuse mechanism present, default off (`similarity_threshold`).
  Metrics on `/v1/status`.
- **Response cache — DONE** (`ResponseCache`, exact-match byte-identical, TTL, off by default, local
  only). Semantic-similarity response caching intentionally **not built** (design §14.3).

### Deferred / new tasks this work exposes
- **UPG-PRO-2/3 (transcript window reader + extractors)** — still required for the **hook** delivery
  seam (Phase 1); the proxy sidesteps them. When built, they feed the same `/v1/proactive` endpoint.
- **UPG-PRO-1 route flag** (`/v1/recall?with_scores`) — deferred; do when a non-proxy caller needs
  structured scores over REST.
- **UPG-PRO-M2** — symbol-definition matcher via `locate` (a matcher + threshold).
- **UPG-PRO-CACHE-APPROX-STUDY** — measure the approximate-artifact-reuse threshold-vs-quality
  trade before considering enabling it by default (design §14.2).
- **UPG-PRO-13 (metrics/economics harness)** — the proxy exposes injection counts + cache metrics
  on status; the offline precision/kill-criteria harness is still Phase-1 work.
