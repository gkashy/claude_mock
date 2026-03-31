"""Backfill: embed existing facts from PostgreSQL and upsert into Qdrant."""

import asyncio

async def backfill():
    from config import settings
    import memory
    import vector_store
    from embeddings import embed_batch

    await memory.init_db()
    await vector_store.ensure_collection()

    facts = await memory.load_facts("default")
    if not facts:
        print("No facts to backfill.")
        return

    print(f"Embedding {len(facts)} facts...")
    texts = [f["content"] for f in facts]
    embeddings = await embed_batch(texts)

    points = []
    for fact, emb in zip(facts, embeddings):
        points.append({
            "id": fact["id"],
            "embedding": emb,
            "payload": {
                "user_id": "default",
                "content": fact["content"],
            },
        })

    await vector_store.batch_upsert(points)
    print(f"Upserted {len(points)} vectors into Qdrant.")

    results = await vector_store.search(embeddings[0], top_k=3, user_id="default")
    print(f"\nVerification -- top 3 matches for first fact:")
    for r in results:
        print(f"  [{r['id']}] score={r['score']:.4f} | {r['payload'].get('content', '')[:80]}")

    await vector_store.close()
    print("\n--- Backfill complete ---")

if __name__ == "__main__":
    asyncio.run(backfill())
