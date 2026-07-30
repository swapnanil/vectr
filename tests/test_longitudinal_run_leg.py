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
import json
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


# ---------------------------------------------------------------------------
# DEFECT 4 (branch feature/eval-workspace-stable): --workspace-dir makes the
# agent/daemon workspace trajectory-stable instead of nested under the per-leg
# --leg-dir, so a note leg k plants is registered under the same workspace path
# leg k+1's fresh daemon queries (vectr's working-memory store scopes notes by
# workspace path, not by --db-dir alone -- see run_leg.py's module docstring and
# `--workspace-dir` help text).
# ---------------------------------------------------------------------------


def test_workspace_defaults_to_leg_dir_nested_when_workspace_dir_omitted(tmp_path):
    """Unchanged pre-fix behavior for any caller that never passes
    --workspace-dir (standalone k==1 debugging, T0's single-shot probe cells):
    the workspace stays nested under this leg's own --leg-dir, exactly as before.
    """
    runner = _make_runner(tmp_path)
    assert runner.workspace == runner.root / "workspace"
    assert runner.workspace == (tmp_path / "leg" / "workspace")


def test_workspace_dir_flag_overrides_to_a_trajectory_stable_path(tmp_path):
    """The actual fix: an explicit --workspace-dir (what run_plan.py now always
    passes for a normal leg) is used verbatim instead of the per-leg default, and
    is independent of --leg-dir/--k entirely.
    """
    traj_workspace = tmp_path / "traj" / "workspace"
    runner_k2 = _make_runner(
        tmp_path, k=2, leg_dir=str(tmp_path / "traj" / "legs" / "2"),
        workspace_dir=str(traj_workspace),
    )
    runner_k3 = _make_runner(
        tmp_path, k=3, leg_dir=str(tmp_path / "traj" / "legs" / "3"),
        workspace_dir=str(traj_workspace),
    )
    # Same workspace path for k=2 and k=3 of one trajectory (the invariant this
    # whole fix exists to establish)...
    assert runner_k2.workspace == runner_k3.workspace == traj_workspace
    # ...while each leg's own artifacts stay per-leg, unaffected.
    assert runner_k2.root != runner_k3.root
    assert runner_k2.artifacts == tmp_path / "traj" / "legs" / "2" / "artifacts"
    assert runner_k3.artifacts == tmp_path / "traj" / "legs" / "3" / "artifacts"


def test_workspace_dir_rejected_with_shared_leg1():
    ap = run_leg._build_argparser()
    args = ap.parse_args([
        "--shared-leg1", "--scenario", "bench_box_only", "--out-dir", "/tmp/x",
        "--workspace-dir", "/tmp/y",
    ])
    with pytest.raises(SystemExit):
        run_leg._validate_args(ap, args)


def test_reset_workspace_wipes_stale_content_left_by_a_prior_leg(tmp_path):
    """The other half of the fix: reusing one workspace path across legs is only
    safe because `prepare()`'s `_reset_workspace()` wipes it first -- otherwise a
    file an earlier leg's agent created (and that is absent from this leg's own
    restore-tar / fresh materialize) would wrongly persist into a later leg.
    """
    runner = _make_runner(tmp_path, workspace_dir=str(tmp_path / "traj-workspace"))
    runner.workspace.mkdir(parents=True)
    stray = runner.workspace / "leftover_from_prior_leg.txt"
    stray.write_text("should not survive")
    assert stray.exists()

    runner._reset_workspace()

    assert runner.workspace.is_dir()
    assert not stray.exists()
    assert list(runner.workspace.iterdir()) == []


def test_reset_workspace_is_a_no_op_the_first_time(tmp_path):
    """A never-before-used workspace path (first leg of a trajectory) has nothing
    to wipe -- must not raise on a directory that does not exist yet.
    """
    runner = _make_runner(tmp_path, k=1, workspace_dir=str(tmp_path / "brand-new"))
    assert not runner.workspace.exists()
    runner._reset_workspace()
    assert runner.workspace.is_dir()
    assert list(runner.workspace.iterdir()) == []


# ---------------------------------------------------------------------------
# DEFECT 6 (branch feature/eval-hook-preflight): zero-cost SessionStart hook
# mechanism preflight, run before the paid `claude -p` session for arms
# hook-sessionstart/hook-full. None of these tests spawn a real daemon or hook
# subprocess: `_http_json` (daemon calls) and `_run_hook_command` (the hook
# subprocess spawn) are the two module-level seams, both monkeypatched here --
# the exact same pattern the existing plant_note() tests above use for `_http_json`.
# ---------------------------------------------------------------------------


def _make_hook_runner(tmp_path: Path, *, arm: str = "hook-sessionstart", **overrides) -> "run_leg.LegRunner":
    overrides.setdefault("planted_note_id", 7)
    overrides.setdefault("planted_anchor", "note:7")
    runner = _make_runner(tmp_path, arm=arm, **overrides)
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.artifacts.mkdir(parents=True, exist_ok=True)
    settings_path = runner.workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{
                "matcher": "startup|resume|clear|compact",
                "hooks": [{"type": "command", "command": "fake-vectr-hook session-start"}],
            }],
        },
    }))
    runner.record["hooks_settings_path"] = str(settings_path)
    return runner


def _note_content(runner: "run_leg.LegRunner") -> str:
    return runner._find_note_variant().content


def test_hook_preflight_noop_for_non_hook_arms(tmp_path, monkeypatch):
    """arm "proxy" (or "none"/"mcp"/"mcp-bare") must be completely unaffected --
    no daemon call, no hook subprocess spawn, no record mutation.
    """
    runner = _make_runner(tmp_path)  # default arm="proxy" per _make_runner
    assert runner.arm == "proxy"

    def fail_http(*a, **k):
        raise AssertionError("hook_preflight must not touch the daemon for a non-hook arm")

    def fail_run(*a, **k):
        raise AssertionError("hook_preflight must not spawn a hook subprocess for a non-hook arm")

    monkeypatch.setattr(run_leg, "_http_json", fail_http)
    monkeypatch.setattr(run_leg, "_run_hook_command", fail_run)

    runner.hook_preflight()  # must return immediately, no exception

    assert "hook_preflight" not in runner.record


def test_hook_preflight_noop_when_nothing_planted(tmp_path, monkeypatch):
    """A hook arm with no planted note yet (e.g. leg 1) has nothing to preflight."""
    runner = _make_hook_runner(tmp_path, planted_note_id=None, planted_anchor=None)
    assert runner.note_id is None and runner.planted_anchor is None

    def fail_http(*a, **k):
        raise AssertionError("must not touch the daemon with nothing planted")

    monkeypatch.setattr(run_leg, "_http_json", fail_http)
    runner.hook_preflight()
    assert "hook_preflight" not in runner.record


def test_hook_preflight_success_records_evidence_and_does_not_abort(tmp_path, monkeypatch):
    runner = _make_hook_runner(tmp_path)
    content = _note_content(runner)

    status_calls = {"n": 0}

    def fake_http_json(method, url, payload=None, timeout=30):
        assert url.startswith(f"http://127.0.0.1:{runner.daemon_port}/")
        assert method == "GET" and url.endswith("/v1/status")
        status_calls["n"] += 1
        # Before the hook call: 0 injections; after: 1 -- a real delta.
        count = 0 if status_calls["n"] == 1 else 1
        return {"hook_injection_counts": {"SessionStart": count}}

    def fake_run_hook_command(command, *, cwd, stdin, env, timeout=60.0):
        assert command == "fake-vectr-hook session-start"
        assert cwd == str(runner.workspace)
        payload = json.loads(stdin)
        assert payload["cwd"] == str(runner.workspace)
        assert payload["hook_event_name"] == "SessionStart"
        stdout = json.dumps({
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": content}
        })
        return 0, stdout, "", False

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)
    monkeypatch.setattr(run_leg, "_run_hook_command", fake_run_hook_command)

    runner.hook_preflight()  # must not raise

    hp = runner.record["hook_preflight"]
    assert hp["daemon_evidence"] is True
    assert hp["session_start_delta"] == 1
    assert hp["stdout_has_planted_content"] is True
    assert hp["command"] == "fake-vectr-hook session-start"
    assert (runner.artifacts / "hook-preflight.json").is_file()


def test_hook_preflight_aborts_when_daemon_evidence_missing(tmp_path, monkeypatch):
    """The hook subprocess reports success and echoes the right content, but this
    leg's own scratch daemon never sees hook_injection_counts move -- the exact
    live symptom this preflight exists to catch pre-spend (DEFECT 6).
    """
    runner = _make_hook_runner(tmp_path)
    content = _note_content(runner)

    def fake_http_json(method, url, payload=None, timeout=30):
        # Same (zero) count before and after -- no daemon-side evidence.
        return {"hook_injection_counts": {}}

    def fake_run_hook_command(command, *, cwd, stdin, env, timeout=60.0):
        stdout = json.dumps({
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": content}
        })
        return 0, stdout, "", False

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)
    monkeypatch.setattr(run_leg, "_run_hook_command", fake_run_hook_command)

    with pytest.raises(SystemExit) as excinfo:
        runner.hook_preflight()
    msg = str(excinfo.value)
    assert "daemon_evidence=False" in msg
    assert "fix the scenario" in msg
    # The artifact is still written even on abort, for post-mortem.
    hp = runner.record["hook_preflight"]
    assert hp["daemon_evidence"] is False
    assert hp["stdout_has_planted_content"] is True


def test_hook_preflight_aborts_when_stdout_content_missing(tmp_path, monkeypatch):
    """Daemon-side evidence is real (the count moves), but the hook's own stdout
    never carried the planted note's content -- e.g. a kind/trigger-eligibility
    gap where the delivery mechanism fires but returns empty/unrelated text.
    """
    runner = _make_hook_runner(tmp_path)

    status_calls = {"n": 0}

    def fake_http_json(method, url, payload=None, timeout=30):
        status_calls["n"] += 1
        count = 0 if status_calls["n"] == 1 else 1
        return {"hook_injection_counts": {"SessionStart": count}}

    def fake_run_hook_command(command, *, cwd, stdin, env, timeout=60.0):
        return 0, "", "", False  # empty stdout -- e.g. a silently-ineligible note

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)
    monkeypatch.setattr(run_leg, "_run_hook_command", fake_run_hook_command)

    with pytest.raises(SystemExit) as excinfo:
        runner.hook_preflight()
    msg = str(excinfo.value)
    assert "stdout_has_planted_content=False" in msg
    hp = runner.record["hook_preflight"]
    assert hp["daemon_evidence"] is True
    assert hp["stdout_has_planted_content"] is False


def test_hook_preflight_wrong_daemon_resolution_abort_names_the_culprit_port(tmp_path, monkeypatch):
    """Instance mis-resolution (the hook command resolves, via the global
    ~/.vectr/instances.json registry, to a DIFFERENT daemon than this leg's own
    scratch port) is caught by the SAME daemon_evidence assertion (the count never
    moves on THIS leg's own daemon) -- this test additionally checks the registry
    diagnostic names the wrong port in the abort message instead of leaving it a
    mystery.
    """
    runner = _make_hook_runner(tmp_path)
    content = _note_content(runner)
    other_port = runner.daemon_port + 1

    registry_path = tmp_path / "instances.json"
    registry_path.write_text(json.dumps({
        run_leg._workspace_hash(str(runner.workspace.resolve())): {"port": other_port},
    }))
    monkeypatch.setattr(run_leg, "_VECTR_INSTANCE_REGISTRY_PATH", registry_path)

    def fake_http_json(method, url, payload=None, timeout=30):
        # This leg's own daemon never sees the increment (the hook actually
        # talked to `other_port`, simulated here simply as "no evidence").
        return {"hook_injection_counts": {}}

    def fake_run_hook_command(command, *, cwd, stdin, env, timeout=60.0):
        stdout = json.dumps({
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": content}
        })
        return 0, stdout, "", False

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)
    monkeypatch.setattr(run_leg, "_run_hook_command", fake_run_hook_command)

    with pytest.raises(SystemExit) as excinfo:
        runner.hook_preflight()
    msg = str(excinfo.value)
    assert f"port {other_port}" in msg
    assert "mis-resolution" in msg
    hp = runner.record["hook_preflight"]
    assert hp["registry_resolved_port"] == other_port
    assert hp["registry_port_matches_leg_daemon"] is False


def test_hook_preflight_allow_hook_unreachable_bypasses_abort(tmp_path, monkeypatch):
    runner = _make_hook_runner(tmp_path, allow_hook_unreachable=True)

    def fake_http_json(method, url, payload=None, timeout=30):
        return {"hook_injection_counts": {}}

    def fake_run_hook_command(command, *, cwd, stdin, env, timeout=60.0):
        return 0, "", "", False

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)
    monkeypatch.setattr(run_leg, "_run_hook_command", fake_run_hook_command)

    runner.hook_preflight()  # must not raise despite both assertions failing

    hp = runner.record["hook_preflight"]
    assert hp["daemon_evidence"] is False
    assert hp["stdout_has_planted_content"] is False


def test_run_agent_passes_settings_for_hook_arms_only(tmp_path, monkeypatch):
    """`--settings <hooks_settings_path>` is added to the spawned `claude` argv
    for arms hook-sessionstart/hook-full (so headless hook loading no longer
    depends on directory trust), and left out for every other arm.
    """
    captured = {}

    class _FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(run_leg.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_leg.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    hook_runner = _make_hook_runner(tmp_path)
    hook_runner.run_agent()
    assert "--settings" in captured["cmd"]
    idx = captured["cmd"].index("--settings")
    assert captured["cmd"][idx + 1] == hook_runner.record["hooks_settings_path"]

    proxy_runner = _make_runner(tmp_path)  # arm="proxy"
    proxy_runner.workspace.mkdir(parents=True, exist_ok=True)
    proxy_runner.artifacts.mkdir(parents=True, exist_ok=True)
    proxy_runner.run_agent()
    assert "--settings" not in captured["cmd"]


# ---------------------------------------------------------------------------
# DEFECT 6 follow-up: plant-time hook-channel delivery metadata
# (`LegRunner._apply_hook_delivery_metadata()`). `plant_note()` always sends
# an EXPLICIT `triggers` list (every `NoteVariant.trigger_paths` is
# non-empty), which per `trigger_engine.effective_triggers()`'s
# replace-not-merge contract means the note's `kind` default trigger bundle
# never applies -- and the explicit list is PATH-only, which
# `trigger_engine._trigger_matches()` can never match at SessionStart/boot
# (no `file_path` is ever supplied there, so a path-bearing trigger
# deterministically fails). `_apply_hook_delivery_metadata()` closes this for
# arms "hook-sessionstart"/"hook-full" only by (1) appending an explicit
# `{"event": "session-start"}` trigger and (2) switching `kind` to
# "directive" (gotcha's 100-token per-kind injection cap is smaller than
# every scenario's real full-block render — 101-181 tokens measured directly
# against the shipped renderer — so a gotcha-kind hook note would always
# degrade to its index-tier, title-only line; directive's 400-token cap is
# not). Both empirically verified live against a scratch daemon (real
# `vectr hook session-start` + `vectr hook pre-tool-use`) before being
# encoded here — see the coder-lane report for this follow-up.
# ---------------------------------------------------------------------------


def _trigger_is_session_start_eligible(trigger: dict) -> bool:
    """Conservative, LOCAL mirror of `agent/trigger_engine.py`'s real
    `_trigger_matches()` conjunction check, specialized to the SessionStart/
    boot lifecycle moment — the same pattern
    `test_longitudinal_scorer.py::_probe_file_structurally_reaches` uses to
    mirror `recall_for_path()` rather than importing the product module (so
    this test stays a $0, offline, no-daemon structural check).

    At boot (`app/service.py::_recall_impl`'s `boot` branch ->
    `WorkingContextStore.fire_and_format(events=["session-start"], ...)`), no
    file_path/query/command/resolved-symbols context is ever supplied
    (nothing is being edited, run, or searched yet) — so the real
    `_trigger_matches()` structurally rejects any trigger declaring 'path',
    'symbol', 'semantic', or 'command' (each of those axes requires
    call-time context absent at boot), and accepts only a trigger whose
    'event' is unset or literally "session-start". `validate_trigger()`
    requires at least one axis, so a trigger meeting this rule always has
    `event == "session-start"` with no other axis set.
    """
    return (
        trigger.get("path") is None
        and trigger.get("symbol") is None
        and not trigger.get("semantic")
        and trigger.get("command") is None
        and trigger.get("event") == "session-start"
    )


def _hook_plant_payload(variant: "scen.NoteVariant") -> dict:
    """The subset of `plant_note()`'s payload construction that
    `_apply_hook_delivery_metadata()` reads/mutates (`triggers`, `kind`) —
    built the identical way `plant_note()` itself builds it, so this test
    exercises the real transformation, not a re-description of it."""
    payload: dict = {"kind": variant.kind}
    if variant.trigger_paths:
        payload["triggers"] = [{"path": p} for p in variant.trigger_paths]
    return payload


def test_every_note_variant_is_session_start_eligible_for_hook_arms(tmp_path):
    """Regression guard for DEFECT 6 (a live hook-sessionstart leg came back
    INVALID with `hook_injection_counts: {}`, no planted content delivered):
    the hook-arm analogue of
    `test_longitudinal_scorer.py::test_every_leg_probe_file_structurally_reaches_every_note_variant`.
    For every scenario, every note variant, both hook arms: the `triggers`
    list `_apply_hook_delivery_metadata()` produces must contain at least one
    session-start-eligible trigger (per the real trigger engine's own
    matching rule, mirrored above), and `kind` must be a kind whose per-kind
    injection budget the real content actually fits — asserted here as
    "directive", not "gotcha", for the reason documented on
    `_apply_hook_delivery_metadata()` itself. No daemon, no network call:
    pure structural evaluation over `scenarios.py`'s real data — the $0 test
    that would have caught DEFECT 6 before any `claude -p` spend.
    """
    for arm in ("hook-sessionstart", "hook-full"):
        runner = _make_runner(tmp_path, arm=arm)
        for slug, scenario in scen.SCENARIOS.items():
            for variant in scenario.note_variants:
                payload = _hook_plant_payload(variant)
                runner._apply_hook_delivery_metadata(payload)
                triggers = payload.get("triggers") or []
                assert any(_trigger_is_session_start_eligible(t) for t in triggers), (
                    f"arm={arm} {slug}/{variant.variant}: no session-start-eligible "
                    f"trigger in {triggers!r} — the SessionStart hook channel would "
                    f"silently deliver nothing for this note (DEFECT 6)"
                )
                assert payload["kind"] == "directive", (
                    f"arm={arm} {slug}/{variant.variant}: hook-arm kind is "
                    f"{payload['kind']!r}, not 'directive' — gotcha's 100-token "
                    f"per-kind injection cap is smaller than every scenario's "
                    f"real full-block render (101-181 tokens measured), so "
                    f"content would silently degrade to its index-tier, "
                    f"title-only line regardless of trigger eligibility"
                )


def test_apply_hook_delivery_metadata_is_a_noop_for_non_hook_arms(tmp_path):
    """Channel parity (DESIGN.md 8): every arm OTHER than hook-sessionstart/
    hook-full must be byte-for-byte unaffected by
    `_apply_hook_delivery_metadata()` — the advisory text and its delivery
    metadata are identical to today's shape for `none`/`mcp`/`mcp-bare`/
    `proxy`."""
    for arm in ("none", "mcp", "mcp-bare", "proxy"):
        runner = _make_runner(tmp_path, arm=arm)
        payload = {"kind": "gotcha", "triggers": [{"path": "**/foo.py"}]}
        before = json.loads(json.dumps(payload))
        runner._apply_hook_delivery_metadata(payload)
        assert payload == before


def test_plant_note_sends_directive_kind_and_session_start_trigger_for_hook_arms(tmp_path, monkeypatch):
    """End-to-end through `plant_note()` itself (not just the metadata helper
    in isolation): the real POST /v1/remember payload for a hook arm carries
    `kind="directive"` and a `{"event": "session-start"}` trigger alongside
    the scenario's own path triggers; a non-hook arm's payload is unchanged
    from before this follow-up (still `kind="gotcha"`, path-only triggers).
    """
    captured = {}

    def fake_http_json(method, url, payload=None, timeout=30):
        if method == "POST" and url.endswith("/v1/remember"):
            captured["payload"] = payload
            return {"note_id": 7}
        if method == "GET" and url.endswith("/v1/status"):
            return {"notes_count": 1}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(run_leg, "_http_json", fake_http_json)

    hook_runner = _make_runner(tmp_path, arm="hook-sessionstart")
    hook_runner.notes_count_at_start = 0
    hook_runner.record["notes_in_store_at_start"] = 0
    hook_runner.plant_note()
    hook_payload = captured["payload"]
    assert hook_payload["kind"] == "directive"
    assert {"event": "session-start"} in hook_payload["triggers"]
    assert any("path" in t for t in hook_payload["triggers"]), (
        "the pre-existing path-anchored triggers must survive alongside the "
        "added session-start trigger (OR composition), not be replaced"
    )

    proxy_runner = _make_runner(tmp_path, arm="proxy")
    proxy_runner.notes_count_at_start = 0
    proxy_runner.record["notes_in_store_at_start"] = 0
    proxy_runner.plant_note()
    proxy_payload = captured["payload"]
    assert proxy_payload["kind"] == "gotcha"
    assert {"event": "session-start"} not in proxy_payload["triggers"]
