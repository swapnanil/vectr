"""Tests for integrations/vscode_bridge.py — editor MCP config writers.

UPG-RESTART-PORT-WALK-BREAKS-MCP: `_merge_json_file`'s "never overwrite an
existing key" merge rule meant a workspace's `.claude/settings.json` (and
`.cursor/mcp.json`) never picked up a new port after the first write — the
daemon logged "Updated <path>" on every restart while the file's vectr MCP
URL silently kept pointing at the very first port it was ever configured
with. These tests pin the fixed contract: vectr's own managed subtree
(`mcpServers.vectr`) always syncs to the current call's value; every other
key in the file is left untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

from integrations.vscode_bridge import (
    _merge_json_file,
    configure_claude_code,
    configure_cursor,
)


# ---------------------------------------------------------------------------
# configure_claude_code
# ---------------------------------------------------------------------------

def test_configure_claude_code_creates_file_with_port(tmp_path):
    configure_claude_code(str(tmp_path), port=8765)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert data["mcpServers"]["vectr"]["url"] == "http://localhost:8765/mcp"


def test_configure_claude_code_updates_port_on_restart(tmp_path):
    """The exact defect: a second call with a different port (simulating a
    `vectr stop` + `start` landing on a new port) must overwrite the URL,
    not silently preserve the first one."""
    configure_claude_code(str(tmp_path), port=8765)
    configure_claude_code(str(tmp_path), port=8766)

    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert data["mcpServers"]["vectr"]["url"] == "http://localhost:8766/mcp"


def test_configure_claude_code_preserves_unrelated_keys(tmp_path):
    """Non-vectr keys a user has set by hand (permissions, other MCP
    servers) must never be clobbered by a port sync."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(git *)"]},
        "mcpServers": {"other-server": {"url": "http://localhost:9999/mcp"}},
    }))

    configure_claude_code(str(tmp_path), port=8766)

    data = json.loads(path.read_text())
    assert data["permissions"] == {"allow": ["Bash(git *)"]}
    assert data["mcpServers"]["other-server"]["url"] == "http://localhost:9999/mcp"
    assert data["mcpServers"]["vectr"]["url"] == "http://localhost:8766/mcp"


# ---------------------------------------------------------------------------
# configure_cursor
# ---------------------------------------------------------------------------

def test_configure_cursor_updates_port_on_restart(tmp_path):
    configure_cursor(str(tmp_path), port=8765)
    configure_cursor(str(tmp_path), port=8766)

    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert data["mcpServers"]["vectr"]["url"] == "http://localhost:8766/mcp"


def test_configure_cursor_preserves_other_mcp_servers(tmp_path):
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"url": "http://localhost:1234/mcp"}}}))

    configure_cursor(str(tmp_path), port=8766)

    data = json.loads(path.read_text())
    assert data["mcpServers"]["other"]["url"] == "http://localhost:1234/mcp"
    assert data["mcpServers"]["vectr"]["url"] == "http://localhost:8766/mcp"


# ---------------------------------------------------------------------------
# _merge_json_file — owned_keys contract (direct unit test)
# ---------------------------------------------------------------------------

def test_merge_json_file_without_owned_keys_never_overwrites_existing(tmp_path):
    """Baseline behavior (no owned_keys) is unchanged: first-write-wins."""
    path = tmp_path / "config.json"
    _merge_json_file(path, {"a": {"b": 1}})
    _merge_json_file(path, {"a": {"b": 2}})
    assert json.loads(path.read_text()) == {"a": {"b": 1}}


def test_merge_json_file_owned_key_path_always_synced(tmp_path):
    path = tmp_path / "config.json"
    _merge_json_file(path, {"a": {"b": 1, "c": 1}}, owned_keys=(("a",),))
    _merge_json_file(path, {"a": {"b": 2}}, owned_keys=(("a",),))
    # The whole "a" subtree is owned -> replaced wholesale, including "c"
    # dropping out, since the second call's `updates` no longer mentions it.
    assert json.loads(path.read_text()) == {"a": {"b": 2}}


def test_merge_json_file_owned_key_leaves_sibling_keys_alone(tmp_path):
    path = tmp_path / "config.json"
    _merge_json_file(path, {"owned": {"x": 1}, "other": {"y": 1}}, owned_keys=(("owned",),))
    _merge_json_file(path, {"owned": {"x": 2}}, owned_keys=(("owned",),))
    data = json.loads(path.read_text())
    assert data["owned"] == {"x": 2}
    assert data["other"] == {"y": 1}
