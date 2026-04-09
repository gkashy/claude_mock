"""PostgreSQL storage backend using async SQLAlchemy + asyncpg."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ._models import Base, EntityModel, FactModel, RelationshipModel

log = logging.getLogger(__name__)


class PostgresStorage:
    """StorageBackend implementation backed by PostgreSQL."""

    def __init__(self, database_url: str, **engine_kwargs: Any) -> None:
        defaults = {
            "echo": False,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
        }
        defaults.update(engine_kwargs)
        self._engine = create_async_engine(database_url, **defaults)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False,
        )

    def _session(self) -> AsyncSession:
        return self._session_factory()

    async def init_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("PostgresStorage: tables ensured")

    # -- Facts --

    async def save_fact(
        self,
        user_id: str,
        content: str,
        source_session_id: str | None = None,
        embedding: Any = None,
    ) -> str:
        fact_id = str(uuid.uuid4())[:8]
        async with self._session() as db:
            db.add(FactModel(
                id=fact_id, user_id=user_id, content=content,
                source_session_id=source_session_id, active=True,
            ))
            await db.commit()
        return fact_id

    async def load_facts(self, user_id: str) -> list[dict]:
        async with self._session() as db:
            result = await db.execute(
                select(FactModel)
                .where(FactModel.user_id == user_id, FactModel.active == True)
                .order_by(FactModel.created_at)
            )
            return [
                {
                    "id": r.id,
                    "content": r.content,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in result.scalars().all()
            ]

    async def update_fact(
        self, fact_id: str, content: str, embedding: Any = None,
    ) -> bool:
        async with self._session() as db:
            result = await db.execute(
                select(FactModel).where(FactModel.id == fact_id)
            )
            fact = result.scalar_one_or_none()
            if not fact:
                return False
            fact.content = content
            fact.updated_at = datetime.now(timezone.utc)
            await db.commit()
        return True

    async def deactivate_fact(self, fact_id: str) -> bool:
        async with self._session() as db:
            result = await db.execute(
                select(FactModel).where(FactModel.id == fact_id)
            )
            fact = result.scalar_one_or_none()
            if not fact:
                return False
            fact.active = False
            fact.updated_at = datetime.now(timezone.utc)
            await db.commit()
        return True

    # -- Entities --

    async def upsert_entity(
        self,
        name: str,
        entity_type: str,
        user_id: str,
        source_session_id: str | None = None,
        properties: dict | None = None,
    ) -> tuple[str, bool]:
        name_lower = name.strip().lower()
        props_json = json.dumps(properties or {})
        now = datetime.now(timezone.utc)

        async with self._session() as db:
            result = await db.execute(
                select(EntityModel).where(
                    EntityModel.name_lower == name_lower,
                    EntityModel.user_id == user_id,
                    EntityModel.active == True,
                )
            )
            existing = result.scalars().first()

            if existing:
                existing.last_seen = now
                if properties:
                    try:
                        merged = {**json.loads(existing.properties or "{}"), **properties}
                        existing.properties = json.dumps(merged)
                    except (json.JSONDecodeError, TypeError):
                        existing.properties = props_json
                await db.commit()
                return existing.id, False

            entity_id = str(uuid.uuid4())[:8]
            db.add(EntityModel(
                id=entity_id, name=name.strip(), name_lower=name_lower,
                entity_type=entity_type, properties=props_json,
                user_id=user_id, source_session_id=source_session_id,
                first_seen=now, last_seen=now, active=True,
            ))
            await db.commit()
            return entity_id, True

    async def list_entities(self, user_id: str) -> list[dict]:
        async with self._session() as db:
            result = await db.execute(
                select(EntityModel)
                .where(EntityModel.user_id == user_id, EntityModel.active == True)
                .order_by(EntityModel.last_seen.desc())
            )
            return [
                {
                    "id": e.id, "name": e.name,
                    "entity_type": e.entity_type,
                    "properties": json.loads(e.properties or "{}"),
                    "first_seen": e.first_seen.isoformat() if e.first_seen else None,
                    "last_seen": e.last_seen.isoformat() if e.last_seen else None,
                }
                for e in result.scalars().all()
            ]

    # -- Relationships --

    async def upsert_relationship(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relation: str,
        user_id: str,
        source_session_id: str | None = None,
        properties: dict | None = None,
    ) -> tuple[str, bool]:
        props_json = json.dumps(properties or {})
        now = datetime.now(timezone.utc)

        async with self._session() as db:
            result = await db.execute(
                select(RelationshipModel).where(
                    RelationshipModel.source_entity_id == source_entity_id,
                    RelationshipModel.target_entity_id == target_entity_id,
                    RelationshipModel.relation == relation,
                    RelationshipModel.user_id == user_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.updated_at = now
                existing.active = True
                if properties:
                    try:
                        merged = {**json.loads(existing.properties or "{}"), **properties}
                        existing.properties = json.dumps(merged)
                    except (json.JSONDecodeError, TypeError):
                        existing.properties = props_json
                await db.commit()
                return existing.id, False

            rel_id = str(uuid.uuid4())[:8]
            db.add(RelationshipModel(
                id=rel_id, source_entity_id=source_entity_id,
                target_entity_id=target_entity_id, relation=relation,
                properties=props_json, user_id=user_id,
                source_session_id=source_session_id,
                created_at=now, updated_at=now, active=True,
            ))
            await db.commit()
            return rel_id, True

    async def deactivate_relationship(self, rel_id: str) -> bool:
        async with self._session() as db:
            result = await db.execute(
                select(RelationshipModel).where(RelationshipModel.id == rel_id)
            )
            rel = result.scalar_one_or_none()
            if not rel:
                return False
            rel.active = False
            await db.commit()
        return True

    async def load_graph_data(self, user_id: str) -> tuple[list[dict], list[dict]]:
        async with self._session() as db:
            ent_result = await db.execute(
                select(EntityModel).where(
                    EntityModel.user_id == user_id, EntityModel.active == True,
                )
            )
            entities = [
                {
                    "id": e.id, "name": e.name, "name_lower": e.name_lower,
                    "entity_type": e.entity_type,
                    "properties": json.loads(e.properties or "{}"),
                    "source_session_id": e.source_session_id,
                    "first_seen": e.first_seen.isoformat() if e.first_seen else None,
                    "last_seen": e.last_seen.isoformat() if e.last_seen else None,
                }
                for e in ent_result.scalars().all()
            ]

            rel_result = await db.execute(
                select(RelationshipModel).where(
                    RelationshipModel.user_id == user_id,
                    RelationshipModel.active == True,
                )
            )
            relationships = [
                {
                    "id": r.id, "source_entity_id": r.source_entity_id,
                    "target_entity_id": r.target_entity_id,
                    "relation": r.relation,
                    "properties": json.loads(r.properties or "{}"),
                    "source_session_id": r.source_session_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rel_result.scalars().all()
            ]
        return entities, relationships

    async def close(self) -> None:
        await self._engine.dispose()
