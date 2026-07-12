"""Settings + localhost-only enforcement tests (UPG-PRO-6)."""
from __future__ import annotations

import pytest

from agent.proactive.settings import (
    ProactiveRefused,
    ProactiveSettings,
    derive_provider_timeout_s,
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


# -- provider-timeout / outer-budget ordering invariant (UPG-PROXY-BUDGET-40MS) --


def _settings(**over):
    """Full ProactiveSettings from the bundled defaults, with fields overridden
    for the ordering test — avoids repeating every field just to vary one or two."""
    import dataclasses

    return dataclasses.replace(ProactiveSettings.from_env(), **over)


def test_bundled_default_derives_below_budget(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("VECTR_PROACTIVE"):
            monkeypatch.delenv(k, raising=False)
    # The shipped default (config.yaml) must already satisfy the invariant.
    s = ProactiveSettings.from_env()
    derived = derive_provider_timeout_s(s)
    budget_s = s.proxy_inject_budget_ms / 1000.0
    assert derived < budget_s
    assert s.proxy_inject_budget_ms >= 750  # UPG-PROXY-BUDGET-40MS: no longer self-defeating


def test_derived_timeout_strictly_below_budget_across_configs():
    for budget_ms, fraction, cap_s in [
        (750, 0.8, 2.0),
        (40, 0.8, 2.0),   # a tight, misconfigured-small budget
        (100, 1.0, 100.0),  # fraction/cap misconfigured to be permissive
        (5000, 0.5, 0.2),  # cap binds well below fraction * budget
    ]:
        s = _settings(
            proxy_inject_budget_ms=budget_ms,
            proxy_inject_provider_timeout_fraction=fraction,
            proxy_inject_provider_timeout_max_s=cap_s,
        )
        derived = derive_provider_timeout_s(s)
        budget_s = max(budget_ms, 1) / 1000.0
        assert derived < budget_s, (budget_ms, fraction, cap_s, derived)
        assert derived > 0


def test_derived_timeout_respects_fraction_and_cap():
    s = _settings(
        proxy_inject_budget_ms=1000, proxy_inject_provider_timeout_fraction=0.5,
        proxy_inject_provider_timeout_max_s=2.0,
    )
    # fraction * budget (0.5s) is below the cap (2.0s), so fraction governs.
    assert derive_provider_timeout_s(s) == pytest.approx(0.5)

    s = _settings(
        proxy_inject_budget_ms=10_000, proxy_inject_provider_timeout_fraction=0.9,
        proxy_inject_provider_timeout_max_s=1.0,
    )
    # fraction * budget (9s) exceeds the cap (1.0s), so the cap governs.
    assert derive_provider_timeout_s(s) == pytest.approx(1.0)
