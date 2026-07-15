#!/usr/bin/env python3
"""Replay product_cases.jsonl against a live vectr daemon (via /v1 REST).

Usage:
    python3 run_acceptance.py [--port PORT] [--corpus CORPUS_FILTER]

Reads benchmarks/acceptance/product_cases.jsonl. For each case with a
matching corpus (or all cases if no filter), issues a /v1/search call (or
/v1/locate when the case sets "tool": "locate") and evaluates the 'expect'
assertions. Locate results are normalized to the same {file, symbol} shape
as search results, so the same assertion helpers apply to either tool.

Assertion rules
---------------
- top_k_contains: at least one result in the top-k has the expected file
  (substring of file path) AND the expected symbol (exact qualified name
  OR leaf match).
- top_k_absent: the given symbol and/or file must NOT appear (as a leaf/
  exact match for symbol, substring match for file) on ANY single result in
  the top-k — at least one of symbol/file is given; when both are given a
  result only counts as a match (i.e. fires the absent check) if it matches
  both, mirroring top_k_contains's AND-of-given-fields semantics.  Symbol is
  LEAF equality, not substring containment: the leaf is
  r["symbol"].split(".")[-1] — so symbol="read" correctly accepts
  r["symbol"]="HttpRequest.readlines" (leaf "readlines" != "read").  Old
  substring match caused false positives for this case and any like it
  ("all" ⊂ "recall", etc.).
- sorted_by_score: returned scores must be non-increasing.
- scores_in_unit_interval: every returned score is within [0, 1]. This is
  the current displayed-score contract (UPG-SCORE-DISPLAY-FLAT): score is
  absolute per-(query,chunk) relevance, not a composite ranking signal, so
  monotonicity with rank order is explicitly NOT required — only
  boundedness is.
- uniform_score_source: every result in the set shares the same
  score_source ("reranker" or "dense") — the two are structurally
  different measurements and must never be mixed in one displayed response
  (UPG-SCORE-DISPLAY-MIXED-SCALE).
- status_languages_include: /v1/status must list all named languages.
- affordance_expand_to_symbol: at least one result has symbol_start_line > 0.

A case whose 'expect' dict contains no key this harness recognizes (e.g. a
free-text 'notes'-only entry, or an assertion primitive not yet implemented
here) is reported as MANUAL rather than silently counted as a pass — it was
never actually checked. A case whose evaluation raises an unexpected
exception (a malformed 'expect' shape this harness doesn't anticipate) is
reported as ERROR and the run continues with the next case — one bad corpus
line must never truncate the rest of the suite.

Exit code: 0 if every evaluated case passes and no case errored, 1 otherwise.
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


def top_k_absent(results: list[dict], k: int, symbol: str | None = None,
                  file: str | None = None) -> bool:
    """Return True when NO result in the top-k matches the given criteria —
    the absent thing must not appear. When both symbol and file are given, a
    single result must match BOTH for the absent check to fire (the same
    AND-of-given-fields semantics as top_k_contains, negated). symbol=None
    and file=None together is a vacuous always-True check (matches a few
    pre-existing corpus entries that pair a real top_k_contains assertion
    with a no-op top_k_absent — preserved as-is, not treated as an error).

    symbol uses LEAF equality, not substring containment, to avoid false
    positives where the absent symbol is a strict prefix of a correct
    result's leaf.  For example:
        symbol="read", result symbol="HttpRequest.readlines"
        leaf("HttpRequest.readlines") = "readlines" != "read"  -> no match
        -> absent check PASSES (correct; readlines is the right answer)
    The old substring check ('absent_sym in r["symbol"]') would have
    matched "read" inside "readlines" and falsely reported a regression.
    Also checks the full qualified name for an exact match (to catch the
    case where the absent symbol IS the complete qualified form, e.g.
    "RemoveField.deconstruct").

    file is checked by substring (path suffix match), same as
    top_k_contains — for cases where the noise to guard against is an
    entire chunk/file rather than a specific symbol (e.g. a trivial-stub
    chunk with no meaningful symbol name at all).
    """
    if symbol is None and file is None:
        return True
    for r in results[:k]:
        file_ok = file is None or file in (r.get("file") or "")
        if symbol is None:
            sym_ok = True
        else:
            sym = r.get("symbol") or ""
            sym_ok = bool(sym) and (_symbol_leaf(sym) == symbol or sym == symbol)
        if file_ok and sym_ok:
            return False
    return True


def sorted_by_score(results: list[dict]) -> bool:
    scores = [r.get("score", 0.0) for r in results]
    return scores == sorted(scores, reverse=True)


def scores_in_unit_interval(results: list[dict]) -> bool:
    """Return True if every displayed score is within [0, 1].

    UPG-SCORE-DISPLAY-FLAT: the displayed score is absolute per-(query,chunk)
    relevance (the cross-encoder's calibrated sigmoid, or the bi-encoder
    cosine similarity when reranking didn't run) — not the internal ordering
    composite. Both underlying scales are naturally bounded to [0, 1], and
    that boundedness (not monotonicity with rank order, which this contract
    does not require) is what a caller thresholding on score can rely on.
    Vacuously True for an empty result list.
    """
    return all(0.0 <= r.get("score", 0.0) <= 1.0 for r in results)


def uniform_score_source(results: list[dict]) -> bool:
    """Return True if every result shares the same score_source.

    UPG-SCORE-DISPLAY-MIXED-SCALE: score_source is "reranker" (a calibrated
    cross-encoder judgment) or "dense" (a raw bi-encoder cosine similarity)
    — two structurally different measurements that are not comparable side
    by side. A displayed result set must never mix the two. Vacuously True
    for an empty result list.
    """
    sources = {r.get("score_source", "dense") for r in results}
    return len(sources) <= 1


def affordance_expand_to_symbol(results: list[dict]) -> bool:
    return any(r.get("symbol_start_line", 0) > 0 for r in results)


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"


def run_case(case: dict, base: str) -> tuple[bool | None, list[str]]:
    """Evaluate one product_cases.jsonl entry against the live daemon.

    Returns (all_pass, list_of_messages). ``all_pass`` is None — a distinct
    "manual" result, not a pass — when 'expect' contained no key this
    harness recognizes (e.g. a free-text 'notes'-only entry, or an
    assertion primitive not yet implemented here): such a case was never
    actually checked, so it must never be silently counted as a pass.
    """
    messages: list[str] = []
    results: list[dict] = []
    status: dict = {}

    query = case["query"]
    language = case.get("language")
    n_results = case.get("n_results", 5)
    expect = case.get("expect", {})
    tool = case.get("tool", "search")

    all_pass = True
    ran_any_assertion = False

    # Fetch /v1/status if needed for language coverage checks
    if "status_languages_include" in expect:
        try:
            status = _get(base, "/v1/status")
        except Exception as exc:
            messages.append(f"  ERROR fetching /v1/status: {exc}")
            return False, messages

    # Fetch results from the tool under test. Both /v1/search and /v1/locate
    # are normalized to a common {file, symbol, score, symbol_start_line}
    # shape so the same assertion helpers apply to either tool.
    if tool == "locate":
        try:
            resp = _post(base, "/v1/locate", {
                "name": query,
                "limit": n_results,
            })
            results = [
                {
                    "file": r.get("file_path") or "",
                    "symbol": r.get("name") or "",
                    "score": 0.0,
                    "symbol_start_line": r.get("start_line", 0),
                }
                for r in resp.get("results", [])
            ]
        except Exception as exc:
            messages.append(f"  ERROR fetching /v1/locate: {exc}")
            return False, messages
    else:
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
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        messages.append(
            f"  [{mark}] top_k_contains(k={spec['k']}, file={spec.get('file')!r}, "
            f"symbol={spec.get('symbol')!r})"
        )
        all_pass = all_pass and ok

    # --- top_k_absent ---
    if "top_k_absent" in expect:
        spec = expect["top_k_absent"]
        ok = top_k_absent(
            results, k=spec["k"], symbol=spec.get("symbol"), file=spec.get("file"),
        )
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        messages.append(
            f"  [{mark}] top_k_absent(k={spec['k']}, symbol={spec.get('symbol')!r}, "
            f"file={spec.get('file')!r}) [leaf-match]"
        )
        all_pass = all_pass and ok

    # --- sorted_by_score ---
    if expect.get("sorted_by_score"):
        ok = sorted_by_score(results)
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        scores = [round(r.get("score", 0.0), 4) for r in results]
        messages.append(f"  [{mark}] sorted_by_score  scores={scores}")
        all_pass = all_pass and ok

    # --- scores_in_unit_interval ---
    if expect.get("scores_in_unit_interval"):
        ok = scores_in_unit_interval(results)
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        scores = [round(r.get("score", 0.0), 4) for r in results]
        messages.append(f"  [{mark}] scores_in_unit_interval  scores={scores}")
        all_pass = all_pass and ok

    # --- uniform_score_source ---
    if expect.get("uniform_score_source"):
        ok = uniform_score_source(results)
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        sources = [r.get("score_source", "dense") for r in results]
        messages.append(f"  [{mark}] uniform_score_source  sources={sources}")
        all_pass = all_pass and ok

    # --- affordance_expand_to_symbol ---
    if expect.get("affordance_expand_to_symbol"):
        ok = affordance_expand_to_symbol(results)
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        messages.append(f"  [{mark}] affordance_expand_to_symbol")
        all_pass = all_pass and ok

    # --- status_languages_include ---
    if "status_languages_include" in expect:
        ran_any_assertion = True
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

    if not ran_any_assertion:
        messages.append(
            "  [MANUAL] 'expect' has no key this harness evaluates "
            f"(keys present: {sorted(expect.keys())!r}) — verify by hand"
        )
        return None, messages

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
    n_error = 0
    n_manual = 0
    skipped = 0

    for case in cases:
        cid = case["id"]
        query = case["query"]
        expect = case.get("expect", {})

        if not expect:
            skipped += 1
            print(f"\n[SKIP] {cid}: no assertions")
            continue

        # A malformed corpus entry (an 'expect' shape this harness doesn't
        # anticipate) must never take down the whole run — report it as an
        # error for this one case and keep going, so the remaining corpus is
        # still evaluated and the summary line reflects reality.
        try:
            ok, messages = run_case(case, base)
        except Exception as exc:
            n_error += 1
            print(f"\n[ERROR] {cid}  {query!r}")
            print(f"  {type(exc).__name__}: {exc}")
            continue

        if ok is None:
            n_manual += 1
            print(f"\n[MANUAL] {cid}  {query!r}")
            for msg in messages:
                print(msg)
            continue

        mark = _PASS if ok else _FAIL
        print(f"\n[{mark}] {cid}  {query!r}")
        for msg in messages:
            print(msg)
        if ok:
            n_pass += 1
        else:
            n_fail += 1

    print("\n" + "=" * 80)
    print(
        f"Results: {n_pass} pass / {n_fail} fail / {n_error} error / "
        f"{n_manual} manual / {skipped} skip  ({total} total)"
    )
    print("=" * 80)
    return 0 if n_fail == 0 and n_error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
