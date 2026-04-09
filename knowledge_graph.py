"""
In-memory knowledge graph backed by Postgres persistence.

The graph is a NetworkX DiGraph loaded at startup from the kg_entities and
kg_relationships tables. All writes go to Postgres first, then update the
in-memory graph, so the two stay in sync without a full reload.

graphify is used for clustering, analysis, and export. The core entity/
relationship CRUD delegates to memory.py so there is a single source of truth
for ORM interactions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import networkx as nx

log = logging.getLogger(__name__)

# Matches all common Unicode emoji ranges
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Remove emoji characters from a string and clean up leftover whitespace."""
    return _EMOJI_RE.sub("", text).strip()

# The in-memory graph. Keyed by entity id; node attributes carry name,
# entity_type, and other metadata. Edges carry relation and properties.
_graph: nx.DiGraph = nx.DiGraph()

# Secondary index: (name_lower, entity_type, user_id) -> entity_id
# Allows O(1) lookups when upserting from triple extraction.
_entity_index: dict[tuple[str, str, str], str] = {}

# Set of all known entity name_lower values per user, for fast text matching.
_entity_names: dict[str, set[str]] = {}  # user_id -> {name_lower, ...}

VALID_ENTITY_TYPES = frozenset({
    "person",
    "organization",
    "project",
    "technology",
    "goal",
    "constraint",
    "event",
    "preference",
})


# ---------------------------------------------------------------------------
# Init and rebuild
# ---------------------------------------------------------------------------

async def init_graph(user_id: str = "default") -> None:
    """Load all persisted entities and relationships into the in-memory graph.

    Called once at app startup (and can be called again to force a full
    rebuild, e.g. after a bulk import).
    """
    import memory

    global _graph, _entity_index, _entity_names

    _graph = nx.DiGraph()
    _entity_index = {}
    _entity_names[user_id] = set()

    entities, relationships = await memory.load_graph_data(user_id)

    for e in entities:
        _add_node(e)

    for r in relationships:
        _add_edge(r)

    log.info(
        "Knowledge graph loaded: %d nodes, %d edges for user=%s",
        _graph.number_of_nodes(),
        _graph.number_of_edges(),
        user_id,
    )

    _maybe_cluster()


def _add_node(entity: dict) -> None:
    """Add or update a node in the in-memory graph and indexes."""
    eid = entity["id"]
    uid = entity.get("user_id", "default")
    name_lower = entity["name_lower"]
    etype = entity["entity_type"]

    _graph.add_node(eid, **{
        "label": entity["name"],
        "name": entity["name"],
        "name_lower": name_lower,
        "entity_type": etype,
        "user_id": uid,
        "properties": entity.get("properties", {}),
        "last_seen": entity.get("last_seen"),
    })

    _entity_index[(name_lower, etype, uid)] = eid
    _entity_names.setdefault(uid, set()).add(name_lower)


def _add_edge(rel: dict) -> None:
    """Add or update an edge in the in-memory graph."""
    _graph.add_edge(
        rel["source_entity_id"],
        rel["target_entity_id"],
        id=rel["id"],
        label=rel["relation"],
        relation=rel["relation"],
        properties=rel.get("properties", {}),
        source_session_id=rel.get("source_session_id"),
    )


def _maybe_cluster() -> None:
    """Run Leiden community detection via graphify if the graph is non-trivial."""
    if _graph.number_of_nodes() < 3:
        return
    try:
        from graphify.cluster import cluster
        communities = cluster(_graph)
        for node_id, community_id in communities.items():
            if _graph.has_node(node_id):
                _graph.nodes[node_id]["community"] = community_id
        log.info("Graph clustering complete: %d communities", len(set(communities.values())))
    except Exception:
        log.debug("graphify clustering skipped (library not available or graph too small)", exc_info=True)


# ---------------------------------------------------------------------------
# Write operations (Postgres + in-memory graph)
# ---------------------------------------------------------------------------

async def add_triple(
    subject: str,
    subject_type: str,
    relation: str,
    obj: str,
    object_type: str,
    user_id: str = "default",
    source_session_id: str | None = None,
    properties: dict | None = None,
) -> None:
    """Persist a triple and update the in-memory graph.

    Subject and object are upserted as entities, then linked by the relation.
    """
    import memory

    subject = _strip_emojis(subject)
    obj = _strip_emojis(obj)
    subject_type = _normalise_type(subject_type)
    object_type = _normalise_type(object_type)

    if not subject or not obj:
        log.debug("Triple skipped: subject or object is empty after emoji strip")
        return

    # Resolve partial person names to their canonical full name before dedup.
    subject = _resolve_person_name(subject, subject_type, user_id)
    obj = _resolve_person_name(obj, object_type, user_id)

    source_id, source_created = await memory.upsert_entity(
        name=subject,
        entity_type=subject_type,
        user_id=user_id,
        source_session_id=source_session_id,
    )
    target_id, target_created = await memory.upsert_entity(
        name=obj,
        entity_type=object_type,
        user_id=user_id,
        source_session_id=source_session_id,
    )
    rel_id, rel_created = await memory.upsert_relationship(
        source_entity_id=source_id,
        target_entity_id=target_id,
        relation=relation,
        user_id=user_id,
        source_session_id=source_session_id,
        properties=properties,
    )

    # Mirror into in-memory graph
    if source_created or not _graph.has_node(source_id):
        _add_node({
            "id": source_id,
            "name": subject,
            "name_lower": subject.strip().lower(),
            "entity_type": subject_type,
            "user_id": user_id,
            "properties": {},
        })
    if target_created or not _graph.has_node(target_id):
        _add_node({
            "id": target_id,
            "name": obj,
            "name_lower": obj.strip().lower(),
            "entity_type": object_type,
            "user_id": user_id,
            "properties": {},
        })

    _graph.add_edge(source_id, target_id, id=rel_id, label=relation,
                    relation=relation, properties=properties or {},
                    source_session_id=source_session_id)

    log.debug(
        "Triple: [%s] --%s--> [%s] (src_new=%s, tgt_new=%s, rel_new=%s)",
        subject, relation, obj, source_created, target_created, rel_created,
    )


def _resolve_person_name(name: str, entity_type: str, user_id: str) -> str:
    """Canonicalize partial person names to the fuller known name.

    Prevents 'Gaurav' and 'Gaurav Kashyap' becoming separate nodes.
    If the incoming name is a word-prefix of an already-known person name (or
    vice versa), return the longer (more specific) name so both map to the same
    entity via normal dedup.

    Only acts on entity_type == 'person' to avoid false positives on other types.
    """
    if entity_type != "person":
        return name

    name_lower = name.strip().lower()

    # Already an exact match in the index — normal dedup will handle it.
    if (name_lower, "person", user_id) in _entity_index:
        return name

    # Scan known person nodes for a partial-name overlap.
    for nid, data in _graph.nodes(data=True):
        if data.get("user_id") != user_id or data.get("entity_type") != "person":
            continue
        known_lower = data.get("name_lower", "")
        if known_lower == name_lower:
            continue
        # One name must be a leading-word prefix of the other, e.g.
        # "gaurav" is a prefix of "gaurav kashyap".
        shorter, longer = (
            (name_lower, known_lower)
            if len(name_lower) <= len(known_lower)
            else (known_lower, name_lower)
        )
        if longer.startswith(shorter) and (
            len(longer) == len(shorter) or longer[len(shorter)] == " "
        ):
            # Return the longer (canonical) name so the entity deduplicates.
            return data["name"] if len(known_lower) >= len(name_lower) else name

    return name


def _normalise_type(entity_type: str) -> str:
    """Map free-form LLM-produced types to the canonical set."""
    t = entity_type.strip().lower()
    if t in VALID_ENTITY_TYPES:
        return t
    # Best-effort fuzzy mapping
    mapping = {
        "company": "organization", "firm": "organization", "team": "organization",
        "tool": "technology", "framework": "technology", "library": "technology",
        "language": "technology", "stack": "technology", "platform": "technology",
        "skill": "technology", "pattern": "technology", "architecture": "technology",
        "person": "person", "user": "person", "contact": "person",
        "target": "goal", "objective": "goal", "aspiration": "goal",
        "blocker": "constraint", "limitation": "constraint", "requirement": "constraint",
        "milestone": "event", "meeting": "event", "offer": "event", "decision": "event",
        "preference": "preference", "opinion": "preference",
    }
    return mapping.get(t, "project")  # default to project if unknown


# ---------------------------------------------------------------------------
# Edge removal (for SUPERSEDE actions)
# ---------------------------------------------------------------------------

def remove_edge_by_id(rel_id: str) -> bool:
    """Remove an edge from the in-memory graph by relationship ID."""
    for u, v, data in list(_graph.edges(data=True)):
        if data.get("id") == rel_id:
            _graph.remove_edge(u, v)
            log.debug("Removed edge %s from in-memory graph", rel_id)
            return True
    log.debug("Edge %s not found in in-memory graph", rel_id)
    return False


# ---------------------------------------------------------------------------
# Retrieval: edges between a specific entity pair
# ---------------------------------------------------------------------------

def get_edges_between(
    entity_a: str,
    entity_b: str,
    user_id: str = "default",
) -> list[dict]:
    """Return all edges between two named entities (both directions).

    Returns [{"rel_id": str, "relation": str, "source": str, "target": str}].
    """
    a_lower = entity_a.strip().lower()
    b_lower = entity_b.strip().lower()

    a_ids = [
        nid for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id and data.get("name_lower") == a_lower
    ]
    b_ids = [
        nid for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id and data.get("name_lower") == b_lower
    ]

    if not a_ids or not b_ids:
        return []

    results: list[dict] = []
    seen_ids: set[str] = set()

    for a_id in a_ids:
        for b_id in b_ids:
            # a -> b edges
            if _graph.has_edge(a_id, b_id):
                data = _graph.edges[a_id, b_id]
                rid = data.get("id", "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    results.append({
                        "rel_id": rid,
                        "relation": data.get("relation", ""),
                        "source": _graph.nodes[a_id].get("name", a_id),
                        "target": _graph.nodes[b_id].get("name", b_id),
                    })
            # b -> a edges
            if _graph.has_edge(b_id, a_id):
                data = _graph.edges[b_id, a_id]
                rid = data.get("id", "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    results.append({
                        "rel_id": rid,
                        "relation": data.get("relation", ""),
                        "source": _graph.nodes[b_id].get("name", b_id),
                        "target": _graph.nodes[a_id].get("name", a_id),
                    })

    return results


def get_all_entity_names(user_id: str = "default") -> dict[str, str]:
    """Return {name_lower: display_name} for all entities of a user."""
    names: dict[str, str] = {}
    for nid, data in _graph.nodes(data=True):
        if data.get("user_id") == user_id:
            nl = data.get("name_lower", "")
            if nl:
                names[nl] = data.get("name", nl)
    return names


# ---------------------------------------------------------------------------
# Retrieval: neighbor lookup
# ---------------------------------------------------------------------------

def get_neighbors(
    entity_name: str,
    user_id: str = "default",
    depth: int = 1,
) -> list[dict]:
    """Return first-degree neighbors of a named entity.

    Searches all entity types for the given name. Returns a list of dicts:
      {"rel_id": str, "from": str, "relation": str, "to": str, "direction": "out"|"in"}
    """
    name_lower = entity_name.strip().lower()
    results: list[dict] = []

    # Find all matching node ids (same name, any type, this user)
    matching_ids = [
        nid for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id and data.get("name_lower") == name_lower
    ]

    if not matching_ids:
        return results

    for node_id in matching_ids:
        from_name = _graph.nodes[node_id].get("name", node_id)

        # Outgoing edges: what this entity does/has/relates-to
        for _, target_id, edge_data in _graph.out_edges(node_id, data=True):
            target_name = _graph.nodes[target_id].get("name", target_id)
            results.append({
                "rel_id": edge_data.get("id", ""),
                "from": from_name,
                "relation": edge_data.get("relation", "related_to"),
                "to": target_name,
                "to_type": _graph.nodes[target_id].get("entity_type", ""),
                "direction": "out",
            })

        # Incoming edges: what points to this entity
        for source_id, _, edge_data in _graph.in_edges(node_id, data=True):
            source_name = _graph.nodes[source_id].get("name", source_id)
            results.append({
                "rel_id": edge_data.get("id", ""),
                "from": source_name,
                "relation": edge_data.get("relation", "related_to"),
                "to": from_name,
                "to_type": _graph.nodes[node_id].get("entity_type", ""),
                "direction": "in",
            })

    return results


def extract_entities_from_text(
    texts: list[str],
    user_id: str = "default",
) -> list[str]:
    """Find known entity names mentioned in a list of text strings.

    Scans the known entity name set for this user against each text using
    word-boundary regex. No LLM call -- purely string matching against the
    persisted entity index. Returns deduplicated list of matched entity names.
    """
    known = _entity_names.get(user_id, set())
    if not known:
        return []

    combined = " ".join(texts).lower()
    found: list[str] = []

    for name_lower in known:
        # Skip very short names to avoid noise
        if len(name_lower) < 3:
            continue
        pattern = r'\b' + re.escape(name_lower) + r'\b'
        if re.search(pattern, combined):
            found.append(name_lower)
            continue
        # Also match on first word alone (e.g. "Gaurav" matches "Gaurav Kashyap")
        first_word = name_lower.split()[0] if " " in name_lower else None
        if first_word and len(first_word) >= 4:
            first_pattern = r'\b' + re.escape(first_word) + r'\b'
            if re.search(first_pattern, combined):
                found.append(name_lower)

    # Resolve name_lower back to display names via node data
    display_names: list[str] = []
    seen: set[str] = set()
    for name_lower in found:
        for nid, data in _graph.nodes(data=True):
            if (data.get("name_lower") == name_lower
                    and data.get("user_id") == user_id
                    and data.get("name") not in seen):
                display_names.append(data["name"])
                seen.add(data["name"])
                break

    return display_names


# ---------------------------------------------------------------------------
# Stats and analysis
# ---------------------------------------------------------------------------

def get_graph_stats(user_id: str = "default") -> dict:
    """Return basic graph statistics for a user."""
    user_nodes = [
        (nid, data) for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id
    ]
    user_node_ids = {nid for nid, _ in user_nodes}

    user_edges = [
        (u, v, d) for u, v, d in _graph.edges(data=True)
        if u in user_node_ids and v in user_node_ids
    ]

    type_counts: dict[str, int] = {}
    for _, data in user_nodes:
        etype = data.get("entity_type", "unknown")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    relation_counts: dict[str, int] = {}
    for _, _, d in user_edges:
        rel = d.get("relation", "unknown")
        relation_counts[rel] = relation_counts.get(rel, 0) + 1

    # Top entities by degree (most connected)
    if user_node_ids:
        subgraph = _graph.subgraph(user_node_ids)
        degree_map = dict(subgraph.degree())
        top_entities = sorted(
            [
                {"name": _graph.nodes[nid].get("name"), "degree": deg}
                for nid, deg in degree_map.items()
            ],
            key=lambda x: x["degree"],
            reverse=True,
        )[:10]
    else:
        top_entities = []

    return {
        "node_count": len(user_nodes),
        "edge_count": len(user_edges),
        "entity_types": type_counts,
        "relation_types": relation_counts,
        "top_entities": top_entities,
    }


def get_all_entities(user_id: str = "default") -> list[dict]:
    """Return all entity nodes for a user as plain dicts."""
    return [
        {
            "id": nid,
            "name": data.get("name"),
            "entity_type": data.get("entity_type"),
            "properties": data.get("properties", {}),
            "last_seen": data.get("last_seen"),
            "community": data.get("community"),
        }
        for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id
    ]


def get_primary_person_name(user_id: str = "default") -> str | None:
    """Return the display name of the most-connected person entity for this user.

    Used to resolve a real human name when registering artifacts rather than
    inserting a generic 'user' node.
    """
    candidates = [
        (nid, data) for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id and data.get("entity_type") == "person"
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda x: _graph.degree(x[0]))
    return best[1].get("name")


def format_neighbors_as_context(neighbors: list[dict]) -> list[str]:
    """Convert neighbor dicts to human-readable context strings."""
    lines: list[str] = []
    for n in neighbors:
        lines.append(f"[Related] {n['from']} --{n['relation']}--> {n['to']}")
    return lines


# ---------------------------------------------------------------------------
# graphify export (visualization)
# ---------------------------------------------------------------------------

def export_html(user_id: str = "default") -> str:
    """Export an interactive HTML visualization using graphify.

    Returns the raw HTML string. Returns an empty string if graphify is
    unavailable or the graph is empty.
    """
    import tempfile
    import os

    user_node_ids = {
        nid for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id
    }
    if not user_node_ids:
        return ""

    try:
        from graphify.export import to_html
        subgraph = _graph.subgraph(user_node_ids).copy()

        # Build communities dict: {community_id: [node_id, ...]}
        communities: dict[int, list[str]] = {}
        for nid, data in subgraph.nodes(data=True):
            cid = data.get("community", 0)
            communities.setdefault(cid, []).append(nid)

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            tmp_path = f.name

        to_html(subgraph, communities=communities, output_path=tmp_path)

        with open(tmp_path, "r", encoding="utf-8") as f:
            html = f.read()

        os.unlink(tmp_path)
        return html
    except Exception:
        log.warning("graphify HTML export failed", exc_info=True)
        return ""


def export_json(user_id: str = "default") -> dict:
    """Export the graph as a node-link JSON dict."""
    user_node_ids = {
        nid for nid, data in _graph.nodes(data=True)
        if data.get("user_id") == user_id
    }
    subgraph = _graph.subgraph(user_node_ids).copy()
    return nx.node_link_data(subgraph)
