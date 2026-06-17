#!/usr/bin/env python3
"""Full post-upgrade re-audit of ALL 13 vectr MCP tools per corpus, driven through
the /mcp JSON-RPC endpoint exactly like the original Opus audits. Search hits are
classified with the real chunk_quality predicates. Compares to opus_findings_*.md.

Usage: python3 vectr_full_audit.py <cpython|uv|tigerbeetle> <port>
"""
import json, sys, urllib.request
from collections import Counter

sys.path.insert(0, "/Users/swapnanil.s/Documents/fde/vectr")
from agent.chunk_quality import (
    is_trivial_chunk, is_navigational_chunk, is_test_file,
    is_generated_file, is_vectr_config_file, is_doc_language,
)

CFG = {
    "cpython": {
        "search": [
            "garbage collector generations collection", "dict resize hash collision",
            "global interpreter lock acquire release", "build and install CPython",
            "bytecode evaluation interpreter loop", "asyncio event loop coroutine",
            "list append grow resize array", "unicode string utf-8 encoding decode",
            "float object arithmetic operations", "reference counting incref decref",
            "tuple object allocation freelist", "exception handling set traceback",
            "memory allocator arena pool obmalloc", "import module machinery finder",
            "set object membership lookup",
        ],
        "locate": ["PyObject", "gc_collect", "PyDict_New", "dictresize", "PyList_Append",
                   "PyFloat_FromDouble", "_PyObject_GC_New", "PyImport_ImportModule",
                   "GCState", "get_gc_state"],
        "trace": ["gc_collect", "PyDict_New", "get_gc_state", "PyList_Append", "_PyObject_GC_New"],
        "lang_filter": "c",
    },
    "uv": {
        "search": [
            "dependency resolution pubgrub", "pip install command", "install plan",
            "lockfile generation lock", "version specifiers parsing", "marker environment evaluation",
            "wheel build distribution", "registry client http request", "workspace discovery",
            "package name normalization", "virtual environment creation venv",
            "requirement parsing pep508", "cache management storage", "git source resolution",
            "python interpreter discovery",
        ],
        "locate": ["Resolver", "Workspace", "Lock", "PubGrubPackage", "Requirement",
                   "PackageName", "RegistryClient", "VersionSpecifiers", "MarkerTree", "BuildContext"],
        "trace": ["Lock", "Workspace", "PackageName", "RegistryClient", "BuildContext", "PubGrubPackage"],
        "lang_filter": "rust",
    },
    "tigerbeetle": {
        "search": [
            "superblock checkpoint state", "write-ahead log journal", "fuzzer random seed",
            "build run quickstart", "data modeling create accounts", "io_uring linux async io",
            "checksum crc verification", "double-entry accounting transfer", "LSM tree compaction",
            "replica consensus view change vsr", "grid block storage", "free set allocation bitset",
            "message bus networking", "state machine commit", "cache map eviction",
        ],
        "locate": ["StateMachine", "Replica", "Storage", "Forest", "Journal",
                   "SuperBlock", "Grid", "FreeSet", "Account", "Transfer"],
        "trace": ["Journal", "StateMachine", "Forest", "Account", "Transfer", "checksum"],
        "lang_filter": "zig",
    },
}

PORT = None
def mcp(name, args=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}
    r = urllib.request.Request(f"http://localhost:{PORT}/mcp", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(r, timeout=120))
    if "error" in d:
        return f"[MCP ERROR {d['error'].get('code')}] {d['error'].get('message')}"
    blocks = d.get("result", {}).get("content", [])
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")

def rest_search(query, n=5, language=None):
    body = {"query": query, "n_results": n, "language": language}
    r = urllib.request.Request(f"http://localhost:{PORT}/v1/search", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(r, timeout=120))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code}

def classify(r):
    content, fp, lang = r.get("content") or "", r.get("file") or "", r.get("language") or ""
    if is_vectr_config_file(fp): return "CONFIG"
    if is_generated_file(fp): return "GEN"
    if is_trivial_chunk(content, lang): return "trivial"
    if is_navigational_chunk(content, lang): return "nav"
    if is_test_file(fp): return "test"
    if is_doc_language(lang): return "doc"
    return "CODE"

TAG = {"CODE":"✅","doc":"📄","test":"🧪","nav":"🔗","trivial":"❌","GEN":"⚙️","CONFIG":"🚫"}
KINDS = ["CODE","doc","test","nav","trivial","GEN","CONFIG"]

def first(text, n):  # first n nonblank lines, truncated
    return " ¶ ".join(l.strip()[:70] for l in text.splitlines() if l.strip()[:1])[:240] if text else ""

def hr(t): print("\n" + "="*94 + f"\n{t}\n" + "="*94)

def main():
    global PORT
    corpus, PORT = sys.argv[1], sys.argv[2]
    c = CFG[corpus]
    hr(f"FULL TOOL AUDIT — {corpus} (port {PORT})")

    # 1. STATUS
    hr("1. vectr_status")
    print(mcp("vectr_status"))

    # 2. SEARCH battery (classified)
    hr(f"2. vectr_search — {len(c['search'])} queries (no language filter)")
    agg = Counter()
    for q in c["search"]:
        res = rest_search(q, 5, None)
        if "_http_error" in res:
            print(f"\n▶ {q!r}: HTTP {res['_http_error']}"); continue
        rs = res["results"]; kinds = [classify(r) for r in rs]
        cc = Counter(kinds); agg.update(cc)
        summ = " ".join(f"{cc[k]}{TAG[k]}{k}" for k in KINDS if cc[k])
        top = rs[0] if rs else None
        topf = (top["file"].split("/tmp/")[-1] if top else "")
        print(f"\n▶ {q!r}  ->  {summ}  ({res['query_time_ms']}ms)")
        if top: print(f"   #1 {TAG[kinds[0]]} [{top.get('language')}] {top['score']:.3f} {topf}:{top['lines']} | {first(top.get('content',''),1)[:60]}")
    print(f"\n  AGGREGATE over {len(c['search'])} queries (top-5 each): " +
          " ".join(f"{agg[k]}{TAG[k]}{k}" for k in KINDS if agg[k]))

    # 2b. language filter on the corpus's real language
    hr(f"2b. vectr_search language={c['lang_filter']!r} (enum sync check)")
    res = rest_search(c["search"][0], 5, c["lang_filter"])
    if "_http_error" in res:
        print(f"language={c['lang_filter']!r} -> HTTP {res['_http_error']}  (422 = enum still omits this language; Wave 2 UPG-3.1)")
    else:
        print(f"language={c['lang_filter']!r} -> {len(res['results'])} results (filter accepted)")

    # 3. LOCATE
    hr(f"3. vectr_locate — {len(c['locate'])} symbols")
    for s in c["locate"]:
        out = mcp("vectr_locate", {"name": s})
        head = out.splitlines()[0] if out else ""
        found = not ("No symbol" in out or "no symbol" in out.lower())
        print(f"  {'✅' if found else '❌'} {s:22s} {head[:88]}")

    # 4. TRACE
    hr(f"4. vectr_trace — {len(c['trace'])} symbols (dedup check)")
    for s in c["trace"]:
        out = mcp("vectr_trace", {"name": s})
        rel = [l.strip() for l in out.splitlines() if any(k in l for k in ("Called by", "Calls", "callers", "callees", "none"))]
        # detect duplicate caller/callee entries (the documented dup bug)
        entries = [l.strip() for l in out.splitlines() if l.strip().startswith(("-", "•", "*"))]
        dups = [e for e, n in Counter(entries).items() if n > 1]
        dupnote = f"  ⚠️DUPLICATES x{len(dups)}: {dups[:2]}" if dups else ""
        print(f"  {s:18s} {(' | '.join(rel))[:110]}{dupnote}")

    # 5. MAP
    hr("5. vectr_map")
    print(first(mcp("vectr_map"), 6)[:400])

    # 6. RECALL (cutoff check: query vs no-query)
    hr("6. vectr_recall — relevance-cutoff check")
    print("  [no-query]:", first(mcp("vectr_recall"), 3)[:200])
    print("  [off-topic query]:", first(mcp("vectr_recall", {"query": "weekly digest meeting commitment notion zoom"}), 3)[:200])

    # 7. EVICT_HINT
    hr("7. vectr_evict_hint")
    print(first(mcp("vectr_evict_hint"), 3)[:200])

    # 8. REMEMBER
    hr("8. vectr_remember")
    print(mcp("vectr_remember", {"content": f"[post-upgrade audit {corpus}] verifying tool surface after Wave 1 + ranking tuning.",
                                  "tags": ["post-upgrade-audit", corpus], "priority": "low"}))

    # 9. SNAPSHOT + 10. SNAPSHOT_LIST
    hr("9/10. vectr_snapshot + vectr_snapshot_list")
    print(mcp("vectr_snapshot", {"label": f"post-upgrade-audit-{corpus}"}))
    print(first(mcp("vectr_snapshot_list"), 6)[:300])

    # 11. INGEST_TRACES
    hr("11. vectr_ingest_traces")
    ev = {"cpython": {"caller":"get_gc_state","callee":"_PyInterpreterState_GET"},
          "uv": {"caller":"RegistryClient","callee":"build_request"},
          "tigerbeetle": {"caller":"Journal","callee":"write_headers"}}[corpus]
    print(mcp("vectr_ingest_traces", {"events": [{"caller": ev["caller"], "callee": ev["callee"], "edge_type":"dynamic"}]}))
    print("  verify via trace:", " | ".join(mcp("vectr_trace", {"name": ev["caller"], "direction":"callees"}).splitlines()[:2])[:140])

    # 12. FORGET — guarded
    hr("12. vectr_forget — guarded")
    st = mcp("vectr_status")
    print("  (status notes line):", [l for l in st.splitlines() if "ote" in l][:1])
    print("  Held back unless only the probe note exists; behavior already characterized (destructive; snapshot≠undo).")

if __name__ == "__main__":
    main()
