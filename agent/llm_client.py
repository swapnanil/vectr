"""Multi-provider LLM adapter. Used only for optional module summary generation."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int


CLAUDE_MODELS = {"claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-7"}
OPENAI_MODELS = {"gpt-4o", "gpt-4o-mini", "o3", "o3-mini", "o4-mini"}


def get_model() -> str:
    return os.getenv("LLM_MODEL", "claude-sonnet-4-6")


async def call_llm(system: str, user: str, *, use_cache: bool = True) -> LLMResponse:
    model = get_model()
    if model in CLAUDE_MODELS or model.startswith("claude"):
        return await _call_anthropic(model, system, user, use_cache=use_cache)
    elif model in OPENAI_MODELS or model.startswith(("gpt-", "o1", "o3", "o4")):
        return await _call_openai(model, system, user)
    raise ValueError(f"Unknown model '{model}'. Set LLM_MODEL to a Claude or OpenAI model name.")


async def _call_anthropic(model: str, system: str, user: str, *, use_cache: bool) -> LLMResponse:
    import anthropic

    client = anthropic.AsyncAnthropic()
    system_block = [{"type": "text", "text": system}]
    if use_cache:
        system_block[0]["cache_control"] = {"type": "ephemeral"}

    for attempt in range(3):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_block,
                messages=[{"role": "user", "content": user}],
            )
            return LLMResponse(
                content=resp.content[0].text,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )
        except anthropic.RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except anthropic.AuthenticationError:
            raise


async def _call_openai(model: str, system: str, user: str) -> LLMResponse:
    import openai

    client = openai.AsyncOpenAI()
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return LLMResponse(
                content=resp.choices[0].message.content,
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
            )
        except openai.RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except openai.AuthenticationError:
            raise
