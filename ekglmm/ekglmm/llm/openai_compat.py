"""OpenAI-compatible LLM provider for EKGLMM.

Works with OpenAI, Groq, Together, vLLM, or any OpenAI-compatible endpoint.
Minimal text completion only -- no streaming, no tool handling.
"""

from __future__ import annotations

import logging

import openai

log = logging.getLogger(__name__)


class OpenAICompatLLM:
    """LLMProvider implementation using any OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, system: str, messages: list[dict]) -> str:
        all_messages = [{"role": "system", "content": system}] + messages
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=all_messages,
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content or ""
