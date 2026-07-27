"""Durable audit coverage for PROACTIVE_INJECT (UPG-PROXY-AUDIT-DURABLE).

Before this file, no test anywhere asserted that a PROACTIVE_INJECT event is
ever written to the opt-in durable audit log (`agent/working_context_store/
_audit.py`) — the event could silently stop firing and the suite would stay
green. This module closes that hole and additionally covers the two fields
added to the audit line: `chars=` (exact injected-context size, making
`max_chars_per_event` budget adherence observable) and `states=` (each
selected item's folded note-lifecycle state, positionally aligned with
`anchors=`).

Test hygiene: `_get_audit_logger()` caches handlers on the process-level
singleton logger `logging.getLogger("vectr.audit")` (agent/working_context_store/
_audit.py) — the established pattern elsewhere (tests/test_service.py,
tests/test_memory.py) clears its handlers before and after each case so tests
never leak a file handler onto each other or the rest of the suite. Reused
here as an autouse fixture instead of duplicating the two clear() calls in
every test.
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
    """Parse `<ts> EVENT k1=v1 k2=v2 ...` into {k: v} (audit.py's own format:
    `" ".join([event] + [f"{k}={v}" ...])`, prefixed with an asctime by the
    handler's formatter)."""
    parts = line.strip().split(" ")
    out: dict[str, str] = {}
    for p in parts[2:]:
        if "=" in p:
            k, _, v = p.partition("=")
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 1. The event fires at all, with every expected field.
# ---------------------------------------------------------------------------

def test_event_fires_with_workspace_channel_items_anchors_chars_states(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    f = tmp_path / "resolver.py"
    f.write_text("# resolver\n")
    svc.remember("resolver.py holds the workspace lock", kind="gotcha", anchors=["resolver.py"])

    out = svc.proactive_context(text="", file_paths=[str(f)], session_id="s1", channel="proxy")
    assert out["item_count"] == 1

    lines = _injection_lines(log_file)
    assert len(lines) == 1
    line = lines[0]
    assert "PROACTIVE_INJECT" in line
    fields = _fields(line)
    for key in ("workspace", "channel", "items", "anchors", "chars", "states"):
        assert key in fields, f"missing {key!r} in audit line: {line!r}"
    assert fields["channel"] == "proxy"
    assert fields["items"] == "1"


# ---------------------------------------------------------------------------
# 2. Opt-in preserved: unset VECTR_AUDIT_LOG writes no file, no directory,
#    and never raises into the request path.
# ---------------------------------------------------------------------------

def test_opt_in_preserved_no_file_no_directory_no_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("VECTR_AUDIT_LOG", raising=False)
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    f = tmp_path / "resolver.py"
    f.write_text("# resolver\n")
    svc.remember("resolver.py holds the workspace lock", kind="gotcha", anchors=["resolver.py"])

    out = svc.proactive_context(text="", file_paths=[str(f)], session_id="s1", channel="proxy")
    assert out["item_count"] == 1  # the injection itself is unaffected by audit being off

    would_be_log = tmp_path / "audit.log"
    assert not would_be_log.exists()
    # No directory side effect either — _init_audit_logger only creates the
    # log's parent dir when VECTR_AUDIT_LOG names a path.
    assert list(tmp_path.glob("*audit*")) == []


# ---------------------------------------------------------------------------
# 3. Size field is truthful for at least two different item counts.
# ---------------------------------------------------------------------------

def test_chars_field_equals_injected_context_length_for_two_item_counts(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    fa = tmp_path / "a.py"
    fa.write_text("# a\n")
    fb = tmp_path / "b.py"
    fb.write_text("# b\n")
    svc.remember("a.py holds the resource pool", kind="gotcha", anchors=["a.py"])
    svc.remember("b.py releases the resource pool", kind="gotcha", anchors=["b.py"])

    one = svc.proactive_context(text="", file_paths=[str(fa)], session_id="s-one", channel="proxy")
    assert one["item_count"] == 1
    two = svc.proactive_context(
        text="", file_paths=[str(fa), str(fb)], session_id="s-two", channel="proxy"
    )
    assert two["item_count"] == 2

    lines = _injection_lines(log_file)
    assert len(lines) == 2
    f1, f2 = _fields(lines[0]), _fields(lines[1])
    assert int(f1["chars"]) == len(one["context"])
    assert int(f2["chars"]) == len(two["context"])
    assert f1["chars"] != f2["chars"]


# ---------------------------------------------------------------------------
# 4. Budget observability: chars= never exceeds the configured budget plus
#    the fixed envelope overhead (the open/close provenance markers and their
#    separating newlines — the only bytes the gate's own per-event char budget
#    does not count). Computed from the gate's own constants rather than
#    hardcoded, so retuning the envelope wording cannot silently invalidate
#    the budget-adherence guarantee this asserts.
# ---------------------------------------------------------------------------

def test_chars_stays_within_configured_budget_plus_fixed_envelope(tmp_path, monkeypatch):
    from agent.proactive.gate import _ENVELOPE_CLOSE, _ENVELOPE_OPEN

    log_file = tmp_path / "audit.log"
    max_chars = 120
    svc = _service(
        tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file),
        VECTR_PROACTIVE_MAX_CHARS=str(max_chars), VECTR_PROACTIVE_MAX_ITEMS="5",
    )
    f = tmp_path / "pool.py"
    f.write_text("# pool\n")
    svc.remember("pool.py " + ("x" * 300), kind="gotcha", anchors=["pool.py"])

    out = svc.proactive_context(text="", file_paths=[str(f)], session_id="s1", channel="proxy")
    assert out["item_count"] == 1

    lines = _injection_lines(log_file)
    assert len(lines) == 1
    chars = int(_fields(lines[0])["chars"])
    # open marker + its trailing newline, plus the newline + close marker
    envelope = len(_ENVELOPE_OPEN) + 1 + 1 + len(_ENVELOPE_CLOSE)
    assert chars == len(out["context"])
    assert chars <= max_chars + envelope, (
        f"chars={chars} exceeds configured budget {max_chars} + envelope {envelope}"
    )


# ---------------------------------------------------------------------------
# 5. Lifecycle states are truthful and positionally aligned with anchors.
# ---------------------------------------------------------------------------

def test_states_positionally_aligned_with_anchors_for_active_and_revoked(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    f = tmp_path / "shared.py"
    f.write_text("# shared\n")
    active_id = svc.remember("shared.py active note", kind="gotcha", anchors=["shared.py"])
    revoked_id = svc.remember("shared.py revoked note", kind="gotcha", anchors=["shared.py"])
    svc.revoke_note(revoked_id, reason="measured: the limit is 3, not 5")

    out = svc.proactive_context(text="", file_paths=[str(f)], session_id="s1", channel="proxy")
    assert out["item_count"] == 2
    assert f"note:{active_id}" in out["anchor_ids"]
    assert f"note:{revoked_id}" in out["anchor_ids"]

    lines = _injection_lines(log_file)
    assert len(lines) == 1
    fields = _fields(lines[0])
    anchors = fields["anchors"].split(",")
    states = fields["states"].split(",")
    assert len(anchors) == len(states) == 2

    idx_active = anchors.index(f"note:{active_id}")
    idx_revoked = anchors.index(f"note:{revoked_id}")
    assert states[idx_active] == "active"
    assert states[idx_revoked] == "revoked"


# ---------------------------------------------------------------------------
# 6. No leakage: the audit line never carries window text, note body, or the
#    injected context string itself.
# ---------------------------------------------------------------------------

def test_no_leakage_of_window_text_note_body_or_injected_context(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    f = tmp_path / "secret.py"
    f.write_text("# secret\n")
    note_secret = "ZQXJ7-NOTE-BODY-SENTINEL"
    window_secret = "PLMK9-WINDOW-TEXT-SENTINEL"
    svc.remember(f"secret.py holds {note_secret}", kind="gotcha", anchors=["secret.py"])

    out = svc.proactive_context(
        text=f"debugging {window_secret}", file_paths=[str(f)], session_id="s1", channel="proxy",
    )
    assert out["item_count"] >= 1
    # Sanity: the secret really is present in the injected context, so the
    # absence check below is meaningful rather than vacuous.
    assert note_secret in out["context"]

    lines = _injection_lines(log_file)
    assert len(lines) == 1
    line = lines[0]
    assert note_secret not in line
    assert window_secret not in line
    assert out["context"] not in line


# ---------------------------------------------------------------------------
# 7. Fail-closed preserved: when the lifecycle-state fold raises, no note
#    candidate is injected, so no PROACTIVE_INJECT line ever claims one.
# ---------------------------------------------------------------------------

def test_fail_closed_note_states_error_emits_no_injection_line(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    f = tmp_path / "risky.py"
    f.write_text("# risky\n")
    svc.remember("risky.py note", kind="gotcha", anchors=["risky.py"])

    def _boom(*_a, **_kw):
        raise RuntimeError("note_events table unreadable")

    monkeypatch.setattr(svc._context_store, "note_event_states", _boom)

    out = svc.proactive_context(text="", file_paths=[str(f)], session_id="s1", channel="proxy")
    assert out == {"context": "", "item_count": 0, "anchor_ids": [], "scores": []}
    assert _injection_lines(log_file) == []


# ---------------------------------------------------------------------------
# 8. REST route level: POST /v1/proactive through a real TestClient over a
#    real VectrService writes the same audit line (MCP/service green is not
#    REST green).
# ---------------------------------------------------------------------------

def test_rest_route_emits_audit_line(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    log_file = tmp_path / "audit.log"
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1", VECTR_AUDIT_LOG=str(log_file))
    f = tmp_path / "route.py"
    f.write_text("# route\n")
    svc.remember("route.py handles dispatch", kind="gotcha", anchors=["route.py"])

    from api import app
    prior = getattr(app.state, "service", None)
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as client:
            app.state.service = svc
            try:
                resp = client.post(
                    "/v1/proactive",
                    json={
                        "text": "", "file_paths": [str(f)], "symbols": [],
                        "session_id": "route-s1", "channel": "proxy",
                    },
                )
            finally:
                app.state.service = prior

    assert resp.status_code == 200
    data = resp.json()
    assert data["item_count"] == 1

    lines = _injection_lines(log_file)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["channel"] == "proxy"
    assert fields["items"] == "1"
    assert int(fields["chars"]) == len(data["context"])
