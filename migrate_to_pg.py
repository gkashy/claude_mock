"""
One-time migration: SQLite (data/chat.db) -> PostgreSQL + Qdrant.

Reads all sessions, messages, and facts from the SQLite database,
writes them into PostgreSQL, embeds all facts, and upserts vectors
into Qdrant.

Usage:
    docker compose up -d
    python migrate_to_pg.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

SQLITE_PATH = Path("data/chat.db")
SQLITE_URL = f"sqlite+aiosqlite:///{SQLITE_PATH}"


class OldBase(DeclarativeBase):
    pass

class OldSession(OldBase):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    user_id = Column(String)
    title = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class OldMessage(OldBase):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id"))
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime)

class OldFact(OldBase):
    __tablename__ = "facts"
    id = Column(String, primary_key=True)
    user_id = Column(String)
    content = Column(Text)
    source_session_id = Column(String)
    created_at = Column(DateTime)


async def migrate():
    if not SQLITE_PATH.exists():
        print(f"No SQLite database found at {SQLITE_PATH}. Nothing to migrate.")
        return

    print(f"Reading from SQLite: {SQLITE_PATH}")
    sqlite_engine = create_async_engine(SQLITE_URL, echo=False)
    sqlite_session_factory = async_sessionmaker(sqlite_engine, class_=AsyncSession, expire_on_commit=False)

    async with sqlite_session_factory() as db:
        sessions = (await db.execute(select(OldSession))).scalars().all()
        messages = (await db.execute(select(OldMessage))).scalars().all()
        facts = (await db.execute(select(OldFact))).scalars().all()

    await sqlite_engine.dispose()

    print(f"  Found {len(sessions)} sessions, {len(messages)} messages, {len(facts)} facts")

    if not sessions and not messages and not facts:
        print("Nothing to migrate.")
        return

    from config import settings
    print(f"\nWriting to PostgreSQL: {settings.DATABASE_URL}")

    import memory
    await memory.init_db()

    pg_engine = memory._engine
    pg_session_factory = memory._async_session

    async with pg_session_factory() as db:
        for s in sessions:
            db.add(memory.Session(
                id=s.id,
                user_id=s.user_id or "default",
                title=s.title,
                created_at=s.created_at,
                updated_at=s.updated_at,
            ))
        await db.commit()
        print(f"  Migrated {len(sessions)} sessions")

        for m in messages:
            db.add(memory.Message(
                id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            ))
        await db.commit()
        print(f"  Migrated {len(messages)} messages")

        for f in facts:
            db.add(memory.Fact(
                id=f.id,
                user_id=f.user_id or "default",
                content=f.content,
                source_session_id=f.source_session_id,
                created_at=f.created_at,
            ))
        await db.commit()
        print(f"  Migrated {len(facts)} facts")

    if facts:
        print(f"\nEmbedding {len(facts)} facts and upserting into Qdrant...")
        import vector_store
        from embeddings import embed_batch

        await vector_store.ensure_collection()

        fact_texts = [f.content for f in facts]
        embeddings = await embed_batch(fact_texts)

        qdrant_points = []
        for fact, emb in zip(facts, embeddings):
            qdrant_points.append({
                "id": fact.id,
                "embedding": emb,
                "payload": {
                    "user_id": fact.user_id or "default",
                    "content": fact.content,
                },
            })

        await vector_store.batch_upsert(qdrant_points)
        print(f"  Upserted {len(qdrant_points)} vectors into Qdrant")

    print("\n--- Migration complete ---")
    print("You can now start the app with: python app.py")
    print(f"The old SQLite database at {SQLITE_PATH} can be archived or deleted.")


if __name__ == "__main__":
    asyncio.run(migrate())
