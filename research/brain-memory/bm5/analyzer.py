#!/usr/bin/env python3
"""BM-5 re-exploration analyzer, v1.

Implements definition.md over Claude Code transcript JSONL files.

Usage:
    python3 analyzer.py <transcript.jsonl> [...] [--json out.json]

v1 over v0 (see definition.md honesty ledger):
- Compound Bash commands are no longer opaque: commands are tokenized
  quote-aware (shlex, punctuation_chars) and split into segments on
  && / || / ; / |. Each segment is classified independently, so
  `cd x && cat y/z.py | head -50` yields a read of y/z.py.
- Bash-side mutations reset the read ledger: `> file`, `>> file`,
  `sed -i file`, `tee file`, `mv/cp src dst`, `rm file` (mv/rm also pop
  the source). Verification reads after shell edits no longer count.
- R3 keys are per read-only SEGMENT, not per whole command: a repeated
  `grep foo bar.py` is caught whether it ran alone or inside a compound.
  (Semantic change from v0: a repeated compound now counts one R3 per
  repeated read-only segment within it.)
- Waste attribution is conservative: a call's result size is added to
  wasted_result_chars only when EVERY classified read/search segment of
  that call is a repeat — partial-fresh compounds contribute zero waste.

Remaining heuristics (v1 floor):
- Commands containing $( ) or backticks stay opaque (single whole-command
  R3 key, no read extraction).
- Relative paths are ledger keys as written; `cd`-dependent aliasing of
  the same file under different relative spellings is not resolved.
- awk file extraction is guarded (last '/'-token without { ( $), may
  false-negative.
- Token proxy: len(chars)/4 of tool_result text.
"""

import argparse
import json
import re
import shlex
import sys
from collections import defaultdict

READ_CMDS = {"cat", "head", "tail", "less", "more", "awk"}
SEARCH_CMDS = {"grep", "rg", "fgrep", "egrep", "find", "ls", "fd"}
# git diff is deliberately absent: its output reflects current working-tree
# state, so repeating it during an edit loop is verification, not
# re-exploration. log/show address immutable objects — repeats are waste.
GIT_READ_SUBCMDS = {"log", "show"}
MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ARTIFACT_RE = re.compile(r"(surefire-reports|target/|build/|\.log$|\.class$)")
WHOLE = (0, float("inf"))
SEG_SEPARATORS = {"&&", "||", ";", ";;", "|", "|&", "&"}
SED_RANGE_TOK_RE = re.compile(r"^(\d+),(\d+)p$")


def blocks(msg):
    content = (msg or {}).get("content")
    if isinstance(content, list):
        return content
    return []


def result_size(block):
    content = block.get("content")
    if isinstance(content, str):
        return len(content)
    n = 0
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                n += len(c.get("text") or "")
    return n


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def ranges_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def split_segments(command):
    """Quote-aware split of a shell command into (separator, tokens) pairs.

    separator is None for the first segment, else the operator that
    preceded the segment ("&&", ";", "|", ...). Returns None when the
    command must stay opaque (substitution, unbalanced quotes).
    """
    if "$(" in command or "`" in command:
        return None
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    segments, current, sep = [], [], None
    for tok in tokens:
        if tok in SEG_SEPARATORS:
            if current:
                segments.append((sep, current))
                current = []
            sep = tok
        else:
            current.append(tok)
    if current:
        segments.append((sep, current))
    return segments


def path_tokens(toks):
    return [t for t in toks if "/" in t and not t.startswith("-")]


def seg_read_target(toks):
    """Return (path, range) for a read segment, else (None, None)."""
    if not toks:
        return None, None
    if toks[0] == "sed" and "-n" in toks and "-i" not in toks:
        for i, t in enumerate(toks):
            m = SED_RANGE_TOK_RE.match(t)
            if m and i + 1 < len(toks):
                cand = path_tokens(toks[i + 1:])
                if cand:
                    return cand[0], (int(m.group(1)), int(m.group(2)) + 1)
        return None, None
    if toks[0] not in READ_CMDS:
        return None, None
    for tok in reversed(toks[1:]):
        if tok.startswith("-") or "/" not in tok:
            continue
        if toks[0] == "awk" and any(c in tok for c in "{($"):
            continue
        return tok, WHOLE
    return None, None


def seg_mutations(toks):
    """Paths a segment mutates (read-ledger resets)."""
    muts = []
    for i, t in enumerate(toks):
        if t in (">", ">>") and i + 1 < len(toks) and "/" in toks[i + 1]:
            muts.append(toks[i + 1])
    if not toks:
        return muts
    if toks[0] == "sed" and "-i" in toks:
        cand = path_tokens(toks[1:])
        if cand:
            muts.append(cand[-1])  # sed -i <script> <file> — file is last
    elif toks[0] == "tee":
        muts.extend(path_tokens(toks[1:]))
    elif toks[0] in ("mv", "cp", "rm"):
        muts.extend(path_tokens(toks[1:]))
    return muts


def seg_is_search(sep, toks):
    if not toks:
        return False
    if sep in ("|", "|&"):
        return False  # pipeline filter reads stdin, not the filesystem
    if toks[0] in SEARCH_CMDS:
        # bare listing commands ("ls" after a cd) alias across cwds —
        # require at least one non-flag argument to key on
        return any(not t.startswith("-") for t in toks[1:])
    if toks[0] == "git" and len(toks) > 1 and toks[1] in GIT_READ_SUBCMDS:
        return True
    if toks[0] == "sed" and "-n" in toks and "-i" not in toks:
        return True  # unparsed-range sed -n falls through to search
    return False


def classify_bash(command):
    """Return a list of classification dicts for one Bash command."""
    segments = split_segments(command)
    if segments is None:
        # opaque: whole-command exact-repeat detection only (v0 behavior)
        first = command.strip().split()[:1]
        if first and (
            first[0] in READ_CMDS
            or first[0] in SEARCH_CMDS
            or command.strip().startswith(("git log", "git show", "git diff"))
        ):
            return [{"kind": "search", "key": "bash:" + norm(command)}]
        return []
    out = []
    compound = len(segments) > 1
    for sep, toks in segments:
        for mut_path in seg_mutations(toks):
            out.append({"kind": "mutate", "file": mut_path, "bash": True})
        fp, rng = seg_read_target(toks)
        if fp:
            out.append(
                {"kind": "read", "file": fp, "range": rng, "bash": True,
                 "compound": compound}
            )
        elif seg_is_search(sep, toks):
            out.append(
                {"kind": "search", "key": "bashseg:" + norm(" ".join(toks))}
            )
    return out


def analyze(path):
    reads = defaultdict(list)      # file -> list of (start, end)
    searches = {}                  # key -> count
    pending = {}                   # tool_use_id -> list of classifications
    compacted = False
    pre_compaction_files = set()

    stats = {
        "transcript": path,
        "tool_calls": 0,
        "exploration_calls": 0,
        "r1_reread": 0,
        "r2_cross_compaction": 0,
        "r3_repeat_search": 0,
        "narrowing_rereads": 0,
        "artifact_rereads": 0,
        "wasted_result_chars": 0,
        "compactions": 0,
        "bash_compound_reads": 0,
        "bash_mutations": 0,
    }

    def classify_use(name, inp):
        """Return list of classification dicts (possibly empty)."""
        if name == "Read":
            fp = inp.get("file_path") or ""
            off = inp.get("offset")
            lim = inp.get("limit")
            rng = WHOLE if off is None else (off, off + (lim or float("inf")))
            return [{"kind": "read", "file": fp, "range": rng}]
        if name in MUTATING_TOOLS:
            return [{"kind": "mutate", "file": inp.get("file_path") or ""}]
        if name == "Grep":
            key = "grep:" + norm(
                f"{inp.get('pattern')}|{inp.get('path')}|{inp.get('glob')}"
            )
            return [{"kind": "search", "key": key}]
        if name == "Glob":
            return [{"kind": "search", "key": "glob:" + norm(inp.get("pattern"))}]
        if name.endswith("vectr_search"):
            return [{"kind": "search", "key": "vsearch:" + norm(inp.get("query"))}]
        if name.endswith("vectr_locate"):
            return [{"kind": "search", "key": "vlocate:" + norm(inp.get("name"))}]
        if name == "Bash":
            return classify_bash(inp.get("command") or "")
        return []

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            if (
                ev.get("isCompactSummary")
                or ev.get("type") == "summary"
                or (
                    ev.get("type") == "system"
                    and ev.get("subtype") == "compact_boundary"
                )
            ):
                compacted = True
                stats["compactions"] += 1
                pre_compaction_files = set(reads.keys())
                continue

            msg = ev.get("message") or {}
            for b in blocks(msg):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    stats["tool_calls"] += 1
                    cs = classify_use(b.get("name") or "", b.get("input") or {})
                    if cs:
                        pending[b.get("id")] = cs
                elif b.get("type") == "tool_result":
                    cs = pending.pop(b.get("tool_use_id"), None)
                    if not cs:
                        continue
                    size = result_size(b)
                    retrievals = 0
                    repeats = 0
                    # process in list order — classify_bash preserves
                    # segment order, so mutate/read sequencing is faithful
                    for c in cs:
                        if c["kind"] == "mutate":
                            reads.pop(c["file"], None)  # reset read ledger
                            # a search re-run against an edited path is
                            # verification — reset matching search keys too
                            mut_key = norm(c["file"])
                            if mut_key:
                                for k in [k for k in searches if mut_key in k]:
                                    del searches[k]
                            if c.get("bash"):
                                stats["bash_mutations"] += 1
                        elif c["kind"] == "read":
                            retrievals += 1
                            if c.get("bash") and c.get("compound"):
                                stats["bash_compound_reads"] += 1
                            prior = reads[c["file"]]
                            overlap = any(
                                ranges_overlap(c["range"], p) for p in prior
                            )
                            if overlap:
                                repeats += 1
                                stats["r1_reread"] += 1
                                if compacted and c["file"] in pre_compaction_files:
                                    stats["r2_cross_compaction"] += 1
                                if ARTIFACT_RE.search(c["file"]):
                                    stats["artifact_rereads"] += 1
                                if c["range"] != WHOLE and WHOLE in prior:
                                    stats["narrowing_rereads"] += 1
                            prior.append(c["range"])
                        elif c["kind"] == "search":
                            retrievals += 1
                            if c["key"] in searches:
                                repeats += 1
                                stats["r3_repeat_search"] += 1
                            searches[c["key"]] = searches.get(c["key"], 0) + 1
                    if retrievals:
                        stats["exploration_calls"] += 1
                        if repeats == retrievals:
                            stats["wasted_result_chars"] += size

    expl = stats["exploration_calls"] or 1
    stats["wasted_result_tokens_est"] = stats["wasted_result_chars"] // 4
    stats["reexploration_share"] = round(
        (stats["r1_reread"] + stats["r3_repeat_search"]) / expl, 3
    )
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcripts", nargs="+")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    results = [analyze(p) for p in args.transcripts]

    cols = [
        ("transcript", lambda s: s["transcript"].split("/")[-2] if "/" in s["transcript"] else s["transcript"]),
        ("tools", lambda s: s["tool_calls"]),
        ("expl", lambda s: s["exploration_calls"]),
        ("R1", lambda s: s["r1_reread"]),
        ("R2", lambda s: s["r2_cross_compaction"]),
        ("R3", lambda s: s["r3_repeat_search"]),
        ("narrow", lambda s: s["narrowing_rereads"]),
        ("bashrd", lambda s: s["bash_compound_reads"]),
        ("waste-tok", lambda s: s["wasted_result_tokens_est"]),
        ("share", lambda s: s["reexploration_share"]),
        ("compactions", lambda s: s["compactions"]),
    ]
    header = " | ".join(name for name, _ in cols)
    print(header)
    print("-" * len(header))
    for s in results:
        print(" | ".join(str(fn(s)) for _, fn in cols))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nwrote {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
