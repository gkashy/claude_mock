"""OpenAI embedding provider for EKGLMM."""

from __future__ import annotations

import logging

import openai

log = logging.getLogger(__name__)


class OpenAIEmbeddings:
    """EmbeddingProvider implementation using OpenAI text-embedding-3-small."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
    ) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed_text(self, text: str) -> list[float]:
        result = await self.embed_batch([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        chunk_size = 2048

        for start in range(0, len(texts), chunk_size):
            chunk = texts[start : start + chunk_size]
            cleaned = [t.replace("\n", " ").strip() for t in chunk]
            response = await self._client.embeddings.create(
                input=cleaned, model=self._model,
            )
            for item in response.data:
                all_vectors.append(item.embedding)

        return all_vectors
