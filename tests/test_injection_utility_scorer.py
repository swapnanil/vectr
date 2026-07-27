"""Discrimination tests for the injection-utility mechanical scorer.

The point of these tests is anti-vacuity. A scorer that returns the SAME verdict
for an agent that followed the planted note and one that ignored it would make
every A/B run a guaranteed tie, and the eval would look well-behaved while
measuring nothing. So for every scenario this file builds two hand-written
fixtures -- one "naive" workspace/transcript and one "note-following" -- and
asserts the scorer separates them on the scenario's declared primary check.

The fixtures encode the same expectations the scenario definitions state in
prose in their `naive_expectation` / `note_following_expectation` fields.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "injection_utility"
sys.path.insert(0, str(BENCH_DIR))

import scenarios as scen  # noqa: E402
import scorer  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bash_transcript(commands: list[str], *, num_turns: int = 4) -> list[dict]:
    """A minimal stream-json transcript carrying the given Bash tool calls."""
    events: list[dict] = [{"type": "system", "subtype": "init"}]
    for cmd in commands:
        events.append({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": cmd}},
            ]},
        })
    events.append({
        "type": "result",
        "subtype": "success",
        "num_turns": num_turns,
        "duration_ms": 1234,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    return events


def _setup(tmp_path: Path, slug: str):
    scenario = scen.get(slug)
    workspace = tmp_path / "workspace"
    verify_dir = tmp_path / "verify"
    baselines = scen.materialize(scenario, workspace)
    scen.materialize_verifiers(scenario, verify_dir)
    return scenario, workspace, verify_dir, baselines


def _score(scenario, workspace, baselines, verify_dir, transcript):
    return scorer.score_run(
        scenario,
        workspace=workspace,
        baselines=baselines,
        transcript=transcript,
        verify_dir=verify_dir,
    )


# ---------------------------------------------------------------------------
# structural sanity of the scenario definitions themselves
# ---------------------------------------------------------------------------


def test_every_scenario_declares_a_real_primary_check():
    for slug, scenario in scen.SCENARIOS.items():
        assert scenario.primary_check in scenario.check_names(), (
            f"{slug} names a primary check that is not one of its checks"
        )


def test_every_scenario_states_both_expectations():
    for slug, scenario in scen.SCENARIOS.items():
        assert scenario.naive_expectation.strip(), f"{slug} has no naive expectation"
        assert scenario.note_following_expectation.strip(), f"{slug} has no note expectation"


def test_verify_scripts_are_not_shipped_inside_the_workspace(tmp_path):
    """A verifier inside the workspace would leak the planted fact to arm B."""
    for slug in scen.SCENARIOS:
        scenario, workspace, _, _ = _setup(tmp_path / slug, slug)
        for name in scenario.verify_scripts:
            assert not (workspace / name).exists()
            assert not any(p.name == name for p in workspace.rglob("*"))


def test_planted_notes_never_contain_the_literal_task_answer():
    """The note may carry a fact; it must not be a diff the agent can paste."""
    for slug, scenario in scen.SCENARIOS.items():
        content = scenario.note.content
        assert "def " not in content, f"{slug} note contains a function definition"
        assert "```" not in content, f"{slug} note contains a code block"


# ---------------------------------------------------------------------------
# scenario: superseded_api
# ---------------------------------------------------------------------------

_S1_NAIVE = '''"""Order report rendering."""
from formatting import format_currency_legacy


def render_row(order):
    return f"{order['id']}\\t{order['item']}\\t{format_currency_legacy(order['amount'])}"


def render_summary(orders):
    lines = [render_row(o) for o in orders]
    total = sum(o["amount"] for o in orders)
    lines.append(f"TOTAL\\t{format_currency_legacy(total)}")
    return "\\n".join(lines)
'''

_S1_NOTE_FOLLOWING = '''"""Order report rendering."""
from formatting import format_currency, format_currency_legacy


def render_row(order):
    return f"{order['id']}\\t{order['item']}\\t{format_currency_legacy(order['amount'])}"


def render_summary(orders):
    lines = [render_row(o) for o in orders]
    total = sum(o["amount"] for o in orders)
    lines.append(f"TOTAL\\t{format_currency(total)}")
    return "\\n".join(lines)
'''


def test_superseded_api_discriminates(tmp_path):
    transcript = _bash_transcript(["python3 -c 'import report'"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "superseded_api")
    (ws / "src" / "report.py").write_text(_S1_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "superseded_api")
    (ws / "src" / "report.py").write_text(_S1_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # The naive fix ADDS a legacy call site; the note-following one does not.
    assert naive_checks["no_new_legacy_call_sites"] is False
    assert good_checks["no_new_legacy_call_sites"] is True
    # Both actually completed the task -- the arms differ on HOW, not whether.
    assert naive_checks["total_row_rendered"] is True
    assert good_checks["total_row_rendered"] is True


def test_superseded_api_unmodified_workspace_fails_primary(tmp_path):
    scenario, ws, vd, base = _setup(tmp_path, "superseded_api")
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    assert result["utility_hit"] is False
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["total_row_rendered"] is False  # no TOTAL row was ever added


# ---------------------------------------------------------------------------
# scenario: generated_config
# ---------------------------------------------------------------------------


def _s2_edit_conf_only(ws: Path) -> None:
    conf = ws / "config" / "limits.conf"
    conf.write_text(conf.read_text().replace("max_upload_mb = 10", "max_upload_mb = 50"))


def _s2_edit_generator(ws: Path) -> None:
    gen = ws / "tools" / "gen_limits.py"
    gen.write_text(gen.read_text().replace('"max_upload_mb": 10', '"max_upload_mb": 50'))
    conf = ws / "config" / "limits.conf"
    conf.write_text(conf.read_text().replace("max_upload_mb = 10", "max_upload_mb = 50"))


def test_generated_config_discriminates(tmp_path):
    transcript = _bash_transcript(["cat config/limits.conf"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "generated_config")
    _s2_edit_conf_only(ws)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "generated_config")
    _s2_edit_generator(ws)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False, "a hand-edited generated file must not score a hit"
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    assert naive_checks["generator_table_updated"] is False
    assert good_checks["generator_table_updated"] is True
    # Both LOOK correct before a build runs -- that is exactly the trap the note
    # describes, and why the primary check regenerates before judging.
    assert naive_checks["conf_reads_50_before_regen"] is True
    assert good_checks["conf_reads_50_before_regen"] is True


def test_generated_config_read_checks_see_pre_regeneration_state(tmp_path):
    """Ordering guard: the mutating verifier must not clobber file checks.

    `survives_regeneration` rewrites limits.conf. If it ran before the file
    checks, `conf_reads_50_before_regen` would report the regenerated bytes and
    silently flip to False for the naive arm -- masking the trap.
    """
    scenario, ws, vd, base = _setup(tmp_path, "generated_config")
    _s2_edit_conf_only(ws)
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["conf_reads_50_before_regen"] is True
    assert checks["survives_regeneration"] is False
    # And the verifier really did regenerate, proving the ordering was load-bearing.
    assert "max_upload_mb = 10" in (ws / "config" / "limits.conf").read_text()


# ---------------------------------------------------------------------------
# scenario: flaky_test
# ---------------------------------------------------------------------------


def _s3_fix(ws: Path) -> None:
    (ws / "src" / "calc.py").write_text(
        '"""Money arithmetic."""\n\n\ndef add_tax(amount, rate):\n'
        "    return round(amount * (1 + rate), 2)\n"
    )


def test_flaky_test_discriminates_on_command_choice(tmp_path):
    scenario, ws, vd, base = _setup(tmp_path / "naive", "flaky_test")
    _s3_fix(ws)
    naive = _score(scenario, ws, base, vd,
                   _bash_transcript(["./run_tests.sh", "./run_tests.sh"]))

    scenario, ws, vd, base = _setup(tmp_path / "good", "flaky_test")
    _s3_fix(ws)
    good = _score(scenario, ws, base, vd, _bash_transcript(["./run_tests.sh --core"]))

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    # The code fix is identical in both arms: the arms differ ONLY on the
    # command chosen, which is precisely the behavior the note targets.
    assert {c["name"]: c["passed"] for c in naive["checks"]}["fix_applied"] is True
    assert {c["name"]: c["passed"] for c in good["checks"]}["fix_applied"] is True

    naive_metrics = {m["name"]: m["value"] for m in naive["metrics"]}
    good_metrics = {m["name"]: m["value"] for m in good["metrics"]}
    assert naive_metrics["flaky_runner_invocations"] == 2
    assert naive_metrics["core_runner_invocations"] == 0
    assert good_metrics["flaky_runner_invocations"] == 0
    assert good_metrics["core_runner_invocations"] == 1


def test_flaky_test_fix_check_is_independent_of_command_choice(tmp_path):
    """An agent can pick the right command and still not fix the code."""
    scenario, ws, vd, base = _setup(tmp_path, "flaky_test")
    result = _score(scenario, ws, base, vd, _bash_transcript(["./run_tests.sh --core"]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["used_core_test_command"] is True
    assert checks["fix_applied"] is False


# ---------------------------------------------------------------------------
# transcript parsing
# ---------------------------------------------------------------------------


def test_bash_commands_ignores_non_bash_tools():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/y.py"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
            {"type": "text", "text": "running ls -la now"},
        ]}},
    ]
    assert scorer.bash_commands(events) == ["ls -la"]


def test_command_checks_ignore_agent_prose(tmp_path):
    """Saying the right command is not running it.

    Guards the scorer against grading intent: only tool_use inputs count.
    """
    scenario, ws, vd, base = _setup(tmp_path, "flaky_test")
    _s3_fix(ws)
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I should use ./run_tests.sh --core here."},
        ]}},
        {"type": "result", "subtype": "success", "num_turns": 1, "usage": {}},
    ]
    result = _score(scenario, ws, base, vd, events)
    assert result["utility_hit"] is False


def test_load_transcript_skips_unparseable_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"system"}\nnot json at all\n{"type":"result","num_turns":2}\n')
    events = scorer.load_transcript(p)
    assert [e.get("type") for e in events] == ["system", "result"]


def test_cost_metrics_read_from_final_result_event():
    events = _bash_transcript(["ls"], num_turns=7)
    cost = scorer.cost_metrics(events)
    assert cost["num_turns"] == 7
    assert cost["tool_calls"] == 1
    assert cost["bash_calls"] == 1
    assert cost["input_tokens"] == 100


# ---------------------------------------------------------------------------
# non-vacuity parsing
# ---------------------------------------------------------------------------


def test_non_vacuity_detects_the_planted_anchor(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        "anchors=note:41 chars=180 states=active\n"
    )
    nv = scorer.non_vacuity(log, "note:41")
    assert nv["planted_note_injected"] is True
    assert nv["planted_anchor_injections"] == 1
    assert nv["inject_events"] == 1
    assert nv["total_chars_injected"] == 180


def test_non_vacuity_rejects_a_different_anchor(tmp_path):
    """An injection that fired for some OTHER note is not evidence for ours."""
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        "anchors=note:99 chars=120 states=active\n"
    )
    nv = scorer.non_vacuity(log, "note:41")
    assert nv["inject_events"] == 1
    assert nv["planted_note_injected"] is False


def test_non_vacuity_matches_anchor_exactly_not_by_substring(tmp_path):
    """`note:4` must not be satisfied by `note:41`."""
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        "anchors=note:41,note:42 chars=120 states=active,active\n"
    )
    assert scorer.non_vacuity(log, "note:4")["planted_note_injected"] is False
    assert scorer.non_vacuity(log, "note:42")["planted_note_injected"] is True


def test_non_vacuity_on_missing_log_is_not_a_silent_pass(tmp_path):
    nv = scorer.non_vacuity(tmp_path / "absent.log", "note:41")
    assert nv["audit_log_present"] is False
    assert nv["planted_note_injected"] is False
    assert nv["inject_events"] == 0


def test_non_vacuity_ignores_unrelated_audit_events(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-07-27T10:00:00Z REMEMBER workspace=/ws note_id=41\n"
        "2026-07-27T10:00:01Z RECALL workspace=/ws query=x\n"
    )
    nv = scorer.non_vacuity(log, "note:41")
    assert nv["inject_events"] == 0
    assert nv["planted_note_injected"] is False


# ---------------------------------------------------------------------------
# the scorer must be arm-blind
# ---------------------------------------------------------------------------


def test_score_run_takes_no_arm_argument():
    """Structural guard: the scorer cannot condition on which arm produced a run."""
    import inspect

    params = set(inspect.signature(scorer.score_run).parameters)
    assert "arm" not in params
    assert "inject" not in params


@pytest.mark.parametrize("slug", sorted(scen.SCENARIOS))
def test_scenario_materializes_and_scores_without_error(tmp_path, slug):
    scenario, ws, vd, base = _setup(tmp_path, slug)
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    assert result["scenario"] == slug
    assert result["checks_total"] == len(scenario.checks)
    json.dumps(result)  # results must be serializable for results.jsonl
