"""Cross-lane composition gate for the three UPG-PROXY-*  P1 fixes.

Each P1 branch was green in isolation; this file exists because "green
separately" is not "correct together". It drives the real service stack with
all three fixes live and pins the interactions no single-lane test could see:

  * UPG-PROXY-INJECT-ROLE-PROVENANCE — envelope framing, per-item provenance
    markers, proxy-channel directive exclusion.
  * UPG-PROXY-AUDIT-DURABLE — `chars=` / `states=` on the durable audit line.
  * UPG-PROXY-INJECT-PRECISION — structural kind eligibility, tiered
    structural scores, weak-mention cap.

The directive-overlap cases are the reason this file is a lane deliverable
rather than a coder deliverable: two INDEPENDENT mechanisms suppress
directive notes, they were built on different branches, and their combined
behaviour is asymmetric in a way a reader of either config alone would guess
wrong (see `test_two_directive_filters_are_independent_and_asymmetric`).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_audit_logger():
    logging.getLogger("vectr.audit").handlers.clear()
    yield
    logging.getLogger("vectr.audit").handlers.clear()


def _service(tmp_path, monkeypatch, **env):
    from agent import indexer as idx_module
    from tests.conftest import _DummyEmbedProvider

    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    monkeypatch.delenv("VECTR_BIND_HOST", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        from app.service import VectrService
        return VectrService(workspace_root=str(tmp_path), memory_only=True)


def _injection_lines(log_file) -> list[str]:
    if not log_file.exists():
        return []
    return [ln for ln in log_file.read_text().splitlines() if "PROACTIVE_INJECT" in ln]


def _fields(line: str) -> dict:
    out: dict[str, str] = {}
    for p in line.strip().split(" ")[2:]:
        if "=" in p:
            k, _, v = p.partition("=")
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Condition 8: the two directive filters are INDEPENDENT and ASYMMETRIC.
# ---------------------------------------------------------------------------

def test_two_directive_filters_are_independent_and_asymmetric(tmp_path, monkeypatch):
    """Two separate mechanisms withhold directive notes, and turning one OFF
    does not restore them to the other's channel.

    * `proactive.proxy.exclude_directive_notes` (ROLE-PROVENANCE) drops
      directives from BOTH the structural and semantic channels, on the proxy
      channel only, and is a user-facing toggle.
    * `proactive.structural_kinds` (INJECT-PRECISION) is an allowlist that
      omits `directive`, so it drops directives from the STRUCTURAL channel
      only, on EVERY channel, regardless of that toggle.

    So a user who sets `exclude_directive_notes=false` expecting directives
    back gets them through the semantic channel only — the structural channel
    still withholds them. That is intentional on both sides, but it is not
    guessable from either config key alone, so it is pinned here rather than
    left to be rediscovered as a bug report.
    """
    svc = _service(
        tmp_path, monkeypatch, VECTR_PROACTIVE="1",
        # ROLE-PROVENANCE's toggle explicitly OFF (non-default).
        VECTR_PROACTIVE_PROXY_EXCLUDE_DIRECTIVE_NOTES="0",
        # INJECT-PRECISION's allowlist left at its default, which omits
        # `directive` (and `task`).
        VECTR_PROACTIVE_MAX_ITEMS="5",
    )
    f = tmp_path / "resolver.py"
    f.write_text("# resolver\n")
    directive_id = svc.remember(
        "Always run resolver.py with --strict before committing.",
        kind="directive", anchors=["resolver.py"],
    )

    out = svc.proactive_context(
        text="", file_paths=[str(f)], session_id="s1", channel="proxy",
    )

    # The structural channel still withholds it: the allowlist is not the
    # toggle, and disabling the toggle did not put `directive` on the list.
    assert f"note:{directive_id}" not in out["anchor_ids"], (
        "directive reached the structural channel even though `directive` is "
        "absent from proactive.structural_kinds — the two filters are no "
        "longer independent"
    )


def test_directive_suppressed_when_both_filters_are_active(tmp_path, monkeypatch):
    """Both mechanisms at their defaults: no double-suppression surprise, no
    error, and the directive is simply absent — the ROLE-PROVENANCE outcome
    holds regardless of the PRECISION allowlist also excluding it."""
    svc = _service(
        tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_PROACTIVE_MAX_ITEMS="5",
    )
    f = tmp_path / "resolver.py"
    f.write_text("# resolver\n")
    directive_id = svc.remember(
        "Always run resolver.py with --strict before committing.",
        kind="directive", anchors=["resolver.py"],
    )
    finding_id = svc.remember(
        "resolver.py acquires a PID-scoped workspace lock, dropped on scope exit.",
        kind="finding", anchors=["resolver.py"],
    )

    out = svc.proactive_context(
        text="", file_paths=[str(f)], session_id="s1", channel="proxy",
    )

    assert f"note:{directive_id}" not in out["anchor_ids"]
    # ...and suppressing the directive did not suppress its eligible sibling.
    assert f"note:{finding_id}" in out["anchor_ids"]


# ---------------------------------------------------------------------------
# All three fixes visible in one injected block + one audit line.
# ---------------------------------------------------------------------------

def test_all_three_fixes_compose_in_one_injection(tmp_path, monkeypatch):
    """One delivery moment exercising every fix at once:

      envelope open/close  (ROLE-PROVENANCE)
      per-item provenance  (ROLE-PROVENANCE)
      directive withheld   (ROLE-PROVENANCE + INJECT-PRECISION)
      task withheld        (INJECT-PRECISION lever 1)
      declared anchor      (INJECT-PRECISION lever 2, tier A)
      revoked deterrent    (P0 anti-memory contract, still intact)
      chars= / states=     (AUDIT-DURABLE)
    """
    # `remember()` defaults provenance="agent" for every note below (none
    # passes an explicit provenance), so the packed block gets the
    # agent-tier envelope open (UPG-PROXY-INJECT-ROLE-PROVENANCE) rather
    # than the auto-tier `_ENVELOPE_OPEN` -- see
    # test_proactive_role_provenance.py for the tiering logic itself.
    from agent.proactive.gate import _ENVELOPE_CLOSE, _ENVELOPE_OPEN_AGENT

    log_file = tmp_path / "audit.log"
    svc = _service(
        tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file),
        VECTR_PROACTIVE_MAX_ITEMS="5",
    )
    f = tmp_path / "resolver.py"
    f.write_text("# resolver\n")

    finding_id = svc.remember(
        "resolver.py acquires a PID-scoped workspace lock, dropped on scope exit.",
        kind="finding", anchors=["resolver.py"],
    )
    directive_id = svc.remember(
        "Always run resolver.py with --strict before committing.",
        kind="directive", anchors=["resolver.py"],
    )
    task_id = svc.remember(
        "Checkpoint: resolver.py refactor is half done, resuming tomorrow.",
        kind="task", anchors=["resolver.py"],
    )
    revoked_id = svc.remember(
        "resolver.py retry timeout is 30 seconds.",
        kind="finding", anchors=["resolver.py"],
    )
    svc.revoke_note(revoked_id, reason="measured at 10s, not 30s")

    out = svc.proactive_context(
        text="", file_paths=[str(f)], session_id="s1", channel="proxy",
    )
    ctx = out["context"]

    # ROLE-PROVENANCE: the block is enveloped on both sides.
    assert ctx.startswith(_ENVELOPE_OPEN_AGENT)
    assert ctx.rstrip().endswith(_ENVELOPE_CLOSE)

    # ROLE-PROVENANCE + INJECT-PRECISION: imperative-shaped and
    # moment-in-time kinds are both withheld from this channel.
    assert f"note:{directive_id}" not in out["anchor_ids"]
    assert f"note:{task_id}" not in out["anchor_ids"]

    # INJECT-PRECISION lever 2: a declared anchor is Tier A, not a flat 1.0
    # applied to everything — the eligible notes are declared-anchored here,
    # so they score at the top tier rather than the weak-mention tier.
    assert f"note:{finding_id}" in out["anchor_ids"]
    idx = list(out["anchor_ids"]).index(f"note:{finding_id}")
    assert out["scores"][idx] > 0.6, (
        "a DECLARED-anchor structural match must not be scored at the weak "
        "mention tier"
    )

    # ROLE-PROVENANCE: every rendered note line carries a provenance marker.
    for line in ctx.splitlines():
        if line.startswith("note #"):
            assert any(p in line for p in ("human", "agent", "auto")), line

    # P0 anti-memory contract survives all of the above.
    assert f"note:{revoked_id}" in out["anchor_ids"]
    assert "REVOKED" in ctx

    # AUDIT-DURABLE: one line, sized, with states aligned to anchors.
    lines = _injection_lines(log_file)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert int(fields["chars"]) == len(ctx)
    anchors = fields["anchors"].split(",")
    states = fields["states"].split(",")
    assert len(anchors) == len(states) == out["item_count"]
    assert states[anchors.index(f"note:{revoked_id}")] == "revoked"
    assert states[anchors.index(f"note:{finding_id}")] == "active"
