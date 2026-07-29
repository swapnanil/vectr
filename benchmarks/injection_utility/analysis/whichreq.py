"""Was the injected request a main-agent-loop request or an auxiliary call?

Evidence used: the RECALL lines the daemon logs while assembling the window for
the injected request. A main-loop request carries tool-input file paths, so its
window lookups are `method=path` against real workspace files. An auxiliary
call (title-gen, quota probe) has no tool inputs and produces no path lookups.
"""
import json
import re
import sys
from pathlib import Path

RUNS = Path(sys.argv[1])
cells = [c for c in sorted(RUNS.glob("*-inject-s*")) if "-no-inject-" not in c.name]

for cell in cells:
    art = cell / "artifacts"
    res = json.loads((art / "result.json").read_text())
    lines = (art / "audit.log").read_text().splitlines()

    tok_idx = next((i for i, ln in enumerate(lines)
                    if "PROACTIVE_RETRIEVE" in ln and "token=" in ln), None)

    req_ids = []
    for ln in (art / "transcript.jsonl").read_text().splitlines():
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "assistant" and d.get("request_id") not in req_ids:
            req_ids.append(d.get("request_id"))

    pm = res["proxy_metrics"]
    print(f"\n=== {res['scenario']} s{res['seed']} ===")
    print(f"  proxy requests={pm['requests']} injected={pm['injected']} "
          f"skipped={pm['inject_skipped']} not_appendable="
          f"{pm.get('inject_skipped_not_appendable')} | "
          f"main-loop request_ids in transcript={len(req_ids)}")
    if tok_idx is None:
        print("  no token-carrying RETRIEVE")
        continue
    for ln in [x for x in lines[max(0, tok_idx - 6): tok_idx + 1] if " " in x and x[:4].isdigit()]:
        stamp = ln.split(" ", 1)[0]
        rest = re.sub(r"workspace=\S+ ", "", ln.split(" ", 1)[1])
        print(f"    {stamp} {rest[:150]}")
