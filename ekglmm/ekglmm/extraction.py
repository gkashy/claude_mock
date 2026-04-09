"""LLM-powered fact and triple extraction with graph-aware CRUD.

The extraction prompt sees both existing facts AND existing graph edges,
enabling full CRUD on both data stores in a single LLM call.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .resolver import find_candidates, resolve_and_persist

if TYPE_CHECKING:
    from .graph import GraphManager
    from .protocols import (
        EmbeddingProvider,
        LLMProvider,
        StorageBackend,
        VectorStore,
    )

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
You are a precise memory manager for an AI assistant.

## Primary user
{user_name_block}

## Existing facts
{existing_facts}

## Existing graph edges
{existing_graph_edges}

## New conversation transcript
{transcript}

## Your task
Compare the conversation against the existing facts AND graph edges, then produce a JSON object with three keys:
1. "actions" -- fact-level changes
2. "triples" -- new structured entity-relationship-entity triples to ADD to the knowledge graph
3. "triple_deletions" -- existing graph edges to REMOVE (by ID from the list above)

### actions (each action is one of ADD / UPDATE / DELETE)
- **ADD**: A new fact not already captured. Content must be a single concise sentence.
- **UPDATE**: An existing fact whose content is now outdated or incomplete. Provide the fact `id` and the corrected full content.
- **DELETE**: An existing fact that is now wrong, redundant, or explicitly contradicted. Provide the fact `id` and a brief reason.

### triples (new knowledge graph edges to ADD)
Extract named entities and their relationships as triples. Each triple has:
- subject / subject_type: the source entity and its type
- relation: a short verb phrase (snake_case)
- object / object_type: the target entity and its type

Entity types: {entity_types}

Only extract triples where BOTH entities are named (not pronouns or vague references).

IMPORTANT -- naming rule: when referring to the primary user in any triple, ALWAYS use their
exact full name as given above (not first name only, not "user", not "the user").

IMPORTANT -- graph connectivity rule: when a third-party person appears in the conversation,
always extract at least one triple connecting the primary user to that person.

### triple_deletions (existing graph edges to REMOVE)
Review the existing graph edges listed above. Delete an edge when:
- The conversation **explicitly contradicts** it
- The conversation **negates** it
- New information **supersedes** it
- It is **invalidated by cascading implication**
If unsure whether an edge is still valid, leave it alone.
Each deletion needs the edge `id` and a brief reason.

### Rules for actions
1. Only ADD facts that are genuinely new and worth remembering long-term.
2. Prefer UPDATE over ADD when a fact is a refinement of an existing one.
3. DELETE only when a fact is clearly wrong or superseded.
4. If nothing changed, return empty arrays for all three keys.
5. Do NOT re-add facts that already exist with equivalent meaning.

### Output format (strict JSON, no markdown fences)
{{"actions": [
  {{"action": "ADD", "content": "..."}},
  {{"action": "UPDATE", "id": "abc123", "content": "updated content here"}},
  {{"action": "DELETE", "id": "def456", "reason": "superseded by ..."}}
],
"triples": [
  {{"subject": "...", "subject_type": "person", "relation": "targets", "object": "...", "object_type": "organization"}}
],
"triple_deletions": [
  {{"id": "rel-abc", "reason": "user is allergic to chicken, does not like it"}}
]}}
"""


def _build_transcript(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text = " ".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)
        if text.strip():
            lines.append(f"{role}: {text[:500]}")
    return "\n".join(lines)


def _parse_extraction_response(raw: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse LLM response into (actions, triples, triple_deletions)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end])
            except json.JSONDecodeError:
                return [], [], []
        else:
            return [], [], []

    if isinstance(parsed, dict):
        actions = parsed.get("actions", [])
        triples = parsed.get("triples", [])
        triple_dels = parsed.get("triple_deletions", [])
        return (
            actions if isinstance(actions, list) else [],
            triples if isinstance(triples, list) else [],
            triple_dels if isinstance(triple_dels, list) else [],
        )
    if isinstance(parsed, list):
        return parsed, [], []
    return [], [], []


async def _retrieve_relevant_edges(
    transcript: str,
    existing_facts: list[dict],
    user_id: str,
    graph: GraphManager,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> str:
    """Retrieve graph edges relevant to the transcript for extraction context."""
    fact_texts = [f["content"] for f in existing_facts if f.get("content")]
    texts = transcript.split("\n") + fact_texts

    mentioned = graph.extract_entities_from_text(texts, user_id)
    if not mentioned:
        return "(none)"

    expanded_names: set[str] = set(mentioned)

    if embeddings and vectors:
        try:
            for name in mentioned:
                candidates = await find_candidates(
                    name, "", user_id, graph, embeddings, vectors,
                )
                for c in candidates:
                    if c.method in ("fuzzy", "vector") and c.score >= 0.5:
                        expanded_names.add(c.name)
        except Exception:
            log.debug("Edge retrieval expansion failed", exc_info=True)

    seen_ids: set[str] = set()
    edges: list[dict] = []
    for name in expanded_names:
        for edge in graph.neighbors(name, user_id):
            rid = edge.get("rel_id", "")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                edges.append(edge)

    if not edges:
        return "(none)"

    return "\n".join(
        f"- [{e['rel_id']}] {e['from']} --{e['relation']}--> {e['to']}"
        for e in edges
    )


async def extract_from_messages(
    messages: list[dict],
    user_id: str,
    graph: GraphManager,
    storage: StorageBackend,
    llm: LLMProvider,
    embeddings: EmbeddingProvider,
    vectors: VectorStore,
    user_name: str | None = None,
    entity_types: list[str] | None = None,
    relation_synonyms: dict[str, str] | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Full extraction pipeline: facts + triples + triple_deletions."""
    transcript = _build_transcript(messages)
    if len(transcript) < 50:
        return []

    existing = await storage.load_facts(user_id)
    facts_block = "\n".join(
        f"- [{f['id']}] {f['content']}" for f in existing
    ) if existing else "(none)"

    user_name_block = (
        f"The primary user's canonical full name is: **{user_name}**. "
        "Always use this exact name in triples."
    ) if user_name else "(unknown)"

    types_str = ", ".join(entity_types) if entity_types else (
        "person, organization, project, technology, goal, constraint, event, preference"
    )

    edges_block = await _retrieve_relevant_edges(
        transcript, existing, user_id, graph, embeddings, vectors,
    )

    prompt_text = _EXTRACTION_PROMPT.format(
        user_name_block=user_name_block,
        existing_facts=facts_block,
        existing_graph_edges=edges_block,
        transcript=transcript,
        entity_types=types_str,
    )

    response = await llm.complete(
        "You are a precise memory manager. Output only valid JSON.",
        [{"role": "user", "content": prompt_text}],
    )

    actions, triples, triple_deletions = _parse_extraction_response(response)
    if not actions and not triples and not triple_deletions:
        return []

    applied: list[str] = []

    # Fact-level CRUD
    for action in actions:
        act = action.get("action", "").upper()
        content = action.get("content", "").strip()
        fact_id = action.get("id", "").strip()

        if act == "ADD" and content:
            emb = await embeddings.embed_text(content)
            fid = await storage.save_fact(user_id, content, session_id, emb)
            await vectors.upsert_fact(fid, emb, {"user_id": user_id, "content": content})
            applied.append(f"ADD: {content}")

        elif act == "UPDATE" and fact_id and content:
            emb = await embeddings.embed_text(content)
            ok = await storage.update_fact(fact_id, content, emb)
            if ok:
                await vectors.upsert_fact(fact_id, emb, {"user_id": user_id, "content": content})
                applied.append(f"UPDATE({fact_id}): {content}")

        elif act == "DELETE" and fact_id:
            reason = action.get("reason", "")
            ok = await storage.deactivate_fact(fact_id)
            if ok:
                await vectors.delete_fact(fact_id)
                applied.append(f"DELETE({fact_id}): {reason}")

    # Triple ADD via resolver
    if triples:
        try:
            count = await resolve_and_persist(
                triples, user_id, graph, storage, llm,
                embeddings, vectors, relation_synonyms,
                source_session_id=session_id,
            )
            applied.append(f"TRIPLES({count}): graph updated")
        except Exception:
            log.warning("Triple resolution failed", exc_info=True)

    # Triple deletions
    if triple_deletions:
        del_count = 0
        for td in triple_deletions:
            rel_id = td.get("id", "").strip()
            if not rel_id:
                continue
            try:
                ok = await storage.deactivate_relationship(rel_id)
                if ok:
                    graph.remove_edge_by_id(rel_id)
                    del_count += 1
            except Exception:
                log.warning("Triple DELETE error for %s", rel_id, exc_info=True)
        if del_count:
            applied.append(f"TRIPLE_DELETIONS({del_count}): edges removed")

    return applied
