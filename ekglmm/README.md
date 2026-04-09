# EKGLMM -- Efficient Knowledge Graph and Long-term Memory Manager

Built on [graphify (v3)](https://github.com/safishamsi/graphify/tree/v3) for graph visualization, community clustering, and NetworkX graph infrastructure. EKGLMM adds persistent memory, LLM-powered extraction, entity/edge resolution, and graph-enriched retrieval on top.

## Installation

```bash
pip install ekglmm                     # core + graphify
pip install ekglmm[postgres]           # + asyncpg + sqlalchemy
pip install ekglmm[qdrant]             # + qdrant-client
pip install ekglmm[all]                # everything
```

## Quick Start

```python
import asyncio
from ekglmm import EKGLMM
from ekglmm.storage import PostgresStorage
from ekglmm.vectors import QdrantVectors
from ekglmm.embeddings import OpenAIEmbeddings
from ekglmm.llm import AnthropicLLM

async def main():
    mem = EKGLMM(
        storage=PostgresStorage("postgresql+asyncpg://user:pass@localhost/mydb"),
        vectors=QdrantVectors("localhost", 6333),
        embeddings=OpenAIEmbeddings(api_key="sk-..."),
        llm=AnthropicLLM(api_key="sk-ant-..."),
        user_name="Gaurav Kashyap",
    )
    await mem.init()

    # Store a fact (auto-deduplicates and extracts graph triples)
    fact_id, action = await mem.remember(
        "Gaurav targets a backend role at Google",
        user_id="gaurav",
    )
    print(f"{action}: {fact_id}")

    # Semantic recall with graph enrichment
    results = await mem.recall("career goals", user_id="gaurav")
    for r in results:
        print(f"  [{r.source}] {r.content} (score={r.score:.2f})")

    # Full conversation extraction
    messages = [
        {"role": "user", "content": "I just started learning Rust"},
        {"role": "assistant", "content": "That's great! Rust is excellent for systems programming."},
        {"role": "user", "content": "Yeah I want to build a CLI tool with it"},
    ]
    applied = await mem.extract(messages, user_id="gaurav")
    print(f"Extraction applied: {applied}")

    # Direct graph access
    neighbors = mem.graph.neighbors("Gaurav Kashyap", user_id="gaurav")
    stats = mem.graph.stats(user_id="gaurav")
    html = mem.graph.visualize(user_id="gaurav")        # interactive HTML via graphify
    communities = mem.graph.cluster(user_id="gaurav")    # Leiden community detection

    # Forget a fact
    await mem.forget(fact_id)

asyncio.run(main())
```

## API Reference

### `EKGLMM`

The main class. Requires four backend implementations injected at init.

| Parameter | Type | Description |
|---|---|---|
| `storage` | `StorageBackend` | Persistent store (facts, entities, relationships) |
| `vectors` | `VectorStore` | Vector index for semantic search |
| `embeddings` | `EmbeddingProvider` | Text embedding provider |
| `llm` | `LLMProvider` | Text completion for extraction and resolution |
| `user_name` | `str` (optional) | Canonical user name for triple extraction |
| `entity_types` | `list[str]` (optional) | Allowed entity types |
| `relation_synonyms` | `dict[str, str]` (optional) | Custom relation synonym map |

#### Methods

| Method | Signature | Description |
|---|---|---|
| `init()` | `async def init(user_id="default")` | Create tables, collections, load graph |
| `remember()` | `async def remember(content, user_id, source) -> (fact_id, action)` | Store fact with dedup + auto-extract triples |
| `recall()` | `async def recall(query, user_id, top_k) -> list[RecallResult]` | Semantic search + graph enrichment |
| `extract()` | `async def extract(messages, user_id, session_id) -> list[str]` | Full extraction from a conversation |
| `forget()` | `async def forget(fact_id) -> bool` | Deactivate a fact |
| `graph` | `@property -> GraphManager` | Direct graph access |

### `GraphManager`

Instance-based knowledge graph wrapping NetworkX + graphify.

| Method | Description |
|---|---|
| `neighbors(entity_name, user_id)` | First-degree neighbors |
| `edges_between(a, b, user_id)` | All edges between two entities |
| `stats(user_id)` | Node/edge counts, top entities |
| `export_json(user_id)` | Node-link JSON export |
| `visualize(user_id)` | Interactive HTML via graphify |
| `cluster(user_id)` | Leiden community detection via graphify |

## Protocols

Implement these to bring your own backends:

```python
class StorageBackend(Protocol):
    async def init_tables(self) -> None: ...
    async def save_fact(self, user_id, content, source_session_id, embedding) -> str: ...
    async def load_facts(self, user_id) -> list[dict]: ...
    async def update_fact(self, fact_id, content, embedding) -> bool: ...
    async def deactivate_fact(self, fact_id) -> bool: ...
    async def upsert_entity(self, name, entity_type, user_id, ...) -> tuple[str, bool]: ...
    async def list_entities(self, user_id) -> list[dict]: ...
    async def upsert_relationship(self, source_id, target_id, relation, user_id, ...) -> tuple[str, bool]: ...
    async def deactivate_relationship(self, rel_id) -> bool: ...
    async def load_graph_data(self, user_id) -> tuple[list[dict], list[dict]]: ...

class VectorStore(Protocol):
    async def ensure_collections(self, dimension) -> None: ...
    async def upsert_fact(self, fact_id, embedding, payload) -> None: ...
    async def search_facts(self, embedding, user_id, top_k) -> list[dict]: ...
    async def delete_fact(self, fact_id) -> None: ...
    async def upsert_entity(self, entity_id, embedding, payload) -> None: ...
    async def search_entities(self, embedding, user_id, top_k) -> list[dict]: ...

class EmbeddingProvider(Protocol):
    dimension: int
    async def embed_text(self, text) -> list[float]: ...
    async def embed_batch(self, texts) -> list[list[float]]: ...

class LLMProvider(Protocol):
    async def complete(self, system, messages) -> str: ...
```

## Omnio Example (Fitness App)

```python
mem = EKGLMM(
    storage=PostgresStorage("postgresql+asyncpg://user:pass@localhost/omnio"),
    vectors=QdrantVectors("localhost", 6333),
    embeddings=OpenAIEmbeddings(api_key="sk-..."),
    llm=AnthropicLLM(api_key="sk-ant-..."),
    user_name="default",
    entity_types=["person", "exercise", "food", "goal", "injury", "metric"],
    relation_synonyms={"does": "performs", "eats": "consumes"},
)
await mem.init()

# User tells the app about their preferences
await mem.remember("I prefer morning workouts", user_id="user123")
await mem.remember("I'm allergic to peanuts", user_id="user123")

# Later, retrieve context for personalization
results = await mem.recall("meal plan for today", user_id="user123")
# Results include: the allergy fact + any graph neighbors
# (e.g., [Graph] User123 --allergic_to--> peanuts)

# Full chat extraction after a coaching session
await mem.extract(chat_messages, user_id="user123", session_id="session-abc")
```

## Architecture

```
EKGLMM (main class)
  |
  |-- GraphManager (NetworkX + graphify)
  |     |-- add_triple / remove_edge_by_id
  |     |-- neighbors / edges_between / stats
  |     |-- visualize (graphify to_html)
  |     |-- cluster (graphify Leiden)
  |
  |-- extraction.py (LLM-powered fact + triple extraction)
  |     |-- graph-aware CRUD (sees existing edges, can DELETE them)
  |     |-- configurable entity types and relation synonyms
  |
  |-- resolver.py (entity + edge resolution pipeline)
  |     |-- exact + fuzzy + vector candidate retrieval
  |     |-- LLM-based entity matching
  |     |-- LLM-based edge lifecycle (SUPERSEDE / COEXIST / SKIP)
  |
  |-- enrichment.py (graph-enriched semantic recall)
  |     |-- vector search + neighbor expansion + merge + rank
  |
  |-- Protocols (pluggable backends)
        |-- StorageBackend  -> PostgresStorage (default)
        |-- VectorStore     -> QdrantVectors (default)
        |-- EmbeddingProvider -> OpenAIEmbeddings (default)
        |-- LLMProvider     -> AnthropicLLM / OpenAICompatLLM (default)
```

## License

MIT
