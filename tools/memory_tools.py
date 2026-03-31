"""
Tool: explicit memory management -- remember, recall, and forget facts.
Backed by PostgreSQL + Qdrant via the memory module.
"""

from __future__ import annotations

import memory

TOOL_DEFINITIONS = [
    {
        "name": "remember_fact",
        "description": (
            "Store an important fact for future conversations. Use when the user "
            "shares preferences, important information, decisions, or anything "
            "worth remembering across sessions. Automatically detects duplicates "
            "and updates existing facts when appropriate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact to remember, as a concise statement.",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall_facts",
        "description": (
            "Search stored facts using semantic similarity. Understands meaning, "
            "not just keywords -- e.g. querying 'programming skills' finds facts "
            "about Python, JavaScript, etc. Returns results ranked by relevance "
            "with confidence scores. Falls back to keyword search if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language query describing what you want to recall. "
                        "Be descriptive -- 'user work history' works better than just 'work'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a specific fact from memory by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "string",
                    "description": "The ID of the fact to delete.",
                },
            },
            "required": ["fact_id"],
        },
    },
]


async def remember_fact(fact: str, _user_id: str = "default") -> str:
    fact_id, action = await memory.save_fact_with_dedup(_user_id, fact)
    if action == "updated":
        return f"Updated existing memory (id: {fact_id}): {fact}"
    if action == "duplicate":
        return f"Already remembered (id: {fact_id}): {fact}"
    return f"Remembered (id: {fact_id}): {fact}"


async def recall_facts(query: str, _user_id: str = "default") -> str:
    results = await memory.recall_facts_smart(_user_id, query)
    if not results:
        return f"No facts found matching: {query}"
    lines = []
    for r in results:
        score = r.get("score", 0.0)
        score_label = f" (relevance: {score:.0%})" if score > 0 else " (keyword match)"
        lines.append(f"[{r['id']}] {r['content']}{score_label}")
    return "\n".join(lines)


async def forget_fact(fact_id: str) -> str:
    deleted = await memory.delete_fact(fact_id)
    if deleted:
        return f"Forgotten fact {fact_id}"
    return f"No fact found with id: {fact_id}"
