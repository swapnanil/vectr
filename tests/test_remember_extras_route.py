"""Route-level REST coverage for `/v1/remember`'s additive extras fields
(`related`, `proxy_anchor_suggestions`) — app/routes.py dispatching
`VectrService.remember_with_extras()` (app/service.py) instead of the
untouched `remember()`.

Unlike tests/test_remember_extras.py (service-layer gating matrix), these
exercise the full HTTP -> routes -> VectrService -> WorkingContextStore /
ChromaDB stack via the `real_service_client` fixture, so the JSON body is
checked against REAL non-empty values, not a mocked stand-in — the same
mock-fidelity gap this task closed in tests/conftest.py's
`_base_mock_service()` (a bare MagicMock for `remember_with_extras` used to
leak a "<MagicMock ...>" repr into the confirmation `message` string; only
`test_remember_message_suggests_eviction`'s loose substring check hid it).
"""
from __future__ import annotations

import os

import pytest


class TestRememberRouteRelatedNotesReal:
    def test_related_populated_with_real_fields_on_near_duplicate(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})

        content = "shared exact content for REST near-duplicate similarity"
        first = client.post(
            "/v1/remember", json={"content": content, "priority": "high", "kind": "gotcha"}
        )
        assert first.status_code == 200
        base_id = first.json()["note_id"]

        second = client.post("/v1/remember", json={"content": content})
        assert second.status_code == 200
        data = second.json()

        assert data["related"], "expected a non-empty related list for a near-duplicate write"
        hit = data["related"][0]
        assert hit["note_id"] == base_id
        assert isinstance(hit["title"], str)
        assert isinstance(hit["similarity"], float)
        assert isinstance(hit["created_at"], float)
        # kind and priority must round-trip with their REAL values, not merely
        # be present as strings: they are what lets a caller tell a high-priority
        # gotcha it may be contradicting from an incidental low-priority note.
        assert hit["kind"] == "gotcha"
        assert hit["priority"] == "high"

        client.post("/v1/memory/clear", json={})

    def test_related_absent_when_disabled(self, real_service_client, monkeypatch) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})
        monkeypatch.setattr("app.service.MEMORY_WRITE_RELATED_ENABLED", False)

        content = "another shared content block for REST gating"
        client.post("/v1/remember", json={"content": content})
        resp = client.post("/v1/remember", json={"content": content})
        assert resp.json()["related"] == []

        client.post("/v1/memory/clear", json={})


class TestRememberRouteProxyAnchorSuggestionsReal:
    def test_proxy_anchors_populated_with_real_dockerfile(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})
        dockerfile = os.path.join(ws, "Dockerfile")
        with open(dockerfile, "w") as f:
            f.write("FROM python:3.12\n")
        try:
            resp = client.post("/v1/remember", json={
                "content": "deploy note via REST", "kind": "operational",
            })
            assert resp.status_code == 200
            assert "Dockerfile" in resp.json()["proxy_anchor_suggestions"]
        finally:
            os.remove(dockerfile)
            client.post("/v1/memory/clear", json={})

    def test_proxy_anchors_empty_when_anchors_supplied(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})
        dockerfile = os.path.join(ws, "Dockerfile")
        with open(dockerfile, "w") as f:
            f.write("FROM python:3.12\n")
        try:
            resp = client.post("/v1/remember", json={
                "content": "deploy note via REST", "kind": "operational",
                "anchors": ["Dockerfile"],
            })
            assert resp.status_code == 200
            assert resp.json()["proxy_anchor_suggestions"] == []
        finally:
            os.remove(dockerfile)
            client.post("/v1/memory/clear", json={})

    def test_proxy_anchors_empty_for_non_operational_kind(self, real_service_client) -> None:
        client, svc, ws = real_service_client
        client.post("/v1/memory/clear", json={})
        dockerfile = os.path.join(ws, "Dockerfile")
        with open(dockerfile, "w") as f:
            f.write("FROM python:3.12\n")
        try:
            resp = client.post("/v1/remember", json={
                "content": "a finding via REST", "kind": "finding",
            })
            assert resp.status_code == 200
            assert resp.json()["proxy_anchor_suggestions"] == []
        finally:
            os.remove(dockerfile)
            client.post("/v1/memory/clear", json={})
