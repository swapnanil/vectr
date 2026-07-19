"""
EvictionAdvisor — tells the LLM which chunks vectr can re-retrieve in <50ms.

The guarantee: anything listed here is fully indexed and re-retrievable in <50ms.
This is the reverse signal in the bidirectional protocol. The LLM calls vectr_remember
to save notes; the EvictionAdvisor proactively signals when retrieved content
is fully indexed and can be re-retrieved without re-reading the file.

The advisor tracks which chunks have been retrieved in the current session and
estimates their token cost. When the session hits a threshold, it fires an
eviction hint.

Limitation — Read/Bash blind spot (E2):
The advisor only tracks content delivered through vectr's own tools (vectr_search,
vectr_locate, vectr_trace, vectr_recall). Code the agent reads via the IDE's native
Read or Bash tools is invisible here. Token estimates are therefore a lower bound;
eviction may fire later than ideal on sessions that mix vectr retrieval with direct
file reading. The gap shrinks as agents adopt vectr_search as their primary navigation
path (Problem 3). Track `vectr_evict_hint_triggered` in benchmarks (E5) to monitor
real-world eviction uptake.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent.config import (
    EVICTION_HINT_MAX_IDS,
    EVICTION_REMEMBER_ESCALATION_CHUNKS,
    EVICTION_REMEMBER_ESCALATION_TOKENS,
    EVICTION_RETRIEVED_TOKEN_GATE,
)
from agent.render_paths import workspace_relpath


@dataclass
class RetrievedChunk:
    file_path: str
    lines: str
    symbol_name: str
    content: str
    # UPG-EVICT-SESSION-SCOPE: the exact vectr_fetch re-fetch key for this chunk
    # (`file_path:start_line-end_line`), when the caller had one. Empty for
    # chunks recorded from a surface that doesn't guarantee an exact indexed
    # chunk id (e.g. a symbol-graph snippet whose line range may not match a
    # stored chunk boundary) — never guessed, so eviction_hint() only ever
    # advertises a re-fetch key that is known to work.
    chunk_id: str = ""
    retrieved_at: float = field(default_factory=time.time)

    @property
    def estimated_tokens(self) -> int:
        return max(1, len(self.content) // 4)


class EvictionAdvisor:
    """
    Tracks what the LLM has retrieved this session and advises on eviction.

    Usage:
        advisor = EvictionAdvisor(eviction_threshold_tokens=4000)
        advisor.record(chunk)          # call after every vectr_search result
        hint = advisor.eviction_hint() # returns text the LLM can act on
    """

    def __init__(
        self,
        eviction_threshold_tokens: int = 40_000,
        tool_call_threshold: int = 10,
        retrieval_call_threshold: int = 1,
        time_threshold_seconds: float = 180.0,
        rearm_retrieval_calls: int = 4,
        retrieved_token_gate: int | None = None,
        remember_escalation_chunks: int | None = None,
        remember_escalation_tokens: int | None = None,
    ) -> None:
        # Fire when ANY of these conditions is met:
        #   cumulative injected chars ÷ 4 >= 40K  (vectr-tracked content)
        #   tool_call_count > 10                   (all MCP calls this session)
        #   retrieval_call_count > 1               (search/locate/trace calls only)
        #   elapsed seconds >= 180                 (wall-clock since session start)
        #
        # Why retrieval_call_threshold: the tool_call_count trigger counts all vectr MCP
        # calls, but the LLM typically makes only 3-4 per task (status + 1 locate + 2 search)
        # so threshold=10 never fires. Counting only retrieval calls (search/locate/trace)
        # and firing on the 2nd one gives a natural mid-task trigger: the LLM has found
        # something worth keeping before it switches into implementation mode.
        #
        # Why time_threshold: the eviction protocol requires vectr to see every retrieval
        # to track real context pressure (spec line 78), but the LLM uses native Read/Bash
        # for most reading — those calls are invisible here. A time-based trigger fires
        # regardless of tool mix, catching sessions where the LLM barely uses vectr at all.
        # arXiv:2310.08560, arXiv:2510.24699.
        self._threshold = eviction_threshold_tokens
        self._tool_call_threshold = tool_call_threshold
        self._retrieval_call_threshold = retrieval_call_threshold
        self._time_threshold_seconds = time_threshold_seconds
        # UPG-7.1: re-arm the auto-footer only after this many further retrieval
        # calls (or a fresh token/time crossing) since the last time it fired —
        # so the footer can't repeat on every response.
        self._rearm_retrieval_calls = rearm_retrieval_calls
        # UPG-11.15: suppress auto_eviction_hint() when accumulated retrieved
        # tokens since the last hint (or session start) are below this gate.
        # Prevents a burst of small-result searches from triggering the full
        # ACTION REQUIRED block before real context pressure has accumulated.
        # None → use the config value (EVICTION_RETRIEVED_TOKEN_GATE).
        self._retrieved_token_gate: int = (
            retrieved_token_gate if retrieved_token_gate is not None
            else EVICTION_RETRIEVED_TOKEN_GATE
        )
        # UPG-REMEMBER-BANNER-FATIGUE: gates auto_eviction_hint()'s escalated
        # directive on chunks retrieved since the last vectr_remember, not
        # just on raw context pressure — see note_remembered().
        self._remember_escalation_chunks: int = (
            remember_escalation_chunks if remember_escalation_chunks is not None
            else EVICTION_REMEMBER_ESCALATION_CHUNKS
        )
        # UPG-EVICT-ESCALATION-GATE-TOO-LOW: companion gate required IN ADDITION
        # to _remember_escalation_chunks — see note_remembered() and
        # _tokens_since_remember(). A single large search can satisfy the chunk
        # gate alone in one burst; requiring both makes that burst insufficient
        # on its own to re-trip the escalated directive.
        self._remember_escalation_tokens: int = (
            remember_escalation_tokens if remember_escalation_tokens is not None
            else EVICTION_REMEMBER_ESCALATION_TOKENS
        )
        self._chunks: list[RetrievedChunk] = []
        self._tool_call_count: int = 0
        self._retrieval_call_count: int = 0
        self._session_started_at: float = time.time()
        # (tokens, retrieval_count, wall_time) recorded the last time the auto
        # footer emitted; None until the first emit. Gates auto_eviction_hint().
        self._last_emit: tuple[int, int, float] | None = None
        # New chunks recorded since the caller's last vectr_remember (or
        # session start). Reset by note_remembered(). See
        # EVICTION_REMEMBER_ESCALATION_CHUNKS.
        self._chunks_since_remember: int = 0
        # Tracked total-token count as of the caller's last vectr_remember (or
        # 0 at session start). _tokens_since_remember() subtracts this baseline
        # from the running total (UPG-EVICT-ESCALATION-GATE-TOO-LOW).
        self._tokens_at_last_remember: int = 0
        # True once note_remembered() has fired and no escalated (ACTION
        # REQUIRED) directive has re-emitted since. While True, the NEXT
        # eligible auto-hint renders the softer, non-imperative form instead
        # of escalating immediately — repeating harsh wording on the very
        # next search after compliance trains the caller to ignore it.
        # Starts False: before the caller has ever called vectr_remember,
        # auto_eviction_hint() escalates immediately as before.
        self._soft_fire_pending: bool = False

    def record(
        self, file_path: str, lines: str, symbol_name: str, content: str,
        chunk_id: str = "",
    ) -> None:
        """Record a chunk that was delivered to the LLM this session."""
        # avoid duplicate tracking for the same file:lines
        key = f"{file_path}:{lines}"
        for i, c in enumerate(self._chunks):
            if f"{c.file_path}:{c.lines}" == key:
                # UPG-EVICT-RECENCY-DEDUP-BLIND: a repeat retrieval of the exact
                # same chunk (the LLM re-running the same query and getting the
                # same top hit) is the common case, and eviction_hint()'s
                # most-recently-retrieved-first ordering keys recency off a
                # file's position in self._chunks. Without this move, re-touching
                # a file never refreshes that position, so the file the caller
                # just retrieved again silently renders LAST instead of first.
                # Move the existing entry to the end (fresh recency) without
                # storing a duplicate or advancing any counter below — a dedup
                # hit must be a no-op for token/chunk totals, only recency moves.
                self._chunks.append(self._chunks.pop(i))
                return
        self._chunks.append(RetrievedChunk(
            file_path=file_path,
            lines=lines,
            symbol_name=symbol_name,
            content=content,
            chunk_id=chunk_id,
        ))
        self._chunks_since_remember += 1

    def record_results(self, results: list) -> None:
        """Record a batch of SearchResult objects (from searcher.py)."""
        for r in results:
            self.record(
                file_path=r.file_path,
                lines=str(r.lines),
                symbol_name=r.symbol_name or "",
                content=r.content,
                chunk_id=getattr(r, "chunk_id", "") or "",
            )

    def note_remembered(self) -> None:
        """Call when the caller's vectr_remember succeeds this session.

        Resets the chunks-since-last-remember counter and the tokens-since-
        last-remember baseline so auto_eviction_hint()'s escalated directive
        doesn't immediately re-fire on the very next retrieval
        (UPG-REMEMBER-BANNER-FATIGUE, UPG-EVICT-ESCALATION-GATE-TOO-LOW).
        Also arms the softer first-refire wording (see _soft_fire_pending).
        Does not touch should_evict()'s own token/call/time state — a
        vectr_remember doesn't reduce the actual context already retrieved.
        """
        self._chunks_since_remember = 0
        self._tokens_at_last_remember = self.total_tokens_in_session()
        self._soft_fire_pending = True

    def _tokens_since_remember(self) -> int:
        """Tokens tracked since the caller's last vectr_remember (or session
        start, if never called). Companion to _chunks_since_remember —
        see EVICTION_REMEMBER_ESCALATION_TOKENS."""
        return self.total_tokens_in_session() - self._tokens_at_last_remember

    def increment_tool_call(self) -> None:
        """Increment the total MCP tool call counter for this session."""
        self._tool_call_count += 1

    def increment_retrieval_call(self) -> None:
        """Increment the retrieval-specific call counter (search/locate/trace only)."""
        self._retrieval_call_count += 1

    def total_tokens_in_session(self) -> int:
        return sum(c.estimated_tokens for c in self._chunks)

    def should_evict(self) -> bool:
        elapsed = time.time() - self._session_started_at
        return (
            self.total_tokens_in_session() >= self._threshold
            or self._tool_call_count > self._tool_call_threshold
            or self._retrieval_call_count > self._retrieval_call_threshold
            or elapsed >= self._time_threshold_seconds
        )

    def _fresh_escalation(self) -> bool:
        """True on the first auto-emit, then only after a MATERIAL increase since
        the last one: another full token-threshold retrieved, `_rearm_retrieval_calls`
        more retrieval calls, or another time-threshold window elapsed. Keeps the
        per-response footer from repeating once pressure has already been flagged."""
        if self._last_emit is None:
            return True
        tokens0, retr0, t0 = self._last_emit
        return (
            self.total_tokens_in_session() - tokens0 >= self._threshold
            or self._retrieval_call_count - retr0 >= self._rearm_retrieval_calls
            or (time.time() - t0) >= self._time_threshold_seconds
        )

    def _tokens_since_last_hint(self) -> int:
        """Tokens accumulated since the last auto-hint emit (or session start)."""
        tokens_at_last = self._last_emit[0] if self._last_emit is not None else 0
        return self.total_tokens_in_session() - tokens_at_last

    def auto_eviction_hint(self, workspace_root: str = "") -> str:
        """Gated variant for the per-response footer (UPG-7.1 / UPG-11.15).

        Emits the hint only when BOTH conditions hold:
          1. Context pressure freshly escalates (UPG-7.1 — never on every response).
          2. Accumulated retrieved tokens since the last hint (or session start)
             have crossed _retrieved_token_gate (UPG-11.15 — suppresses bursts of
             small-result searches that add negligible context pressure).

        The explicit ``vectr_evict_hint`` tool and the ``/v1/evict`` endpoint
        still use ``eviction_hint()`` (ungated): an explicit ask always answers.

        UPG-REMEMBER-BANNER-FATIGUE / UPG-EVICT-ESCALATION-GATE-TOO-LOW: also
        suppressed unless BOTH enough new chunks AND enough new tokens have
        been retrieved since the caller's last vectr_remember (or session
        start) — a single large search can trip a chunk-only gate in one
        burst, so both must independently clear before the gate is satisfied
        at all. Below that, MCP dispatch's own turn-count soft nudge (or
        nothing) takes over instead of this method's wording.

        Once the gate is satisfied, the FIRST eligible fire after a
        vectr_remember renders the softer, non-imperative form (not ACTION
        REQUIRED); only a second-or-later eligible fire without an
        intervening vectr_remember escalates to the imperative directive
        (UPG-EVICT-ESCALATION-GATE-TOO-LOW) — so compliance is never
        answered with the same harsh wording on the very next response.
        """
        if not self.should_evict() or not self._fresh_escalation():
            return ""
        # UPG-11.15: even if should_evict() is true (e.g. retrieval_call_count
        # tripped), don't emit if the retrieved content since the last hint is
        # small.  The per-call-count trigger fires on the 2nd retrieval call,
        # but three 15-line methods contribute only ~100–150 tokens — far below
        # real context pressure.
        if self._tokens_since_last_hint() < self._retrieved_token_gate:
            return ""
        if (
            self._chunks_since_remember < self._remember_escalation_chunks
            or self._tokens_since_remember() < self._remember_escalation_tokens
        ):
            return ""
        escalate = not self._soft_fire_pending
        hint = self.eviction_hint(escalated=escalate, workspace_root=workspace_root)
        if hint:
            self._last_emit = (
                self.total_tokens_in_session(),
                self._retrieval_call_count,
                time.time(),
            )
            if not escalate:
                # Soft form consumed — the next eligible fire (without an
                # intervening vectr_remember) escalates.
                self._soft_fire_pending = False
        return hint

    def eviction_hint(
        self, escalated: bool = True, on_demand: bool = False, workspace_root: str = "",
    ) -> str:
        """
        Return a message listing chunks vectr can re-retrieve in <50ms.
        Always safe to call — returns an empty hint if nothing has been retrieved
        and no time-based pressure exists.

        escalated=True (the default) renders the imperative "ACTION REQUIRED"
        wording plus a trailing machine-readable ``needs_remember: true`` line a
        harness can key off without parsing prose. escalated=False renders the
        identical factual content (files, token count, re-fetch keys) with
        softer, non-imperative wording and omits the needs_remember line — used
        by auto_eviction_hint() for the first eligible re-fire after a
        vectr_remember (UPG-EVICT-ESCALATION-GATE-TOO-LOW).

        on_demand=True (UPG-7.2 — the explicit vectr_evict_hint tool / /v1/evict
        endpoint): the caller is proactively asking WHAT it can drop, so render
        an informational, eviction-focused listing distinct from the auto-footer
        — no imperative remember alarm and no needs_remember line (those belong
        to the gated auto-footer's escalation, not to a deliberate ask). The
        factual content (files, tokens, re-fetch keys) is identical. An explicit
        ask with nothing retrieved is a plain "clean context" answer, never a
        time-based remember nudge.
        """
        if not self._chunks:
            if on_demand:
                # A deliberate ask with nothing retrieved is not a remember
                # nudge — the caller learns its context is clean (the dispatch
                # layer renders the "nothing to evict" message on an empty hint).
                return ""
            # No vectr-tracked chunks, but time pressure still warrants a nudge
            elapsed = time.time() - self._session_started_at
            if elapsed >= self._time_threshold_seconds:
                if escalated:
                    return (
                        "─── ACTION REQUIRED ───\n"
                        "Call vectr_remember(content, tags=[...]) NOW before continuing.\n"
                        "Save: key type names, module paths, entry points, non-obvious patterns.\n"
                        "Your synthesized understanding does not persist automatically.\n"
                        "Call vectr_remember now, then continue your task.\n"
                        "needs_remember: true"
                    )
                return (
                    "Reminder: consider calling vectr_remember(content, tags=[...]) soon.\n"
                    "Save: key type names, module paths, entry points, non-obvious patterns.\n"
                    "Your synthesized understanding does not persist automatically."
                )
            return ""

        total_tokens = self.total_tokens_in_session()
        by_file: dict[str, list[RetrievedChunk]] = {}
        for c in self._chunks:
            by_file.setdefault(c.file_path, []).append(c)

        # UPG-EVICT-HINT-RECENCY: order files most-recently-retrieved-first,
        # not by first-ever-recorded order (a dict's insertion-order iteration
        # was an accident of implementation, not a chosen policy). The
        # caller's active working set — what it can most usefully drop from
        # context and re-fetch on demand — is whatever it retrieved most
        # recently, not whatever it happened to retrieve first this session.
        # A file's recency is the position (in self._chunks) of the LAST
        # chunk recorded for it; since record() appends monotonically, that
        # position doubles as a recency sequence number. Sorting descending
        # by this unique-per-file key is deterministic; the stable sort
        # tie-breaks by insertion order in the (practically unreachable)
        # case of equal keys.
        last_seq: dict[str, int] = {}
        for i, c in enumerate(self._chunks):
            last_seq[c.file_path] = i
        file_items = sorted(by_file.items(), key=lambda kv: last_seq[kv[0]], reverse=True)
        shown = file_items[:EVICTION_HINT_MAX_IDS]
        overflow = len(file_items) - len(shown)

        if on_demand:
            # UPG-7.2: explicit ask — an eviction-focused, informational framing
            # that answers "what can I drop?" rather than nudging a remember.
            lines = [
                "─── Evictable context (on demand) ───",
                f"{len(self._chunks)} retrieved chunk(s) (~{total_tokens} tokens) are safely"
                " droppable from your context — each is re-fetchable verbatim via"
                " vectr_fetch(ids=[...]) in <50ms, so you can free the space now and"
                " restore any of it exactly when you need it again.",
                "",
                "Chunks below are listed most recently retrieved first — each line is a vectr_fetch id:",
            ]
        elif escalated:
            lines = [
                "─── ACTION REQUIRED ───",
                "Call vectr_remember(content, tags=[...]) NOW before continuing.",
                "Save: key type names, module paths, entry points, non-obvious patterns.",
                "Your synthesized understanding does not persist automatically — the output",
                "file captures findings, not the navigational path to reach them.",
                "",
                f"Vectr has {len(self._chunks)} retrieved chunks (~{total_tokens} tokens)"
                " fully indexed. Drop these chunks from context — each is re-fetchable"
                " verbatim via vectr_fetch(ids=[...]) in <50ms."
                " Your synthesized analysis (saved via vectr_remember) is retrievable via vectr_recall.",
                "",
                "Chunks below are listed most recently retrieved first — each line is a vectr_fetch id:",
            ]
        else:
            lines = [
                "─── context recap ───",
                "New content has been retrieved since your last vectr_remember. If you found",
                "anything worth keeping — key type names, module paths, entry points, non-obvious",
                "patterns — call vectr_remember(content, tags=[...]) now; otherwise continue.",
                "",
                f"Vectr has {len(self._chunks)} retrieved chunks (~{total_tokens} tokens)"
                " fully indexed. Drop these chunks from context — each is re-fetchable"
                " verbatim via vectr_fetch(ids=[...]) in <50ms."
                " Your synthesized analysis (saved via vectr_remember) is retrievable via vectr_recall.",
                "",
                "Chunks below are listed most recently retrieved first — each line is a vectr_fetch id:",
            ]
        # UPG-RELATIVE-PATH-RENDER: print the absolute workspace root ONCE; every
        # chunk id below is rendered relative to it.
        if workspace_root:
            lines.append(f"workspace: {workspace_root}")

        # UPG-EVICT-HINT-SINGLE-SERIALIZE: render each shown chunk EXACTLY ONCE in
        # id-ready, workspace-relative form — `relpath:X-Y (symbol)` is
        # simultaneously the human-readable location AND a copy-pasteable
        # vectr_fetch id — instead of serializing every path twice (a grouped
        # file list PLUS a separate "Re-fetch keys" block, ~70% redundant). A
        # single usage template follows, referencing the ids above rather than
        # re-listing them.
        # UPG-EVICT-REFETCH-KEYS-STALE ordering is preserved: `shown` is already
        # most-recently-retrieved-file first, so the chunk lines inherit it.
        for _fpath, chunks in shown:
            for c in chunks:
                rel_id = f"{workspace_relpath(c.file_path, workspace_root)}:{c.lines}"
                sym = f"  ({c.symbol_name})" if c.symbol_name else ""
                lines.append(f"  {rel_id}{sym}")
        if overflow:
            lines.append(f"  ... and {overflow} more file(s). All retrievable via vectr_search('<description>').")

        # UPG-EVICT-SESSION-SCOPE: fetch is advertised only when at least one
        # recorded chunk carries a real stored id (a locate snippet's symbol
        # range is not a guaranteed chunk boundary, so its `relpath:X-Y` line is
        # a location, not a re-fetch key). A single template referencing the ids
        # above — the ids themselves are not serialized a second time.
        if any(c.chunk_id for c in self._chunks):
            lines += [
                "",
                "Re-fetch any of the ids above verbatim (<50ms): vectr_fetch(ids=[...]).",
            ]

        if escalated and not on_demand:
            lines += [
                "",
                "Call vectr_remember now, then continue your task.",
                "needs_remember: true",
            ]
        return "\n".join(lines)

    def clear_session(self) -> None:
        """Reset for a new session."""
        self._chunks.clear()
        self._tool_call_count = 0
        self._retrieval_call_count = 0
        self._session_started_at = time.time()
        self._last_emit = None
        self._chunks_since_remember = 0
        self._tokens_at_last_remember = 0
        self._soft_fire_pending = False

    def as_chunk_dicts(self) -> list[dict]:
        """Serialisable form for snapshot storage."""
        return [
            {
                "file": c.file_path,
                "lines": c.lines,
                "symbol": c.symbol_name,
                "content": c.content,
                "chunk_id": c.chunk_id,
            }
            for c in self._chunks
        ]
