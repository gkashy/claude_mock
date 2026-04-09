"""EKGLMM -- Efficient Knowledge Graph and Long-term Memory Manager.

Built on graphify for graph visualization, community clustering, and
NetworkX graph infrastructure. Adds persistent memory, LLM-powered
extraction, entity/edge resolution, and graph-enriched retrieval.

Usage:
    from ekglmm import EKGLMM
    from ekglmm.storage import PostgresStorage
    from ekglmm.vectors import QdrantVectors
    from ekglmm.embeddings import OpenAIEmbeddings
    from ekglmm.llm import AnthropicLLM

    mem = EKGLMM(
        storage=PostgresStorage("postgresql+asyncpg://user:pass@localhost/db"),
        vectors=QdrantVectors("localhost", 6333),
        embeddings=OpenAIEmbeddings(api_key="sk-..."),
        llm=AnthropicLLM(api_key="sk-ant-..."),
    )
    await mem.init()
    await mem.remember("User prefers morning workouts", user_id="u1")
    results = await mem.recall("workout preferences", user_id="u1")
"""

from __future__ import annotations

import logging
from typing import Any

from ._types import (
    Candidate,
    EdgeAction,
    Entity,
    Fact,
    RecallResult,
    Relationship,
    ResolvedTriple,
    Triple,
)
from .enrichment import recall as _recall
from .extraction import extract_from_messages as _extract
from .graph import GraphManager
from .protocols import EmbeddingProvider, LLMProvider, StorageBackend, VectorStore

__all__ = [
    "EKGLMM",
    "GraphManager",
    "StorageBackend",
    "VectorStore",
    "EmbeddingProvider",
    "LLMProvider",
    "RecallResult",
    "Entity",
    "Relationship",
    "Fact",
    "Triple",
    "ResolvedTriple",
    "EdgeAction",
    "Candidate",
]

log = logging.getLogger(__name__)

_DEDUP_SIMILARITY_THRESHOLD = 0.85


class EKGLMM:
    """Main entry point -- wires storage, vectors, embeddings, LLM, and graph."""

    def __init__(
        self,
        storage: StorageBackend,
        vectors: VectorStore,
        embeddings: EmbeddingProvider,
        llm: LLMProvider,
        *,
        user_name: str | None = None,
        entity_types: list[str] | None = None,
        relation_synonyms: dict[str, str] | None = None,
    ) -> None:
        self._storage = storage
        self._vectors = vectors
        self._embeddings = embeddings
        self._llm = llm
        self._user_name = user_name
        self._entity_types = entity_types
        self._relation_synonyms = relation_synonyms

        etypes = frozenset(entity_types) if entity_types else None
        self._graph = GraphManager(entity_types=etypes)

    async def init(self, user_id: str = "default") -> None:
        """Create tables, ensure collections, load graph into memory."""
        await self._storage.init_tables()
        await self._vectors.ensure_collections(self._embeddings.dimension)
        await self._graph.load(self._storage, user_id)
        log.info("EKGLMM initialized for user=%s", user_id)

    # ------------------------------------------------------------------
    # 5-method API
    # ------------------------------------------------------------------

    async def remember(
        self,
        content: str,
        user_id: str = "default",
        source: str | None = None,
    ) -> tuple[str, str]:
        """Store a fact with dedup + auto-extract triples. Returns (fact_id, action).

        Actions: "created", "updated", "duplicate".
        """
        embedding = await self._embeddings.embed_text(content)

        existing = await self._vectors.search_facts(embedding, user_id, top_k=3)
        for r in existing:
            if r.get("score", 0) >= _DEDUP_SIMILARITY_THRESHOLD:
                existing_content = r.get("payload", {}).get("content", "")
                if existing_content.strip() == content.strip():
                    return r["id"], "duplicate"
                await self._storage.update_fact(r["id"], content, embedding)
                await self._vectors.upsert_fact(
                    r["id"], embedding, {"user_id": user_id, "content": content},
                )
                await self._mini_extract(content, user_id, source)
                return r["id"], "updated"

        fact_id = await self._storage.save_fact(user_id, content, source, embedding)
        await self._vectors.upsert_fact(
            fact_id, embedding, {"user_id": user_id, "content": content},
        )

        await self._mini_extract(content, user_id, source)
        return fact_id, "created"

    async def recall(
        self,
        query: str,
        user_id: str = "default",
        top_k: int = 10,
    ) -> list[RecallResult]:
        """Semantic search + graph enrichment. Returns ranked results."""
        return await _recall(
            query, user_id, self._graph, self._storage,
            self._embeddings, self._vectors, top_k=top_k,
        )

    async def extract(
        self,
        messages: list[dict],
        user_id: str = "default",
        session_id: str | None = None,
    ) -> list[str]:
        """Full extraction: facts + triples + triple_deletions from a conversation."""
        return await _extract(
            messages, user_id, self._graph, self._storage,
            self._llm, self._embeddings, self._vectors,
            user_name=self._user_name or self._graph.primary_person_name(user_id),
            entity_types=self._entity_types,
            relation_synonyms=self._relation_synonyms,
            session_id=session_id,
        )

    async def forget(self, fact_id: str) -> bool:
        """Deactivate a fact + remove from vector store."""
        ok = await self._storage.deactivate_fact(fact_id)
        if ok:
            try:
                await self._vectors.delete_fact(fact_id)
            except Exception:
                log.warning("Failed to delete fact %s from vectors", fact_id, exc_info=True)
        return ok

    @property
    def graph(self) -> GraphManager:
        """Direct access: neighbors, stats, export, visualize, cluster."""
        return self._graph

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _mini_extract(
        self, content: str, user_id: str, source: str | None,
    ) -> None:
        """Run a lightweight extraction on a single fact to produce graph triples."""
        try:
            await _extract(
                [{"role": "user", "content": content}],
                user_id, self._graph, self._storage,
                self._llm, self._embeddings, self._vectors,
                user_name=self._user_name or self._graph.primary_person_name(user_id),
                entity_types=self._entity_types,
                relation_synonyms=self._relation_synonyms,
                session_id=source,
            )
        except Exception:
            log.debug("Mini-extraction failed for remember()", exc_info=True)
