"""Gating policy for proactive context (UPG-PRO-5).

Turns a set of scored `Candidate`s into 0..K emitted items packed into one
deterministic block. Every rule is numeric and deterministic — a similarity
floor, a per-event budget, a per-session dedup/cooldown ledger, and a stable
sort. There is no branch that reads conversation content to decide *what kind*
of help to give; the gate only orders, thresholds, and concatenates.
"""
from __future__ import annotations

import threading
from collections import OrderedDict, deque

from agent.proactive.types import Candidate, InjectionResult

# One-line header so the model knows the provenance and trust level of what
# follows. Fixed overhead, not counted against the item budget.
_HEADER = "vectr proactive context (deterministic, local; verify before relying):"


class SessionLedger:
    """Bounded ring of recently-emitted anchor ids for one session.

    Suppresses re-injecting the same item within a cooldown window (last N
    emitted anchor ids). Insertion-ordered; the oldest id is evicted once the
    capacity is exceeded.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        self._ring: deque[str] = deque()
        self._seen: set[str] = set()

    def seen(self, anchor_id: str) -> bool:
        return anchor_id in self._seen

    def add(self, anchor_id: str) -> None:
        if anchor_id in self._seen:
            return
        self._ring.append(anchor_id)
        self._seen.add(anchor_id)
        while len(self._ring) > self._capacity:
            old = self._ring.popleft()
            self._seen.discard(old)


class LedgerStore:
    """Thread-safe, LRU-bounded map of session_id -> SessionLedger.

    Proxied requests can arrive concurrently, so ledger access is locked. The
    number of tracked sessions is bounded so a long-lived proxy cannot grow
    unbounded state; the least-recently-used session's ledger is dropped first.
    """

    def __init__(self, cooldown_items: int, max_sessions: int = 512) -> None:
        self._cooldown_items = cooldown_items
        self._max_sessions = max(1, max_sessions)
        self._ledgers: "OrderedDict[str, SessionLedger]" = OrderedDict()
        self._lock = threading.Lock()

    def _ledger_for(self, session_id: str) -> SessionLedger:
        led = self._ledgers.get(session_id)
        if led is None:
            led = SessionLedger(self._cooldown_items)
            self._ledgers[session_id] = led
            while len(self._ledgers) > self._max_sessions:
                self._ledgers.popitem(last=False)
        else:
            self._ledgers.move_to_end(session_id)
        return led

    def seen(self, session_id: str, anchor_id: str) -> bool:
        with self._lock:
            return self._ledger_for(session_id).seen(anchor_id)

    def record(self, session_id: str, anchor_ids) -> None:
        with self._lock:
            led = self._ledger_for(session_id)
            for aid in anchor_ids:
                led.add(aid)


class ProactiveGate:
    """Applies floor -> dedup/cooldown -> budget -> deterministic pack."""

    def __init__(
        self,
        *,
        min_similarity: float,
        max_items_per_event: int,
        max_chars_per_event: int,
        cooldown_items: int,
        ledger_store: LedgerStore | None = None,
    ) -> None:
        self._min_similarity = min_similarity
        self._max_items = max(0, max_items_per_event)
        self._max_chars = max(0, max_chars_per_event)
        self._ledger = ledger_store or LedgerStore(cooldown_items)

    def select(
        self,
        candidates: list[Candidate],
        *,
        session_id: str = "",
        structural_only: bool = False,
    ) -> InjectionResult:
        """Deterministically pick and pack the items to inject.

        `structural_only` (a static per-channel policy, never a content read)
        drops every semantic candidate — used by the high-frequency channels
        where only exact matches are cheap enough to be worth the budget.
        """
        if self._max_items == 0 or self._max_chars == 0:
            return InjectionResult.empty()

        # 1. Dedup across matchers by anchor_id: keep the strongest occurrence
        #    (highest score, then best provenance rank). Deterministic.
        best: dict[str, Candidate] = {}
        for c in candidates:
            cur = best.get(c.anchor_id)
            if cur is None or (c.score, -c.provenance_rank) > (cur.score, -cur.provenance_rank):
                best[c.anchor_id] = c

        # 2. Floor + per-channel policy: drop below-floor semantic candidates;
        #    structural exact matches bypass the floor entirely.
        eligible: list[Candidate] = []
        for c in best.values():
            if c.is_structural:
                eligible.append(c)
                continue
            if structural_only:
                continue
            if c.score >= self._min_similarity:
                eligible.append(c)

        # 3. Cooldown: drop anything already emitted for this session recently.
        if session_id:
            eligible = [c for c in eligible if not self._ledger.seen(session_id, c.anchor_id)]

        if not eligible:
            return InjectionResult.empty()

        # 4. Deterministic order: score desc, provenance rank asc, anchor_id asc.
        eligible.sort(key=lambda c: (-c.score, c.provenance_rank, c.anchor_id))

        # 5. Budget: at most K items and T chars. Each candidate `line` is capped
        #    to T by the matcher, so a single item always fits; stop at the first
        #    item that would overflow the running character total.
        selected: list[Candidate] = []
        used_chars = 0
        for c in eligible:
            if len(selected) >= self._max_items:
                break
            line = c.line
            add_chars = len(line) + (1 if selected else 0)  # newline separator
            if used_chars + add_chars > self._max_chars:
                break
            selected.append(c)
            used_chars += add_chars

        if not selected:
            return InjectionResult.empty()

        if session_id:
            self._ledger.record(session_id, [c.anchor_id for c in selected])

        body = "\n".join(c.line for c in selected)
        context = f"{_HEADER}\n{body}"
        return InjectionResult(
            context=context,
            item_count=len(selected),
            anchor_ids=tuple(c.anchor_id for c in selected),
            scores=tuple(round(c.score, 4) for c in selected),
        )
