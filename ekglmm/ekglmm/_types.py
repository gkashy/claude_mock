"""Core data types used across the EKGLMM SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entity:
    id: str
    name: str
    name_lower: str
    entity_type: str
    user_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    last_seen: str | None = None
    community: int | None = None


@dataclass
class Relationship:
    id: str
    source_entity_id: str
    target_entity_id: str
    relation: str
    user_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    source_session_id: str | None = None


@dataclass
class Fact:
    id: str
    user_id: str
    content: str
    score: float = 0.0


@dataclass
class Triple:
    subject: str
    subject_type: str
    relation: str
    object: str
    object_type: str


@dataclass
class ResolvedTriple:
    subject: str
    subject_type: str
    relation: str
    obj: str
    object_type: str


@dataclass
class EdgeAction:
    action: str  # ADD, SUPERSEDE, COEXIST, SKIP
    triple: ResolvedTriple
    superseded_rel_id: str | None = None


@dataclass
class Candidate:
    entity_id: str
    name: str
    entity_type: str
    score: float
    method: str  # exact, fuzzy, vector


@dataclass
class RecallResult:
    id: str
    content: str
    score: float
    source: str  # "vector" or "graph"
    metadata: dict[str, Any] = field(default_factory=dict)
