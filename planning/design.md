# Proactive participation — design

Status: design of record for the experimental proactive-participation workstream (internal
codename "god mode" — never product-facing; see §12 Naming). Read `research.md` first; this
doc builds directly on its verdicts and its file:line evidence.

Product thesis, kept at the centre of every decision: **vectr kills context debt.** Pull-based
recall already relieves it (the agent asks, the note arrives). Proactive participation is the
logical endgame — the agent should not even have to remember to ask; the right note or search
result arrives exactly when it is relevant. Every design choice below serves that, and **every
injection must be worth more than the tokens and attention it costs** — an irrelevant hint is
not neutral, it is harmful (distraction + prompt-cache churn). The design is therefore as much
about *when to stay silent* as about what to say.

---

## 1. Guiding constraints (non-negotiable)

1. **Localhost / solo mode only.** Off and refused whenever the daemon is bound to a
   non-loopback address (team / shared-instance mode). See §10 and the boundary in §11.
2. **Off by default, explicit experimental opt-in.** No proactive behaviour without the user
   turning it on.
3. **No LLM/API calls, ever.** All intelligence is embeddings + deterministic structural
   lookups, computed locally. vectr makes no outbound inference call for this feature.
4. **No query-side heuristics — hard rule (three strikes).** No keyword/regex classification,
   rerouting, or gating logic anywhere. Triggering = deterministic structural matches +
   embedding-similarity thresholds + additive deterministic packing. §7 shows the compliance
   line-by-line.
5. **Reads the most sensitive data on the machine (the conversation).** Privacy design (§9) is
   a first-class requirement, not an afterthought.
6. **Delivery is only as good as what Claude Code sanctions.** No unsupported injection tricks
   (no transcript-file writes, no undocumented sockets). Event-synchronous in v1.

---

## 2. Architecture overview

Four layers, cleanly separated so each can be built, tested, and reasoned about alone. The
separation mirrors the research's two halves: **intelligence** (adapter + matching) vs
**delivery** (channels), with the **gating** layer as the economics gate between them.

```
                         vectr daemon (localhost, one per workspace)
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                                                                                │
  │   (1) STREAM ADAPTER            (2) MATCHING ENGINE         (3) GATING POLICY   │
  │   normalise transcript   ─────▶  structural + semantic ───▶  budget / floor /   │
  │   → rolling window of            candidates, each SCORED     dedup / cooldown   │
  │   ConversationEvents             • note store (cosine)       → 0..K items to     │
  │                                  • code index (search)        emit, or nothing   │
  │                                  • symbol graph (locate)                        │
  │                                  • file/symbol exact match              │        │
  └───────────────────────────────────────────────────────────────────────┼────────┘
                                                                            │
   (4) CHANNEL ADAPTERS  ◀──────────────────────────────────────────────────┘
   Phase 1: Claude Code hooks — additionalContext (model) / systemMessage (user)
            SessionStart · UserPromptSubmit · PreToolUse · PostToolUse
   Phase 2: (only if a sanctioned async channel appears upstream)
   Phase 3: ANTHROPIC_BASE_URL localhost proxy — append after last cache breakpoint
```

**Trigger model (the crux, decided by the research).** Delivery is event-synchronous: a
channel adapter fires *only* at a sanctioned moment (a hook event, or a proxied request). At
that moment the adapter asks layers 1→3 for the best 0..K items to inject. There is **no**
autonomous mid-loop push, because none exists (research §2.2). The stream adapter supplies the
*intelligence* (a rich, current understanding of the conversation) so that when a delivery
moment arrives, vectr injects the best thing — not merely something matched to a single
payload field, as the current `UserPromptSubmit` hook does.

### 2.1 Where the layers live in the codebase

- Stream adapter, matching engine, gating policy: a new package, e.g.
  `agent/proactive/` (adapter.py, matcher.py, gate.py, config), owned by the daemon.
- Channel adapters (Phase 1): extend the existing hook path — `main.py:1261 cmd_hook` and a new
  daemon endpoint the hook calls (analogous to today's `_fetch_recall`), plus the hook writer
  `main.py:523 _write_claude_hooks` gains a PostToolUse group and broader PreToolUse matcher.
- Proxy (Phase 3): a separate ASGI app / process, `agent/proactive/proxy.py`, not on the
  existing daemon port.

---

## 3. Component — stream adapter

**Job:** turn raw transcript records into a normalised, bounded **rolling window** of
`ConversationEvent`s that the matcher can consume, without ever persisting content.

`ConversationEvent` (in-memory only): `role` (user|assistant), `text` (message text and/or
assistant reasoning summary), `tool_name`, `tool_input` (dict), `tool_result_excerpt`,
`file_paths` (extracted deterministically from tool inputs/results), `symbols` (identifiers
seen in tool traffic), `timestamp`, `uuid`.

**Decision — Phase 1 reads the window on demand, not via a continuous observer.**

The Claude Code hook payload already contains `transcript_path` (research §2.1). So at each
delivery moment vectr **reads the last N conversational records from that exact file**, builds
the window, matches, injects — then discards the window. Rationale:

- **Lower privacy surface.** No always-on background thread tailing the user's most sensitive
  file; content is read transiently, only at a sanctioned delivery moment, only for the
  session that fired the hook, and is never written anywhere.
- **More deterministic.** The window is a pure function of the file's tail at hook time; no
  observer-timing races, no stale state.
- **Simpler and safe-by-default.** No new long-lived component; reuses the existing short-lived
  hook subprocess → daemon call shape.
- **Async push doesn't exist anyway** (research §2.2), so the only thing a continuous observer
  would enable — computing something *between* hook firings and pushing it — has no delivery
  channel in v1. Building the observer now would be speculative.

A **continuous tail** (reusing `agent/watcher.py`'s watchdog `Observer` + debounce pattern) is
specified as a *future* capability, activated only if/when a sanctioned async delivery channel
lands upstream, or for the Phase-3 proxy (which sees each request directly and needs no file
tail at all). The parsing code is shared between on-demand and continuous modes.

**Robustness requirements:**

- Parse defensively: unknown `type` or content-block types are skipped, never fatal (schema is
  not a public contract — research §5.4). Partial trailing lines (mid-append) are tolerated.
- Window is bounded by count **and** token estimate (e.g. last 20 conversational records or
  ~2k tokens, whichever is smaller) so the embed cost and matching cost stay flat regardless of
  session length.
- Assistant `thinking` blocks: included behind a config flag, default decided by the offline
  precision experiment (UPG-PRO tasks); the adapter can emit them or not without touching the
  matcher.

**Stream-adapter interface** isolates the harness. `StreamAdapter.window(session) →
list[ConversationEvent]` has one concrete implementation for Claude Code v1; a second harness
would add another implementation, leaving matching/gating untouched (research §1.5).

---

## 4. Component — matching engine

**Job:** given the rolling window, produce a set of **scored candidate items**, each of which
is one concrete thing that could be injected. Purely additive: it runs every matcher and
unions the results; it never branches on the *content* of the window.

Four matchers, each producing `Candidate{kind, payload, score, provenance, anchor_id}`:

1. **Structural file match (deterministic).** File paths extracted from `tool_use.input`
   (e.g. the `file_path` a Read/Edit/Write targets) and from `tool_result` content are matched
   by **exact string equality** against note file-anchors (the same anchoring that today's
   `PreToolUse` gotcha injection uses — research §2.1) and against the code index's known
   files. Match → candidate: the gotcha/finding notes anchored to that file, and/or "this file
   is indexed at …". Score = 1.0 (exact match is maximal confidence). *No keyword logic — pure
   path equality.*
2. **Structural symbol match (deterministic).** Identifiers appearing in tool traffic (e.g. a
   symbol name in a grep query or a `locate` argument) are resolved against the **symbol graph**
   via the existing `/v1/locate`. Match → candidate: "symbol X is defined at file:line". Score
   from the locate resolver's own confidence. *Exact/prefix symbol resolution, not
   classification.*
3. **Semantic note match.** The window text is embedded (`embed_query_fn`) and cosine-matched
   against the `working_memory` collection via the existing `_semantic_recall` path
   (research §3), returning notes **with their similarity scores**. Candidates below the
   similarity floor are dropped inside the existing UPG-5.1 cutoff. *Numeric threshold only.*
4. **Semantic code match.** The window text is run through the existing `/v1/search` (hybrid
   semantic+BM25), returning code chunks with scores. Candidates below a floor are dropped.
   *Numeric threshold only.*

All four run unconditionally every time; the union of their outputs (with scores) is handed to
the gate. The matcher has **no branch that inspects window content to decide which matcher to
run** — that would be query-side routing (forbidden, §7). Turning a matcher off is a static
config toggle, not a runtime content decision.

**Reuse, not rebuild:** matchers 1–4 call `/v1/recall`(scored), `/v1/search`, `/v1/locate`,
and the file-anchor lookup that already exist. The only genuinely new retrieval work is
returning **scores** from recall (§3 item 2) and assembling the window as the query.

---

## 5. Component — gating policy (the economics gate)

**Job:** decide how many of the scored candidates (0..K) actually get emitted, so that every
injection clears its cost. This is where noise is controlled. All rules are deterministic and
numeric.

**Rules (applied in order):**

1. **Similarity floor.** Semantic candidates must clear `min_similarity` (reuse today's `0.35`
   default; per-channel tunable via config/env). Structural candidates (exact file/symbol
   match) bypass the floor — an exact match is not a fuzzy guess.
2. **Per-event injection budget.** At most `K` items and at most `T` tokens per delivery moment
   (defaults e.g. `K=3`, `T≈800` chars, well under the 10k hook cap). Higher-confidence
   candidates fill the budget first; ties broken deterministically (score desc, then
   provenance rank structural>note>code, then `anchor_id` asc). No randomness.
3. **Dedup + cooldown.** Never inject the same item twice within a window. A per-session
   ledger of recently injected `anchor_id`s (bounded ring / TTL, e.g. last 30 items or last N
   turns) suppresses repeats. This reuses the per-session-state pattern already in
   `integrations/mcp_server/_session.py` (calls-since-save tracking). A candidate whose
   `anchor_id` is in the ledger is dropped before budgeting.
4. **Per-channel policy.** PostToolUse fires far more often than UserPromptSubmit, so it gets a
   tighter budget and a longer cooldown (e.g. only structural exact matches on PostToolUse by
   default; semantic matches reserved for UserPromptSubmit). This is a static per-channel
   config, not a content decision.
5. **Empty is normal.** If nothing clears the floor + budget, vectr emits **nothing** — the
   existing `_emit_hook_context` already prints nothing (not an empty envelope) when there is
   no text, so a quiet turn stays silent (`main.py:1230`).

**Output packing.** Emitted items are packed into a single `additionalContext` block, ordered
deterministically, each line self-describing ("note #12 (gotcha, anchored to <file>): …",
"<symbol> defined at <file:line>", "search hit <file:line> …") and carrying its provenance so
the model can trust/verify it. Packing is additive and deterministic — it concatenates
selected candidates; it never rewrites or reasons about them.

---

## 6. Component — channel adapters + phased roadmap

### Phase 1 — Proactive context via synchronous hooks (RECOMMENDED FIRST, highest value/risk ratio)

Deliver intelligence through the four sanctioned model-facing hook events. This is the whole
product for most users and needs **no** async channel and **no** proxy.

- **UserPromptSubmit** — replace today's single-prompt recall with a **rolling-window** match
  (matchers 3+4 primarily). This is the biggest single upgrade: the agent's proactive context
  now reflects the last several turns, not just the sentence the user typed.
- **PreToolUse** — keep the structural gotcha injection (matcher 1) but broaden the matcher
  beyond `Edit|Write` to also cover reads/greps (still a static matcher list decided by the
  hook writer, not content logic — research §2.1). Add matcher 2 (symbol resolution) when the
  tool input names a symbol.
- **PostToolUse (new)** — the untapped high-frequency channel. After a Read/Grep/Glob/Edit,
  inject structural candidates: notes anchored to the file just touched, the definition of a
  symbol just grepped. Tight budget + cooldown (§5.4) so per-tool injection doesn't become
  noise. `updatedToolOutput` is **not** used (rewriting tool output is riskier and out of
  scope); only `additionalContext` is emitted.
- **SessionStart** — unchanged boot behaviour, plus on the `compact`/`resume` matcher it
  re-primes after compaction (already wired — research §2.1).
- **systemMessage (user-facing), sparingly** — a one-line "vectr surfaced N notes / a
  definition for X" to the *user* on high-confidence structural injections, so the human sees
  vectr is participating. Off by default or rate-limited; the model-facing `additionalContext`
  is the primary path.

Phase 1 is the literal "vectr in the middle" seam that exists today: hooks sit in the
request-construction path and deterministically add context before the model sees the turn.

### Phase 2 — Async delivery (CONDITIONAL, likely not v1)

Only if a sanctioned channel to push into a *running* Claude Code CLI session appears upstream
(track GH #27441 / #53049 / MCP sampling #1785 — research §2.2). Until then this phase is a
**watch item, not buildable**. If it lands, the continuous stream tail (from §3) plus the
gating policy are reused unchanged; only a new channel adapter is added. Do not build
speculatively.

### Phase 3 — Localhost API proxy (the literal wire "in the middle")

`ANTHROPIC_BASE_URL` → a vectr localhost proxy that forwards to the real Anthropic API and
appends a deterministic context block per request. Highest power (sees the full canonical
conversation every request; not limited to hook moments), highest risk. Built last, behind its
own opt-in, with these hard requirements (research §2.4):

- **Cache discipline:** inject **after the last `cache_control` breakpoint** (append as newest
  content); never touch `tools`/top-of-`system`. Reprocessing the small injected block each
  turn is the accepted cost.
- **Key hygiene:** forward the caller's key untouched; **never store or log it** (coordinate
  with the security workstream's honesty standard). Match the `ANTHROPIC_AUTH_TOKEN` / apiKey
  precedence.
- **Fail-open:** proxy failure must transparently pass the original request through (or the CLI
  must fall back) — proxy down must never mean agent down.
- **Streaming + tool_use passthrough** in the Anthropic `/v1/messages` SSE shape; do **not**
  adopt LiteLLM (its message-mutation hook is bypassed on `/v1/messages` — research §2.4). A
  minimal custom Anthropic-shaped proxy is the right build.
- **Non-first-party host caveats:** `ENABLE_TOOL_SEARCH`, Remote-Control-disabled — documented,
  not silently accepted.

### Recommendation

**Build Phase 1 fully; keep Phase 2 as a labelled watch item; design Phase 3 now but ship it
last.** The evidence is decisive: Phase 1 captures most of the product value using only
sanctioned, mostly-already-wired channels, at low risk; async push does not exist to build;
the proxy's correctness bar (cache, keys, availability) makes it a deliberate, later, opt-in
step rather than the entry point.

---

## 7. How the no-query-heuristics hard rule is honored

The forbidden pattern is *inspecting query/conversation content to classify it and branch/route
or gate on that classification*. This design contains none of it. Explicitly:

- **Triggering is not content-classified.** A delivery moment is a *harness event* (a hook
  fired, a request proxied), never a decision vectr makes by reading the conversation and
  judging "this looks like a question about X, so act". vectr acts at every sanctioned moment
  and lets thresholds decide whether anything is emitted.
- **Matchers do not route on content.** All four matchers run unconditionally on every window;
  there is no `if window mentions "test" then run the test-matcher` branch. Which matchers are
  enabled is a **static config toggle**, not a runtime content read.
- **Structural matching is pure equality.** File-path and symbol matches are exact string
  equality against note anchors and the symbol graph — deterministic lookups, no regex
  classification of the query.
- **Semantic matching is a numeric threshold.** Cosine similarity vs a floor (`min_similarity`),
  reusing the existing UPG-5.1 mechanism. A number crossing a threshold is not keyword logic.
- **Packing is additive and deterministic.** The gate unions candidates, sorts by
  (score, provenance rank, id), truncates to budget, and concatenates. No branch decides
  *what kind* of help to give based on *what the query is about*.

If any future change wants to say "when the user seems to be doing X, inject Y", that is the
forbidden pattern and must be rejected — the correct expression is always "add a matcher/anchor
+ a threshold", never a content classifier.

---

## 8. Noise economics — the full spec

Every injection is a debit against tokens *and* attention; an irrelevant one is a double loss
(distraction now, prompt-cache churn on the proxy path). The economics knobs (all in
`config.yaml` under a new `proactive:` block, env-overridable, matching the existing
`ranking:` config style):

| Knob | Default (starting point) | Purpose |
|---|---|---|
| `enabled` | `false` | Master opt-in (also gated on loopback bind, §10) |
| `min_similarity` | `0.35` | Semantic floor; structural matches bypass it |
| `max_items_per_event` (`K`) | `3` | Budget: max candidates emitted per delivery moment |
| `max_chars_per_event` (`T`) | `800` | Budget: token/char ceiling per injection |
| `cooldown_items` | `30` | Dedup ledger size (recent anchor_ids suppressed) |
| `channels.post_tool_use.enabled` | `true` | Toggle the high-frequency channel |
| `channels.post_tool_use.structural_only` | `true` | PostToolUse emits only exact-match candidates by default |
| `include_thinking` | (decided by experiment) | Whether assistant reasoning enters the window |
| `user_facing_systemMessage` | `false` | Whether to also show the user a one-liner |

**Cache-thrash note (proxy path only):** on Phase 3 the injected block is appended after the
last cache breakpoint, so a changing injection never invalidates the cached prefix; on the hook
path the injection is a `<system-reminder>` the harness places itself, so vectr does not manage
cache breakpoints directly.

---

## 9. Privacy / consent / safety design

This feature reads the conversation stream — the most sensitive data on the machine. The design
treats that as a hard constraint:

- **Localhost only, refused otherwise.** Enabled only when the daemon is bound to a loopback
  address. If the security workstream's `--host` puts the daemon on a non-loopback interface
  (team mode), proactive mode is **forced off and the attempt is refused with a clear error**
  (§10, §11). Team mode and proactive mode are mutually exclusive by construction.
- **Off by default; explicit experimental opt-in.** `proactive.enabled=false` out of the box;
  turned on by config or `vectr proactive on`, with a one-time notice that vectr will read the
  local transcript to compute proactive context.
- **All local, embeddings-only, zero inference.** Stated loudly: matching is cosine similarity
  + deterministic lookups; vectr makes **no LLM/API call** and sends nothing off-machine for
  this feature. (Consistent with the security doc's data-handling statement: "Nothing leaves
  the machine.")
- **No transcript content is persisted.** The rolling window lives in memory for the duration
  of one delivery computation and is discarded. The **only** thing that persists is what the
  agent explicitly stores via `vectr_remember` — unchanged from today. Ephemeral conversation
  embeddings are held in memory only and never written to Chroma or SQLite.
- **Never logged.** Window content is never written to any vectr log. If the security
  workstream's opt-in audit log (`VECTR_AUDIT_LOG`) is enabled, proactive injections are
  recorded as a new `PROACTIVE_INJECT` audit event carrying **metadata only** (event type,
  count of items, anchor ids, scores) — never the conversation text and never the injected
  bodies. This reuses their audit machinery; it does not redesign it.
- **Auditable + visible.** Injection counts already surface in `vectr status`
  (`_hook_injection_line`, `main.py:1397`); proactive injections extend that so the human can
  always see how active the feature is (a working memory system vs a silent one).
- **Kill switch.** `vectr proactive off` (or `proactive.enabled=false`) instantly reverts to
  today's pull-based behaviour; no state to unwind.

---

## 10. Localhost-only enforcement (mechanism)

Two independent gates, both must pass:

1. **Bind check.** Proactive mode reads the daemon's bind address (today hardcoded
   `127.0.0.1`, `main.py:872`; the security workstream generalises it to `--host`). If the
   resolved bind is not a loopback address, proactive mode is disabled and any explicit
   `proactive.enabled=true` produces a startup refusal error naming the conflict.
2. **Team-mode check.** If `VECTR_API_KEY` is set *and* the bind is non-loopback (the security
   doc's team-mode signature), proactive mode is off regardless of config. (A key on a loopback
   bind — the "shared host" hardening model — does **not** by itself disable proactive mode;
   the deciding factor is the non-loopback bind that defines team mode.)

The refusal is fail-closed: any ambiguity about whether the daemon is in a shared/team posture
resolves to proactive-off.

---

## 11. Interaction boundaries with the team-mode / security workstream

Reference: `security-features-design.md` (docs repo). This design **consumes** that workstream's
primitives and never redefines them:

- **Bind guard / `--host`:** proactive mode keys its localhost-only enforcement (§10) off the
  same bind address that the security workstream's bind guard governs. If they add a
  `is_loopback(host)` helper, proactive mode uses it rather than duplicating the check.
- **Audit log:** proactive injections become a `PROACTIVE_INJECT` event **in their existing
  audit logger** (`VECTR_AUDIT_LOG`, off by default). Metadata only (§9). No new logging
  subsystem.
- **Encryption / at-rest:** unaffected — proactive mode persists nothing new; the rolling
  window is memory-only. Note encryption (content/title) is orthogonal.
- **Scope isolation:** proactive mode is per-workspace like everything else; it reads only the
  transcript(s) for the daemon's own `workspace_root`, never another workspace's.
- **Mutual exclusion with team mode:** stated in both directions — the security doc can note
  that proactive mode is a solo-only feature; this doc refuses to run under a non-loopback
  bind. No overlap in the config surface (`VECTR_PROACTIVE` / `proactive:` block is new and
  disjoint from the security env vars).

Open coordination item for the orchestrator: confirm the final name of the loopback-detection
helper and the audit event-type enum so the two workstreams share them rather than fork.

---

## 12. Naming

"God mode" is the internal codename and must never appear in product copy, docs, config keys,
CLI, or UI. vectr has plain features, not marketing tiers, and its copy is editor-agnostic.

Three candidate product names:

1. **Proactive context** *(recommended).* Plain, accurate, and encompasses both halves of what
   is delivered (working-memory notes *and* code-search/definition results = "context"). Mode
   toggle reads naturally: "proactive mode". Config `proactive:` block; env `VECTR_PROACTIVE`;
   CLI `vectr proactive on|off`. Editor-agnostic. No tier connotation.
2. **Proactive recall.** Concrete and catchy (recall is an established vectr noun), but biases
   toward notes and under-sells the code-search/definition injections, which are equally part
   of the feature.
3. **Ambient context.** Evocative of "always present", but "ambient" can imply always-on/heavy
   background processing — which the Phase-1 on-demand design deliberately is *not*; risks
   mis-setting expectations about resource use and privacy.

**Recommendation: "Proactive context"**, with the user-facing mode called **proactive mode**.
It states exactly what the feature does, spans notes + code, carries no tier/marketing framing,
and is editor-agnostic. The config/env/CLI surface (`proactive`) is short and unambiguous.

---

## 13. Config surface (proposed, all new, disjoint from security env vars)

```yaml
# config.yaml (new block, mirrors the existing `ranking:` style)
proactive:
  enabled: false            # master opt-in; also requires loopback bind (§10)
  min_similarity: 0.35      # semantic floor; structural matches bypass
  max_items_per_event: 3
  max_chars_per_event: 800
  cooldown_items: 30
  include_thinking: false   # revisited after the offline precision experiment
  user_facing_systemMessage: false
  channels:
    user_prompt_submit: { enabled: true }
    pre_tool_use:       { enabled: true }
    post_tool_use:      { enabled: true, structural_only: true }
```

Env overrides follow the established pattern (deployment/runtime → env), e.g.
`VECTR_PROACTIVE=1`, `VECTR_PROACTIVE_MIN_SIMILARITY`, `VECTR_PROACTIVE_MAX_ITEMS`. Keys are
read at request/startup time; none are persisted beyond `config.yaml` defaults.

The Phase-3 proxy + caching add a `proxy:` and a `cache:` sub-block (both under
`proactive:`, so the `VECTR_PROACTIVE_*` env prefix stays consistent):

```yaml
proactive:
  proxy:
    enabled: false
    host: 127.0.0.1                 # localhost only; a non-loopback bind is refused
    port: 8785
    upstream_base_url: https://api.anthropic.com
    inject: true
    inject_budget_ms: 40            # fail-open soft budget for the whole injection lookup
  cache:
    enabled: false
    max_entries: 2048
    ttl_seconds: 0                  # 0 = invalidation by index epoch only
    similarity_threshold: 1.0       # 1.0 = exact-identity keying (provably correct)
    response_cache:
      enabled: false                # exact-match byte-identical LLM response cache (proxy)
      ttl_seconds: 60
      max_entries: 256
```

---

## 14. Org-wide caching + telemetry (the shared-cache capability)

With team mode (a central shared vectr instance, `security-features-design.md` §7) plus a
proxy on each developer's machine, the org gains a **shared layer**. This section is the
rigorous possibilities map and the safe/unsafe decision for each — implement what is provably
correct, design (and refuse) the rest honestly.

### 14.1 The possibility map

| Capability | What is shared | Safety | Verdict |
|---|---|---|---|
| **Injection** (Phase 3, §6) | Nothing shared; each proxy injects locally from its own daemon | Safe (deterministic, additive, cache-append) | **SHIP** |
| **Org-wide vectr-artifact cache** | Search results, recall results, embedding computations computed on the team instance | Safe when keyed by exact identity + index epoch | **SHIP (exact keying)** |
| **Approximate artifact reuse** | A cached artifact reused for an embedding-similar (not identical) query | Trades exactness for hit-rate; documented staleness | **SHIP the mechanism, default OFF** |
| **Org-level telemetry** | "What are our agents asking", attributed, local/team-only | Safe (metadata, opt-in audit, never transmitted) | **SHIP via existing audit + metrics** |
| **LLM-response cache — exact** | Byte-identical full request → cached upstream response within a TTL | Safe (same request = valid sample) | **SHIP, off by default, local only** |
| **LLM-response cache — semantic** | A cached response served for a semantically-similar-but-not-identical request | **UNSAFE** — a wrong hit silently corrupts a stateful conversation | **DO NOT BUILD** |

### 14.2 Org-wide vectr-artifact cache (SAFE — the shippable value)

The team instance computes each artifact once and every connected client benefits, because
they all query the same daemon. What is cached: `/v1/search` results, scored recall results,
and embedding computations — the expensive, deterministic vectr-layer outputs.

**Cache-correctness guarantees shipped:**

1. **Exact-identity keying (default, `similarity_threshold: 1.0`).** The key is
   `sha256(kind + canonical(args) + index_epoch)`. Identical inputs against the same index
   produce the same key and therefore the same result. This is not a heuristic reuse; it is
   memoisation of a pure function.
2. **Invalidation-aware.** The key includes an **index epoch**: for code artifacts it is
   `total_chunks · last_indexed · embed_model · version_stamp`; for note artifacts it is a
   monotonic **notes-mutation sequence** bumped on every `remember`/`forget`/`forget_all`. A
   re-index or any note change changes the epoch, so a stale artifact can never be served — a
   cache miss, never a wrong answer. Staleness window = zero for exact keying: the moment the
   underlying state changes, prior keys stop matching.
3. **Bounded + expiry-aware.** LRU-bounded (`max_entries`), optional wall-clock TTL
   (`ttl_seconds`, `0` = rely on the epoch alone). No unbounded growth.
4. **Attributed + authenticated.** On a team instance the cache lives behind the same API-key
   auth as every other route, and the `X-Vectr-Client` attribution already threads through the
   audit log — so a shared hit is still recorded against the client that issued the query.
5. **Measurable.** Hits, misses, hit-rate, entries, evictions, bytes served, and an estimated
   tokens-saved figure are exposed on `/v1/status` (and summarised in `vectr status`), so the
   value is measured, not asserted.

**Approximate reuse (mechanism shipped, default OFF).** Setting `similarity_threshold < 1.0`
enables a deterministic nearest-above-threshold match: among cached entries carrying a key
vector, the one with the highest cosine similarity to the probe that clears the threshold wins,
ties broken by cache-key string ascending. This is fully deterministic and correct *as
specified* (it returns the nearest cached query's result), but its **residual** is that a
near-but-not-identical query receives an approximate result. That is a real correctness/quality
trade, so it is **off by default** (threshold `1.0`, exact) and its staleness semantics are
documented rather than enabled silently. The default daemon wiring uses exact keying only.

**No-query-heuristics compliance.** Cache keys are content-identity hashes and numeric cosine
thresholds — never a keyword/regex classification of the query, never a branch that routes on
what the query is "about". This is the same discipline as the rest of the design (§7).

### 14.3 LLM-response caching — the safety analysis

Agentic requests are context-heavy and stateful: the conversation grows monotonically, tools
have side effects, and a response is consumed as the next step of an ongoing plan. Serving the
**wrong** cached response does not degrade a result — it silently corrupts the conversation
from that turn on. Therefore:

- **Semantic-similarity response caching is NOT offered.** There is no threshold at which "this
  request is close enough to that one" is safe for stateful agent traffic: two requests that
  differ only in the last tool_result are "similar" yet must get different continuations. A
  wrong hit is undetectable to the agent and unrecoverable. We build none of it, and say so.
- **The one provably-safe class is exact match.** A **byte-identical full request within a
  short TTL** may be served a cached upstream response, because it *is* the same request — the
  cached response is a valid sample of the same distribution. Shipped as the proxy's
  `response_cache`: keyed on the exact forwarded bytes (+ path + cache-relevant headers),
  streaming responses cached as the exact ordered SSE byte chunks so a replay is byte-identical.
  It is **off by default**, **local to each proxy** (never shared org-wide — an org-wide LLM
  response cache multiplies the blast radius of any wrong hit), and expected to hit rarely
  (agentic requests are almost never byte-identical because the transcript grows every turn).
  We ship it for the narrow, safe, honest case and document that it will seldom fire.

### 14.4 Org-level telemetry

"What are our agents asking" is answered by the **existing** opt-in audit log
(`VECTR_AUDIT_LOG`) plus the new `PROACTIVE_INJECT` event and the cache metrics — all
metadata, all local/team-only, never transmitted off the host. The audit records query text
(that is what an audit log is for) only when the operator turns it on; `PROACTIVE_INJECT`
records ids/scores/counts only, never conversation text or note bodies (§9). No new telemetry
subsystem, no phone-home.

### 14.5 What is shipped vs deferred

- **Shipped:** exact-keyed artifact cache (search + scored recall) with index-epoch
  invalidation and metrics; the approximate-reuse mechanism (default off); the exact-match
  local response cache (default off); proactive injection through the proxy; metrics on status.
- **Deferred (honest):** turning approximate artifact reuse on by default (needs a
  threshold-vs-quality study); caching `/v1/locate`/`/v1/trace` artifacts (symbol-graph
  outputs are already sub-millisecond, so the cache would rarely pay for itself); a distributed/
  shared-across-hosts response cache (rejected on the blast-radius argument above, not merely
  deferred).
