"""Runtime settings for proactive context (UPG-PRO-6 / UPG-PRO-16).

Layers deployment/runtime env vars over the bundled `config.yaml` defaults
(exposed as constants in `agent/config.py`), matching vectr's established split:
product-behaviour defaults live in yaml, runtime toggles in env. Nothing here is
persisted.

Also owns the localhost-only enforcement (design §10). There are two
independent gates here, and only one of them is unconditional:

- `enforce_proactive_bind`/`proactive_enabled` are CONFIG-GATED: they only
  refuse a non-loopback bind when proactive is explicitly enabled
  (`config_enabled=True`). A non-loopback bind with proactive left off is a
  legitimate, supported team/shared-instance posture and must be allowed to
  start.
- `proactive_bind_is_loopback` is UNCONDITIONAL: the actual runtime gate
  inside `proactive_context` consults it before any config/channel check, so
  a non-loopback bind refuses proactive injection for every channel — the
  proxy channel included — regardless of the master switch or a client-
  supplied `channel` label. Proactive context reads the conversation, the
  most sensitive data on the machine, so this refusal cannot be a config
  toggle a caller can route around.
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
    proxy_inject_provider_timeout_fraction: float
    proxy_inject_provider_timeout_max_s: float

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
            proxy_inject_provider_timeout_fraction=_env_float(
                "VECTR_PROACTIVE_PROXY_INJECT_PROVIDER_TIMEOUT_FRACTION",
                _c.PROACTIVE_PROXY_INJECT_PROVIDER_TIMEOUT_FRACTION,
            ),
            proxy_inject_provider_timeout_max_s=_env_float(
                "VECTR_PROACTIVE_PROXY_INJECT_PROVIDER_TIMEOUT_MAX_S",
                _c.PROACTIVE_PROXY_INJECT_PROVIDER_TIMEOUT_MAX_S,
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


def derive_provider_timeout_s(settings: ProactiveSettings) -> float:
    """The daemon-provider's own httpx timeout for one injection round trip.

    Two timeouts guard `_maybe_inject`: this one, inside the provider's httpx
    call, and the proxy's outer `asyncio.wait_for(budget)` backstop. They must
    not be equal or inverted — if the outer backstop can fire at or before the
    provider's own timeout, `asyncio.wait_for` cancels the coroutine mid-flight
    on every slow daemon response, which the proxy can only record as an abrupt
    bypass (`inject_bypassed_error`, logged as a WARNING). If the provider's own
    timeout fires first instead, its `except` clause returns a clean
    `InjectionResult.empty()` — the proxy records a graceful `inject_skipped`
    with no warning, and forwarding still fails open.

    So the provider timeout is always derived as a strict fraction of the
    outer budget (config: `inject_provider_timeout_fraction`), capped at a
    sensible absolute ceiling (`inject_provider_timeout_max_s`) so a large
    budget never produces an unreasonably long per-request stall, and clamped
    a final time so it is always strictly below the outer budget regardless of
    how fraction/cap are configured.
    """
    budget_s = max(settings.proxy_inject_budget_ms, 1) / 1000.0
    derived = min(
        budget_s * settings.proxy_inject_provider_timeout_fraction,
        settings.proxy_inject_provider_timeout_max_s,
    )
    # Invariant clamp: strictly below the outer backstop no matter what
    # fraction/cap are configured to.
    return min(derived, budget_s * 0.95)


class ProactiveRefused(RuntimeError):
    """Raised when proactive context is explicitly enabled under a non-loopback
    (team / shared-instance) bind — a fail-closed refusal, never silent."""


def _is_loopback(host: str) -> bool:
    """Loopback check — reuses the daemon's bind-guard helper rather than
    forking a second implementation (imported lazily to avoid an import cycle
    with main.py, which imports this package for `vectr proxy`)."""
    from main import _is_loopback_host

    return _is_loopback_host(host)


def _current_bind_host() -> str:
    """The daemon's actual bind host for THIS process.

    `main.py`'s `_do_start` sets `VECTR_BIND_HOST` on the uvicorn subprocess
    env to the exact `--host` it launched with (UPG-PROXY-LOOPBACK-BYPASS) —
    the daemon process has no other way to learn its own bind address. When
    the var is absent (in-process/test usage, or a daemon started outside
    `_do_start`), fall back to `_do_start`'s own documented default bind,
    127.0.0.1 — never treat an unset value as non-loopback, or every
    in-process caller (tests, the MCP server sharing the daemon's process)
    would be spuriously refused."""
    return os.environ.get("VECTR_BIND_HOST", "").strip() or "127.0.0.1"


def proactive_bind_is_loopback() -> bool:
    """Unconditional, channel-independent bind check for the proactive
    injection gate (UPG-PROXY-LOOPBACK-BYPASS).

    Unlike `proactive_enabled`/`enforce_proactive_bind` below, this does NOT
    take a `config_enabled` argument and must never be skipped by one: it is
    the runtime refusal `proactive_context` consults BEFORE any config or
    `channel` branch, so a non-loopback bind refuses proactive injection —
    including the proxy channel's own launch-is-consent exemption from the
    master switch — for every caller. A non-loopback bind with proactive
    left off entirely is still a legitimate startup (see
    `enforce_proactive_bind`'s docstring); this function only governs
    whether proactive context is ever served, not whether the daemon may
    start."""
    return _is_loopback(_current_bind_host())


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
