#!/bin/bash
# vectr release: preflight checks + tag + push + GitHub release.
# Version bumps are explicit edits made BEFORE running this:
#   pyproject.toml, README badge/version line, CHANGELOG section, vscode-extension/package.json.
# Tag push triggers the PyPI, VS Code extension, and MCP-registry publish workflows.
set -euo pipefail
V="${1:?usage: release.sh X.Y.Z (run after bumping versions + CHANGELOG)}"
cd "$(dirname "$0")/.."

[ "$(git branch --show-current)" = "main" ] || { echo "ERROR: not on main"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "ERROR: dirty tree"; exit 1; }
grep -q "^version = \"$V\"" pyproject.toml || { echo "ERROR: pyproject.toml version != $V"; exit 1; }
grep -q "^## $V " CHANGELOG.md || { echo "ERROR: CHANGELOG.md missing '## $V' section"; exit 1; }
git rev-parse "v$V" >/dev/null 2>&1 && { echo "ERROR: tag v$V already exists"; exit 1; }

git tag "v$V"
git push origin main "v$V"
gh release create "v$V" --title "v$V" \
  --notes "$(awk "/^## $V /{flag=1;next}/^## /{flag=0}flag" CHANGELOG.md)"
echo "v$V tagged and pushed — PyPI / VS Code / MCP-registry workflows triggered."
