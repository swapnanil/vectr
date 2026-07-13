"""MCP protocol implementation — exposes vectr tools.

Package layout:
  _schemas.py  — MCP_SERVER_INFO, tool schema lists (_EXPLORATION_TOOLS,
                 _MEMORY_WRITE_TOOLS, _MEMORY_TOOLS, _UTILITY_TOOLS, MCP_TOOLS)
  _session.py  — session state (_memory_enabled_sessions,
                 _session_calls_since_save), nudge helpers, enable/is_memory_enabled
  _dispatch.py — handle_tools_list, handle_tools_call, _format_search_results,
                 _mcp_error
  _stdio.py    — stdio transport: ServiceHandle, dispatch_line, run_stdio_loop
                 (newline-delimited JSON-RPC on stdin/stdout, reuses _dispatch.py)
"""
from __future__ import annotations

# Re-export the full public API so all existing import sites continue to work:
#   from integrations.mcp_server import <name>

from integrations.mcp_server._schemas import (
    MCP_SERVER_INFO,
    _EXPLORATION_TOOLS,
    _MEMORY_WRITE_TOOLS,
    _MEMORY_TOOLS,
    _UTILITY_TOOLS,
    MCP_TOOLS,
)

from integrations.mcp_server._session import (
    _memory_enabled_sessions,
    _session_calls_since_save,
    _REMEMBER_NUDGE_THRESHOLD,
    _REMEMBER_NUDGE_COOLDOWN,
    _increment_calls_since_save,
    _reset_calls_since_save,
    _should_nudge_remember,
    _remember_nudge_text,
    enable_memory_for_session,
    is_memory_enabled,
)

from integrations.mcp_server._dispatch import (
    handle_tools_list,
    handle_tools_call,
    _format_search_results,
    _mcp_error,
)

from integrations.mcp_server._stdio import (
    ServiceHandle,
    dispatch_line,
    run_stdio_loop,
)

__all__ = [
    # Server info
    "MCP_SERVER_INFO",
    # Schema lists
    "_EXPLORATION_TOOLS",
    "_MEMORY_WRITE_TOOLS",
    "_MEMORY_TOOLS",
    "_UTILITY_TOOLS",
    "MCP_TOOLS",
    # Session state (mutable — same objects as in _session.py)
    "_memory_enabled_sessions",
    "_session_calls_since_save",
    # Nudge constants (aliases from agent/config.py)
    "_REMEMBER_NUDGE_THRESHOLD",
    "_REMEMBER_NUDGE_COOLDOWN",
    # Session/nudge helpers
    "_increment_calls_since_save",
    "_reset_calls_since_save",
    "_should_nudge_remember",
    "_remember_nudge_text",
    "enable_memory_for_session",
    "is_memory_enabled",
    # Dispatch
    "handle_tools_list",
    "handle_tools_call",
    # Formatting helpers
    "_format_search_results",
    "_mcp_error",
    # Stdio transport
    "ServiceHandle",
    "dispatch_line",
    "run_stdio_loop",
]
