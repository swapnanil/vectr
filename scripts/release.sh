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

v$V tagged and pushed. Automatic on the tag: PyPI (pypi-publish.yml) only.
vscode-publish.yml is disabled_manually, so the Marketplace does not move.

POST-RELEASE, none of which happens on its own:

  1. WAIT for PyPI Publish to finish, then confirm the VERSION-SPECIFIC endpoint
     returns 200. The registry validates against that exact URL and reads a
     cache that lags the /pypi/vectr/json summary by up to a minute, so the
     summary already reporting the new version is NOT sufficient:
       gh run watch \$(gh run list --workflow=pypi-publish.yml --limit 1 \\
         --json databaseId --jq '.[0].databaseId') --exit-status
       curl -s -o /dev/null -w "%{http_code}\\n" \\
         https://pypi.org/pypi/vectr/$V/json

  2. ONLY once that reads 200:
       gh workflow run mcp-registry-publish.yml --ref main
     The MCP registry is workflow_dispatch only; a tag does not trigger it.
     Dispatching it before PyPI is live fails with a 400 naming a 404 on the
     package version. That failure is harmless and the fix is to re-dispatch,
     but it costs a round trip every release, so check first.

  3. Verify, do not assume:
       gh run list --limit 5
       curl -s https://pypi.org/pypi/vectr/json | python3 -c \\
         "import json,sys; print(json.load(sys.stdin)['info']['version'])"
       curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=vectr"

     NOTE: vscode-publish.yml is currently disabled_manually and has never
     run, so the VS Code Marketplace listing does not move on a tag. Enabling
     it needs a VSCE_PAT secret. Until then the extension manifest bump is
     bookkeeping only.

  4. Update the product page at https://swapnanilsaha.com/tools/vectr/ so its
     public claims still match what shipped. At minimum the JSON-LD
     "softwareVersion", and any counted claim the release moved (MCP tool count,
     supported languages, named tools/CLI subcommands in the copy). These are
     assertions a reader can check against the repo, so a stale one is a wrong
     one, not merely an old one. The site is a separate repo; see its own
     vectr release-playbook doc for the exact files and grep commands.
EOF
