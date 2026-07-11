"""Runtime settings for proactive context (UPG-PRO-6 / UPG-PRO-16).

Layers deployment/runtime env vars over the bundled `config.yaml` defaults
(exposed as constants in `agent/config.py`), matching vectr's established split:
product-behaviour defaults live in yaml, runtime toggles in env. Nothing here is
persisted.

Also owns the localhost-only enforcement (design §10): proactive context — proxy
included — is refused whenever the daemon/proxy is bound beyond loopback (the
team / shared-instance signature), because it reads the conversation, the most
sensitive data on the machine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from agent import config as _c


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw


@dataclass(frozen=True)
class ProactiveSettings:
    """Fully-resolved proactive-context settings (env over yaml defaults)."""

    enabled: bool
    min_similarity: float
    max_items_per_event: int
    max_chars_per_event: int
    cooldown_items: int

    matcher_structural_note: bool
    matcher_semantic_note: bool
    matcher_code_search: bool

    proxy_enabled: bool
    proxy_host: str
    proxy_port: int
    proxy_upstream_base_url: str
    proxy_connect_timeout_s: float
    proxy_read_timeout_s: float
    proxy_inject: bool
    proxy_inject_budget_ms: int

    cache_enabled: bool
    cache_max_entries: int
    cache_ttl_seconds: float
    cache_similarity_threshold: float

    response_cache_enabled: bool
    response_cache_ttl_seconds: float
    response_cache_max_entries: int

    @classmethod
    def from_env(cls) -> "ProactiveSettings":
        return cls(
            enabled=_env_bool("VECTR_PROACTIVE", _c.PROACTIVE_ENABLED),
            min_similarity=_env_float(
                "VECTR_PROACTIVE_MIN_SIMILARITY", _c.PROACTIVE_MIN_SIMILARITY
            ),
            max_items_per_event=_env_int(
                "VECTR_PROACTIVE_MAX_ITEMS", _c.PROACTIVE_MAX_ITEMS_PER_EVENT
            ),
            max_chars_per_event=_env_int(
                "VECTR_PROACTIVE_MAX_CHARS", _c.PROACTIVE_MAX_CHARS_PER_EVENT
            ),
            cooldown_items=_env_int(
                "VECTR_PROACTIVE_COOLDOWN", _c.PROACTIVE_COOLDOWN_ITEMS
            ),
            matcher_structural_note=_env_bool(
                "VECTR_PROACTIVE_MATCH_STRUCTURAL", _c.PROACTIVE_MATCHER_STRUCTURAL_NOTE
            ),
            matcher_semantic_note=_env_bool(
                "VECTR_PROACTIVE_MATCH_SEMANTIC", _c.PROACTIVE_MATCHER_SEMANTIC_NOTE
            ),
            matcher_code_search=_env_bool(
                "VECTR_PROACTIVE_MATCH_CODE", _c.PROACTIVE_MATCHER_CODE_SEARCH
            ),
            proxy_enabled=_env_bool("VECTR_PROACTIVE_PROXY", _c.PROACTIVE_PROXY_ENABLED),
            proxy_host=_env_str("VECTR_PROACTIVE_PROXY_HOST", _c.PROACTIVE_PROXY_HOST),
            proxy_port=_env_int("VECTR_PROACTIVE_PROXY_PORT", _c.PROACTIVE_PROXY_PORT),
            proxy_upstream_base_url=_env_str(
                "VECTR_PROACTIVE_PROXY_UPSTREAM", _c.PROACTIVE_PROXY_UPSTREAM_BASE_URL
            ),
            proxy_connect_timeout_s=_env_float(
                "VECTR_PROACTIVE_PROXY_CONNECT_TIMEOUT", _c.PROACTIVE_PROXY_CONNECT_TIMEOUT_S
            ),
            proxy_read_timeout_s=_env_float(
                "VECTR_PROACTIVE_PROXY_READ_TIMEOUT", _c.PROACTIVE_PROXY_READ_TIMEOUT_S
            ),
            proxy_inject=_env_bool("VECTR_PROACTIVE_PROXY_INJECT", _c.PROACTIVE_PROXY_INJECT),
            proxy_inject_budget_ms=_env_int(
                "VECTR_PROACTIVE_PROXY_INJECT_BUDGET_MS", _c.PROACTIVE_PROXY_INJECT_BUDGET_MS
            ),
            cache_enabled=_env_bool("VECTR_PROACTIVE_CACHE", _c.PROACTIVE_CACHE_ENABLED),
            cache_max_entries=_env_int(
                "VECTR_PROACTIVE_CACHE_MAX_ENTRIES", _c.PROACTIVE_CACHE_MAX_ENTRIES
            ),
            cache_ttl_seconds=_env_float(
                "VECTR_PROACTIVE_CACHE_TTL", _c.PROACTIVE_CACHE_TTL_SECONDS
            ),
            cache_similarity_threshold=_env_float(
                "VECTR_PROACTIVE_CACHE_SIMILARITY", _c.PROACTIVE_CACHE_SIMILARITY_THRESHOLD
            ),
            response_cache_enabled=_env_bool(
                "VECTR_PROACTIVE_RESPONSE_CACHE", _c.PROACTIVE_RESPONSE_CACHE_ENABLED
            ),
            response_cache_ttl_seconds=_env_float(
                "VECTR_PROACTIVE_RESPONSE_CACHE_TTL", _c.PROACTIVE_RESPONSE_CACHE_TTL_SECONDS
            ),
            response_cache_max_entries=_env_int(
                "VECTR_PROACTIVE_RESPONSE_CACHE_MAX_ENTRIES",
                _c.PROACTIVE_RESPONSE_CACHE_MAX_ENTRIES,
            ),
        )


class ProactiveRefused(RuntimeError):
    """Raised when proactive context is explicitly enabled under a non-loopback
    (team / shared-instance) bind — a fail-closed refusal, never silent."""


def _is_loopback(host: str) -> bool:
    """Loopback check — reuses the daemon's bind-guard helper rather than
    forking a second implementation (imported lazily to avoid an import cycle
    with main.py, which imports this package for `vectr proxy`)."""
    from main import _is_loopback_host

    return _is_loopback_host(host)


def proactive_enabled(bind_host: str, config_enabled: bool, api_key: str | None = None) -> bool:
    """Two-gate localhost enforcement (design §10). Both gates must pass.

    Gate 1 (bind): a non-loopback bind (team-mode signature) forces proactive
    OFF regardless of config. Gate 2 (config): proactive runs only when
    explicitly enabled. `api_key` on a loopback bind (the shared-host hardening
    model) does NOT by itself disable proactive mode — the deciding factor is
    the non-loopback bind that defines team mode. Fail-closed: any ambiguity
    resolves to OFF.
    """
    if not config_enabled:
        return False
    if not bind_host or not _is_loopback(bind_host):
        return False
    return True


def enforce_proactive_bind(bind_host: str, config_enabled: bool) -> None:
    """Fail-closed startup check. If proactive is explicitly enabled but the
    bind is non-loopback, raise `ProactiveRefused` with an actionable message,
    naming the conflict — never start proactive under a shared/team posture."""
    if config_enabled and (not bind_host or not _is_loopback(bind_host)):
        raise ProactiveRefused(
            f"Refusing to run Proactive context under a non-loopback bind "
            f"({bind_host or '<unset>'}). Proactive context reads the local "
            f"conversation and is a solo/localhost-only feature; it is mutually "
            f"exclusive with team / shared-instance mode. Either bind to "
            f"127.0.0.1, or unset VECTR_PROACTIVE / proactive.enabled."
        )
