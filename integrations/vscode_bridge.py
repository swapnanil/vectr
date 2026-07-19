"""Auto-configure IDE MCP settings when Vectr starts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.config import DEFAULT_PORT

logger = logging.getLogger(__name__)


def _merge_json_file(path: Path, updates: dict) -> None:
    """Merge `updates` into the JSON file at `path`. Never overwrites existing keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    # Deep merge: only add missing keys at each level
    def _deep_merge(base: dict, additions: dict) -> dict:
        result = dict(base)
        for key, val in additions.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = _deep_merge(result[key], val)
            elif key not in result:
                result[key] = val
        return result

    merged = _deep_merge(existing, updates)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    logger.info("Updated %s", path)


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
    })


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
    })


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
