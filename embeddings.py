"""
Embedding client for semantic memory retrieval.

Uses OpenAI's text-embedding-3-small model (1536 dimensions).
All vectors returned by OpenAI are L2-normalized, so cosine similarity
reduces to a simple dot product.
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import openai

from config import settings

log = logging.getLogger(__name__)

_client: openai.AsyncOpenAI | None = None

EMBEDDING_DIM = 1536


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is required for embeddings. "
                "Set it in your .env file."
            )
        _client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def embed_text(text: str) -> np.ndarray:
    """Embed a single text string. Returns a 1-D float32 numpy array."""
    vecs = await embed_batch([text])
    return vecs[0]


async def embed_batch(texts: Sequence[str]) -> list[np.ndarray]:
    """Embed multiple texts in one API call (max 2048 per call).

    Returns a list of 1-D float32 numpy arrays, one per input text.
    Automatically chunks inputs that exceed the API batch limit.
    """
    if not texts:
        return []

    all_vectors: list[np.ndarray] = []
    chunk_size = 2048

    for start in range(0, len(texts), chunk_size):
        chunk = texts[start : start + chunk_size]
        cleaned = [t.replace("\n", " ").strip() for t in chunk]

        try:
            response = await _get_client().embeddings.create(
                input=cleaned,
                model=settings.EMBEDDING_MODEL,
            )
            for item in response.data:
                all_vectors.append(
                    np.array(item.embedding, dtype=np.float32)
                )
        except openai.APIError as exc:
            log.error("Embedding API error: %s", exc)
            raise

    return all_vectors


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors.

    OpenAI embeddings are pre-normalized, so this is just a dot product.
    Handles the edge case of zero vectors gracefully.
    """
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def rank_by_similarity(
    query_vec: np.ndarray,
    candidates: list[np.ndarray],
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    """Rank candidate vectors by cosine similarity to the query.

    Args:
        query_vec: The query embedding.
        candidates: List of candidate embeddings.
        top_k: If set, return only the top-K results.

    Returns:
        List of (original_index, similarity_score) tuples, descending by score.
    """
    if not candidates:
        return []

    matrix = np.stack(candidates)
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    matrix_norm = matrix / norms

    scores = matrix_norm @ query_norm
    ranked_indices = np.argsort(scores)[::-1]

    if top_k is not None:
        ranked_indices = ranked_indices[:top_k]

    return [(int(idx), float(scores[idx])) for idx in ranked_indices]


def serialize_embedding(vec: np.ndarray) -> bytes:
    """Convert a numpy embedding to bytes for SQLite BLOB storage."""
    return vec.astype(np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    """Restore a numpy embedding from SQLite BLOB storage."""
    return np.frombuffer(blob, dtype=np.float32).copy()
