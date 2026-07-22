"""Tests for the arc distillation build contract
(memoization-l3-distiller-design.md): `EpisodeStore.list_arcs`/
`resolve_arcs_distilled`/`resolve_arcs_dismissed`, the additive `arcs`
columns migration, `GET /v1/arcs` + `POST /v1/arcs/dismiss`,
`vectr_remember(..., distilled_from=[...])` on both REST and MCP, the MCP
`vectr_distill` tool (render + dismiss), and the session-start pending-arc
nudge line.

vectr never decides what is worth keeping here (zero-inference core): every
test below either asserts a RENDERED FACT (arc rows, a nudge count) or a
CALLER-PROPOSED VERDICT being recorded verbatim (distilled_from linkage, a
dismiss reason) — never a judgment computed by vectr itself.
"""
from __future__ import annotations

import sqlite3
import time as time_module
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent.episode_store import EpisodeStore
from tests.conftest import _DummyEmbedProvider


# ---------------------------------------------------------------------------
# EpisodeStore.list_arcs / resolve_arcs_distilled / resolve_arcs_dismissed
# ---------------------------------------------------------------------------


def _insert_arc(store: EpisodeStore, ws: str, *, ts: float, confidence: str = "normal") -> int:
    """`ts` must be a recent (near-`time.time()`) epoch value — `.insert()`
    runs the same per-workspace TTL retention sweep production code does
    (`agent/episode_store.py::_enforce_retention`, EPISODES_TTL_DAYS-based),
    which would otherwise prune an old-epoch fixture row out from under
    this helper before `insert_arc` ever links to it."""
    fail_id = store.insert(
        ws, session_id="s1", ts=ts, cwd="/repo", tool="bash", cmd_raw="mvn test -Dtest=Foo",
        verb="mvn test", flags=["-Dtest=Foo"], args=[], rc=1, termination="normal",
        outcome="failure", stdout_digest="", stderr_digest="", markers_matched=[],
        env_delta_names=[], file_path=None, max_rows=1000, ttl_days=30,
    )
    success_id = store.insert(
        ws, session_id="s1", ts=ts + 1, cwd="/repo", tool="bash", cmd_raw="mvn test -Dtest=Bar",
        verb="mvn test", flags=["-Dtest=Bar"], args=[], rc=0, termination="normal",
        outcome="success", stdout_digest="", stderr_digest="", markers_matched=[],
        env_delta_names=[], file_path=None, max_rows=1000, ttl_days=30,
    )
    arc_id = store.insert_arc(
        ws, session_id="s1", cwd="/repo", ts=ts, confidence=confidence,
        mutation_diff={"flag": (("-Dtest=Foo",), ("-Dtest=Bar",))},
        failure_episode_ids=[fail_id], success_episode_id=success_id,
    )
    store.mark_episode_arc(fail_id, arc_id)
    store.mark_episode_arc(success_id, arc_id)
    return arc_id


class TestListArcs:
    def test_returns_pending_by_default_with_joined_episode_fields(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        arc_id = _insert_arc(store, ws, ts=time_module.time())

        rows = store.list_arcs(ws)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == arc_id
        assert row["confidence"] == "normal"
        assert row["mutation_diff"] == {"flag": [["-Dtest=Foo"], ["-Dtest=Bar"]]}
        assert len(row["failures"]) == 1
        assert row["failures"][0]["verb"] == "mvn test"
        assert row["failures"][0]["outcome"] == "failure"
        assert row["success"]["verb"] == "mvn test"
        assert row["success"]["cmd_raw"] == "mvn test -Dtest=Bar"
        assert row["distilled_at"] is None
        assert row["distilled_note_id"] is None
        assert row["dismissed_reason"] is None

    def test_orders_confidence_first_then_oldest(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        now = time_module.time()
        low_id = _insert_arc(store, ws, ts=now - 10, confidence="low")
        normal_old_id = _insert_arc(store, ws, ts=now - 100, confidence="normal")
        normal_new_id = _insert_arc(store, ws, ts=now - 5, confidence="normal")

        ids = [r["id"] for r in store.list_arcs(ws)]
        assert ids == [normal_old_id, normal_new_id, low_id]

    def test_status_resolved_and_all(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        now = time_module.time()
        pending_id = _insert_arc(store, ws, ts=now - 100)
        resolved_id = _insert_arc(store, ws, ts=now - 50)
        store.resolve_arcs_distilled(ws, [resolved_id], note_id=7)

        assert [r["id"] for r in store.list_arcs(ws, status="pending")] == [pending_id]
        assert [r["id"] for r in store.list_arcs(ws, status="resolved")] == [resolved_id]
        assert {r["id"] for r in store.list_arcs(ws, status="all")} == {pending_id, resolved_id}

    def test_unknown_status_raises_value_error(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.list_arcs(str(tmp_path), status="bogus")

    def test_missing_episode_rows_are_skipped_not_fatal(self, tmp_path):
        """A failure/success episode could in principle be pruned by the
        episodes ring buffer/TTL sweep (agent/episode_store.py's
        `_enforce_retention`) after the arc that references it was
        written — the join must degrade gracefully (drop the missing
        entry), never raise."""
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        arc_id = store.insert_arc(
            ws, session_id="s1", cwd="/repo", ts=100.0, confidence="normal",
            mutation_diff={}, failure_episode_ids=[999999], success_episode_id=888888,
        )
        rows = store.list_arcs(ws)
        assert rows[0]["id"] == arc_id
        assert rows[0]["failures"] == []
        assert rows[0]["success"] is None


class TestResolveArcs:
    def test_resolve_arcs_distilled_sets_note_id_not_reason(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        arc_id = _insert_arc(store, ws, ts=time_module.time())

        result = store.resolve_arcs_distilled(ws, [arc_id], note_id=42)
        assert result == {"resolved": [arc_id], "unresolved": []}

        row = store.list_arcs(ws, status="all")[0]
        assert row["distilled_at"] is not None
        assert row["distilled_note_id"] == 42
        assert row["dismissed_reason"] is None

    def test_resolve_arcs_dismissed_sets_reason_not_note_id(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        arc_id = _insert_arc(store, ws, ts=time_module.time())

        result = store.resolve_arcs_dismissed(ws, [arc_id], "covered by note #3")
        assert result == {"resolved": [arc_id], "unresolved": []}

        row = store.list_arcs(ws, status="all")[0]
        assert row["distilled_at"] is not None
        assert row["distilled_note_id"] is None
        assert row["dismissed_reason"] == "covered by note #3"

    def test_unknown_and_already_resolved_ids_come_back_unresolved_never_raise(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        ws = str(tmp_path)
        arc_id = _insert_arc(store, ws, ts=time_module.time())
        store.resolve_arcs_distilled(ws, [arc_id], note_id=1)

        result = store.resolve_arcs_distilled(ws, [arc_id, 999999], note_id=2)
        assert result == {"resolved": [], "unresolved": [arc_id, 999999]}
        # the first resolution is untouched — no double-write.
        row = store.list_arcs(ws, status="all")[0]
        assert row["distilled_note_id"] == 1

    def test_arc_in_a_different_workspace_is_unresolved(self, tmp_path):
        store = EpisodeStore(str(tmp_path))
        arc_id = _insert_arc(store, str(tmp_path), ts=time_module.time())
        result = store.resolve_arcs_distilled("some/other/workspace", [arc_id], note_id=1)
        assert result == {"resolved": [], "unresolved": [arc_id]}


class TestArcsColumnMigration:
    def test_migration_adds_columns_to_preexisting_populated_arcs_table(self, tmp_path):
        """A db file created before this build's `distilled_note_id`/
        `dismissed_reason` columns existed — hand-built pre-migration
        schema with a data row already in it — must gain both columns
        (nullable, existing row untouched) the next time `EpisodeStore`
        opens it, with no data loss."""
        db_path = tmp_path / "working_context.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE arcs (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace                TEXT NOT NULL,
                session_id               TEXT NOT NULL,
                cwd                      TEXT NOT NULL DEFAULT '',
                ts                       REAL NOT NULL,
                confidence               TEXT NOT NULL DEFAULT 'normal',
                mutation_diff_json       TEXT NOT NULL DEFAULT '{}',
                failure_episode_ids_json TEXT NOT NULL DEFAULT '[]',
                success_episode_id       INTEGER,
                distilled_at             REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO arcs (workspace, session_id, cwd, ts) VALUES (?, ?, ?, ?)",
            (str(tmp_path), "s1", "/repo", 100.0),
        )
        conn.commit()
        conn.close()

        store = EpisodeStore(str(tmp_path))
        check_conn = sqlite3.connect(str(db_path))
        check_conn.row_factory = sqlite3.Row
        cols = {row["name"] for row in check_conn.execute("PRAGMA table_info(arcs)")}
        check_conn.close()
        assert "distilled_note_id" in cols
        assert "dismissed_reason" in cols

        rows = store.list_arcs(str(tmp_path), status="all")
        assert len(rows) == 1
        assert rows[0]["distilled_note_id"] is None
        assert rows[0]["dismissed_reason"] is None

        # New columns are usable immediately, on the pre-existing row.
        result = store.resolve_arcs_dismissed(str(tmp_path), [rows[0]["id"]], "stale")
        assert result["resolved"] == [rows[0]["id"]]

    def test_reopening_an_already_migrated_store_is_a_no_op(self, tmp_path):
        """Calling `_init_db`/`_migrate_arcs_columns` twice (two
        `EpisodeStore` instances against the same db file, as happens on
        every process restart) must never raise `duplicate column`."""
        EpisodeStore(str(tmp_path))
        EpisodeStore(str(tmp_path))  # must not raise


# ---------------------------------------------------------------------------
# Real-service integration — REST + MCP, via the actual detection pipeline
# ---------------------------------------------------------------------------


def _make_real_service(tmp_path, monkeypatch):
    """Mirrors `_make_real_service` in tests/test_episode_route.py — a real
    VectrService, dummy embedder, own workspace root."""
    from agent import indexer as idx_module

    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        from app.service import VectrService
        svc = VectrService(workspace_root=str(tmp_path))
    return svc


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    """Function-scoped TestClient over a REAL VectrService, dedicated to
    this file (same isolation rationale as test_episode_route.py's
    `real_episode_client`)."""
    svc = _make_real_service(tmp_path, monkeypatch)
    from api import app
    prior = getattr(app.state, "service", None)
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            try:
                yield c, svc
            finally:
                app.state.service = prior


def _produce_one_pending_arc(client, session_id: str = "s1") -> None:
    """Drives the REAL `POST /v1/episode` route through a failure ->
    (edit) -> success chain so `app.arcs.ArcDetector` emits exactly one
    pending arc — the same shape as
    tests/test_episode_route.py::TestArcDetectionWiring's repro, reused
    here as this file's arc-producing fixture rather than re-testing
    detection itself (already covered there and in tests/test_arcs.py)."""
    base_ts = time_module.time()
    client.post("/v1/episode", json={
        "session_id": session_id, "cwd": "/repo", "tool": "bash",
        "command": "pytest tests/test_foo.py -k test_a", "rc": 1, "ts": base_ts,
    })
    client.post("/v1/episode", json={
        "session_id": session_id, "cwd": "/repo", "tool": "bash",
        "command": "pytest tests/test_foo.py -k test_b", "rc": 1, "ts": base_ts + 1,
    })
    client.post("/v1/episode", json={
        "session_id": session_id, "cwd": "/repo", "tool": "edit",
        "file_path": "/repo/tests/test_foo.py", "ts": base_ts + 2,
    })
    resp = client.post("/v1/episode", json={
        "session_id": session_id, "cwd": "/repo", "tool": "bash",
        "command": "pytest tests/test_foo.py -k test_b", "rc": 0, "ts": base_ts + 3,
    })
    assert resp.status_code == 200


class TestArcsRestRoute:
    def test_get_arcs_happy_path(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)

        resp = client.get("/v1/arcs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pending"] == 1
        assert len(data["arcs"]) == 1
        arc = data["arcs"][0]
        assert arc["confidence"] == "normal"
        assert len(arc["failures"]) == 2
        assert arc["success"]["cmd_raw"] == "pytest tests/test_foo.py -k test_b"
        assert arc["distilled_note_id"] is None
        assert arc["dismissed_reason"] is None
        assert "processing_ms" in data

    def test_get_arcs_invalid_status_returns_422(self, real_client):
        client, _svc = real_client
        resp = client.get("/v1/arcs", params={"status": "bogus"})
        assert resp.status_code == 422

    def test_get_arcs_empty_when_none_pending(self, real_client):
        client, _svc = real_client
        resp = client.get("/v1/arcs")
        assert resp.status_code == 200
        assert resp.json()["arcs"] == []
        assert resp.json()["total_pending"] == 0

    def test_get_arcs_no_limit_param_applies_config_default(self, real_client):
        """§2/§6: an omitted `limit` resolves to
        `episodes.distill_max_arcs_rendered`, not a route-local hardcoded
        number — insert more pending arcs than the config default and
        confirm the unbounded-looking call still stops there."""
        from agent.config import EPISODES_DISTILL_MAX_ARCS_RENDERED

        client, svc = real_client
        now = time_module.time()
        extra = 3
        for i in range(EPISODES_DISTILL_MAX_ARCS_RENDERED + extra):
            _insert_arc(svc._episode_store, svc._workspace_root, ts=now - i)

        resp = client.get("/v1/arcs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["arcs"]) == EPISODES_DISTILL_MAX_ARCS_RENDERED
        assert data["total_pending"] == EPISODES_DISTILL_MAX_ARCS_RENDERED + extra

    def test_get_arcs_default_limit_resolved_at_request_time(self, real_client, monkeypatch):
        """The config value is read inside the route handler at request
        time (`config.EPISODES_DISTILL_MAX_ARCS_RENDERED`, module-attribute
        lookup), not captured once at import time — a config override
        applied after the app module has already been imported must still
        change the effective default on the next request."""
        import agent.config as config_module

        client, svc = real_client
        now = time_module.time()
        for i in range(5):
            _insert_arc(svc._episode_store, svc._workspace_root, ts=now - i)

        monkeypatch.setattr(config_module, "EPISODES_DISTILL_MAX_ARCS_RENDERED", 2)
        resp = client.get("/v1/arcs")
        assert resp.status_code == 200
        assert len(resp.json()["arcs"]) == 2

    def test_get_arcs_explicit_limit_overrides_config_default(self, real_client):
        client, svc = real_client
        now = time_module.time()
        for i in range(5):
            _insert_arc(svc._episode_store, svc._workspace_root, ts=now - i)

        resp = client.get("/v1/arcs", params={"limit": 3})
        assert resp.status_code == 200
        assert len(resp.json()["arcs"]) == 3

    def test_dismiss_happy_path_then_status_resolved_reflects_it(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]

        resp = client.post("/v1/arcs/dismiss", json={"arc_ids": [arc_id], "reason": "flaky, not a real lesson"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolved"] == [arc_id]
        assert data["unresolved"] == []

        assert client.get("/v1/arcs").json()["arcs"] == []
        resolved = client.get("/v1/arcs", params={"status": "resolved"}).json()["arcs"]
        assert resolved[0]["dismissed_reason"] == "flaky, not a real lesson"
        assert resolved[0]["distilled_note_id"] is None

    def test_dismiss_unknown_id_returns_unresolved_not_error(self, real_client):
        client, _svc = real_client
        resp = client.post("/v1/arcs/dismiss", json={"arc_ids": [999999], "reason": "x"})
        assert resp.status_code == 200
        assert resp.json() == {"resolved": [], "unresolved": [999999], "processing_ms": resp.json()["processing_ms"]}

    def test_dismiss_missing_reason_returns_422(self, real_client):
        client, _svc = real_client
        resp = client.post("/v1/arcs/dismiss", json={"arc_ids": [1], "reason": ""})
        assert resp.status_code == 422

    def test_dismiss_missing_arc_ids_returns_422(self, real_client):
        client, _svc = real_client
        resp = client.post("/v1/arcs/dismiss", json={"reason": "x"})
        assert resp.status_code == 422


class TestRememberDistilledFromRest:
    def test_remember_with_distilled_from_resolves_the_arc(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]

        resp = client.post("/v1/remember", json={
            "content": "pytest -k selects by node id substring, not exact match",
            "distilled_from": [arc_id],
        })
        assert resp.status_code == 200
        data = resp.json()
        note_id = data["note_id"]
        assert data["distilled"]["resolved"] == [arc_id]
        assert data["distilled"]["unresolved"] == []

        resolved = client.get("/v1/arcs", params={"status": "resolved"}).json()["arcs"]
        assert resolved[0]["distilled_note_id"] == note_id

    def test_remember_without_distilled_from_omits_the_field(self, real_client):
        client, _svc = real_client
        resp = client.post("/v1/remember", json={"content": "an ordinary note"})
        assert resp.status_code == 200
        assert resp.json()["distilled"] is None

    def test_remember_with_unknown_arc_id_reports_unresolved_note_still_written(self, real_client):
        client, _svc = real_client
        resp = client.post("/v1/remember", json={
            "content": "a note", "distilled_from": [999999],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["note_id"], int)
        assert data["distilled"]["resolved"] == []
        assert data["distilled"]["unresolved"] == [999999]


class TestVectrDistillMcp:
    def test_render_pending_arcs_includes_rules_header_and_arc_block(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        from integrations.mcp_server import handle_tools_call
        from integrations.mcp_server._schemas import _DISTILL_RULES_TEXT

        result = handle_tools_call("vectr_distill", {}, svc)
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert _DISTILL_RULES_TEXT in text
        assert "pytest tests/test_foo.py -k test_b" in text

    def test_render_with_no_pending_arcs_says_so(self, real_client):
        client, svc = real_client
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call("vectr_distill", {}, svc)
        assert result["isError"] is False
        assert "No arcs pending distillation." in result["content"][0]["text"]

    def test_dismiss_via_mcp_resolves_the_arc(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_distill", {"dismiss": [arc_id], "reason": "covered by note #1"}, svc,
        )
        assert result["isError"] is False
        assert f"Dismissed arcs [{arc_id}]" in result["content"][0]["text"]
        assert svc.count_arcs_pending_distill() == 0

    def test_dismiss_via_mcp_without_reason_is_a_caller_error(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call("vectr_distill", {"dismiss": [arc_id]}, svc)
        assert result["isError"] is True
        # the arc must remain pending — a rejected call writes nothing.
        assert svc.count_arcs_pending_distill() == 1


class TestRememberDistilledFromMcp:
    def test_vectr_remember_with_distilled_from_resolves_the_arc(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember",
            {"content": "flaky test needed -k filter fix", "distilled_from": [arc_id]},
            svc,
            session_id="s1",
        )
        assert result["isError"] is False
        assert f"Distilled arcs [{arc_id}]" in result["content"][0]["text"]
        assert svc.count_arcs_pending_distill() == 0

    def test_vectr_remember_without_distilled_from_has_no_distill_suffix(self, real_client):
        client, svc = real_client
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember", {"content": "an ordinary note"}, svc, session_id="s1",
        )
        assert result["isError"] is False
        assert "Distilled arcs" not in result["content"][0]["text"]

    def test_mixed_valid_and_non_integer_ids_resolves_the_valid_one_and_reports_the_rest(self, real_client):
        """Live repro: distilled_from=[<valid arc id>, "abc"] must not
        silently drop the ENTIRE batch — the note is written, the valid
        arc id IS resolved, and the non-integer entry is reported back
        rather than disappearing without a trace."""
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember",
            {"content": "flaky test needed -k filter fix", "distilled_from": [arc_id, "abc"]},
            svc,
            session_id="s1",
        )
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert f"Distilled arcs [{arc_id}]" in text
        assert "Ignored non-integer distilled_from entries: ['abc']" in text
        assert svc.count_arcs_pending_distill() == 0

    def test_bool_entries_are_excluded_never_treated_as_arc_ids(self, real_client):
        """`bool` is a subclass of `int` in Python — `isinstance(True, int)`
        is True and a naive int-type check would silently resolve arc id
        `1`/`0` for a caller-supplied `True`/`False`. Must be reported back
        as invalid instead."""
        client, svc = real_client
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember",
            {"content": "an ordinary note", "distilled_from": [True]},
            svc,
            session_id="s1",
        )
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "Distilled arcs" not in text
        assert "Ignored non-integer distilled_from entries: [True]" in text


class TestArcDistillNudgeLine:
    def test_boot_recall_includes_nudge_when_arcs_pending(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)

        text = svc.recall(boot=True)
        assert "1 command-discovery arcs are pending distillation" in text
        assert "vectr_distill()" in text

    def test_boot_recall_omits_nudge_when_no_arcs_pending(self, real_client):
        client, svc = real_client
        text = svc.recall(boot=True)
        assert "pending distillation" not in text

    def test_nudge_disappears_once_the_arc_is_resolved(self, real_client):
        client, svc = real_client
        _produce_one_pending_arc(client)
        arc_id = client.get("/v1/arcs").json()["arcs"][0]["id"]
        svc.resolve_arcs_dismissed([arc_id], "not useful")

        text = svc.recall(boot=True)
        assert "pending distillation" not in text


class TestDistillRenderCaps:
    """Config §6 (agent/config.yaml `episodes.distill_max_arcs_rendered`/
    `episodes.distill_render_token_cap`) — both caps are read via
    agent/config.py, never hardcoded in the render path."""

    def test_render_caps_at_distill_max_arcs_rendered(self, real_client):
        from agent.config import EPISODES_DISTILL_MAX_ARCS_RENDERED
        from integrations.mcp_server import handle_tools_call

        client, svc = real_client
        now = time_module.time()
        extra = 2
        for i in range(EPISODES_DISTILL_MAX_ARCS_RENDERED + extra):
            _insert_arc(svc._episode_store, svc._workspace_root, ts=now - i)

        result = handle_tools_call("vectr_distill", {}, svc)
        text = result["content"][0]["text"]
        assert text.count("[arc #") == EPISODES_DISTILL_MAX_ARCS_RENDERED
        assert f"({extra} more pending arc(s) not shown" in text

    def test_render_trims_under_a_tiny_token_cap_with_explicit_truncation_indicator(self, real_client, monkeypatch):
        """Under the shipped default (2000 tokens) 10 small arc blocks never
        get close to the cap, so this branch of `_format_pending_arcs`
        (`used_tokens + block_tokens > EPISODES_DISTILL_RENDER_TOKEN_CAP`)
        never fires in the other tests above. Force it with a tiny
        override and assert the render is trimmed WITH the same explicit
        "N more pending arc(s) not shown" indicator the max-arcs-rendered
        cap uses — never a silent cut."""
        import integrations.mcp_server._dispatch as dispatch_module
        from integrations.mcp_server import handle_tools_call

        client, svc = real_client
        now = time_module.time()
        num_arcs = 5
        for i in range(num_arcs):
            _insert_arc(svc._episode_store, svc._workspace_root, ts=now - i)

        # Small enough that only the first (always-rendered) block fits;
        # large enough that the header alone doesn't already exceed it.
        monkeypatch.setattr(dispatch_module, "EPISODES_DISTILL_RENDER_TOKEN_CAP", 1)

        result = handle_tools_call("vectr_distill", {}, svc)
        text = result["content"][0]["text"]
        rendered = text.count("[arc #")
        assert 0 < rendered < num_arcs
        remaining = num_arcs - rendered
        assert f"({remaining} more pending arc(s) not shown" in text

    def test_config_values_loaded_from_yaml_not_hardcoded_defaults(self):
        """Sanity that agent/config.py actually exports these two keys
        (agent/config.yaml `episodes.distill_max_arcs_rendered` = 10,
        `episodes.distill_render_token_cap` = 2000) rather than silently
        falling back to some in-code default — a missing key raises
        KeyError at import per this repo's config-access rule, so a
        successful import here already proves the keys exist; this also
        pins the shipped values so an accidental edit is caught."""
        from agent.config import (
            EPISODES_DISTILL_MAX_ARCS_RENDERED,
            EPISODES_DISTILL_RENDER_TOKEN_CAP,
        )
        assert isinstance(EPISODES_DISTILL_MAX_ARCS_RENDERED, int)
        assert isinstance(EPISODES_DISTILL_RENDER_TOKEN_CAP, int)
        assert EPISODES_DISTILL_MAX_ARCS_RENDERED == 10
        assert EPISODES_DISTILL_RENDER_TOKEN_CAP == 2000


class TestVectrDistillToolRegistration:
    def test_registered_in_mcp_tools_with_non_empty_description(self):
        from integrations.mcp_server import MCP_TOOLS

        tool = next(t for t in MCP_TOOLS if t["name"] == "vectr_distill")
        assert tool["description"]
        assert "dismiss" in tool["inputSchema"]["properties"]
        assert "reason" in tool["inputSchema"]["properties"]

    def test_always_visible_like_vectr_remember(self):
        """Placed in `_MEMORY_WRITE_TOOLS` (never the gated `_MEMORY_TOOLS`)
        — the session-start nudge line promises it is callable regardless
        of note count."""
        from integrations.mcp_server import _MEMORY_WRITE_TOOLS

        write_names = {t["name"] for t in _MEMORY_WRITE_TOOLS}
        assert "vectr_distill" in write_names

    def test_phase1_ready_no_embedder_or_index_required(self):
        from integrations.mcp_server._schemas import MEMORY_READY_TOOLS

        assert "vectr_distill" in MEMORY_READY_TOOLS
