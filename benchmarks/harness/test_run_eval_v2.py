"""Unit tests for the eval v2 runner summary logic (no agent sessions)."""
from __future__ import annotations

from unittest.mock import patch

import eval_v2
from eval_v2 import CompactSessionResult, PhaseUsage
from scoring import ExecScore
from run_eval_v2 import _summarize, _resolve_model_id


def _result(**kw):
    res = CompactSessionResult(arm_id="C", task_id="custom_field")
    res.research = PhaseUsage(input_tokens=1000, output_tokens=200, turns=4)
    res.impl = PhaseUsage(input_tokens=300, output_tokens=80, turns=2)
    res.compacted = True
    res.research_injection = {"injections": 2, "injected_chars": 120,
                              "injected_text": "MoneyField deconstruct"}
    res.impl_injection = {"injections": 1, "injected_chars": 60,
                          "injected_text": "IRRELEVANT-SIGTASK noise"}
    res.exec_score = ExecScore(task_id="custom_field", ran=True, passed=5, failed=0)
    for k, v in kw.items():
        setattr(res, k, v)
    return res


def test_summarize_core_metrics():
    s = _summarize(_result(), guardrail=False)
    assert s["arm"] == "C"
    assert s["total_tokens"] == 1200 + 380
    assert s["impl_injections"] == 1
    assert s["injected_chars"] == 180
    assert s["exec_success"] is True and s["exec_passed"] == 5
    assert "injection_precision" not in s
    # passing runs stay lean — no exec_log
    assert "exec_log" not in s


def test_summarize_failed_exec_archives_log():
    es = ExecScore(task_id="custom_field", ran=True, passed=4, failed=1)
    es.log_tail = "FAILED test_negative_value_rejected - DID NOT RAISE"
    s = _summarize(_result(exec_score=es), guardrail=False)
    assert s["exec_success"] is False
    assert "test_negative_value_rejected" in s["exec_log"]


def test_summarize_guardrail_precision():
    s = _summarize(_result(), guardrail=True)
    p = s["injection_precision"]
    # one relevant marker block + one irrelevant marker present
    assert p["relevant"] >= 1 and p["irrelevant"] == 1
    assert 0.0 <= p["precision"] <= 1.0


def test_summarize_failed_exec_and_no_compaction():
    s = _summarize(_result(compacted=False,
                           exec_score=ExecScore(task_id="t", ran=False)),
                   guardrail=False)
    assert s["compacted"] is False
    assert s["exec_ran"] is False and s["exec_success"] is False


# ---------------------------------------------------------------------------
# BV-MODEL-PIN — explicit model ID resolution
# ---------------------------------------------------------------------------

def test_resolve_model_id_opus_alias_pinned_to_4_8():
    # This is also the CLI's --model default value, so "default" and
    # "--model opus" resolve identically.
    assert _resolve_model_id("opus") == "claude-opus-4-8"


def test_resolve_model_id_sonnet_alias_pinned_to_4_6():
    assert _resolve_model_id("sonnet") == "claude-sonnet-4-6"


def test_resolve_model_id_full_claude_id_used_verbatim():
    assert _resolve_model_id("claude-opus-4-1") == "claude-opus-4-1"
    assert _resolve_model_id("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_resolve_model_id_unrecognized_alias_passes_through():
    # Not pinned here (e.g. "haiku") — Claude Code resolves it itself.
    assert _resolve_model_id("haiku") == "haiku"


def test_resolved_model_id_lands_in_claude_cli_invocation():
    """The full resolved ID (not the bare alias) must reach the spawned
    `claude -p --model ...` argv — no live claude invocation (Popen mocked
    to fail fast, but its call args are still captured)."""
    with patch("eval_v2.subprocess.Popen", side_effect=FileNotFoundError) as mock_popen:
        eval_v2.run_session(
            turns=["hello"], working_dir=".", allowed_tools=[],
            model=_resolve_model_id("opus"),
        )
    cmd = mock_popen.call_args[0][0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
