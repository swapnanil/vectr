"""Auto-configure IDE MCP settings when Vectr starts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.config import DEFAULT_PORT

logger = logging.getLogger(__name__)


def _merge_json_file(
    path: Path, updates: dict, *, owned_keys: tuple[tuple[str, ...], ...] = ()
) -> None:
    """Merge `updates` into the JSON file at `path`.

    Preserves every existing key the caller didn't ask to change — so a
    user's other MCP servers, permissions, etc. in the same file survive —
    EXCEPT for the dotted key-paths listed in `owned_keys`: these are
    vectr's own managed subtrees (e.g. its MCP server entry), and are always
    replaced with the current value on every call rather than left alone the
    first time they're seen. Without `owned_keys`, a port change would never
    reach the file after its first write: the "never overwrite an existing
    key" rule below would keep re-preserving the stale value forever, while
    still logging "Updated" on every call (UPG-RESTART-PORT-WALK-BREAKS-MCP).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    def _deep_merge(base: dict, additions: dict, key_path: tuple[str, ...]) -> dict:
        result = dict(base)
        for key, val in additions.items():
            child_path = key_path + (key,)
            if child_path in owned_keys:
                # vectr-managed subtree — always synced to the current value.
                result[key] = val
            elif key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = _deep_merge(result[key], val, child_path)
            elif key not in result:
                result[key] = val
        return result

    merged = _deep_merge(existing, updates, ())
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    logger.info("Updated %s", path)


# Both configs below store vectr's own entry at the same dotted path,
# "mcpServers.vectr" — the whole subtree is vectr-owned (url, headers, name),
# so it's always synced rather than merge-preserved; everything else in the
# file (other MCP servers, unrelated settings) is left untouched.
_VECTR_MCP_ENTRY_KEY: tuple[tuple[str, ...], ...] = (("mcpServers", "vectr"),)


def configure_cursor(workspace_root: str, port: int = DEFAULT_PORT) -> None:
    """Write/merge .cursor/mcp.json for Cursor IDE."""
    path = Path(workspace_root) / ".cursor" / "mcp.json"
    _merge_json_file(path, {
        "mcpServers": {
            "vectr": {
                "url": f"http://localhost:{port}/mcp",
                "name": "Vectr — Semantic Codebase Search",
            }
        }
    }, owned_keys=_VECTR_MCP_ENTRY_KEY)


def configure_claude_code(workspace_root: str, port: int = DEFAULT_PORT) -> None:
    """Write/merge .claude/settings.json for Claude Code."""
    path = Path(workspace_root) / ".claude" / "settings.json"
    _merge_json_file(path, {
        "mcpServers": {
            "vectr": {
                "type": "http",
                "url": f"http://localhost:{port}/mcp",
            }
        }
    }, owned_keys=_VECTR_MCP_ENTRY_KEY)


def configure_all(workspace_root: str, port: int = DEFAULT_PORT) -> None:
    """Configure all supported AI tools."""
    try:
        configure_cursor(workspace_root, port)
    except Exception as e:
        logger.warning("Could not configure Cursor: %s", e)
    try:
        configure_claude_code(workspace_root, port)
    except Exception as e:
        logger.warning("Could not configure Claude Code: %s", e)
