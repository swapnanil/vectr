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

## Standing adoption metrics (recompute every loop, no fixed threshold yet — watch for regression)
- **grep-fallback rate** — count of `Grep`/`Read` calls that follow a vectr_search miss (model abandoning vectr). Cross-check each against a product case; a fallback usually = a retrieval miss (F1/F2).
- **calls-to-answer** — vectr_search/locate calls before the model acts on a result. >1 for a single need = retrieval insufficiency.
- **post-compact recall** — exactly one self-recall in no-hooks mode; correct suppression in hooks mode (see F4).
- **What's holding (do not regress):** high adoption in all vectr arms (19–27 calls, 8–11 notes), no tool-as-bash flail (UPG-10.1), no built-in auto-memory fallback (UPG-10.2).
