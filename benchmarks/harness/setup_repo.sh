#!/usr/bin/env bash
# POC setup: clone django/django into two directories and index the vectr copy.
#
#   /tmp/poc-django-vanilla/  — plain Django clone, no vectr config
#   /tmp/poc-django-vectr/    — Django clone, .mcp.json pointing to vectr, indexed
#
# Run once before running run_poc.py.
# Requires: git, vectr server running at localhost:8765
set -euo pipefail

VANILLA_DIR="/tmp/poc-django-vanilla"
VECTR_DIR="/tmp/poc-django-vectr"
VECTR_URL="http://localhost:8765"
DJANGO_REPO="https://github.com/django/django.git"

echo "=== POC Setup (Django) ==="

# 1. Clone vanilla copy (no vectr)
if [ -d "$VANILLA_DIR/.git" ]; then
  echo "[1/5] Vanilla repo already at $VANILLA_DIR (skipping)"
else
  echo "[1/5] Cloning django/django (vanilla) -> $VANILLA_DIR  (may take ~30s)"
  git clone --depth=1 "$DJANGO_REPO" "$VANILLA_DIR"
fi

# 2. Clone vectr copy
if [ -d "$VECTR_DIR/.git" ]; then
  echo "[2/5] Vectr repo already at $VECTR_DIR (skipping)"
else
  echo "[2/5] Cloning django/django (vectr) -> $VECTR_DIR  (may take ~30s)"
  git clone --depth=1 "$DJANGO_REPO" "$VECTR_DIR"
fi

# 3. Configure vectr copy: .mcp.json + .claude/settings.json
echo "[3/5] Writing vectr config to $VECTR_DIR..."

cat > "$VECTR_DIR/.mcp.json" <<'EOF'
{
  "mcpServers": {
    "vectr": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
EOF

mkdir -p "$VECTR_DIR/.claude"
cat > "$VECTR_DIR/.claude/settings.json" <<'EOF'
{
  "enableAllProjectMcpServers": true
}
EOF

cat > "$VECTR_DIR/CLAUDE.md" <<'EOF'
# Vectr tools — USE THESE INSTEAD OF READ/BASH FOR ALL CODE EXPLORATION

This workspace is indexed by vectr. Always prefer vectr tools over direct file access.

## Exploration tools (replace Read, Bash grep/find)

| Instead of | Use |
|---|---|
| Reading a file to find a class | `vectr_locate("ClassName")` |
| grep / Bash search for a symbol | `vectr_search("what you're looking for")` |
| Manually reading callers/callees | `vectr_trace("function_name")` |
| Reading many files for overview | `vectr_map()` first, then `vectr_search()` |

## Memory tools

- `vectr_remember(content, tags=["tag"], priority="high")` — store a finding immediately after making it
- `vectr_recall(query)` — retrieve notes from a previous session (call this first in implementation phase)
- `vectr_snapshot("label")` — checkpoint all notes (call at end of research phase)

## Rules

1. Start every research session with `vectr_map()` to orient yourself.
2. Use `vectr_search()` and `vectr_locate()` to find code — not Read or Bash.
3. Use `vectr_trace()` to follow call chains — not manual file browsing.
4. Call `vectr_remember()` after every meaningful finding, not just at the end.
5. End research with `vectr_snapshot("phase1-complete")`.
6. Start implementation with `vectr_recall()` to retrieve all stored notes.
EOF

echo "  .mcp.json, .claude/settings.json, CLAUDE.md written"

# 4. Check vectr is running
echo "[4/5] Checking vectr server at $VECTR_URL..."
STATUS=$(curl -sf "$VECTR_URL/v1/health" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null \
  || echo "unreachable")
if [ "$STATUS" != "ok" ]; then
  echo "ERROR: vectr server not reachable at $VECTR_URL"
  echo ""
  echo "  Start it with (from the vectr repo root):"
  echo "    VECTR_WORKSPACE=$VECTR_DIR VECTR_PORT=8765 .venv/bin/vectr start"
  echo ""
  echo "  Or if it's already running with a different workspace, just:"
  echo "    curl -X POST http://localhost:8765/v1/index -H 'Content-Type: application/json' \\"
  echo "         -d '{\"path\": \"$VECTR_DIR\", \"force\": true}'"
  exit 1
fi
echo "  vectr is running (status=$STATUS)"

# 5. Index the vectr copy (Django has ~850 files — allow a few minutes)
echo "[5/5] Indexing $VECTR_DIR with vectr  (Django is large; may take 3-5 min)..."
RESPONSE=$(curl -sf -X POST "$VECTR_URL/v1/index" \
  -H "Content-Type: application/json" \
  -d "{\"path\": \"$VECTR_DIR\", \"force\": true}")
INDEXED=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('indexed_files','?'))" 2>/dev/null || echo "?")
CHUNKS=$(echo "$RESPONSE"  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_chunks','?'))" 2>/dev/null || echo "?")
echo "  Indexed $INDEXED files, $CHUNKS chunks"

# Ensure vanilla copy has no vectr or claude settings
rm -rf "$VANILLA_DIR/.mcp.json" "$VANILLA_DIR/.claude" "$VANILLA_DIR/CLAUDE.md"
echo "  Cleared any vectr/claude config from vanilla copy"

echo ""
echo "Setup complete."
echo "  Vanilla : $VANILLA_DIR"
echo "  Vectr   : $VECTR_DIR"
echo ""
echo "Run the benchmark:"
echo "  cd $(dirname "$0")"
echo "  python3 run_poc.py --save"
echo "  python3 run_poc.py --task custom_field --agent vectr --save"
