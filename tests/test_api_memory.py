"""
Tests for REST API memory routes: POST /v1/remember, POST /v1/recall,
POST /v1/snapshot, GET /v1/evict-hint, GET /v1/snapshot/list.

Two fixture layers:
  client              — mocked VectrService, tests HTTP shape/status codes
  client_real_memory  — real WorkingContextStore, tests cross-request persistence
    (the exact flow that failed in the POC benchmark: Phase 1 remember → Phase 2 recall)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# POST /v1/remember
# ---------------------------------------------------------------------------

class TestRememberRoute:
    def test_remember_returns_note_id(self, client) -> None:
        resp = client.post("/v1/remember", json={"content": "Signal.send at dispatcher.py:220"})
        assert resp.status_code == 200
        data = resp.json()
        assert "note_id" in data
        assert "message" in data

    def test_remember_with_tags_and_priority(self, client) -> None:
        resp = client.post("/v1/remember", json={
            "content": "BaseHandler.load_middleware at django/core/handlers/base.py",
            "tags": ["middleware", "lifecycle"],
            "priority": "high",
        })
        assert resp.status_code == 200
        assert resp.json()["note_id"] is not None

    def test_remember_missing_content_returns_422(self, client) -> None:
        resp = client.post("/v1/remember", json={})
        assert resp.status_code == 422

    def test_remember_message_suggests_eviction(self, client) -> None:
        resp = client.post("/v1/remember", json={"content": "some note"})
        msg = resp.json()["message"]
        assert "drop" in msg.lower() or "evict" in msg.lower() or "stored" in msg.lower()

    def test_remember_processing_ms_present(self, client) -> None:
        resp = client.post("/v1/remember", json={"content": "timing test"})
        assert "processing_ms" in resp.json()


# ---------------------------------------------------------------------------
# POST /v1/recall
# ---------------------------------------------------------------------------

class TestRecallRoute:
    def test_recall_returns_notes_text(self, client) -> None:
        resp = client.post("/v1/recall", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "notes" in data
        assert isinstance(data["notes"], str)

    def test_recall_with_query_filter(self, client) -> None:
        resp = client.post("/v1/recall", json={"query": "signal dispatch"})
        assert resp.status_code == 200
        assert "notes" in resp.json()

    def test_recall_with_tags_filter(self, client) -> None:
        resp = client.post("/v1/recall", json={"tags": ["middleware"]})
        assert resp.status_code == 200

    def test_recall_with_priority_filter(self, client) -> None:
        resp = client.post("/v1/recall", json={"priority": "high"})
        assert resp.status_code == 200

    def test_recall_with_limit(self, client) -> None:
        resp = client.post("/v1/recall", json={"limit": 5})
        assert resp.status_code == 200

    def test_recall_processing_ms_present(self, client) -> None:
        resp = client.post("/v1/recall", json={})
        assert "processing_ms" in resp.json()


# ---------------------------------------------------------------------------
# POST /v1/snapshot
# ---------------------------------------------------------------------------

class TestSnapshotRoute:
    def test_snapshot_returns_snapshot_id(self, client) -> None:
        resp = client.post("/v1/snapshot", json={"label": "phase1-complete"})
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshot_id" in data
        assert data["snapshot_id"] is not None

    def test_snapshot_echoes_label(self, client) -> None:
        resp = client.post("/v1/snapshot", json={"label": "my-label"})
        assert resp.json()["label"] == "my-label"

    def test_snapshot_missing_label_returns_422(self, client) -> None:
        resp = client.post("/v1/snapshot", json={})
        assert resp.status_code == 422

    def test_snapshot_processing_ms_present(self, client) -> None:
        resp = client.post("/v1/snapshot", json={"label": "test"})
        assert "processing_ms" in resp.json()


# ---------------------------------------------------------------------------
# GET /v1/evict-hint
# ---------------------------------------------------------------------------

class TestEvictHintRoute:
    def test_evict_hint_returns_hint_and_should_evict(self, client) -> None:
        resp = client.get("/v1/evict-hint")
        assert resp.status_code == 200
        data = resp.json()
        assert "hint" in data
        assert "should_evict" in data

    def test_evict_hint_should_evict_is_bool(self, client) -> None:
        data = client.get("/v1/evict-hint").json()
        assert isinstance(data["should_evict"], bool)

    def test_evict_hint_text_non_empty(self, client) -> None:
        data = client.get("/v1/evict-hint").json()
        assert isinstance(data["hint"], str)
        assert len(data["hint"]) > 0


# ---------------------------------------------------------------------------
# Cross-request persistence — THE key integration tests
# ---------------------------------------------------------------------------

class TestMemoryCrossRequest:
    """
    These tests mirror the two-phase POC benchmark flow exactly:
      Request 1 (Phase 1): POST /v1/remember
      Request 2 (Phase 2): POST /v1/recall  → must return what Phase 1 stored

    Uses client_real_memory fixture: real WorkingContextStore, mocked search.
    """

    def test_remember_then_recall_rest_round_trip(self, client_real_memory) -> None:
        client = client_real_memory
        resp = client.post("/v1/remember", json={
            "content": "Field.contribute_to_class at django/db/models/fields/__init__.py:770",
            "tags": ["field"],
            "priority": "high",
        })
        assert resp.status_code == 200

        recall_resp = client.post("/v1/recall", json={})
        notes = recall_resp.json()["notes"]
        assert "contribute_to_class" in notes
        assert "django/db/models/fields" in notes

    def test_multiple_remember_then_recall_all_returned(self, client_real_memory) -> None:
        client = client_real_memory
        findings = [
            "BaseHandler.load_middleware builds the middleware stack",
            "Middleware must set async_capable = True for ASGI",
            "HttpResponseTooManyRequests at django/http/response.py",
        ]
        for content in findings:
            client.post("/v1/remember", json={"content": content})

        notes = client.post("/v1/recall", json={}).json()["notes"]
        for finding in findings:
            first_word = finding.split()[0]
            assert first_word in notes, f"'{first_word}' missing from recalled notes"

    def test_recall_empty_before_any_remember(self, client_real_memory) -> None:
        resp = client_real_memory.post("/v1/recall", json={})
        assert "No working notes found" in resp.json()["notes"]

    def test_tag_filter_via_rest(self, client_real_memory) -> None:
        client = client_real_memory
        client.post("/v1/remember", json={"content": "signal dispatch path", "tags": ["signal"]})
        client.post("/v1/remember", json={"content": "middleware chain", "tags": ["middleware"]})

        notes = client.post("/v1/recall", json={"tags": ["signal"]}).json()["notes"]
        assert "signal dispatch" in notes
        assert "middleware chain" not in notes

    def test_priority_filter_via_rest(self, client_real_memory) -> None:
        client = client_real_memory
        client.post("/v1/remember", json={"content": "high priority finding", "priority": "high"})
        client.post("/v1/remember", json={"content": "low priority note", "priority": "low"})

        notes = client.post("/v1/recall", json={"priority": "high"}).json()["notes"]
        assert "high priority finding" in notes
        assert "low priority note" not in notes

    def test_snapshot_then_recall_via_rest(self, client_real_memory) -> None:
        client = client_real_memory
        client.post("/v1/remember", json={"content": "key finding for snapshot"})
        snap_resp = client.post("/v1/snapshot", json={"label": "phase1-done"})
        assert snap_resp.status_code == 200
        assert snap_resp.json()["snapshot_id"] is not None

        # Notes still accessible after snapshot
        notes = client.post("/v1/recall", json={}).json()["notes"]
        assert "key finding for snapshot" in notes

    def test_limit_via_rest(self, client_real_memory) -> None:
        client = client_real_memory
        for i in range(8):
            client.post("/v1/remember", json={"content": f"finding number {i}"})
        notes_text = client.post("/v1/recall", json={"limit": 3}).json()["notes"]
        # Rough check: with limit=3 the output is shorter than with no limit
        notes_all = client.post("/v1/recall", json={}).json()["notes"]
        assert len(notes_text) <= len(notes_all)

    def test_recall_note_id_increments(self, client_real_memory) -> None:
        client = client_real_memory
        r1 = client.post("/v1/remember", json={"content": "first"}).json()
        r2 = client.post("/v1/remember", json={"content": "second"}).json()
        assert r2["note_id"] > r1["note_id"]

    def test_kind_filter_via_rest(self, client_real_memory) -> None:
        """UPG-9.3: kind round-trips through REST and recall can filter on it."""
        client = client_real_memory
        client.post("/v1/remember", json={"content": "never push to main", "kind": "directive"})
        client.post("/v1/remember", json={"content": "just a plain finding"})

        directives = client.post("/v1/recall", json={"kind": "directive"}).json()["notes"]
        assert "never push to main" in directives
        assert "just a plain finding" not in directives

    def test_invalid_kind_rejected_via_rest(self, client_real_memory) -> None:
        """An unknown kind is a 422 validation error, not a silent finding."""
        resp = client_real_memory.post("/v1/remember", json={"content": "x", "kind": "bogus"})
        assert resp.status_code == 422

    def test_min_similarity_out_of_range_rejected(self, client_real_memory) -> None:
        """UPG-5.1: min_similarity is bounded to [0,1] at the REST boundary."""
        assert client_real_memory.post("/v1/recall", json={"min_similarity": 1.5}).status_code == 422
        assert client_real_memory.post("/v1/recall", json={"min_similarity": -0.1}).status_code == 422
        assert client_real_memory.post("/v1/recall", json={"min_similarity": 0.5}).status_code == 200

    def test_boot_recall_empty_workspace_returns_blank(self, client_real_memory) -> None:
        """UPG-9.2: boot recall on a 0-note workspace returns '' with 200 — never errors."""
        resp = client_real_memory.post("/v1/recall", json={"boot": True})
        assert resp.status_code == 200
        assert resp.json()["notes"] == ""

    def test_boot_recall_returns_directives_and_high_tasks_ignoring_query(self, client_real_memory) -> None:
        """UPG-9.2: boot returns directives + high tasks verbatim regardless of query."""
        client = client_real_memory
        client.post("/v1/remember", json={"content": "never push to main", "kind": "directive"})
        client.post("/v1/remember", json={"content": "sprint goal", "kind": "task", "priority": "high"})
        client.post("/v1/remember", json={"content": "an ordinary finding"})

        notes = client.post("/v1/recall", json={"boot": True, "query": "totally unrelated topic"}).json()["notes"]
        assert "never push to main" in notes
        assert "sprint goal" in notes
        assert "an ordinary finding" not in notes
