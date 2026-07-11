"""Settings + localhost-only enforcement tests (UPG-PRO-6)."""
from __future__ import annotations

import pytest

from agent.proactive.settings import (
    ProactiveRefused,
    ProactiveSettings,
    enforce_proactive_bind,
    proactive_enabled,
)


def test_defaults_disabled(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("VECTR_PROACTIVE"):
            monkeypatch.delenv(k, raising=False)
    s = ProactiveSettings.from_env()
    assert s.enabled is False
    assert s.proxy_inject is True
    assert s.cache_similarity_threshold == 1.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("VECTR_PROACTIVE", "1")
    monkeypatch.setenv("VECTR_PROACTIVE_MIN_SIMILARITY", "0.5")
    monkeypatch.setenv("VECTR_PROACTIVE_MAX_ITEMS", "7")
    monkeypatch.setenv("VECTR_PROACTIVE_PROXY_PORT", "19999")
    monkeypatch.setenv("VECTR_PROACTIVE_CACHE", "true")
    s = ProactiveSettings.from_env()
    assert s.enabled is True
    assert s.min_similarity == 0.5
    assert s.max_items_per_event == 7
    assert s.proxy_port == 19999
    assert s.cache_enabled is True


def test_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("VECTR_PROACTIVE_MAX_ITEMS", "not-an-int")
    s = ProactiveSettings.from_env()
    assert s.max_items_per_event == 3  # bundled default, not a crash


def test_proactive_enabled_two_gates():
    # Gate 1: loopback required. Gate 2: config enabled.
    assert proactive_enabled("127.0.0.1", True) is True
    assert proactive_enabled("localhost", True) is True
    assert proactive_enabled("0.0.0.0", True) is False   # non-loopback bind
    assert proactive_enabled("10.0.0.5", True) is False
    assert proactive_enabled("127.0.0.1", False) is False  # config off
    # An API key on a loopback bind does NOT disable it.
    assert proactive_enabled("127.0.0.1", True, api_key="secret") is True


def test_enforce_refuses_non_loopback_when_enabled():
    with pytest.raises(ProactiveRefused):
        enforce_proactive_bind("0.0.0.0", True)
    # Loopback is fine; config-off is fine even on non-loopback (nothing to refuse).
    enforce_proactive_bind("127.0.0.1", True)
    enforce_proactive_bind("0.0.0.0", False)
