"""Abstract protocols (interfaces) for pluggable backends.

Consumers provide implementations of these protocols at init time.
Default implementations ship in ekglmm.storage, ekglmm.vectors,
ekglmm.embeddings, and ekglmm.llm.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Persistent store for facts, entities, and relationships."""

    async def init_tables(self) -> None:
        """Create tables / run migrations if needed."""
        ...

    # -- Facts --

    async def save_fact(
        self,
        user_id: str,
        content: str,
        source_session_id: str | None = None,
        embedding: Any = None,
    ) -> str:
        """Insert a new fact. Returns the fact ID."""
        ...

    async def load_facts(self, user_id: str) -> list[dict]:
        """Load all active facts for a user. Each dict has id, content, etc."""
        ...

    async def update_fact(
        self, fact_id: str, content: str, embedding: Any = None
    ) -> bool:
        """Update fact content. Returns True if found."""
        ...

    async def deactivate_fact(self, fact_id: str) -> bool:
        """Soft-delete a fact. Returns True if found."""
        ...

    # -- Entities --

    async def upsert_entity(
        self,
        name: str,
        entity_type: str,
        user_id: str,
        source_session_id: str | None = None,
        properties: dict | None = None,
    ) -> tuple[str, bool]:
        """Create or update an entity. Returns (entity_id, was_created)."""
        ...

    async def list_entities(self, user_id: str) -> list[dict]:
        """List all active entities for a user."""
        ...

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
        """Create or update a relationship. Returns (rel_id, was_created)."""
        ...

    async def deactivate_relationship(self, rel_id: str) -> bool:
        """Soft-delete a relationship. Returns True if found."""
        ...

    async def load_graph_data(self, user_id: str) -> tuple[list[dict], list[dict]]:
        """Load all active entities and relationships for graph init.

        Returns (entities_list, relationships_list).
        """
        ...


@runtime_checkable
class VectorStore(Protocol):
    """Vector index for semantic search over facts and entity names."""

    async def ensure_collections(self, dimension: int) -> None:
        """Create collections (facts + entities) if they don't exist."""
        ...

    async def upsert_fact(
        self, fact_id: str, embedding: Any, payload: dict[str, Any]
    ) -> None:
        ...

    async def search_facts(
        self, embedding: Any, user_id: str, top_k: int = 15
    ) -> list[dict]:
        """Returns list of {id, score, payload}."""
        ...

    async def delete_fact(self, fact_id: str) -> None:
        ...

    async def upsert_entity(
        self, entity_id: str, embedding: Any, payload: dict[str, Any]
    ) -> None:
        ...

    async def search_entities(
        self, embedding: Any, user_id: str, top_k: int = 5
    ) -> list[dict]:
        """Returns list of {id, name, entity_type, score}."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text embedding provider."""

    @property
    def dimension(self) -> int:
        ...

    async def embed_text(self, text: str) -> list[float]:
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal text completion provider for extraction and resolution."""

    async def complete(self, system: str, messages: list[dict]) -> str:
        """Single-shot text completion. Returns the full response text."""
        ...
