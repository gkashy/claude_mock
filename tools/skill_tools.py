"""
Tools for loading expert skill documents on demand.

Skills are markdown files in the skills/ directory. The agent loads a skill
before executing a complex task to get detailed, step-by-step instructions.
"""

from __future__ import annotations

import json
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent.parent / "skills"
_REGISTRY_PATH = _SKILLS_DIR / "_registry.json"


def _load_registry() -> list[dict]:
    if not _REGISTRY_PATH.exists():
        return []
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


TOOL_DEFINITIONS = [
    {
        "name": "list_skills",
        "description": (
            "List all available skills the agent can load. Returns skill names "
            "and descriptions. Call this if you are unsure which skill to use."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "load_skill",
        "description": (
            "Load a skill document containing expert instructions for a specific "
            "task type. Call this BEFORE executing complex tasks like creating "
            "resumes, conducting research, or building HTML documents. "
            "The skill provides step-by-step instructions, formatting rules, "
            "code templates, and best practices that you should follow precisely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The skill name to load (e.g. 'docx_resume', 'web_research', "
                        "'html_document'). Use list_skills to see available options."
                    ),
                },
            },
            "required": ["name"],
        },
    },
]


async def list_skills() -> str:
    registry = _load_registry()
    if not registry:
        return "No skills available."
    lines = []
    for s in registry:
        lines.append(f"- **{s['name']}**: {s['description']}")
    return "Available skills:\n" + "\n".join(lines)


async def load_skill(name: str) -> str:
    skill_path = _SKILLS_DIR / f"{name}.md"
    if not skill_path.exists():
        available = [s["name"] for s in _load_registry()]
        return (
            f"Error: skill '{name}' not found. "
            f"Available skills: {', '.join(available) if available else 'none'}"
        )
    content = skill_path.read_text(encoding="utf-8")
    return f"=== SKILL: {name} ===\n\n{content}\n\n=== END SKILL ==="
