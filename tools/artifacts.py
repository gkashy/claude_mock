"""
Artifact tools: create and update persistent artifacts stored in PostgreSQL.

Artifacts appear in a side panel for the user. Each update creates a new
version, enabling prev/next navigation.
"""

from __future__ import annotations

import memory
from tool_context import get_session_id, get_user_id

# Track the last artifact created/updated this turn so the agent loop
# can emit it to the frontend immediately after the tool call.
_last_artifact: dict | None = None


def get_last_artifact() -> dict | None:
    return _last_artifact


def clear_last_artifact() -> None:
    global _last_artifact
    _last_artifact = None


# ---------------------------------------------------------------------------
# create_artifact
# ---------------------------------------------------------------------------

async def _register_artifact_in_graph(
    art: dict,
    session_id: str,
    user_id: str,
) -> None:
    """Add an artifact as a node in the knowledge graph with an edge from its creator."""
    import knowledge_graph as kg

    artifact_name = art.get("title", "Untitled Artifact")
    artifact_id = art.get("id", "")

    # Resolve the real person name rather than using a generic "user" label.
    # Falls back to user_id if no person entity has been established yet.
    creator_name = kg.get_primary_person_name(user_id) or user_id

    await kg.add_triple(
        subject=creator_name,
        subject_type="person",
        relation="created",
        obj=artifact_name,
        object_type="project",
        user_id=user_id,
        source_session_id=session_id,
        properties={"artifact_id": artifact_id, "filename": art.get("filename", "")},
    )


TOOL_DEFINITIONS = [
    {
        "name": "find_artifact",
        "description": (
            "Search for artifacts created in ANY past session by keyword. "
            "Use this when the user asks to see, pull up, or reference something "
            "created before (a presentation, document, code file, etc.) that isn't "
            "in the current session. Returns artifact metadata and IDs that can be "
            "loaded with get_artifact_content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search for in artifact titles (e.g. 'watchlist', 'presentation', 'resume').",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_artifact_content",
        "description": (
            "Retrieve the current content of an existing artifact. Use this before "
            "calling update_artifact when you need to see the current content to make "
            "targeted edits (e.g. the user asks to 'change line 3' or 'fix the header'). "
            "Not needed if you just created the artifact in this conversation and "
            "still have it in context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The ID of the artifact to read.",
                },
                "version": {
                    "type": "integer",
                    "description": "Optional specific version to read. Omit for the latest version.",
                },
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "create_artifact",
        "description": (
            "Create a viewable artifact that appears in a side panel for the user. "
            "Use this for ANY substantial content: code files, documents, HTML pages, "
            "markdown documents, CSV data, config files, scripts, etc. "
            "The artifact is rendered with syntax highlighting and is downloadable. "
            "Do NOT use write_file for content the user should see -- use this instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "A short descriptive title for the artifact.",
                },
                "content_type": {
                    "type": "string",
                    "description": (
                        "The type of content. One of: "
                        "code, document, html, markdown, csv, json, xml, yaml, text"
                    ),
                },
                "language": {
                    "type": "string",
                    "description": (
                        "Programming language for syntax highlighting (for code artifacts). "
                        "E.g. python, javascript, typescript, html, css, sql, bash, etc."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "Suggested filename for download. E.g. 'main.py', 'report.md'",
                },
                "content": {
                    "type": "string",
                    "description": "The full content of the artifact.",
                },
            },
            "required": ["title", "content_type", "filename", "content"],
        },
    },
    {
        "name": "update_artifact",
        "description": (
            "Update an existing artifact with new content, creating a new version. "
            "Use this when the user asks to modify, edit, fix, or improve an artifact "
            "that was already created in this conversation. The previous version is "
            "preserved and the user can navigate between versions. "
            "You MUST provide the complete updated content -- not just the diff."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The ID of the artifact to update (from a previous create_artifact result).",
                },
                "content": {
                    "type": "string",
                    "description": "The full updated content of the artifact.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional new title. Omit to keep the current title.",
                },
                "content_type": {
                    "type": "string",
                    "description": "Optional new content type. Omit to keep the current type.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional new language. Omit to keep the current language.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional new filename. Omit to keep the current filename.",
                },
            },
            "required": ["artifact_id", "content"],
        },
    },
]


async def find_artifact(query: str) -> str:
    """Search for artifacts across all sessions by title keyword."""
    user_id = get_user_id()
    results = await memory.search_artifacts(user_id, query)
    if not results:
        return f"No artifacts found matching: {query}"
    lines = []
    for r in results:
        lines.append(
            f"[id: {r['id']}] \"{r['title']}\" ({r['filename']}) "
            f"-- v{r['current_version']}, {r['content_type']}, "
            f"session: {r['session_id']}"
        )
    return "\n".join(lines)


async def get_artifact_content(
    artifact_id: str,
    version: int | None = None,
) -> str:
    global _last_artifact

    loaded = await memory.get_artifact(artifact_id, version=version)
    if not loaded:
        return f"Error: artifact '{artifact_id}' not found."

    _last_artifact = loaded

    ver_label = f"v{loaded['version']}/{loaded['total_versions']}"
    header = (
        f"[{loaded['title']}] ({loaded['filename']}) -- {ver_label}, "
        f"{loaded['content_type']}"
    )
    return f"{header}\n---\n{loaded['content']}"


async def create_artifact(
    title: str,
    content_type: str,
    filename: str,
    content: str,
    language: str = "",
) -> str:
    global _last_artifact

    session_id = get_session_id()
    user_id = get_user_id()

    art = await memory.create_artifact(
        session_id=session_id,
        user_id=user_id,
        title=title,
        content_type=content_type,
        filename=filename,
        content=content,
        language=language,
    )
    _last_artifact = art

    try:
        await _register_artifact_in_graph(art, session_id, user_id)
    except Exception:
        pass

    return f"Artifact created: {title} ({filename}) [id: {art['id']}]"


async def update_artifact(
    artifact_id: str,
    content: str,
    title: str | None = None,
    content_type: str | None = None,
    language: str | None = None,
    filename: str | None = None,
) -> str:
    global _last_artifact

    updated = await memory.update_artifact(
        artifact_id=artifact_id,
        content=content,
        title=title,
        content_type=content_type,
        language=language,
        filename=filename,
    )

    if not updated:
        return f"Error: artifact '{artifact_id}' not found."

    _last_artifact = updated

    try:
        await _register_artifact_in_graph(updated, get_session_id(), get_user_id())
    except Exception:
        pass

    return (
        f"Artifact updated: {updated['title']} ({updated['filename']}) "
        f"[id: {artifact_id}, version: {updated['version']}]"
    )
