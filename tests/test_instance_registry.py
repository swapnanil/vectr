"""Tests for agent/instance_registry.py."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.instance_registry import (
    InstanceRegistry,
    _is_pid_alive,
    _port_is_free,
    workspace_hash,
)


# ---------------------------------------------------------------------------
# workspace_hash
# ---------------------------------------------------------------------------

def test_workspace_hash_is_12_hex_chars():
    h = workspace_hash("/some/project")
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_workspace_hash_is_deterministic():
    assert workspace_hash("/foo/bar") == workspace_hash("/foo/bar")


def test_workspace_hash_differs_for_different_paths():
    assert workspace_hash("/project/a") != workspace_hash("/project/b")


# ---------------------------------------------------------------------------
# InstanceRegistry — basic CRUD
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path):
    return InstanceRegistry(registry_path=tmp_path / "instances.json")


def test_empty_registry_get_returns_none(registry):
    assert registry.get("aabbccddeeff") is None


def test_empty_registry_list_all_returns_empty(registry):
    assert registry.list_all() == {}


def test_register_and_get(registry):
    registry.register("abc123456789", "/project/a", 8765, 12345)
    entry = registry.get("abc123456789")
    assert entry is not None
    assert entry["workspace"] == "/project/a"
    assert entry["port"] == 8765
    assert entry["pid"] == 12345
    assert "started_at" in entry


def test_register_multiple_workspaces(registry):
    registry.register("aaa000000000", "/project/a", 8765, 100)
    registry.register("bbb000000000", "/project/b", 8766, 101)
    all_entries = registry.list_all()
    assert len(all_entries) == 2
    assert all_entries["aaa000000000"]["port"] == 8765
    assert all_entries["bbb000000000"]["port"] == 8766


def test_unregister_removes_entry(registry):
    registry.register("abc123456789", "/project/a", 8765, 12345)
    registry.unregister("abc123456789")
    assert registry.get("abc123456789") is None


def test_unregister_nonexistent_is_noop(registry):
    registry.unregister("doesnotexist")  # should not raise


def test_register_defaults_extra_roots_and_code_workspace_file(registry):
    registry.register("abc123456789", "/project/a", 8765, 12345)
    entry = registry.get("abc123456789")
    assert entry["extra_roots"] == []
    assert entry["code_workspace_file"] is None


def test_register_stores_extra_roots_and_code_workspace_file(registry):
    registry.register(
        "abc123456789", "/project/a", 8765, 12345,
        extra_roots=["/project/b"], code_workspace_file="/project/proj.code-workspace",
    )
    entry = registry.get("abc123456789")
    assert entry["extra_roots"] == ["/project/b"]
    assert entry["code_workspace_file"] == "/project/proj.code-workspace"


def test_register_overwrites_existing_entry(registry):
    registry.register("abc123456789", "/project/a", 8765, 100)
    registry.register("abc123456789", "/project/a", 8766, 200)
    entry = registry.get("abc123456789")
    assert entry["port"] == 8766
    assert entry["pid"] == 200


# ---------------------------------------------------------------------------
# InstanceRegistry — prune_dead
# ---------------------------------------------------------------------------

def test_prune_dead_removes_dead_pid(registry):
    with patch("agent.instance_registry._is_pid_alive", return_value=False):
        registry.register("aaa000000000", "/project/a", 8765, 99999)
        registry.prune_dead()
    assert registry.get("aaa000000000") is None


def test_prune_dead_keeps_live_pid(registry):
    with patch("agent.instance_registry._is_pid_alive", return_value=True):
        registry.register("aaa000000000", "/project/a", 8765, 99999)
        registry.prune_dead()
    assert registry.get("aaa000000000") is not None


def test_prune_dead_only_removes_dead_entries(registry):
    registry.register("aaa000000000", "/project/a", 8765, 11111)
    registry.register("bbb000000000", "/project/b", 8766, 22222)

    def side_effect(pid):
        return pid == 11111  # only first is alive

    with patch("agent.instance_registry._is_pid_alive", side_effect=side_effect):
        registry.prune_dead()

    assert registry.get("aaa000000000") is not None
    assert registry.get("bbb000000000") is None


# ---------------------------------------------------------------------------
# InstanceRegistry — atomic write
# ---------------------------------------------------------------------------

def test_write_is_atomic(registry):
    """Written file must be valid JSON (no partial writes)."""
    registry.register("abc123456789", "/project/a", 8765, 12345)
    content = registry._path.read_text()
    parsed = json.loads(content)
    assert "abc123456789" in parsed


def test_no_tmp_file_left_after_write(registry):
    registry.register("abc123456789", "/project/a", 8765, 12345)
    assert not registry._path.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# InstanceRegistry — find_free_port
# ---------------------------------------------------------------------------

def test_find_free_port_returns_live_entry_port(registry):
    """If the entry is alive, return its port (caller must detect no-op)."""
    registry.register("aaa000000000", "/project/a", 8765, 11111)
    with patch("agent.instance_registry._is_pid_alive", return_value=True):
        port = registry.find_free_port("aaa000000000", 8765)
    assert port == 8765


def test_find_free_port_reuses_dead_entry_port_if_free(registry):
    """Dead entry → reuse its previous port (avoids rewriting .mcp.json)."""
    registry.register("aaa000000000", "/project/a", 8900, 99999)

    def pid_dead(pid):
        return False

    def port_free(port):
        return True  # old port is free

    with patch("agent.instance_registry._is_pid_alive", side_effect=pid_dead), \
         patch("agent.instance_registry._port_is_free", side_effect=port_free):
        port = registry.find_free_port("aaa000000000", 8765)

    assert port == 8900  # reuses the previously assigned port


def test_find_free_port_scans_forward_when_dead_port_taken(registry):
    """Dead entry with its old port taken → scan forward from preferred."""
    registry.register("aaa000000000", "/project/a", 8765, 99999)

    def pid_dead(pid):
        return False

    # Old port (8765) is taken, 8766 is free
    def port_free(port):
        return port == 8766

    with patch("agent.instance_registry._is_pid_alive", side_effect=pid_dead), \
         patch("agent.instance_registry._port_is_free", side_effect=port_free):
        port = registry.find_free_port("aaa000000000", 8765)

    assert port == 8766


def test_find_free_port_new_workspace_finds_first_free(registry):
    """Unknown workspace → scan from preferred_port."""
    def port_free(port):
        return port == 8767  # only 8767 is free

    with patch("agent.instance_registry._port_is_free", side_effect=port_free):
        port = registry.find_free_port("newworkspace0", 8765)

    assert port == 8767


def test_find_free_port_raises_when_none_available(registry):
    with patch("agent.instance_registry._port_is_free", return_value=False):
        with pytest.raises(RuntimeError, match="No free port"):
            registry.find_free_port("newworkspace0", 8765)


# ---------------------------------------------------------------------------
# _is_pid_alive / _port_is_free (module-level helpers)
# ---------------------------------------------------------------------------

def test_is_pid_alive_current_process():
    assert _is_pid_alive(os.getpid()) is True


def test_is_pid_alive_nonexistent_pid():
    # PID 0 is not a real user process; os.kill(0, 0) sends to the process group.
    # Use a very high PID that is almost certainly dead.
    assert _is_pid_alive(99999999) is False


def test_port_is_free_high_port():
    # A fixed high port (e.g. 59876) races the OS's own ephemeral port
    # allocator — another process on the machine can be bound to it when
    # this test runs (UPG-TEST-PORT-FLAKE), so assert on a port the OS itself
    # just certified as free instead of a hardcoded guess: bind a throwaway
    # socket to port 0 (kernel picks any free port), read back the assigned
    # port, then release it and confirm _port_is_free agrees.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert _port_is_free(free_port) is True


def test_port_is_free_false_when_port_is_held():
    # Companion case: a port this test itself holds open must report False.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        held_port = held.getsockname()[1]
        assert _port_is_free(held_port) is False
