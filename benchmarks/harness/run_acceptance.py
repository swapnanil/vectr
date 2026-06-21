#!/usr/bin/env python3
"""Replay product_cases.jsonl against a live vectr daemon (via /v1 REST).

Usage:
    python3 run_acceptance.py [--port PORT] [--corpus CORPUS_FILTER]

Reads benchmarks/acceptance/product_cases.jsonl. For each case with a
matching corpus (or all cases if no filter), issues a /v1/search call and
evaluates the 'expect' assertions.

Assertion rules
---------------
- top_k_contains: at least one result in the top-k has the expected file
  (substring of file path) AND the expected symbol (exact qualified name
  OR leaf match).
- top_k_absent: the absent_sym must NOT appear as the LEAF of any result's
  symbol in the top-k.  The leaf is r["symbol"].split(".")[-1] — so
  absent_sym="read" correctly accepts r["symbol"]="HttpRequest.readlines"
  (leaf "readlines" != "read").  Old substring match caused false positives
  for this case and any like it ("all" ⊂ "recall", etc.).
- sorted_by_score: returned scores must be non-increasing.
- status_languages_include: /v1/status must list all named languages.
- affordance_expand_to_symbol: at least one result has symbol_start_line > 0.

Exit code: 0 if all evaluated cases pass, 1 if any fail.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

_CASES_PATH = Path(__file__).parent.parent / "acceptance" / "product_cases.jsonl"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.load(r)


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _symbol_leaf(sym: str) -> str:
    """Return the leaf of a qualified symbol.

    'HttpRequest.readlines' -> 'readlines'
    'Field.deconstruct'     -> 'deconstruct'
    'deconstruct'           -> 'deconstruct'
    """
    if not sym:
        return ""
    # Handle both '.' and '::' separators (Python and Rust/C++)
    return sym.replace("::", ".").split(".")[-1]


def top_k_contains(results: list[dict], k: int, file: str | None = None,
                   symbol: str | None = None) -> bool:
    """Return True if at least one result in top-k matches the criteria.

    File is checked by substring (path suffix match).
    Symbol is checked by exact qualified name OR matching the leaf.
    """
    for r in results[:k]:
        file_ok = file is None or file in (r.get("file") or "")
        if symbol is None:
            sym_ok = True
        else:
            sym = r.get("symbol") or ""
            sym_ok = (
                sym == symbol
                or sym.endswith("." + symbol)
                or sym.endswith("::" + symbol)
                or _symbol_leaf(sym) == symbol
            )
        if file_ok and sym_ok:
            return True
    return False


def top_k_absent(results: list[dict], k: int, symbol: str) -> bool:
    """Return True when the absent_sym does NOT appear as the leaf of any
    result's symbol in the top-k results.

    This uses LEAF equality, not substring containment, to avoid false
    positives where the absent symbol is a strict prefix of a correct
    result's leaf.  For example:
        absent_sym="read", result symbol="HttpRequest.readlines"
        leaf("HttpRequest.readlines") = "readlines" != "read"  -> no match
        -> absent check PASSES (correct; readlines is the right answer)

    The old substring check ('absent_sym in r["symbol"]') would have
    matched "read" inside "readlines" and falsely reported a regression.

    Also checks the full qualified name for an exact match (to catch the
    case where the absent symbol IS the complete qualified form, e.g.
    "RemoveField.deconstruct").
    """
    for r in results[:k]:
        sym = r.get("symbol") or ""
        if not sym:
            continue
        leaf = _symbol_leaf(sym)
        if leaf == symbol or sym == symbol:
            return False
    return True


def sorted_by_score(results: list[dict]) -> bool:
    scores = [r.get("score", 0.0) for r in results]
    return scores == sorted(scores, reverse=True)


def affordance_expand_to_symbol(results: list[dict]) -> bool:
    return any(r.get("symbol_start_line", 0) > 0 for r in results)


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"


def run_case(case: dict, base: str) -> tuple[bool, list[str]]:
    """Evaluate one product_cases.jsonl entry against the live daemon.

    Returns (all_pass, list_of_messages).
    """
    messages: list[str] = []
    results: list[dict] = []
    status: dict = {}

    query = case["query"]
    language = case.get("language")
    n_results = case.get("n_results", 5)
    expect = case.get("expect", {})

    all_pass = True

    # Fetch /v1/status if needed for language coverage checks
    if "status_languages_include" in expect:
        try:
            status = _get(base, "/v1/status")
        except Exception as exc:
            messages.append(f"  ERROR fetching /v1/status: {exc}")
            return False, messages

    # Fetch search results
    try:
        resp = _post(base, "/v1/search", {
            "query": query,
            "language": language,
            "n_results": n_results,
        })
        results = resp.get("results", [])
    except Exception as exc:
        messages.append(f"  ERROR fetching /v1/search: {exc}")
        return False, messages

    # --- top_k_contains ---
    if "top_k_contains" in expect:
        spec = expect["top_k_contains"]
        ok = top_k_contains(
            results,
            k=spec["k"],
            file=spec.get("file"),
            symbol=spec.get("symbol"),
        )
        mark = _PASS if ok else _FAIL
        messages.append(
            f"  [{mark}] top_k_contains(k={spec['k']}, file={spec.get('file')!r}, "
            f"symbol={spec.get('symbol')!r})"
        )
        all_pass = all_pass and ok

    # --- top_k_absent ---
    if "top_k_absent" in expect:
        spec = expect["top_k_absent"]
        ok = top_k_absent(results, k=spec["k"], symbol=spec["symbol"])
        mark = _PASS if ok else _FAIL
        messages.append(
            f"  [{mark}] top_k_absent(k={spec['k']}, symbol={spec['symbol']!r}) "
            f"[leaf-match]"
        )
        all_pass = all_pass and ok

    # --- sorted_by_score ---
    if expect.get("sorted_by_score"):
        ok = sorted_by_score(results)
        mark = _PASS if ok else _FAIL
        scores = [round(r.get("score", 0.0), 4) for r in results]
        messages.append(f"  [{mark}] sorted_by_score  scores={scores}")
        all_pass = all_pass and ok

    # --- affordance_expand_to_symbol ---
    if expect.get("affordance_expand_to_symbol"):
        ok = affordance_expand_to_symbol(results)
        mark = _PASS if ok else _FAIL
        messages.append(f"  [{mark}] affordance_expand_to_symbol")
        all_pass = all_pass and ok

    # --- status_languages_include ---
    if "status_languages_include" in expect:
        indexed_langs = {
            lang_obj.get("language", "")
            for lang_obj in status.get("languages", [])
        }
        for lang in expect["status_languages_include"]:
            ok = lang in indexed_langs
            mark = _PASS if ok else _FAIL
            messages.append(
                f"  [{mark}] status_languages_include {lang!r}  "
                f"(indexed: {sorted(indexed_langs)})"
            )
            all_pass = all_pass and ok

    return all_pass, messages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_cases(corpus_filter: str | None = None) -> list[dict]:
    cases = []
    with open(_CASES_PATH) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            if corpus_filter and c.get("corpus") != corpus_filter:
                continue
            cases.append(c)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run product_cases.jsonl acceptance suite")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--corpus", default=None, help="Filter by corpus (e.g. django)")
    args = parser.parse_args(argv)

    base = f"http://localhost:{args.port}"

    # Verify daemon is reachable
    try:
        st = _get(base, "/v1/status")
    except Exception as exc:
        print(f"ERROR: cannot reach daemon at {base}: {exc}", file=sys.stderr)
        return 1

    print("=" * 80)
    print(f"Acceptance replay — {_CASES_PATH.name}")
    print(f"Daemon: {base}  ({st.get('indexed_files')} files / "
          f"{st.get('total_chunks')} chunks)")
    print("=" * 80)

    cases = load_cases(args.corpus)
    if not cases:
        print(f"No cases found (corpus filter: {args.corpus!r})")
        return 0

    total = len(cases)
    n_pass = 0
    n_fail = 0
    skipped = 0

    for case in cases:
        cid = case["id"]
        query = case["query"]
        expect = case.get("expect", {})

        if not expect:
            skipped += 1
            print(f"\n[SKIP] {cid}: no assertions")
            continue

        ok, messages = run_case(case, base)
        mark = _PASS if ok else _FAIL
        print(f"\n[{mark}] {cid}  {query!r}")
        for msg in messages:
            print(msg)
        if ok:
            n_pass += 1
        else:
            n_fail += 1

    print("\n" + "=" * 80)
    print(f"Results: {n_pass} pass / {n_fail} fail / {skipped} skip  ({total} total)")
    print("=" * 80)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
