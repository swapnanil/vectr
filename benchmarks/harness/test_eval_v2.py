"""Unit tests for eval_v2 stream parsing + arm wiring (no agent sessions)."""
from __future__ import annotations

from eval_v2 import (
    ARMS,
    PhaseUsage,
    _VECTR_MEMORY_TOOLS,
    _VECTR_SEARCH_TOOLS,
    compaction_summary_chars,
    events_to_timeline,
    split_phases_on_compaction,
    usage_from_events,
)


def _init(session="s1"):
    return {"type": "system", "subtype": "init", "session_id": session}


def _assistant_text(text):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _assistant_tool(tool_id, name, finput):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": finput}]}}


def _tool_result(tool_id, text):
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def _result(answer, *, input_t=100, cc=50, cr=200, out=30, turns=3):
    return {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": turns, "result": answer, "total_cost_usd": 0.0123,
            "usage": {"iterations": [
                {"input_tokens": input_t, "cache_creation_input_tokens": cc,
                 "cache_read_input_tokens": cr, "output_tokens": out},
            ]}}


# ---- the real 2.1.149 shape: compaction = a 2nd system/init, no result of its own ----

def _compact_session_events():
    return [
        _init(),                                   # phase 1 start
        _assistant_tool("t1", "vectr_search", {"query": "x"}),
        _tool_result("t1", "found it"),
        _assistant_text("RESEARCH DONE"),
        _result("RESEARCH DONE", turns=2),          # phase-1 result
        _init(),                                   # <-- /compact boundary (2nd init)
        _assistant_text("implementing"),
        _result("IMPL DONE", input_t=10, cc=5, cr=20, out=8, turns=1),  # phase-2 result
    ]


def test_split_on_second_init():
    events = _compact_session_events()
    research, impl, compacted = split_phases_on_compaction(events)
    assert compacted is True
    # research is everything before the 2nd init
    assert research[0]["subtype"] == "init"
    assert all(not (e.get("subtype") == "init") for e in research[1:])
    # impl slice starts at the boundary init
    assert impl[0]["subtype"] == "init"
    # each slice has exactly one result
    assert sum(e.get("type") == "result" for e in research) == 1
    assert sum(e.get("type") == "result" for e in impl) == 1


def test_split_tolerates_compact_boundary_subtype():
    events = [
        _init(),
        _result("a"),
        {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"summary": "S" * 42}},
        _result("b"),
    ]
    research, impl, compacted = split_phases_on_compaction(events)
    assert compacted is True
    assert impl[0]["subtype"] == "compact_boundary"
    assert compaction_summary_chars(impl) == 42


def test_no_compaction_means_all_research():
    events = [_init(), _assistant_text("hi"), _result("hi")]
    research, impl, compacted = split_phases_on_compaction(events)
    assert compacted is False
    assert impl == []
    assert len(research) == 3


def test_usage_split_per_phase():
    events = _compact_session_events()
    research, impl, _ = split_phases_on_compaction(events)
    ru = usage_from_events(research)
    iu = usage_from_events(impl)
    assert ru.input_tokens == 100 + 50 + 200
    assert ru.output_tokens == 30
    assert ru.turns == 2
    assert iu.input_tokens == 10 + 5 + 20
    assert iu.output_tokens == 8
    assert iu.turns == 1
    # impl re-discovery cost is far lower than research here
    assert iu.total_tokens < ru.total_tokens


def test_usage_empty_when_no_result():
    assert usage_from_events([_init(), _assistant_text("x")]) == PhaseUsage()


def test_timeline_reconstructs_tool_calls():
    events = _compact_session_events()
    research, _, _ = split_phases_on_compaction(events)
    # stamp synthetic times so durations are computable
    for i, e in enumerate(research):
        e["_t"] = float(i)
    tl = events_to_timeline(research, session_start=0.0)
    assert len(tl) == 1
    assert tl[0].tool_name == "vectr_search"
    assert tl[0].result_chars == len("found it")
    assert tl[0].duration_s >= 0


# ---- arm wiring ----

def test_arm_tool_sets():
    a1 = ARMS["A1"]
    assert not a1.uses_vectr
    assert "Read" in a1.allowed_tools()
    assert not any("vectr" in t for t in a1.allowed_tools())

    b = ARMS["B"]
    assert set(_VECTR_SEARCH_TOOLS).issubset(b.allowed_tools())
    assert not any(t in b.allowed_tools() for t in _VECTR_MEMORY_TOOLS)

    for arm_id in ("C", "D"):
        arm = ARMS[arm_id]
        assert set(_VECTR_SEARCH_TOOLS).issubset(arm.allowed_tools())
        assert set(_VECTR_MEMORY_TOOLS).issubset(arm.allowed_tools())

    assert ARMS["C"].hooks is True
    assert ARMS["D"].hooks is False
    assert ARMS["A2"].notes_md is True


def test_all_five_arms_present():
    assert set(ARMS) == {"A1", "A2", "B", "C", "D"}
