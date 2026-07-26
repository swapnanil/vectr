"""Gating policy tests (UPG-PRO-5): floor, budget, dedup/cooldown, determinism."""
from __future__ import annotations

from agent.proactive.gate import LedgerStore, ProactiveGate, SessionLedger
from agent.proactive.types import Candidate


def _cand(kind, line, score, anchor, structural):
    return Candidate(kind=kind, line=line, score=score, anchor_id=anchor, is_structural=structural)


def _gate(**kw):
    defaults = dict(
        min_similarity=0.35, max_items_per_event=3, max_chars_per_event=800, cooldown_items=30
    )
    defaults.update(kw)
    return ProactiveGate(**defaults)


def test_budget_takes_top_k_deterministically():
    cands = [
        _cand("code_semantic", f"hit {i}", 0.9 - i * 0.01, f"chunk:{i}", False)
        for i in range(10)
    ]
    gate = _gate(max_items_per_event=3)
    out = gate.select(cands, session_id="s1")
    assert out.item_count == 3
    # Top-3 by score desc, deterministic.
    assert out.anchor_ids == ("chunk:0", "chunk:1", "chunk:2")


def test_identical_input_identical_output():
    cands = [
        _cand("note_semantic", "note a", 0.8, "note:1", False),
        _cand("code_semantic", "hit b", 0.7, "chunk:x", False),
    ]
    a = _gate().select(list(cands), session_id="")
    b = _gate().select(list(cands), session_id="")
    assert a == b


def test_floor_applies_to_structural_candidates_too():
    # UPG-PROXY-LOOPBACK-BYPASS / similarity-floor bypass: structural exact
    # matches no longer skip the floor. A structural candidate BELOW the
    # floor is dropped exactly like a semantic one; a structural candidate
    # AT the floor (1.0, the score every exact match carries) still passes —
    # this is behavior-preserving for genuine structural matches.
    cands = [
        _cand("note_semantic", "weak", 0.20, "note:1", False),          # below 0.35 floor
        _cand("note_structural", "below-floor", 0.20, "note:2", True),  # structural below floor: dropped
        _cand("note_structural", "at-floor", 1.0, "note:3", True),      # structural at 1.0: clears floor
    ]
    out = _gate().select(cands, session_id="")
    assert out.item_count == 1
    assert out.anchor_ids == ("note:3",)


def test_floor_above_one_admits_nothing_even_structural():
    # Fail-closed: a floor configured above the maximum possible score
    # admits nothing, including an exact structural match.
    cands = [_cand("note_structural", "struct", 1.0, "note:1", True)]
    out = _gate(min_similarity=1.01).select(cands, session_id="")
    assert out.is_empty()


def test_structural_outranks_semantic_on_tie():
    cands = [
        _cand("note_semantic", "sem", 1.0, "note:9", False),
        _cand("note_structural", "struct", 1.0, "note:1", True),
    ]
    out = _gate(max_items_per_event=2).select(cands, session_id="")
    # Same score => provenance rank breaks tie: structural (0) before note (1).
    assert out.anchor_ids[0] == "note:1"


def test_dedup_cooldown_suppresses_repeat():
    gate = _gate()
    c = [_cand("note_structural", "x", 1.0, "note:5", True)]
    first = gate.select(list(c), session_id="sess")
    second = gate.select(list(c), session_id="sess")
    assert first.item_count == 1
    assert second.item_count == 0  # already emitted within cooldown


def test_dedup_across_matchers_keeps_highest_score():
    cands = [
        _cand("note_semantic", "low", 0.5, "note:1", False),
        _cand("note_structural", "high", 1.0, "note:1", True),  # same anchor
    ]
    out = _gate().select(cands, session_id="")
    assert out.item_count == 1
    assert out.scores == (1.0,)


def test_structural_only_drops_semantic():
    cands = [
        _cand("note_semantic", "sem", 0.99, "note:1", False),
        _cand("note_structural", "struct", 1.0, "note:2", True),
    ]
    out = _gate().select(cands, session_id="", structural_only=True)
    assert out.anchor_ids == ("note:2",)


def test_empty_selection_returns_empty_string():
    out = _gate().select([], session_id="")
    assert out.is_empty()
    assert out.context == ""


def test_char_budget_stops_overflow():
    long_line = "x" * 100
    cands = [
        _cand("note_semantic", long_line, 0.9, "note:1", False),
        _cand("note_semantic", long_line, 0.8, "note:2", False),
        _cand("note_semantic", long_line, 0.7, "note:3", False),
    ]
    # header + 100 + \n + 100 = fits 2 but not 3 within 260.
    out = _gate(max_chars_per_event=260, max_items_per_event=5).select(cands, session_id="")
    assert out.item_count == 2


def test_session_ledger_bounded_ring():
    led = SessionLedger(capacity=2)
    led.add("a")
    led.add("b")
    led.add("c")  # evicts "a"
    assert not led.seen("a")
    assert led.seen("b")
    assert led.seen("c")


def test_ledger_store_lru_bounded_sessions():
    store = LedgerStore(cooldown_items=5, max_sessions=2)
    store.record("s1", ["a"])
    store.record("s2", ["b"])
    store.record("s3", ["c"])  # evicts s1 (LRU)
    assert not store.seen("s1", "a")
    assert store.seen("s3", "c")


# -- cross-channel turn-ledger dedup (UPG-PROXY-CROSS-CHANNEL-DEDUP) --------
# Uses the REAL `TurnInjectionLedger` (not a stand-in) — the same object the
# hook/trigger-engine surfaces share via `VectrService._turn_ledger_for`.

def test_note_already_claimed_this_turn_is_dropped():
    from agent.trigger_engine import TurnInjectionLedger

    turn_ledger = TurnInjectionLedger()
    turn_ledger.claim(1)  # another surface already injected note #1 this turn
    cands = [
        _cand("note_structural", "struct", 1.0, "note:1", True),
        _cand("note_semantic", "sem", 0.9, "note:2", False),
    ]
    out = _gate().select(cands, session_id="", turn_ledger=turn_ledger)
    assert out.anchor_ids == ("note:2",)


def test_turn_ledger_absent_is_a_no_op():
    cands = [_cand("note_structural", "struct", 1.0, "note:1", True)]
    out = _gate().select(cands, session_id="")  # turn_ledger omitted entirely
    assert out.item_count == 1


def test_selected_notes_are_claimed_in_turn_ledger():
    from agent.trigger_engine import TurnInjectionLedger

    turn_ledger = TurnInjectionLedger()
    cands = [_cand("note_structural", "struct", 1.0, "note:1", True)]
    out = _gate().select(cands, session_id="", turn_ledger=turn_ledger)
    assert out.item_count == 1
    assert turn_ledger.eligible(1) is False  # claimed on the way out


def test_budget_evicted_note_is_not_claimed_in_turn_ledger():
    from agent.trigger_engine import TurnInjectionLedger

    turn_ledger = TurnInjectionLedger()
    cands = [
        _cand("note_structural", "kept", 1.0, "note:1", True),
        _cand("note_structural", "evicted", 0.9, "note:2", True),
    ]
    gate = _gate(max_items_per_event=1)  # only room for one item
    out = gate.select(cands, session_id="", turn_ledger=turn_ledger)
    assert out.anchor_ids == ("note:1",)
    assert turn_ledger.eligible(1) is False  # actually delivered: claimed
    assert turn_ledger.eligible(2) is True   # matched but budget-evicted: NOT claimed


def test_chunk_candidates_unaffected_by_turn_ledger():
    from agent.trigger_engine import TurnInjectionLedger

    turn_ledger = TurnInjectionLedger()
    cands = [_cand("code_semantic", "hit", 0.9, "chunk:foo.py:1-9", False)]
    out = _gate().select(cands, session_id="", turn_ledger=turn_ledger)
    assert out.item_count == 1  # non-note anchors have no note_id to check
