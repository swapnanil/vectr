"""Composite regression for the four proxy-injection P0 defects.

Each defect has its own focused tests elsewhere (test_proactive_matcher.py,
test_proactive_service.py, test_memory.py). This module exists for the case
those miss: a fix that is individually correct but whose guarantee is undone
by another fix's code path. Every test here drives the FULL service stack and
asserts on the composed outcome:

  UPG-PROXY-REVOKED-LEAK       revoked notes never render raw in injection
  UPG-PROXY-SUBSTRING-ANCHOR   path anchors match on boundaries, not substrings
  UPG-PROXY-CROSS-CHANNEL-DEDUP proxy and hook share one turn ledger
  UPG-PROXY-LOOPBACK-BYPASS    bind refusal holds unconditionally

The scenario is deliberately adversarial: a revoked note and an active note
whose basenames are substrings of one another, delivered over two channels in
one session, under both bind postures.
"""
from __future__ import annotations

from unittest.mock import patch


def _service(tmp_path, monkeypatch, **env):
    from agent import indexer as idx_module
    from tests.conftest import _DummyEmbedProvider

    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        from app.service import VectrService
        return VectrService(workspace_root=str(tmp_path), memory_only=True)


def _setup(tmp_path, monkeypatch, **env):
    """A workspace with the substring-adjacent pair gate.py / uv_regate.py,
    one revoked note about gate.py, and one active note about uv_regate.py."""
    monkeypatch.delenv("VECTR_BIND_HOST", raising=False)
    svc = _service(tmp_path, monkeypatch, **env)
    (tmp_path / "gate.py").write_text("# gate\n")
    (tmp_path / "uv_regate.py").write_text("# regate\n")

    revoked_id = svc.remember(
        "gate.py rejects a candidate below the similarity floor",
        kind="gotcha", anchors=["gate.py"],
    )
    svc.revoke_note(revoked_id, reason="floor moved into the gate")
    other_id = svc.remember(
        "uv_regate.py re-runs the gate after a config reload", kind="gotcha",
    )
    return svc, revoked_id, other_id


# -- the composed happy path -----------------------------------------------

def test_revoked_note_renders_deterrent_and_substring_neighbour_stays_out(
    tmp_path, monkeypatch
):
    """B1 + B2 composed. Recalling for gate.py must:
      - still surface the REVOKED note (an agent about to re-assert a wrong
        belief is the highest-value injection) but as the deterrent, never
        as its raw claim presented as fact; and
      - NOT drag in uv_regate.py's note, whose content contains "gate.py"
        as a bare substring.

    A fix for either defect alone could mask the other: a revoked-note filter
    that dropped the note entirely would make the substring assertion vacuous,
    and a substring fix that over-narrowed would make the deterrent assertion
    vacuous. Both are asserted on the same single call.
    """
    svc, revoked_id, other_id = _setup(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    out = svc.proactive_context(
        text="", file_paths=[str(tmp_path / "gate.py")],
        session_id="s1", channel="proxy",
    )
    ctx = out["context"]

    assert f"note:{revoked_id}" in out["anchor_ids"], "revoked note should still inject"
    assert "REVOKED" in ctx
    assert "Do not re-derive" in ctx
    # The raw claim must never stand as an unqualified assertion: it may only
    # appear inside the deterrent's quoted "previously believed" clause.
    assert "rejects a candidate below the similarity floor" not in ctx.split(
        "Previously believed"
    )[0]
    # B2: the substring-adjacent neighbour is a different file.
    assert f"note:{other_id}" not in out["anchor_ids"]
    assert "uv_regate.py" not in ctx


def test_neighbour_recall_is_not_vacuous(tmp_path, monkeypatch):
    """Non-vacuity guard for the assertion above: uv_regate.py's note is
    genuinely reachable — it is simply not reachable from gate.py."""
    svc, _revoked_id, other_id = _setup(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    out = svc.proactive_context(
        text="", file_paths=[str(tmp_path / "uv_regate.py")],
        session_id="s1", channel="proxy",
    )
    assert f"note:{other_id}" in out["anchor_ids"]


# -- dedup composed with the revoked rendering ------------------------------

def test_revoked_note_does_not_double_inject_across_channels(tmp_path, monkeypatch):
    """B1 + B3 composed. The deterrent rendering changes a note's rendered
    TEXT; cross-channel dedup keys on its note id. A revoked note delivered
    on the hook surface must therefore still suppress the proxy channel in
    the same turn — the deterrent must not read as a second, different item.
    """
    svc, revoked_id, _other_id = _setup(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    path = str(tmp_path / "gate.py")
    sid = "shared-session"

    hook_text = svc.recall(file_path=path, session_id=sid, hook_event="PreToolUse")
    assert "gate.py" in hook_text

    proxy_out = svc.proactive_context(
        text="", file_paths=[path], session_id=sid, channel="proxy",
    )
    assert f"note:{revoked_id}" not in proxy_out["anchor_ids"]

    # Non-vacuous: a fresh session's proxy call does inject it.
    fresh = svc.proactive_context(
        text="", file_paths=[path], session_id="fresh", channel="proxy",
    )
    assert f"note:{revoked_id}" in fresh["anchor_ids"]


# -- bind refusal dominates every other fix ---------------------------------

def test_non_loopback_bind_suppresses_everything_the_other_fixes_allow(
    tmp_path, monkeypatch
):
    """B4 composed with B1/B2/B3. The bind refusal is the outermost gate: on
    a non-loopback bind, the exact call that injects a deterrent above must
    produce nothing at all — for every channel label a client can send,
    including the proxy channel's launch-is-consent exemption, and with the
    master switch explicitly on."""
    svc, _revoked_id, _other_id = _setup(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    path = str(tmp_path / "gate.py")

    # Control: loopback (the default) does inject, so the assertion below is
    # about the bind host and nothing else.
    assert svc.proactive_context(
        text="", file_paths=[path], session_id="control", channel="proxy"
    )["item_count"] >= 1

    monkeypatch.setenv("VECTR_BIND_HOST", "0.0.0.0")
    for channel in ("proxy", "hook", "SessionStart", "anything-a-client-sends"):
        out = svc.proactive_context(
            text="", file_paths=[path], session_id=f"s-{channel}", channel=channel,
        )
        assert out == {
            "context": "", "item_count": 0, "anchor_ids": [], "scores": []
        }, channel


def test_bind_refusal_does_not_consume_dedup_budget(tmp_path, monkeypatch):
    """B4 + B3 composed. A refused call must not claim the note in the turn
    ledger — otherwise a misconfigured bind would silently burn the note's
    one injection for that turn, and fixing the bind would surface nothing."""
    svc, revoked_id, _other_id = _setup(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    path = str(tmp_path / "gate.py")
    sid = "s1"

    monkeypatch.setenv("VECTR_BIND_HOST", "0.0.0.0")
    assert svc.proactive_context(
        text="", file_paths=[path], session_id=sid, channel="proxy"
    )["item_count"] == 0

    monkeypatch.setenv("VECTR_BIND_HOST", "127.0.0.1")
    out = svc.proactive_context(
        text="", file_paths=[path], session_id=sid, channel="proxy",
    )
    assert f"note:{revoked_id}" in out["anchor_ids"]
