# vectr adoption cases

Caller-behavior + ergonomics cases run by `vectr-adoption-reviewer`. **Primary signal = first-person dogfooding**: the reviewer accomplishes a real task with vectr's own MCP tools and logs the friction it personally hits (zero `claude -p` quota). Secondary = replaying Claude Code session transcripts (`~/.claude/projects/<cwd-slug>/*.jsonl`, full tool_use+tool_result) for cross-model metrics. A live `claude -p` run is needed only at a sentinel-authorized gate. Seeded from the eval-v2 N=1 audit (2026-06-20).

Each case: **id · what it measures · threshold (pass) · origin · status · classification (product / harness).**

---

## F4 — hooks-ON memory double-dip
- **Measures:** in a hooks-installed (arm-C) session, after `/compact` the recalled notes are injected by the `UserPromptSubmit` hook AND vectr's CLAUDE.md nudges the model to ALSO self-call `vectr_recall` → memory delivered twice.
- **Pass:** in a hooks-ON transcript, count of `vectr_recall` tool_use calls *after* a hook injection of notes == 0 (or the injected block explicitly says "notes already loaded, do not recall"). Total tokens for identical output should not exceed the no-hooks (arm-D) baseline.
- **Origin:** Opus C self-recalled 35,170 chars on top of the hook inject; total 156,907 vs arm-D 155,604 for identical 5/5 output. Sonnet C 23,126 chars likewise.
- **Status:** failing
- **Classification:** product (integration design — hook-aware recall suppression). UPG-11.5.

## BV-MODEL-PIN — eval runs the model it claims
- **Measures:** `run_eval_v2.py --model opus` must resolve to the intended full ID, not a silent older minor.
- **Pass:** every transcript's `message.model` == the pinned full ID (e.g. `claude-opus-4-8`); the runner pins the full ID, not the alias.
- **Origin:** `--model opus` resolved to `claude-opus-4-7`, NOT 4-8 — the entire N=1 "Opus" matrix ran on the wrong Opus and must be re-baselined.
- **Status:** failing
- **Classification:** harness (a real user never hits this; legitimate benchmark-side fix). BV-MODEL-PIN.

---

---

## Ergonomics / affordance candidates (first-person dogfooding — to confirm & quantify)
These are friction hypotheses a result-quality metric can't see; the reviewer must hit them in real use, then turn each into a measurable case + UPG item.
- **ERG-recall-size** — `vectr_recall` can return a block too large to act on (~20k+ chars), relevant note buried. Candidate threshold: recall payload bounded + most-relevant-first; reviewer reports whether it could act on the response without re-reading.
- **ERG-remember-nextstep** — `vectr_remember` returns no confirmation of what was stored or what to do next, leaving the agent unsure it landed / whether to keep working from it. Candidate: remember returns a short confirmation + (optional) suggested next action.
- **ERG-search-rereads** — `vectr_search` returns a chunk but the agent still `Read`s the whole file (see product F3). Candidate: expand-to-symbol/file affordance; metric = full-file reads per search.
- **ERG-locate-trust** — a `vectr_locate` miss (symbol-not-found / wrong same-named symbol) makes the agent distrust the tool and fall back to grep for the rest of the session.

---

## ERG-eviction-spam — ACTION REQUIRED footer fires every call post-threshold
- **Measures:** after the eviction time/token threshold is crossed, every single vectr MCP response (search, locate, recall, evict_hint) appends the full "─── ACTION REQUIRED ───" block (~250–800 chars per call), regardless of whether the agent just called `vectr_remember` moments earlier. The signal becomes noise when repeated on every turn.
- **Pass:** the ACTION REQUIRED footer fires at most once per N consecutive MCP calls without an intervening `vectr_remember`, where N >= 3. After the agent calls `vectr_remember`, no footer on the next K calls (K >= 2). The footer must NOT appear if the agent called `vectr_remember` in the immediately preceding turn.
- **Origin:** dogfooded 2026-06-20. Every call in a 10-call session had the footer after the time threshold was hit (auto_eviction_hint at `eviction_advisor.py:149`). The guard `_fresh_escalation()` fires at first threshold crossing but the footer still appears on subsequent calls because the session token count keeps growing. In one session the footer appeared 8 consecutive times with no `vectr_remember` call between them — correctly nagging — but it also appeared immediately after a successful `vectr_remember`, cancelling the positive feedback loop.
- **Status:** failing (confirmed by first-person dogfood, 2026-06-20)
- **Classification:** product (ergonomics — eviction_advisor.py suppression logic). Candidate UPG item.

## ERG-locate-snippet-mismatch — vectr_locate shows wrong code in snippet
- **Measures:** `vectr_locate("X")` correctly identifies the file:line where symbol X is defined, but the snippet shown in the result is NOT the function/class body — it shows surrounding code (e.g., the parent class body context lines above the definition). The agent cannot verify from the snippet that it has the right thing and must `Read` the file.
- **Pass:** for any `vectr_locate(name)` result, the snippet begins at or within 3 lines of the actual `def`/`class` statement for the located symbol. At least the function signature must appear in the snippet.
- **Origin:** dogfooded 2026-06-20. `vectr_locate("handle_tools_call")` returned `mcp_server.py:448` but snippet showed `MCP_TOOLS = _EXPLORATION_TOOLS + ...` — class attribute list, not the function body. `vectr_locate("index_file")` second result (`symbol_graph.py:543`) showed UPG-10.3 tree-walking code, not an `index_file` definition. Direct fallout: agent distrust → file Read, 1 extra turn per locate call.
- **Status:** failing (confirmed by first-person dogfood, 2026-06-20)
- **Classification:** product (retrieval/display — snippet extraction anchored at wrong line). Cross-ref ERG-locate-trust. Candidate UPG item.

## ERG-bare-name-search-miss — vectr_search on a bare symbol name misses the definition
- **Measures:** when an agent issues `vectr_search("symbol_name")` with no surrounding description, the semantic embedding returns thematically related chunks (sub-words, neighboring class names) instead of the definition. UPG-11.1's symbol-identity boost only re-ranks already-retrieved candidates; it cannot rescue a definition that was not retrieved by the embedding step.
- **Pass:** `vectr_search("X")` where X is an exact snake_case function name present in the index must return the definition of X at rank 1 or 2 (within top-3). OR: the CLAUDE.md / tool description must steer agents away from this usage pattern (instruct them to use `vectr_locate` for exact names, `vectr_search` for concepts).
- **Origin:** dogfooded 2026-06-20. `vectr_search("index_file")` (n_results=5) returned `IndexResponse`, `IndexRequest`, a pool.query JS snippet, `locate` function, and a Pydantic model — NOT `CodeIndexer.index_file` at indexer.py:690. `vectr_search("search function definition")` also missed all three `search` method definitions. Calls-to-answer = 2 (search + fallback to locate or descriptive query). This is a systemic retrieval gap where the tool description ("NOT when you already know the symbol name — use vectr_locate instead") is the correct routing, but agents don't always honour it.
- **Status:** failing (confirmed by first-person dogfood, 2026-06-20)
- **Classification:** product (retrieval — short-name query embedding blind spot) + ergonomics (CLAUDE.md routing instruction needs sharper negative example). Cross-ref F1. Candidate UPG item.

## Standing adoption metrics (recompute every loop, no fixed threshold yet — watch for regression)
- **grep-fallback rate** — count of `Grep`/`Read` calls that follow a vectr_search miss (model abandoning vectr). Cross-check each against a product case; a fallback usually = a retrieval miss (F1/F2).
- **calls-to-answer** — vectr_search/locate calls before the model acts on a result. >1 for a single need = retrieval insufficiency.
- **post-compact recall** — exactly one self-recall in no-hooks mode; correct suppression in hooks mode (see F4).
- **What's holding (do not regress):** high adoption in all vectr arms (19–27 calls, 8–11 notes), no tool-as-bash flail (UPG-10.1), no built-in auto-memory fallback (UPG-10.2).
