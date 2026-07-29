"""Mechanism classification for injection-utility inject cells.

For each inject cell: pin the assistant turn the token-confirmed injection
landed on, the turn of the first mutating action, and whether the note's
distinctive strings ever surface in the executor's own output (incl. thinking).

NOTE: daemon audit.log stamps local time with a bogus "Z" suffix, so the audit
clock is offset from the transcript's true-UTC stamps. Offset is recovered per
cell from result.json:started_utc and rounded to the nearest 15 minutes.
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

RUNS = Path(sys.argv[1])
MUTATORS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_transcript(path):
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        for block in d.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                kind, name, body = "text", "text", block.get("text", "")
            elif btype == "thinking":
                kind, name, body = "thinking", "thinking", block.get("thinking", "")
            elif btype == "tool_use":
                kind, name = "tool", block.get("name", "?")
                body = json.dumps(block.get("input", {}))
            else:
                continue
            events.append({"t": ts(d["timestamp"]), "kind": kind, "name": name,
                           "body": body, "req": d.get("request_id")})
    return events


def audit_events(path, offset):
    out = []
    for line in path.read_text().splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2 or "PROACTIVE_" not in line:
            continue
        kind = "INJECT" if "PROACTIVE_INJECT" in line else "RETRIEVE"
        token = re.search(r"token=(\w+)", line)
        out.append({"t": ts(parts[0]) - offset, "kind": kind,
                    "token": token.group(1) if token else None})
    return out


def note_markers(note):
    return sorted(set(re.findall(r"\b[A-Z]{2,6}-\d{3,5}\b", note)))


rows = []
cells = [c for c in sorted(RUNS.glob("*-inject-s*")) if "-no-inject-" not in c.name and (c / "artifacts" / "result.json").exists()]
for cell in cells:
    art = cell / "artifacts"
    res = json.loads((art / "result.json").read_text())
    scen = json.loads((art / "scenario.json").read_text())
    markers = note_markers(scen["planted_note"])
    ev = load_transcript(art / "transcript.jsonl")

    started = ts(res["started_utc"])
    raw = audit_events(art / "audit.log", timedelta(0))
    skew = (raw[0]["t"] - started).total_seconds() if raw else 0
    offset = timedelta(seconds=round(skew / 900.0) * 900)
    aud = audit_events(art / "audit.log", offset)

    delivered = [a for a in aud if a["kind"] == "INJECT" and a["token"]]
    tinj = delivered[-1]["t"] if delivered else None

    landed = next((i for i, e in enumerate(ev) if e["t"] >= tinj), None) if tinj else None
    mutate = next((i for i, e in enumerate(ev)
                   if e["kind"] == "tool" and e["name"] in MUTATORS), None)
    hits = [(i, ev[i]["kind"], m) for i in range(len(ev)) for m in markers
            if m in ev[i]["body"]]

    if tinj is None:
        klass = "NO-CONFIRMED-DELIVERY"
    elif landed is None:
        klass = "LANDED-AFTER-LAST-TURN"
    elif mutate is not None and mutate < landed:
        klass = "TOO-LATE"
    elif hits:
        klass = "READ-IGNORED"
    else:
        klass = "NEVER-SURFACED"

    rows.append({
        "scenario": res["scenario"], "seed": res["seed"],
        "hit": res["score"]["utility_hit"], "class": klass,
        "events": len(ev), "landed_at": landed, "first_mutate": mutate,
        "offset_min": offset.total_seconds() / 60,
        "markers": markers,
        "marker_hits": [(i, k, m) for i, k, m in hits],
        "usd": res["score"]["cost"].get("usd"),
        "turns": res["score"]["cost"].get("turns"),
        "seq": [f"{i}:{e['name']}" for i, e in enumerate(ev)],
    })

for r in rows:
    print(f"{r['scenario']:20s} s{r['seed']} hit={r['hit']!s:5s} "
          f"landed@{str(r['landed_at']):4s} mutate@{str(r['first_mutate']):4s} "
          f"n={r['events']:2d} {r['class']:16s} "
          f"markers={','.join(r['markers']) or '-':18s} "
          f"hits={[(i, k) for i, k, m in r['marker_hits']]}")
    print(f"    seq: {' '.join(r['seq'])}")

(RUNS / "mechanism.json").write_text(json.dumps(rows, indent=2, default=str))
