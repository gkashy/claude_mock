"""
Qdrant vector store client for semantic memory.

Manages two collections:
- ``facts``    -- fact sentence embeddings for semantic memory dedup/search
- ``entities`` -- entity name embeddings for entity resolution candidate retrieval

All vectors are 1536-dimension float32 arrays produced by OpenAI
text-embedding-3-small.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from qdrant_client import AsyncQdrantClient, models

from config import settings
from embeddings import EMBEDDING_DIM

log = logging.getLogger(__name__)

COLLECTION_NAME = "facts"
ENTITY_COLLECTION_NAME = "entities"


def _to_qdrant_id(fact_id: str) -> str:
    """Convert a short fact ID to a valid UUID for Qdrant.

    Qdrant requires point IDs to be unsigned integers or UUIDs.
    Our fact IDs are 8-char hex strings from uuid4()[:8], so we
    pad them into a valid UUID format.
    """
    clean = fact_id.replace("-", "")
    if len(clean) == 32:
        return fact_id
    padded = clean.ljust(32, "0")
    return f"{padded[:8]}-{padded[8:12]}-{padded[12:16]}-{padded[16:20]}-{padded[20:32]}"

_client: AsyncQdrantClient | None = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
        )
    return _client


async def ensure_collection() -> None:
    """Create the facts collection if it doesn't exist."""
    client = _get_client()
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]

    if COLLECTION_NAME not in existing:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        log.info("Created Qdrant collection '%s' (%d dims)", COLLECTION_NAME, EMBEDDING_DIM)
    else:
        log.info("Qdrant collection '%s' already exists", COLLECTION_NAME)


async def upsert_fact(
    fact_id: str,
    embedding: np.ndarray,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert or update a single fact vector."""
    client = _get_client()
    point = models.PointStruct(
        id=_to_qdrant_id(fact_id),
        vector=embedding.tolist(),
        payload={**(payload or {}), "_fact_id": fact_id},
    )
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[point],
    )


async def batch_upsert(
    facts: list[dict],
) -> None:
    """Bulk upsert facts. Each dict must have ``id``, ``embedding``, and ``payload``."""
    if not facts:
        return
    client = _get_client()
    points = [
        models.PointStruct(
            id=_to_qdrant_id(f["id"]),
            vector=f["embedding"].tolist() if isinstance(f["embedding"], np.ndarray) else f["embedding"],
            payload={**f.get("payload", {}), "_fact_id": f["id"]},
        )
        for f in facts
    ]
    batch_size = 100
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        await client.upsert(
            collection_name=COLLECTION_NAME,
            points=batch,
        )
    log.info("Batch upserted %d vectors into Qdrant", len(points))


async def delete_fact(fact_id: str) -> None:
    """Remove a fact vector from Qdrant."""
    client = _get_client()
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.PointIdsList(points=[_to_qdrant_id(fact_id)]),
    )


async def search(
    query_embedding: np.ndarray,
    top_k: int = 15,
    user_id: str | None = None,
) -> list[dict]:
    """Search for similar facts. Returns list of {id, score, payload}."""
    client = _get_client()

    query_filter = None
    if user_id:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id),
                )
            ]
        )

    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding.tolist(),
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "id": (point.payload or {}).get("_fact_id", str(point.id)),
            "score": point.score,
            "payload": point.payload or {},
        }
        for point in results.points
    ]


async def get_all_points(
    user_id: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Retrieve all points (for dedup checks). Returns {id, payload, vector}."""
    client = _get_client()

    scroll_filter = None
    if user_id:
        scroll_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id),
                )
            ]
        )

    points, _next_offset = await client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=scroll_filter,
        limit=limit,
        with_vectors=True,
        with_payload=True,
    )

    return [
        {
            "id": (p.payload or {}).get("_fact_id", str(p.id)),
            "payload": p.payload or {},
            "vector": np.array(p.vector, dtype=np.float32) if p.vector else None,
        }
        for p in points
    ]


async def ensure_entity_collection() -> None:
    """Create the entities collection if it doesn't exist."""
    client = _get_client()
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]

    if ENTITY_COLLECTION_NAME not in existing:
        await client.create_collection(
            collection_name=ENTITY_COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        log.info("Created Qdrant collection '%s' (%d dims)", ENTITY_COLLECTION_NAME, EMBEDDING_DIM)
    else:
        log.info("Qdrant collection '%s' already exists", ENTITY_COLLECTION_NAME)


async def upsert_entity_embedding(
    entity_id: str,
    embedding: np.ndarray,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert or update an entity name vector."""
    client = _get_client()
    point = models.PointStruct(
        id=_to_qdrant_id(entity_id),
        vector=embedding.tolist(),
        payload={**(payload or {}), "_entity_id": entity_id},
    )
    await client.upsert(
        collection_name=ENTITY_COLLECTION_NAME,
        points=[point],
    )


async def search_entities(
    query_embedding: np.ndarray,
    user_id: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Search for similar entity names. Returns list of {id, name, score}."""
    client = _get_client()

    query_filter = None
    if user_id:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id),
                )
            ]
        )

    results = await client.query_points(
        collection_name=ENTITY_COLLECTION_NAME,
        query=query_embedding.tolist(),
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "id": (point.payload or {}).get("_entity_id", str(point.id)),
            "name": (point.payload or {}).get("name", ""),
            "entity_type": (point.payload or {}).get("entity_type", ""),
            "score": point.score,
        }
        for point in results.points
    ]


async def close() -> None:
    """Cleanly shut down the Qdrant client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
