#!/bin/bash
# vectr release: preflight checks + tag + push + GitHub release.
#
# Version bumps are explicit edits made BEFORE running this. All FIVE must match:
#   pyproject.toml          version = "X.Y.Z"
#   README.md               badge + "Version X.Y.Z · Last updated YYYY-MM-DD" line
#   server.json             three "version" fields
#   vscode-extension/package.json
#   CHANGELOG.md            a "## X.Y.Z - YYYY-MM-DD" section (awk-extracted as the release notes)
# The preflight below enforces all five, because a silently skipped bump is how
# the VS Code extension manifest drifted several releases behind.
#
# Tag push triggers pypi-publish.yml and vscode-publish.yml.
# It does NOT trigger the MCP registry: that workflow is workflow_dispatch only
# and must be run by hand after the tag lands (see the post-release list).
set -euo pipefail
V="${1:?usage: release.sh X.Y.Z (run after bumping versions + CHANGELOG)}"
cd "$(dirname "$0")/.."

[ "$(git branch --show-current)" = "main" ] || { echo "ERROR: not on main"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "ERROR: dirty tree"; exit 1; }
grep -q "^version = \"$V\"" pyproject.toml || { echo "ERROR: pyproject.toml version != $V"; exit 1; }
grep -q "^## $V " CHANGELOG.md || { echo "ERROR: CHANGELOG.md missing '## $V' section"; exit 1; }
grep -q "version-$V-blue" README.md || { echo "ERROR: README.md version badge != $V"; exit 1; }
grep -q "^Version $V " README.md || { echo "ERROR: README.md version line != $V"; exit 1; }
SJ=$(grep -c "\"version\": \"$V\"" server.json || true)
[ "$SJ" = "3" ] || { echo "ERROR: server.json has $SJ/3 version fields at $V"; exit 1; }
grep -q "\"version\": \"$V\"" vscode-extension/package.json || {
  echo "ERROR: vscode-extension/package.json version != $V"; exit 1; }
git rev-parse "v$V" >/dev/null 2>&1 && { echo "ERROR: tag v$V already exists"; exit 1; }

git tag "v$V"
git push origin main "v$V"
gh release create "v$V" --title "v$V" \
  --notes "$(awk "/^## $V /{flag=1;next}/^## /{flag=0}flag" CHANGELOG.md)"

cat <<EOF

v$V tagged and pushed. Automatic: PyPI (pypi-publish.yml), VS Code Marketplace
(vscode-publish.yml, only if that workflow is enabled and VSCE_PAT is set).

POST-RELEASE, none of which happens on its own:

  1. gh workflow run mcp-registry-publish.yml --ref main
     The MCP registry is workflow_dispatch only. A tag does not trigger it.

  2. Verify, do not assume:
       gh run list --limit 5
       curl -s https://pypi.org/pypi/vectr/json | python3 -c \\
         "import json,sys; print(json.load(sys.stdin)['info']['version'])"

  3. Update the product page at https://swapnanilsaha.com/tools/vectr/ so its
     public claims still match what shipped. At minimum the JSON-LD
     "softwareVersion", and any counted claim the release moved (MCP tool count,
     supported languages, named tools/CLI subcommands in the copy). These are
     assertions a reader can check against the repo, so a stale one is a wrong
     one, not merely an old one. The site is a separate repo; see its own
     vectr release-playbook doc for the exact files and grep commands.
EOF
