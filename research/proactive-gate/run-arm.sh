#!/bin/sh
# Proactive-gate arm runner. See protocol.md. Autonomous firing authorized by
# the quota owner 2026-07-12; pacing per protocol / user instruction.
#
# Usage:
#   ./run-arm.sh phase1-direct   # transparency gate, no proxy
#   ./run-arm.sh phase1-proxy    # transparency gate, proxy with injection off
#   ./run-arm.sh A               # task 1 vanilla: no vectr at all
#   ./run-arm.sh B               # task 1: vectr MCP tools only
#   ./run-arm.sh C               # task 1: vectr MCP + proactive proxy
#   ./run-arm.sh A2|B2|C2        # same arm shapes on task 2 (timebox EIP)
#   ./run-arm.sh CS              # task 1: C shape + seeded exploration notes
#   ./run-arm.sh H               # BM-4 task 1: NATIVE hooks + trigger engine (main build), seeded
#   ./run-arm.sh V               # BM-4 task 1: voluntary control — same seeds/tools, NO hooks
#
# Matrix of record (user, 2026-07-12): task 1 = 3x A + 3x C; task 2 = 1x A2 +
# 1x C2; all `--model sonnet`. Repeats = invoke this script N times; each run
# gets its own timestamped results dir. That matrix is CLOSED (see
# matrix-verdict.md); H/V are the BM-4 extension (see bm4-protocol.md) and run
# the SHIPPED main-build vectr — no proxy, no godMode worktree.
#
# Legacy proxy arms only — if the scratchpad worktree/venv is gone, recreate:
#   cd ~/Documents/fde/vectr && git worktree add <WT_PATH> feature/experimental-godMode
#   python3 -m venv <WT_PATH>/.venv && <WT_PATH>/.venv/bin/pip install -e <WT_PATH>
set -eu

ARM="${1:?arm required: phase1-direct|phase1-proxy|A|B|C|A2|B2|C2|CS|H|V}"
CAMEL=/home/user/Documents/fde/vectr/tmp/poc-camel

# Maven must bypass the user-level settings.xml (mirrors everything to an
# unreachable private Nexus). Exported so the agent's own mvn calls inherit it.
export JAVA_HOME="$(/usr/libexec/java_home -v 21)"
# license-maven-plugin's format goal fails on non-camel files the harness/vectr
# generate in the tree (unknown comment styles); irrelevant to compile+test.
export MAVEN_ARGS="-s $(cd "$(dirname "$0")" && pwd)/maven-settings.xml -Dlicense.skip=true"

# claude CLI is not on PATH in non-interactive shells on this machine; the
# newest VS Code extension bundle is the only install. Override with CLAUDE=.
CLAUDE="${CLAUDE:-$(ls -t "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | head -1)}"
[ -x "$CLAUDE" ] || { echo "ABORT: claude CLI not found"; exit 1; }
CAMEL_SHA=a543dc64
WT=/private/tmp/claude-502/-home-user-Documents-fde-meeting-to-action/8a96fe83-7a66-47e6-917f-67598cca0bcf/scratchpad/vectr-proxy-dev
VECTR="$WT/.venv/bin/vectr"
# BM-4 native arms run the shipped main-build vectr (global editable install),
# not the godMode worktree build. The 8766 daemon must be started from the
# same build BEFORE invoking this script: cd poc-camel && vectr start --port 8766
case "$ARM" in H|V) VECTR="${VECTR_MAIN:-/opt/homebrew/bin/vectr}";; esac
GATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$GATE_DIR/results/$ARM-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# Prevent idle sleep for this run's lifetime (lid-close still sleeps; the
# transcript's duration_ms clock pauses during sleep and stays canonical).
caffeinate -i -w $$ >/dev/null 2>&1 &

# --- reset workspace (tracked files to pinned SHA; keep vectr editor configs) ---
cd "$CAMEL"
git reset --hard "$CAMEL_SHA"
git clean -fd -e .mcp.json -e .cursor -e .vscode -e .claude

# Equal disk-memory starting state for EVERY arm: Claude Code's auto-memory dir
# for this cwd persists across runs and is harness-injected at session start —
# a note stored by one run would leak into the next. Wipe, then audit post-run
# (disk notes taken is itself an adoption metric).
DISK_MEM="$HOME/.claude/projects/-home-user-Documents-fde-vectr-tmp-poc-camel/memory"
rm -rf "$DISK_MEM"

# --- wait out watcher-debounce churn from the reset above (ALL arms) ---
# The reset feeds the daemon's file watcher a large delete/change set; the
# debounced de-index/re-index job is chroma-heavy, pegs ~4.5 cores, and
# starves daemon REST for tens of minutes (killed C launches 011040 and
# 013327 at preflight). Even vanilla arms must wait: a churning daemon
# steals CPU from the agent run and pollutes the wall-clock comparison.
# 90s lets the debounce fire if it's going to; then require 3 consecutive
# fast /v1/status answers (30-min ceiling) before proceeding.
sleep 90
quiet_ok=0
quiet_tries=0
while [ "$quiet_ok" -lt 3 ]; do
  if curl -sf -m 10 http://127.0.0.1:8766/v1/status >/dev/null 2>&1; then
    quiet_ok=$((quiet_ok+1))
  else
    quiet_ok=0
  fi
  quiet_tries=$((quiet_tries+1))
  if [ "$quiet_tries" -gt 120 ]; then echo "ABORT: daemon 8766 REST still starved after 30 min post-reset"; exit 1; fi
  sleep 15
done

EXTRA_FLAGS=""
case "$ARM" in
  A|A2)
    # Vanilla: no MCP server and no vectr guidance. `git reset --hard` already
    # restored pristine tracked CLAUDE.md/AGENTS.md. Three leak paths closed:
    # .mcp.json (vectr init), .claude/settings.json (carries an mcpServers
    # entry — leaked vectr into run A-20260712-180615, invalidating it), and
    # any user/global-scope server via --strict-mcp-config.
    rm -f .mcp.json .claude/settings.json
    if grep -qi vectr AGENTS.md; then echo "ABORT: vanilla arm but AGENTS.md still has vectr guidance"; exit 1; fi
    EXTRA_FLAGS="--strict-mcp-config"
    echo "vanilla arm: no vectr surface (no .mcp.json, no .claude/settings.json, strict-mcp-config, pristine CLAUDE.md/AGENTS.md)" | tee "$OUT/preflight.txt"
    ;;
  *)
    # Deterministic MCP surface: settings.json auto-approves the project
    # .mcp.json in headless mode (A arms delete this file, so re-write it).
    mkdir -p .claude
    printf '%s\n' '{' '  "enableAllProjectMcpServers": true,' '  "mcpServers": {' '    "vectr": {' '      "type": "http",' '      "url": "http://localhost:8766/mcp"' '    }' '  }' '}' > .claude/settings.json
    [ -x "$VECTR" ] || { echo "ABORT: vectr binary not executable: $VECTR"; exit 1; }
    # Arm H is the native-channel arm: `vectr init --hooks` additionally
    # installs the SessionStart/UserPromptSubmit/PreToolUse/PreCompact hook
    # groups into .claude/settings.json (merged with the mcpServers block
    # above). Arm V and legacy arms get tools-only init — no hooks.
    INIT_FLAGS=""
    if [ "$ARM" = "H" ]; then INIT_FLAGS="--hooks"; fi
    "$VECTR" init $INIT_FLAGS >/dev/null 2>&1 || true   # regenerate editor config/AGENTS.md section
    # Assert the guidance actually regenerated — `vectr init` failing silently
    # would run the arm with pristine camel AGENTS.md and invalidate it
    # (post-hoc lesson from arm B: -p transcripts don't echo injected memory,
    # so the run itself can't prove what guidance the agent saw).
    grep -qi 'vectr_search' AGENTS.md || { echo "ABORT: vectr init left no guidance in AGENTS.md"; exit 1; }
    [ -f .mcp.json ] || { echo "ABORT: .mcp.json missing after vectr init"; exit 1; }
    # Hook-posture asserts (BM-4): H must have the hook groups installed;
    # V must NOT — a leaked hooks block would contaminate the voluntary
    # control exactly the way the leaked settings.json contaminated
    # A-20260712-180615.
    if [ "$ARM" = "H" ]; then
      grep -q '"hooks"' .claude/settings.json || { echo "ABORT: H arm but no hooks block in settings.json"; exit 1; }
      grep -q 'vectr hook' .claude/settings.json || { echo "ABORT: H arm but no vectr hook commands in settings.json"; exit 1; }
    fi
    if [ "$ARM" = "V" ] && grep -q '"hooks"' .claude/settings.json; then
      echo "ABORT: V arm but hooks block present in settings.json"; exit 1
    fi
    # --- equal memory starting state: zero notes on the camel daemon (port 8766) ---
    # Daemon-scoped clear (REST /v1/memory/clear), NOT `forget --all`: --all
    # sweeps every workspace database on the machine, including stores this
    # gate must never touch (8765/8767). Caught 2026-07-13 when the C-arm
    # probe exposed that --all was also globbing a stale cache layout and
    # deleting nothing (fixed in the branch; harness stays scoped regardless).
    # Retry loop: the CLI can hit a client-side httpx ReadTimeout while the
    # daemon is busy with post-reset reindex churn (C-20260713-011040 aborted
    # this way); the scoped clear is idempotent, so loop until the daemon
    # confirms zero notes rather than trusting a single attempt.
    notes="?"
    for _ in 1 2 3 4 5; do
      "$VECTR" forget --path "$CAMEL" --port 8766 || true
      notes=$(curl -sf -m 30 http://127.0.0.1:8766/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("notes_count", "?"))') || notes="?"
      [ "$notes" = "0" ] && break || sleep 20
    done
    echo "notes at start: $notes (must be 0)" | tee "$OUT/preflight.txt"
    [ "$notes" = "0" ] || { echo "ABORT: notes not zero"; exit 1; }
    ;;
esac

# --- snapshot the exact guidance/config surfaces the agent will see ---
# (per-run proof of what was injected; CLAUDE.md is a symlink to AGENTS.md)
cp AGENTS.md "$OUT/AGENTS.md.at-launch"
{ ls -la CLAUDE.md; echo "vectr mentions in AGENTS.md: $(grep -ci vectr AGENTS.md || true)"; } > "$OUT/guidance-state.txt"
if [ -f .mcp.json ]; then cp .mcp.json "$OUT/mcp.json.at-launch"; fi
if [ -f .claude/settings.json ]; then cp .claude/settings.json "$OUT/settings.json.at-launch"; fi

# --- C arms only: end-to-end injection assert (lesson from C-20260712-124925) ---
# The daemon-side master switch (proactive.enabled, default false) used to gate
# /v1/proactive for ALL channels; the branch fix makes channel="proxy" consent
# on its own. Either way, assert the full path live: a seeded probe note must
# come back as packed context, then re-wipe for the equal-note start.
if [ "$ARM" = "C" ] || [ "$ARM" = "C2" ] || [ "$ARM" = "CS" ]; then
  curl -sf -X POST http://127.0.0.1:8766/v1/remember -H 'content-type: application/json' \
    -d '{"content":"GATE-PROBE: stream resequencer reverse comparator wiring note used to verify proactive injection end-to-end.","tags":["gate-probe"],"priority":"high"}' >/dev/null \
    || { echo "ABORT: probe note store failed"; exit 1; }
  ctx=$(curl -sf -X POST http://127.0.0.1:8766/v1/proactive -H 'content-type: application/json' \
    -d '{"text":"How is the stream resequencer reverse comparator wired? gate-probe","file_paths":[],"symbols":[],"session_id":"gate-probe","channel":"proxy"}' \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("context") or ""))')
  echo "injection probe context chars: ${ctx:-0}" | tee -a "$OUT/preflight.txt"
  # Same retry rationale as the preflight wipe above (ReadTimeout vs churn).
  notes="?"
  for _ in 1 2 3 4 5; do
    "$VECTR" forget --path "$CAMEL" --port 8766 || true
    notes=$(curl -sf -m 30 http://127.0.0.1:8766/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("notes_count", "?"))') || notes="?"
    [ "$notes" = "0" ] && break || sleep 20
  done
  [ "${ctx:-0}" -gt 0 ] || { echo "ABORT: proactive injection dead end-to-end — daemon-side proactive gate closed (pre-fix daemons need VECTR_PROACTIVE=true)"; exit 1; }
  [ "$notes" = "0" ] || { echo "ABORT: notes not zero after probe cleanup"; exit 1; }
fi

# --- seeded arms (CS legacy; H/V BM-4): seed exploration-grade notes AFTER the wipe ---
# The seeded starting store models a prior session's exploration findings
# (locations, semantics, the guard site) — NEVER solution steps or a diff.
# CS uses seeds-task1.jsonl; H/V use seeds-task1-triggered.jsonl — IDENTICAL
# note content, the only delta is delivery metadata: the gotcha carries an
# explicit path trigger (fires at PreToolUse on the anchored file), so the
# H-vs-CS comparison is native-channel-vs-proxy with the same knowledge, and
# H-vs-V is delivery-channel-vs-voluntary with the same knowledge.
case "$ARM" in CS|H|V)
  case "$ARM" in CS) SEEDS="$GATE_DIR/seeds-task1.jsonl";; *) SEEDS="$GATE_DIR/seeds-task1-triggered.jsonl";; esac
  seeded=0
  while IFS= read -r body; do
    [ -n "$body" ] || continue
    curl -sf -X POST http://127.0.0.1:8766/v1/remember -H 'content-type: application/json' \
      -d "$body" >/dev/null || { echo "ABORT: seed note store failed"; exit 1; }
    seeded=$((seeded+1))
  done < "$SEEDS"
  notes=$(curl -sf -m 30 http://127.0.0.1:8766/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("notes_count", "?"))')
  echo "seeded notes: $seeded (daemon notes_count: $notes)" | tee -a "$OUT/preflight.txt"
  [ "$notes" = "$seeded" ] || { echo "ABORT: seeded note count mismatch"; exit 1; }
  cp "$SEEDS" "$OUT/seeds-at-launch.jsonl"
  ;;
esac

# --- H arm only: end-to-end native-channel asserts (analog of the C-arm probe) ---
# Drive the REAL hook binary the editor will invoke, against the REAL seeded
# store, for both injection channels the arm depends on. No session_id →
# ledger-less path, zero state consumed; the run itself starts fresh.
# 1. UserPromptSubmit: a resequencer-flavored prompt must inject the seeded
#    findings (semantic recall over the hooks.min_similarity floor).
# 2. PreToolUse on the anchored file: the seeded gotcha's explicit path
#    trigger must fire (this is the cue-anchored mechanism BM-4 exists to
#    validate; a silent miss here = UPG-TRIGGER-PATH-ABS-RELATIVE class).
if [ "$ARM" = "H" ]; then
  printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","prompt":"How does the stream resequencer decide ordering and where is the reverse option wired for batch mode?"}' "$CAMEL" \
    | "$VECTR" hook user-prompt-submit > "$OUT/hook-probe-prompt.json" 2>/dev/null || true
  # Assert the CHANNEL is live with seeded content — not which specific note
  # ranks into the capped injection set (_HOOK_RECALL_LIMIT=3 by default;
  # first H probe 2026-07-16 failed on exactly that over-strict assert while
  # the hook had correctly injected 3 of 4 seeds).
  if ! grep -q 'Working Notes' "$OUT/hook-probe-prompt.json" || ! grep -qi 'resequenc' "$OUT/hook-probe-prompt.json"; then
    echo "ABORT: prompt-submit hook probe did not inject seeded findings"; exit 1
  fi
  printf '{"cwd":"%s","hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"%s/core/camel-core-model/src/main/java/org/apache/camel/model/ResequenceDefinition.java"}}' "$CAMEL" "$CAMEL" \
    | "$VECTR" hook pre-tool-use > "$OUT/hook-probe-pretool.json" 2>/dev/null || true
  grep -q 'ResequenceDefinition' "$OUT/hook-probe-pretool.json" \
    || { echo "ABORT: pre-tool-use hook probe did not fire the path-triggered gotcha"; exit 1; }
  echo "hook probes: prompt-submit + pre-tool-use both injected (native channel live)" | tee -a "$OUT/preflight.txt"
fi

# --- acceptance test in place, fingerprinted ---
case "$ARM" in
  *2) GATE_TEST=TimeboxGateTest.java ;;
  *)  GATE_TEST=StreamResequencerReverseGateTest.java ;;
esac
TEST_DST="core/camel-core/src/test/java/org/apache/camel/processor/$GATE_TEST"
cp "$GATE_DIR/tests/$GATE_TEST" "$TEST_DST"
shasum -a 256 "$TEST_DST" | tee "$OUT/gate-test.sha256"

# --- arm-specific env ---
ENVV=""
case "$ARM" in
  phase1-proxy|C|C2|CS)
    # phase1-proxy expects the proxy started with --no-inject; C arms without:
    #   $VECTR proxy --daemon-port 8766 [--no-inject]
    # The proxy's only self-endpoint is /__vectr_proxy/health (everything else
    # forwards upstream); it also carries the injection/error counters.
    curl -sf http://127.0.0.1:8785/__vectr_proxy/health >/dev/null || { echo "ABORT: proxy not running on 8785"; exit 1; }
    ENVV="ANTHROPIC_BASE_URL=http://127.0.0.1:8785"
    ;;
esac

# --- prompt ---
case "$ARM" in
  phase1-*) PROMPT="Read pom.xml and state the Camel version being built. Nothing else." ;;
  A2|B2|C2) PROMPT="$(cat "$GATE_DIR/task2-prompt.md")" ;;
  *)        PROMPT="$(cat "$GATE_DIR/task-prompt.md")" ;;
esac

# --- run (Sonnet; verify resolved model id in the transcript afterwards) ---
start=$(date +%s)
env $ENVV "$CLAUDE" -p "$PROMPT" \
  --model sonnet \
  --dangerously-skip-permissions \
  $EXTRA_FLAGS \
  --output-format stream-json --verbose \
  > "$OUT/transcript.jsonl" 2> "$OUT/stderr.log" || echo "claude exit: $?" | tee -a "$OUT/preflight.txt"
echo "wall_seconds=$(( $(date +%s) - start ))" | tee "$OUT/wall.txt"

# Sleep events near the run window (lesson from A-20260712-185136: system
# sleep inflates wall.txt; duration_ms is canonical, this snapshot proves
# whether the two may diverge for this run).
pmset -g log 2>/dev/null | grep -E "Entering Sleep|Wake from" | tail -40 > "$OUT/sleep-events.txt" || true

# --- MCP posture proof (from the transcript's own init event) ---
# Vanilla arms must show ZERO MCP servers; vectr arms must show vectr connected.
# Lesson from A-20260712-180615: a leaked settings.json put vectr into a
# "vanilla" run and only the init event revealed it.
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
vanilla = arm in ("A", "A2")
if servers is None:
    print("mcp_posture: NO INIT EVENT — INVALID RUN")
elif vanilla and servers:
    print(f"mcp_posture: FAIL — vanilla arm saw MCP servers {servers} — INVALID RUN")
elif not vanilla and not any(s.get("name") == "vectr" and s.get("status") == "connected" for s in servers):
    print(f"mcp_posture: FAIL — vectr not connected in {servers} — INVALID RUN")
else:
    print(f"mcp_posture: OK ({'none' if not servers else 'vectr connected'})")
PYEOF

# --- disk-memory adoption audit (equal start enforced at reset) ---
if [ -d "$DISK_MEM" ]; then
  n=$(find "$DISK_MEM" -type f | wc -l | tr -d ' ')
  echo "disk_memory_notes: $n" | tee -a "$OUT/preflight.txt"
  cp -R "$DISK_MEM" "$OUT/disk-memory-at-end" 2>/dev/null || true
else
  echo "disk_memory_notes: 0 (dir never created)" | tee -a "$OUT/preflight.txt"
fi

# --- grade (value arms) ---
case "$ARM" in
  A|B|C|H|V)
    shasum -a 256 -c "$OUT/gate-test.sha256" | tee "$OUT/grade.txt" || echo "GATE TEST MODIFIED — FAIL" | tee -a "$OUT/grade.txt"
    mvn -q -pl core/camel-core test -Dtest=StreamResequencerReverseGateTest >> "$OUT/grade.txt" 2>&1 && echo "acceptance: PASS" | tee -a "$OUT/grade.txt" || echo "acceptance: FAIL" | tee -a "$OUT/grade.txt"
    mvn -q -pl core/camel-core test -Dtest='*Resequencer*' >> "$OUT/grade.txt" 2>&1 && echo "regression: PASS" | tee -a "$OUT/grade.txt" || echo "regression: FAIL" | tee -a "$OUT/grade.txt"
    cp core/camel-core/target/surefire-reports/*StreamResequencerReverseGateTest*.xml "$OUT/" 2>/dev/null || true
    git diff --stat "$CAMEL_SHA" > "$OUT/diff-scope.txt"
    ;;
  A2|B2|C2)
    shasum -a 256 -c "$OUT/gate-test.sha256" | tee "$OUT/grade.txt" || echo "GATE TEST MODIFIED — FAIL" | tee -a "$OUT/grade.txt"
    mvn -q -pl core/camel-core test -Dtest=TimeboxGateTest >> "$OUT/grade.txt" 2>&1 && echo "acceptance: PASS" | tee -a "$OUT/grade.txt" || echo "acceptance: FAIL" | tee -a "$OUT/grade.txt"
    mvn -q -pl core/camel-core test -Dtest='*Pipeline*,*Step*,*Multicast*' >> "$OUT/grade.txt" 2>&1 && echo "regression-core: PASS" | tee -a "$OUT/grade.txt" || echo "regression-core: FAIL" | tee -a "$OUT/grade.txt"
    mvn -q -pl core/camel-core-model,core/camel-xml-io test >> "$OUT/grade.txt" 2>&1 && echo "regression-model-xmlio: PASS" | tee -a "$OUT/grade.txt" || echo "regression-model-xmlio: FAIL" | tee -a "$OUT/grade.txt"
    # per-method results for partial credit on a hard task
    cp core/camel-core/target/surefire-reports/*TimeboxGateTest*.xml "$OUT/" 2>/dev/null || true
    git diff --stat "$CAMEL_SHA" > "$OUT/diff-scope.txt"
    ;;
esac

# --- capture daemon/proxy metrics ---
curl -sf http://127.0.0.1:8766/v1/status > "$OUT/daemon-status.json" || true
curl -sf http://127.0.0.1:8785/__vectr_proxy/health > "$OUT/proxy-status.json" 2>/dev/null || true
echo "results in $OUT"
