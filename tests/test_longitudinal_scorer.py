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

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

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
