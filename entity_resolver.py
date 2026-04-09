"""
Entity and edge resolution pipeline for the knowledge graph.

Sits between raw LLM-extracted triples and graph persistence. Ensures
entities are deduplicated (via exact + fuzzy + vector candidate retrieval,
then LLM-based final decision) and edges are lifecycle-managed (the LLM
decides SUPERSEDE / COEXIST / SKIP against existing edges).

Flow:
    raw triples  ->  resolve_and_persist()
        Phase 1: entity resolution  (find_candidates + LLM)
        Phase 2: relation normalization  (static synonym map)
        Phase 3: edge resolution  (existing-edge lookup + LLM)
        Phase 4: persist  (add / supersede / skip)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relation synonym map (deterministic, safe for small verb vocabulary)
# ---------------------------------------------------------------------------

RELATION_SYNONYMS: dict[str, str] = {
    # dating / relationship
    "is_dating": "has_partner",
    "dating": "has_partner",
    "has_boyfriend": "has_partner",
    "has_girlfriend": "has_partner",
    "in_relationship_with": "has_partner",
    # role
    "holds_role": "has_role",
    "works_as": "has_role",
    "role_is": "has_role",
    "serves_as": "has_role",
    "position_is": "has_role",
    # goal
    "targets": "has_goal",
    "pursues": "has_goal",
    "looking_for": "has_goal",
    "seeks": "has_goal",
    "aims_for": "has_goal",
    # interest
    "interested_in": "has_interest",
    "likes": "has_interest",
    "enjoys": "has_interest",
    "prefers": "has_interest",
    # employment
    "employed_at": "works_at",
    "employed_by": "works_at",
    "works_for": "works_at",
    "joined": "works_at",
    # education
    "studies_at": "attended",
    "graduated_from": "attended",
    "enrolled_at": "attended",
    # usage
    "utilizes": "uses",
    "built_with": "uses",
    "leverages": "uses",
    # creation
    "built": "created",
    "developed": "created",
    "authored": "created",
    "made": "created",
    # experience
    "suffers_from": "experiences",
    "has_condition": "experiences",
    "deals_with": "experiences",
    # knowledge
    "is_roommate_of": "lives_with",
    "roommate_of": "lives_with",
    "is_friend_of": "knows",
    "friend_of": "knows",
    "is_colleague_of": "works_with",
    "colleague_of": "works_with",
}


def normalize_relation(relation: str) -> str:
    """Map a relation to its canonical form via the synonym table."""
    return RELATION_SYNONYMS.get(relation.lower().strip(), relation.lower().strip())


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ResolvedTriple:
    subject: str
    subject_type: str
    relation: str
    obj: str
    object_type: str


@dataclass
class EdgeAction:
    """Describes what to do with a resolved triple."""
    action: str          # ADD, SUPERSEDE, COEXIST, SKIP
    triple: ResolvedTriple
    superseded_rel_id: str | None = None


@dataclass
class Candidate:
    entity_id: str
    name: str
    entity_type: str
    score: float
    method: str          # exact, fuzzy, vector


# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------

_VECTOR_SIMILARITY_THRESHOLD = 0.70
_FUZZY_TOKEN_THRESHOLD = 0.40


def _jaccard_tokens(a: str, b: str) -> float:
    """Word-level Jaccard similarity."""
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


async def find_candidates(
    entity_name: str,
    entity_type: str,
    user_id: str,
) -> list[Candidate]:
    """Retrieve candidate matches for an entity using exact + fuzzy + vector."""
    import knowledge_graph as kg

    name_lower = entity_name.strip().lower()
    candidates: dict[str, Candidate] = {}

    # --- Exact match ---
    for (nl, _etype, uid), eid in kg._entity_index.items():
        if uid == user_id and nl == name_lower:
            node = kg._graph.nodes.get(eid, {})
            candidates[eid] = Candidate(
                entity_id=eid,
                name=node.get("name", nl),
                entity_type=node.get("entity_type", _etype),
                score=1.0,
                method="exact",
            )

    # --- Fuzzy match (token overlap) ---
    all_names = kg.get_all_entity_names(user_id)
    for nl, display in all_names.items():
        if nl == name_lower:
            continue
        score = _jaccard_tokens(name_lower, nl)
        if score >= _FUZZY_TOKEN_THRESHOLD:
            key = (nl, user_id)
            for (idx_nl, _etype, uid), eid in kg._entity_index.items():
                if uid == user_id and idx_nl == nl and eid not in candidates:
                    node = kg._graph.nodes.get(eid, {})
                    candidates[eid] = Candidate(
                        entity_id=eid,
                        name=node.get("name", display),
                        entity_type=node.get("entity_type", ""),
                        score=score,
                        method="fuzzy",
                    )
                    break

    # --- Vector match (embedding similarity) ---
    try:
        from embeddings import embed_text
        import vector_store

        embedding = await embed_text(entity_name)
        results = await vector_store.search_entities(embedding, user_id=user_id, top_k=5)
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


# ---------------------------------------------------------------------------
# LLM resolution calls
# ---------------------------------------------------------------------------

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

If there are no existing edges, the action is ADD.

{edge_blocks}

Reply as strict JSON (no markdown fences):
{{"results": [{{"new_triple": "subject --relation--> object", "action": "ADD|SUPERSEDE|COEXIST|SKIP", "superseded_rel_id": "..." or null}}]}}
"""


async def _llm_call(prompt: str) -> str:
    """Make a small LLM call for resolution. Returns raw text response."""
    from models import get_provider, TextDelta

    provider = get_provider()
    response = ""
    async for event in provider.stream(
        system="You are a precise entity/edge resolution engine. Output only valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    ):
        if isinstance(event, TextDelta):
            response += event.text
    return response


async def _resolve_entities_via_llm(
    entity_candidate_map: dict[str, list[Candidate]],
) -> dict[str, str | None]:
    """Batch LLM call: for each entity with candidates, decide match or NULL.

    Returns {original_entity_name: matched_canonical_name or None}.
    """
    if not entity_candidate_map:
        return {}

    blocks: list[str] = []
    for entity_name, candidates in entity_candidate_map.items():
        cand_strs = ", ".join(
            f'"{c.name}" ({c.entity_type}, {c.method}={c.score:.2f})'
            for c in candidates
        )
        blocks.append(f'New entity: "{entity_name}"\nCandidates: [{cand_strs}]')

    prompt = _ENTITY_RESOLUTION_PROMPT.format(entity_blocks="\n\n".join(blocks))
    raw = await _llm_call(prompt)

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        results = parsed.get("results", [])
    except (json.JSONDecodeError, ValueError):
        log.warning("Entity resolution LLM returned unparseable response: %s", raw[:200])
        return {name: None for name in entity_candidate_map}

    mapping: dict[str, str | None] = {}
    for r in results:
        new_name = r.get("new_entity", "")
        match = r.get("match")
        if match and match.lower() != "null":
            mapping[new_name] = match
        else:
            mapping[new_name] = None

    for name in entity_candidate_map:
        if name not in mapping:
            mapping[name] = None

    return mapping


async def _resolve_edges_via_llm(
    edge_blocks_data: list[dict],
) -> list[dict]:
    """Batch LLM call for edge resolution.

    Each item in edge_blocks_data:
        {"triple_str": "A --rel--> B", "subject": ..., "relation": ..., "obj": ...,
         "existing": [{"rel_id": ..., "relation": ...}]}
    """
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
    raw = await _llm_call(prompt)

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        results = parsed.get("results", [])
    except (json.JSONDecodeError, ValueError):
        log.warning("Edge resolution LLM returned unparseable response: %s", raw[:200])
        return [{"action": "ADD", "superseded_rel_id": None} for _ in edge_blocks_data]

    return results


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def resolve_and_persist(
    raw_triples: list[dict],
    user_id: str,
    source_session_id: str | None = None,
) -> int:
    """Full resolution pipeline: entity dedup -> relation normalize -> edge resolve -> persist.

    Returns the number of triples persisted (ADD + COEXIST + SUPERSEDE).
    """
    import knowledge_graph as kg

    if not raw_triples:
        return 0

    # ------------------------------------------------------------------
    # Phase 1: Entity Resolution
    # ------------------------------------------------------------------
    unique_entities: dict[str, str] = {}
    for t in raw_triples:
        subj = t.get("subject", "").strip()
        obj = t.get("object", "").strip()
        subj_type = t.get("subject_type", "project").strip()
        obj_type = t.get("object_type", "project").strip()
        if subj:
            unique_entities.setdefault(subj, subj_type)
        if obj:
            unique_entities.setdefault(obj, obj_type)

    entity_candidate_map: dict[str, list[Candidate]] = {}
    entity_name_mapping: dict[str, str] = {}

    for name, etype in unique_entities.items():
        candidates = await find_candidates(name, etype, user_id)
        non_exact = [c for c in candidates if c.method != "exact"]
        if non_exact:
            entity_candidate_map[name] = non_exact
        elif candidates:
            entity_name_mapping[name] = candidates[0].name

    if entity_candidate_map:
        llm_mapping = await _resolve_entities_via_llm(entity_candidate_map)
        for name, matched in llm_mapping.items():
            if matched:
                entity_name_mapping[name] = matched
                log.info("Entity resolved: '%s' -> '%s'", name, matched)
            else:
                log.debug("Entity is new: '%s'", name)

    # ------------------------------------------------------------------
    # Phase 2: Relation Normalization + build resolved triples
    # ------------------------------------------------------------------
    resolved: list[ResolvedTriple] = []
    for t in raw_triples:
        subj = t.get("subject", "").strip()
        obj = t.get("object", "").strip()
        rel = t.get("relation", "").strip()
        subj_type = t.get("subject_type", "project").strip()
        obj_type = t.get("object_type", "project").strip()

        if not subj or not rel or not obj:
            continue

        resolved_subj = entity_name_mapping.get(subj, subj)
        resolved_obj = entity_name_mapping.get(obj, obj)
        resolved_rel = normalize_relation(rel)

        resolved.append(ResolvedTriple(
            subject=resolved_subj,
            subject_type=subj_type,
            relation=resolved_rel,
            obj=resolved_obj,
            object_type=obj_type,
        ))

    if not resolved:
        return 0

    # ------------------------------------------------------------------
    # Phase 3: Edge Resolution
    # ------------------------------------------------------------------
    edge_actions: list[EdgeAction] = []
    edges_needing_llm: list[dict] = []
    edges_needing_llm_indices: list[int] = []

    for i, rt in enumerate(resolved):
        existing = kg.get_edges_between(rt.subject, rt.obj, user_id)
        if not existing:
            edge_actions.append(EdgeAction(action="ADD", triple=rt))
        else:
            exact_rel_match = any(e["relation"] == rt.relation for e in existing)
            if exact_rel_match:
                edge_actions.append(EdgeAction(action="SKIP", triple=rt))
                log.debug("Edge SKIP (exact relation match): %s --%s--> %s",
                          rt.subject, rt.relation, rt.obj)
            else:
                edge_actions.append(EdgeAction(action="PENDING", triple=rt))
                edges_needing_llm.append({
                    "triple_str": f"{rt.subject} --{rt.relation}--> {rt.obj}",
                    "subject": rt.subject,
                    "relation": rt.relation,
                    "obj": rt.obj,
                    "existing": existing,
                })
                edges_needing_llm_indices.append(i)

    if edges_needing_llm:
        llm_results = await _resolve_edges_via_llm(edges_needing_llm)
        for idx, result in zip(edges_needing_llm_indices, llm_results):
            action = result.get("action", "COEXIST").upper()
            superseded = result.get("superseded_rel_id")
            if action in ("SUPERSEDE", "COEXIST", "SKIP", "ADD"):
                edge_actions[idx] = EdgeAction(
                    action=action,
                    triple=edge_actions[idx].triple,
                    superseded_rel_id=superseded,
                )
            else:
                edge_actions[idx] = EdgeAction(
                    action="COEXIST",
                    triple=edge_actions[idx].triple,
                )

    # ------------------------------------------------------------------
    # Phase 4: Persist
    # ------------------------------------------------------------------
    import memory

    persisted = 0
    for ea in edge_actions:
        rt = ea.triple
        if ea.action == "SKIP":
            log.debug("SKIP: %s --%s--> %s", rt.subject, rt.relation, rt.obj)
            continue

        if ea.action == "SUPERSEDE" and ea.superseded_rel_id:
            ok = await memory.deactivate_relationship(ea.superseded_rel_id)
            if ok:
                kg.remove_edge_by_id(ea.superseded_rel_id)
                log.info("SUPERSEDE: deactivated edge %s", ea.superseded_rel_id)

        await kg.add_triple(
            subject=rt.subject,
            subject_type=rt.subject_type,
            relation=rt.relation,
            obj=rt.obj,
            object_type=rt.object_type,
            user_id=user_id,
            source_session_id=source_session_id,
        )
        persisted += 1
        log.debug("%s: %s --%s--> %s", ea.action, rt.subject, rt.relation, rt.obj)

    log.info(
        "Resolution complete: %d raw -> %d persisted (%d skipped, %d superseded)",
        len(raw_triples),
        persisted,
        sum(1 for ea in edge_actions if ea.action == "SKIP"),
        sum(1 for ea in edge_actions if ea.action == "SUPERSEDE"),
    )
    return persisted
