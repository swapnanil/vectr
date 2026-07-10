"""
Live tests — validate eviction hint language using the Claude CLI.

Uses the same `claude -p ... --output-format stream-json` mechanism as the
benchmark, so no Anthropic API key is needed (relies on Claude Pro auth).
The vectr daemon must be running for the MCP tools to be available.

Run with:
    pytest -m live_api tests/test_hint_language_live.py -v -s

What is being tested:
    Core hypothesis: when the LLM receives the ACTION REQUIRED eviction hint
    (either chunks-based or time-based fallback), it calls vectr_remember.

    Each test is a 2-turn claude CLI session (~$0.01). The hint text is injected
    directly into the prompt (not as a prior tool call) to avoid the multi-turn
    conversation injection problem with the CLI.

Two scenarios:
    1. Chunks-based: agent got vectr_search results; hint appended to them
    2. Fallback: agent used only Read/Bash; time trigger fired; generic nudge
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_DEFAULT = (
    "/path/to/home/Library/Application Support/Claude"
    "/claude-code/2.1.149/claude.app/Contents/MacOS/claude"
)
CLAUDE_BIN = _CLAUDE_DEFAULT if os.path.exists(_CLAUDE_DEFAULT) else "claude"

VECTR_WORKSPACE = "/path/to/vectr/tmp/poc-cpython-vectr"

VECTR_REMEMBER_TOOL = "mcp__vectr__vectr_remember"

_TASK_CONTEXT = (
    "You are working on CPython. Your task: add gc.total_collected() to the gc module "
    "(returns total objects collected across all GC generations, cumulative since process start).\n\n"
    "You already called vectr_status() at the start of this session: "
    "indexed_files=1039, notes_count=0, no prior notes to recall. "
    "You have been exploring the codebase and found the relevant structures.\n\n"
)

_SEARCH_RESULTS = (
    "vectr_search returned the following results for 'gc generation statistics collected':\n\n"
    "File: Modules/gcmodule.c, lines 120-165 (gc_collect):\n"
    "  static Py_ssize_t gc_collect(...) { stats->collected += n; ... }\n\n"
    "File: Include/internal/pycore_gc.h, lines 88-110 (gc_generation_stats):\n"
    "  struct gc_generation_stats { Py_ssize_t collected; Py_ssize_t uncollectable; };\n"
    "  struct _gc_runtime_state { struct gc_generation_stats generation_stats[NUM_GENERATIONS]; };\n\n"
    "File: Modules/gcmodule.c, lines 1820-1860 (get_stats):\n"
    "  Returns list of dicts per generation, each with .collected field.\n\n"
)

_READ_BASH_CONTEXT = (
    "You have been reading files with Read/Bash for the past several minutes. "
    "You've read Modules/gcmodule.c (1860 lines), Include/internal/pycore_gc.h, "
    "and Modules/clinic/gcmodule.c.h. You now understand: gc_generation_stats holds "
    ".collected per generation; get_stats() packages them; you need to add total_collected() "
    "by summing state->generation_stats[i].collected across NUM_GENERATIONS.\n\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_claude(prompt: str, max_turns: int = 3) -> list[dict]:
    """Run claude CLI from the vectr workspace, return parsed stream-json events."""
    if not Path(VECTR_WORKSPACE).exists():
        pytest.skip(f"vectr workspace not found at {VECTR_WORKSPACE}")

    cmd = [
        CLAUDE_BIN,
        "-p", prompt,
        "--output-format", "stream-json",
        "--max-turns", str(max_turns),
        "--verbose",
        "--allowedTools", VECTR_REMEMBER_TOOL,
    ]
    result = subprocess.run(
        cmd,
        cwd=VECTR_WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
    )

    events = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _tool_calls_in(events: list[dict]) -> list[str]:
    """Extract tool names called across all events."""
    names = []
    for ev in events:
        for msg in ev.get("message", {}).get("content", []):
            if isinstance(msg, dict) and msg.get("type") == "tool_use":
                names.append(msg.get("name", ""))
        # Also check top-level tool_use blocks
        if ev.get("type") == "assistant" and isinstance(ev.get("content"), list):
            for block in ev["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    names.append(block.get("name", ""))
    return names


def _remember_called(events: list[dict]) -> bool:
    """Return True if vectr_remember was called in any event."""
    for ev in events:
        # stream-json wraps tool calls differently depending on claude version
        # check multiple locations
        content_blocks = (
            ev.get("message", {}).get("content", [])
            + (ev.get("content", []) if isinstance(ev.get("content"), list) else [])
        )
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if "remember" in block.get("name", ""):
                    return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.live_api
class TestChunksBasedHint:
    """Hint fires because vectr_search returned results — tests the primary flow."""

    def _hint_text(self) -> str:
        from agent.eviction_advisor import EvictionAdvisor
        adv = EvictionAdvisor()
        adv.record("Modules/gcmodule.c", "120-165", "gc_collect", "static Py_ssize_t gc_collect" * 10)
        adv.record("Include/internal/pycore_gc.h", "88-110", "gc_generation_stats", "struct gc_generation_stats" * 10)
        adv.record("Modules/gcmodule.c", "1820-1860", "get_stats", "static PyObject* get_stats" * 10)
        return adv.eviction_hint()

    def test_hint_text_has_correct_language(self) -> None:
        """Sanity: verify the chunks-based hint has ACTION REQUIRED before sending it live."""
        hint = self._hint_text()
        assert "ACTION REQUIRED" in hint
        assert "vectr_remember" in hint
        assert "Modules/gcmodule.c" in hint

    def test_llm_calls_vectr_remember_after_hint(self) -> None:
        """
        Primary validation: does ACTION REQUIRED language cause vectr_remember to be called?
        2-turn session, ~$0.01. If this fails, hint language must be redesigned.
        """
        hint = self._hint_text()
        prompt = _TASK_CONTEXT + _SEARCH_RESULTS + "---\n\n" + hint + "\n\n---\n\nContinue with your task."

        events = _run_claude(prompt, max_turns=5)

        assert events, "claude produced no output — is the daemon running? is claude CLI installed?"
        assert _remember_called(events), (
            f"LLM did not call vectr_remember after ACTION REQUIRED hint.\n"
            f"Tool calls seen: {_tool_calls_in(events)}\n"
            f"Events: {json.dumps(events[:3], indent=2)[:800]}"
        )


@pytest.mark.live_api
class TestFallbackHint:
    """Hint fires via time trigger with no chunks — tests the fallback path."""

    def _hint_text(self) -> str:
        from agent.eviction_advisor import EvictionAdvisor
        adv = EvictionAdvisor(time_threshold_seconds=0)
        hint = adv.eviction_hint()
        assert hint, "fallback hint must be non-empty when time threshold=0"
        return hint

    def test_fallback_hint_text_has_correct_language(self) -> None:
        """Sanity: verify the time-based fallback hint has ACTION REQUIRED."""
        hint = self._hint_text()
        assert "ACTION REQUIRED" in hint
        assert "vectr_remember" in hint

    def test_llm_calls_vectr_remember_after_fallback_hint(self) -> None:
        """
        Fallback validation: does the generic time-based nudge trigger vectr_remember
        even when no specific chunks are referenced?
        """
        hint = self._hint_text()
        prompt = _TASK_CONTEXT + _READ_BASH_CONTEXT + "---\n\n" + hint + "\n\n---\n\nContinue with your task."

        events = _run_claude(prompt, max_turns=5)

        assert events, "claude produced no output — is the daemon running? is claude CLI installed?"
        assert _remember_called(events), (
            f"LLM did not call vectr_remember after fallback ACTION REQUIRED hint.\n"
            f"Tool calls seen: {_tool_calls_in(events)}\n"
            f"Events: {json.dumps(events[:3], indent=2)[:800]}"
        )
