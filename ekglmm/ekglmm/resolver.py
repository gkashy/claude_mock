"""Entity and edge resolution pipeline.

Sits between raw LLM-extracted triples and graph persistence:
  Phase 1: entity resolution  (exact + fuzzy + vector + LLM)
  Phase 2: relation normalization  (configurable synonym map)
  Phase 3: edge resolution  (existing-edge lookup + LLM)
  Phase 4: persist  (add / supersede / skip)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ._types import Candidate, EdgeAction, ResolvedTriple

if TYPE_CHECKING:
    from .graph import GraphManager
    from .protocols import (
        EmbeddingProvider,
        LLMProvider,
        StorageBackend,
        VectorStore,
    )

log = logging.getLogger(__name__)

DEFAULT_RELATION_SYNONYMS: dict[str, str] = {
    "is_dating": "has_partner", "dating": "has_partner",
    "has_boyfriend": "has_partner", "has_girlfriend": "has_partner",
    "in_relationship_with": "has_partner",
    "holds_role": "has_role", "works_as": "has_role", "role_is": "has_role",
    "serves_as": "has_role", "position_is": "has_role",
    "targets": "has_goal", "pursues": "has_goal", "looking_for": "has_goal",
    "seeks": "has_goal", "aims_for": "has_goal",
    "interested_in": "has_interest", "likes": "has_interest",
    "enjoys": "has_interest", "prefers": "has_interest",
    "employed_at": "works_at", "employed_by": "works_at",
    "works_for": "works_at", "joined": "works_at",
    "studies_at": "attended", "graduated_from": "attended",
    "enrolled_at": "attended",
    "utilizes": "uses", "built_with": "uses", "leverages": "uses",
    "built": "created", "developed": "created", "authored": "created",
    "made": "created",
    "suffers_from": "experiences", "has_condition": "experiences",
    "deals_with": "experiences",
    "is_roommate_of": "lives_with", "roommate_of": "lives_with",
    "is_friend_of": "knows", "friend_of": "knows",
    "is_colleague_of": "works_with", "colleague_of": "works_with",
}

_VECTOR_SIMILARITY_THRESHOLD = 0.70
_FUZZY_TOKEN_THRESHOLD = 0.40

_ENTITY_RESOLUTION_PROMPT = """\
You are an entity resolution engine. Given new entities extracted from a conversation and candidate matches from the existing knowledge graph, decide whether each new entity is the same as an existing candidate or genuinely new.

For each new entity below, reply with the name of the matching candidate (exact string) or NULL if it is genuinely new.

IMPORTANT: Only match if the entities refer to the same real-world thing. Different things with similar names should NOT be matched.

{entity_blocks}

Reply as strict JSON (no markdown fences):
{{"results": [{{"new_entity": "...", "match": "..." or null}}]}}
"""

_EDGE_RESOLUTION_PROMPT = """\
You are a knowledge graph edge resolution engine. For each new triple, existing edges between the same entity pair are shown. Decide how the new triple relates to each existing edge.

Actions:
- SUPERSEDE: the new triple replaces the existing edge (e.g. ex_girlfriend replaces has_partner)
- COEXIST: both are valid simultaneously (e.g. works_at and leads_team_at)
- SKIP: the new triple is redundant with an existing edge (same meaning, already captured)

{edge_blocks}

Reply as strict JSON (no markdown fences):
{{"results": [{{"new_triple": "subject --relation--> object", "action": "ADD|SUPERSEDE|COEXIST|SKIP", "superseded_rel_id": "..." or null}}]}}
"""


def _jaccard_tokens(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


async def find_candidates(
    entity_name: str,
    entity_type: str,
    user_id: str,
    graph: GraphManager,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> list[Candidate]:
    """Retrieve candidate matches using exact + fuzzy + vector."""
    name_lower = entity_name.strip().lower()
    candidates: dict[str, Candidate] = {}

    for (nl, _etype, uid), eid in graph._entity_index.items():
        if uid == user_id and nl == name_lower:
            node = graph._graph.nodes.get(eid, {})
            candidates[eid] = Candidate(
                entity_id=eid,
                name=node.get("name", nl),
                entity_type=node.get("entity_type", _etype),
                score=1.0,
                method="exact",
            )

    all_names = graph.all_entity_names(user_id)
    for nl, display in all_names.items():
        if nl == name_lower:
            continue
        score = _jaccard_tokens(name_lower, nl)
        if score >= _FUZZY_TOKEN_THRESHOLD:
            for (idx_nl, _etype, uid), eid in graph._entity_index.items():
                if uid == user_id and idx_nl == nl and eid not in candidates:
                    node = graph._graph.nodes.get(eid, {})
                    candidates[eid] = Candidate(
                        entity_id=eid,
                        name=node.get("name", display),
                        entity_type=node.get("entity_type", ""),
                        score=score,
                        method="fuzzy",
                    )
                    break

    if embeddings and vectors:
        try:
            embedding = await embeddings.embed_text(entity_name)
            results = await vectors.search_entities(embedding, user_id=user_id, top_k=5)
            for r in results:
                if r["score"] >= _VECTOR_SIMILARITY_THRESHOLD and r["id"] not in candidates:
                    candidates[r["id"]] = Candidate(
                        entity_id=r["id"],
                        name=r["name"],
                        entity_type=r.get("entity_type", ""),
                        score=r["score"],
                        method="vector",
                    )
        except Exception:
            log.debug("Vector candidate search failed", exc_info=True)

    return list(candidates.values())


async def _resolve_entities_via_llm(
    entity_candidate_map: dict[str, list[Candidate]],
    llm: LLMProvider,
) -> dict[str, str | None]:
    if not entity_candidate_map:
        return {}

    blocks: list[str] = []
    for entity_name, cands in entity_candidate_map.items():
        cand_strs = ", ".join(
            f'"{c.name}" ({c.entity_type}, {c.method}={c.score:.2f})'
            for c in cands
        )
        blocks.append(f'New entity: "{entity_name}"\nCandidates: [{cand_strs}]')

    prompt = _ENTITY_RESOLUTION_PROMPT.format(entity_blocks="\n\n".join(blocks))
    raw = await llm.complete(
        "You are a precise entity/edge resolution engine. Output only valid JSON.",
        [{"role": "user", "content": prompt}],
    )

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        results = parsed.get("results", [])
    except (json.JSONDecodeError, ValueError):
        log.warning("Entity resolution LLM unparseable: %s", raw[:200])
        return {name: None for name in entity_candidate_map}

    mapping: dict[str, str | None] = {}
    for r in results:
        match = r.get("match")
        mapping[r.get("new_entity", "")] = match if match and match.lower() != "null" else None

    for name in entity_candidate_map:
        mapping.setdefault(name, None)
    return mapping


async def _resolve_edges_via_llm(
    edge_blocks_data: list[dict],
    llm: LLMProvider,
) -> list[dict]:
    if not edge_blocks_data:
        return []

    blocks: list[str] = []
    for item in edge_blocks_data:
        existing_strs = "\n  ".join(
            f'[{e["rel_id"]}] --{e["relation"]}-->'
            for e in item["existing"]
        )
        blocks.append(
            f'New triple: {item["triple_str"]}\n'
            f'Existing edges between same pair:\n  {existing_strs}'
        )

    prompt = _EDGE_RESOLUTION_PROMPT.format(edge_blocks="\n\n".join(blocks))
    raw = await llm.complete(
        "You are a precise entity/edge resolution engine. Output only valid JSON.",
        [{"role": "user", "content": prompt}],
    )

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        return parsed.get("results", [])
    except (json.JSONDecodeError, ValueError):
        log.warning("Edge resolution LLM unparseable: %s", raw[:200])
        return [{"action": "ADD", "superseded_rel_id": None} for _ in edge_blocks_data]


def normalize_relation(
    relation: str,
    synonyms: dict[str, str] | None = None,
) -> str:
    table = synonyms if synonyms is not None else DEFAULT_RELATION_SYNONYMS
    return table.get(relation.lower().strip(), relation.lower().strip())


async def resolve_and_persist(
    raw_triples: list[dict],
    user_id: str,
    graph: GraphManager,
    storage: StorageBackend,
    llm: LLMProvider,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
    relation_synonyms: dict[str, str] | None = None,
    source_session_id: str | None = None,
) -> int:
    """Full resolution pipeline. Returns count of persisted triples."""
    if not raw_triples:
        return 0

    # Phase 1: Entity Resolution
    unique_entities: dict[str, str] = {}
    for t in raw_triples:
        subj = t.get("subject", "").strip()
        obj = t.get("object", "").strip()
        if subj:
            unique_entities.setdefault(subj, t.get("subject_type", "project").strip())
        if obj:
            unique_entities.setdefault(obj, t.get("object_type", "project").strip())

    entity_candidate_map: dict[str, list[Candidate]] = {}
    entity_name_mapping: dict[str, str] = {}

    for name, etype in unique_entities.items():
        candidates = await find_candidates(name, etype, user_id, graph, embeddings, vectors)
        non_exact = [c for c in candidates if c.method != "exact"]
        if non_exact:
            entity_candidate_map[name] = non_exact
        elif candidates:
            entity_name_mapping[name] = candidates[0].name

    if entity_candidate_map:
        llm_mapping = await _resolve_entities_via_llm(entity_candidate_map, llm)
        for name, matched in llm_mapping.items():
            if matched:
                entity_name_mapping[name] = matched
                log.info("Entity resolved: '%s' -> '%s'", name, matched)

    # Phase 2: Relation Normalization
    resolved: list[ResolvedTriple] = []
    for t in raw_triples:
        subj = t.get("subject", "").strip()
        obj = t.get("object", "").strip()
        rel = t.get("relation", "").strip()
        if not subj or not rel or not obj:
            continue
        resolved.append(ResolvedTriple(
            subject=entity_name_mapping.get(subj, subj),
            subject_type=t.get("subject_type", "project").strip(),
            relation=normalize_relation(rel, relation_synonyms),
            obj=entity_name_mapping.get(obj, obj),
            object_type=t.get("object_type", "project").strip(),
        ))

    if not resolved:
        return 0

    # Phase 3: Edge Resolution
    edge_actions: list[EdgeAction] = []
    edges_needing_llm: list[dict] = []
    edges_needing_llm_indices: list[int] = []

    for i, rt in enumerate(resolved):
        existing = graph.edges_between(rt.subject, rt.obj, user_id)
        if not existing:
            edge_actions.append(EdgeAction(action="ADD", triple=rt))
        else:
            if any(e["relation"] == rt.relation for e in existing):
                edge_actions.append(EdgeAction(action="SKIP", triple=rt))
            else:
                edge_actions.append(EdgeAction(action="PENDING", triple=rt))
                edges_needing_llm.append({
                    "triple_str": f"{rt.subject} --{rt.relation}--> {rt.obj}",
                    "existing": existing,
                })
                edges_needing_llm_indices.append(i)

    if edges_needing_llm:
        llm_results = await _resolve_edges_via_llm(edges_needing_llm, llm)
        for idx, result in zip(edges_needing_llm_indices, llm_results):
            action = result.get("action", "COEXIST").upper()
            superseded = result.get("superseded_rel_id")
            if action in ("SUPERSEDE", "COEXIST", "SKIP", "ADD"):
                edge_actions[idx] = EdgeAction(
                    action=action, triple=edge_actions[idx].triple,
                    superseded_rel_id=superseded,
                )
            else:
                edge_actions[idx] = EdgeAction(
                    action="COEXIST", triple=edge_actions[idx].triple,
                )

    # Phase 4: Persist
    persisted = 0
    for ea in edge_actions:
        rt = ea.triple
        if ea.action == "SKIP":
            continue
        if ea.action == "SUPERSEDE" and ea.superseded_rel_id:
            ok = await storage.deactivate_relationship(ea.superseded_rel_id)
            if ok:
                graph.remove_edge_by_id(ea.superseded_rel_id)

        await graph.add_triple(
            subject=rt.subject, subject_type=rt.subject_type,
            relation=rt.relation, obj=rt.obj, object_type=rt.object_type,
            user_id=user_id, storage=storage,
            embeddings=embeddings, vectors=vectors,
            source_session_id=source_session_id,
        )
        persisted += 1

    log.info(
        "Resolution: %d raw -> %d persisted (%d skipped, %d superseded)",
        len(raw_triples), persisted,
        sum(1 for ea in edge_actions if ea.action == "SKIP"),
        sum(1 for ea in edge_actions if ea.action == "SUPERSEDE"),
    )
    return persisted
