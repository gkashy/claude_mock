"""pgvector (Postgres) vector store implementation for EKGLMM.

Works with any PostgreSQL database that has the pgvector extension enabled,
including Supabase. Uses the same Postgres connection as PostgresStorage,
so Soundar only needs one database -- no separate Qdrant instance.

Prerequisites:
    - pgvector extension enabled on the database:
        CREATE EXTENSION IF NOT EXISTS vector;
    - pip install ekglmm[pgvector]

Supabase setup:
    Supabase enables pgvector by default on all projects.
    Just pass the connection string from the Supabase dashboard.

    from ekglmm.vectors import PgVectorStore
    vectors = PgVectorStore("postgresql+asyncpg://postgres:password@db.xxx.supabase.co:5432/postgres")
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector"

_CREATE_FACTS_TABLE = """
CREATE TABLE IF NOT EXISTS ekglmm_fact_vectors (
    fact_id   TEXT PRIMARY KEY,
    user_id   TEXT NOT NULL,
    content   TEXT,
    embedding vector({dim})
);
CREATE INDEX IF NOT EXISTS ix_ekglmm_fv_user ON ekglmm_fact_vectors (user_id);
"""

_CREATE_ENTITIES_TABLE = """
CREATE TABLE IF NOT EXISTS ekglmm_entity_vectors (
    entity_id   TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT,
    entity_type TEXT,
    embedding   vector({dim})
);
CREATE INDEX IF NOT EXISTS ix_ekglmm_ev_user ON ekglmm_entity_vectors (user_id);
"""


def _pg_url(url: str) -> str:
    """Convert SQLAlchemy-style URL to bare asyncpg URL."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


class PgVectorStore:
    """VectorStore implementation backed by PostgreSQL + pgvector.

    Compatible with Supabase, Neon, RDS, and any Postgres >= 14
    with the vector extension installed.
    """

    def __init__(self, database_url: str) -> None:
        self._url = _pg_url(database_url)
        self._pool: asyncpg.Pool | None = None
        self._dim: int | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=2, max_size=10)
        return self._pool

    async def ensure_collections(self, dimension: int) -> None:
        """Create pgvector tables if they don't exist."""
        self._dim = dimension
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_CREATE_EXTENSION)
            await conn.execute(_CREATE_FACTS_TABLE.format(dim=dimension))
            await conn.execute(_CREATE_ENTITIES_TABLE.format(dim=dimension))
        log.info("PgVectorStore: tables ensured (%d dims)", dimension)

    # -- Facts --

    async def upsert_fact(
        self, fact_id: str, embedding: Any, payload: dict[str, Any],
    ) -> None:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ekglmm_fact_vectors (fact_id, user_id, content, embedding)
                VALUES ($1, $2, $3, $4::vector)
                ON CONFLICT (fact_id) DO UPDATE
                    SET user_id   = EXCLUDED.user_id,
                        content   = EXCLUDED.content,
                        embedding = EXCLUDED.embedding
                """,
                fact_id,
                payload.get("user_id", ""),
                payload.get("content", ""),
                str(vec),
            )

    async def search_facts(
        self, embedding: Any, user_id: str, top_k: int = 15,
    ) -> list[dict]:
        """Cosine similarity search. Returns list of {id, score, payload}."""
        vec = embedding if isinstance(embedding, list) else list(embedding)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fact_id, content,
                       1 - (embedding <=> $1::vector) AS score
                FROM ekglmm_fact_vectors
                WHERE user_id = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                str(vec), user_id, top_k,
            )
        return [
            {
                "id": row["fact_id"],
                "score": float(row["score"]),
                "payload": {"user_id": user_id, "content": row["content"]},
            }
            for row in rows
        ]

    async def delete_fact(self, fact_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ekglmm_fact_vectors WHERE fact_id = $1", fact_id,
            )

    # -- Entities --

    async def upsert_entity(
        self, entity_id: str, embedding: Any, payload: dict[str, Any],
    ) -> None:
        vec = embedding if isinstance(embedding, list) else list(embedding)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ekglmm_entity_vectors (entity_id, user_id, name, entity_type, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT (entity_id) DO UPDATE
                    SET user_id     = EXCLUDED.user_id,
                        name        = EXCLUDED.name,
                        entity_type = EXCLUDED.entity_type,
                        embedding   = EXCLUDED.embedding
                """,
                entity_id,
                payload.get("user_id", ""),
                payload.get("name", ""),
                payload.get("entity_type", ""),
                str(vec),
            )

    async def search_entities(
        self, embedding: Any, user_id: str, top_k: int = 5,
    ) -> list[dict]:
        """Cosine similarity search over entity names."""
        vec = embedding if isinstance(embedding, list) else list(embedding)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT entity_id, name, entity_type,
                       1 - (embedding <=> $1::vector) AS score
                FROM ekglmm_entity_vectors
                WHERE user_id = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                str(vec), user_id, top_k,
            )
        return [
            {
                "id": row["entity_id"],
                "name": row["name"],
                "entity_type": row["entity_type"],
                "score": float(row["score"]),
            }
            for row in rows
        ]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
