#!/bin/sh
# decay-probe.sh v4 — k-compaction memory-decay probe (brain-memory follow-up
# to BM-4; see decay-protocol.md). Question: what happens to early-session
# facts after >=10 auto-compactions — in-context retention only (arm N) vs the
# same facts also externalized as working memory with hooks (arm M)?
#
# v3: one SESSION driven through 14 sequential `claude -p --resume` phases
# (the ecologically-valid one-chat-per-project shape). The fact obligation is
# stated ONLY in phase 0; the final phase asks for the facts without restating
# them — the pre-registered harder probe. Anti-sampling forcing function:
# every audit entry must list EVERY type declaration with line numbers,
# graded against regex ground truth (v1 fell to shell extraction, v2 to
# slice-sampling + 40 fabricated entries; see decay-protocol.md Run log).
#
# v4 (post-N3): (a) DECAY-FACTS.md is DELETED after phase 0 — N3's endpoint
# was contaminated by the harness's post-compaction file restoration, which
# re-attached the small fixture with FULL verbatim content after compaction 1;
# the grader now scans the session file for restoration contamination (the
# restore may be cache-sourced). (b) CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000
# halves the compaction threshold (the CLI floors this env var at 100k;
# CLAUDE_AUTOCOMPACT_PCT_OVERRIDE proved a no-op — N4 compacted at 200k with
# pct=20 set) and the corpus is extended to 64 files, phase count derived
# from decay-files.txt (N3 hit only k=2 at stock threshold + 48 files).
# (c) Report parser accepts the '## File N: <path>' headers the agent
# actually writes. (d) Early-fact-write flag + rehearsal count metric.
# (e) v4.2: CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=8000 (default 25000) —
# at the 100k window, 25k-token reads refill the window within 3 turns and
# trip the CLI's autocompact THRASH GUARD, which aborts the call with
# is_error ("Autocompact is thrashing... Try reading in smaller chunks, or
# use /clear"; killed N5 phases 3-4). 8k reads refill in ~9 turns: same
# total token throughput (same k), no guard trips.
#
# Model: haiku. One arm at a time; M requires the 8766 camel daemon up first:
#   cd <camel checkout> && vectr start --port 8766
set -eu

ARM="${1:?arm required: N (naive, no vectr) | M (memory: seeded notes + hooks)}"
CAMEL=/home/user/Documents/fde/vectr/tmp/poc-camel

CLAUDE="${CLAUDE:-$(ls -t "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | head -1)}"
[ -x "$CLAUDE" ] || { echo "ABORT: claude CLI not found"; exit 1; }
CAMEL_SHA=a543dc64
VECTR="${VECTR_MAIN:-/opt/homebrew/bin/vectr}"
GATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$GATE_DIR/results/decay-$ARM-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# Shrink the auto-compact window so k >= 10 is reachable. Verified against
# CLI 2.1.211: CLAUDE_AUTOCOMPACT_PCT_OVERRIDE is a no-op on this path (N4
# still compacted at pre_tokens=200556), and CLAUDE_CODE_AUTO_COMPACT_WINDOW
# is honored but clamped to a 100k-token FLOOR (Math.max(1e5, value)), so
# 100000 is the lowest real threshold (~2x more compactions than stock ~190k).
# The rest of the power comes from corpus length (decay-files.txt).
AUTO_COMPACT_WINDOW="${AUTO_COMPACT_WINDOW:-100000}"
READ_MAX_TOKENS="${READ_MAX_TOKENS:-8000}"
echo "auto-compact window override: $AUTO_COMPACT_WINDOW (CLI floor: 100000); read cap: $READ_MAX_TOKENS tokens" | tee "$OUT/preflight.txt"

caffeinate -i -w $$ >/dev/null 2>&1 &

# --- reset workspace (tracked files to pinned SHA; keep vectr editor configs) ---
cd "$CAMEL"
git reset --hard "$CAMEL_SHA"
git clean -fd -e .mcp.json -e .cursor -e .vscode -e .claude

# Equal disk-memory starting state (see run-arm.sh for rationale).
DISK_MEM="$HOME/.claude/projects/-home-user-Documents-fde-vectr-tmp-poc-camel/memory"
rm -rf "$DISK_MEM"

# --- fixture: the 10 operational facts read in Phase 0 by BOTH arms ---
cat > DECAY-FACTS.md <<'FACTS'
# DECAY-FACTS — operational context for this audit engagement

Current operational facts for the project. They are needed VERBATIM
(id and value, exactly) in the final phase of the audit.

- F1: the build-freeze code is QK-4417
- F2: the rollback window is 73 minutes
- F3: the staging tenant is maroon-otter-19
- F4: the payments cutover date is 2027-03-09
- F5: the telemetry salt is v9u-KL22-xr
- F6: the incident bridge channel is #inc-5561-bridge
- F7: the canary abort threshold is 0.83%
- F8: the artifact bucket is opal-crate-407
- F9: the on-call rotation id is RTX-88-BLUE
- F10: the postmortem doc code is PM-7Q-9932
FACTS
shasum -a 256 DECAY-FACTS.md | tee "$OUT/decay-facts.sha256"

EXTRA_FLAGS=""
case "$ARM" in
  N)
    rm -f .mcp.json .claude/settings.json
    if grep -qi vectr AGENTS.md; then echo "ABORT: N arm but AGENTS.md still has vectr guidance"; exit 1; fi
    EXTRA_FLAGS="--strict-mcp-config"
    echo "N arm: no vectr surface (no .mcp.json, no settings.json, strict-mcp-config, pristine AGENTS.md)" | tee -a "$OUT/preflight.txt"
    ;;
  M)
    up=0
    for _try in $(seq 1 36); do
      if curl -sf -m 10 http://127.0.0.1:8766/v1/status >/dev/null 2>&1; then up=1; break; fi
      sleep 5
    done
    [ "$up" = 1 ] \
      || { echo "ABORT: daemon 8766 not up after 3 min of retries (cd $CAMEL && vectr start --port 8766); note: the git reset above can starve REST briefly — retries cover that"; exit 1; }
    sleep 90
    quiet_ok=0; quiet_tries=0
    while [ "$quiet_ok" -lt 3 ]; do
      if curl -sf -m 10 http://127.0.0.1:8766/v1/status >/dev/null 2>&1; then quiet_ok=$((quiet_ok+1)); else quiet_ok=0; fi
      quiet_tries=$((quiet_tries+1))
      if [ "$quiet_tries" -gt 120 ]; then echo "ABORT: daemon 8766 REST still starved after 30 min post-reset"; exit 1; fi
      sleep 15
    done
    mkdir -p .claude
    printf '%s\n' '{' '  "enableAllProjectMcpServers": true,' '  "mcpServers": {' '    "vectr": {' '      "type": "http",' '      "url": "http://localhost:8766/mcp"' '    }' '  }' '}' > .claude/settings.json
    [ -x "$VECTR" ] || { echo "ABORT: vectr binary not executable: $VECTR"; exit 1; }
    "$VECTR" init --hooks >/dev/null 2>&1 || true
    grep -qi 'vectr_search' AGENTS.md || { echo "ABORT: vectr init left no guidance in AGENTS.md"; exit 1; }
    [ -f .mcp.json ] || { echo "ABORT: .mcp.json missing after vectr init"; exit 1; }
    grep -q '"hooks"' .claude/settings.json || { echo "ABORT: M arm but no hooks block in settings.json"; exit 1; }
    grep -q 'vectr hook' .claude/settings.json || { echo "ABORT: M arm but no vectr hook commands in settings.json"; exit 1; }
    notes="?"
    for _ in 1 2 3 4 5; do
      "$VECTR" forget --path "$CAMEL" --port 8766 || true
      notes=$(curl -sf -m 30 http://127.0.0.1:8766/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("notes_count", "?"))') || notes="?"
      [ "$notes" = "0" ] && break || sleep 20
    done
    echo "notes at start: $notes (must be 0)" | tee -a "$OUT/preflight.txt"
    [ "$notes" = "0" ] || { echo "ABORT: notes not zero"; exit 1; }
    seeded=0
    while IFS= read -r body; do
      [ -n "$body" ] || continue
      curl -sf -X POST http://127.0.0.1:8766/v1/remember -H 'content-type: application/json' \
        -d "$body" >/dev/null || { echo "ABORT: seed note store failed"; exit 1; }
      seeded=$((seeded+1))
    done < "$GATE_DIR/decay-seeds.jsonl"
    notes=$(curl -sf -m 30 http://127.0.0.1:8766/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("notes_count", "?"))')
    echo "seeded notes: $seeded (daemon notes_count: $notes)" | tee -a "$OUT/preflight.txt"
    [ "$notes" = "$seeded" ] || { echo "ABORT: seeded note count mismatch"; exit 1; }
    cp "$GATE_DIR/decay-seeds.jsonl" "$OUT/seeds-at-launch.jsonl"
    printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$CAMEL" \
      | "$VECTR" hook session-start > "$OUT/hook-probe-sessionstart.json" 2>/dev/null || true
    grep -q 'QK-4417' "$OUT/hook-probe-sessionstart.json" \
      || { echo "ABORT: session-start hook probe did not inject seeded facts"; exit 1; }
    echo "hook probe: session-start injected seeded facts (native channel live)" | tee -a "$OUT/preflight.txt"
    ;;
esac

# --- snapshot the guidance/config surfaces the agent will see ---
cp AGENTS.md "$OUT/AGENTS.md.at-launch"
{ ls -la CLAUDE.md 2>/dev/null || true; echo "vectr mentions in AGENTS.md: $(grep -ci vectr AGENTS.md || true)"; } > "$OUT/guidance-state.txt"
if [ -f .mcp.json ]; then cp .mcp.json "$OUT/mcp.json.at-launch"; fi
if [ -f .claude/settings.json ]; then cp .claude/settings.json "$OUT/settings.json.at-launch"; fi

# --- phased run: one session, 14 sequential -p calls (phase 0, 12 audit, final) ---
SID=$(uuidgen | tr '[:upper:]' '[:lower:]')
echo "session id: $SID" | tee -a "$OUT/preflight.txt"

NFILES=$(grep -c . "$GATE_DIR/decay-files.txt")
AUDIT_PHASES=$((NFILES / 4))
TOTAL_PHASES=$((AUDIT_PHASES + 2))
echo "corpus: $NFILES files, $AUDIT_PHASES audit phases, $TOTAL_PHASES total" | tee -a "$OUT/preflight.txt"

CONSTRAINTS="HARD TOOL CONSTRAINTS (compliance is graded): file contents may be accessed ONLY through the Read tool — no Bash, no shell commands, no scripts, no cat/head/tail/wc/grep. audit-report.md may be written ONLY with the Write/Edit tools. Do NOT use subagents or the Task tool. Work alone, in order, completely."

run_phase() {
  # $1 = phase label, $2 = prompt, $3 = first|resume
  if [ "$3" = "first" ]; then SESS_FLAGS="--session-id $SID"; else SESS_FLAGS="--resume $SID"; fi
  CLAUDE_CODE_AUTO_COMPACT_WINDOW="$AUTO_COMPACT_WINDOW" \
  CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS="$READ_MAX_TOKENS" "$CLAUDE" -p "$2" \
    --model haiku \
    --dangerously-skip-permissions \
    $EXTRA_FLAGS $SESS_FLAGS \
    --output-format stream-json --verbose \
    >> "$OUT/transcript.jsonl" 2>> "$OUT/stderr.log" \
    || { echo "phase $1: claude exit $?" | tee -a "$OUT/preflight.txt"; }
  echo "phase $1 done" | tee -a "$OUT/phases.txt"
}

start=$(date +%s)

run_phase 0 "$CONSTRAINTS

Phase 0 of a $TOTAL_PHASES-phase codebase audit engagement on this Apache Camel checkout.
Read the file DECAY-FACTS.md at the repository root with the Read tool. It
contains 10 operational facts (F1-F10) for this engagement. You will need to
reproduce them EXACTLY — id and value, verbatim — in the final phase, many
phases from now. Do NOT copy them into any file on disk at any point before
the final phase. Confirm (without transcribing them) that you have read and
are carrying all 10." first

# v4: remove the fixture the moment phase 0 has read it — the facts must have
# no re-readable source (that IS the tier-2 distinction), and N3 showed the
# harness re-attaches small read files with full content after compaction.
rm -f DECAY-FACTS.md
echo "fixture deleted after phase 0 (no re-readable source; defeats post-compaction file restoration)" | tee -a "$OUT/preflight.txt"

i=1
batch=""
count=0
phase=1
while IFS= read -r f; do
  batch="$batch
- $f"
  count=$((count+1))
  if [ "$count" = 4 ]; then
    run_phase "$phase" "$CONSTRAINTS

Phase $phase of $TOTAL_PHASES (audit). For EACH of the following 4 files, in order:
1. Read the ENTIRE file with the Read tool — continue with offset reads until
   you have reached the actual end; do not sample or skip regions.
2. Append one entry to audit-report.md at the repository root containing:
   - the file path and its total line count,
   - a complete ordered list of EVERY type declaration in the file (class,
     interface, enum, annotation type — including nested ones), each with the
     line number where it is declared,
   - a 3-5 line summary of the file's role.
Files:$batch" resume
    batch=""
    count=0
    phase=$((phase+1))
  fi
done < "$GATE_DIR/decay-files.txt"

run_phase final "Phase $TOTAL_PHASES of $TOTAL_PHASES (closing). Using ONLY the Write/Edit tools:
1. Append a section '## Operational Facts' to audit-report.md reproducing all
   10 facts from Phase 0 verbatim, one line per fact ('Fn: <value>'), from
   what you have carried through this session.
2. Append a 10-line overall synthesis of the architecture you audited.
3. State DONE." resume

echo "wall_seconds=$(( $(date +%s) - start ))" | tee "$OUT/wall.txt"

# The session file is the evidentiary surface for compaction internals
# (isCompactSummary messages, post-compaction attachment events).
SESS_FILE="$HOME/.claude/projects/-home-user-Documents-fde-vectr-tmp-poc-camel/$SID.jsonl"
cp "$SESS_FILE" "$OUT/session.jsonl" 2>/dev/null \
  || echo "session file not found: $SESS_FILE" | tee -a "$OUT/preflight.txt"

pmset -g log 2>/dev/null | grep -E "Entering Sleep|Wake from" | tail -40 > "$OUT/sleep-events.txt" || true

# --- MCP posture proof (first init event) ---
python3 - "$ARM" "$OUT/transcript.jsonl" <<'PYEOF' | tee -a "$OUT/preflight.txt"
import json, sys
arm, path = sys.argv[1], sys.argv[2]
servers = None
with open(path) as f:
    for line in f:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("type") == "system" and d.get("subtype") == "init":
            servers = d.get("mcp_servers", []); break
if servers is None:
    print("mcp_posture: NO INIT EVENT — INVALID RUN")
elif arm == "N" and servers:
    print(f"mcp_posture: FAIL — naive arm saw MCP servers {servers} — INVALID RUN")
elif arm == "M" and not any(s.get("name") == "vectr" and s.get("status") == "connected" for s in servers):
    print(f"mcp_posture: FAIL — vectr not connected in {servers} — INVALID RUN")
else:
    print(f"mcp_posture: OK ({'none' if not servers else 'vectr connected'})")
PYEOF

# --- disk-memory adoption audit ---
if [ -d "$DISK_MEM" ]; then
  n=$(find "$DISK_MEM" -type f | wc -l | tr -d ' ')
  echo "disk_memory_notes: $n" | tee -a "$OUT/preflight.txt"
  cp -R "$DISK_MEM" "$OUT/disk-memory-at-end" 2>/dev/null || true
else
  echo "disk_memory_notes: 0 (dir never created)" | tee -a "$OUT/preflight.txt"
fi

# --- decay grading ---
cp audit-report.md "$OUT/audit-report.md" 2>/dev/null || echo "(no audit-report.md produced)" > "$OUT/audit-report.md"
python3 - "$OUT/transcript.jsonl" "$OUT/audit-report.md" "$GATE_DIR/decay-files.txt" "$CAMEL" "$OUT/session.jsonl" <<'PYEOF' | tee "$OUT/decay-grade.txt"
import json, re, sys
tpath, rpath, flist, root, spath = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
TOKENS = {
    "F1": "QK-4417", "F2": "73 minutes", "F3": "maroon-otter-19",
    "F4": "2027-03-09", "F5": "v9u-KL22-xr", "F6": "inc-5561-bridge",
    "F7": "0.83", "F8": "opal-crate-407", "F9": "RTX-88-BLUE", "F10": "PM-7Q-9932",
}
compactions = 0
summaries = []
results = []
models = set()
tools = {}
rehearsals = 0
with open(tpath) as f:
    for line in f:
        try: ev = json.loads(line)
        except Exception: continue
        if ev.get("type") == "system" and ev.get("subtype") == "compact_boundary":
            compactions += 1
        if ev.get("type") == "result":
            results.append(ev)
        if ev.get("type") == "assistant":
            m = (ev.get("message") or {}).get("model")
            if m: models.add(m)
            blob = ""
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    n = c.get("name", "?")
                    tools[n] = tools.get(n, 0) + 1
                elif isinstance(c, dict):
                    blob += (c.get("text") or c.get("thinking") or "") + " "
            if sum(1 for tok in TOKENS.values() if tok in blob) >= 8:
                rehearsals += 1
        if ev.get("type") == "user":
            c = (ev.get("message") or {}).get("content")
            texts = []
            if isinstance(c, str): texts = [c]
            elif isinstance(c, list):
                texts = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
            for t in texts:
                if "continued from a previous conversation" in t:
                    summaries.append(t)
print(f"model(s): {sorted(models)}")
print(f"tool calls: {dict(sorted(tools.items()))}")
print(f"tool compliance: Bash={tools.get('Bash', 0)} (task forbids shell; >2 => SHORTCUT SUSPECT), Read={tools.get('Read', 0)}")
print(f"compactions: {compactions} (target >= 10; below 10 => UNDERPOWERED)")

# Anti-sampling compliance: declaration recall per file vs regex ground truth.
DECL = re.compile(r"^\s*(?:@\w+\s+)*(?:public|protected|private|static|final|abstract|\s)*\b(?:class|interface|enum|@interface|record)\s+([A-Za-z_]\w*)", re.M)
report = open(rpath, encoding="utf-8", errors="replace").read()
sections = re.split(r"^#{2,4}\s+", report, flags=re.M)
sec_by_file = {}
# Agents title sections variably: some put the full path in the header,
# others use "File N: Basename.java" with the path on a **Path:** line
# below. Bind on either, and concatenate duplicate sections for one file.
for s in sections[1:]:
    slines = s.splitlines()
    header = slines[0] if slines else ""
    head = "\n".join(slines[:4])
    for line in open(flist):
        p = line.strip()
        if p and (p in head or " " + p.split("/")[-1] in " " + header):
            sec_by_file[p] = sec_by_file.get(p, "") + s
recalls = []
low = []
for line in open(flist):
    p = line.strip()
    if not p: continue
    try:
        src = open(f"{root}/{p}", encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    truth = set(DECL.findall(src))
    if not truth: continue
    body = sec_by_file.get(p, "")
    hit = sum(1 for name in truth if re.search(r"\b" + re.escape(name) + r"\b", body))
    r = hit / len(truth)
    recalls.append(r)
    if r < 0.8: low.append((p.split("/")[-1], f"{hit}/{len(truth)}"))
if recalls:
    mean_r = sum(recalls) / len(recalls)
    print(f"declaration recall: mean {mean_r:.2f} over {len(recalls)} files; <0.8 on {len(low)}: {low[:8]}")
    print(f"audit compliance: {'OK' if mean_r >= 0.8 else 'SAMPLING/FABRICATION SUSPECT'}")
else:
    print("declaration recall: n/a (no report sections matched)")

hits = {fid: (tok in report.split('## Operational Facts')[-1]) for fid, tok in TOKENS.items()}
if '## Operational Facts' not in report:
    print("WARNING: no '## Operational Facts' section in report — scoring whole report")
print(f"final fact score: {sum(hits.values())}/10")
print("per-fact:", " ".join(f"{fid}={'Y' if ok else 'n'}" for fid, ok in hits.items()))
early = [fid for fid, tok in TOKENS.items() if tok in report.split('## Operational Facts')[0]]
if early:
    print(f"EARLY FACT WRITE (constraint violation): {early} in report before the final section")
print(f"rehearsals: {rehearsals} assistant messages carrying >=8 fact tokens (content-plane self-defense)")
for i, s in enumerate(summaries, 1):
    n = sum(1 for tok in TOKENS.values() if tok in s)
    print(f"summary {i}: {n}/10 fact tokens present ({len(s)} chars)")

# Harness file-restoration contamination: post-compaction attachment events
# (session file) that re-deliver the fixture. Full-content attachments carrying
# fact tokens INVALIDATE the in-context-only premise of the run (N3 lesson).
try:
    contam = []
    with open(spath) as f:
        for i, line in enumerate(f, 1):
            if '"attachment"' not in line: continue
            try: ev = json.loads(line)
            except Exception: continue
            if ev.get("type") != "attachment": continue
            ablob = json.dumps(ev.get("attachment", {}))
            n = sum(1 for tok in TOKENS.values() if tok in ablob)
            if n or "DECAY-FACTS" in ablob:
                contam.append((i, ev.get("attachment", {}).get("type"), f"{n}/10 tokens"))
    if any(int(c[2].split('/')[0]) for c in contam):
        print(f"file-restoration contamination: {contam} — RUN INVALID for the in-context-only premise")
    elif contam:
        print(f"file-restoration references (no fact content): {contam}")
    else:
        print("file-restoration contamination: none")
except OSError:
    print("file-restoration contamination: session file unavailable — UNVERIFIED")
tot_cost = sum(r.get("total_cost_usd") or 0 for r in results)
tot_turns = sum(r.get("num_turns") or 0 for r in results)
tot_dur = sum(r.get("duration_ms") or 0 for r in results)
print(f"phases completed: {len(results)}; totals: duration_ms={tot_dur} turns={tot_turns} cost=${tot_cost:.2f}")
PYEOF

echo "results in $OUT"
