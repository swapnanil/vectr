"""Discrimination and invariant tests for the longitudinal-rediscovery mechanical scorer.

The point of the discrimination tests is anti-vacuity, exactly as in
`tests/test_injection_utility_scorer.py`: a scorer that returns the SAME verdict for
an agent that re-derives a fact the hard way (and along the way commits the scenario's
documented mistake) and one that uses the fact directly would make every longitudinal
A/B comparison a guaranteed tie, while looking well-behaved. For every scenario this
file builds two hand-written fixtures against a freshly materialized workspace -- one
"naive" (commits the mistake, never reaches the fact) and one "fact-using" (acquires
the fact directly, avoids the mistake, and satisfies the leg's real checks) -- and
asserts `score_run`/`leg_metrics` separate them on the primary check AND on
`turns_to_fact` (DESIGN.md section 11.6).

The remaining tests in this file cover the other section 11.6 requirements directly at
the primitive level: censoring never imputes from session totals; a missing transcript
degrades to "not acquired" rather than raising; `leg_non_vacuity`'s anchor matching is
exact (`note:4` is never satisfied by `note:41`); `score_run`/`leg_metrics` take no
`arm` argument; and the cross-scenario structural invariants scenarios.py's own
`__post_init__` and docstrings promise (fact-token confinement, the forcing-leg flag,
verify scripts never landing inside a workspace, every note variant carrying the fact
sentence byte-identically).

`mistake_repetition_rate is null when leg 1 is clean` is NOT tested here: it is a
TRAJECTORY-level aggregate across sibling legs that `leg_metrics()`'s own docstring
says explicitly belongs to `report.py` (not yet implemented -- see the coder-lane
report for this task), not to this per-leg scorer. What IS tested here is the leg-level
primitive that aggregate will read: `weak_prior` (`test_weak_prior_flags_a_clean_leg1_on_a_discovered_scenario`).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "longitudinal_rediscovery"

# `benchmarks/injection_utility/` ships its own same-named `scenarios.py`/`scorer.py`,
# and `tests/test_injection_utility_scorer.py` imports them under those bare names. A
# bare `sys.path.insert` + `import scenarios`/`import scorer` here would race that
# file for `sys.modules["scenarios"]`/`["scorer"]` -- whichever test module pytest
# collects first wins, and the loser silently binds the wrong harness's module
# (observed: `scen.get()` resolving to the trap harness's slugs). Loading by explicit
# file path under a fixed, cache-checked key sidesteps the collision regardless of
# collection order. `scenarios.py` uses the identical `_vectr_eval_longitudinal_scenarios`
# key when `scorer.py` loads it internally, so this file's `scen` and `scorer`'s own
# check-primitive classes always converge on ONE module object.
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
scorer = _load_local_module("_vectr_eval_longitudinal_scorer_test", "scorer.py")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _transcript(
    actions: list[tuple[str, dict[str, Any]]],
    *,
    mcp_servers: list[dict[str, str]] | None = None,
    tools: list[str] | None = None,
    num_turns: int = 4,
    usage: dict[str, Any] | None = None,
) -> list[dict]:
    """A minimal stream-json transcript carrying the given ordered tool calls.

    One `assistant` event per action (not batched) so each action's `event_index`
    lands on its own event, matching how `leg_metrics` reads the acquiring event's
    own usage for `context_tokens_at_fact`.
    """
    usage = usage or {"input_tokens": 100, "output_tokens": 50}
    events: list[dict] = [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": mcp_servers if mcp_servers is not None else [],
            "tools": tools if tools is not None else [],
        }
    ]
    for name, input_ in actions:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": name, "input": input_}],
                    "usage": usage,
                },
            }
        )
    events.append(
        {
            "type": "result",
            "subtype": "success",
            "num_turns": num_turns,
            "duration_ms": 1234,
            "total_cost_usd": 0.01,
            "usage": usage,
        }
    )
    return events


def _init_and_result_events(
    mcp_servers: list[dict[str, str]], tools: list[str] | None = None
) -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "mcp_servers": mcp_servers, "tools": tools or []},
        {"type": "result", "subtype": "success", "num_turns": 1, "usage": {}},
    ]


def _setup(tmp_path: Path, slug: str, *, variant: str, leg_index: int = 0):
    """Materialize a FRESH workspace under `tmp_path/variant` -- naive and
    fact-using fixtures must never share a workspace directory, or one fixture's
    mutation would leak into the other's `FileUnchanged` baseline.
    """
    scenario = scen.get(slug)
    root = tmp_path / variant
    workspace = root / "workspace"
    verify_dir = root / "verify"
    baselines = scen.materialize(scenario, workspace)
    leg = scenario.legs[leg_index]
    scen.materialize_verifiers(leg, verify_dir)
    return scenario, leg, workspace, verify_dir, baselines


def _write(workspace: Path, rel: str, content: str) -> None:
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _score(leg, workspace: Path, baselines, verify_dir: Path, events: list[dict]):
    run_score = scorer.score_run(
        leg, workspace=workspace, leg_start_baselines=baselines,
        transcript=events, verify_dir=verify_dir,
    )
    actions = scorer.build_action_stream(events)
    metrics = scorer.leg_metrics(
        leg, events=events, actions=actions, workspace=workspace,
        leg_start_baselines=baselines, k=1, origin="discovered",
    )
    return run_score, metrics


def _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics) -> None:
    assert naive_score["fact_used"] is False, naive_score
    assert fact_score["fact_used"] is True, fact_score
    assert naive_metrics["censored"] is True
    assert naive_metrics["turns_to_fact"] is None
    assert fact_metrics["censored"] is False
    assert fact_metrics["turns_to_fact"] is not None


# ---------------------------------------------------------------------------
# cross-scenario structural invariants
# ---------------------------------------------------------------------------


def test_every_leg_declares_a_real_primary_check():
    for slug, scenario in scen.SCENARIOS.items():
        for i, leg in enumerate(scenario.legs, start=1):
            names = [getattr(c, "name") for c in leg.checks]
            assert leg.primary_check in names, (
                f"{slug} leg {i}: primary_check {leg.primary_check!r} not among {names!r}"
            )


def test_note_variant_content_byte_identical_to_fact_sentence():
    """`__post_init__` already enforces this at construction time; this test makes
    the invariant an explicit, independently-checkable assertion (DESIGN.md 11.6)."""
    for slug, scenario in scen.SCENARIOS.items():
        for variant in scenario.note_variants:
            assert scenario.fact_sentence in variant.content, (
                f"{slug}/{variant.variant}: note body drops the byte-identical fact sentence"
            )


def test_forcing_leg_flag_set_only_on_leg_one_of_discovered_scenarios():
    """S1-S4 (discovered) force the mistake via leg 1's task; S5-S6 (told) state the
    fact directly in leg 1's prompt instead and never set `is_forcing_leg` (its own
    docstring: "leg 1 of a DISCOVERED scenario"). Either way, no leg after 1 forces.
    """
    for slug, scenario in scen.SCENARIOS.items():
        if scenario.origin == "discovered":
            assert scenario.legs[0].is_forcing_leg, f"{slug}: leg 1 must be the forcing leg"
        else:
            assert not scenario.legs[0].is_forcing_leg, (
                f"{slug}: a TOLD scenario states the fact directly, no forcing leg"
            )
        for i, leg in enumerate(scenario.legs[1:], start=2):
            assert not leg.is_forcing_leg, f"{slug} leg {i}: only leg 1 may force"


def test_fact_token_confined_to_anchor_files_and_leg1_prompt():
    """No `fact_token` may appear in any scenario file except its designated anchor
    file(s), and in no leg->=2 prompt (DESIGN.md 5.4, 11.6). Leg 1's prompt is exempt:
    S5/S6 (told) state the fact directly there for every arm (shared leg 1).
    """
    for slug, scenario in scen.SCENARIOS.items():
        anchors = set(scenario.anchor_files())
        for rel, content in scenario.files.items():
            if rel in anchors:
                continue
            for token in scenario.fact_tokens:
                assert token not in content, (
                    f"{slug}: fact_token {token!r} leaked into non-anchor file {rel!r}"
                )
        for i, leg in enumerate(scenario.legs[1:], start=2):
            for token in scenario.fact_tokens:
                assert token not in leg.prompt, (
                    f"{slug} leg {i}: fact_token {token!r} leaked into its own prompt"
                )


def test_verify_scripts_never_land_inside_the_workspace(tmp_path):
    """A verifier inside the workspace would leak the answer to the no-memory arm."""
    for slug, scenario in scen.SCENARIOS.items():
        workspace = tmp_path / slug / "workspace"
        verify_dir = tmp_path / slug / "verify"
        scen.materialize(scenario, workspace)
        for leg in scenario.legs:
            scen.materialize_verifiers(leg, verify_dir)
            for name in leg.verify_scripts:
                assert not (workspace / name).exists()
                assert not any(p.name == Path(name).name for p in workspace.rglob("*"))


# ---------------------------------------------------------------------------
# DEFECT 10 (direction 1, user decision 2026-07-30; DESIGN.md 6.5): per-leg reset
# of scenario-declared `critical_residue_paths`.
# ---------------------------------------------------------------------------


def test_critical_residue_paths_entry_must_have_a_matching_files_seed():
    """`LongitudinalScenario.__post_init__` refuses a declared reset path with no
    `files` seed to restore FROM -- `run_leg.py`'s `_apply_critical_residue_reset`
    trusts this invariant instead of re-checking it at run time (KeyError would be
    the alternative, much later and far from the authoring mistake).
    """
    real = scen.SCENARIOS["deploy_reverted_by_reconciler"]
    with pytest.raises(ValueError, match="critical_residue_paths"):
        dataclasses.replace(real, critical_residue_paths=("deploy/queue.yaml", "no/such/file.txt"))


def test_no_scenario_declares_critical_residue_paths_by_accident():
    """S1-S4 each target a distinct value/artifact per leg, so no leg's check can be
    pre-satisfied by an earlier leg's own residue. S5 (`deploy/queue.yaml`) and S6
    (`RESULTS.md`, UPG-EVAL-S6-LEG4-VACUOUS: leg 4 re-measures leg 1's algorithm on
    a deterministic bench box) do need a reset. This is a deliberate allow-list, not
    an oversight -- a future scenario that DOES need one must add itself here.
    """
    declared = {slug: s.critical_residue_paths for slug, s in scen.SCENARIOS.items() if s.critical_residue_paths}
    assert declared == {
        "deploy_reverted_by_reconciler": ("deploy/queue.yaml",),
        "bench_box_only": ("RESULTS.md",),
    }


def test_s5_queue_gained_staging_entry_is_uniform_minimum_one_across_every_leg():
    """Post-fix: every leg's engagement half is a flat minimum=1 (not the old
    cumulative 1/2/3/4) -- coherent only because `deploy/queue.yaml` is reset to
    its zero-staging-entry seed at every k>=2 leg's start (`critical_residue_paths`),
    so ANY staging entry present at leg-end was necessarily added THIS leg.
    """
    s5 = scen.SCENARIOS["deploy_reverted_by_reconciler"]
    for i, leg in enumerate(s5.legs, start=1):
        allof = leg.checks[0]
        sub = next(c for c in allof.of if c.name == "queue_gained_staging_entry")
        assert sub.minimum == 1, f"leg {i}: expected uniform minimum=1, got {sub.minimum}"


def test_s5_check_measures_this_legs_own_addition_not_leg1_residue(tmp_path):
    """Mechanical proof of the check-semantics fix, via the real `evaluate_check`:
    starting from the scenario seed (what every k>=2 leg sees after
    `_apply_critical_residue_reset`), the engagement half fails until THIS leg adds
    its own entry, and passes as soon as it does -- for every leg, not just leg 1.
    Was previously only true for leg 1 (legs 2-4 needed 2/3/4 CUMULATIVE entries,
    so leg 1's leftover alone already satisfied leg 2's old check).
    """
    s5 = scen.SCENARIOS["deploy_reverted_by_reconciler"]
    for i, leg in enumerate(s5.legs, start=1):
        workspace = tmp_path / f"leg{i}"
        baselines = scen.materialize(s5, workspace)
        verify_dir = tmp_path / f"verify{i}"

        # Seed only (post-reset leg-start state, or leg 1's own untouched start):
        # the engagement half must fail -- nothing this leg did yet.
        pre = scorer.evaluate_check(
            leg.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
        )
        engagement_pre = next(s for s in pre["sub_checks"] if s["name"] == "queue_gained_staging_entry")
        assert engagement_pre["passed"] is False, f"leg {i}: seed alone must not satisfy the check"

        # This leg's own compliant addition (append, never edit in place, per the
        # seed file's own header comment) -- must now satisfy it.
        _write(
            workspace, "deploy/queue.yaml",
            s5.files["deploy/queue.yaml"] + '- date: "2026-07-20"\n  target: staging\n  ref: HEAD\n',
        )
        post = scorer.evaluate_check(
            leg.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
        )
        engagement_post = next(s for s in post["sub_checks"] if s["name"] == "queue_gained_staging_entry")
        assert engagement_post["passed"] is True, f"leg {i}: own addition must satisfy the check"


# ---------------------------------------------------------------------------
# DEFECT 12 (Option A, user decision 2026-08-03): S5's hidden fact has a
# descriptive half (the reconciler silently reverts a direct `deploy.sh` run) and
# a prescriptive half (`deploy/queue.yaml` is the sanctioned channel). The
# `deploy/README.md` fixture used to leak the prescriptive half ("queue.yaml is
# consumed by the release bot"), letting a fixture-reading agent derive the
# correct action with no note. It is now mechanical only; the prescriptive half
# lives solely in the note variants and the fact sentence.
# ---------------------------------------------------------------------------


def test_s5_readme_fixture_is_de_prescribed():
    """`deploy/README.md` must name `deploy/queue.yaml`'s fields (what a mechanical
    reader would find) without ever stating who consumes the file or that it is the
    deploy channel -- that claim would let an agent derive the correct action
    (queue.yaml, not deploy.sh) from the workspace alone, with no note, collapsing
    the action contrast the note-variant ladder (DESIGN.md section 7.2) measures.
    """
    s5 = scen.SCENARIOS["deploy_reverted_by_reconciler"]
    readme = s5.files["deploy/README.md"]
    assert readme == "deploy/queue.yaml holds a list of dated entries: date, target, ref, requested_by.\n"
    queue_seed = s5.files["deploy/queue.yaml"]
    for leaked_phrase in ("consumed by", "release bot", "release-bot", "sanctioned", "bot picks it up"):
        assert leaked_phrase not in readme, f"README fixture still leaks {leaked_phrase!r}"
        assert leaked_phrase.lower() not in queue_seed.lower(), (
            f"queue seed still leaks {leaked_phrase!r} -- the prescriptive half must "
            "live only in the note variants and the fact sentence"
        )


def test_s5_queue_seed_bytes_are_pinned():
    """`deploy/queue.yaml`'s seed is declared in `critical_residue_paths` (DEFECT 10,
    DESIGN.md section 6.5) and `run_leg.py` restores it to exactly this string at the
    start of every k>=2 leg. A byte change here -- even whitespace -- would silently
    change the workspace surface mid-trajectory for any recorded S5 trajectory a
    later leg extends (T5's leg-4 extension, T2's reuse of a shared leg 1), so this
    string is pinned byte-for-byte rather than just checked for the fields it
    contains. The seed's former "Consumed by the release bot" first line carried the
    same prescriptive leak as the README; it was de-prescribed at the SECOND DEFECT
    12 boundary, which landed only after the last pre-fix S5 extension had run, and
    the seed is pinned again from that commit on.
    """
    s5 = scen.SCENARIOS["deploy_reverted_by_reconciler"]
    assert s5.files["deploy/queue.yaml"] == (
        "# Entries are appended, never edited in place.\n"
        "- date: \"2026-07-01\"\n"
        "  target: prod-canary\n"
        "  ref: v1.1.0\n"
        "  requested_by: ops-bot\n"
    )


# ---------------------------------------------------------------------------
# UPG-EVAL-S6-LEG4-VACUOUS: S6's leg 4 re-measures leg 1's algorithm on a
# deterministic bench box, so its primary check was satisfied by leg 1's residue
# with zero leg-4 work. Fixed by declaring `RESULTS.md` in the same
# `critical_residue_paths` mechanism S5 uses (DESIGN.md 6.5).
# ---------------------------------------------------------------------------

_S6_RADIX_JSON = '{"algorithm": "radix_sort", "n": 1000000, "ns_op": 182, "source": "remote-box"}\n'
_S6_RADIX_ROW = "| radix_sort | 1000000 | 182 | remote-box |\n"

# The scenario's own deterministic box numbers (`bench/boxrun.sh`), keyed by the
# algorithm each leg's primary check names.
_S6_BOX_NS = {"radix_sort": 182, "merge_sort": 241, "tim_sort": 205}
_S6_LEG_ALGO = ("radix_sort", "merge_sort", "tim_sort", "radix_sort")


def _s6_box_run(workspace: Path, algo: str) -> None:
    """What a compliant `make bench REMOTE=1 ALGO=<algo>` leaves behind: the box's
    result JSON, plus the row the agent pastes into RESULTS.md from it."""
    _write(
        workspace, f"bench/results/remote-{algo}.json",
        f'{{"algorithm": "{algo}", "n": 1000000, "ns_op": {_S6_BOX_NS[algo]}, "source": "remote-box"}}\n',
    )
    results = (workspace / "RESULTS.md").read_text(encoding="utf-8")
    _write(workspace, "RESULTS.md", results + f"| {algo} | 1000000 | {_S6_BOX_NS[algo]} | remote-box |\n")


def test_s6_leg4_check_is_vacuous_against_leg1_residue_and_non_vacuous_after_the_reset(tmp_path):
    """The acceptance case, both directions, through the real `evaluate_check`.

    Leg 4's `VerifyCommand` argv is leg 1's verbatim (`verify_traceability.py
    radix_sort`) and `boxrun.sh` is deterministic, so leg 1's compliant end state
    already exits it 0: an agent that does NOTHING at leg 4 scores a pass. Declaring
    `RESULTS.md` in `critical_residue_paths` -- which `run_leg.py` applies at the
    start of every k>=2 leg -- is what makes the leg 4 verdict its own again.
    """
    s6 = scen.SCENARIOS["bench_box_only"]
    leg4 = s6.legs[3]
    workspace = tmp_path / "ws"
    baselines = scen.materialize(s6, workspace)
    verify_dir = tmp_path / "verify"
    scen.materialize_verifiers(leg4, verify_dir)

    # Leg 1's compliant end state (the residue leg 4 inherits without a reset).
    _write(workspace, "bench/results/remote-radix_sort.json", _S6_RADIX_JSON)
    _write(workspace, "RESULTS.md", s6.files["RESULTS.md"] + _S6_RADIX_ROW)

    unreset = scorer.evaluate_check(
        leg4.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
    )
    assert unreset["passed"] is True, (
        "fixture no longer reproduces the defect: leg 1's residue must be what made "
        "leg 4's check vacuous"
    )

    # The declared reset, applied exactly as `_apply_critical_residue_reset` does:
    # restore the declared path to its `files` seed, nothing else.
    assert s6.critical_residue_paths == ("RESULTS.md",)
    for path in s6.critical_residue_paths:
        _write(workspace, path, s6.files[path])

    reset = scorer.evaluate_check(
        leg4.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
    )
    assert reset["passed"] is False, "post-reset, a leg 4 that does nothing must not pass"

    # ...and leg 4's own compliant box run passes it again.
    _s6_box_run(workspace, "radix_sort")
    own_work = scorer.evaluate_check(
        leg4.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
    )
    assert own_work["passed"] is True, "leg 4's own bench-box row must satisfy its check"


def test_s6_every_leg_check_measures_its_own_row_from_the_reset_seed(tmp_path):
    """Blast-radius guard for the reset: legs 2 and 3 now also start from the seed
    table, so this pins that each leg's primary check still fails on the seed alone
    and passes on that leg's OWN compliant box row -- the reset never makes a
    compliant leg unpassable (the two pre-existing seed rows stay traceable via the
    `remote-quick_sort.json`/`remote-bubble_sort.json` seeds).
    """
    s6 = scen.SCENARIOS["bench_box_only"]
    for i, (leg, algo) in enumerate(zip(s6.legs, _S6_LEG_ALGO), start=1):
        workspace = tmp_path / f"leg{i}"
        baselines = scen.materialize(s6, workspace)
        verify_dir = tmp_path / f"verify{i}"
        scen.materialize_verifiers(leg, verify_dir)

        pre = scorer.evaluate_check(
            leg.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
        )
        assert pre["passed"] is False, f"leg {i}: the reset seed alone must not satisfy the check"

        _s6_box_run(workspace, algo)
        post = scorer.evaluate_check(
            leg.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
        )
        assert post["passed"] is True, f"leg {i}: this leg's own bench-box row must satisfy the check"


def test_s6_laptop_number_still_fails_the_traceability_check(tmp_path):
    """Discrimination, unchanged by the reset: a plain local `make bench` number
    pasted into RESULTS.md has no `bench/results/remote-*.json` to trace to."""
    s6 = scen.SCENARIOS["bench_box_only"]
    leg4 = s6.legs[3]
    workspace = tmp_path / "ws"
    baselines = scen.materialize(s6, workspace)
    verify_dir = tmp_path / "verify"
    scen.materialize_verifiers(leg4, verify_dir)

    _write(workspace, "RESULTS.md", s6.files["RESULTS.md"] + "| radix_sort | 1000000 | 1470 | remote-box |\n")
    verdict = scorer.evaluate_check(
        leg4.checks[0], workspace=workspace, baselines=baselines, commands=(), verify_dir=verify_dir,
    )
    assert verdict["passed"] is False
    assert "untraceable" in verdict["detail"]


def _declared_check(checks, name: str):
    """The declared check object called `name`, searched recursively through any
    `AllOf.of` -- the object-level mirror of `scorer._find_check_by_name`, which
    does the same search over already-evaluated result dicts."""
    for check in checks:
        if getattr(check, "name", None) == name:
            return check
        found = _declared_check(getattr(check, "of", ()), name)
        if found is not None:
            return found
    return None


def _check_shape(value):
    """Structural identity of a declared check, ignoring its `name` (which carries
    a per-leg suffix like `_leg1` and so hides otherwise identical checks)."""
    if dataclasses.is_dataclass(value):
        return (type(value).__name__,) + tuple(
            _check_shape(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.name != "name"
        )
    if isinstance(value, (list, tuple)):
        return tuple(_check_shape(v) for v in value)
    return value


def test_a_leg_check_repeated_verbatim_by_a_later_leg_requires_a_residue_reset():
    """Drift guard, mechanical and scenario-agnostic: two legs whose primary checks
    are structurally identical (same primitive, same parameters -- only the leg
    suffix in `name` differs) ask the workspace the SAME question, so the later
    leg's verdict is pre-satisfiable by the earlier leg's residue unless the
    scenario resets the artifact that answers it. Catches a future leg re-targeting
    an earlier leg's value (how UPG-EVAL-S6-LEG4-VACUOUS arose) without waiting for
    a paid run to produce an always-passing leg.
    """
    for slug, scenario in scen.SCENARIOS.items():
        shapes: dict[object, int] = {}
        for i, leg in enumerate(scenario.legs, start=1):
            primary = _declared_check(leg.checks, leg.primary_check)
            assert primary is not None, f"{slug}: leg {i} names a primary_check it does not declare"
            shape = _check_shape(primary)
            if shape in shapes:
                assert scenario.critical_residue_paths, (
                    f"{slug}: leg {i}'s primary check is identical to leg "
                    f"{shapes[shape]}'s, so leg {i} can pass on leg {shapes[shape]}'s "
                    f"residue alone -- declare the artifact it reads in "
                    f"critical_residue_paths (DESIGN.md 6.5)"
                )
            shapes.setdefault(shape, i)


def _is_path_identifier_char(ch: str) -> bool:
    """Mirrors `agent.working_context_store._store._is_path_identifier_char`
    conservatively: a char that extends a filename/identifier token."""
    return ch.isalnum() or ch in ("_", "-")


def _path_boundary_match(text: str, needle: str) -> bool:
    """Mirrors `agent.working_context_store._store._path_boundary_match`
    conservatively -- True if `needle` occurs in `text` at a genuine path
    boundary, not merely as a substring of a longer identifier (e.g.
    "gate.py" must not false-match inside "uv_regate.py")."""
    if not needle:
        return False
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not _is_path_identifier_char(text[idx - 1])
        after_idx = idx + len(needle)
        after_ok = after_idx >= len(text) or not _is_path_identifier_char(text[after_idx])
        if before_ok and after_ok:
            return True
        start = idx + 1


def _probe_file_structurally_reaches(probe_file: str, variant: "scen.NoteVariant") -> bool:
    """True if `probe_file` would surface `variant` through the daemon's real
    `recall_for_path()` rule: a path-boundary match of the probe file's
    basename or full (workspace-relative) path in the note's content, or an
    exact declared `anchors` entry -- see `NoteVariant`'s own docstring
    invariant. `recall_for_path()` never consults `trigger_paths`
    (UPG-TRIGGERS-INERT-ON-PROXY-STRUCTURAL), so `trigger_paths` is
    deliberately not consulted here either."""
    if probe_file in variant.anchors:
        return True
    basename = probe_file.rsplit("/", 1)[-1]
    return _path_boundary_match(variant.content, basename) or _path_boundary_match(
        variant.content, probe_file
    )


def test_every_leg_probe_file_structurally_reaches_every_note_variant():
    """Regression guard for the T0 probe-reachability class of authoring bug
    (`run_leg.py`'s `_proactive_probe` turn 2 POSTs a leg's `probe_files` to
    `/v1/proactive`, whose structural channel surfaces a note only via a path
    mentioned in the note TEXT or a declared `anchors` entry, never via
    `trigger_paths`): for every scenario, every leg, every note variant, at
    least one of that leg's `probe_files` must structurally reach that
    variant, or `run_plan.py --tier T0` ABORTs at that leg regardless of the
    note's semantic content. Caught release_via_ci legs 2-4 (probe_files
    named no path the fact sentence mentioned) and bench_box_only (probe_files
    named only boxrun.sh, which the fact sentence never mentions) before this
    test existed.
    """
    for slug, scenario in scen.SCENARIOS.items():
        for i, leg in enumerate(scenario.legs, start=1):
            for variant in scenario.note_variants:
                assert any(
                    _probe_file_structurally_reaches(pf, variant) for pf in leg.probe_files
                ), (
                    f"{slug} leg {i}/{variant.variant}: none of {leg.probe_files!r} is "
                    f"mentioned (path-boundary) or anchored in the note content -- "
                    f"structurally unreachable, T0 would ABORT here"
                )


# ---------------------------------------------------------------------------
# arm-blindness of the outcome verdict (DESIGN.md 6.1/6.6)
# ---------------------------------------------------------------------------


def test_score_run_and_leg_metrics_take_no_arm_argument():
    assert "arm" not in inspect.signature(scorer.score_run).parameters
    assert "arm" not in inspect.signature(scorer.leg_metrics).parameters
    # `leg_non_vacuity` is deliberately the ONE place arm identity is consulted.
    assert "arm" in inspect.signature(scorer.leg_non_vacuity).parameters


# ---------------------------------------------------------------------------
# S1 release_via_ci -- discovered, corroborable
# ---------------------------------------------------------------------------


def test_release_via_ci_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "release_via_ci", variant="naive"
    )
    naive_events = _transcript(
        [
            ("Bash", {"command": "python -m build"}),
            ("Bash", {"command": "twine upload dist/*"}),
        ]
    )
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    fact_scenario, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "release_via_ci", variant="fact_using"
    )
    _write(fact_ws, "pyproject.toml", fact_scenario.files["pyproject.toml"].replace("1.3.2", "1.4.0"))
    _write(
        fact_ws, "src/ledgerkit/__init__.py",
        fact_scenario.files["src/ledgerkit/__init__.py"].replace("1.3.2", "1.4.0"),
    )
    _write(
        fact_ws, "CHANGELOG.md",
        fact_scenario.files["CHANGELOG.md"].replace("## Unreleased", "## 1.4.0"),
    )
    fact_events = _transcript(
        [
            ("Bash", {"command": "git tag -a v1.4.0 -m 'release 1.4.0'"}),
            ("Bash", {"command": "git push origin v1.4.0 --tags"}),
        ]
    )
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# S2 spec_lives_outside -- discovered, corroborable
# ---------------------------------------------------------------------------


def test_spec_lives_outside_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "spec_lives_outside", variant="naive"
    )
    _write(
        naive_ws, "orbit/docs/spec.md",
        naive_scenario.files["orbit/docs/spec.md"] + "\n## Retry policy\n\nExponential backoff.\n",
    )
    naive_events = _transcript(
        [
            (
                "Edit",
                {
                    "file_path": "orbit/docs/spec.md",
                    "old_string": "Rate limits",
                    "new_string": "Rate limits\n\n## Retry policy\n\nExponential backoff.",
                },
            ),
        ]
    )
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    fact_scenario, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "spec_lives_outside", variant="fact_using"
    )
    _write(
        fact_ws, "orbit-docs/spec.md",
        fact_scenario.files["orbit-docs/spec.md"]
        + "\n## Retry policy\n\nRetries use exponential backoff (see src/orbit/limits.py).\n",
    )
    fact_events = _transcript(
        [
            (
                "Edit",
                {
                    "file_path": "orbit-docs/spec.md",
                    "old_string": "Rate limits",
                    "new_string": "Rate limits\n\n## Retry policy\n\nExponential backoff.",
                },
            ),
        ]
    )
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# S3 runner_not_pytest -- discovered, corroborable
# ---------------------------------------------------------------------------


def test_runner_not_pytest_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "runner_not_pytest", variant="naive"
    )
    _write(
        naive_ws, "pytest.ini",
        naive_scenario.files["pytest.ini"].replace("addopts = -q", "addopts = -q\nnorecursedirs = legacy"),
    )
    naive_events = _transcript(
        [
            (
                "Edit",
                {
                    "file_path": "pytest.ini",
                    "old_string": "addopts = -q",
                    "new_string": "addopts = -q\nnorecursedirs = legacy",
                },
            ),
            ("Bash", {"command": "pytest -q"}),
        ]
    )
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    _, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "runner_not_pytest", variant="fact_using"
    )
    _write(
        fact_ws, "tests/unit/test_rows.py",
        "from feldspar.rows import normalize_row\n\n\n"
        "def test_normalize_row():\n"
        "    assert normalize_row({'a': ' x '}) == {'a': 'x'}\n",
    )
    fact_events = _transcript(
        [
            ("Write", {"file_path": "tests/unit/test_rows.py", "content": "..."}),
            ("Bash", {"command": "./tools/t"}),
        ]
    )
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# S4 secrets_not_dotenv -- discovered, corroborable
# ---------------------------------------------------------------------------


def test_secrets_not_dotenv_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "secrets_not_dotenv", variant="naive"
    )
    _write(naive_ws, ".env", naive_scenario.files[".env"] + "ACME_WEBHOOK_SECRET=abc123\n")
    naive_events = _transcript(
        [
            (
                "Edit",
                {
                    "file_path": ".env",
                    "old_string": "ACME_API_KEY=REPLACE_ME_LOCAL_ONLY",
                    "new_string": "ACME_API_KEY=REPLACE_ME_LOCAL_ONLY\nACME_WEBHOOK_SECRET=abc123",
                },
            ),
        ]
    )
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    _, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "secrets_not_dotenv", variant="fact_using"
    )
    _write(
        fact_ws, "vault/secrets.json",
        '{\n  "ACME_WEBHOOK_SECRET": "wh-value-123",\n  "SIGNING_KEY": "s3cr3t-baseline-value"\n}\n',
    )
    fact_events = _transcript(
        [("Bash", {"command": "scripts/envctl add ACME_WEBHOOK_SECRET wh-value-123"})]
    )
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# S5 deploy_reverted_by_reconciler -- told, uncorroborable
#
# Leg 1's prompt states the fact directly for every arm (shared leg 1), so leg 2
# is used here: an agent must draw on what was established, not read it fresh.
# ---------------------------------------------------------------------------


def test_deploy_reverted_by_reconciler_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "deploy_reverted_by_reconciler", variant="naive", leg_index=1
    )
    _write(naive_ws, ".deploy-state", "2026-07-06T00:00:00Z target=staging\n")
    naive_events = _transcript([("Bash", {"command": "./deploy.sh staging"})])
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    fact_scenario, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "deploy_reverted_by_reconciler", variant="fact_using", leg_index=1
    )
    extra = (
        '- date: "2026-07-05"\n  target: staging\n  ref: v1.1.1\n  requested_by: harness\n'
        '- date: "2026-07-06"\n  target: staging\n  ref: v1.1.2\n  requested_by: harness\n'
    )
    _write(fact_ws, "deploy/queue.yaml", fact_scenario.files["deploy/queue.yaml"] + extra)
    fact_events = _transcript(
        [
            (
                "Edit",
                {
                    "file_path": "deploy/queue.yaml",
                    "old_string": "requested_by: ops-bot",
                    "new_string": "requested_by: ops-bot\n" + extra,
                },
            ),
        ]
    )
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# S6 bench_box_only -- told, uncorroborable
#
# Leg 1's prompt states the fact directly for every arm (shared leg 1); leg 2 is
# used here for the same reason as S5. This scenario's primary check is a real
# `VerifyCommand` (DESIGN.md's own S6 gap noted in scenarios.py: `mistake_signature`
# is an approximation, but `fact_used`/primary_check is exact and IS exercised here
# via a real subprocess run of the scenario's own verify_traceability.py).
# ---------------------------------------------------------------------------


def test_bench_box_only_discriminates(tmp_path):
    naive_scenario, leg, naive_ws, naive_verify, naive_baselines = _setup(
        tmp_path, "bench_box_only", variant="naive", leg_index=1
    )
    naive_events = _transcript([("Bash", {"command": "make bench ALGO=merge_sort"})])
    naive_score, naive_metrics = _score(leg, naive_ws, naive_baselines, naive_verify, naive_events)

    fact_scenario, _, fact_ws, fact_verify, fact_baselines = _setup(
        tmp_path, "bench_box_only", variant="fact_using", leg_index=1
    )
    _write(
        fact_ws, "bench/results/remote-merge_sort.json",
        '{"algorithm": "merge_sort", "n": 1000000, "ns_op": 241, "source": "remote-box"}\n',
    )
    _write(
        fact_ws, "RESULTS.md",
        fact_scenario.files["RESULTS.md"] + "| merge_sort | 1000000 | 241 | remote-box |\n",
    )
    fact_events = _transcript([("Bash", {"command": "make bench REMOTE=1 ALGO=merge_sort"})])
    fact_score, fact_metrics = _score(leg, fact_ws, fact_baselines, fact_verify, fact_events)

    _assert_discriminates(naive_score, naive_metrics, fact_score, fact_metrics)
    assert naive_metrics["mistake_committed"] is True
    assert fact_metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# censoring: never imputed from session totals (DESIGN.md 6.3, 11.6)
# ---------------------------------------------------------------------------


def test_censoring_never_imputes_from_session_totals(tmp_path):
    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "release_via_ci", variant="censored"
    )
    events = _transcript([])  # no tool_use at all -- nothing can match
    actions = scorer.build_action_stream(events)
    metrics = scorer.leg_metrics(
        leg, events=events, actions=actions, workspace=workspace,
        leg_start_baselines=baselines, k=1, origin=scenario.origin,
        session_usd=12.34, billable_tokens_session=500.0,
    )
    assert metrics["censored"] is True
    assert metrics["censor_reason"] == "fact never acquired"
    for key in (
        "turns_to_fact", "tool_calls_to_fact", "output_tokens_to_fact",
        "billable_tokens_to_fact", "context_tokens_at_fact",
        "usd_to_fact_alloc", "usd_to_fact_basis",
    ):
        assert metrics[key] is None, f"{key} was imputed on a censored leg: {metrics[key]!r}"


# ---------------------------------------------------------------------------
# a missing transcript degrades to "not acquired", never raises (DESIGN.md 11.6)
# ---------------------------------------------------------------------------


def test_missing_transcript_degrades_to_not_acquired(tmp_path):
    missing = tmp_path / "does-not-exist" / "transcript.jsonl"
    events = scorer.load_transcript(missing)
    assert events == []
    actions = scorer.build_action_stream(events)
    assert actions == []

    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "release_via_ci", variant="missing"
    )
    metrics = scorer.leg_metrics(
        leg, events=events, actions=actions, workspace=workspace,
        leg_start_baselines=baselines, k=1, origin=scenario.origin,
    )
    assert metrics["censored"] is True
    assert metrics["acquisition_action_index"] is None

    run_score = scorer.score_run(
        leg, workspace=workspace, leg_start_baselines=baselines,
        transcript=events, verify_dir=verify_dir,
    )
    assert run_score["fact_used"] is False


# ---------------------------------------------------------------------------
# exact anchor matching: note:4 is never satisfied by note:41 (DESIGN.md 11.6)
# ---------------------------------------------------------------------------


def test_leg_non_vacuity_exact_anchor_match_not_prefix(tmp_path):
    audit_log = tmp_path / "audit.log"
    events = _init_and_result_events(mcp_servers=[])

    audit_log.write_text(
        "2026-07-29T12:00:00Z PROACTIVE_INJECT anchors=note:41 count=1\n", encoding="utf-8"
    )
    result = scorer.leg_non_vacuity(
        arm="proxy", k=1, events=events, notes_count_at_start=3,
        audit_log=audit_log, proxy_injected=1, planted_anchor="note:4",
    )
    assert result["non_vacuity"]["planted_anchor_injections"] == 0
    assert result["valid"] is False
    assert "planted anchor" in result["invalid_reason"]

    audit_log.write_text(
        "2026-07-29T12:00:01Z PROACTIVE_INJECT anchors=note:4 count=1\n", encoding="utf-8"
    )
    result2 = scorer.leg_non_vacuity(
        arm="proxy", k=1, events=events, notes_count_at_start=3,
        audit_log=audit_log, proxy_injected=1, planted_anchor="note:4",
    )
    assert result2["non_vacuity"]["planted_anchor_injections"] == 1
    assert result2["valid"] is True


# ---------------------------------------------------------------------------
# mcp-bare guard: held to the same connected-server bar as mcp, not the
# empty-servers bar every other tool-less arm gets (fix landed in scorer.py's
# _EXPECTED_MCP_SERVERS; this pins it against regressing).
# ---------------------------------------------------------------------------


def test_leg_non_vacuity_mcp_bare_accepts_connected_vectr_server():
    events = _init_and_result_events(
        mcp_servers=[{"name": "vectr", "status": "connected"}],
        tools=["Bash", "mcp__vectr__search", "mcp__vectr__recall"],
    )
    result = scorer.leg_non_vacuity(
        arm="mcp-bare", k=1, events=events, notes_count_at_start=3,
        proxy_injected=0, recall_probe_returned_note=True,
    )
    assert result["valid"] is True, result["invalid_reason"]
    assert result["non_vacuity"]["vectr_tools_in_init"] is True


# ---------------------------------------------------------------------------
# DEFECT 8: hook-sessionstart's D1 transcript-content check (leg_non_vacuity's
# `arm == "hook-sessionstart"` branch) reads a raw `--output-format stream-json`
# transcript file. The planted note's content, once delivered through a
# SessionStart hook's `hookSpecificOutput.additionalContext` field
# (`agent/hook_cli.py::_emit_hook_context` -> `print(json.dumps({...}))`), is
# JSON-string-escaped in that raw text: a real internal newline in a multi-line
# NoteVariant (e.g. `release_via_ci`'s "verifiable" trail, which genuinely
# contains one -- see `scenarios.py`) becomes the two literal characters `\n`,
# which is not whitespace and survives untouched by a plain
# whitespace-collapse. A literal OR whitespace-collapsed-only `in` check
# false-negatives here even though the hook genuinely fired; this pins
# `scorer.py`'s `_content_delivered_in_json_text` (mirrors `run_leg.py`'s
# helper of the same name, duplicated per this file's no-cross-import
# convention) against that exact shape, plus the genuinely-absent case.
# ---------------------------------------------------------------------------


def _release_via_ci_verifiable_content() -> str:
    scenario = scen.get("release_via_ci")
    variant = next(v for v in scenario.note_variants if v.variant == "verifiable")
    assert "\n" in variant.content, "fixture sanity: this variant's content must be multi-line"
    return variant.content


def _stream_json_transcript_line(content: str) -> str:
    """One `hookSpecificOutput` JSON line shaped exactly like
    `agent/hook_cli.py::_emit_hook_context`'s stdout, as it would appear
    embedded in a `--output-format stream-json` transcript."""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": f"# Triggered Memory (1 fired)\n\n[7] [DIRECTIVE]\n  {content}",
            }
        }
    )


def test_leg_non_vacuity_hook_sessionstart_recognizes_multiline_content_json_escaped_in_transcript(tmp_path):
    content = _release_via_ci_verifiable_content()
    assert content not in _stream_json_transcript_line(content), (
        "fixture sanity: the literal content must NOT appear unescaped in the "
        "raw JSON line -- otherwise this test doesn't exercise the escaping bug"
    )

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(_stream_json_transcript_line(content) + "\n", encoding="utf-8")

    events = _init_and_result_events(mcp_servers=[])  # hook-sessionstart expects []
    result = scorer.leg_non_vacuity(
        arm="hook-sessionstart", k=1, events=events, notes_count_at_start=1,
        hook_injection_counts={"SessionStart": 1},
        transcript_path=transcript_path,
        planted_note_content=content,
    )
    assert result["valid"] is True, result["invalid_reason"]
    assert result["non_vacuity"]["planted_content_in_transcript"] is True


def test_leg_non_vacuity_hook_sessionstart_content_genuinely_absent_still_invalid(tmp_path):
    content = _release_via_ci_verifiable_content()

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        _stream_json_transcript_line("totally unrelated note content") + "\n", encoding="utf-8"
    )

    events = _init_and_result_events(mcp_servers=[])
    result = scorer.leg_non_vacuity(
        arm="hook-sessionstart", k=1, events=events, notes_count_at_start=1,
        hook_injection_counts={"SessionStart": 1},
        transcript_path=transcript_path,
        planted_note_content=content,
    )
    assert result["valid"] is False
    assert result["non_vacuity"]["planted_content_in_transcript"] is False
    assert "not found in transcript" in result["invalid_reason"]


# ---------------------------------------------------------------------------
# weak_prior: the leg-level signal report.py's mistake_repetition_rate
# aggregation (DESIGN.md 6.5, not yet implemented) will read
# ---------------------------------------------------------------------------


def test_weak_prior_flags_a_clean_leg1_on_a_discovered_scenario(tmp_path):
    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "release_via_ci", variant="weak-prior"
    )
    assert scenario.origin == "discovered"

    clean_events = _transcript(
        [
            ("Bash", {"command": "git tag -a v1.4.0 -m 'release'"}),
            ("Bash", {"command": "git push origin v1.4.0 --tags"}),
        ]
    )
    clean_actions = scorer.build_action_stream(clean_events)
    clean_metrics = scorer.leg_metrics(
        leg, events=clean_events, actions=clean_actions, workspace=workspace,
        leg_start_baselines=baselines, k=1, origin=scenario.origin,
    )
    assert clean_metrics["mistake_committed"] is False
    assert clean_metrics["weak_prior"] is True

    erring_events = _transcript([("Bash", {"command": "twine upload dist/*"})])
    erring_actions = scorer.build_action_stream(erring_events)
    erring_metrics = scorer.leg_metrics(
        leg, events=erring_events, actions=erring_actions, workspace=workspace,
        leg_start_baselines=baselines, k=1, origin=scenario.origin,
    )
    assert erring_metrics["mistake_committed"] is True
    assert erring_metrics["weak_prior"] is False


# ---------------------------------------------------------------------------
# session_errored: an arm-agnostic, content-free prerequisite BELOW the per-arm
# premise -- did the agent session run at all -- catching a CLI shape observed on
# a live run: `result.subtype == "success"` with `is_error: true` and zero output
# tokens (a session that errored out before producing any response, still tagged
# "success" by the transcript's own `result` event). See run_leg.py's
# `_abort_if_session_errored` for how this gate turns into a nonzero process exit.
# ---------------------------------------------------------------------------


def test_leg_non_vacuity_flags_session_errored_even_with_subtype_success():
    events = _init_and_result_events(mcp_servers=[])  # arm "none" expects []
    result = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=1, is_error=True, output_tokens=0,
    )
    assert result["valid"] is False
    assert result["non_vacuity"]["session_errored"] is True
    assert "agent session errored" in result["invalid_reason"]


def test_leg_non_vacuity_session_errored_fires_on_each_signal_independently():
    events = _init_and_result_events(mcp_servers=[])

    # is_error alone, everything else clean.
    r1 = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=0, is_error=True, output_tokens=50,
    )
    assert r1["non_vacuity"]["session_errored"] is True

    # nonzero agent_returncode alone.
    r2 = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=1, is_error=False, output_tokens=50,
    )
    assert r2["non_vacuity"]["session_errored"] is True

    # output_tokens == 0 alone (strict equality -- a present result event
    # explicitly reporting zero, not a missing one).
    r3 = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=0, is_error=False, output_tokens=0,
    )
    assert r3["non_vacuity"]["session_errored"] is True


def test_leg_non_vacuity_session_errored_absent_on_a_clean_session():
    events = _init_and_result_events(mcp_servers=[])
    result = scorer.leg_non_vacuity(
        arm="none", k=1, events=events, notes_count_at_start=0,
        agent_returncode=0, is_error=False, output_tokens=50,
    )
    assert result["non_vacuity"]["session_errored"] is False
    assert result["valid"] is True, result["invalid_reason"]


# ---------------------------------------------------------------------------
# DEFECT 9: exec-position anchoring + the contradiction guard.
#
# `re.search` finds a BashAction/CommandRan pattern ANYWHERE in a command string, so
# a purely READ-ONLY command that merely mentions the tracked script (`cat
# deploy.sh`, `grep -n foo deploy.sh`) scored identically to an actual execution of
# it. `_matches_at_exec_position` restricts a matching pattern to genuine
# command-execution positions (string start, or immediately after a `; && || | &`
# separator or newline, through any interpreter/exec-wrapper prefix). These tests
# exercise it directly at the primitive level (`_matches_at_exec_position`) and
# through the full `score_run`/S5 `mistake_signature` pipeline, then separately
# cover `detect_contradictions`/`_find_check_by_name`.
# ---------------------------------------------------------------------------

_DEPLOY_PATTERN = r"(\./)?deploy\.sh\b"


def test_matches_at_exec_position_rejects_read_only_mentions():
    for command in [
        "cat deploy.sh",
        "echo x; cat deploy.sh",
        "stat deploy.sh",
        "grep -n foo deploy.sh",
        "less deploy.sh",
        "echo deploy.sh",
    ]:
        assert scorer._matches_at_exec_position(_DEPLOY_PATTERN, command) is False, command


def test_matches_at_exec_position_accepts_genuine_executions():
    for command in [
        "./deploy.sh staging",
        "bash deploy.sh",
        "cd x && ./deploy.sh",
        "sh deploy.sh; echo done",
        "deploy.sh staging",
        "echo x || ./deploy.sh",
        "echo x | ./deploy.sh",
        "echo x\n./deploy.sh",
        "env FOO=bar ./deploy.sh",
        "timeout 30 ./deploy.sh",
        "FOO=bar BAZ=qux ./deploy.sh staging",
    ]:
        assert scorer._matches_at_exec_position(_DEPLOY_PATTERN, command) is True, command


def test_matches_at_exec_position_git_tag_rejects_read_only_mentions():
    git_tag_pattern = r"git\s+tag\b"
    for command in [
        "cat .github/workflows/release.yml  # mentions git tag in a comment",
        "grep -rn 'git tag' README.md",
        "echo 'run: git tag -a vX.Y.Z' >> notes.txt",
    ]:
        assert scorer._matches_at_exec_position(git_tag_pattern, command) is False, command


def test_matches_at_exec_position_git_tag_accepts_genuine_executions():
    git_tag_pattern = r"git\s+tag\b"
    for command in [
        "git tag -a v1.4.0 -m 'release 1.4.0'",
        "cd repo && git tag v1.4.0",
        "echo start; git tag -a v1.4.0",
    ]:
        assert scorer._matches_at_exec_position(git_tag_pattern, command) is True, command


def test_deploy_reverted_by_reconciler_read_only_mentions_do_not_count_as_mistake(tmp_path):
    """The full pipeline version of `test_matches_at_exec_position_rejects_read_only_
    mentions`: `cat deploy.sh` must not flip `mistake_committed` even though the
    scenario's `mistake_signature`/`no_direct_deploy_script` both target it.
    """
    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "deploy_reverted_by_reconciler", variant="read_only", leg_index=1
    )
    events = _transcript([("Bash", {"command": "cat deploy.sh"})])
    run_score, metrics = _score(leg, workspace, baselines, verify_dir, events)
    assert metrics["mistake_committed"] is False
    no_direct = scorer._find_check_by_name(run_score["checks"], "no_direct_deploy_script")
    assert no_direct is not None
    assert no_direct["passed"] is True


def test_release_via_ci_read_only_mention_of_twine_does_not_count_as_mistake(tmp_path):
    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "release_via_ci", variant="read_only"
    )
    events = _transcript([("Bash", {"command": "grep -n twine RELEASING.md"})])
    _, metrics = _score(leg, workspace, baselines, verify_dir, events)
    assert metrics["mistake_committed"] is False


# ---------------------------------------------------------------------------
# detect_contradictions / _find_check_by_name
# ---------------------------------------------------------------------------


def _s5_leg2():
    return scen.SCENARIOS["deploy_reverted_by_reconciler"].legs[1]


def test_find_check_by_name_recurses_through_allof_sub_checks():
    checks = [
        {"name": "top_level", "passed": True},
        {
            "name": "primary",
            "passed": True,
            "sub_checks": [
                {"name": "nested_a", "passed": False},
                {"name": "deploy_state_untouched", "passed": True, "detail": "sha256 unchanged"},
            ],
        },
    ]
    found = scorer._find_check_by_name(checks, "deploy_state_untouched")
    assert found is not None
    assert found["detail"] == "sha256 unchanged"
    assert scorer._find_check_by_name(checks, "does_not_exist") is None


def test_detect_contradictions_fires_when_action_signature_and_state_check_disagree():
    leg = _s5_leg2()
    checks = [
        {
            "name": "primary",
            "passed": False,
            "sub_checks": [
                {"name": "deploy_state_untouched", "passed": True, "detail": "sha256 unchanged"},
            ],
        }
    ]
    metrics = {"mistake_committed": True, "mistake_source": "action", "first_mistake_action_index": 3}
    contradictions = scorer.detect_contradictions(leg, checks=checks, metrics=metrics)
    assert len(contradictions) == 1
    assert contradictions[0]["kind"] == "mistake_action_without_state_mutation"
    assert contradictions[0]["mistake_state_check"] == "deploy_state_untouched"


def test_detect_contradictions_silent_when_mistake_not_committed():
    leg = _s5_leg2()
    checks = [
        {
            "name": "primary", "passed": True,
            "sub_checks": [{"name": "deploy_state_untouched", "passed": True}],
        }
    ]
    metrics = {"mistake_committed": False, "mistake_source": None}
    assert scorer.detect_contradictions(leg, checks=checks, metrics=metrics) == []


def test_detect_contradictions_silent_when_state_check_also_failed():
    """Both signals agree the mistake happened -- no contradiction to report."""
    leg = _s5_leg2()
    checks = [
        {
            "name": "primary", "passed": False,
            "sub_checks": [{"name": "deploy_state_untouched", "passed": False}],
        }
    ]
    metrics = {"mistake_committed": True, "mistake_source": "action", "first_mistake_action_index": 1}
    assert scorer.detect_contradictions(leg, checks=checks, metrics=metrics) == []


def test_detect_contradictions_silent_when_source_is_file_state_not_action():
    """A mistake sourced from FileMutated evidence IS the state signal -- it cannot
    contradict itself; only an action-only signature paired with a passing state
    check is a genuine disagreement.
    """
    leg = _s5_leg2()
    checks = [
        {
            "name": "primary", "passed": True,
            "sub_checks": [{"name": "deploy_state_untouched", "passed": True}],
        }
    ]
    metrics = {"mistake_committed": True, "mistake_source": "file_state"}
    assert scorer.detect_contradictions(leg, checks=checks, metrics=metrics) == []


def test_detect_contradictions_silent_when_leg_declares_no_state_check():
    """S1's `LegSpec`s have `mistake_state_check=None` -- no independent file-state
    signal exists to contradict the action signature, so the guard is a no-op.
    """
    leg = scen.SCENARIOS["release_via_ci"].legs[0]
    assert leg.mistake_state_check is None
    checks = [{"name": "no_local_upload", "passed": True}]
    metrics = {"mistake_committed": True, "mistake_source": "action", "first_mistake_action_index": 0}
    assert scorer.detect_contradictions(leg, checks=checks, metrics=metrics) == []


def test_deploy_reverted_by_reconciler_live_specimen_produces_no_contradiction_post_fix(tmp_path):
    """DEFECT 9's own live evidence (DESIGN.md): every deploy leg in the campaign
    scored `mistake_committed=true` while `deploy_state_untouched=PASS`, with the
    scorer never flagging the contradiction it was itself producing. Post-fix, the
    anchored `mistake_signature` no longer fires on a read-only mention, so
    `mistake_committed` is correctly False and there is nothing left to contradict --
    the guard exists for defense-in-depth, not because this specimen still needs it.
    """
    scenario, leg, workspace, verify_dir, baselines = _setup(
        tmp_path, "deploy_reverted_by_reconciler", variant="specimen", leg_index=1
    )
    events = _transcript([("Bash", {"command": "cat deploy.sh"})])
    run_score, metrics = _score(leg, workspace, baselines, verify_dir, events)
    assert metrics["mistake_committed"] is False
    contradictions = scorer.detect_contradictions(leg, checks=run_score["checks"], metrics=metrics)
    assert contradictions == []
