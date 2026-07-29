"""Print the EXACT proactive-context block a scenario's planted note produces.

No LLM, no agent: starts a scratch daemon, plants the note, calls /v1/proactive
with the scenario's probe file in the window, and dumps the returned context
verbatim so we can see what the executor actually receives.
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/swapnanilsaha/dev/vectr-wt-util2-1/benchmarks/injection_utility")
import scenarios as scen  # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else "vendor_batch_bug"
PORT = 8899
VECTR = "/Users/swapnanilsaha/Library/Python/3.14/bin/vectr"


def http(method, url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


scenario = scen.SCENARIOS[SLUG]
root = Path(tempfile.mkdtemp(prefix="showblock-", dir="/tmp"))
ws = root / "workspace"
ws.mkdir()
scen.materialize(scenario, ws)

env = dict(__import__("os").environ)
env["VECTR_DB_DIR"] = str(root / "db")
env["VECTR_PORT"] = str(PORT)
env["VECTR_WORKSPACE"] = str(ws)

subprocess.run([VECTR, "start", str(ws), "--port", str(PORT),
                "--memory-only", "--no-ide-config"],
               capture_output=True, text=True, timeout=300, env=env)
for _ in range(60):
    try:
        http("GET", f"http://127.0.0.1:{PORT}/v1/status", timeout=5)
        break
    except Exception:
        time.sleep(1)

note = scenario.note
payload = {"content": note.content, "title": note.title, "kind": note.kind,
           "priority": note.priority, "tags": list(note.tags),
           "agent": "injection-utility-harness"}
if note.trigger_paths:
    payload["triggers"] = [{"path": p} for p in note.trigger_paths]
out = http("POST", f"http://127.0.0.1:{PORT}/v1/remember", payload)

res = http("POST", f"http://127.0.0.1:{PORT}/v1/proactive", {
    "text": scenario.task_prompt,
    "file_paths": [str(ws / scenario.probe_file)],
    "symbols": [],
    "session_id": f"showblock-{SLUG}",
    "channel": "proxy",
})

print(f"=== planted note ({len(note.content)} chars) ===")
print(note.content)
print(f"\n=== injected context block ({len(res.get('context') or '')} chars, "
      f"items={res.get('item_count')}) ===")
print(res.get("context"))

subprocess.run([VECTR, "stop", "--path", str(ws)], capture_output=True, env=env)
