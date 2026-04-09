"""SQLAlchemy ORM models for the EKGLMM storage layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class FactModel(Base):
    __tablename__ = "facts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    user_id = Column(String, nullable=False, default="default", index=True)
    content = Column(Text, nullable=False)
    source_session_id = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class EntityModel(Base):
    __tablename__ = "kg_entities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    name = Column(String, nullable=False)
    name_lower = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    properties = Column(Text, nullable=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    source_session_id = Column(String, nullable=True)
    first_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        Index("ix_entity_dedup", "name_lower", "entity_type", "user_id", unique=True),
        Index("ix_entity_user_active", "user_id", "active"),
    )


class RelationshipModel(Base):
    __tablename__ = "kg_relationships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    source_entity_id = Column(String, ForeignKey("kg_entities.id"), nullable=False)
    target_entity_id = Column(String, ForeignKey("kg_entities.id"), nullable=False)
    relation = Column(String, nullable=False)
    properties = Column(Text, nullable=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    source_session_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        Index("ix_rel_dedup", "source_entity_id", "target_entity_id", "relation", "user_id", unique=True),
        Index("ix_rel_user_active", "user_id", "active"),
    )
