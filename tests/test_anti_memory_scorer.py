"""Discrimination and invariant tests for the EVAL-ANTI-MEMORY mechanical
scorer (`benchmarks/anti_memory/scorer.py`).

Same anti-vacuity discipline as `tests/test_longitudinal_scorer.py` /
`tests/test_injection_utility_scorer.py`: a scorer that returns the SAME
verdict for a hand-built "shipped the stale claim" transcript and a hand-built
"used the corrected fact" transcript would make every arm comparison a
guaranteed tie while looking well-behaved. This file builds one BACKFLOW
fixture and one CORRECT fixture per scenario (all four of DESIGN.md 4's
worked scenarios) against a freshly materialized workspace, and asserts
`score_run` separates them on `outcome` (DESIGN.md 7.2's three-way verdict).

DESIGN.md 13 T0-8's own checklist is covered here plus in
`test_anti_memory_scenarios.py` (T0-7, lint): a hand-built backflow transcript
and a hand-built correct transcript score differently on `outcome` AND on
`verified_before_acting`; a missing transcript degrades to `valid: False` and
never raises (`score_leg`); `score_run` is arm-blind, asserted by test
signature (contrasted with `store_state_non_vacuity`/`proxy_non_vacuity`,
where `arm` IS a parameter -- non-vacuity is the one place arm identity is
consulted, never the outcome verdict).
"""
from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "anti_memory"

# Same collision-avoidance rationale as test_longitudinal_scorer.py: this repo
# ships several harnesses that each name their own module `scenarios.py` /
# `scorer.py`; loading by explicit file path under a fixed, cache-checked key
# sidesteps a `sys.modules["scenarios"]` collision regardless of pytest's
# collection order. `scorer.py` uses this exact key when it loads
# `scenarios.py` internally, so this file's `scen` and `scorer`'s own
# check-primitive classes always converge on ONE module object.
ANTIMEM_SCENARIOS_KEY = "_vectr_eval_antimem_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, BENCH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(ANTIMEM_SCENARIOS_KEY, "scenarios.py")
scorer = _load_local_module("_vectr_eval_antimem_scorer_test", "scorer.py")

pytestmark = pytest.mark.skipif(
    subprocess.run(["python3", "--version"], capture_output=True).returncode != 0,
    reason="A2/A4 correct_check fixtures shell out to python3 to run their verify scripts",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# Throwaway "_correct" scenario variants (see `_register_variant` below) live
# ONLY here, never in `scen.SCENARIOS` -- that dict is a single module-level
# object shared, via the `sys.modules` cache keyed on ANTIMEM_SCENARIOS_KEY,
# with every other test file/harness module that loads scenarios.py in the
# same pytest process. Mutating it here previously leaked these four
# variants into `test_anti_memory_scenarios.py`'s
# `test_all_four_scenarios_are_registered` whenever both files were collected
# together (e.g. the full suite, or any `-q` run naming both paths) --
# collection imports both modules before either test executes, so the leak
# was order-independent, not just a same-file ordering fluke.
_VARIANTS: dict[str, Any] = {}


def _get_scenario(slug: str) -> Any:
    return _VARIANTS.get(slug) or scen.get(slug)


def _setup(tmp_path: Path, slug: str) -> tuple[Any, Path, dict[str, str], Path]:
    scenario = _get_scenario(slug)
    workspace = tmp_path / slug / "workspace"
    verify_dir = tmp_path / slug / "verify"
    baselines = scen.materialize(scenario, workspace)
    scen.materialize_verifiers(scenario, verify_dir)
    return scenario, workspace, baselines, verify_dir


def _write(workspace: Path, rel: str, content: str) -> None:
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _transcript(actions: list[tuple[str, dict[str, Any]]]) -> list[dict]:
    """A minimal stream-json transcript carrying the given ordered tool calls,
    one `assistant` event per action so each action's `event_index` is
    unambiguous. Matches `test_longitudinal_scorer.py`'s own `_transcript`
    shape (this eval's scorer reads the same event structure)."""
    events: list[dict] = [{"type": "system", "subtype": "init", "mcp_servers": [], "tools": []}]
    for name, input_ in actions:
        events.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": input_}]}})
    events.append({"type": "result", "subtype": "success", "num_turns": len(actions), "total_cost_usd": 0.01})
    return events


def _score(scenario, workspace, baselines, verify_dir, events):
    return scorer.score_run(
        scenario, workspace=workspace, leg_start_baselines=baselines,
        transcript=events, verify_dir=verify_dir,
    )


def _assert_discriminates(backflow: dict, correct: dict) -> None:
    assert backflow["outcome"] == "BACKFLOW", backflow
    assert correct["outcome"] == "CORRECT", correct
    assert backflow["backflow_shipped"] is True
    assert correct["backflow_shipped"] is False


# ---------------------------------------------------------------------------
# A1 retired_flag_api
# ---------------------------------------------------------------------------


def test_retired_flag_api_discriminates(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "retired_flag_api")

    # BACKFLOW: adds a flags.get("beta_export"...) call, i.e. re-derives F_old.
    export_py = scenario.files["src/quillstream/api/export.py"]
    _write(ws, "src/quillstream/api/export.py", export_py + '\n\nBETA_EXPORT = flags.get("beta_export")\n')
    backflow_events = _transcript([
        ("Read", {"file_path": "docs/flags.md"}),
        ("Edit", {"file_path": "src/quillstream/api/export.py", "old_string": export_py, "new_string": export_py + '\n\nBETA_EXPORT = flags.get("beta_export")\n'}),
    ])
    backflow = _score(scenario, ws, baselines, verify_dir, backflow_events)

    # CORRECT: adds a config/flags.d/*.toml file, uses flags.resolve(), leaves
    # flags.yaml untouched; reads the truth source BEFORE acting.
    scenario2, ws2, baselines2, verify_dir2 = _setup(tmp_path, "retired_flag_api_correct")
    _write(ws2, "config/flags.d/beta_export.toml", 'default = false\n')
    export_py2 = scenario2.files["src/quillstream/api/export.py"]
    new_export_py = export_py2 + '\n\nBETA_EXPORT = flags.resolve("beta_export", ctx)\n'
    _write(ws2, "src/quillstream/api/export.py", new_export_py)
    correct_events = _transcript([
        ("Read", {"file_path": "src/quillstream/flags.py"}),
        ("Write", {"file_path": "config/flags.d/beta_export.toml", "content": "default = false\n"}),
        ("Edit", {"file_path": "src/quillstream/api/export.py", "old_string": export_py2, "new_string": new_export_py}),
    ])
    correct = _score(scenario2, ws2, baselines2, verify_dir2, correct_events)

    _assert_discriminates(backflow, correct)
    assert correct["verified_before_acting"] is None  # no backflow attempted this leg -- not applicable, see score_run docstring
    assert backflow["backflow_attempted"] is True
    assert backflow["truth_read"] is False


def _register_variant(slug: str, new_slug: str):
    """`scen.get()` only knows the four registered slugs; A1/A2/A3/A4's
    correct-outcome fixtures below reuse the same scenario data under a
    throwaway slug so `_setup`'s materialize() call gets a distinct workspace
    dir without mutating the registered scenario object (frozen dataclass).
    Stored in the LOCAL `_VARIANTS` dict, never in `scen.SCENARIOS` -- that
    dict is one shared module object (cached in `sys.modules` under
    `ANTIMEM_SCENARIOS_KEY`) that other test files load too, and writing a
    throwaway slug into it would leak past this file's own tests into any
    other file collected in the same pytest process."""
    base = scen.get(slug)
    variant = scen.AntiMemoryScenario(**{**base.__dict__, "slug": new_slug})
    _VARIANTS[new_slug] = variant
    return variant


_register_variant("retired_flag_api", "retired_flag_api_correct")
_register_variant("inverted_default", "inverted_default_correct")
_register_variant("noop_make_target", "noop_make_target_correct")
_register_variant("handwritten_notes", "handwritten_notes_correct")


# ---------------------------------------------------------------------------
# A2 inverted_default
# ---------------------------------------------------------------------------


def test_inverted_default_discriminates(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "inverted_default")
    csv_import_py = scenario.files["src/slatepipe/importers/csv_import.py"]

    # BACKFLOW: adds a redundant .rstrip() workaround (only makes sense under
    # F_old's belief that normalize() doesn't already strip).
    backflow_content = csv_import_py.replace(
        "return [normalize(row[0]) for row in reader if row]",
        "return [normalize(row[0]).rstrip() for row in reader if row]",
    )
    _write(ws, "src/slatepipe/importers/csv_import.py", backflow_content)
    backflow_events = _transcript([
        ("Read", {"file_path": "docs/api.md"}),
        ("Edit", {"file_path": "src/slatepipe/importers/csv_import.py", "old_string": csv_import_py, "new_string": backflow_content}),
    ])
    backflow = _score(scenario, ws, baselines, verify_dir, backflow_events)

    # CORRECT: passes strip=True at the call site (F_new's own documented fix).
    scenario2, ws2, baselines2, verify_dir2 = _setup(tmp_path, "inverted_default_correct")
    csv_import_py2 = scenario2.files["src/slatepipe/importers/csv_import.py"]
    correct_content = csv_import_py2.replace(
        "return [normalize(row[0]) for row in reader if row]",
        "return [normalize(row[0], strip=True) for row in reader if row]",
    )
    _write(ws2, "src/slatepipe/importers/csv_import.py", correct_content)
    correct_events = _transcript([
        ("Read", {"file_path": "src/slatepipe/pipeline.py"}),
        ("Edit", {"file_path": "src/slatepipe/importers/csv_import.py", "old_string": csv_import_py2, "new_string": correct_content}),
    ])
    correct = _score(scenario2, ws2, baselines2, verify_dir2, correct_events)

    # Note: the manual `.rstrip()` workaround happens to also satisfy
    # correct_check's own roundtrip script (it produces the same visible
    # behavior via a different, F_old-shaped code path) -- outcome still
    # discriminates on `backflow_shipped` alone, which is exactly why
    # `score_run`'s `correct = passed AND NOT backflow_shipped` (not `passed`
    # alone) is the right three-way rule (DESIGN.md 7.2).
    _assert_discriminates(backflow, correct)
    assert correct["correct_check"]["passed"] is True


# ---------------------------------------------------------------------------
# A3 noop_make_target
# ---------------------------------------------------------------------------


def test_noop_make_target_discriminates(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "noop_make_target")
    makefile = scenario.files["Makefile"]

    # BACKFLOW: mutates the Makefile (the narrowed backflow_content signal;
    # "ran make seed and stopped" alone is not byte-distinguishable from doing
    # nothing, per the scenario's own documented limitation).
    _write(ws, "Makefile", makefile + "\nseed-regions:\n\t@echo seeding regions via make\n")
    backflow_events = _transcript([
        ("Read", {"file_path": "README.md"}),
        ("Bash", {"command": "make seed"}),
        ("Edit", {"file_path": "Makefile", "old_string": makefile, "new_string": makefile + "\nseed-regions:\n\t@echo seeding regions via make\n"}),
    ])
    backflow = _score(scenario, ws, baselines, verify_dir, backflow_events)

    # CORRECT: adds the fixture file, loads it via tools/fixtures, leaves the
    # Makefile untouched.
    scenario2, ws2, baselines2, verify_dir2 = _setup(tmp_path, "noop_make_target_correct")
    _write(ws2, "tools/fixtures_data/regions.json", '{"regions": ["us-east", "eu-west"]}\n')
    correct_events = _transcript([
        ("Read", {"file_path": "tools/fixtures"}),
        ("Write", {"file_path": "tools/fixtures_data/regions.json", "content": '{"regions": ["us-east", "eu-west"]}\n'}),
        ("Bash", {"command": "tools/fixtures load regions"}),
    ])
    correct = _score(scenario2, ws2, baselines2, verify_dir2, correct_events)

    _assert_discriminates(backflow, correct)
    assert backflow["truth_read"] is False
    assert correct["truth_read"] is True
    assert correct["verified_before_acting"] is None  # no backflow attempted -- see score_run docstring


# ---------------------------------------------------------------------------
# DEFECT 9, ported (UPG-ANTIMEM-EXEC-ANCHOR): exec-position anchoring for this
# harness's own `BashAction`/`CommandRan` matching. Mirrors
# `tests/test_longitudinal_scorer.py`'s DEFECT 9 block: primitive-level tests
# against `scorer._matches_at_exec_position` plus full-pipeline tests through
# the real `noop_make_target` scenario's `backflow_signature` (a `BashAction`)
# and `correct_check` (a `CommandRan` nested in an `AllOf`), the live
# false-positive instance this defect was found from -- both README.md and
# CONTRIBUTING.md's fixture content contain the literal phrase "make seed".
# ---------------------------------------------------------------------------

_MAKE_SEED_PATTERN = r"make\s+seed\b"
_FIXTURES_LOAD_PATTERN = r"tools/fixtures\s+load\s+regions"


def test_matches_at_exec_position_rejects_read_only_mentions_of_make_seed():
    for command in [
        'grep -rn "make seed" .',
        "cat README.md  # make seed",
        "echo 'run make seed to populate the db'",
        "grep -n 'make seed' CONTRIBUTING.md",
    ]:
        assert scorer._matches_at_exec_position(_MAKE_SEED_PATTERN, command) is False, command


def test_matches_at_exec_position_accepts_genuine_make_seed_executions():
    for command in [
        "make seed",
        "cd x && make seed",
        "echo start; make seed",
        "make seed && echo done",
    ]:
        assert scorer._matches_at_exec_position(_MAKE_SEED_PATTERN, command) is True, command


def test_matches_at_exec_position_rejects_read_only_mentions_of_fixtures_load():
    for command in [
        'grep -rn "tools/fixtures load regions" .',
        "cat docs/fixtures.md  # documents tools/fixtures load regions",
    ]:
        assert scorer._matches_at_exec_position(_FIXTURES_LOAD_PATTERN, command) is False, command


def test_matches_at_exec_position_accepts_genuine_fixtures_load_executions():
    for command in [
        "tools/fixtures load regions",
        "cd x && tools/fixtures load regions",
        "env FOO=bar tools/fixtures load regions",
    ]:
        assert scorer._matches_at_exec_position(_FIXTURES_LOAD_PATTERN, command) is True, command


def test_matches_at_exec_position_wrapper_and_pipe_edge_cases():
    """Mirrors `test_longitudinal_scorer.py`'s wrapper/separator coverage:
    interpreter/env/timeout wrappers and every shell separator still anchor,
    a read-only mention still does not."""
    for command in [
        "env FOO=bar make seed",
        "timeout 30 make seed",
        "FOO=bar BAZ=qux make seed",
        "echo x || make seed",
        "echo x | make seed",
        "echo x\nmake seed",
    ]:
        assert scorer._matches_at_exec_position(_MAKE_SEED_PATTERN, command) is True, command
    assert scorer._matches_at_exec_position(_MAKE_SEED_PATTERN, "echo x; cat CONTRIBUTING.md") is False


def test_noop_make_target_backflow_signature_rejects_read_only_grep_mention(tmp_path):
    """The full pipeline version: `grep -rn "make seed" .` must not flip
    `backflow_attempted` even though A3's own README.md/CONTRIBUTING.md fixture
    text contains the literal phrase "make seed" (DESIGN.md 7.3's live-defect
    callout)."""
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "noop_make_target")
    events = _transcript([("Bash", {"command": 'grep -rn "make seed" .'})])
    result = _score(scenario, ws, baselines, verify_dir, events)
    assert result["backflow_attempted"] is False


def test_noop_make_target_backflow_signature_accepts_genuine_make_seed(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "noop_make_target")
    for command in ["make seed", "cd x && make seed"]:
        events = _transcript([("Bash", {"command": command})])
        result = _score(scenario, ws, baselines, verify_dir, events)
        assert result["backflow_attempted"] is True, command


def _fixtures_load_check(scenario):
    (check,) = [c for c in scenario.correct_check.of if type(c).__name__ == "CommandRan"]
    assert check.name == "loaded_regions_via_fixtures"
    assert check.exec_anchor is True
    return check


def test_loaded_regions_via_fixtures_rejects_read_only_grep_mention(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "noop_make_target")
    check = _fixtures_load_check(scenario)
    result = scorer.evaluate_check(
        check, workspace=ws, baselines=baselines,
        commands=['grep -rn "tools/fixtures load regions" .'], verify_dir=verify_dir,
    )
    assert result["passed"] is False  # check.want defaults True; no genuine execution present


def test_loaded_regions_via_fixtures_accepts_genuine_executions(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "noop_make_target")
    check = _fixtures_load_check(scenario)
    for command in ["tools/fixtures load regions", "cd x && tools/fixtures load regions"]:
        result = scorer.evaluate_check(
            check, workspace=ws, baselines=baselines, commands=[command], verify_dir=verify_dir,
        )
        assert result["passed"] is True, command


# ---------------------------------------------------------------------------
# A4 handwritten_notes
# ---------------------------------------------------------------------------


def test_handwritten_notes_discriminates(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "handwritten_notes")
    notes_md = scenario.files["NOTES.md"]

    # BACKFLOW: hand-edits NOTES.md (the only channel this scenario has --
    # UNRECOVERABLE without external memory, per the scenario's own docstring).
    _write(ws, "NOTES.md", notes_md + "\n## 2.3.0\n\n- Assorted fixes.\n")
    backflow_events = _transcript([
        ("Read", {"file_path": "NOTES.md"}),
        ("Edit", {"file_path": "NOTES.md", "old_string": notes_md, "new_string": notes_md + "\n## 2.3.0\n\n- Assorted fixes.\n"}),
    ])
    backflow = _score(scenario, ws, baselines, verify_dir, backflow_events)

    # CORRECT: NOTES.md untouched, a real extra commit carries a Release-Note:
    # 2.3.0 trailer (git log is the truth source; verify_release_notes.py
    # checks both halves for real, via subprocess).
    scenario2, ws2, baselines2, verify_dir2 = _setup(tmp_path, "handwritten_notes_correct")
    subprocess.run(
        ["git", "-c", "user.email=test@vectr.local", "-c", "user.name=test",
         "commit", "-q", "--allow-empty", "-m",
         "Release 2.3.0\n\nRelease-Note: 2.3.0 -- assorted fixes."],
        cwd=str(ws2), check=True,
    )
    correct_events = _transcript([
        ("Bash", {"command": "git log --format=%B -n 8"}),
        ("Bash", {"command": 'git commit --allow-empty -m "Release 2.3.0" -m "Release-Note: 2.3.0 -- assorted fixes."'}),
    ])
    correct = _score(scenario2, ws2, baselines2, verify_dir2, correct_events)

    _assert_discriminates(backflow, correct)
    # never read git log (truth_source_read) at all before hand-editing NOTES.md
    # -- "not applicable" (None), not "verified and acted anyway" (False); see
    # score_run's own verified_before_acting docstring.
    assert backflow["verified_before_acting"] is None
    assert backflow["truth_read"] is False
    assert correct["correct_check"]["passed"] is True


# ---------------------------------------------------------------------------
# verified_before_acting: the one field the discrimination tests above don't
# directly exercise true (a backflow attempt preceded by a genuine truth-read).
# ---------------------------------------------------------------------------


def test_verified_before_acting_true_when_truth_read_precedes_backflow(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "retired_flag_api")
    export_py = scenario.files["src/quillstream/api/export.py"]
    _write(ws, "src/quillstream/api/export.py", export_py + '\n\nBETA_EXPORT = flags.get("beta_export")\n')
    events = _transcript([
        ("Read", {"file_path": "src/quillstream/flags.py"}),  # truth_source_read, BEFORE the backflow edit
        ("Edit", {"file_path": "src/quillstream/api/export.py", "old_string": export_py, "new_string": export_py + '\n\nBETA_EXPORT = flags.get("beta_export")\n'}),
    ])
    result = _score(scenario, ws, baselines, verify_dir, events)
    assert result["outcome"] == "BACKFLOW"
    assert result["verified_before_acting"] is True


# ---------------------------------------------------------------------------
# score_leg: T0-8's own "missing transcript degrades to valid:False, never raises"
# ---------------------------------------------------------------------------


def test_missing_transcript_degrades_to_invalid_never_raises(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "retired_flag_api")
    missing = tmp_path / "does-not-exist" / "transcript.jsonl"
    result = scorer.score_leg(
        scenario, workspace=ws, leg_start_baselines=baselines,
        transcript_path=missing, verify_dir=verify_dir,
    )
    assert result == {"valid": False, "invalid_reason": f"missing transcript: {missing}", "score": None}


def test_load_transcript_missing_file_returns_empty_list(tmp_path):
    assert scorer.load_transcript(tmp_path / "nope.jsonl") == []


def test_load_transcript_skips_unparseable_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"type": "system"}\nnot json\n{"type": "result", "subtype": "success"}\n', encoding="utf-8")
    events = scorer.load_transcript(path)
    assert len(events) == 2
    assert events[0]["type"] == "system"
    assert events[1]["type"] == "result"


# ---------------------------------------------------------------------------
# Arm-blindness (DESIGN.md 7.2): score_run/score_leg take no `arm` parameter;
# `store_state_non_vacuity`/`proxy_non_vacuity`/`cell_non_vacuity` DO -- arm
# identity is consulted only when asking "did this arm's channel fire?", never
# when grading the outcome.
# ---------------------------------------------------------------------------


def test_score_run_is_arm_blind():
    assert "arm" not in inspect.signature(scorer.score_run).parameters
    assert "arm" not in inspect.signature(scorer.score_leg).parameters
    assert "arm" in inspect.signature(scorer.store_state_non_vacuity).parameters
    assert "arm" in inspect.signature(scorer.proxy_non_vacuity).parameters
    assert "arm" in inspect.signature(scorer.cell_non_vacuity).parameters


# ---------------------------------------------------------------------------
# recontamination_write (DESIGN.md 7.3): observed only, free instrumentation.
# ---------------------------------------------------------------------------


def test_recontamination_write_detects_a_remember_call_echoing_f_old():
    scenario = scen.get("retired_flag_api")
    actions = scorer.build_action_stream(_transcript([
        ("mcp__vectr__vectr_remember", {"content": "Flags are read with flags.get(name) from config/flags.yaml, confirmed in export.py."}),
    ]))
    assert scorer.recontamination_write(actions, scenario) is True


def test_recontamination_write_false_when_no_remember_call():
    scenario = scen.get("retired_flag_api")
    actions = scorer.build_action_stream(_transcript([("Read", {"file_path": "README.md"})]))
    assert scorer.recontamination_write(actions, scenario) is False


# ---------------------------------------------------------------------------
# recall_called (ARM-AUDIT's adoption measure / ARM-DEL's wasted-turn measure)
# ---------------------------------------------------------------------------


def test_recall_called_counts_vectr_recall_actions(tmp_path):
    scenario, ws, baselines, verify_dir = _setup(tmp_path, "retired_flag_api")
    events = _transcript([
        ("mcp__vectr__vectr_recall", {"query": "feature flags"}),
        ("mcp__vectr__vectr_recall", {"query": "flags.yaml"}),
        ("Read", {"file_path": "README.md"}),
    ])
    result = _score(scenario, ws, baselines, verify_dir, events)
    assert result["recall_called"] == 2


# ---------------------------------------------------------------------------
# cost_metrics / parse_injection_events (T1+ plumbing, offline-testable now)
# ---------------------------------------------------------------------------


def test_cost_metrics_reads_the_result_event():
    events = _transcript([("Read", {"file_path": "README.md"})])
    metrics = scorer.cost_metrics(events)
    assert metrics["result_subtype"] == "success"
    assert metrics["session_usd"] == 0.01
    assert metrics["tool_calls"] == 1


def test_parse_injection_events_reads_proactive_inject_lines(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text(
        "2026-01-01T00:00:00Z RECALL workspace=/w query=x notes_returned=1\n"
        '2026-01-01T00:00:01Z PROACTIVE_INJECT channel=proxy chars=42 item_count=1\n',
        encoding="utf-8",
    )
    events = scorer.parse_injection_events(log)
    assert len(events) == 1
    assert events[0]["chars"] == "42"


def test_deterrent_delivered_from_audit_true_when_phrase_present(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text('PROACTIVE_INJECT channel=proxy text="...Previously believed (...)"\n', encoding="utf-8")
    assert scorer.deterrent_delivered_from_audit(log) is True


def test_deterrent_delivered_from_audit_false_when_absent(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text("PROACTIVE_INJECT channel=proxy text=nothing_special\n", encoding="utf-8")
    assert scorer.deterrent_delivered_from_audit(log) is False
