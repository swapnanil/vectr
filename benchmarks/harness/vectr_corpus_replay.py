#!/usr/bin/env python3
"""Replay the documented per-corpus audit search batteries against a freshly
re-chunked instance, classifying each result with the REAL chunk_quality
predicates so we can measure Wave 1 + tuning impact faithfully.

Usage: python3 vectr_corpus_replay.py <cpython|uv|tigerbeetle> <port>

'Before' baselines are from opus_findings_{corpus}.md (2026-06-14)."""
import json, sys, urllib.request

sys.path.insert(0, "/Users/swapnanil.s/Documents/fde/vectr")
from agent.chunk_quality import (
    is_trivial_chunk, is_navigational_chunk, is_test_file,
    is_generated_file, is_vectr_config_file, is_doc_language,
)

BATTERIES = {
    "cpython": [
        ("garbage collector generations collection", "S1 control — was good (gcmodule.c #1)"),
        ("dict resize hash collision", "S2 BEFORE: all 5 trivial }/return 0; chunks; dictobject.c never appeared"),
        ("global interpreter lock acquire release", "S3 BEFORE: trivial-chunk dominated"),
        ("bytecode evaluation interpreter loop", "S5 BEFORE: trivial-chunk dominated"),
        ("float object arithmetic operations", "S14 BEFORE: trivial-chunk dominated"),
    ],
    "uv": [
        ("dependency resolution pubgrub", "S1 BEFORE: test scenarios outrank uv-resolver/src/resolver/mod.rs"),
        ("pip install command", "S8 BEFORE: test scenarios + pub use blocks; no real impl/docs"),
        ("install plan", "S13 BEFORE: lock_scenarios.rs over uv-installer/src/plan.rs"),
    ],
    "tigerbeetle": [
        ("superblock checkpoint state", "S6 control — was excellent (CheckpointState/SuperBlockHeader)"),
        ("write-ahead log journal", "S5 BEFORE: all 5 = `const log = std.log;`; journal.zig never surfaced"),
        ("fuzzer random seed", "S8 BEFORE: all `const seed = 42;` tied 0.740"),
        ("build run quickstart", "S10 BEFORE: 5 CHANGELOG `## TigerBeetle 0.16.x` headers"),
        ("data modeling create accounts", "S11 BEFORE: 5 IDENTICAL `## 1. Create accounts` README headers tied 0.730"),
    ],
}

def req(base, path, body=None):
    if body is None:
        return json.load(urllib.request.urlopen(base + path, timeout=120))
    r = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=600))

def classify(r):
    content = r.get("content") or ""
    fp = r.get("file") or ""
    lang = r.get("language") or ""
    if is_vectr_config_file(fp):
        return "CONFIG"
    if is_generated_file(fp):
        return "GENERATED"
    if is_trivial_chunk(content, lang):
        return "trivial"
    if is_navigational_chunk(content, lang):
        return "nav"
    if is_test_file(fp):
        return "test"
    if is_doc_language(lang):
        return "doc"
    return "CODE"

TAG = {"CODE":"✅","doc":"📄","test":"🧪","nav":"🔗","trivial":"❌","GENERATED":"⚙️","CONFIG":"🚫"}

def main():
    corpus, port = sys.argv[1], sys.argv[2]
    base = f"http://localhost:{port}"
    battery = BATTERIES[corpus]
    st = req(base, "/v1/status")
    print("="*92)
    print(f"CORPUS: {corpus}  (port {port})")
    print(f"index: {st.get('indexed_files')} files / {st.get('total_chunks')} chunks")
    print("="*92)
    for query, before in battery:
        n = 5
        res = req(base, "/v1/search", {"query": query, "n_results": n, "language": None})
        results = res["results"]
        kinds = [classify(r) for r in results]
        from collections import Counter
        c = Counter(kinds)
        summary = " ".join(f"{c[k]}{TAG[k]}{k}" for k in ["CODE","doc","test","nav","trivial","GENERATED","CONFIG"] if c[k])
        print(f"\n▶ {query!r}  [no filter, n={n}]")
        print(f"  {before}")
        print(f"  AFTER: {summary}  ({res['query_time_ms']}ms)")
        for i,(r,k) in enumerate(zip(results,kinds),1):
            f = r["file"].replace("/Users/swapnanil.s/Documents/fde/vectr/tmp/","")
            snip = (r.get("content") or "").strip().splitlines()
            snip = snip[0][:58] if snip else ""
            print(f"    {i}. {TAG[k]} [{r.get('language') or '?':<8}] {r['score']:.3f} {f}:{r['lines']} | {snip}")

if __name__ == "__main__":
    main()
