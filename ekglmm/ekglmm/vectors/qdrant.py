"""Qdrant vector store implementation for EKGLMM."""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models

log = logging.getLogger(__name__)

FACTS_COLLECTION = "facts"
ENTITIES_COLLECTION = "entities"


def _to_qdrant_id(short_id: str) -> str:
    """Pad a short hex ID into a valid UUID for Qdrant."""
    clean = short_id.replace("-", "")
    if len(clean) == 32:
        return short_id
    padded = clean.ljust(32, "0")
    return f"{padded[:8]}-{padded[8:12]}-{padded[12:16]}-{padded[16:20]}-{padded[20:32]}"


class QdrantVectors:
    """VectorStore implementation backed by Qdrant."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        facts_collection: str = FACTS_COLLECTION,
        entities_collection: str = ENTITIES_COLLECTION,
    ) -> None:
        self._client = AsyncQdrantClient(host=host, port=port)
        self._facts = facts_collection
        self._entities = entities_collection

    async def ensure_collections(self, dimension: int) -> None:
        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}

        for name in (self._facts, self._entities):
            if name not in existing:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(
                        size=dimension, distance=models.Distance.COSINE,
                    ),
                )
                log.info("Created Qdrant collection '%s' (%d dims)", name, dimension)

    # -- Facts --

    async def upsert_fact(
        self, fact_id: str, embedding: Any, payload: dict[str, Any],
    ) -> None:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        point = models.PointStruct(
            id=_to_qdrant_id(fact_id), vector=vec,
            payload={**payload, "_fact_id": fact_id},
        )
        await self._client.upsert(collection_name=self._facts, points=[point])

    async def search_facts(
        self, embedding: Any, user_id: str, top_k: int = 15,
    ) -> list[dict]:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        query_filter = models.Filter(must=[
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id),
            )
        ]) if user_id else None

        results = await self._client.query_points(
            collection_name=self._facts, query=vec,
            query_filter=query_filter, limit=top_k, with_payload=True,
        )
        return [
            {
                "id": (p.payload or {}).get("_fact_id", str(p.id)),
                "score": p.score,
                "payload": p.payload or {},
            }
            for p in results.points
        ]

    async def delete_fact(self, fact_id: str) -> None:
        await self._client.delete(
            collection_name=self._facts,
            points_selector=models.PointIdsList(points=[_to_qdrant_id(fact_id)]),
        )

    # -- Entities --

    async def upsert_entity(
        self, entity_id: str, embedding: Any, payload: dict[str, Any],
    ) -> None:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        point = models.PointStruct(
            id=_to_qdrant_id(entity_id), vector=vec,
            payload={**payload, "_entity_id": entity_id},
        )
        await self._client.upsert(collection_name=self._entities, points=[point])

    async def search_entities(
        self, embedding: Any, user_id: str, top_k: int = 5,
    ) -> list[dict]:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        query_filter = models.Filter(must=[
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id),
            )
        ]) if user_id else None

        results = await self._client.query_points(
            collection_name=self._entities, query=vec,
            query_filter=query_filter, limit=top_k, with_payload=True,
        )
        return [
            {
                "id": (p.payload or {}).get("_entity_id", str(p.id)),
                "name": (p.payload or {}).get("name", ""),
                "entity_type": (p.payload or {}).get("entity_type", ""),
                "score": p.score,
            }
            for p in results.points
        ]

    async def close(self) -> None:
        await self._client.close()
