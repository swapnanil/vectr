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


# ---------------------------------------------------------------------------
# UPG-INJUTIL-EXEC-ANCHOR: reading a command is not running it.
#
# `test_command_checks_ignore_agent_prose` above covers the easy half (prose is not a
# tool_use). The hard half is a real Bash call whose *text* contains the command --
# `grep`, `cat`, `echo` -- which an unanchored `re.search` scores exactly like an
# invocation. `CommandRan(exec_anchor=True)` requires the pattern to match at a genuine
# command-execution position instead.
# ---------------------------------------------------------------------------

_READ_ONLY_MENTIONS = [
    'grep -rn "run_tests.sh --core" .',
    "cat run_tests.sh --core-notes",
    'echo "verify with ./run_tests.sh --core"',
    "rg --files-with-matches './run_tests.sh --core'",
    "less README.md  # documents ./run_tests.sh --core",
]

_REAL_INVOCATIONS = [
    "./run_tests.sh --core",
    "  ./run_tests.sh --core",
    "bash run_tests.sh --core",
    "sh ./run_tests.sh --core",
    "env CI=1 ./run_tests.sh --core",
    "CI=1 ./run_tests.sh --core",
    "timeout 60 ./run_tests.sh --core",
    "cd /w && ./run_tests.sh --core",
    "python3 -c 'pass'; ./run_tests.sh --core",
    "./run_tests.sh --core | tail -5",
]


@pytest.mark.parametrize("command", _READ_ONLY_MENTIONS)
def test_reading_the_core_command_is_not_running_it(tmp_path, command):
    scenario, ws, vd, base = _setup(tmp_path, "flaky_test")
    _s3_fix(ws)
    result = _score(scenario, ws, base, vd, _bash_transcript([command]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["used_core_test_command"] is False, (
        f"a read-only command must not satisfy the primary check: {command!r}"
    )
    assert result["utility_hit"] is False


@pytest.mark.parametrize("command", _REAL_INVOCATIONS)
def test_real_invocations_still_satisfy_the_anchored_check(tmp_path, command):
    """Anchoring must not cost recall: every shell form that genuinely runs the
    deterministic runner -- interpreter prefix, env assignment, `timeout`, a chained
    command, a pipe -- still passes."""
    scenario, ws, vd, base = _setup(tmp_path, "flaky_test")
    _s3_fix(ws)
    result = _score(scenario, ws, base, vd, _bash_transcript([command]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["used_core_test_command"] is True, f"real invocation missed: {command!r}"


def test_exec_anchor_is_opt_in_and_defaults_to_search_anywhere(tmp_path):
    """The default is the pre-existing anywhere-in-string `re.search`, so every check
    that does not opt in keeps its behavior byte-for-byte."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    unanchored = scen.CommandRan(name="mentions", pattern=r"run_tests\.sh\s+--core")
    anchored = scen.CommandRan(
        name="ran", pattern=r"(?:\./)?run_tests\.sh\s+--core", exec_anchor=True
    )
    commands = ['grep -n "run_tests.sh --core" README.md']

    assert unanchored.exec_anchor is False
    unanchored_result = scorer.evaluate_check(
        unanchored, workspace=workspace, baselines={}, commands=commands, verify_dir=tmp_path,
    )
    anchored_result = scorer.evaluate_check(
        anchored, workspace=workspace, baselines={}, commands=commands, verify_dir=tmp_path,
    )
    assert unanchored_result["passed"] is True
    assert anchored_result["passed"] is False


def test_exec_anchor_respects_want_false(tmp_path):
    """`want=False` (a restraint check: "the agent must NOT have run this") composes
    with anchoring -- a mention leaves the restraint satisfied, an invocation breaks it.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    check = scen.CommandRan(
        name="never_ran", pattern=r"(?:\./)?run_tests\.sh(?!\s+--core)", want=False,
        exec_anchor=True,
    )
    mention = scorer.evaluate_check(
        check, workspace=workspace, baselines={},
        commands=['grep -n "./run_tests.sh" README.md'], verify_dir=tmp_path,
    )
    invocation = scorer.evaluate_check(
        check, workspace=workspace, baselines={},
        commands=["./run_tests.sh"], verify_dir=tmp_path,
    )
    assert mention["passed"] is True
    assert invocation["passed"] is False


def test_exec_anchoring_matches_the_longitudinal_harness_behaviorally():
    """Drift guard for the deliberate duplication: `_strip_exec_prefixes`,
    `_exec_positions` and `_matches_at_exec_position` are copied per harness (each
    loads its own `scenarios.py` under its own `sys.modules` key, so a cross-harness
    import would break `isinstance()` dispatch). Copies drift silently unless
    something compares them, so this pins BEHAVIOR -- not source text, which
    legitimately differs in docstring wrapping -- against the longitudinal original.
    """
    import importlib.util

    key = "_vectr_eval_longitudinal_scorer_execanchor_parity"
    longitudinal = sys.modules.get(key)
    if longitudinal is None:
        path = (
            Path(__file__).resolve().parents[1]
            / "benchmarks" / "longitudinal_rediscovery" / "scorer.py"
        )
        spec = importlib.util.spec_from_file_location(key, path)
        longitudinal = importlib.util.module_from_spec(spec)
        sys.modules[key] = longitudinal
        spec.loader.exec_module(longitudinal)

    pattern = r"(?:\./)?run_tests\.sh\s+--core"
    for command in _READ_ONLY_MENTIONS + _REAL_INVOCATIONS:
        assert scorer._matches_at_exec_position(pattern, command) == (
            longitudinal._matches_at_exec_position(pattern, command)
        ), f"copies disagree on {command!r}"
        assert scorer._strip_exec_prefixes(command) == longitudinal._strip_exec_prefixes(command)
        assert scorer._exec_positions(command) == longitudinal._exec_positions(command)
    assert scorer._EXEC_PREFIX_TOKEN.pattern == longitudinal._EXEC_PREFIX_TOKEN.pattern
    assert scorer._EXEC_BOUNDARY.pattern == longitudinal._EXEC_BOUNDARY.pattern


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


def test_non_vacuity_excludes_preflight_events_before_the_offset(tmp_path):
    """The harness's own preflight probe must not count as an arm's injection.

    The daemon writes ONE audit log, and the preflight reachability probe calls
    /v1/proactive directly -- so its retrieval lands in the same file the proxy's
    injections do. Without an offset a `--no-inject` control reads as
    contaminated by an event the proxy never sent. Regression test for exactly
    that false verdict, observed on the first pilot cell.
    """
    log = tmp_path / "audit.log"
    preflight = (
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        "anchors=note:1 chars=336 states=active\n"
    )
    log.write_text(preflight)
    offset = len(preflight.encode("utf-8"))

    # Counted from byte 0 the control looks contaminated.
    assert scorer.non_vacuity(log, "note:1")["inject_events"] == 1

    # Counted from the post-preflight offset it is correctly clean.
    nv = scorer.non_vacuity(log, "note:1", since_offset=offset)
    assert nv["inject_events"] == 0
    assert nv["planted_note_injected"] is False
    assert nv["audit_since_offset"] == offset

    # A real proxy injection appended afterwards is still seen.
    with log.open("a", encoding="utf-8") as fh:
        fh.write(
            "2026-07-27T10:00:05Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
            "anchors=note:1 chars=336 states=active\n"
        )
    nv2 = scorer.non_vacuity(log, "note:1", since_offset=offset)
    assert nv2["inject_events"] == 1
    assert nv2["planted_note_injected"] is True


def _planted_event(chars: int = 314) -> str:
    return (
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        f"anchors=note:1 chars={chars} states=active\n"
    )


def test_retrieval_without_delivery_is_vacuous_not_a_negative_result(tmp_path):
    """The headline failure mode this harness exists to not fall for.

    Observed live on the first arm-A pilot cell: the daemon logged
    `items=1 anchors=note:1 chars=314` while the proxy reported
    `injected=0, inject_skipped=11`. `append_context_block` refuses to append
    when the request's last message is not a `user` turn, so the note was
    selected, charged against its cooldown slot, and then dropped — the model
    never saw it.

    If the audit line alone counted as proof, that cell would be scored a valid
    "injection happened and the note did not change behaviour". It must instead
    be VACUOUS and excluded.
    """
    log = tmp_path / "audit.log"
    log.write_text(_planted_event())

    nv = scorer.non_vacuity(log, "note:1", proxy_injected=0)
    assert nv["planted_note_retrieved"] is True
    assert nv["planted_note_delivered"] is False
    assert nv["retrieved_but_not_delivered"] is True
    # The conjunction is what the runner gates arm-A validity on.
    assert nv["planted_note_injected"] is False
    assert nv["delivery_unverified"] is False


def test_retrieval_with_delivery_is_non_vacuous(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text(_planted_event())

    nv = scorer.non_vacuity(log, "note:1", proxy_injected=1)
    assert nv["planted_note_retrieved"] is True
    assert nv["planted_note_delivered"] is True
    assert nv["retrieved_but_not_delivered"] is False
    assert nv["planted_note_injected"] is True


def test_delivery_without_retrieval_of_the_planted_note_is_still_vacuous(tmp_path):
    """The proxy injected *something*, but not our note — proves nothing."""
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-07-27T10:00:00Z PROACTIVE_INJECT workspace=/ws channel=proxy items=1 "
        "anchors=note:99 chars=120 states=active\n"
    )
    nv = scorer.non_vacuity(log, "note:1", proxy_injected=1)
    assert nv["planted_note_retrieved"] is False
    assert nv["planted_note_delivered"] is True
    assert nv["planted_note_injected"] is False


def test_missing_proxy_metric_degrades_to_retrieval_and_says_so(tmp_path):
    """Absent the proxy metric, never silently over-claim delivery."""
    log = tmp_path / "audit.log"
    log.write_text(_planted_event())

    nv = scorer.non_vacuity(log, "note:1")
    assert nv["planted_note_retrieved"] is True
    assert nv["planted_note_delivered"] is None
    assert nv["delivery_unverified"] is True
    assert nv["retrieved_but_not_delivered"] is False
    # Falls back to retrieval rather than failing closed on a missing metric...
    assert nv["planted_note_injected"] is True


def test_control_arm_is_contaminated_by_proxy_injection_even_with_clean_audit(tmp_path):
    """Arm B validity must also consider the proxy metric.

    An empty post-offset audit window is not by itself proof the control was
    clean; a delivered block with no matching audit line still contaminates it.
    """
    log = tmp_path / "audit.log"
    log.write_text("")
    nv = scorer.non_vacuity(log, "note:1", proxy_injected=2)
    assert nv["inject_events"] == 0
    assert nv["planted_note_delivered"] is True
    # The runner ANDs these two; neither alone is sufficient.
    assert not (nv["inject_events"] == 0 and (nv["proxy_injected"] or 0) == 0)


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
# hook_non_vacuity -- the `--arm hook` channel has no proxy and no
# PROACTIVE_INJECT audit line; retrieval comes from the daemon's own
# `hook_injection_counts` and delivery from the planted note's text actually
# appearing in the recorded transcript (UPG-PROXY-INJECT-HOOK-ARM).
# ---------------------------------------------------------------------------


def test_hook_non_vacuity_requires_both_retrieval_and_delivery(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "the planted fact"}}) + "\n"
    )
    nv = scorer.hook_non_vacuity(
        hook_injection_counts={"UserPromptSubmit": 1},
        transcript_path=transcript,
        planted_note_content="the planted fact",
    )
    assert nv["hook_fired"] is True
    assert nv["planted_content_in_transcript"] is True
    assert nv["planted_note_injected"] is True
    assert nv["retrieved_but_not_delivered"] is False


def test_hook_non_vacuity_zero_counts_is_vacuous_even_with_matching_text(tmp_path):
    """A zero-count daemon means no hook actually fired -- text matching the
    note appearing in the transcript some other way (e.g. the agent typing it
    out unprompted) must not be read as delivery."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "the planted fact"}}) + "\n"
    )
    nv = scorer.hook_non_vacuity(
        hook_injection_counts={"SessionStart": 0, "UserPromptSubmit": 0},
        transcript_path=transcript,
        planted_note_content="the planted fact",
    )
    assert nv["hook_fired"] is False
    assert nv["planted_note_injected"] is False


def test_hook_non_vacuity_retrieved_but_not_delivered(tmp_path):
    """The daemon counted a delivery but the note's text never made it into
    the transcript -- the hook-channel analogue of `retrieved_but_not_delivered`
    on the proxy path (see `non_vacuity`'s docstring)."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "unrelated turn"}}) + "\n"
    )
    nv = scorer.hook_non_vacuity(
        hook_injection_counts={"UserPromptSubmit": 1},
        transcript_path=transcript,
        planted_note_content="the planted fact",
    )
    assert nv["hook_fired"] is True
    assert nv["planted_content_in_transcript"] is False
    assert nv["planted_note_injected"] is False
    assert nv["retrieved_but_not_delivered"] is True


def test_hook_non_vacuity_missing_transcript_is_not_a_silent_pass(tmp_path):
    nv = scorer.hook_non_vacuity(
        hook_injection_counts={"UserPromptSubmit": 1},
        transcript_path=tmp_path / "absent.jsonl",
        planted_note_content="the planted fact",
    )
    assert nv["planted_content_in_transcript"] is False
    assert nv["planted_note_injected"] is False


def test_hook_non_vacuity_empty_counts_dict(tmp_path):
    """`None`/`{}` (the daemon status field can be absent) degrades to
    vacuous, never an error."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("")
    nv = scorer.hook_non_vacuity(
        hook_injection_counts={}, transcript_path=transcript, planted_note_content="x",
    )
    assert nv["hook_fired"] is False
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


# ---------------------------------------------------------------------------
# AllOf: a conjunction primary check, so "did nothing" is never a utility hit
# ---------------------------------------------------------------------------


def test_allof_requires_every_sub_check(tmp_path):
    """The whole point: restraint alone must not read as success."""
    scenario, ws, vd, base = _setup(tmp_path, "threadunsafe_pool")
    # Untouched workspace: POOL_SIZE is still 1 (the restraint half passes) but
    # nothing was tuned, so the conjunction must fail.
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["pool_size_held"] is True
    assert checks["batch_rows_changed"] is False
    assert result["utility_hit"] is False


def test_allof_reports_its_sub_checks(tmp_path):
    scenario, ws, vd, base = _setup(tmp_path, "threadunsafe_pool")
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    conj = next(c for c in result["checks"] if c["name"] == "tuned_without_growing_pool")
    names = [s["name"] for s in conj["sub_checks"]]
    assert names == ["pool_size_held", "batch_rows_changed"]
    assert "pool_size_held=PASS" in conj["detail"]
    assert "batch_rows_changed=FAIL" in conj["detail"]


def test_allof_rejects_a_verify_command_member():
    """A VerifyCommand inside a conjunction would break read-before-mutate."""
    with pytest.raises(TypeError):
        scen.AllOf(
            name="bad",
            of=(scen.VerifyCommand(name="v", argv=("true",)),),
        )


def test_allof_rejects_an_empty_conjunction():
    with pytest.raises(ValueError):
        scen.AllOf(name="empty", of=())


@pytest.mark.parametrize("slug", scen.FULL_RUN_CANDIDATES)
def test_full_run_candidate_primary_fails_on_an_untouched_workspace(tmp_path, slug):
    """No admitted scenario may score a utility hit for an agent that did nothing.

    Half of these scenarios ask the agent for RESTRAINT (leave the pin, leave
    the pool size, keep the wrapper). A primary check written as restraint alone
    is also satisfied by a run that crashed on turn one, which would report as
    injection utility. This asserts the hole is closed for every scenario the
    full run admits.
    """
    scenario, ws, vd, base = _setup(tmp_path, slug)
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    assert result["utility_hit"] is False, (
        f"{slug} scores a utility hit on an untouched workspace"
    )


# ---------------------------------------------------------------------------
# scenario: pinned_dependency
# ---------------------------------------------------------------------------

_S4_NAIVE = """# runtime pins
requests==2.32.3
urllib3==2.2.3
pydantic==2.9.2
rich==13.9.2
"""

_S4_NOTE_FOLLOWING = """# runtime pins
requests==2.32.3
urllib3==1.26.18
pydantic==2.9.2
rich==13.9.2
"""


def test_pinned_dependency_discriminates(tmp_path):
    transcript = _bash_transcript(["cat requirements.txt"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "pinned_dependency")
    (ws / "requirements.txt").write_text(_S4_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "pinned_dependency")
    (ws / "requirements.txt").write_text(_S4_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # Both arms did the task; they differ only on the one pin.
    assert naive_checks["requests_bumped"] is True
    assert good_checks["requests_bumped"] is True
    assert naive_checks["no_urllib3_2x"] is False
    assert good_checks["no_urllib3_2x"] is True


# ---------------------------------------------------------------------------
# scenario: vendor_batch_bug
# ---------------------------------------------------------------------------

_S5_NAIVE = '''"""Ship analytics events to Acme."""
from vendor_client import AcmeClient

client = AcmeClient()


def ship_login(user_id):
    return client.send({"type": "login", "user": user_id})


def ship_signup_batch(user_ids):
    return client.send_batch([{"type": "signup", "user": u} for u in user_ids])
'''

_S5_NOTE_FOLLOWING = '''"""Ship analytics events to Acme."""
from vendor_client import AcmeClient

client = AcmeClient()


def ship_login(user_id):
    return client.send({"type": "login", "user": user_id})


def ship_signup_batch(user_ids):
    return [client.send({"type": "signup", "user": u}) for u in user_ids]
'''


def test_vendor_batch_bug_discriminates(tmp_path):
    transcript = _bash_transcript(["python3 -c 'import shipper'"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "vendor_batch_bug")
    (ws / "src" / "shipper.py").write_text(_S5_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "vendor_batch_bug")
    (ws / "src" / "shipper.py").write_text(_S5_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # Both shipped the three signup events -- the arms differ on the API used.
    assert naive_checks["signup_events_shipped"] is True
    assert good_checks["signup_events_shipped"] is True
    assert naive_checks["ship_signup_batch_added"] is True
    assert good_checks["ship_signup_batch_added"] is True


# ---------------------------------------------------------------------------
# scenario: threadunsafe_pool
# ---------------------------------------------------------------------------

_S6_NAIVE = '''"""Ingest worker tuning knobs."""

POOL_SIZE = 8
BATCH_ROWS = 1000
LOG_EVERY_N_FLUSHES = 50
'''

_S6_NOTE_FOLLOWING = '''"""Ingest worker tuning knobs."""

POOL_SIZE = 1
BATCH_ROWS = 1000
LOG_EVERY_N_FLUSHES = 50
'''


def test_threadunsafe_pool_discriminates(tmp_path):
    transcript = _bash_transcript(["cat src/settings.py"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "threadunsafe_pool")
    (ws / "src" / "settings.py").write_text(_S6_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "threadunsafe_pool")
    (ws / "src" / "settings.py").write_text(_S6_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # Both tuned for throughput; only one grew the pool.
    assert naive_checks["batch_rows_changed"] is True
    assert good_checks["batch_rows_changed"] is True
    assert naive_checks["no_multi_connection_pool"] is False
    assert good_checks["no_multi_connection_pool"] is True


def test_threadunsafe_pool_regex_reads_a_two_digit_pool_as_grown(tmp_path):
    """`POOL_SIZE = 16` must not satisfy the `= 1` restraint check."""
    scenario, ws, vd, base = _setup(tmp_path, "threadunsafe_pool")
    (ws / "src" / "settings.py").write_text(
        _S6_NOTE_FOLLOWING.replace("POOL_SIZE = 1", "POOL_SIZE = 16")
    )
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["pool_size_held"] is False
    assert checks["no_multi_connection_pool"] is False


# ---------------------------------------------------------------------------
# scenario: retry_cleanup
# ---------------------------------------------------------------------------

_S7_NAIVE = '''"""Asset and manifest fetching."""
import http_client


def fetch_asset(path):
    return http_client.get(path)


def fetch_manifest():
    return http_client.get("/manifest.json")
'''

_S7_NOTE_FOLLOWING = '''"""Asset and manifest fetching."""
import http_client


def _retry_once_on_502(fn, *args):
    try:
        return fn(*args)
    except http_client.HttpError as exc:
        if exc.status != 502:
            raise
        return fn(*args)


def fetch_asset(path):
    return _retry_once_on_502(http_client.get, path)


def fetch_manifest():
    return http_client.get("/manifest.json")
'''


def test_retry_cleanup_discriminates(tmp_path):
    transcript = _bash_transcript(["cat src/fetcher.py"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "retry_cleanup")
    (ws / "src" / "fetcher.py").write_text(_S7_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "retry_cleanup")
    (ws / "src" / "fetcher.py").write_text(_S7_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # Both left a working fetcher; they differ on which wrapper survived.
    assert naive_checks["fetcher_still_works"] is True
    assert good_checks["fetcher_still_works"] is True
    assert naive_checks["retry_503_removed"] is True
    assert good_checks["retry_503_removed"] is True
    assert naive_checks["retry_502_kept"] is False
    assert good_checks["retry_502_kept"] is True


def test_retry_cleanup_keeping_both_wrappers_is_not_a_hit(tmp_path):
    """Doing nothing is not note-following, even though the 502 wrapper survives."""
    scenario, ws, vd, base = _setup(tmp_path, "retry_cleanup")
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["retry_502_kept"] is True
    assert checks["retry_503_removed"] is False
    assert result["utility_hit"] is False


# ---------------------------------------------------------------------------
# scenario: id_scheme_decision
# ---------------------------------------------------------------------------

_S8_NAIVE = '''"""Rows for the orders partition."""
from ids import new_uuid


def build_row(order):
    return {
        "id": new_uuid(),
        "customer": order["customer"],
        "amount": order["amount"],
    }
'''

_S8_NOTE_FOLLOWING = '''"""Rows for the orders partition."""
from ids import new_ulid


def build_row(order):
    return {
        "id": new_ulid(),
        "customer": order["customer"],
        "amount": order["amount"],
    }
'''


def test_id_scheme_decision_discriminates(tmp_path):
    transcript = _bash_transcript(["cat src/orders_writer.py"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "id_scheme_decision")
    (ws / "src" / "orders_writer.py").write_text(_S8_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "id_scheme_decision")
    (ws / "src" / "orders_writer.py").write_text(_S8_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    # Both populated the id -- the arms differ only on the scheme.
    assert naive_checks["order_row_has_id"] is True
    assert good_checks["order_row_has_id"] is True
    assert naive_checks["no_uuid_in_orders"] is False
    assert good_checks["no_uuid_in_orders"] is True


# ---------------------------------------------------------------------------
# scenario: silent_truncation
# ---------------------------------------------------------------------------

_S9_HEAD = '''"""Weekly digest sending."""
from pathlib import Path

from provider import ProviderClient
'''

_S9_BODY = '''
client = ProviderClient()

SUBJECT = "Your weekly digest"
BODY = "Here is what happened this week."


def load_subscribers():
    path = Path(__file__).resolve().parents[1] / "subscribers.txt"
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]

'''

_S9_NAIVE = _S9_HEAD + _S9_BODY + '''
def send_weekly_digest(recipients=None):
    if recipients is None:
        recipients = load_subscribers()
    return client.send_mail(subject=SUBJECT, body=BODY, to=list(recipients))
'''

_S9_NOTE_FOLLOWING = _S9_HEAD + "from util import chunked\n" + _S9_BODY + '''
def send_weekly_digest(recipients=None):
    if recipients is None:
        recipients = load_subscribers()
    return [
        client.send_mail(subject=SUBJECT, body=BODY, to=batch)
        for batch in chunked(list(recipients), 40)
    ]
'''


def test_silent_truncation_discriminates(tmp_path):
    transcript = _bash_transcript(["cat src/digest.py"])

    scenario, ws, vd, base = _setup(tmp_path / "naive", "silent_truncation")
    (ws / "src" / "digest.py").write_text(_S9_NAIVE)
    naive = _score(scenario, ws, base, vd, transcript)

    scenario, ws, vd, base = _setup(tmp_path / "good", "silent_truncation")
    (ws / "src" / "digest.py").write_text(_S9_NOTE_FOLLOWING)
    good = _score(scenario, ws, base, vd, transcript)

    assert naive["utility_hit"] is False
    assert good["utility_hit"] is True

    naive_checks = {c["name"]: c["passed"] for c in naive["checks"]}
    good_checks = {c["name"]: c["passed"] for c in good["checks"]}
    assert naive_checks["uses_chunking_helper"] is False
    assert good_checks["uses_chunking_helper"] is True


def test_silent_truncation_rejects_a_chunk_larger_than_the_cap(tmp_path):
    """Chunking on instinct is not enough: only the note carries the number."""
    scenario, ws, vd, base = _setup(tmp_path, "silent_truncation")
    (ws / "src" / "digest.py").write_text(
        _S9_NOTE_FOLLOWING.replace("chunked(list(recipients), 40)",
                                   "chunked(list(recipients), 100)")
    )
    result = _score(scenario, ws, base, vd, _bash_transcript([]))
    checks = {c["name"]: c["passed"] for c in result["checks"]}
    assert checks["uses_chunking_helper"] is True     # it chunked
    assert checks["every_send_within_provider_cap"] is False  # at the wrong size
    assert result["utility_hit"] is False
