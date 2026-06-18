"""Unit tests for eval_v2 stream parsing + arm wiring (no agent sessions)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import eval_v2
from eval_v2 import (
    ARMS,
    COMPACT_TURN,
    PhaseUsage,
    _VECTR_MEMORY_TOOLS,
    _VECTR_SEARCH_TOOLS,
    build_turns,
    compaction_summary_chars,
    events_to_timeline,
    setup_arm,
    split_phases_on_compaction,
    usage_from_events,
)


@dataclass
class _FakeTask:
    id: str = "t1"
    title: str = "T"
    phase1_description: str = "EXPLORE-BODY"
    phase2_description: str = "IMPL-BODY"


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


# ---- prompt + orchestration wiring ----

def test_build_turns_shape_and_compact_position():
    for arm_id in ARMS:
        turns = build_turns(ARMS[arm_id], _FakeTask(), "/repo", "The Django source tree")
        assert len(turns) == 3
        assert turns[1] == COMPACT_TURN
        # task bodies always present; explore says don't implement yet
        assert "EXPLORE-BODY" in turns[0] and "/repo" in turns[0]
        assert "Do NOT implement" in turns[0]
        assert "IMPL-BODY" in turns[2]


def test_per_arm_preambles():
    t = _FakeTask()
    # A1 = bare: no persistence/recall scaffolding beyond the task body
    a1 = build_turns(ARMS["A1"], t, "/r", "d")
    assert "NOTES.md" not in a1[0] and "vectr_recall" not in a1[2]
    # A2 = NOTES.md on both ends
    a2 = build_turns(ARMS["A2"], t, "/r", "d")
    assert "NOTES.md" in a2[0] and "NOTES.md" in a2[2]
    # B = re-query via search, no recall
    b = build_turns(ARMS["B"], t, "/r", "d")
    assert "vectr_search" in b[2]
    # C = product carries it; no recall nudge in the prompt
    c = build_turns(ARMS["C"], t, "/r", "d")
    assert "vectr_recall" not in c[2] and "NOTES.md" not in c[2]
    # D = explicit recall nudge (model-choice)
    d = build_turns(ARMS["D"], t, "/r", "d")
    assert "vectr_recall" in d[2]


def test_setup_arm_notes_md(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(eval_v2, "_run_vectr", lambda args: calls.append(args))
    monkeypatch.setattr(eval_v2, "_clear_vectr_memory", lambda port=8765: None)

    # A2 scaffolds an empty NOTES.md
    setup_arm(ARMS["A2"], str(tmp_path))
    notes = tmp_path / "NOTES.md"
    assert notes.exists() and notes.read_text() == ""

    # A non-NOTES arm removes a stale NOTES.md
    setup_arm(ARMS["A1"], str(tmp_path))
    assert not notes.exists()


def test_setup_arm_vectr_init_variants(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(eval_v2, "_run_vectr", lambda args: calls.append(args))
    monkeypatch.setattr(eval_v2, "_clear_vectr_memory", lambda port=8765: None)

    # C installs hooks
    calls.clear()
    setup_arm(ARMS["C"], str(tmp_path))
    init_calls = [c for c in calls if c[0] == "init" and "--reset-config" not in c]
    assert any("--hooks" in c for c in init_calls)

    # D inits vectr but WITHOUT hooks
    calls.clear()
    setup_arm(ARMS["D"], str(tmp_path))
    init_calls = [c for c in calls if c[0] == "init" and "--reset-config" not in c]
    assert init_calls and not any("--hooks" in c for c in init_calls)

    # A1 only resets config, never inits vectr
    calls.clear()
    setup_arm(ARMS["A1"], str(tmp_path))
    assert all("--reset-config" in c for c in calls if c[0] == "init")
