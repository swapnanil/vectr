#!/usr/bin/env python3
"""Replay the documented current_workspace audit query battery against the live
8765 instance and classify results, to measure Wave 1 impact (UPG-2.1/2.2/2.4).

'Before' baselines are from opus_findings_current_workspace.md (2026-06-14)."""
import json, sys, urllib.request

BASE = "http://localhost:8765"

# (query, language, n_results, before_summary)
BATTERY = [
    ("extract decisions and action items from meeting transcript", None, 5,
     "BEFORE: 0 code — all 5 were benchmark/blog markdown headers"),
    ("HNSW vector index nearest neighbor insertion and search", None, 15,
     "BEFORE: 0 code — all 15 were markdown headers"),
    ("tree-sitter chunking of source code into symbols", None, 5,
     "BEFORE: 0 code — 5 identical README headers"),
    # python-filtered controls (worked before — should stay good)
    ("pydantic model for action item with owner and deadline", "python", 5,
     "BEFORE: 5/5 real code (control)"),
    ("retry API call with exponential backoff on rate limit", "python", 5,
     "BEFORE: 5/5 real code (control)"),
    ("LLM judge evaluation scoring harness", "python", 5,
     "BEFORE: 5/5 real code (control)"),
]

MD_HEADING_PREFIXES = ("#", "##", "###", "####", "#####")

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def classify(r):
    """Return 'code' | 'md-heading' | 'md-text' for a result."""
    lang = (r.get("language") or "").lower()
    content = (r.get("content") or "").strip()
    first = content.splitlines()[0].strip() if content else ""
    meaningful = [l for l in content.splitlines() if l.strip()]
    if lang == "markdown" or lang == "md":
        # heading-only if all meaningful lines are headings, or 1-2 lines starting with #
        non_heading = [l for l in meaningful if not l.lstrip().startswith("#")]
        if not non_heading or (len(meaningful) <= 2 and len(" ".join(non_heading)) < 40):
            return "md-heading"
        return "md-text"
    return "code"

def main():
    print("=" * 78)
    print("VECTR AUDIT REPLAY — current_workspace corpus (post Wave 1 + 8.x)")
    st = json.load(urllib.request.urlopen(BASE + "/v1/status", timeout=30))
    print(f"index: {st.get('indexed_files')} files / {st.get('total_chunks')} chunks / model {st.get('embed_model')}")
    print("=" * 78)
    overall = []
    for query, lang, n, before in BATTERY:
        res = post("/v1/search", {"query": query, "n_results": n, "language": lang})
        results = res["results"]
        kinds = [classify(r) for r in results]
        code = kinds.count("code")
        mdh = kinds.count("md-heading")
        mdt = kinds.count("md-text")
        flt = f'language="{lang}"' if lang else "NO FILTER"
        print(f"\n▶ {query!r}  [{flt}, n={n}]")
        print(f"  {before}")
        print(f"  AFTER:  {code} code · {mdh} md-heading · {mdt} md-text  ({res['query_time_ms']}ms)")
        for i, (r, k) in enumerate(zip(results, kinds), 1):
            tag = {"code": "✅", "md-heading": "❌", "md-text": "📄"}[k]
            f = r["file"].replace("/Users/swapnanil.s/Documents/", "~/")
            first = (r.get("content") or "").strip().splitlines()
            snip = first[0][:60] if first else ""
            print(f"    {i:>2}. {tag} [{r.get('language') or '?':<8}] {r['score']:.3f} {f}:{r['lines']}  | {snip}")
        overall.append((query, lang, code, n))
    print("\n" + "=" * 78)
    print("UPG-2.4 verdict (unfiltered code-shaped queries should now return code):")
    for q, lang, code, n in overall:
        if lang is None:
            mark = "PASS" if code >= 1 else "FAIL"
            print(f"  [{mark}] {code}/{n} code  — {q!r}")
    print("=" * 78)

if __name__ == "__main__":
    main()
