#!/usr/bin/env python3
"""Mine real `vectr_recall`/`vectr_search` query strings out of session
transcript jsonl files, as raw material for hand-labeling a recall-miss
query set (UPG-RECALL-MISS-FLOOR part (a)).

This is a DATA-PREP utility, not part of any retrieval path — it never
runs at recall time and never decides what a live query means. Extraction
is purely STRUCTURAL: it walks the transcript's own JSON schema (assistant
turns -> tool_use blocks -> a `name` ending in the tool name -> that call's
own `input.query` field) and never inspects the text of the query itself
to decide anything. This is the same category of "structural, not
content-conditional" parsing already established for transcript/command
handling elsewhere in vectr (see the leading-`cd` parse in
UPG-ARC-CWD-VS-EFFECTIVE-DIR) — it is not the query-side heuristic the
standing no-heuristics rule forbids, because nothing here reroutes,
reclassifies, or scores a live retrieval call.

Output is a deduplicated, frequency-sorted list of query strings printed to
stdout for a human to review, select from, and pair with hand-authored
notes + a written labeling basis (see fixture_corpus.py) — this script
produces candidates, never labels.

Usage:
    python3 benchmarks/recall_miss/mine_prompts.py \\
        --project ~/.claude/projects/-Users-you-dev-yourrepo \\
        --tool recall --min-len 8 --max-len 200 --limit 40

`--tool` selects which vectr tool's `query` argument to mine: "recall"
(mcp__vectr__vectr_recall) or "search" (mcp__vectr__vectr_search).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
from pathlib import Path

# Structural-only bounds on candidate length, purely to keep degenerate
# entries (empty strings, multi-paragraph pastes) out of a human reviewer's
# way — not a content classifier, never inspects what the text SAYS.
_DEFAULT_MIN_LEN = 4
_DEFAULT_MAX_LEN = 300


def _tool_suffix(tool: str) -> str:
    if tool == "recall":
        return "vectr_recall"
    if tool == "search":
        return "vectr_search"
    raise ValueError(f"unknown --tool {tool!r}, expected 'recall' or 'search'")


def mine_queries(
    project_dir: Path,
    tool_suffix: str,
    min_len: int = _DEFAULT_MIN_LEN,
    max_len: int = _DEFAULT_MAX_LEN,
) -> Counter:
    """Return a Counter of query-string -> occurrence count, scanning every
    *.jsonl transcript under `project_dir`. Best-effort: a malformed line or
    file is skipped, never raises."""
    counts: Counter = Counter()
    for path in sorted(glob.glob(str(project_dir / "*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    content = (obj.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        if not name.endswith(tool_suffix):
                            continue
                        query = (block.get("input") or {}).get("query")
                        if not isinstance(query, str):
                            continue
                        query = query.strip()
                        if min_len <= len(query) <= max_len:
                            counts[query] += 1
        except OSError:
            continue
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="A ~/.claude/projects/<encoded-path> directory.")
    ap.add_argument("--tool", default="recall", choices=("recall", "search"))
    ap.add_argument("--min-len", type=int, default=_DEFAULT_MIN_LEN)
    ap.add_argument("--max-len", type=int, default=_DEFAULT_MAX_LEN)
    ap.add_argument("--limit", type=int, default=40, help="Max distinct queries to print.")
    args = ap.parse_args()

    project_dir = Path(os.path.expanduser(args.project))
    counts = mine_queries(project_dir, _tool_suffix(args.tool), args.min_len, args.max_len)
    for query, n in counts.most_common(args.limit):
        print(f"{n}\t{query}")


if __name__ == "__main__":
    main()
