"""Unit tests for `benchmarks/longitudinal_rediscovery/run_leg.py`'s own defects
fixed on branch `feature/eval-t1-validity-fixes`:

1. `LegRunner.plant_note()` re-snapshots `/v1/status` AFTER planting so
   `notes_count_at_start` (the scorer's non-vacuity premise counter, DESIGN.md 4.1)
   records what the agent will actually see -- not the stale pre-plant count taken
   in `start_daemon()`, before the plant step runs.
2. `main()` treats a session-level abort (`scorer.leg_non_vacuity`'s arm-agnostic
   `session_errored` gate) as a genuine abort: nonzero process exit, never chained
   or cached by a driver.

Neither test spawns a real daemon, proxy, or agent: `plant_note()`'s only external
calls are through the module-level `_http_json` helper, which is monkeypatched here;
the exit-on-session-error behavior is exercised directly against a constructed
`record` dict, the same shape `LegRunner.score()` produces.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "longitudinal_rediscovery"

# Same collision-safe loading pattern as test_longitudinal_plan.py / _scorer.py: two
# harnesses under benchmarks/ ship same-named scenarios.py/scorer.py files, so a bare
# `import` would race for one sys.modules slot. run_leg.py itself uses this identical
# `_vectr_eval_longitudinal_scenarios` key when it loads scenarios.py, so this test's
# `scen` and run_leg's own converge on ONE module object.
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, BENCH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(_LONGITUDINAL_SCENARIOS_KEY, "scenarios.py")
run_leg = _load_local_module("_vectr_eval_longitudinal_run_leg_test", "run_leg.py")


def _make_runner(tmp_path: Path, **overrides) -> "run_leg.LegRunner":
    ap = run_leg._build_argparser()
    argv = [
        "--scenario", "bench_box_only",
        "--seed", "0",
        "--k", "2",
        "--leg-dir", str(tmp_path / "leg"),
        "--trajectory-id", "bench_box_only-proxy-plain-s0",
        "--arm", "proxy",
        "--note-variant", "plain",
        "--db-dir", str(tmp_path / "db"),
        "--daemon-port", "8899",
        "--proxy-port", "8900",
    ]
    args = ap.parse_args(argv)
    for k, v in overrides.items():
        setattr(args, k, v)
    return run_leg.LegRunner(args)


# ---------------------------------------------------------------------------
# Defect 1: plant_note() re-snapshots notes_count_at_start AFTER planting
# ---------------------------------------------------------------------------


def test_plant_note_records_post_plant_note_count(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    # start_daemon() would normally set this from a pre-plant /v1/status read;
    # simulate that here without a real daemon.
    runner.notes_count_at_start = 0
    runner.record["notes_in_store_at_start"] = 0

    calls: list[tuple[str, str]] = []

    def fake_http_json(method, url, payload=None, timeout=30):
        calls.append((method, url))
        if method == "POST" and url.endswith("/v1/remember"):
            return {"note_id": 7}
        if method == "GET" and url.endswith("/v1/status"):
            # Post-plant: the store now holds the just-planted note.
            return {"notes_count": 1}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)

    runner.plant_note()

    # The premise counter the scorer reads must reflect the POST-plant count...
    assert runner.notes_count_at_start == 1
    assert runner.record["notes_in_store_at_start"] == 1
    # ...and the stale pre-plant snapshot is preserved separately, not discarded.
    assert runner.record["notes_in_store_pre_plant"] == 0
    # One GET /v1/status re-snapshot happened, after the POST /v1/remember plant.
    assert ("POST", f"http://127.0.0.1:{runner.daemon_port}/v1/remember") in calls
    assert ("GET", f"http://127.0.0.1:{runner.daemon_port}/v1/status") in calls
    plant_i = calls.index(("POST", f"http://127.0.0.1:{runner.daemon_port}/v1/remember"))
    status_i = calls.index(("GET", f"http://127.0.0.1:{runner.daemon_port}/v1/status"))
    assert plant_i < status_i


def test_plant_note_falls_back_to_pre_plant_count_on_bad_status_reply(tmp_path, monkeypatch):
    """A daemon hiccup on the diagnostic re-snapshot must not abort the leg over a
    read that isn't the plant itself -- notes_count_at_start keeps its last known
    (pre-plant) value rather than being corrupted by a non-int/error reply.
    """
    runner = _make_runner(tmp_path)
    runner.notes_count_at_start = 3
    runner.record["notes_in_store_at_start"] = 3

    def fake_http_json(method, url, payload=None, timeout=30):
        if method == "POST" and url.endswith("/v1/remember"):
            return {"note_id": 9}
        if method == "GET" and url.endswith("/v1/status"):
            return {"_error": "ConnectionResetError"}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)

    runner.plant_note()

    assert runner.notes_count_at_start == 3
    assert runner.record["notes_in_store_at_start"] == 3
    assert runner.record["notes_in_store_pre_plant"] == 3


# ---------------------------------------------------------------------------
# Defect 2: a session-level abort exits main() nonzero
# ---------------------------------------------------------------------------


def test_main_raises_systemexit_when_score_flags_session_errored():
    """`main()` runs `score()` (which calls `scorer.leg_non_vacuity`, setting
    `record["non_vacuity"]["session_errored"]`) then calls
    `_abort_if_session_errored(runner.record)` before returning. This test calls
    that real function directly against a constructed record shaped exactly like
    `score()` would leave it, so no daemon/proxy/agent is spawned.
    """
    errored_record = {
        "non_vacuity": {"session_errored": True},
        "invalid_reason": "agent session errored (is_error=True, agent_returncode=1, output_tokens=0)",
    }
    with pytest.raises(SystemExit) as excinfo:
        run_leg._abort_if_session_errored(errored_record)
    assert "agent session errored" in str(excinfo.value)

    clean_record = {
        "non_vacuity": {"session_errored": False},
        "invalid_reason": "",
    }
    # A clean session must never trip the same abort path (this is the negative
    # control -- the whole point of scoping the abort to session_errored alone,
    # not to every invalid leg: a task-failing-but-ran leg is still a completed
    # leg for resumability purposes, exit 0).
    run_leg._abort_if_session_errored(clean_record)  # must not raise

    # Also: a record with no non_vacuity key at all (e.g. a code path that never
    # reached score()) must not spuriously abort.
    run_leg._abort_if_session_errored({})  # must not raise


def test_leg_non_vacuity_session_errored_produces_the_reason_main_reads():
    """Ties the scorer's `session_errored` gate to the exact record shape
    `run_leg.py`'s `main()` inspects (`record["non_vacuity"]["session_errored"]`,
    `record["invalid_reason"]`) -- the true end-to-end contract, without spawning a
    live agent. See test_longitudinal_scorer.py for the scorer-level discrimination
    tests of this same gate.
    """
    scorer = _load_local_module("_vectr_eval_longitudinal_scorer_test", "scorer.py")
    events = [
        {"type": "system", "subtype": "init", "mcp_servers": [], "tools": []},
        {"type": "result", "subtype": "success", "num_turns": 1, "usage": {}, "is_error": True},
    ]
    nv = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=1, is_error=True, output_tokens=0,
    )
    record = {"non_vacuity": nv["non_vacuity"], "invalid_reason": nv["invalid_reason"], "valid": nv["valid"]}
    assert record["valid"] is False
    assert record["non_vacuity"]["session_errored"] is True
    should_abort = (record.get("non_vacuity") or {}).get("session_errored")
    assert should_abort
