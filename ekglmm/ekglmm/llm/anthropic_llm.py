"""Anthropic (Claude) LLM provider for EKGLMM.

Minimal text completion only -- no streaming, no tool handling.
The SDK only needs text completion for extraction and resolution.
"""

from __future__ import annotations

import logging

import anthropic

log = logging.getLogger(__name__)


class AnthropicLLM:
    """LLMProvider implementation using Anthropic Claude."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, system: str, messages: list[dict]) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,
        )
        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        return "".join(text_parts)
