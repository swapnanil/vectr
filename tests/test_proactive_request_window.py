"""Window assembly + cache-safe injection tests (UPG-PRO-14/15).

The byte-stable-prefix tests are the load-bearing correctness guarantee: an
injection must never invalidate an existing prompt-cache prefix.
"""
from __future__ import annotations

import copy
import json

from agent.proactive.request_window import (
    append_context_block,
    assemble_window,
    cache_prefix_signature,
)


def _messages_body():
    return {
        "model": "claude-x",
        "system": [
            {"type": "text", "text": "You are a coding agent.", "cache_control": {"type": "ephemeral"}}
        ],
        "tools": [
            {"name": "Read", "input_schema": {}, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "Please read resolver.py"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading it now."},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "resolver.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "def lock(): ..."},
                {"type": "text", "text": "now explain WorkspaceLock",
                 "cache_control": {"type": "ephemeral"}},
            ]},
        ],
    }


# -- window assembly --------------------------------------------------------

def test_assemble_window_extracts_text_and_paths():
    w = assemble_window(_messages_body())
    assert "resolver.py" in w.text
    assert "WorkspaceLock" in w.text
    assert "resolver.py" in w.file_paths
    assert "tool:Read" in w.text
    # WorkspaceLock is a bare identifier in a text block, not a tool arg -> not a symbol anchor.


def test_symbol_anchor_from_tool_arg_only():
    body = {"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "grep", "input": {"pattern": "WorkspaceLock"}},
        ]},
        {"role": "user", "content": "ok"},
    ]}
    w = assemble_window(body)
    assert "WorkspaceLock" in w.symbols
    # A free-text (non-identifier) arg yields no symbol.
    body2 = {"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "grep", "input": {"pattern": "the lock helper"}},
        ]},
        {"role": "user", "content": "ok"},
    ]}
    assert assemble_window(body2).symbols == []


def test_assemble_window_bounds_and_malformed():
    assert assemble_window({}).is_empty()
    assert assemble_window({"messages": "nope"}).is_empty()
    # Char budget: text never exceeds max_chars.
    big = {"messages": [{"role": "user", "content": "z" * 10000}]}
    w = assemble_window(big, max_chars=500)
    assert len(w.text) <= 500


def test_include_thinking_flag():
    body = {"messages": [
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "secret reasoning"}]},
        {"role": "user", "content": "hi"},
    ]}
    assert "secret reasoning" not in assemble_window(body, include_thinking=False).text
    assert "secret reasoning" in assemble_window(body, include_thinking=True).text


# -- cache-safe injection ---------------------------------------------------

def test_injection_appends_and_keeps_prefix_byte_stable():
    body = _messages_body()
    before_sig = cache_prefix_signature(body)
    original = copy.deepcopy(body)

    new_body, injected = append_context_block(body, "PROACTIVE: note #1")
    assert injected is True
    # Input body was NOT mutated.
    assert body == original
    # The protected cache prefix is byte-identical after injection.
    assert cache_prefix_signature(new_body) == before_sig
    # The injected block is the newest content on the last user message.
    last_content = new_body["messages"][-1]["content"]
    assert last_content[-1] == {"type": "text", "text": "PROACTIVE: note #1"}
    # Every pre-existing block (incl. the cache breakpoint) is byte-unchanged.
    assert last_content[:-1] == original["messages"][-1]["content"]


def test_byte_stable_prefix_with_and_without_injection():
    """Consecutive requests, one injected and one not, must share the exact
    same cache prefix so cached reads keep hitting."""
    body_no_inject = _messages_body()
    body_inject = _messages_body()
    sig_no = cache_prefix_signature(body_no_inject)
    new_body, injected = append_context_block(body_inject, "extra context")
    assert injected
    sig_yes = cache_prefix_signature(new_body)
    assert sig_no == sig_yes


def test_injection_into_string_content_preserves_prefix():
    body = {
        "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "assistant", "content": "prior"},
            {"role": "user", "content": "the last user turn as a plain string"},
        ],
    }
    before = cache_prefix_signature(body)
    new_body, injected = append_context_block(body, "CTX")
    assert injected
    assert cache_prefix_signature(new_body) == before
    content = new_body["messages"][-1]["content"]
    assert content == [
        {"type": "text", "text": "the last user turn as a plain string"},
        {"type": "text", "text": "CTX"},
    ]


def test_no_injection_when_last_message_not_user():
    body = {"messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "prefill"}]},
    ]}
    new_body, injected = append_context_block(body, "CTX")
    assert injected is False
    assert new_body is body  # unchanged, same object


def test_no_injection_on_empty_context():
    body = _messages_body()
    new_body, injected = append_context_block(body, "   ")
    assert injected is False
    assert new_body is body
