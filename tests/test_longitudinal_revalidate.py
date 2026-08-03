"""Unit tests for `benchmarks/longitudinal_rediscovery/revalidate.py` (DEFECT 13,
required change C): recomputing `scorer.leg_non_vacuity` against a preserved
leg's on-disk artifacts alone (no daemon, no network, no model), writing
`result.revalidated.json` as a NEW sibling file and never touching the
original `result.json`.

Builds a real-shape fixture -- a `system.init` event with `mcp_servers`/
`tools`, an assistant `tool_use` block named `mcp__vectr__vectr_recall`, a
user `tool_result` block, and a terminal `result` event -- mirroring
`tests/test_longitudinal_scorer.py`'s own DEFECT 13 fixtures, duplicated
locally per this directory's no-cross-import-between-test-files convention
(mirrors `run_leg.py`/`scorer.py` themselves never importing each other).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "longitudinal_rediscovery"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, BENCH_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


revalidate = _load_local_module("_vectr_eval_longitudinal_revalidate_test", "revalidate.py")


def _mcp_tool_use_event(tool_use_id: str, name: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}]},
    }


def _mcp_tool_result_event(tool_use_id: str, *, text: str = "") -> dict:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": False,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }


def _mcp_transcript_events() -> list[dict]:
    return [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "vectr", "status": "pending"}],
            # ToolSearch present, mcp__vectr__* deliberately absent from
            # system.init.tools -- current Claude Code headless behavior.
            "tools": ["Bash", "Read", "ToolSearch"],
        },
        _mcp_tool_use_event("toolu_1", "mcp__vectr__vectr_recall"),
        _mcp_tool_result_event("toolu_1", text="[#7] lock acquisition notes ..."),
        {"type": "result", "subtype": "success", "usage": {"output_tokens": 10}},
    ]


def _write_leg_fixture(tmp_path: Path, *, original_valid: bool, original_reason: str) -> tuple[Path, str]:
    """Lays out `<tmp_path>/legs/2/artifacts/{result.json,transcript.jsonl}` --
    the standard on-disk shape `run_plan.py` writes for a k>=2 leg (`leg_dir =
    traj_dir / "legs" / str(k)`, `result.json` under its `artifacts/`
    subdirectory). Returns `(trajectory_dir, original_result_json_text)`.
    """
    traj_dir = tmp_path / "release_via_ci-mcp-none-s0"
    artifacts_dir = traj_dir / "legs" / "2" / "artifacts"
    artifacts_dir.mkdir(parents=True)

    events = _mcp_transcript_events()
    (artifacts_dir / "transcript.jsonl").write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )

    original = {
        "leg_id": "20260101T000000Z-release_via_ci-mcp-none-s0-k2",
        "trajectory_id": "release_via_ci-mcp-none-s0",
        "scenario": "release_via_ci",
        "arm": "mcp",
        "note_variant": "none",
        "k": 2,
        "notes_in_store_at_start": 1,
        "restored_manifest_ok": None,
        "planted_note_id": 7,
        "planted_anchor": None,
        "mcp_handshake_ok": None,
        "recall_probe_returned_note": True,
        "recall_probe_method": "semantic",
        "recall_probe_elapsed_s": 4.0,
        "trail_text_delivered": None,
        "agent_returncode": 0,
        "hook_injection_counts": None,
        "proxy_metrics": {"injected": 0},
        "cost": {"is_error": False, "output_tokens": 10},
        "audit_offset_after_preflight": 0,
        "valid": original_valid,
        "invalid_reason": original_reason,
        "non_vacuity": {"stub": "pre-fix gate output, superseded by revalidation"},
    }
    text = json.dumps(original, indent=2)
    (artifacts_dir / "result.json").write_text(text)
    return traj_dir, text


def test_revalidate_leg_flips_invalid_to_valid_and_never_mutates_original(tmp_path):
    traj_dir, original_text = _write_leg_fixture(
        tmp_path, original_valid=False,
        original_reason="pre-fix gate: mcp_servers != expected exact-equality shape",
    )

    artifacts_dir = revalidate.resolve_leg_artifacts_dir(f"{traj_dir}:2")
    assert artifacts_dir == traj_dir / "legs" / "2" / "artifacts"

    revalidated = revalidate.revalidate_leg(artifacts_dir)
    assert revalidated["valid"] is True, revalidated["invalid_reason"]
    assert revalidated["non_vacuity"]["vectr_tools_evidence"] == "tool-use"
    assert revalidated["non_vacuity"]["mcp_server_status"] == "pending"

    meta = revalidated["revalidate_meta"]
    assert meta["old_valid"] is False
    assert meta["new_valid"] is True
    assert meta["flipped"] is True
    assert meta["evidence_class"] == "tool-use"
    assert "date" not in meta  # deliberately no wall-clock timestamp field

    # never mutate the original file
    result_path = artifacts_dir / "result.json"
    assert result_path.read_text() == original_text

    out_path = artifacts_dir / "result.revalidated.json"
    out_path.write_text(json.dumps(revalidated, indent=2))
    on_disk = json.loads(out_path.read_text())
    assert on_disk["valid"] is True
    assert on_disk["revalidate_meta"]["flipped"] is True
    # result.json itself is still byte-identical after the sibling write
    assert result_path.read_text() == original_text


def test_revalidate_leg_cli_main_writes_sibling_and_leaves_original_byte_identical(tmp_path, capsys):
    traj_dir, original_text = _write_leg_fixture(
        tmp_path, original_valid=False, original_reason="stale pre-fix verdict",
    )
    artifacts_dir = traj_dir / "legs" / "2" / "artifacts"

    argv = sys.argv
    try:
        sys.argv = ["revalidate.py", "--revalidate-leg", f"{traj_dir}:2"]
        revalidate.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "valid False -> True" in out

    result_path = artifacts_dir / "result.json"
    assert result_path.read_text() == original_text, "result.json must never be mutated"

    revalidated_path = artifacts_dir / "result.revalidated.json"
    assert revalidated_path.is_file()
    on_disk = json.loads(revalidated_path.read_text())
    assert on_disk["valid"] is True
    assert on_disk["revalidate_meta"]["old_valid"] is False
    assert on_disk["revalidate_meta"]["new_valid"] is True
