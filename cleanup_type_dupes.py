"""
One-time script: merge same-name different-type entity duplicates.

For every (name_lower, user_id) group that has more than one row in
kg_entities, keep the row with the highest degree (most edges), re-point all
relationships to it, then deactivate the losers.
"""

import asyncio
import json
from collections import defaultdict

from sqlalchemy import select, update, delete, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = "postgresql+asyncpg://agent:agent_dev@localhost:5432/agent_chat"

engine = create_async_engine(DATABASE_URL, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def main() -> None:
    async with Session() as db:
        rows = await db.execute(
            text(
                "SELECT id, name, name_lower, entity_type, user_id "
                "FROM kg_entities WHERE active = true"
            )
        )
        entities = rows.fetchall()

    # Group by (name_lower, user_id)
    groups: dict[tuple, list] = defaultdict(list)
    for e in entities:
        groups[(e.name_lower, e.user_id)].append(e)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    if not dupes:
        print("No cross-type duplicates found.")
        return

    print(f"Found {len(dupes)} duplicate name groups:")
    for key, rows in dupes.items():
        print(f"  {key[0]} ({key[1]}): {[r.entity_type for r in rows]}")

    async with Session() as db:
        for (name_lower, user_id), rows in dupes.items():
            # Determine degree (number of edges) for each entity
            best_id = None
            best_degree = -1
            for row in rows:
                deg_result = await db.execute(
                    text(
                        "SELECT COUNT(*) FROM kg_relationships "
                        "WHERE source_entity_id = :id OR target_entity_id = :id"
                    ),
                    {"id": row.id},
                )
                deg = deg_result.scalar()
                if deg > best_degree:
                    best_degree = deg
                    best_id = row.id

            loser_ids = [r.id for r in rows if r.id != best_id]
            print(f"  Keeping {best_id}, merging {loser_ids} -> {best_id}")

            for loser_id in loser_ids:
                # Re-point outgoing edges
                await db.execute(
                    text(
                        "UPDATE kg_relationships SET source_entity_id = :winner "
                        "WHERE source_entity_id = :loser"
                    ),
                    {"winner": best_id, "loser": loser_id},
                )
                # Re-point incoming edges
                await db.execute(
                    text(
                        "UPDATE kg_relationships SET target_entity_id = :winner "
                        "WHERE target_entity_id = :loser"
                    ),
                    {"winner": best_id, "loser": loser_id},
                )
                # Deactivate the loser entity
                await db.execute(
                    text(
                        "UPDATE kg_entities SET active = false WHERE id = :loser"
                    ),
                    {"loser": loser_id},
                )

        # Remove any self-loops created by the merge
        await db.execute(
            text(
                "DELETE FROM kg_relationships "
                "WHERE source_entity_id = target_entity_id"
            )
        )
        # Remove duplicate edges (same source/target/relation, keep first)
        await db.execute(
            text(
                """
                DELETE FROM kg_relationships
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM kg_relationships
                    GROUP BY source_entity_id, target_entity_id, relation
                )
                """
            )
        )

        await db.commit()

    print("Done. Restart the server to reload the in-memory graph.")


asyncio.run(main())
