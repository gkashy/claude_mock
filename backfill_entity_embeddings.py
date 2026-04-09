"""
One-time script: embed all existing entity names and populate the
Qdrant `entities` collection for vector-based candidate matching.

Run once after deploying the entity resolution pipeline.
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main() -> None:
    from config import settings  # noqa: F401 (trigger settings init)
    import vector_store
    from embeddings import embed_text, embed_batch
    import memory

    await memory.init_db()
    await vector_store.ensure_entity_collection()

    entities = await memory.list_entities(user_id="default")
    if not entities:
        log.info("No entities found, nothing to backfill.")
        return

    log.info("Backfilling embeddings for %d entities...", len(entities))

    names = [e["name"] for e in entities]
    embeddings = await embed_batch(names)

    for entity, embedding in zip(entities, embeddings):
        await vector_store.upsert_entity_embedding(
            entity_id=entity["id"],
            embedding=embedding,
            payload={
                "user_id": "default",
                "name": entity["name"],
                "entity_type": entity["entity_type"],
            },
        )

    log.info("Done. Embedded %d entity names into Qdrant 'entities' collection.", len(entities))


if __name__ == "__main__":
    asyncio.run(main())
