"""
Shared fixtures for the vectr test suite.

Key fixture: `indexer` — real CodeIndexer backed by an in-memory-style ChromaDB
(stored in tmp_path) with the heavy sentence-transformers model replaced by a
deterministic dummy embedder. No model download required; tests run in <1 s.
"""
from __future__ import annotations

import concurrent.futures
import errno
import os
import sys
import textwrap
import tempfile
import threading
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

# langchain_community 0.4+ removed chat_models.vertexai (moved to langchain-google-vertexai).
# Stub it so ragas can be imported without requiring the VertexAI extras.
if "langchain_community.chat_models.vertexai" not in sys.modules:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["langchain_community.chat_models.vertexai"] = MagicMock()

# Disable cross-encoder reranker before any searcher import so tests never
# trigger a model download.
os.environ["VECTR_RERANKER_MODEL"] = ""
# ChromaDB's built-in posthog telemetry phones home from a background thread
# on first PersistentClient construction; with the socket guard active that
# egress would be refused (correctly), and we don't want even the noise.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
# tiktoken downloads its BPE files (cl100k_base.tiktoken) from
# openaipublic.blob.core.windows.net on the first get_encoding() call and
# caches them keyed by TIKTOKEN_CACHE_DIR. A developer machine usually has a
# warm cache, so that fetch is invisible locally while CI, with a cold one,
# takes the network — which is exactly how two tests in test_t2_driver.py
# passed here and failed in CI run 32962144004 once the socket guard landed.
# Pointing the cache at a per-run temp dir makes the first call attempt the
# download in BOTH environments, so the guard refuses it identically and this
# class cannot hide behind a warm cache again. Nothing in the unit suite wants
# a real tokenizer; the two call sites stub the counter.
os.environ["TIKTOKEN_CACHE_DIR"] = tempfile.mkdtemp(prefix="vectr-tiktoken-cold-")

import numpy as np
import pytest
from fastapi.testclient import TestClient

# Saved at collection time (before any fixture patches app.service.VectrService).
# real_service_client patches that name session-wide; test_ragas_eval uses this
# reference so it always gets the real constructor, not the mock.
from app.service import VectrService as _RealVectrService


# ---------------------------------------------------------------------------
# UPG-TEST-CACHE-ISOLATION: redirect the whole session off the real user
# cache (~/.cache/vectr).
#
# Every product cache-path resolution — VectrService's workspace DB dir
# (app/service.py:_default_db_dir), CodeIndexer's own db_path fallback
# (agent/indexer/_core.py), and the Hugging Face model cache (agent/indexer/
# _types.py, agent/searcher.py) — goes through agent.config.vectr_cache_root(),
# which reads VECTR_CACHE_DIR at CALL time (never cached at import). Setting
# that env var once here, before the first test runs, is therefore enough to
# isolate every VectrService(workspace_root=str(tmp_path)) / CodeIndexer(...)
# construction anywhere in the suite — hundreds of call sites across dozens
# of test files, present and future — with no change needed to any of them:
# this fixture is autouse, so no test signature has to name it.
#
# Before vectr_cache_root() existed, each of those constructions used a
# per-test-unique tmp_path as its workspace root, hashed to a unique cache
# slug, and wrote a real directory under ~/.cache/vectr for every test that
# ran — a 2026-07-20 cleanup swept ~4,000 such junk dirs (~550 MB) left by
# prior suite runs.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _isolated_cache_root(tmp_path_factory) -> Generator[Path, None, None]:
    from agent.config import CACHE_DIR_ENV

    cache_dir = tmp_path_factory.mktemp("vectr_cache_root")
    # A session-scoped fixture cannot request the function-scoped `monkeypatch`
    # fixture, so this uses pytest's own MonkeyPatch class directly — the
    # documented pattern for env-var patching outside a test function
    # (https://docs.pytest.org/en/stable/how-to/monkeypatch.html).
    mp = pytest.MonkeyPatch()
    mp.setenv(CACHE_DIR_ENV, str(cache_dir))
    try:
        yield cache_dir
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# UPG-TEST-REGISTRY-NOT-ISOLATED: redirect the session off the developer's
# real instance registry (~/.vectr/instances.json).
#
# `REGISTRY_PATH = Path.home() / ".vectr" / "instances.json"`
# (agent/instance_registry.py:16) sits OUTSIDE VECTR_CACHE_DIR, so the
# `_isolated_cache_root` fixture above does not cover it. Every product call
# site that builds `InstanceRegistry()` without an explicit `registry_path=`
# — main.py's cmd_start / cmd_status / cmd_stop / cmd_restart and
# agent.cache_maintenance.live_instance_slugs — would otherwise read and
# write the developer's real file. Two distinct harms: (1) a unit test can
# observe the live daemon's entry on port 8765 and have its verdict decided
# by the workstation (same class as UPG-TEST-LIVE-DAEMON-PORT-CONTAMINATION),
# and (2) a unit run can corrupt the user's real registry.
#
# Mechanical constraint (do not skip): `__init__` captures the module-level
# `REGISTRY_PATH` as its default-argument value at class definition. Patching
# the module attribute (`agent.instance_registry.REGISTRY_PATH = ...`) does
# NOTHING for `InstanceRegistry()` with no args — the default is already
# bound in the function's `__defaults__` tuple. The correct seam is to wrap
# `__init__` itself and inject the test path when the caller didn't. This is
# deliberately test-only: routing REGISTRY_PATH through vectr_cache_root()
# would be a product change (relocates a user-visible file) and needs its
# own gate and migration story.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _isolated_instance_registry(tmp_path_factory) -> Generator[Path, None, None]:
    from agent import instance_registry

    isolated_dir = tmp_path_factory.mktemp("vectr_instance_registry")
    isolated_path = isolated_dir / "instances.json"
    real_init = instance_registry.InstanceRegistry.__init__

    def _isolated_init(self, registry_path: Path = None) -> None:
        # `Path = None` (not the real default) so the sentinel is detectable:
        # an explicit `registry_path=` from a test or product call still wins
        # (no `is None` substitution), and a caller that genuinely wants the
        # developer's real path can still get it by passing
        # `registry_path=instance_registry.REGISTRY_PATH` — nothing here
        # forbids that, only patches the silent no-arg case.
        if registry_path is None:
            registry_path = isolated_path
        real_init(self, registry_path)

    # `_isolated_init.__defaults__` stays (None,) on purpose: that sentinel
    # is what tells the wrapper the caller didn't pass an explicit path.

    mp = pytest.MonkeyPatch()
    mp.setattr(instance_registry.InstanceRegistry, "__init__", _isolated_init)
    try:
        yield isolated_path
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# UPG-TEST-IDE-CONFIG-REAL-TREE-WRITE: the suite must never rewrite the
# developer's own editor config.
#
# VectrService takes `configure_ide: bool = True`, and at phase-2 startup that
# makes the service write .cursor/mcp.json, .vscode/mcp.json and
# .claude/settings.json into whatever workspace root it was given, pointing
# them at its own port. That is correct for a real daemon and wrong for a
# test: a test that legitimately indexes the real source tree (retrieval
# quality needs real code to search) would silently repoint the developer's
# editor at a port no one is listening on, and the damage outlives the run.
#
# One fixture did exactly that. The rule is cheap to enforce, so enforce it
# rather than relying on every future fixture author to remember
# `configure_ide=False`.
#
# The write is SUPPRESSED as well as recorded, because configure_all() catches
# Exception around each writer and only logs a warning — a raise here would be
# swallowed and the file written anyway. Recording and failing at teardown is
# what actually makes it visible.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _no_ide_config_writes_into_real_tree() -> Generator[None, None, None]:
    from integrations import vscode_bridge

    violations: list[str] = []
    real_merge = vscode_bridge._merge_json_file

    def guarded(path, payload, owned_keys=None, **kwargs):
        try:
            resolved = Path(path).resolve()
            inside = resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents
        except OSError:  # pragma: no cover - unresolvable path is not ours
            inside = False
        if inside:
            violations.append(str(resolved))
            return
        return real_merge(path, payload, owned_keys=owned_keys, **kwargs)

    vscode_bridge._merge_json_file = guarded
    try:
        yield
    finally:
        vscode_bridge._merge_json_file = real_merge
        if violations:
            listed = "\n  ".join(sorted(set(violations)))
            raise RuntimeError(
                "A test wrote editor MCP config into the real source tree:\n  "
                + listed
                + "\n\nConstruct the service with configure_ide=False, or point it "
                "at a tmp_path workspace. The writes above were suppressed."
            )


# ---------------------------------------------------------------------------
# UPG-TEST-LIVE-DAEMON-PORT-CONTAMINATION: suite-wide live-socket guard.
#
# Six tests were fixed at d49e339 for reaching toward what could be a real
# daemon's port; this guard closes the CLASS: no unit test may open a live
# TCP connection to a listening socket it doesn't own. Port 8765 serves the
# user's LIVE vectr session — a stale registry entry or a hardcoded default
# must never turn into a real probe against it.
#
# Mechanism: patch `socket.socket.connect`/`connect_ex` once per session.
# Every HTTP client in the tree funnels through these two methods (httpx →
# httpcore → socket.create_connection; urllib/requests likewise; and
# agent/hook_cli.py's stdlib client dials sockets directly), so one seam
# covers all of them without knowing which client a test happens to use.
#
# Verdicts per connection attempt:
#   * loopback port registered via allow_loopback_port()  → allowed through.
#   * other loopback ports                                → refused AND
#     recorded; a function-scoped autouse fixture fails the OWNING test
#     after it finishes (a connect attempt usually surfaces as an
#     "unreachable daemon" branch the test deliberately tolerates — failing
#     mid-test would misattribute it; failing after keeps the owner named).
#   * non-loopback addresses                              → refused outright,
#     unrecorded (telemetry-style egress from third-party libs is noise we
#     swallow rather than fail unrelated tests over).
#
# The refusal exception derives from ConnectionError (an OSError) so product
# code's broad "daemon unreachable" handlers convert it deterministically
# into exactly the verdict CI gets — tests keep their semantics, they just
# can't accidentally talk to a real listener anymore.
#
# Binding/listening is untouched: find_free_port probes, stub servers, and
# TIME_WAIT-reproduction tests all keep working. FastAPI's TestClient is
# in-process ASGI and opens no socket at all.
#
# Opt-out for `-m integration` runs (real model downloads need real
# network): VECTR_TEST_ALLOW_REAL_NETWORK=1.
# ---------------------------------------------------------------------------

_SOCKET_GUARD_ENV = "VECTR_TEST_ALLOW_REAL_NETWORK"

_allowed_ports_lock = threading.Lock()
_allowed_loopback_ports: set[int] = set()

_violations_lock = threading.Lock()
_loopback_violations: list[tuple[str, int]] = []


class _BlockedConnectionAttempt(ConnectionError):
    """A unit-test connection attempt the socket guard refused."""


@contextmanager
def allow_loopback_port(port: int) -> Generator[None, None, None]:
    """Allow live connections to this loopback `port` for the duration of the
    block. For tests that own a local stub server on an ephemeral port (e.g.
    tests/test_hook_cli_parity.py's stub daemon) — register it here so the
    suite-wide socket guard lets its own clients through."""
    with _allowed_ports_lock:
        _allowed_loopback_ports.add(int(port))
    try:
        yield
    finally:
        with _allowed_ports_lock:
            _allowed_loopback_ports.discard(int(port))


def _inspect_connect_address(address: object) -> tuple[str, str, int] | None:
    """Classify a connect() address as ("allowed" | "loopback" | "remote", host,
    port), or None when it isn't an IP-style connect target at all (AF_UNIX
    paths, malformed tuples) — those are passed through untouched."""
    if not isinstance(address, tuple) or len(address) < 2:
        return None
    host, port = address[0], address[1]
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    if isinstance(host, bytes):
        host = host.decode("utf-8", errors="replace")
    if not isinstance(host, str):
        return None
    normalized = host.lower()
    is_loopback = (
        normalized == "localhost"
        or normalized.startswith("127.")
        or normalized in ("::1", "0:0:0:0:0:0:0:1")
    )
    if not is_loopback:
        return ("remote", host, port)
    with _allowed_ports_lock:
        if port in _allowed_loopback_ports:
            return ("allowed", host, port)
    return ("loopback", host, port)


def _summarize_violations(violations: list[tuple[str, int]]) -> str | None:
    """Human-facing failure message for recorded loopback violations, or None
    when the list is empty. Split out as a pure function so it can be tested
    without going through the autouse fixture's teardown."""
    if not violations:
        return None
    unique = sorted(set(violations))
    targets = ", ".join(f"{host}:{port}" for host, port in unique)
    return (
        f"this test attempted a live connection to {targets}. Unit tests must "
        "never talk to a real listening socket — the default port (8765) serves "
        "the user's live vectr daemon. Stub the probe instead: patch "
        "main._is_server_alive / main._wait_for_daemon_ready / httpx.get (see "
        "tests/test_main.py::_stub_version_skew_probe for the pattern), or, if "
        "the test owns a local stub server, wrap its ephemeral port with "
        f"tests.conftest.allow_loopback_port(...). ({_SOCKET_GUARD_ENV}=1 "
        "disables this guard for integration runs.)"
    )


def _current_loopback_violations() -> list[tuple[str, int]]:
    """Snapshot of violations recorded so far in the current test (for the
    guard's own tests in tests/test_suite_guards.py)."""
    with _violations_lock:
        return list(_loopback_violations)


@pytest.fixture(scope="session", autouse=True)
def _no_live_daemon_probes() -> Generator[None, None, None]:
    """Install the session-wide socket guard (see the block comment above)."""
    if os.environ.get(_SOCKET_GUARD_ENV) == "1":
        yield
        return

    import socket as _socket_module

    mp = pytest.MonkeyPatch()
    real_connect = _socket_module.socket.connect
    real_connect_ex = _socket_module.socket.connect_ex

    def _guarded_connect(self, address):
        info = _inspect_connect_address(address)
        if info is None or info[0] == "allowed":
            return real_connect(self, address)
        kind, host, port = info
        if kind == "loopback":
            with _violations_lock:
                _loopback_violations.append((host, port))
        raise _BlockedConnectionAttempt(
            f"unit test attempted a live connection to {host}:{port} — blocked "
            f"by the suite-wide socket guard ({_SOCKET_GUARD_ENV}=1 opts out)."
        )

    def _guarded_connect_ex(self, address):
        info = _inspect_connect_address(address)
        if info is None or info[0] == "allowed":
            return real_connect_ex(self, address)
        kind, host, port = info
        if kind == "loopback":
            with _violations_lock:
                _loopback_violations.append((host, port))
        # connect_ex contract: report failure as an errno, don't raise.
        return errno.ECONNREFUSED

    mp.setattr(_socket_module.socket, "connect", _guarded_connect)
    mp.setattr(_socket_module.socket, "connect_ex", _guarded_connect_ex)
    try:
        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _fail_on_unallowlisted_loopback_connect() -> Generator[None, None, None]:
    """Fail whichever test caused recorded loopback connect attempts — after
    the test body ran, so the violation names the right test even when the
    product code swallowed the refused connection as an ordinary
    'daemon unreachable' branch."""
    with _violations_lock:
        _loopback_violations.clear()
    yield
    with _violations_lock:
        violations = list(_loopback_violations)
        _loopback_violations.clear()
    message = _summarize_violations(violations)
    if message:
        pytest.fail(message, pytrace=False)


# ---------------------------------------------------------------------------
# UPG-TEST-REAL-EMBEDDER-DOWNLOAD-GUARD: the unit suite must never fetch a
# Hugging Face model. CI has hit real HTTP 429s because something reached for
# the hub; both product load sites (LocalEmbedProvider, _Reranker) route
# through agent.model_cache.load_with_offline_preference, whose cache-miss
# fallback constructs the model with local_files_only=False — i.e. downloads.
#
# Guard rule: refuse ONLY calls whose builder function is defined in product
# code (its __module__ lives under "agent."), captured the REAL
# SentenceTransformer/CrossEncoder class in its closure, and whose model
# is_model_cached reports as not locally cached — precisely the trajectory
# that hits the network. Everything else delegates to the real loader
# untouched:
#   * tests exercising the loader itself pass TEST-defined fake builders
#     (__module__ starts elsewhere) — unaffected;
#   * tests that stub SentenceTransformer/CrossEncoder or patch the loader
#     outright bypass this wrapper entirely (their own patch replaces ours,
#     and mock.patch restores ours afterwards);
#   * the session-scoped VECTR_CACHE_DIR isolation guarantees every real
#     model looks uncached in unit runs, so ANY product-path construction is
#     caught before torch even starts loading weights.
#
# A second layer backstops paths that skip our loader (e.g. direct
# SentenceTransformer construction): huggingface_hub.snapshot_download /
# hf_hub_download are wrapped to raise the same error. Known gap, accepted:
# _Reranker._load wraps its load in `except Exception` and degrades to
# "reranker disabled", so a backstop hit THERE degrades quietly instead of
# failing loudly — but the reranker is already hard-disabled in unit runs
# (VECTR_RERANKER_MODEL="" above), and LocalEmbedProvider.__init__ (the site
# behind the CI incident) has no such handler, so the realistic surface
# fails loudly.
#
# Opt-out for `-m integration` runs (which genuinely download/load models):
# VECTR_TEST_ALLOW_REAL_NETWORK=1 — the same flag the socket guard honors.
# ---------------------------------------------------------------------------


class RealModelDownloadBlocked(RuntimeError):
    """A unit test tried to make vectr fetch/load a real Hugging Face model."""


def _builder_closes_over_real_hf_class(build_fn) -> bool:
    """True when the builder captured the REAL sentence_transformers class in
    its closure. Product builders (`lambda local_only: SentenceTransformer(...)`
    inside LocalEmbedProvider/_Reranker) do; tests that stub
    SentenceTransformer/CrossEncoder produce builders whose closure holds the
    FAKE — a different object, whose __module__ is the test module (or
    unittest.mock), not "sentence_transformers". Keying off __name__ AND
    __module__ together means a fake can't dodge this by being named like the
    real class, and no torch-importing reference to the real class is needed
    for the comparison."""
    for cell in getattr(build_fn, "__closure__", None) or []:
        try:
            value = cell.cell_contents
        except ValueError:
            continue  # emptied cell
        if (
            getattr(value, "__name__", "") in ("SentenceTransformer", "CrossEncoder")
            and getattr(value, "__module__", "") == "sentence_transformers"
        ):
            return True
    return False


def _download_trajectory_detected(build_fn, model_name: str, cache_dir: str) -> bool:
    """True when letting the real loader run would attempt a network fetch:
    a product-defined builder that captured the real HF model class, for a
    model is_model_cached reports as NOT locally cached."""
    from agent import model_cache as _model_cache

    builder_module = getattr(build_fn, "__module__", "") or ""
    if not builder_module.startswith("agent."):
        return False  # test-defined builder — cannot be a product download path
    if not _builder_closes_over_real_hf_class(build_fn):
        return False  # stubbed construction — cannot reach the hub either way
    return not _model_cache.is_model_cached(model_name, cache_dir)


def _refuse_real_model(model_name: str) -> RealModelDownloadBlocked:
    return RealModelDownloadBlocked(
        f"unit test attempted to load the real Hugging Face model '{model_name}' "
        "(agent.model_cache.load_with_offline_preference cache-miss path). The "
        "unit suite must never download models — CI hits HTTP 429. Inject a "
        "deterministic provider instead: see tests/conftest._DummyEmbedProvider "
        "and the `indexer` fixture, or monkeypatch "
        "`agent.indexer.get_embed_provider`. If this test genuinely needs the "
        "real model, mark it @pytest.mark.integration and run the suite with "
        f"VECTR_TEST_ALLOW_REAL_NETWORK=1."
    )


@pytest.fixture(scope="session", autouse=True)
def _no_real_model_downloads() -> Generator[None, None, None]:
    if os.environ.get(_SOCKET_GUARD_ENV) == "1":
        yield
        return

    import huggingface_hub
    from agent import model_cache as _model_cache

    mp = pytest.MonkeyPatch()
    real_load = _model_cache.load_with_offline_preference
    real_snapshot_download = huggingface_hub.snapshot_download
    real_hf_hub_download = huggingface_hub.hf_hub_download

    def _guarded_load(build_fn, model_name, cache_dir):
        if _download_trajectory_detected(build_fn, model_name, cache_dir):
            raise _refuse_real_model(model_name)
        return real_load(build_fn, model_name, cache_dir)

    def _guarded_snapshot_download(*args, **kwargs):
        name = kwargs.get("repo_id") or (args[0] if args else "<unknown>")
        raise _refuse_real_model(str(name))

    def _guarded_hf_hub_download(*args, **kwargs):
        name = kwargs.get("repo_id") or (args[0] if args else "<unknown>")
        raise _refuse_real_model(str(name))

    mp.setattr(_model_cache, "load_with_offline_preference", _guarded_load)
    mp.setattr(huggingface_hub, "snapshot_download", _guarded_snapshot_download)
    mp.setattr(huggingface_hub, "hf_hub_download", _guarded_hf_hub_download)
    try:
        yield
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# UPG-PURPOSE-PASS-TEST-RACE: deterministic drain barrier for the deferred
# purpose-vector pass.
#
# index_workspace() hands purpose-vector upserts to a single-worker
# ThreadPoolExecutor and returns before they land (UPG-PURPOSE-PASS-
# DEFERRAL). Polling `purpose_vectors_pending` works but degrades silently:
# under machine load (e.g. a second full suite running concurrently —
# UPG-TEST-CONCURRENT-SUITE-FLAKE) a fixed-deadline poller expires while the
# pass is still mid-flight and the test proceeds to read a half-updated
# collection. This helper instead submits a sentinel to the SAME executor:
# because the pool has exactly one worker and FIFO ordering, the sentinel
# running means every previously submitted pass has FINISHED. A hang now
# raises a loud TimeoutError naming the mechanism instead of quietly
# proceeding past stale reads.
# ---------------------------------------------------------------------------


def wait_for_deferred_purpose_pass(idx, timeout: float = 30.0) -> None:
    """Block until all purpose-vector passes queued on `idx`'s deferred
    executor have completed; raise TimeoutError if they don't finish within
    `timeout` seconds. Safe to call when nothing was deferred (idle executor →
    returns immediately) or when the sync path ran instead (`_purpose_executor`
    absent)."""
    executor = getattr(idx, "_purpose_executor", None)
    if executor is None:
        return
    try:
        executor.submit(lambda: None).result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise TimeoutError(
            f"deferred purpose-vector pass did not finish within {timeout}s "
            f"(purpose_vectors_pending={idx.purpose_vectors_pending}). The "
            "single-worker FIFO barrier timed out — check for a stuck embed "
            "provider or a governor flag that moved work off "
            "_purpose_executor (agent/indexer/_core.py:"
            "_schedule_deferred_purpose_pass)."
        ) from exc


# ---------------------------------------------------------------------------
# Dummy embed provider — deterministic, zero-download
# ---------------------------------------------------------------------------

class _DummyEmbedProvider:
    """Deterministic, process-independent 768-dim embedder for unit tests.
    Matches nomic-embed-code dim.

    Vectors are seeded from a stable digest (zlib.crc32) of the text — NOT
    from Python's built-in ``hash()`` for str, which is salted by
    ``PYTHONHASHSEED`` and so produced different vectors in every pytest
    process. That made any assertion whose verdict depended on dense ranking
    ORDER (rather than mere presence) nondeterministic across runs, with the
    resulting flakes looking like product regressions because neither the
    test code nor the product code had changed (UPG-DUMMY-EMBEDDER-HASH-
    DETERMINISM). zlib.crc32 is stdlib, has no external dependency, and is
    stable across processes for the same input bytes."""
    DIM = 768

    def encode(self, texts: list[str]) -> np.ndarray:
        out = []
        for text in texts:
            # crc32 returns a 32-bit unsigned int — used directly as a
            # RandomState seed so the same text always lands on the same
            # vector (deterministic across processes, deterministic across
            # runs, deterministic across pytest invocations).
            seed = zlib.crc32(text[:80].encode("utf-8"))
            rng = np.random.RandomState(seed)
            v = rng.randn(self.DIM).astype(np.float32)
            norm = np.linalg.norm(v)
            out.append(v / (norm + 1e-8))
        return np.array(out)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.encode(texts).tolist()

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        # Symmetric stand-in — no registered query prompt, so query-mode embedding
        # is identical to document-mode embedding (matches most real embed models).
        return self.embed(texts)


@pytest.fixture
def indexer(tmp_path, monkeypatch):
    """
    CodeIndexer backed by a fresh ChromaDB in tmp_path.
    The embed provider is replaced with _DummyEmbedProvider — no model download.
    """
    from agent import indexer as idx_module
    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _model: _DummyEmbedProvider())
    from agent.indexer import CodeIndexer
    return CodeIndexer(
        workspace_root=str(tmp_path),
        db_path=str(tmp_path / "chroma"),
    )


@pytest.fixture
def searcher(indexer):
    """CodeSearcher wrapping a mocked-embedder CodeIndexer."""
    from agent.searcher import CodeSearcher
    return CodeSearcher(indexer)


# ---------------------------------------------------------------------------
# UPG-CONFTEST-SERVICE-CLOBBER: universal app.state.service snapshot/restore.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_app_state_service():
    """Snapshot and restore ``app.state.service`` around every test so no fixture
    or inline ``TestClient`` block can leave a mock/partial service installed for
    a later test to exercise vacuously (the "lying mock" class).

    The session-scoped ``real_service_client`` sets ``app.state.service`` once and
    relies on it persisting; a mock-based test that ran in between used to clobber
    it and never restore, so a later real-service REST test silently exercised the
    wrong service. Rolling the value back after every test keeps whatever was
    installed at each test's start (the real service, once ``real_service_client``
    is set up) authoritative — which is what makes removing the local
    ``_reaffirm_real_service`` workaround safe. Higher-scoped fixtures set up
    before this function-scoped autouse, so the snapshot already reflects them."""
    from api import app
    saved = getattr(app.state, "service", None)
    try:
        yield
    finally:
        app.state.service = saved


# ---------------------------------------------------------------------------
# Real-service fixture — full pipeline with dummy embedder
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def real_service_client(tmp_path_factory):
    """
    FastAPI TestClient backed by a REAL VectrService with dummy embedder.

    Unlike `client` (which mocks the entire service), this exercises the full
    pipeline: HTTP → routes → VectrService → CodeIndexer → ChromaDB →
    CodeSearcher (BM25 + vector) → memory store.

    The embed provider is the deterministic dummy so no model download is
    needed, but everything else is production code.

    Important: the lifespan handler in api.py creates its own VectrService on
    TestClient entry and sets app.state.service.  We prevent that from clobbering
    our pre-built svc by patching VectrService in app.service so the lifespan
    call returns *our* svc instead of creating a fresh one pointed at the repo.
    """
    tmp = tmp_path_factory.mktemp("real_svc")

    # Both env vars are read only once, synchronously, inside VectrService.__init__
    # (app/service.py: VECTR_EMBED_MODEL at self._embed_model assignment,
    # VECTR_DB_DIR at db_dir resolution) — never re-read afterward. Scoping this
    # patch to just the constructor call (like the app.service.VectrService patch
    # below already does) prevents it from leaking VECTR_EMBED_MODEL=dummy into
    # os.environ for the rest of the test session: this fixture is session-scoped,
    # so a `with` block wrapped around the `yield` would only restore os.environ at
    # session teardown, after every other test file has already run with it set.
    with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()):
        from app.service import VectrService
        from api import app

        with patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp), "VECTR_EMBED_MODEL": "dummy"}):
            svc = VectrService(workspace_root=str(tmp))

        # Patch `app.service.VectrService` only across TestClient startup, where the
        # `lifespan` handler's own `VectrService(...)` call must be intercepted to
        # return our pre-built `svc` instead of constructing a fresh one pointed at
        # the real repo. Scoping the patch to just __enter__() (rather than wrapping
        # it around the whole `with` block, which — for a session-scoped generator
        # fixture — would keep the patch active for the REST OF THE TEST SESSION)
        # prevents every later test's unrelated `VectrService(...)` construction
        # from silently being redirected to this one shared, ever-growing instance.
        c = TestClient(app, raise_server_exceptions=True)
        with patch("app.service.VectrService", return_value=svc):
            c.__enter__()
        try:
            yield c, svc, str(tmp)
        finally:
            c.__exit__(None, None, None)
            svc.shutdown()  # release the indexer's ChromaDB client at session end


# ---------------------------------------------------------------------------
# Integration fixture — real nomic-embed-code model
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def integration_indexer(tmp_path_factory):
    """
    CodeIndexer with the production Snowflake/snowflake-arctic-embed-m-v1.5 model.

    Downloads once (~440 MB), then cached at ~/.cache/vectr/models.
    Used only by @pytest.mark.integration tests.  Run with:
        VECTR_TEST_ALLOW_REAL_NETWORK=1 pytest -m integration
    (that flag opts this session out of conftest's socket guard and
    real-model-download guard; without it, constructing this fixture raises
    RealModelDownloadBlocked).
    """
    import os as _os
    tmp = tmp_path_factory.mktemp("integration")
    model = _os.getenv("VECTR_EMBED_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
    from agent.indexer import CodeIndexer
    return CodeIndexer(
        workspace_root=str(tmp),
        embed_model=model,
        db_path=str(tmp / "chroma"),
    )


# ---------------------------------------------------------------------------
# Python file factory helper
# ---------------------------------------------------------------------------

def make_py(tmp_path: Path, name: str, content: str) -> str:
    """Write a Python file into tmp_path and return its absolute path."""
    f = tmp_path / name
    f.write_text(textwrap.dedent(content))
    return str(f)


# ---------------------------------------------------------------------------
# FastAPI client fixtures
# ---------------------------------------------------------------------------

def _base_mock_service():
    """Mock VectrService with sensible defaults for API route tests."""
    from agent.searcher import SearchResult
    from agent.symbol_graph import LocateResult

    svc = MagicMock()
    svc._embed_model = "BAAI/bge-base-en-v1.5"
    svc.total_chunks = 100
    # UPG-8.2: /v1/health sources last_indexed from the same VectrService
    # property that populates svc.status()["last_indexed"] below.
    svc.last_indexed = "2026-01-01T00:00:00Z"

    _result = SearchResult(
        file_path="src/auth.py", lines="10-30", symbol_name="verify_token",
        language="python", score=0.91, content="def verify_token(): ...",
    )
    svc.search.return_value = ([_result], 15)
    # UPG-QUERYTYPE-REROUTE: additive symbol-graph hint — no exact identifier
    # match by default, so no hint section is appended in the common case.
    svc.identifier_hint_symbols.return_value = []
    svc.index.return_value = (5, 100, 120)
    svc.status.return_value = {
        "indexed_files": 5, "total_chunks": 100,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "BAAI/bge-base-en-v1.5",
        "workspace_root": "/repo", "symbol_count": 20,
    }
    svc.get_map.return_value = "# Passport\nPython FastAPI service."
    # Real locate_with_snippets() returns a LocateResult, not a bare list —
    # a mock returning [] made /v1/locate 500 on `result.symbols` for any
    # test that actually asserted a 200 (caught while adding search-only
    # REST coverage; conftest mock was the lone type mismatch here).
    svc.locate_with_snippets.return_value = LocateResult(symbols=[], resolution_strategy="none", query="")
    svc.format_locate.return_value = "No results."
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.should_evict.return_value = False
    svc.eviction_hint.return_value = ""
    svc.remember.return_value = 1
    # /v1/remember dispatches remember_with_extras(), not remember() — a bare
    # MagicMock return here would validate against RememberResponse only by
    # accident (MagicMock's default __int__/__iter__ stubs), while an f-string
    # embedding outcome.note_id would still leak a "<MagicMock ...>" repr into
    # the confirmation message. Return the REAL outcome type.
    from app.service import RememberOutcome
    svc.remember_with_extras.return_value = RememberOutcome(
        note_id=1, related=[], revoked_related=[], proxy_anchor_suggestions=[],
    )
    svc.promote_note.return_value = True
    svc.revoke_note.return_value = True
    svc.reinstate_note.return_value = True
    svc.supersede_note.return_value = True
    svc.pin_note.return_value = True
    svc.recall.return_value = "# Working Notes (1 entries)\n\n[1] [HIGH] test content\n"
    svc.snapshot_session.return_value = "snap_abc123"
    svc.list_snapshots.return_value = [{"snapshot_id": "snap_abc123", "label": "test", "created_at": 0.0}]
    # UPG-RESUME-SURFACE: real VectrService.resume() shape (last_task/gotchas/
    # snapshot/formatted) — a bare MagicMock would fail `ResumeResponse(**data)`
    # at the route (mocks must return the REAL type, not a stand-in).
    svc.resume.return_value = {
        "last_task": None,
        "gotchas": [],
        "snapshot": None,
        "formatted": (
            "Nothing to resume yet — no task notes, snapshots, or gotchas "
            "recorded for this workspace. Use vectr_remember(kind='task', ...) "
            "to start one."
        ),
    }
    # Default mode is full (not memory-only / not search-only); must be an
    # explicit bool, not a MagicMock (bare MagicMock attrs are truthy by default).
    svc.memory_only = False
    svc.search_only = False
    return svc


@pytest.fixture
def client():
    """FastAPI TestClient with fully-mocked VectrService. Fast — no model loading."""
    from api import app
    svc = _base_mock_service()
    # UPG-CONFTEST-SERVICE-CLOBBER: save/restore app.state.service so this mock
    # never persists past the fixture and clobber a later real-service test that
    # runs after it (a REST test could otherwise exercise a mock and pass
    # vacuously depending on execution order).
    _prior_service = getattr(app.state, "service", None)
    try:
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=True) as c:
                app.state.service = svc
                yield c
    finally:
        app.state.service = _prior_service


@pytest.fixture
def client_real_memory(tmp_path):
    """
    FastAPI TestClient where search is mocked but WorkingContextStore is REAL.
    Used to test the full remember → recall round-trip through HTTP without
    loading the embedding model.
    """
    from api import app
    from agent.working_context_store import WorkingContextStore
    from agent.trigger_engine import TriggerFireLedger, TurnInjectionLedger

    svc = _base_mock_service()
    real_store = WorkingContextStore(str(tmp_path))
    ws = str(tmp_path)

    def _remember(content, tags=None, priority="medium", session_id=None, kind="finding", title="",
                  agent="", triggers=None, provenance="agent", scope=None, anchors=None,
                  supersedes=None, contradicts=None, user_quote=None, pin=False):
        return real_store.remember(
            ws, content, tags, priority, session_id, kind=kind, title=title, author_id=agent,
            triggers=triggers, provenance=provenance, scope=scope, anchors=anchors,
            supersedes=supersedes, contradicts=contradicts, user_quote=user_quote, pin=pin,
        )

    def _remember_with_extras(content, tags=None, priority="medium", session_id=None,
                               kind="finding", title="", agent="", triggers=None,
                               provenance="agent", scope=None, anchors=None,
                               supersedes=None, contradicts=None, user_quote=None, pin=False):
        """Mirrors VectrService.remember_with_extras's own gating (app/
        service.py) against this fixture's real store, so /v1/remember
        REST tests routed through this fixture get a REAL RememberOutcome
        (real RelatedNote list, real proxy-anchor list) rather than a bare
        MagicMock return. This store has no embedder attached, so `related`
        is always [] here (the same real fail-open path a genuinely
        embedder-less store takes in production); `proxy_anchor_suggestions`
        is real glob presence against `tmp_path`, needing no embedder.
        `pin` (UPG-RECALL-MISS-FLOOR part (b)) is threaded straight through
        to the real store's own `remember(pin=...)`, same as production."""
        from app.service import RememberOutcome
        from agent.proxy_anchors import suggest_proxy_anchors
        from agent.config import (
            MEMORY_WRITE_RELATED_ENABLED,
            MEMORY_WRITE_RELATED_LIMIT,
            MEMORY_WRITE_RELATED_MIN_SIMILARITY,
            MEMORY_WRITE_PROXY_SUGGEST_ENABLED,
            MEMORY_WRITE_PROXY_SUGGEST_LIMIT,
        )
        note_id = _remember(
            content, tags, priority, session_id, kind, title, agent, triggers,
            provenance, scope, anchors, supersedes, contradicts, user_quote, pin,
        )
        related = []
        if MEMORY_WRITE_RELATED_ENABLED:
            related = real_store.related_active_notes(
                ws, note_id, limit=MEMORY_WRITE_RELATED_LIMIT,
                min_similarity=MEMORY_WRITE_RELATED_MIN_SIMILARITY,
            )
        proxy_anchor_suggestions = []
        if MEMORY_WRITE_PROXY_SUGGEST_ENABLED and kind == "operational" and not anchors:
            proxy_anchor_suggestions = suggest_proxy_anchors(ws, MEMORY_WRITE_PROXY_SUGGEST_LIMIT)
        return RememberOutcome(
            note_id=note_id, related=related, revoked_related=[],
            proxy_anchor_suggestions=proxy_anchor_suggestions,
        )

    svc.remember.side_effect = _remember
    svc.remember_with_extras.side_effect = _remember_with_extras
    svc.promote_note.side_effect = lambda note_id, to: real_store.promote(ws, note_id, to)
    svc.revoke_note.side_effect = lambda note_id, reason, actor="agent": real_store.revoke_note(
        ws, note_id, reason, actor=actor
    )
    svc.reinstate_note.side_effect = lambda note_id, actor="agent", reason=None: real_store.reinstate_note(
        ws, note_id, actor=actor, reason=reason
    )
    svc.supersede_note.side_effect = (
        lambda note_id, superseded_by=None, reason=None, actor="agent": real_store.supersede_note(
            ws, note_id, superseded_by=superseded_by, reason=reason, actor=actor
        )
    )
    svc.pin_note.side_effect = lambda note_id, pinned=True: real_store.set_pinned(ws, note_id, pinned)
    svc.attach_anchors.side_effect = (
        lambda note_id, anchors, session_id=None: real_store.attach_anchors(
            ws, note_id, anchors, session_id=session_id
        )
    )
    svc.detach_anchors.side_effect = (
        lambda note_id, anchors: real_store.detach_anchors(ws, note_id, anchors)
    )

    # TRIGGER-ENGINE wave 2a: a minimal per-session ledger registry mirroring
    # `VectrService._ledger_for`/`reset_trigger_ledger` so REST-level tests
    # against this REAL store can exercise fire-dedup and cumulative budget
    # through the actual `/v1/recall` request/response cycle, not just a
    # stand-in that silently accepts and drops `session_id`/`events`.
    _ledgers: dict[str, TriggerFireLedger] = {}

    def _ledger_for(session_id):
        if not session_id:
            return None
        return _ledgers.setdefault(session_id, TriggerFireLedger())

    def _reset_trigger_ledger(session_id):
        if session_id and session_id in _ledgers:
            _ledgers[session_id].reset()

    svc.reset_trigger_ledger.side_effect = _reset_trigger_ledger

    # Serving-policy hardening (wave 3, §5.3/§5.4): a second, TURN-scoped
    # registry mirroring `VectrService._turn_ledger_for`/`reset_turn_ledger`
    # alongside `_ledgers`/`_ledger_for` above. Without this, a REST test
    # against this REAL store could never exercise cross-surface same-turn
    # dedup or the shared ≤500-token turn budget (`fire_and_format`'s
    # `turn_ledger=None` default silently reproduces the pre-wave-3
    # unbounded/undeduped behaviour, the same way `ledger=None` reproduces
    # pre-wave-2a behaviour) — every `/v1/recall` request would look like
    # its own fresh turn even when two hook surfaces fire within one.
    _turn_ledgers: dict[str, TurnInjectionLedger] = {}

    def _turn_ledger_for(session_id):
        if not session_id:
            return None
        return _turn_ledgers.setdefault(session_id, TurnInjectionLedger())

    def _reset_turn_ledger(session_id):
        if session_id and session_id in _turn_ledgers:
            _turn_ledgers[session_id].reset()

    svc.reset_turn_ledger.side_effect = _reset_turn_ledger

    def _recall(query=None, tags=None, priority=None, limit=10, kind=None, boot=False,
                min_similarity=None, file_path=None, command=None, max_age_days=None, sort_by="relevance",
                detail="index", note_id=None, surface="mcp", hook_event=None,
                session_id=None, events=None):
        if note_id is not None:
            note = real_store.get_note(ws, note_id)
            if note is None:
                return f"Note #{note_id} not found."
            stale = real_store.check_staleness([note], ws)
            return real_store.format_notes_for_llm([note], stale_warnings=stale, detail="full", surface=surface)
        if boot:
            events_to_fire = events if events else ["session-start"]
            # `spend_turn_budget` deliberately omitted (defaults False),
            # mirroring `VectrService._recall_impl`'s boot branch exactly:
            # session-start bulk keeps its own separate per-SESSION cap
            # (`ledger`) rather than the smaller ordinary-turn allowance —
            # the turn ledger's dedup CLAIM still runs via `turn_ledger`,
            # so a note delivered at boot is still excluded from a same-
            # turn PreToolUse/prompt-submit re-delivery.
            fire_text, _ = real_store.fire_and_format(
                ws, events=events_to_fire, session_id=session_id,
                ledger=_ledger_for(session_id), turn_ledger=_turn_ledger_for(session_id),
                surface=surface,
            )
            return fire_text
        if file_path:
            turn_ledger = _turn_ledger_for(session_id)
            fire_text, fired_ids = real_store.fire_and_format(
                ws, event="pre-edit", file_path=file_path, session_id=session_id,
                ledger=_ledger_for(session_id), turn_ledger=turn_ledger,
                spend_turn_budget=True, surface=surface,
            )
            path_notes = real_store.recall_for_path(ws, file_path, kind=kind, limit=limit, session_id=session_id)
            # Mirrors VectrService._recall_impl's own fix: a note claimed by
            # an EARLIER surface this turn (turn-deduped out of `fired_ids`
            # here) must also be excluded from the legacy content-match
            # fallback, not just from this call's own engine delivery.
            path_notes = [
                n for n in path_notes
                if n.note_id not in fired_ids
                and (turn_ledger is None or turn_ledger.eligible(n.note_id))
            ]
            legacy_text = real_store.format_notes_for_llm(path_notes, detail=detail, surface=surface) if path_notes else ""
            if fire_text and legacy_text:
                return fire_text + "\n\n" + legacy_text
            return fire_text or legacy_text
        if command:
            fire_text, _ = real_store.fire_and_format(
                ws, event="pre-run", command=command, session_id=session_id,
                ledger=_ledger_for(session_id), turn_ledger=_turn_ledger_for(session_id),
                spend_turn_budget=True, surface=surface,
            )
            return fire_text
        fire_text, fired_ids = "", set()
        turn_ledger = _turn_ledger_for(session_id)
        if events:
            fire_text, fired_ids = real_store.fire_and_format(
                ws, events=events, session_id=session_id,
                ledger=_ledger_for(session_id), turn_ledger=turn_ledger,
                spend_turn_budget=True, surface=surface,
            )
        notes = real_store.recall(ws, query, tags, priority, limit, kind=kind, min_similarity=min_similarity,
                                  max_age_days=max_age_days, sort_by=sort_by, session_id=session_id)
        # Mirrors VectrService._recall_impl's own fix: exclude notes already
        # claimed this turn by an EARLIER surface, not just this call's own
        # engine delivery — but ONLY when `events` is given, i.e. this call
        # itself stands in for an injection surface. A plain direct
        # `vectr_recall(query=...)` (no `events`) must never be turn-deduped
        # — it is a deliberate lookup, not a passive injection surface, and
        # `fired_ids` is always empty here when `events` is falsy anyway.
        if events:
            notes = [
                n for n in notes
                if n.note_id not in fired_ids
                and (turn_ledger is None or turn_ledger.eligible(n.note_id))
            ]
        formatted = real_store.format_notes_for_llm(notes, detail=detail, surface=surface, sort_by=sort_by)
        if fire_text and formatted:
            return fire_text + "\n\n" + formatted
        return fire_text or formatted

    svc.recall.side_effect = _recall
    svc.forget_note.side_effect = lambda note_id: real_store.forget(ws, note_id)
    svc.forget_all.side_effect = lambda: real_store.forget_all(ws)
    svc.snapshot_session.side_effect = lambda label, session_id=None: \
        real_store.snapshot(ws, label=label)
    svc.list_snapshots.side_effect = lambda: real_store.list_snapshots(ws)

    # UPG-CONFTEST-REAL-MEMORY-MIRRORS: resume() and record_commit_note() are
    # bound straight to the REAL VectrService methods (app/service.py) rather
    # than hand-duplicated here, so a bug in either real method fails these
    # tests instead of silently passing against a parallel reimplementation
    # that could drift from it. The real methods only touch
    # self._context_store / self._workspace_root / self._search_only (plus,
    # for record_commit_note, self._current_task_note()/self._require_memory_
    # layer()/self.remember() — all bound the same way below), so those are
    # set as real attributes on the mock `svc` and the real unbound methods
    # are called against it (svc.remember already delegates to the real
    # store via `_remember` above, so record_commit_note's internal
    # `self.remember(...)` call composes with it for free).
    svc._context_store = real_store
    svc._workspace_root = ws
    svc._search_only = False
    svc._require_memory_layer = lambda: _RealVectrService._require_memory_layer(svc)
    svc._current_task_note = lambda: _RealVectrService._current_task_note(svc)
    svc.resume.side_effect = lambda session_id=None, surface="mcp": _RealVectrService.resume(
        svc, session_id=session_id, surface=surface
    )
    svc.record_commit_note.side_effect = lambda sha, subject, branch, files: (
        _RealVectrService.record_commit_note(svc, sha, subject, branch, files)
    )

    # UPG-CONFTEST-SERVICE-CLOBBER: save/restore app.state.service (see the
    # `client` fixture) so this partial-real service does not persist into a
    # later test that relies on a different app.state.service.
    _prior_service = getattr(app.state, "service", None)
    try:
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=True) as c:
                app.state.service = svc
                yield c
    finally:
        app.state.service = _prior_service
