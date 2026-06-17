#!/usr/bin/env bash
# POC setup: sparse-clone apache/camel (core modules only) into two directories.
#
#   /tmp/poc-camel-vanilla/  — plain Camel clone, no vectr config
#   /tmp/poc-camel-vectr/    — Camel clone, .mcp.json pointing to vectr, indexed
#
# We sparse-checkout only the `core/` subtree (~1200 Java files) — enough to
# cover the internals the tasks explore, without indexing 500+ optional components.
#
# Run once before running the Camel POC.
# Requires: git, vectr server running at localhost:8765
set -euo pipefail

VANILLA_DIR="/tmp/poc-camel-vanilla"
VECTR_DIR="/tmp/poc-camel-vectr"
VECTR_URL="http://localhost:8765"
CAMEL_REPO="https://github.com/apache/camel.git"
# sparse paths: core engine + API — where the internals the tasks explore live
SPARSE_PATHS="core/ components/camel-core-xml/"

echo "=== POC Setup (Apache Camel — core modules only) ==="
echo "Sparse paths: $SPARSE_PATHS"
echo ""

_clone_sparse() {
  local dir="$1"
  if [ -d "$dir/.git" ]; then
    echo "  Already exists at $dir (skipping clone)"
    return
  fi
  echo "  Cloning (sparse, depth=1) → $dir  (may take ~60s)..."
  git clone --depth=1 --filter=blob:none --sparse "$CAMEL_REPO" "$dir"
  cd "$dir"
  git sparse-checkout set $SPARSE_PATHS
  cd - >/dev/null
  echo "  Done. Files: $(find "$dir" -name '*.java' | wc -l | tr -d ' ') Java files"
}

# 1. Clone vanilla
echo "[1/5] Vanilla clone → $VANILLA_DIR"
_clone_sparse "$VANILLA_DIR"

# 2. Clone vectr
echo "[2/5] Vectr clone → $VECTR_DIR"
_clone_sparse "$VECTR_DIR"

# 3. Configure vectr copy
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
# Vectr tools — available alongside Read and Bash

This workspace is indexed by vectr. Use vectr tools when they'd be faster than
reading files directly.

## Exploration tools — use when you don't already know where to look

| Situation | Tool |
|---|---|
| Don't know which file contains a class/interface | `vectr_locate("ClassName")` |
| Looking for code by concept or behaviour | `vectr_search("what you're looking for")` |
| Want to see what calls or is called by a method | `vectr_trace("methodName")` |
| First contact with an unfamiliar codebase | `vectr_map()` for structural overview |

If you already know the file or symbol, Read is fine — no need to use vectr.

## Memory tools — always use for cross-session continuity

The next session starts cold and won't have your current context:

- `vectr_remember(content, tags=["tag"], priority="high"|"normal")` — store each key
  finding immediately: file paths, class names, method signatures, interface contracts, gotchas
- `vectr_snapshot("label")` — seal all notes at the end of a research session
- `vectr_recall()` — retrieve all stored notes at the start of an implementation session
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
  echo "    .venv/bin/vectr start --path $VECTR_DIR"
  exit 1
fi
echo "  vectr is running (status=$STATUS)"

# 5. Index the vectr copy
echo "[5/5] Indexing $VECTR_DIR with vectr..."
RESPONSE=$(curl -sf -X POST "$VECTR_URL/v1/index" \
  -H "Content-Type: application/json" \
  -d "{\"path\": \"$VECTR_DIR\", \"force\": true}")
INDEXED=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('indexed_files','?'))" 2>/dev/null || echo "?")
CHUNKS=$(echo "$RESPONSE"  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_chunks','?'))" 2>/dev/null || echo "?")
echo "  Indexed $INDEXED files, $CHUNKS chunks"

# Ensure vanilla copy has no vectr config
rm -f "$VANILLA_DIR/.mcp.json" "$VANILLA_DIR/CLAUDE.md"
rm -rf "$VANILLA_DIR/.claude"
echo "  Cleared any vectr/claude config from vanilla copy"

echo ""
echo "Setup complete."
echo "  Vanilla : $VANILLA_DIR"
echo "  Vectr   : $VECTR_DIR"
echo ""
echo "Run the benchmark:"
echo "  cd $(dirname "$0")"
SCRIPT_DIR="$(dirname "$0")"
echo "  POC_VANILLA_DIR=$VANILLA_DIR POC_VECTR_DIR=$VECTR_DIR \\"
echo "    POC_OUTPUT_DIR=/path/to/vectr/benchmarks/camel \\"
echo "    python3.14 run_poc.py --save"
