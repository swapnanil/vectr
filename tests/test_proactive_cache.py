"""Caching-layer tests (UPG-PRO org-wide caching): correctness + metrics."""
from __future__ import annotations

from agent.proactive.cache import ArtifactCache, ResponseCache, canonical_key


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# -- ArtifactCache ----------------------------------------------------------

def test_exact_key_hit_and_miss():
    c = ArtifactCache()
    k = canonical_key("search", {"query": "lock"}, "code:1")
    found, _ = c.get(k)
    assert found is False
    c.put(k, ["result"])
    found, val = c.get(k)
    assert found and val == ["result"]
    m = c.metrics()
    assert m["hits"] == 1 and m["misses"] == 1 and m["entries"] == 1


def test_index_epoch_invalidates():
    c = ArtifactCache()
    k1 = canonical_key("search", {"query": "lock"}, "code:1")
    k2 = canonical_key("search", {"query": "lock"}, "code:2")  # epoch bumped
    c.put(k1, ["old"])
    found, _ = c.get(k2)
    assert found is False  # a re-index (new epoch) never serves the stale artifact


def test_lru_eviction():
    c = ArtifactCache(max_entries=2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")           # touch a -> b is LRU
    c.put("c", 3)        # evicts b
    assert c.get("b")[0] is False
    assert c.get("a")[0] is True
    assert c.get("c")[0] is True
    assert c.metrics()["evictions"] == 1


def test_ttl_expiry():
    clk = _Clock()
    c = ArtifactCache(ttl_seconds=10.0, clock=clk)
    c.put("k", "v")
    assert c.get("k")[0] is True
    clk.t += 11.0
    assert c.get("k")[0] is False  # expired


def test_metrics_tokens_saved():
    c = ArtifactCache()
    c.put("k", {"big": "x" * 400})
    c.get("k")
    m = c.metrics()
    assert m["bytes_served"] > 0
    assert m["est_tokens_saved"] == m["bytes_served"] // 4
    assert 0.0 <= m["hit_rate"] <= 1.0


def test_approximate_lookup_deterministic():
    # Approximate reuse only when threshold < 1.0 AND probe/key vectors given.
    c = ArtifactCache(similarity_threshold=0.9)
    c.put("k1", "v1", key_vector=[1.0, 0.0])
    c.put("k2", "v2", key_vector=[0.0, 1.0])
    found, val = c.get("miss", probe_vector=[0.99, 0.14])  # closest to k1
    assert found and val == "v1"
    assert c.metrics()["approx_hits"] == 1


def test_exact_threshold_no_approximate():
    c = ArtifactCache(similarity_threshold=1.0)  # default: exact only
    c.put("k1", "v1", key_vector=[1.0, 0.0])
    found, _ = c.get("miss", probe_vector=[0.99, 0.14])
    assert found is False  # threshold 1.0 disables approximate reuse


# -- ResponseCache ----------------------------------------------------------

def test_response_cache_exact_match_ttl():
    clk = _Clock()
    rc = ResponseCache(ttl_seconds=30.0, clock=clk)
    key = ResponseCache.request_key({"messages": [{"role": "user", "content": "hi"}]}, path="/v1/messages")
    assert rc.get(key)[0] is False
    rc.put(key, {"status": 200, "chunks": [b"data"]})
    found, val = rc.get(key)
    assert found and val["chunks"] == [b"data"]
    clk.t += 31.0
    assert rc.get(key)[0] is False  # TTL expiry


def test_response_cache_key_differs_on_body():
    k1 = ResponseCache.request_key({"messages": [{"role": "user", "content": "a"}]}, path="/v1/messages")
    k2 = ResponseCache.request_key({"messages": [{"role": "user", "content": "b"}]}, path="/v1/messages")
    assert k1 != k2


def test_response_cache_zero_ttl_never_serves():
    rc = ResponseCache(ttl_seconds=0.0)
    key = "k"
    rc.put(key, {"chunks": [b"x"]})
    assert rc.get(key)[0] is False  # zero TTL = effectively off
