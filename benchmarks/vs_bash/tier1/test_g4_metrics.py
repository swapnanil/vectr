#!/usr/bin/env python3
"""Unit tests for g4_metrics.py -- synthetic stream-json fixtures only, no
daemon, no network, no live `claude -p` session. Covers every honest-
verification path (a)-(e), the false-pass definition, wrapper stripping,
`cd`-prefix cwd handling, `-Dtest` glob matching, and Arm-M delivery
detection, per the G4 pre-registration's §5 (memoization-g4-
preregistration.md) and this repo's coder-report ambiguity list.

Run directly (not part of the repo's `tests/`/`agent`/`integrations`/`app`
pytest scope -- benchmarks/ is a driver/harness tree, not product code):
    ./.venv/bin/python -m pytest benchmarks/vs_bash/tier1/test_g4_metrics.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import g4_metrics as m  # noqa: E402

FIX_MODULE = "core/camel-core-languages"
TEST_MODULE = "core/camel-core"
GATE_TEST = "SimplePredicateParserLogicalTest"
SESSION_CWD = "/repo"


# ---------------------------------------------------------------------------
# Fixture builders -- minimal stream-json event shapes
# ---------------------------------------------------------------------------

def bash_call(tool_use_id: str, command: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}},
    ]}}


def bash_result(tool_use_id: str, text: str) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": text},
    ]}}


def edit_call(tool_use_id: str, tool_name: str, file_path: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": {"file_path": file_path}},
    ]}}


def maven_session(command: str, output: str = "BUILD SUCCESS") -> list[dict]:
    return [bash_call("t1", command), bash_result("t1", output)]


def evaluate(events, **kw):
    kw.setdefault("fix_module", FIX_MODULE)
    kw.setdefault("test_module", TEST_MODULE)
    kw.setdefault("gate_test", GATE_TEST)
    kw.setdefault("session_cwd", SESSION_CWD)
    return m.evaluate_transcript(events, **kw)


# ---------------------------------------------------------------------------
# Condition (a): -pl includes BOTH modules
# ---------------------------------------------------------------------------

def test_condition_a_both_modules_any_order():
    events = maven_session(
        "./mvnw -q -s settings.xml -pl core/camel-core,core/camel-core-languages "
        "test -Dtest=SimplePredicateParserLogicalTest"
    )
    out = evaluate(events)
    assert out["honest_verification_conditions"]["a"] is True
    assert out["honest_verification"] is True


def test_condition_a_requires_both_modules():
    events = maven_session("./mvnw -pl core/camel-core-languages test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification_conditions"]["a"] is False


# ---------------------------------------------------------------------------
# Condition (b): -pl test_module + -am
# ---------------------------------------------------------------------------

def test_condition_b_also_make():
    events = maven_session("mvn -pl core/camel-core -am test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification_conditions"]["b"] is True


def test_condition_b_false_without_also_make():
    events = maven_session("mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification_conditions"]["b"] is False


# ---------------------------------------------------------------------------
# Condition (c): no module selection, run from repo root (full reactor)
# ---------------------------------------------------------------------------

def test_condition_c_full_reactor_from_root():
    events = maven_session("mvn test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification_conditions"]["c"] is True


def test_condition_c_false_when_cwd_not_root():
    events = maven_session("cd core/camel-core && mvn test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification_conditions"]["c"] is False
    # single-module cd-into-module run with no -pl -> false-pass trap, not honest.
    assert out["false_pass_events"] == 1
    assert out["honest_verification"] is False


# ---------------------------------------------------------------------------
# Condition (d): two-step install-then-test
# ---------------------------------------------------------------------------

def test_condition_d_two_step_install_then_test():
    events = [
        bash_call("t1", "cd core/camel-core-languages && mvn install"),
        bash_result("t1", "BUILD SUCCESS"),
        bash_call("t2", "mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        bash_result("t2", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["d"] is True
    assert out["honest_verification"] is True


def test_condition_d_false_when_install_fails():
    events = [
        bash_call("t1", "cd core/camel-core-languages && mvn install"),
        bash_result("t1", "BUILD FAILURE"),
        bash_call("t2", "mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        bash_result("t2", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["d"] is False


def test_condition_d_step_order_matters():
    """The test-phase step must come LATER than the install step -- a
    test-then-install ordering does not satisfy (d)."""
    events = [
        bash_call("t1", "mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        bash_result("t1", "BUILD SUCCESS"),
        bash_call("t2", "cd core/camel-core-languages && mvn install"),
        bash_result("t2", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["d"] is False


# ---------------------------------------------------------------------------
# Condition (e): self-authored honest check (no gate-test-coverage
# requirement -- deliberate deviation from a/b/c per the ambiguity list)
# ---------------------------------------------------------------------------

def test_condition_e_fix_and_test_edit_same_module():
    events = [
        edit_call("e1", "Edit", f"{FIX_MODULE}/src/main/java/org/apache/camel/language/simple/ast/LogicalExpression.java"),
        edit_call("e2", "Write", f"{FIX_MODULE}/src/test/java/org/apache/camel/language/simple/MyOwnLogicalTest.java"),
        bash_call("t1", f"mvn -pl {FIX_MODULE} test"),
        bash_result("t1", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["e"] is True
    # (e) does not require -Dtest to match the gate test.
    assert out["maven_invocations"][0]["dtest_value"] is None


def test_condition_e_false_when_edits_in_different_modules():
    events = [
        edit_call("e1", "Edit", f"{FIX_MODULE}/src/main/java/Foo.java"),
        edit_call("e2", "Write", f"{TEST_MODULE}/src/test/java/BarTest.java"),
        bash_call("t1", f"mvn -pl {FIX_MODULE} test"),
        bash_result("t1", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["e"] is False


def test_condition_e_false_when_no_own_test_edit():
    events = [
        edit_call("e1", "Edit", f"{FIX_MODULE}/src/main/java/Foo.java"),
        bash_call("t1", f"mvn -pl {FIX_MODULE} test"),
        bash_result("t1", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["honest_verification_conditions"]["e"] is False


# ---------------------------------------------------------------------------
# No honest-verification event at all -- pure single-module false-pass
# (the T2 headline finding this whole experiment investigates)
# ---------------------------------------------------------------------------

def test_no_condition_fires_on_bare_single_module_run():
    events = maven_session("mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_verification"] is False
    assert not any(out["honest_verification_conditions"].values())


# ---------------------------------------------------------------------------
# False-pass event counting
# ---------------------------------------------------------------------------

def test_false_pass_counts_test_module_only_no_am_no_prior_install():
    events = maven_session("mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["false_pass_events"] == 1


def test_false_pass_not_counted_with_prior_successful_install():
    events = [
        bash_call("t1", f"cd {FIX_MODULE} && mvn install"),
        bash_result("t1", "BUILD SUCCESS"),
        bash_call("t2", f"mvn -pl {TEST_MODULE} test -Dtest=SimplePredicateParserLogicalTest"),
        bash_result("t2", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["false_pass_events"] == 0


def test_false_pass_not_counted_with_am():
    events = maven_session("mvn -pl core/camel-core -am test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["false_pass_events"] == 0


# ---------------------------------------------------------------------------
# Transparent-wrapper stripping (timeout / env)
# ---------------------------------------------------------------------------

def test_timeout_wrapper_stripped():
    events = maven_session("timeout 300 ./mvnw -pl core/camel-core-languages,core/camel-core "
                            "test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["n_maven_invocations"] == 1
    assert out["honest_verification_conditions"]["a"] is True


def test_env_wrapper_stripped():
    events = maven_session("env JAVA_HOME=/opt/jdk21 ./mvnw -pl core/camel-core-languages,core/camel-core "
                            "test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["n_maven_invocations"] == 1
    assert out["honest_verification_conditions"]["a"] is True


def test_non_maven_command_is_not_an_invocation():
    events = maven_session("echo hello && ls -la")
    out = evaluate(events)
    assert out["n_maven_invocations"] == 0
    assert out["honest_verification"] is False


# ---------------------------------------------------------------------------
# `cd X &&` prefix cwd handling
# ---------------------------------------------------------------------------

def test_cd_prefix_sets_effective_cwd():
    events = maven_session("cd core/camel-core && mvn test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["maven_invocations"][0]["cwd"] == "/repo/core/camel-core"
    # No -pl, cwd != session_cwd -- (c) does not fire; this is the exact
    # single-module false-pass shape the T2 baseline observed.
    assert out["honest_verification_conditions"]["c"] is False


def test_no_cd_prefix_defaults_to_session_cwd():
    events = maven_session("mvn test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["maven_invocations"][0]["cwd"] == "/repo"


def test_cd_in_prior_bash_call_does_not_persist():
    """Each Bash tool call is modeled as a fresh shell at the session cwd --
    a `cd` in one call must never leak into a later, separate call."""
    events = [
        bash_call("t1", "cd core/camel-core"),
        bash_result("t1", ""),
        bash_call("t2", "mvn test -Dtest=SimplePredicateParserLogicalTest"),
        bash_result("t2", "BUILD SUCCESS"),
    ]
    out = evaluate(events)
    assert out["maven_invocations"][0]["cwd"] == "/repo"
    assert out["honest_verification_conditions"]["c"] is True


# ---------------------------------------------------------------------------
# `-Dtest` surefire glob-semantics matching (gate-test coverage)
# ---------------------------------------------------------------------------

def test_dtest_absent_covers_gate_test():
    assert m.covers_gate_test(None, GATE_TEST) is True


def test_dtest_exact_match():
    assert m.covers_gate_test("SimplePredicateParserLogicalTest", GATE_TEST) is True


def test_dtest_glob_match():
    assert m.covers_gate_test("SimplePredicateParser*", GATE_TEST) is True
    assert m.covers_gate_test("*LogicalTest", GATE_TEST) is True


def test_dtest_no_match():
    assert m.covers_gate_test("SomeOtherTest", GATE_TEST) is False


def test_dtest_comma_separated_list_includes_gate_test():
    assert m.covers_gate_test("FooTest,SimplePredicateParserLogicalTest,BarTest", GATE_TEST) is True


def test_dtest_method_suffix_stripped():
    assert m.covers_gate_test("SimplePredicateParserLogicalTest#someMethod", GATE_TEST) is True


def test_dtest_exclusion_wins():
    assert m.covers_gate_test("*,!SimplePredicateParserLogicalTest", GATE_TEST) is False


def test_condition_a_false_when_dtest_excludes_gate_test():
    events = maven_session(
        "mvn -pl core/camel-core-languages,core/camel-core test -Dtest=*,!SimplePredicateParserLogicalTest"
    )
    out = evaluate(events)
    assert out["honest_verification_conditions"]["a"] is False


# ---------------------------------------------------------------------------
# Test-phase determination (-DskipTests scope)
# ---------------------------------------------------------------------------

def test_bare_test_goal_with_skiptests_still_test_phase():
    """Ambiguity resolution: -DskipTests's exclusion is read as attaching
    only to verify/install/package, not to a bare `test` goal."""
    events = maven_session("mvn test -DskipTests -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["maven_invocations"][0]["is_test_phase"] is True


def test_verify_with_skiptests_not_test_phase():
    events = maven_session("mvn -pl core/camel-core-languages,core/camel-core verify -DskipTests")
    out = evaluate(events)
    assert out["maven_invocations"][0]["is_test_phase"] is False
    assert out["honest_verification"] is False


def test_install_without_skiptests_is_test_phase():
    events = maven_session("mvn install")
    out = evaluate(events)
    assert out["maven_invocations"][0]["is_test_phase"] is True


def test_clean_compile_is_not_test_phase():
    events = maven_session("mvn clean compile")
    out = evaluate(events)
    assert out["maven_invocations"][0]["is_test_phase"] is False


# ---------------------------------------------------------------------------
# Honest red / piped / quieted secondary metrics
# ---------------------------------------------------------------------------

def test_honest_red_observed_on_build_failure():
    events = maven_session(
        "mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest",
        output="There are test failures.\n\nBUILD FAILURE",
    )
    out = evaluate(events)
    assert out["honest_red_observed"] is True


def test_honest_red_not_observed_on_build_success():
    events = maven_session("mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest")
    out = evaluate(events)
    assert out["honest_red_observed"] is False


def test_tests_run_marker_alone_is_not_honest_red():
    """Deliberate deviation from run_t2.py's own marker tuple: 'Tests run:'
    alone (present on passing runs too) must never count as a red signal."""
    events = maven_session(
        "mvn -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest",
        output="Tests run: 3, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS",
    )
    out = evaluate(events)
    assert out["honest_red_observed"] is False


def test_piped_and_quieted_detected():
    events = maven_session("mvn -q -pl core/camel-core test | tail -20")
    out = evaluate(events)
    assert out["piped_quieted"] == {"total": 1, "piped": 1, "quieted": 1, "piped_or_quieted_fraction": 1.0}


def test_piped_quieted_empty_when_no_invocations():
    out = evaluate([])
    assert out["piped_quieted"] == {"total": 0, "piped": 0, "quieted": 0, "piped_or_quieted_fraction": None}


# ---------------------------------------------------------------------------
# Arm-M delivery detection
# ---------------------------------------------------------------------------

_NOTE_TITLE = "Maven multi-module verification"
_NOTE_BODY_SUBSTRING = "compiles against previously installed artifacts"


def test_delivery_detected_in_tool_result():
    events = [
        {"type": "user", "message": {"content": "task prompt text"}},
        bash_call("h1", "./mvnw test"),
        bash_result("h1", f"[vectr] {_NOTE_TITLE}: In this multi-module Maven repo, a single-module "
                           f"test run... {_NOTE_BODY_SUBSTRING} from ~/.m2 ..."),
    ]
    out = evaluate(events, note_title=_NOTE_TITLE, note_body_substring=_NOTE_BODY_SUBSTRING)
    assert out["delivery"]["delivered"] is True
    assert "command-lane (tool_result)" in out["delivery"]["surfaces"]


def test_delivery_detected_at_prompt_time():
    events = [
        {"type": "user", "message": {"content": f"{_NOTE_TITLE}\n\n{_NOTE_BODY_SUBSTRING} from ~/.m2\n\nActual task prompt."}},
        bash_call("h1", "mvn test"),
        bash_result("h1", "BUILD SUCCESS"),
    ]
    out = evaluate(events, note_title=_NOTE_TITLE, note_body_substring=_NOTE_BODY_SUBSTRING)
    assert out["delivery"]["delivered"] is True
    assert "prompt-time (early event)" in out["delivery"]["surfaces"]


def test_delivery_not_detected_control_arm():
    events = [
        {"type": "user", "message": {"content": "Actual task prompt, no note text."}},
        bash_call("h1", "mvn test"),
        bash_result("h1", "BUILD SUCCESS"),
    ]
    out = evaluate(events, note_title=_NOTE_TITLE, note_body_substring=_NOTE_BODY_SUBSTRING)
    assert out["delivery"]["delivered"] is False
    assert out["delivery"]["surfaces"] == []


def test_delivery_key_absent_without_note_params():
    events = maven_session("mvn test")
    out = evaluate(events)
    assert "delivery" not in out


# ---------------------------------------------------------------------------
# Usage / vectr tool-call counts / result summary (hermetic, no tiktoken)
# ---------------------------------------------------------------------------

def test_usage_parsed_from_result_event():
    events = [
        bash_call("t1", "mvn test"),
        bash_result("t1", "BUILD SUCCESS"),
        {"type": "result", "num_turns": 5, "duration_ms": 12345, "is_error": False,
         "total_cost_usd": 0.05,
         "usage": {"input_tokens": 100, "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 20, "output_tokens": 200}},
    ]
    out = evaluate(events)
    assert out["usage"]["total_input_tokens"] == 130
    assert out["usage"]["usage_unparsed"] is False
    assert out["result"] == {"num_turns": 5, "duration_ms": 12345, "is_error": False, "cost_usd": 0.05}


def test_usage_unparsed_without_result_event():
    out = evaluate(maven_session("mvn test"))
    assert out["usage"]["usage_unparsed"] is True
    assert out["result"]["num_turns"] is None


def test_vectr_tool_call_counts():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "v1", "name": "mcp__vectr__vectr_search", "input": {"query": "x"}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "v2", "name": "mcp__vectr__vectr_search", "input": {"query": "y"}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "ls"}},
        ]}},
    ]
    out = evaluate(events)
    assert out["vectr_tool_call_counts"] == {"mcp__vectr__vectr_search": 2}
    assert out["vectr_tool_call_total"] == 2


# ---------------------------------------------------------------------------
# Edited-path extraction (condition (e)'s data source)
# ---------------------------------------------------------------------------

def test_extract_edited_paths_dedupes_and_preserves_order():
    events = [
        edit_call("e1", "Edit", "a/Foo.java"),
        edit_call("e2", "Write", "b/Bar.java"),
        edit_call("e3", "Edit", "a/Foo.java"),
    ]
    assert m.extract_edited_paths(events) == ["a/Foo.java", "b/Bar.java"]


def test_extract_edited_paths_ignores_non_edit_tools():
    events = [bash_call("t1", "cat a/Foo.java")]
    assert m.extract_edited_paths(events) == []
