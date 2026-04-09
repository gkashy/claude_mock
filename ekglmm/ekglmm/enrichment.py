"""Graph-enriched semantic recall.

Vector search retrieves relevant facts, then the graph expands context
by pulling first-degree neighbors of mentioned entities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._types import RecallResult

if TYPE_CHECKING:
    from .graph import GraphManager
    from .protocols import EmbeddingProvider, StorageBackend, VectorStore

log = logging.getLogger(__name__)

_MIN_RELEVANCE_SCORE = 0.25


async def recall(
    query: str,
    user_id: str,
    graph: GraphManager,
    storage: StorageBackend,
    embeddings: EmbeddingProvider,
    vectors: VectorStore,
    top_k: int = 10,
    token_budget: int = 3000,
) -> list[RecallResult]:
    """Semantic search + graph enrichment. Returns ranked results."""
    try:
        query_embedding = await embeddings.embed_text(query)
    except Exception:
        log.warning("Failed to embed query", exc_info=True)
        return []

    results = await vectors.search_facts(query_embedding, user_id, top_k=top_k)
    if not results:
        return []

    budget_chars = token_budget * 4
    used_chars = 0
    output: list[RecallResult] = []

    for r in results:
        if r.get("score", 0) < _MIN_RELEVANCE_SCORE:
            continue
        content = r.get("payload", {}).get("content", "")
        if not content:
            continue
        if used_chars + len(content) > budget_chars:
            break
        output.append(RecallResult(
            id=r["id"], content=content, score=r["score"], source="vector",
        ))
        used_chars += len(content)

    fact_texts = [r.content for r in output]
    try:
        entity_names = graph.extract_entities_from_text(fact_texts, user_id)
        seen: set[str] = set()
        for name in entity_names:
            for neighbor in graph.neighbors(name, user_id):
                line = f"[Graph] {neighbor['from']} --{neighbor['relation']}--> {neighbor['to']}"
                if line not in seen and used_chars + len(line) <= budget_chars:
                    seen.add(line)
                    output.append(RecallResult(
                        id="graph", content=line, score=0.0, source="graph",
                    ))
                    used_chars += len(line)
    except Exception:
        log.debug("Graph enrichment skipped", exc_info=True)

    log.info(
        "Recall: %d facts + %d graph lines (%.0f chars) for: %.60s...",
        sum(1 for r in output if r.source == "vector"),
        sum(1 for r in output if r.source == "graph"),
        used_chars, query,
    )
    return output
