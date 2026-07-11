"""Proactive context (experimental, UPG-PRO).

Deterministically surface the relevant working-memory note or structural match
at a sanctioned delivery moment, instead of waiting for the agent to ask. Two
delivery seams share one intelligence layer:

- Phase 1 (hooks) — future; not built here.
- Phase 3 (proxy) — a localhost Anthropic-shaped API proxy the agent harness
  targets with ANTHROPIC_BASE_URL. The proxy assembles a window from each
  request, asks the daemon for packed context, and appends it AFTER the last
  prompt-cache breakpoint before forwarding upstream.

Everything here is deterministic: structural exact matches + numeric similarity
thresholds + additive packing. There is no keyword/regex classification of
conversation content anywhere (the project's no-query-heuristics hard rule).
No LLM/API call is ever made to compute proactive context — matching is local
embeddings + structural lookups only.
"""
from __future__ import annotations

from agent.proactive.types import (
    Candidate,
    InjectionResult,
    ProactiveWindow,
    PROVENANCE_RANK,
)

__all__ = [
    "Candidate",
    "InjectionResult",
    "ProactiveWindow",
    "PROVENANCE_RANK",
]
