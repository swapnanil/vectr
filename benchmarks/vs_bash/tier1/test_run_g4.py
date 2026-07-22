"""Unit tests for run_g4.py -- the G4 driver's own logic, isolated from any
daemon/network/live-session dependency: the frozen S4 note payload, the
fixed S3 session order, the REST memory-surface helpers (urllib mocked, no
real HTTP), the notes-endpoint-shape preflight, and the --parse-only
end-to-end path (S5 metrics + S6 decision rule) over synthetic transcripts.

Deliberately never imports g4_metrics' own test fixtures/builders --
self-contained, hermetic, no daemon, no network, no `claude -p` spawn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_g4 as g4  # noqa: E402


# ---------------------------------------------------------------------------
# Frozen S4 note -- content byte-exactness, payload field set
# ---------------------------------------------------------------------------

_EXPECTED_G4_NOTE_CONTENT = (
    "In this multi-module Maven repo, a single-module test run (`-pl <module>`, or cd into the "
    "module) compiles against previously installed artifacts from `~/.m2` — a change made in "
    "another module is invisible to it, so a green single-module run does NOT verify a "
    "cross-module change. To honestly verify a change in module A with a test in module B, run "
    "from the repo root: `./mvnw -pl <moduleA>,<moduleB> test -Dtest=<TestClass>` (list BOTH "
    "modules so A is rebuilt from source in the same reactor), or select module B with `-am`. "
    "And read the result: check the exit status or the final BUILD SUCCESS/FAILURE line — "
    "don't discard it behind `-q` piped into `tail`."
)


def test_note_content_byte_exact():
    assert g4.G4_NOTE_CONTENT == _EXPECTED_G4_NOTE_CONTENT
    # Both em-dashes (U+2014) from the pre-reg blockquote must survive intact.
    assert g4.G4_NOTE_CONTENT.count("—") == 2
    # Every backtick-quoted inline-code span from the source blockquote.
    for span in ("`-pl <module>`", "`~/.m2`",
                 "`./mvnw -pl <moduleA>,<moduleB> test -Dtest=<TestClass>`",
                 "`-am`", "`-q`", "`tail`"):
        assert span in g4.G4_NOTE_CONTENT


def test_note_content_contains_no_task_specifics():
    # S4: "deliberately contains NO task specifics, NO gate module names, NO
    # hint about the bug" -- the note must never name T2-02's actual modules
    # or gate test, only the generic module placeholders <moduleA>/<moduleB>.
    for forbidden in ("camel-core-languages", "camel-core", "SimplePredicateParserLogicalTest",
                       "LogicalExpression", "SimplePredicateParser"):
        assert forbidden not in g4.G4_NOTE_CONTENT


def test_note_payload_fields_frozen():
    assert g4.G4_NOTE_PAYLOAD["kind"] == "operational"
    assert g4.G4_NOTE_PAYLOAD["priority"] == "high"
    assert g4.G4_NOTE_PAYLOAD["title"] == "Maven multi-module verification"
    assert g4.G4_NOTE_PAYLOAD["tags"] == ["maven", "build", "verification"]
    assert g4.G4_NOTE_PAYLOAD["triggers"] == [
        {"event": "prompt-submit", "semantic": True},
        {"command": "*mvn*"},
    ]
    assert g4.G4_NOTE_PAYLOAD["content"] == g4.G4_NOTE_CONTENT
    # S4 explicitly omits provenance/scope/anchors (defaults apply) -- the
    # payload must carry no keys beyond the six S4 actually specifies.
    assert set(g4.G4_NOTE_PAYLOAD) == {"content", "kind", "priority", "title", "tags", "triggers"}


# ---------------------------------------------------------------------------
# Fixed S3 session order
# ---------------------------------------------------------------------------

def test_session_order_fixed():
    assert g4._SESSION_ORDER == ("M1", "C1", "M2", "C2", "M3", "C3")


def test_arm_of():
    assert g4._arm_of("M1") == g4._arm_of("M2") == g4._arm_of("M3") == "memory"
    assert g4._arm_of("C1") == g4._arm_of("C2") == g4._arm_of("C3") == "control"


def test_normalize_session_order_reorders_to_fixed_order():
    # S3: "deterministic alternation ... no randomization" -- any caller-
    # supplied order collapses to the fixed one, same convention as
    # run_t2.py's own _normalize_arm_order.
    assert g4._normalize_session_order(["C1", "M1"]) == ["M1", "C1"]
    assert g4._normalize_session_order(["C3", "M1", "C1"]) == ["M1", "C1", "C3"]
    assert g4._normalize_session_order(["M2"]) == ["M2"]


# ---------------------------------------------------------------------------
# REST memory surface (urllib mocked -- no real HTTP, no daemon)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_status_parses_json_and_hits_status_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse({"notes_count": 3, "episodes_count": 1, "arcs_pending_distill": 0})

    monkeypatch.setattr(g4.urllib.request, "urlopen", fake_urlopen)
    status = g4.get_status("http://localhost:9999")
    assert status == {"notes_count": 3, "episodes_count": 1, "arcs_pending_distill": 0}
    assert captured["url"] == "http://localhost:9999/v1/status"


def test_forget_all_notes_posts_all_true(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResponse({"deleted": 3})

    monkeypatch.setattr(g4.urllib.request, "urlopen", fake_urlopen)
    result = g4.forget_all_notes("http://localhost:9999")
    assert result == {"deleted": 3}
    req = captured["req"]
    assert req.full_url == "http://localhost:9999/v1/forget"
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode("utf-8")) == {"all": True}


def test_seed_g4_note_posts_exact_frozen_payload(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResponse({"id": 1})

    monkeypatch.setattr(g4.urllib.request, "urlopen", fake_urlopen)
    g4.seed_g4_note("http://localhost:9999")
    req = captured["req"]
    assert req.full_url == "http://localhost:9999/v1/remember"
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode("utf-8")) == g4.G4_NOTE_PAYLOAD


# ---------------------------------------------------------------------------
# notes-endpoint-shape preflight (read-only -- get_status mocked)
# ---------------------------------------------------------------------------

def test_notes_endpoint_shape_preflight_ok(monkeypatch):
    monkeypatch.setattr(
        g4, "get_status",
        lambda base, timeout_s=10: {"notes_count": 0, "episodes_count": 2, "arcs_pending_distill": 1},
    )
    ok, msg = g4.check_notes_endpoint_shape_preflight("http://localhost:9999")
    assert ok is True
    assert "notes_count=0" in msg and "episodes_count=2" in msg and "arcs_pending_distill=1" in msg


def test_notes_endpoint_shape_preflight_missing_keys(monkeypatch):
    # Reproduces the live bench-daemon finding: an older running vectr build
    # whose /v1/status predates episodes_count/arcs_pending_distill.
    monkeypatch.setattr(g4, "get_status", lambda base, timeout_s=10: {"notes_count": 0})
    ok, msg = g4.check_notes_endpoint_shape_preflight("http://localhost:9999")
    assert ok is False
    assert "episodes_count" in msg and "arcs_pending_distill" in msg


def test_notes_endpoint_shape_preflight_unreachable(monkeypatch):
    def _raise(base, timeout_s=10):
        raise OSError("connection refused")

    monkeypatch.setattr(g4, "get_status", _raise)
    ok, msg = g4.check_notes_endpoint_shape_preflight("http://localhost:9999")
    assert ok is False
    assert "cannot reach" in msg


# ---------------------------------------------------------------------------
# --parse-only: transcript discovery (tie-break) + end-to-end decision rule
# ---------------------------------------------------------------------------

def test_discover_transcripts_latest_mtime_wins(tmp_path):
    import os
    import time

    older = tmp_path / "M1_20260101T000000.jsonl"
    newer = tmp_path / "M1_20260102T000000.jsonl"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    found = g4._discover_transcripts(tmp_path)
    assert found == {"M1": newer}


def test_discover_transcripts_ignores_non_session_files(tmp_path):
    (tmp_path / "M1_stamp.jsonl").write_text("{}\n")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "T2-02_seed.patch").write_text("x")
    (tmp_path / "mcp_config_vectr.json").write_text("{}")
    found = g4._discover_transcripts(tmp_path)
    assert set(found) == {"M1"}


def _bash_call(tid: str, command: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}
    ]}}


def _bash_result(tid: str, text: str) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}
    ]}}


def _result_event() -> dict:
    return {
        "type": "result", "num_turns": 5, "duration_ms": 1000, "is_error": False,
        "total_cost_usd": 0.01, "usage": {
            "input_tokens": 10, "output_tokens": 5,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        },
    }


def _write_transcript(dirpath: Path, name: str, events: list[dict]) -> None:
    (dirpath / name).write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_parse_only_end_to_end_supported_verdict(tmp_path, capsys):
    # M1: honest via (a) -- both modules in -pl.
    _write_transcript(tmp_path, "M1_stamp.jsonl", [
        _bash_call("t1", "./mvnw -pl core/camel-core-languages,core/camel-core test "
                         "-Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    # C1: single-module false-pass trap firing.
    _write_transcript(tmp_path, "C1_stamp.jsonl", [
        _bash_call("t1", "./mvnw -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    # M2: honest via (c) -- full reactor from the repo root.
    _write_transcript(tmp_path, "M2_stamp.jsonl", [
        _bash_call("t1", "./mvnw test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    # C2: trap firing again.
    _write_transcript(tmp_path, "C2_stamp.jsonl", [
        _bash_call("t1", "./mvnw -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    # M3: max-turns exhaustion, no maven invocation at all -- no honest verification.
    _write_transcript(tmp_path, "M3_stamp.jsonl", [_result_event()])
    # C3: trap firing a third time.
    _write_transcript(tmp_path, "C3_stamp.jsonl", [
        _bash_call("t1", "./mvnw -pl core/camel-core test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])

    rc = g4._run_parse_only(tmp_path, fixture_root=Path("/repo"))
    assert rc == 0
    output = json.loads(capsys.readouterr().out)

    assert [s["label"] for s in output["sessions"]] == ["M1", "C1", "M2", "C2", "M3", "C3"]
    assert [s["arm"] for s in output["sessions"]] == [
        "memory", "control", "memory", "control", "memory", "control",
    ]
    honest_by_label = {s["label"]: s["metrics"]["honest_verification"] for s in output["sessions"]}
    assert honest_by_label == {
        "M1": True, "C1": False, "M2": True, "C2": False, "M3": False, "C3": False,
    }
    assert output["decision_rule"] == {
        "applicable": True,
        "arm_m_honest_count": 2, "arm_m_sessions_total": 3,
        "arm_c_honest_count": 0, "arm_c_sessions_total": 3,
        "condition_i_met": True, "condition_ii_met": True,
        "verdict": "SUPPORTED",
    }


def test_parse_only_not_supported_when_condition_ii_fails(tmp_path, capsys):
    # 2/3 M honest (condition i met) but the M-vs-C gap is only 1, not >= 2.
    _write_transcript(tmp_path, "M1_stamp.jsonl", [
        _bash_call("t1", "./mvnw test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    _write_transcript(tmp_path, "M2_stamp.jsonl", [
        _bash_call("t1", "./mvnw test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    _write_transcript(tmp_path, "M3_stamp.jsonl", [_result_event()])
    _write_transcript(tmp_path, "C1_stamp.jsonl", [
        _bash_call("t1", "./mvnw test -Dtest=SimplePredicateParserLogicalTest"),
        _bash_result("t1", "BUILD SUCCESS"),
        _result_event(),
    ])
    _write_transcript(tmp_path, "C2_stamp.jsonl", [_result_event()])
    _write_transcript(tmp_path, "C3_stamp.jsonl", [_result_event()])

    rc = g4._run_parse_only(tmp_path, fixture_root=Path("/repo"))
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    dr = output["decision_rule"]
    assert dr["applicable"] is True
    assert dr["condition_i_met"] is True
    assert dr["condition_ii_met"] is False
    assert dr["verdict"] == "NOT SUPPORTED"


def test_parse_only_incomplete_session_set_not_applicable(tmp_path, capsys):
    _write_transcript(tmp_path, "M1_stamp.jsonl", [_result_event()])
    _write_transcript(tmp_path, "C1_stamp.jsonl", [_result_event()])

    rc = g4._run_parse_only(tmp_path, fixture_root=Path("/repo"))
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    dr = output["decision_rule"]
    assert dr["applicable"] is False
    assert "1/3 memory, 1/3 control" in dr["reason"]
    assert "verdict" not in dr


def test_parse_only_missing_dir_returns_error(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    rc = g4._run_parse_only(missing, fixture_root=Path("/repo"))
    assert rc == 1
