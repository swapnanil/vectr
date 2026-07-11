"""Caching layers for proactive context (UPG-PRO: org-wide caching).

Two distinct caches, with very different safety profiles — kept separate on
purpose:

* `ArtifactCache` — caches vectr-LAYER artifacts (semantic search results,
  recall results, embedding computations). SAFE and the shippable value: keyed
  by exact artifact identity plus the current index-state epoch, so a re-index
  invalidates automatically. On a team / central instance this cache is shared
  by every connected client, so the org computes each artifact once. Every
  lookup is counted (hits / misses / bytes / estimated tokens saved) so the
  value is measurable, not asserted.

* `ResponseCache` — caches an upstream LLM RESPONSE, and ONLY for a
  byte-identical full request within a short TTL (exact match). This is the one
  provably-safe class of LLM-response caching for stateful agentic traffic;
  semantic-similarity response caching is deliberately NOT offered (a wrong hit
  silently corrupts a conversation). Off by default.

Cache identity is exact by default. `ArtifactCache` also supports an optional,
opt-in APPROXIMATE lookup (a cached result reused for an embedding-similar-but-
not-identical probe, above an explicit cosine threshold, with deterministic
tie-breaking). It is off unless a probe vector and a sub-1.0 threshold are both
supplied; its staleness semantics are documented in the design doc.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field


def canonical_key(kind: str, args: dict, index_epoch: str) -> str:
    """Stable identity for a vectr artifact: kind + canonicalised args + the
    index epoch. Same inputs against the same index -> same key -> same result.
    A changed index epoch changes every key, so stale artifacts never match."""
    payload = json.dumps(
        {"kind": kind, "args": args, "epoch": index_epoch},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _approx_bytes(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int = 0
    approx_hits: int = 0
    bytes_served: int = 0  # cumulative bytes returned from hits

    def as_dict(self) -> dict:
        total = self.hits + self.misses
        hit_rate = round(self.hits / total, 4) if total else 0.0
        # Rough token-savings estimate: bytes served from cache / 4 chars-per-token.
        est_tokens_saved = self.bytes_served // 4
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "approx_hits": self.approx_hits,
            "hit_rate": hit_rate,
            "bytes_served": self.bytes_served,
            "est_tokens_saved": est_tokens_saved,
        }


@dataclass
class _Entry:
    value: object
    stored_at: float
    size_bytes: int
    key_vector: list[float] | None = None


class ArtifactCache:
    """LRU + optional-TTL cache for vectr-layer artifacts, with metrics.

    Thread-safe: a team/central instance serves concurrent clients, and CLI vs
    daemon can touch it together.
    """

    def __init__(
        self,
        *,
        max_entries: int = 2048,
        ttl_seconds: float = 0.0,
        similarity_threshold: float = 1.0,
        clock=time.monotonic,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl = max(0.0, ttl_seconds)
        self._threshold = similarity_threshold
        self._clock = clock
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def _fresh(self, entry: _Entry) -> bool:
        if self._ttl <= 0.0:
            return True
        return (self._clock() - entry.stored_at) <= self._ttl

    def get(self, key: str, *, probe_vector: list[float] | None = None):
        """Return (found, value). Exact key first; then, only if a probe vector
        and a sub-1.0 threshold are both configured, a deterministic
        nearest-above-threshold approximate match."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and self._fresh(entry):
                self._entries.move_to_end(key)
                self.stats.hits += 1
                self.stats.bytes_served += entry.size_bytes
                return True, entry.value
            if entry is not None:
                # Stale — drop it.
                del self._entries[key]

            if probe_vector is not None and self._threshold < 1.0:
                best_key = None
                best_sim = -1.0
                for k, e in self._entries.items():
                    if e.key_vector is None or not self._fresh(e):
                        continue
                    sim = _cosine(probe_vector, e.key_vector)
                    # Deterministic tie-break: higher sim wins; equal sim -> key asc.
                    if sim > best_sim or (sim == best_sim and (best_key is None or k < best_key)):
                        best_sim = sim
                        best_key = k
                if best_key is not None and best_sim >= self._threshold:
                    e = self._entries[best_key]
                    self._entries.move_to_end(best_key)
                    self.stats.hits += 1
                    self.stats.approx_hits += 1
                    self.stats.bytes_served += e.size_bytes
                    return True, e.value

            self.stats.misses += 1
            return False, None

    def put(self, key: str, value, *, key_vector: list[float] | None = None) -> None:
        with self._lock:
            size = _approx_bytes(value)
            self._entries[key] = _Entry(
                value=value, stored_at=self._clock(), size_bytes=size, key_vector=key_vector
            )
            self._entries.move_to_end(key)
            self.stats.stores += 1
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def metrics(self) -> dict:
        with self._lock:
            d = self.stats.as_dict()
            d["entries"] = len(self._entries)
            d["max_entries"] = self._max_entries
            return d


class ResponseCache:
    """Exact-match, TTL-bounded cache of upstream LLM responses (proxy only).

    A response is served ONLY for a request whose canonical bytes are identical
    to a cached one within the TTL. Streaming responses are cached as the exact
    ordered list of raw SSE byte chunks, so a replay is byte-identical to the
    original stream. This is the single provably-safe response-cache class for
    stateful agentic traffic.
    """

    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 60.0,
        clock=time.monotonic,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl = max(0.0, ttl_seconds)
        self._clock = clock
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    @staticmethod
    def request_key(body: dict, *, path: str = "", headers_signature: str = "") -> str:
        """Byte-identity key for a request. Includes the endpoint path and an
        optional header signature so two requests that differ only in a
        cache-relevant header are never conflated."""
        payload = json.dumps(
            {"path": path, "headers": headers_signature, "body": body},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _fresh(self, entry: _Entry) -> bool:
        if self._ttl <= 0.0:
            return False  # a zero TTL means "never serve stale" -> effectively off
        return (self._clock() - entry.stored_at) <= self._ttl

    def get(self, key: str):
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and self._fresh(entry):
                self._entries.move_to_end(key)
                self.stats.hits += 1
                self.stats.bytes_served += entry.size_bytes
                return True, entry.value
            if entry is not None:
                del self._entries[key]
            self.stats.misses += 1
            return False, None

    def put(self, key: str, value) -> None:
        with self._lock:
            size = _approx_bytes(value)
            self._entries[key] = _Entry(value=value, stored_at=self._clock(), size_bytes=size)
            self._entries.move_to_end(key)
            self.stats.stores += 1
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def metrics(self) -> dict:
        with self._lock:
            d = self.stats.as_dict()
            d["entries"] = len(self._entries)
            d["max_entries"] = self._max_entries
            return d
