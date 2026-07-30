"""DESIGN.md 13 T0-7: scenario-fixture lint, and DESIGN.md 3/9's own
structural invariants over the four registered EVAL-ANTI-MEMORY scenarios.

All checks here are over FIXTURE CONTENT authored once at scenario-design
time (file bytes, prompt text, verify-script paths) -- never a runtime
query-conditional heuristic; nothing here reads or reroutes on a caller's
live query (see `scenarios.py`'s own module docstring and
`lint_scenario`'s docstring, both make the same point).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "anti_memory"
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

ALL_SLUGS = ("retired_flag_api", "inverted_default", "noop_make_target", "handwritten_notes")


def test_all_four_scenarios_are_registered():
    assert set(scen.SCENARIOS) == set(ALL_SLUGS)
    for slug in ALL_SLUGS:
        assert scen.get(slug).slug == slug


def test_lint_scenario_reports_no_violations_for_any_registered_scenario():
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        violations = scen.lint_scenario(scenario)
        assert violations == [], f"{slug}: {violations}"


def test_get_unknown_slug_raises_system_exit():
    import pytest

    with pytest.raises(SystemExit):
        scen.get("does-not-exist")


def test_verify_scripts_never_collide_with_workspace_file_paths():
    """T0-7's own collision check, restated directly (not just via
    lint_scenario, in case that check's own logic regresses silently): a
    verify script must never land on a path the workspace itself owns, or a
    scenario materialized then verified in the same directory would
    overwrite/re-read the wrong file."""
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        collisions = set(scenario.verify_scripts) & set(scenario.files)
        assert not collisions, f"{slug}: verify script path(s) collide with workspace files: {collisions}"


def test_old_fact_title_is_a_single_byte_identical_value(tmp_path):
    """DESIGN.md 10: all three revocation-reason rungs (bare/causal/corrective)
    carry the SAME falsified claim (the note's title) and differ only in
    `reason`. `old_fact_title` is a single field (not one per rung), so this
    is a structural guarantee -- asserted directly here per this repo's own
    convention of also testing invariants that are structurally impossible to
    violate (`lint_scenario`'s own `# pragma: no cover` branch makes the same
    point about itself)."""
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        assert isinstance(scenario.old_fact_title, str) and scenario.old_fact_title


def test_revocation_reasons_cover_all_three_variants():
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        for variant in ("bare", "causal", "corrective"):
            text = scenario.revocation_reasons.get(variant)
            assert isinstance(text, str) and text


def test_revocation_reasons_unknown_variant_raises_value_error():
    import pytest

    scenario = scen.get("retired_flag_api")
    with pytest.raises(ValueError):
        scenario.revocation_reasons.get("not-a-real-variant")


def test_materialize_then_lint_workspace_matches_declared_files(tmp_path):
    """`materialize()`'s own contract: every declared file lands on disk, and
    the returned baseline dict covers exactly the declared files, each hashed
    at COMMIT-1 content (DESIGN.md 6.2's residue-baseline rule depends on
    this). A file an `extra_commit` later overwrites (A4's own two version
    bumps) legitimately diverges from `scenario.files` on final disk bytes --
    the baseline dict itself must still match commit-1, which is what
    `sha256_file` is asserted against instead of a live re-read for those
    paths."""
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        workspace = tmp_path / slug
        baselines = scen.materialize(scenario, workspace)
        assert set(baselines) == set(scenario.files)
        overwritten_by_extra_commit = {
            rel for extra in scenario.extra_commits for rel in extra.files
        }
        for rel, content in scenario.files.items():
            target = workspace / rel
            assert target.is_file(), f"{slug}: {rel} missing after materialize()"
            assert baselines[rel] == scen.sha256_text(content), (
                f"{slug}: baseline for {rel} does not match commit-1 content"
            )
            if rel in overwritten_by_extra_commit:
                continue
            assert target.read_text(encoding="utf-8") == content


def test_materialize_verifiers_writes_outside_the_workspace(tmp_path):
    for slug in ALL_SLUGS:
        scenario = scen.get(slug)
        if not scenario.verify_scripts:
            continue
        verify_dir = tmp_path / slug / "verify"
        scen.materialize_verifiers(scenario, verify_dir)
        for rel in scenario.verify_scripts:
            target = verify_dir / rel
            assert target.is_file()
            assert target.stat().st_mode & 0o100  # executable bit set


def test_scenario_order_and_headline_are_the_dated_2026_07_30_resolution():
    """DESIGN.md 16.1's user resolution (dated 2026-07-30, applied in
    scenarios.py): A4 handwritten_notes headlines the report; scenario order
    across tiers is A4 -> A1 -> A2 -> A3; the frozen decision rule (DESIGN.md
    8) still runs on A1 retired_flag_api's own counts. Pinned here so a
    future edit to `scenarios.py` that silently reverts the override gets
    caught."""
    assert scen.SCENARIO_ORDER == (
        "handwritten_notes", "retired_flag_api", "inverted_default", "noop_make_target",
    )
    assert scen.HEADLINE_SCENARIO == "handwritten_notes"
    assert scen.DECISION_RULE_SCENARIO == "retired_flag_api"


def test_materialize_a1_rollback_repopulates_flags_yaml_and_drops_flags_d(tmp_path):
    """DESIGN.md 9's A1-rollback fixture (T3, not T0 -- exercised here purely
    offline since materialize_a1_rollback is pure file I/O, no daemon
    needed): F_old (flags.get/config/flags.yaml) is made literally true again."""
    workspace = tmp_path / "rollback"
    baselines = scen.materialize_a1_rollback(workspace)
    assert "config/flags.yaml" in baselines
    assert not any(rel.startswith("config/flags.d/") for rel in baselines)
    yaml_text = (workspace / "config/flags.yaml").read_text(encoding="utf-8")
    assert "beta_signup: false" in yaml_text
