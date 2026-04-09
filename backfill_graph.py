"""
Backfill: extract knowledge graph triples from existing flat facts in PostgreSQL.

Loads all active facts for a user, sends them to the LLM in batches to extract
(subject, relation, object) triples, then persists them into kg_entities and
kg_relationships via knowledge_graph.add_triple().

Usage:
    python backfill_graph.py
    python backfill_graph.py --user-id default --batch-size 25 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("backfill_graph")

_TRIPLE_EXTRACTION_PROMPT = """\
You are extracting structured knowledge graph triples from a list of memory facts.

## Primary user
{user_name_block}

## Facts
{facts_block}

## Task
For each fact, extract all named entity relationships as triples. Each triple has:
- subject / subject_type: source entity and its type
- relation: short snake_case verb phrase (e.g. targets, uses, blocked_by, built, works_at, prefers, conflicts_with, achieved, requires, affiliated_with)
- object / object_type: target entity and its type

Entity types (use exactly one): person, organization, project, technology, goal, constraint, event, preference

### Rules
1. Only extract triples where BOTH entities are clearly named (not pronouns or "the system").
2. Skip facts that are purely descriptive with no named entities.
3. One fact may produce zero, one, or multiple triples.
4. Keep relation verbs concise and reusable across facts.
5. ALWAYS use the primary user's exact full name as given above -- never use just the first name, "user", or "the user".

### Output format (strict JSON, no markdown fences)
{{"triples": [
  {{"subject": "{example_name}", "subject_type": "person", "relation": "targets", "object": "ScopeAR", "object_type": "organization"}},
  {{"subject": "MakaluHealthPlatform", "subject_type": "project", "relation": "uses", "object": "LangGraph", "object_type": "technology"}},
  {{"subject": "{example_name}", "subject_type": "person", "relation": "constrained_by", "object": "Canadian work permit", "object_type": "constraint"}}
]}}
"""


def _parse_triples(raw: str) -> list[dict]:
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
                return []
        else:
            return []
    if isinstance(parsed, dict):
        return parsed.get("triples", [])
    return []


async def extract_triples_from_facts(
    facts: list[dict],
    user_id: str,
) -> list[dict]:
    """Send a batch of facts to the LLM and return extracted triples."""
    from models import get_provider, TextDelta

    from config import settings as _cfg

    facts_block = "\n".join(
        f"- [{f['id']}] {f['content']}" for f in facts
    )
    canonical_name = _cfg.USER_NAME or "the user"
    user_name_block = f"The primary user's canonical full name is: **{canonical_name}**. Always use this exact name in triples."
    prompt = _TRIPLE_EXTRACTION_PROMPT.format(
        facts_block=facts_block,
        user_name_block=user_name_block,
        example_name=canonical_name,
    )

    provider = get_provider()
    response_text = ""
    async for event in provider.stream(
        system="You are a precise knowledge graph extractor. Output only valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    ):
        if isinstance(event, TextDelta):
            response_text += event.text

    return _parse_triples(response_text)


async def backfill(
    user_id: str = "default",
    batch_size: int = 25,
    dry_run: bool = False,
) -> None:
    import memory
    import knowledge_graph as kg

    await memory.init_db()

    facts = await memory.load_facts(user_id)
    if not facts:
        print("No facts found -- nothing to backfill.")
        return

    print(f"Found {len(facts)} facts for user '{user_id}'.")
    if dry_run:
        print("[DRY RUN] No changes will be written.")

    total_triples = 0
    total_entities = 0
    total_relationships = 0

    # Process in batches to keep prompts manageable
    for batch_start in range(0, len(facts), batch_size):
        batch = facts[batch_start : batch_start + batch_size]
        batch_end = batch_start + len(batch)
        print(f"\nProcessing facts {batch_start + 1}-{batch_end} of {len(facts)}...")

        triples = await extract_triples_from_facts(batch, user_id)
        log.info("Batch %d-%d: %d triples extracted", batch_start + 1, batch_end, len(triples))

        if not triples:
            print(f"  No triples found in this batch.")
            continue

        for t in triples:
            subject = t.get("subject", "").strip()
            subject_type = t.get("subject_type", "project").strip()
            relation = t.get("relation", "").strip()
            obj = t.get("object", "").strip()
            object_type = t.get("object_type", "project").strip()

            if not (subject and relation and obj):
                continue

            total_triples += 1
            print(f"  {subject} ({subject_type}) --{relation}--> {obj} ({object_type})")

            if not dry_run:
                await kg.add_triple(
                    subject=subject,
                    subject_type=subject_type,
                    relation=relation,
                    obj=obj,
                    object_type=object_type,
                    user_id=user_id,
                )

    # Backfill artifacts into the graph
    print(f"\n--- Backfilling artifacts ---")
    artifact_count = 0
    all_sessions = await memory.list_sessions(user_id)
    for sess in all_sessions:
        artifacts = await memory.list_artifacts_for_session(sess["id"])
        for art in artifacts:
            artifact_count += 1
            title = art.get("title", "Untitled")
            print(f"  Artifact: {title} ({art.get('filename', '')}) [id: {art['id']}]")
            if not dry_run:
                await kg.add_triple(
                    subject=title,
                    subject_type="project",
                    relation="created_by",
                    obj="user",
                    object_type="person",
                    user_id=user_id,
                    source_session_id=sess["id"],
                    properties={"artifact_id": art["id"], "filename": art.get("filename", "")},
                )

    print(f"\n--- Summary ---")
    print(f"Facts processed : {len(facts)}")
    print(f"Triples found   : {total_triples}")
    print(f"Artifacts found : {artifact_count}")

    if not dry_run:
        stats = kg.get_graph_stats(user_id=user_id)
        print(f"Graph nodes     : {stats['node_count']}")
        print(f"Graph edges     : {stats['edge_count']}")
        print(f"Entity types    : {stats['entity_types']}")
        if stats["top_entities"]:
            print("Top entities    :")
            for e in stats["top_entities"][:5]:
                print(f"  {e['name']} (degree={e['degree']})")
    else:
        print("[DRY RUN] Nothing was written to the database.")

    print("\n--- Backfill complete ---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill knowledge graph from existing facts")
    parser.add_argument("--user-id", default="default", help="User ID to backfill (default: default)")
    parser.add_argument("--batch-size", type=int, default=25, help="Facts per LLM call (default: 25)")
    parser.add_argument("--dry-run", action="store_true", help="Extract and print triples without writing")
    args = parser.parse_args()

    asyncio.run(backfill(
        user_id=args.user_id,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    ))
