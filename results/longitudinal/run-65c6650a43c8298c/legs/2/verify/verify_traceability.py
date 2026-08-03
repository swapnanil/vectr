import json
import re
import sys
from pathlib import Path

algo = sys.argv[1] if len(sys.argv) > 1 else ""
results_md = Path("RESULTS.md").read_text(encoding="utf-8", errors="replace")

known = set()
results_dir = Path("bench/results")
if results_dir.is_dir():
    for f in results_dir.glob("remote-*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        ns = data.get("ns_op")
        if ns is not None:
            known.add(str(ns))

row_pattern = re.compile(
    r"^\|\s*(\S+)\s*\|\s*[\d.eE+]+\s*\|\s*(\d+)\s*\|\s*remote-box\s*\|", re.MULTILINE
)
rows = row_pattern.findall(results_md)
if not any(name == algo for name, _ in rows):
    print(f"no remote-box row for {algo!r} in RESULTS.md", file=sys.stderr)
    sys.exit(1)
untraceable = [(name, ns) for name, ns in rows if ns not in known]
if untraceable:
    print(f"untraceable RESULTS.md row(s): {untraceable}", file=sys.stderr)
    sys.exit(1)
print(f"{len(rows)} traceable row(s), including {algo!r}")
sys.exit(0)
