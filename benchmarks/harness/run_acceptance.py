#!/usr/bin/env python3
"""Replay product_cases.jsonl against a live vectr daemon.

Usage:
    python3 run_acceptance.py [--port PORT] [--corpus CORPUS_FILTER]
                              [--surface {rest,mcp}] [--strict-status]

Reads benchmarks/acceptance/product_cases.jsonl. For each case with a
matching corpus (or all cases if no filter), issues a /v1/search call (or
/v1/locate when the case sets "tool": "locate") and evaluates the 'expect'
assertions. Locate results are normalized to the same {file, symbol} shape
as search results, so the same assertion helpers apply to either tool.

Surface selection (UPG-ACCEPTANCE-MCP-MODE):
  --surface rest   (default) — POST /v1/search, /v1/locate, /v1/status.
                              This is the legacy behaviour, bit-for-bit.
  --surface mcp              — JSON-RPC POST /mcp with tools/call envelopes
                              for vectr_search / vectr_locate. The surface
                              the real caller LLM actually sees. The /v1/
                              status probe stays on REST either way.
  The default is preserved as REST so no existing script silently changes
  meaning; the new mode is opt-in via the explicit --surface flag.

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
- low_confidence_absent (MCP only): the MCP low-confidence banner did NOT
  fire for this case. REST mode silently skips with a [SKIP] notice — the
  banner signal is not in the /v1 JSON response, so the assertion is
  structurally uncheckable on the wrong surface. Silently passing on REST
  would be worse than skipping: a future "failing→green" flip would land
  with the assertion never actually checked. When `low_confidence_absent`
  is `false`, the assertion inverts (banner MUST have fired); same
  surface-skip rule applies.
- body_present (MCP search only): the rank-1 result carried a code body,
  not a pointer. REST mode silently skips with a [SKIP] notice — REST
  responses always include `content`, so the assertion is structurally
  always true there. When `body_present` is `false`, the assertion inverts
  (rank-1 must be a pointer); same surface-skip rule applies.

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


def _post_raw(base: str, path: str, body: dict, headers: dict | None = None) -> tuple[dict, dict]:
    """POST JSON and return (parsed_json_body, response_headers_dict).

    Required by the MCP transport (UPG-ACCEPTANCE-MCP-MODE): the daemon assigns
    a session id in the `Mcp-Session-Id` response header on `initialize`, and a
    compliant client echoes that same header on every subsequent request — so
    the harness has to capture the response headers, not just the body.
    The legacy REST `_post` ignores headers entirely; this helper is the
    MCP-mode-only escape hatch that does not change REST behaviour.
    """
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers=merged,
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        # email.message is duck-typed by urllib's response; iterate to a plain dict.
        out = {k: v for k, v in r.headers.items()}
        return json.load(r), out


# JSON-RPC envelope ids (UPG-ACCEPTANCE-MCP-MODE). Two ids are enough: the
# session-establishing `initialize` is its own conversation, then every
# tools/call uses the same id since the harness never issues concurrent
# requests. The daemon echoes id verbatim; mismatch is an error.
_MCP_RPC_ID_INIT = 0
_MCP_RPC_ID_CALL = 1


def mcp_initialize(base: str) -> str:
    """Drive the MCP `initialize` handshake and return the assigned session id.

    Per app/routes.py:1000-1072, the daemon's `initialize` handler mints a
    UUID4-hex session id and returns it in the `Mcp-Session-Id` response
    header. The harness echoes that header on every subsequent tools/call,
    mirroring what a real MCP client (Claude Code, etc.) does. No idempotency
    concerns: a fresh session is cheap, and the harness is the only client
    in a one-off replay run.
    """
    body = {
        "jsonrpc": "2.0",
        "id": _MCP_RPC_ID_INIT,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vectr-acceptance-harness", "version": "0.0"},
        },
    }
    data, headers = _post_raw(base, "/mcp", body)
    sid = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
    if not sid:
        raise RuntimeError(
            f"MCP initialize did not return Mcp-Session-Id header; got headers={sorted(headers)!r}"
        )
    if data.get("id") != _MCP_RPC_ID_INIT:
        raise RuntimeError(f"MCP initialize echoed wrong id: {data.get('id')!r}")
    return sid


def mcp_call(base: str, session_id: str, tool_name: str,
             arguments: dict) -> dict:
    """Drive one MCP `tools/call` and return the `result` field (or raise).

    The session id is passed via the `Mcp-Session-Id` request header, the
    transport's contract for every call after `initialize` (per
    app/routes.py:1000-1072, UPG-MCP-SESSION-ID-HANDSHAKE). The result shape
    is `{content: [{type, text}], isError}` per MCP — `text` is the rendered
    response the caller LLM actually sees, and the only thing this harness
    can assert over in MCP mode.
    """
    body = {
        "jsonrpc": "2.0",
        "id": _MCP_RPC_ID_CALL,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    data, _headers = _post_raw(base, "/mcp", body, headers={"Mcp-Session-Id": session_id})
    if data.get("id") != _MCP_RPC_ID_CALL:
        raise RuntimeError(f"MCP tools/call echoed wrong id: {data.get('id')!r}")
    if "error" in data:
        raise RuntimeError(
            f"MCP tools/call returned JSON-RPC error: {data['error']!r}"
        )
    return data.get("result") or {}


def _mcp_text(mcp_result: dict) -> str:
    """Extract the caller's-visible text from a tools/call result.

    `result.content` is always a list per MCP; each item is `{type, text}`.
    The renderer never produces multi-item content for vectr_search or
    vectr_locate (they return a single text block with embedded sections);
    concatenating defensively is harmless and tolerant of future schema
    extensions. `isError=True` results are surfaced as a single error string
    that the parser will treat as no-results, matching REST-mode behaviour
    when a non-existent locate target returns empty.
    """
    if not mcp_result:
        return ""
    parts: list[str] = []
    for item in mcp_result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


# Sentinel substrings the MCP text renderer writes (integrations/mcp_server/_dispatch.py).
# Anchoring on these lets the harness detect what the real caller actually
# sees (banner, pointer-mode rendering, score format) without re-implementing
# the renderer or asking the daemon for a parallel structured response.
_MCP_BANNER = "─── Low confidence ───"  # the literal low-confidence banner
_MCP_SEPARATOR = "─" * 60                # the 60-dash per-result separator
# A result is in pointer mode (no body) iff the per-result section is empty
# between the `symbol:` line and the next `_MCP_SEPARATOR` — the renderer
# inserts a single blank line in pointer mode (see _dispatch.py:1645-1649).


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


# ---------------------------------------------------------------------------
# MCP text parser (UPG-ACCEPTANCE-MCP-MODE)
# ---------------------------------------------------------------------------
# The MCP `tools/call` result is `{content: [{type, text}], isError}` — the
# `text` field is the formatted, prose response the real caller LLM actually
# reads. To keep the existing rank-aware assertions (top_k_contains, etc.)
# testable in MCP mode, the harness re-derives a per-result structured shape
# from that text by anchoring on the renderer's own punctuation. The parser
# is deliberately loose — it does not need to be a perfect round-trip of the
# renderer, only to recover enough to assert rank-1 symbol/file/has-body, the
# three things every acceptance-case `expect` actually depends on.
#
# A parsed per-result entry is:
#   {"rank": int, "file": str, "lines": str, "symbol": str, "score": float,
#    "score_source": str, "body_present": bool, "raw_block": list[str]}
# Plus a top-level `low_confidence` flag (whether the banner appeared).

# `[1] path:start-end  score 0.864 (reranker)` (with possible source/dup/rank_note)
_MCP_RESULT_HEADER_RE = re.compile(
    r"^\[(\d+)\]\s+(?P<file>[^:]+):(?P<lines>\S+)\s+score\s+(?P<score>\d+\.\d+)"
    r"(?:\s+\((?P<source>\w+)\))?"
)
# `    symbol: Name  [lines X–Y]  language: L`  (en-dash line-range optional)
_MCP_SYMBOL_RE = re.compile(
    r"^\s{4}symbol:\s+(?P<sym>\S[^[]*?)(?:\s+\[lines\s+\d+[–-]\d+\])?\s+language:\s+(?P<lang>\S+)\s*$"
)


def parse_mcp_search_text(text: str) -> dict:
    """Parse a vectr_search MCP text response into a structured form.

    Returns {"low_confidence": bool, "results": [parsed_result, ...]}.
    Empty result list when the renderer emits "No results found for: ...".
    """
    out: dict = {"low_confidence": _MCP_BANNER in text, "results": []}
    if "No results found for:" in text and "Found" not in text:
        return out
    blocks = text.split(_MCP_SEPARATOR)
    # The first split element is the response header (before the first
    # 60-dash separator); subsequent elements are the per-result blocks.
    for raw in blocks[1:]:
        if not raw.strip():
            continue
        lines = raw.splitlines()
        # First non-blank line is `[N] path:start-end  score S.SSS (source)?`
        header_line = next((l for l in lines if l.strip()), "")
        m = _MCP_RESULT_HEADER_RE.match(header_line)
        if not m:
            # The banner `─── Low confidence ───` and similar mid-text
            # sections also live between separators and should be skipped
            # silently — they have no `[N]` header.
            continue
        file_path = m.group("file")
        line_range = m.group("lines")
        score = float(m.group("score"))
        source = m.group("source") or ""
        # Find the symbol line (if any) within the same block.
        symbol_name = ""
        for sl in lines:
            sm = _MCP_SYMBOL_RE.match(sl)
            if sm:
                symbol_name = sm.group("sym").strip()
                break
        # Body presence: in pointer mode the block ends right after the
        # symbol line with a single blank line (see _dispatch.py:1645-1649).
        # Anything beyond the symbol+language line, up to the next block,
        # is body content. We say a body is PRESENT iff at least one
        # non-blank, non-symbol, non-language line exists in the block.
        # Equivalent definition: a block whose only non-blank lines are
        # the header and the symbol line has no body.
        body_present = False
        for sl in lines:
            if not sl.strip():
                continue
            if sl is header_line:
                continue
            if _MCP_SYMBOL_RE.match(sl):
                continue
            body_present = True
            break
        out["results"].append({
            "rank": int(m.group(1)),
            "file": file_path,
            "lines": line_range,
            "symbol": symbol_name,
            "score": score,
            "score_source": source,
            "body_present": body_present,
            "raw_block": lines,
        })
    return out


def parse_mcp_locate_text(text: str) -> dict:
    """Parse a vectr_locate MCP text response.

    The locate renderer (service.format_locate) emits per-symbol blocks; we
    only need a minimal extraction so the same `top_k_contains`/`top_k_absent`
    assertions work. Anything more would duplicate the renderer's own
    formatting choices — kept minimal intentionally.
    """
    # The `format_locate` output is plain text not split by `─` separators
    # in the same shape, so we fall back to a coarser parse: split on
    # `name:`/`path:` markers and recover the first hit. The harness in MCP
    # mode treats locate-mode cases as MANUAL (REST `/v1/locate` is the
    # structured path; the brief's named-witness cases F44/F47/F74 are
    # search-mode), so this parser exists for completeness and to keep the
    # assertion code paths symmetric, not because it's load-bearing.
    return {"low_confidence": _MCP_BANNER in text, "results": []}


def run_case(case: dict, base: str,
             surface: str = "rest",
             mcp_session_id: str | None = None) -> tuple[bool | None, list[str]]:
    """Evaluate one product_cases.jsonl entry against the live daemon.

    Returns (all_pass, list_of_messages). ``all_pass`` is None — a distinct
    "manual" result, not a pass — when 'expect' contained no key this
    harness recognizes (e.g. a free-text 'notes'-only entry, or an
    assertion primitive not yet implemented here): such a case was never
    actually checked, so it must never be silently counted as a pass.

    `surface` is "rest" (default — POST /v1/search, /v1/locate) or "mcp"
    (POST /mcp with JSON-RPC tools/call envelopes for vectr_search and
    vectr_locate). The MCP path is a non-default, opt-in mode (UPG-ACCEPTANCE-
    MCP-MODE); REST is preserved bit-for-bit so existing invocations and
    pass/fail counts do not silently change meaning.

    `mcp_session_id` is the session id from a prior `mcp_initialize` call —
    required when surface="mcp", ignored otherwise. The session is created
    once per run in `main()` and reused across all cases, mirroring what a
    real MCP client (Claude Code, etc.) does over a multi-tool conversation.
    """
    messages: list[str] = []
    results: list[dict] = []
    status: dict = {}
    mcp_text: str | None = None   # raw MCP text response (when surface=="mcp")
    mcp_low_conf: bool = False     # low_confidence banner detected in MCP text

    query = case["query"]
    language = case.get("language")
    n_results = case.get("n_results", 5)
    expect = case.get("expect", {})
    tool = case.get("tool", "search")

    all_pass = True
    ran_any_assertion = False

    # Fetch /v1/status if needed for language coverage checks.
    # /v1/status is surface-agnostic — same endpoint, same shape regardless
    # of which protocol is used to issue search/locate calls. Keeping the
    # status fetch on REST is the simplest contract: the harness does not
    # re-enter MCP just to read a status field.
    if "status_languages_include" in expect:
        try:
            status = _get(base, "/v1/status")
        except Exception as exc:
            messages.append(f"  ERROR fetching /v1/status: {exc}")
            return False, messages

    # Fetch results from the tool under test. Both surfaces normalize to a
    # common {file, symbol, score, symbol_start_line} shape so the same
    # assertion helpers apply; MCP additionally exposes the raw text and the
    # low_confidence banner detection for the new banner/body assertions.
    if surface == "mcp":
        if not mcp_session_id:
            messages.append(
                "  ERROR: surface='mcp' requires a non-empty mcp_session_id"
            )
            return False, messages
        try:
            if tool == "locate":
                mcp_result = mcp_call(base, mcp_session_id, "vectr_locate", {
                    "name": query, "limit": n_results,
                })
                parsed = parse_mcp_locate_text(_mcp_text(mcp_result))
                results = parsed["results"]
                mcp_low_conf = parsed["low_confidence"]
                mcp_text = _mcp_text(mcp_result)
            else:
                arguments: dict = {"query": query, "n_results": n_results}
                if language:
                    arguments["language"] = language
                mcp_result = mcp_call(base, mcp_session_id, "vectr_search", arguments)
                mcp_text = _mcp_text(mcp_result)
                parsed = parse_mcp_search_text(mcp_text)
                results = parsed["results"]
                mcp_low_conf = parsed["low_confidence"]
                # Propagate the parsed body's body_present into the same
                # `results` shape the existing assertion helpers consume, so
                # body_present assertions can re-use top_k_contains.
                for r in results:
                    r["body_present"] = bool(r.get("body_present"))
        except Exception as exc:
            messages.append(f"  ERROR fetching MCP tools/call: {exc}")
            return False, messages
    elif tool == "locate":
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

    # --- low_confidence_absent (UPG-ACCEPTANCE-MCP-MODE) ---
    # Asserts the MCP low-confidence banner did NOT fire for this case. The
    # banner ("─── Low confidence ───") is the caller LLM's explicit signal
    # that the whole result set may be a guess; firing it on a case whose
    # expected answer is at rank 1 turns a "passing" case into a delivered
    # "no, really, go grep" answer, and the corpus was structurally blind to
    # it before this lane (the case's expected symbol being at rank 1 is
    # checked over /v1, but the banner is MCP-only).
    #
    # REST-mode behaviour: a `/v1/search` response has no banner field — the
    # low_confidence signal lives on results.low_confidence (a list subclass
    # attribute, not in the JSON). The REST path cannot tell whether the MCP
    # caller would see a banner, so the assertion is only meaningful in MCP
    # mode. In REST mode, the assertion is SILENTLY SKIPPED with a printed
    # notice (not a fail, not a pass, not MANUAL) — same shape as
    # corpus-revision-stamp mismatches: a known gap in the surface the case
    # is being run over. The alternative (silently passing) would be worse:
    # a future label "failing→green" would be applied with the assertion
    # never actually checked.
    if "low_confidence_absent" in expect:
        expected_absent = bool(expect["low_confidence_absent"])
        ran_any_assertion = True
        if surface != "mcp":
            messages.append(
                f"  [SKIP] low_confidence_absent={expected_absent} — only meaningful on "
                "the MCP surface (this run is on /v1); banner signal not in the REST response"
            )
        else:
            ok = (mcp_low_conf is False) if expected_absent else (mcp_low_conf is True)
            mark = _PASS if ok else _FAIL
            messages.append(
                f"  [{mark}] low_confidence_absent={expected_absent}  "
                f"banner_fired={mcp_low_conf}"
            )
            all_pass = all_pass and ok

    # --- body_present (UPG-ACCEPTANCE-MCP-MODE) ---
    # Asserts that the rank-1 result (the caller's actual first read) carried
    # a code body, not a pointer. Pointer mode (UPG-LOWCONF-OUTPUT-SLIM) is
    # the right design for a set the banner flagged as guess, but it is
    # catastrophic when the set is in fact correct: a "passing" case whose
    # rank-1 symbol is the expected answer still forces the caller to spend
    # another round trip calling vectr_fetch to read it.
    #
    # Symmetric to low_confidence_absent: meaningful only in MCP mode, where
    # the renderer actually decides pointer vs body per-result. The
    # `top_k_contains` expectation of "expected symbol at rank 1" alone
    # could not catch this — the result IS at rank 1, the BODY is what's
    # missing.
    #
    # REST-mode behaviour: same shape as low_confidence_absent — the REST
    # JSON response always carries `content`, so body_present is structurally
    # always True over /v1/search. The assertion is silently SKIPPED in REST
    # mode with a printed notice, not silently passed.
    if "body_present" in expect:
        expected_present = bool(expect["body_present"])
        ran_any_assertion = True
        if surface != "mcp" or tool != "search":
            messages.append(
                f"  [SKIP] body_present={expected_present} — only meaningful on the "
                "MCP search surface (this run is on "
                f"{'/'+surface} {'/'+tool if tool else 'search'}); REST always carries body"
            )
        else:
            rank1 = results[0] if results else None
            body_ok = bool(rank1 and rank1.get("body_present"))
            ok = body_ok if expected_present else (not body_ok)
            mark = _PASS if ok else _FAIL
            sym = (rank1 or {}).get("symbol", "(no rank1)")
            messages.append(
                f"  [{mark}] body_present={expected_present}  "
                f"rank1={sym!r}  rank1_body_present={body_ok}"
            )
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
    parser.add_argument(
        "--surface", choices=("rest", "mcp"), default="rest",
        help=(
            "Which vectr surface to drive. 'rest' (default) keeps the legacy "
            "behaviour: POST /v1/search, /v1/locate, /v1/status. 'mcp' "
            "drives the JSON-RPC /mcp endpoint with tools/call envelopes "
            "for vectr_search/vectr_locate, the surface the real caller LLM "
            "actually sees. The /v1/status probe stays on REST either way "
            "(status is surface-agnostic). Surface is an explicit flag, not "
            "a default flip, so no existing script silently changes meaning."
        ),
    )
    parser.add_argument(
        "--strict-status", action="store_true",
        help=(
            "UPG-ACCEPTANCE-MCP-MODE part 3: when set, the harness compares "
            "each case's recorded 'status' field against its observed outcome "
            "and fails on mismatch (the corpus's 'failing'/'passing'/'green' "
            "labels stop being pure documentation and start being machine-"
            "checked). Off by default to preserve the legacy corpus; the "
            "active corpus today has four labels known to be drifted, so "
            "flipping this without re-stamping the drifted cases turns them "
            "red deliberately — see LANE-REPORT.md for which cases and why."
        ),
    )
    args = parser.parse_args(argv)

    base = f"http://localhost:{args.port}"

    # Verify daemon is reachable
    try:
        st = _get(base, "/v1/status")
    except Exception as exc:
        print(f"ERROR: cannot reach daemon at {base}: {exc}", file=sys.stderr)
        return 1

    # MCP mode: do the JSON-RPC `initialize` handshake ONCE per run and reuse
    # the session id across every case. Mirrors what a real MCP client (Claude
    # Code, etc.) does over a multi-tool conversation. Fails loudly (non-zero
    # exit) if the daemon doesn't return a Mcp-Session-Id header — that
    # contract is set in app/routes.py:1022-1028 and the harness should
    # notice the moment a future refactor breaks it.
    mcp_session_id: str | None = None
    if args.surface == "mcp":
        try:
            mcp_session_id = mcp_initialize(base)
        except Exception as exc:
            print(f"ERROR: MCP initialize failed: {exc}", file=sys.stderr)
            return 1

    print("=" * 80)
    print(f"Acceptance replay — {_CASES_PATH.name}")
    print(f"Surface: {args.surface}{'  [session: ' + mcp_session_id[:8] + '...]' if mcp_session_id else ''}")
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
    n_status_mismatch = 0
    n_mcp_skipped = 0

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
            ok, messages = run_case(
                case, base,
                surface=args.surface,
                mcp_session_id=mcp_session_id,
            )
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

        # Count surface-skipped assertions separately so a corpus run can
        # report them in the summary without inflating pass/fail/manual. A
        # MCP-only assertion (low_confidence_absent / body_present) on a
        # REST run is a known information gap, not a failure and not
        # something the caller should have to read the body of every case
        # to notice.
        mcp_skips = sum(1 for m in messages if "[SKIP]" in m and "only meaningful" in m)
        if mcp_skips:
            n_mcp_skipped += mcp_skips

        # UPG-ACCEPTANCE-MCP-MODE part 3: --strict-status compares the
        # recorded 'status' field against the observed outcome. The
        # comparison is ONLY made when the case was actually evaluated (ok
        # is True or False, not None for manual), and ONLY when the recorded
        # label is one the harness recognises. A drift is reported as
        # [STATUS DRIFT] per case and counted in the summary; it
        # contributes to n_fail so a reviewer can't accidentally land a
        # drifted label as a passing test gate.
        if args.strict_status and ok is not None:
            recorded = case.get("status")
            expected_outcome = "passing" if ok else "failing"
            # The corpus has historically used both 'passing' and 'green'
            # for "case currently passes"; treat them as equivalent so the
            # check fires on label drift, not on a synonym swap.
            normalised_recorded = (
                "passing" if recorded in ("passing", "green") else recorded
            )
            if recorded is not None and normalised_recorded != expected_outcome:
                n_status_mismatch += 1
                messages.append(
                    f"  [STATUS DRIFT] recorded={recorded!r}  observed={expected_outcome!r} "
                    f"— see LANE-REPORT.md 'status drift' section"
                )
                ok = False  # strict-status mismatches fail the gate

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
    if n_mcp_skipped:
        print(
            f"{n_mcp_skipped} MCP-only assertion(s) skipped on this {args.surface} run "
            "— see [SKIP] lines above. Informational only; assertion is meaningful "
            "on the OTHER surface."
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
    if n_status_mismatch:
        print(
            f"{n_status_mismatch} case(s) had a recorded 'status' label that disagreed "
            "with the observed outcome — see [STATUS DRIFT] lines above. These were "
            "counted as fails by --strict-status. Re-stamp the recorded label after "
            "re-verification, or drop --strict-status if the label is intentionally "
            "aspirational."
        )
    print("=" * 80)
    return 0 if n_fail == 0 and n_error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
