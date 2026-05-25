"""Optional LLM-generated module summaries stored alongside embeddings."""
from __future__ import annotations

from agent.llm_client import LLMResponse, call_llm

_SYSTEM = """You are a senior software engineer. Given the content of a code file or module,
write a concise 2-4 sentence summary describing:
1. What this module does
2. Its main exports / public API
3. Any important dependencies or side effects

Be specific. Use the actual function/class names. Do not use filler phrases like "this module provides"."""


async def summarise_module(file_path: str, content: str) -> LLMResponse:
    """Generate a short LLM summary for a file/module.

    This is optional — Vectr works without it. Enable via CLI: vectr summarise <path>
    """
    user = f"File: {file_path}\n\n```\n{content[:8000]}\n```"
    return await call_llm(_SYSTEM, user)
