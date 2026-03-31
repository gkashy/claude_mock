"""Drop and recreate all PostgreSQL tables, then re-migrate from SQLite."""

import asyncio

async def reset():
    import memory

    print("Dropping all tables in PostgreSQL...")
    async with memory._engine.begin() as conn:
        await conn.run_sync(memory.Base.metadata.drop_all)

    print("Recreating tables with timezone-aware columns...")
    await memory.init_db()
    print("Tables recreated.\n")

if __name__ == "__main__":
    asyncio.run(reset())
