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

A case may carry an 'embed_model_stamp' field recording the embedding model
its 'expect' assertions were last verified under. When the running daemon's
/v1/status reports a different embed_model, the harness PRINTS a stamp
mismatch notice (UPG-ACCEPT-CORPUS-HYGIENE) — a stale stamp means the label
needs re-verification, not that the case should be treated as failing. A
mismatch is never a FAIL and never affects the exit code; it exists so a
future embedding-model default swap surfaces every case whose "passing"
label was never re-checked under the new model, instead of trusting it
indefinitely (see UPG-ACCEPT-REGRESSION-RECOVERY / the wave2 embedder swap).

A case may likewise carry a 'corpus_revision_stamp' recording the witness
revision its 'expect' assertions were verified against (UPG-CORPUS-
REVISION-STAMP): the git SHA of the external corpus checkout (full or >=7-char
abbreviation), the sentinel "in-repo" when the case's inputs are fixture files
versioned by this repository itself rather than by a separate checkout, or
"unknown" when the verifying revision could not be established. Before
replay, the harness resolves from /v1/status 'workspace_root' which revision
the daemon is actually serving (git rev-parse HEAD) and whether that working
tree is dirty (git status --porcelain); dirtiness is reported distinctly
because a SHA over a dirty tree does not describe the bytes being indexed.
A revision mismatch — or a dirty tree — PRINTS a notice, never a FAIL,
never affecting the exit code: same severity contract as embed_model_stamp,
it flags a label needing re-verification against the new corpus state, not
a product defect. A workspace that is not a git checkout (or an unavailable
git binary) degrades to a reported "unknown" served revision in the run
header, and stamped cases that could not be checked are counted in the run
summary — never silently passed as verified.

Exit code: 0 if every evaluated case passes and no case errored, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
# Corpus-revision stamps (UPG-CORPUS-REVISION-STAMP)
# ---------------------------------------------------------------------------

# Sentinel values of product_cases.jsonl 'corpus_revision_stamp' that carry
# provenance rather than a diffable revision:
_REVISION_STAMP_IN_REPO = "in-repo"   # inputs are fixture files versioned by THIS repo
_REVISION_STAMP_UNKNOWN = "unknown"   # verifying witness revision could not be established

# A stamp is comparable against a served revision only when it looks like a
# git SHA. 7 chars is the shortest abbreviation git itself renders unambiguously.
_HEX_REVISION_RE = re.compile(r"[0-9a-f]{7,40}")


def comparable_revision(stamp: object) -> str | None:
    """Normalize a 'corpus_revision_stamp' into a comparable revision, or None.

    Only a git SHA (7-40 hex chars, case-insensitive) is comparable; the
    "in-repo"/"unknown" sentinels — and any other value — describe the
    stamp's provenance, not a revision to diff the served workspace against.
    """
    if not isinstance(stamp, str):
        return None
    s = stamp.strip().lower()
    return s if _HEX_REVISION_RE.fullmatch(s) else None


def _git(args: list[str], cwd: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run one read-only git query inside cwd. Never raises for a missing
    binary or a hung git — callers catch those and degrade to 'unknown'."""
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def resolve_served_revision(workspace_root: object) -> dict:
    """Determine the git state of the workspace the daemon is serving.

    Returns {"state": <str>, "revision": <str|None>, "detail": <str|None>}
    where state is one of:
      "clean"               — a git repo at `revision`, working tree clean;
                              the SHA then describes the indexed bytes
      "dirty"               — a repo at `revision` with uncommitted OR
                              untracked changes present; the HEAD SHA does
                              NOT describe the bytes being indexed
      "cleanliness-unknown" — a repo at `revision` whose dirtiness could not
                              be determined (`git status` failed)
      "not-a-git-repo"      — the root exists but is not a git checkout
      "no-git-binary"       — git could not be executed at all
      "unknown"             — anything else (no workspace_root, missing dir,
                              git error)

    Never raises and never guesses: every unresolvable condition degrades to
    an explicit reported state, never to a fabricated revision.
    """
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        return {
            "state": "unknown",
            "revision": None,
            "detail": "/v1/status reported no usable workspace_root",
        }
    root = os.path.expanduser(workspace_root.strip())
    if not os.path.isdir(root):
        return {
            "state": "unknown",
            "revision": None,
            "detail": f"workspace_root not found on this machine: {root!r}",
        }
    try:
        probe = _git(["rev-parse", "--verify", "HEAD"], root)
    except FileNotFoundError:
        return {"state": "no-git-binary", "revision": None,
                "detail": "git executable not found"}
    except subprocess.TimeoutExpired:
        return {"state": "unknown", "revision": None,
                "detail": "git rev-parse timed out"}
    except OSError as exc:
        return {"state": "unknown", "revision": None, "detail": f"git failed: {exc}"}
    err = (probe.stderr or "").strip()
    if probe.returncode != 0:
        if "not a git repository" in err.lower():
            return {"state": "not-a-git-repo", "revision": None, "detail": None}
        return {"state": "unknown", "revision": None,
                "detail": err[:200] or "git rev-parse failed"}
    revision = probe.stdout.strip()
    try:
        status = _git(["status", "--porcelain"], root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "cleanliness-unknown", "revision": revision,
                "detail": f"git status failed: {exc}"}
    if status.returncode != 0:
        return {"state": "cleanliness-unknown", "revision": revision,
                "detail": ((status.stderr or "").strip() or "git status failed")[:200]}
    dirty = bool(status.stdout.strip())
    return {"state": "dirty" if dirty else "clean",
            "revision": revision, "detail": None}


def describe_served_revision(served: dict) -> str:
    """One human-readable line about the served workspace's git state."""
    state = served.get("state")
    revision = served.get("revision")
    detail = served.get("detail")
    if state == "clean":
        return f"{revision} (working tree clean)"
    if state == "dirty":
        return (
            f"{revision} (DIRTY working tree — uncommitted/untracked changes; "
            "this SHA does not describe the indexed bytes)"
        )
    if state == "cleanliness-unknown":
        return f"{revision} (working-tree cleanliness undetermined: {detail})"
    if state == "not-a-git-repo":
        return "unknown (workspace is not a git repository)"
    if state == "no-git-binary":
        return "unknown (git is not available to this harness)"
    return f"unknown ({detail or 'unresolvable'})"


def classify_revision_stamp(case_stamp: object, served: dict) -> str:
    """Classify one case's corpus_revision_stamp against the served workspace.

    Returns one of:
      "match"             — served tree is clean at the stamped revision
      "dirty-match"       — stamped revision is checked out, but the tree is
                            dirty (or its cleanliness unknown), so matching
                            SHAs still do not prove the indexed bytes are the
                            labelled ones
      "mismatch"          — clean tree at a DIFFERENT revision than stamped:
                            genuine corpus drift vs the label
      "mismatch-dirty"    — different revision AND unverifiable bytes
      "served-unresolved" — the case carries a comparable stamp but the run
                            could not resolve a served revision
      "unstamped"         — no comparable revision ("in-repo"/"unknown"/absent)
    """
    rev = comparable_revision(case_stamp)
    if rev is None:
        return "unstamped"
    served_rev = served.get("revision")
    if not isinstance(served_rev, str) or not served_rev:
        return "served-unresolved"
    served_rev = served_rev.lower()
    # Either side may be an abbreviated SHA; accept a shared prefix in both
    # directions (stamps written by this harness are full SHAs, but >=7-char
    # abbreviations recorded by hand must still match their full form).
    same_revision = served_rev.startswith(rev) or rev.startswith(served_rev)
    clean = served.get("state") == "clean"
    if same_revision:
        return "match" if clean else "dirty-match"
    return "mismatch" if clean else "mismatch-dirty"


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

    # --- top_k_contains_any_of ---
    # UPG-HARNESS-TOPK-ANY-OF-EVALUATOR: an F56-shaped case passes when the top-k
    # contains AT LEAST ONE of several acceptable answers (a query with more than
    # one defensible canonical result). Each candidate is a {file?, symbol?} spec
    # evaluated with the same top_k_contains semantics; the assertion passes if
    # any candidate is present.
    if "top_k_contains_any_of" in expect:
        spec = expect["top_k_contains_any_of"]
        candidates = spec.get("candidates", [])
        ok = any(
            top_k_contains(
                results, k=spec["k"], file=cand.get("file"), symbol=cand.get("symbol"),
            )
            for cand in candidates
        )
        ran_any_assertion = True
        mark = _PASS if ok else _FAIL
        cand_desc = " | ".join(
            f"{c.get('file')!r}:{c.get('symbol')!r}" for c in candidates
        )
        messages.append(
            f"  [{mark}] top_k_contains_any_of(k={spec['k']}, candidates=[{cand_desc}])"
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

    current_embed_model = st.get("embed_model")

    # Served-corpus revision resolution (UPG-CORPUS-REVISION-STAMP): resolved
    # ONCE per run — every case in a run shares the daemon's single workspace.
    served_revision = resolve_served_revision(st.get("workspace_root"))
    print(f"Served workspace: {st.get('workspace_root') or '(none reported)'}")
    print(f"Served revision: {describe_served_revision(served_revision)}")

    total = len(cases)
    n_pass = 0
    n_fail = 0
    n_error = 0
    n_manual = 0
    skipped = 0
    n_stamp_mismatch = 0
    n_rev_mismatch = 0
    n_rev_dirty_match = 0
    n_rev_unchecked = 0
    n_rev_unstamped = 0

    for case in cases:
        cid = case["id"]
        query = case["query"]
        expect = case.get("expect", {})

        # Stamp-vs-current-embedder check (UPG-ACCEPT-CORPUS-HYGIENE): a
        # print-only signal, never a FAIL — it flags a case whose 'expect'
        # was last verified under a different embedding model than the one
        # this daemon is running, so a stale "passing" label can be caught
        # mechanically instead of trusted indefinitely.
        stamp = case.get("embed_model_stamp")
        if stamp and current_embed_model and stamp != current_embed_model:
            n_stamp_mismatch += 1
            print(
                f"\n[STAMP MISMATCH] {cid}: verified under {stamp!r}, "
                f"daemon is running {current_embed_model!r} — needs re-verification"
            )

        # Stamp-vs-served-revision check (UPG-CORPUS-REVISION-STAMP): also
        # print-only, same reasoning. Mismatches are printed per case; a dirty
        # tree that still matches the stamp is reported once in the summary,
        # since it affects every stamped case identically.
        rev_stamp = case.get("corpus_revision_stamp")
        rev_verdict = classify_revision_stamp(rev_stamp, served_revision)
        if rev_verdict == "mismatch":
            n_rev_mismatch += 1
            print(
                f"\n[REVISION MISMATCH] {cid}: {case.get('corpus')!r} expect was "
                f"verified against corpus revision {rev_stamp!r}, but the served "
                f"workspace is at {served_revision.get('revision')!r} — needs "
                "re-verification"
            )
        elif rev_verdict == "mismatch-dirty":
            n_rev_mismatch += 1
            print(
                f"\n[REVISION MISMATCH] {cid}: {case.get('corpus')!r} expect was "
                f"verified against corpus revision {rev_stamp!r}, but the served "
                f"workspace is at {served_revision.get('revision')!r} (and its "
                "working tree is dirty, so even that SHA may not describe the "
                "indexed bytes) — needs re-verification"
            )
        elif rev_verdict == "dirty-match":
            n_rev_dirty_match += 1
        elif rev_verdict == "served-unresolved":
            n_rev_unchecked += 1
        elif rev_verdict == "unstamped":
            n_rev_unstamped += 1
        # "match": served tree is cleanly at the stamped revision — silent.

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
    if n_stamp_mismatch:
        print(
            f"{n_stamp_mismatch} case(s) stamped under a different embed model than "
            f"the current daemon ({current_embed_model!r}) — see [STAMP MISMATCH] "
            "lines above. Informational only; does not affect pass/fail or exit code."
        )
    if n_rev_mismatch:
        print(
            f"{n_rev_mismatch} case(s) stamped against a different corpus revision "
            "than the served workspace is serving — see [REVISION MISMATCH] lines "
            "above. Informational only; does not affect pass/fail or exit code."
        )
    if n_rev_dirty_match:
        print(
            f"{n_rev_dirty_match} case(s) match their stamped corpus revision, but "
            "the served workspace's working tree is dirty (or its cleanliness could "
            "not be determined), so the HEAD SHA does not prove the indexed bytes "
            "are the ones the labels were verified against. Informational only."
        )
    if n_rev_unchecked:
        print(
            f"{n_rev_unchecked} stamped case(s) could not be revision-checked this "
            f"run (served revision unresolved: {served_revision.get('state')!r}). "
            "Informational only."
        )
    if n_rev_unstamped:
        print(
            f"{n_rev_unstamped} case(s) carry no comparable corpus_revision_stamp "
            "(in-repo / unknown / none) — a flip on them cannot be mechanically "
            "attributed to corpus drift."
        )
    print("=" * 80)
    return 0 if n_fail == 0 and n_error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
