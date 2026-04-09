"""In-memory knowledge graph manager built on graphify and NetworkX.

All state is instance-based (no module globals), so multiple GraphManager
instances can coexist for different users or databases.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    from .protocols import StorageBackend, EmbeddingProvider, VectorStore

log = logging.getLogger(__name__)

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

DEFAULT_ENTITY_TYPES = frozenset({
    "person", "organization", "project", "technology",
    "goal", "constraint", "event", "preference",
})

_TYPE_ALIASES: dict[str, str] = {
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


class GraphManager:
    """Instance-based knowledge graph wrapping NetworkX + graphify."""

    def __init__(
        self,
        entity_types: frozenset[str] | None = None,
    ) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._entity_index: dict[tuple[str, str, str], str] = {}
        self._entity_names: dict[str, set[str]] = {}
        self._entity_types = entity_types or DEFAULT_ENTITY_TYPES

    # ------------------------------------------------------------------
    # Init / rebuild
    # ------------------------------------------------------------------

    async def load(
        self,
        storage: StorageBackend,
        user_id: str = "default",
    ) -> None:
        """Load all persisted entities and relationships into memory."""
        self._graph = nx.DiGraph()
        self._entity_index = {}
        self._entity_names[user_id] = set()

        entities, relationships = await storage.load_graph_data(user_id)

        for e in entities:
            self._add_node(e)
        for r in relationships:
            self._add_edge(r)

        log.info(
            "Graph loaded: %d nodes, %d edges (user=%s)",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
            user_id,
        )
        self._run_clustering()

    # ------------------------------------------------------------------
    # Node / edge helpers
    # ------------------------------------------------------------------

    def _add_node(self, entity: dict) -> None:
        eid = entity["id"]
        uid = entity.get("user_id", "default")
        name_lower = entity["name_lower"]
        etype = entity["entity_type"]

        self._graph.add_node(eid, **{
            "label": entity["name"],
            "name": entity["name"],
            "name_lower": name_lower,
            "entity_type": etype,
            "user_id": uid,
            "properties": entity.get("properties", {}),
            "last_seen": entity.get("last_seen"),
        })
        self._entity_index[(name_lower, etype, uid)] = eid
        self._entity_names.setdefault(uid, set()).add(name_lower)

    def _add_edge(self, rel: dict) -> None:
        self._graph.add_edge(
            rel["source_entity_id"],
            rel["target_entity_id"],
            id=rel["id"],
            label=rel["relation"],
            relation=rel["relation"],
            properties=rel.get("properties", {}),
            source_session_id=rel.get("source_session_id"),
        )

    def _run_clustering(self) -> None:
        if self._graph.number_of_nodes() < 3:
            return
        try:
            from graphify.cluster import cluster
            communities = cluster(self._graph)
            for node_id, community_id in communities.items():
                if self._graph.has_node(node_id):
                    self._graph.nodes[node_id]["community"] = community_id
            log.info("Clustering complete: %d communities", len(set(communities.values())))
        except Exception:
            log.debug("graphify clustering skipped", exc_info=True)

    # ------------------------------------------------------------------
    # Write: add_triple (full persistence + in-memory)
    # ------------------------------------------------------------------

    async def add_triple(
        self,
        subject: str,
        subject_type: str,
        relation: str,
        obj: str,
        object_type: str,
        user_id: str,
        storage: StorageBackend,
        embeddings: EmbeddingProvider | None = None,
        vectors: VectorStore | None = None,
        source_session_id: str | None = None,
        properties: dict | None = None,
    ) -> None:
        subject = _strip_emojis(subject)
        obj = _strip_emojis(obj)
        subject_type = self._normalise_type(subject_type)
        object_type = self._normalise_type(object_type)

        if not subject or not obj:
            return

        subject = self._resolve_person_name(subject, subject_type, user_id)
        obj = self._resolve_person_name(obj, object_type, user_id)

        source_id, source_created = await storage.upsert_entity(
            name=subject, entity_type=subject_type,
            user_id=user_id, source_session_id=source_session_id,
        )
        target_id, target_created = await storage.upsert_entity(
            name=obj, entity_type=object_type,
            user_id=user_id, source_session_id=source_session_id,
        )

        if source_created and embeddings and vectors:
            emb = await embeddings.embed_text(subject)
            await vectors.upsert_entity(source_id, emb, {
                "user_id": user_id, "name": subject, "entity_type": subject_type,
            })

        if target_created and embeddings and vectors:
            emb = await embeddings.embed_text(obj)
            await vectors.upsert_entity(target_id, emb, {
                "user_id": user_id, "name": obj, "entity_type": object_type,
            })

        rel_id, _ = await storage.upsert_relationship(
            source_entity_id=source_id, target_entity_id=target_id,
            relation=relation, user_id=user_id,
            source_session_id=source_session_id, properties=properties,
        )

        if source_created or not self._graph.has_node(source_id):
            self._add_node({
                "id": source_id, "name": subject,
                "name_lower": subject.strip().lower(),
                "entity_type": subject_type, "user_id": user_id, "properties": {},
            })
        if target_created or not self._graph.has_node(target_id):
            self._add_node({
                "id": target_id, "name": obj,
                "name_lower": obj.strip().lower(),
                "entity_type": object_type, "user_id": user_id, "properties": {},
            })

        self._graph.add_edge(
            source_id, target_id, id=rel_id, label=relation,
            relation=relation, properties=properties or {},
            source_session_id=source_session_id,
        )

    # ------------------------------------------------------------------
    # Write: remove edge
    # ------------------------------------------------------------------

    def remove_edge_by_id(self, rel_id: str) -> bool:
        for u, v, data in list(self._graph.edges(data=True)):
            if data.get("id") == rel_id:
                self._graph.remove_edge(u, v)
                return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def neighbors(
        self, entity_name: str, user_id: str = "default",
    ) -> list[dict]:
        """First-degree neighbors with rel_id, from, relation, to, direction."""
        name_lower = entity_name.strip().lower()
        results: list[dict] = []

        matching_ids = [
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id and data.get("name_lower") == name_lower
        ]
        if not matching_ids:
            return results

        for node_id in matching_ids:
            from_name = self._graph.nodes[node_id].get("name", node_id)
            for _, target_id, edge_data in self._graph.out_edges(node_id, data=True):
                target_name = self._graph.nodes[target_id].get("name", target_id)
                results.append({
                    "rel_id": edge_data.get("id", ""),
                    "from": from_name,
                    "relation": edge_data.get("relation", "related_to"),
                    "to": target_name,
                    "to_type": self._graph.nodes[target_id].get("entity_type", ""),
                    "direction": "out",
                })
            for source_id, _, edge_data in self._graph.in_edges(node_id, data=True):
                source_name = self._graph.nodes[source_id].get("name", source_id)
                results.append({
                    "rel_id": edge_data.get("id", ""),
                    "from": source_name,
                    "relation": edge_data.get("relation", "related_to"),
                    "to": from_name,
                    "to_type": self._graph.nodes[node_id].get("entity_type", ""),
                    "direction": "in",
                })
        return results

    def edges_between(
        self, entity_a: str, entity_b: str, user_id: str = "default",
    ) -> list[dict]:
        """All edges between two named entities (both directions)."""
        a_lower = entity_a.strip().lower()
        b_lower = entity_b.strip().lower()

        a_ids = [
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id and data.get("name_lower") == a_lower
        ]
        b_ids = [
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id and data.get("name_lower") == b_lower
        ]
        if not a_ids or not b_ids:
            return []

        results: list[dict] = []
        seen: set[str] = set()
        for a_id in a_ids:
            for b_id in b_ids:
                for u, v in [(a_id, b_id), (b_id, a_id)]:
                    if self._graph.has_edge(u, v):
                        data = self._graph.edges[u, v]
                        rid = data.get("id", "")
                        if rid and rid not in seen:
                            seen.add(rid)
                            results.append({
                                "rel_id": rid,
                                "relation": data.get("relation", ""),
                                "source": self._graph.nodes[u].get("name", u),
                                "target": self._graph.nodes[v].get("name", v),
                            })
        return results

    def all_entity_names(self, user_id: str = "default") -> dict[str, str]:
        """Returns {name_lower: display_name} for all entities of a user."""
        names: dict[str, str] = {}
        for nid, data in self._graph.nodes(data=True):
            if data.get("user_id") == user_id:
                nl = data.get("name_lower", "")
                if nl:
                    names[nl] = data.get("name", nl)
        return names

    def extract_entities_from_text(
        self, texts: list[str], user_id: str = "default",
    ) -> list[str]:
        """Find known entity names mentioned in text strings (regex, no LLM)."""
        known = self._entity_names.get(user_id, set())
        if not known:
            return []

        combined = " ".join(texts).lower()
        found: list[str] = []
        for name_lower in known:
            if len(name_lower) < 3:
                continue
            pattern = r'\b' + re.escape(name_lower) + r'\b'
            if re.search(pattern, combined):
                found.append(name_lower)
                continue
            first_word = name_lower.split()[0] if " " in name_lower else None
            if first_word and len(first_word) >= 4:
                if re.search(r'\b' + re.escape(first_word) + r'\b', combined):
                    found.append(name_lower)

        display_names: list[str] = []
        seen: set[str] = set()
        for nl in found:
            for nid, data in self._graph.nodes(data=True):
                if (data.get("name_lower") == nl
                        and data.get("user_id") == user_id
                        and data.get("name") not in seen):
                    display_names.append(data["name"])
                    seen.add(data["name"])
                    break
        return display_names

    # ------------------------------------------------------------------
    # Stats and export
    # ------------------------------------------------------------------

    def stats(self, user_id: str = "default") -> dict:
        user_nodes = [
            (nid, data) for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id
        ]
        user_node_ids = {nid for nid, _ in user_nodes}
        user_edges = [
            (u, v, d) for u, v, d in self._graph.edges(data=True)
            if u in user_node_ids and v in user_node_ids
        ]

        type_counts: dict[str, int] = {}
        for _, data in user_nodes:
            t = data.get("entity_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        relation_counts: dict[str, int] = {}
        for _, _, d in user_edges:
            r = d.get("relation", "unknown")
            relation_counts[r] = relation_counts.get(r, 0) + 1

        top_entities: list[dict] = []
        if user_node_ids:
            subgraph = self._graph.subgraph(user_node_ids)
            degree_map = dict(subgraph.degree())
            top_entities = sorted(
                [{"name": self._graph.nodes[n].get("name"), "degree": d}
                 for n, d in degree_map.items()],
                key=lambda x: x["degree"], reverse=True,
            )[:10]

        return {
            "node_count": len(user_nodes),
            "edge_count": len(user_edges),
            "entity_types": type_counts,
            "relation_types": relation_counts,
            "top_entities": top_entities,
        }

    def export_json(self, user_id: str = "default") -> dict:
        user_node_ids = {
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id
        }
        subgraph = self._graph.subgraph(user_node_ids).copy()
        return nx.node_link_data(subgraph)

    def visualize(self, user_id: str = "default") -> str:
        """Export interactive HTML visualization via graphify."""
        user_node_ids = {
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id
        }
        if not user_node_ids:
            return ""
        try:
            from graphify.export import to_html
            subgraph = self._graph.subgraph(user_node_ids).copy()
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

    def cluster(self, user_id: str = "default") -> dict[str, int]:
        """Run Leiden community detection via graphify. Returns {node_id: community_id}."""
        user_node_ids = {
            nid for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id
        }
        if len(user_node_ids) < 3:
            return {}
        try:
            from graphify.cluster import cluster
            subgraph = self._graph.subgraph(user_node_ids).copy()
            return cluster(subgraph)
        except Exception:
            log.warning("graphify clustering failed", exc_info=True)
            return {}

    def primary_person_name(self, user_id: str = "default") -> str | None:
        candidates = [
            (nid, data) for nid, data in self._graph.nodes(data=True)
            if data.get("user_id") == user_id and data.get("entity_type") == "person"
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda x: self._graph.degree(x[0]))
        return best[1].get("name")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_person_name(self, name: str, entity_type: str, user_id: str) -> str:
        if entity_type != "person":
            return name
        name_lower = name.strip().lower()
        if (name_lower, "person", user_id) in self._entity_index:
            return name
        for nid, data in self._graph.nodes(data=True):
            if data.get("user_id") != user_id or data.get("entity_type") != "person":
                continue
            known_lower = data.get("name_lower", "")
            if known_lower == name_lower:
                continue
            shorter, longer = (
                (name_lower, known_lower)
                if len(name_lower) <= len(known_lower)
                else (known_lower, name_lower)
            )
            if longer.startswith(shorter) and (
                len(longer) == len(shorter) or longer[len(shorter)] == " "
            ):
                return data["name"] if len(known_lower) >= len(name_lower) else name
        return name

    def _normalise_type(self, entity_type: str) -> str:
        t = entity_type.strip().lower()
        if t in self._entity_types:
            return t
        return _TYPE_ALIASES.get(t, "project")


def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()
