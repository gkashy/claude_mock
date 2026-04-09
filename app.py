"""
FastAPI server: WebSocket chat streaming + REST endpoints for sessions and memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import anthropic

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("agent-chat")

import memory
import vector_store
from agent import (
    run_turn,
    AgentTextDelta,
    AgentThinking,
    AgentToolStart,
    AgentToolResult,
    AgentArtifact,
    AgentToolGenerating,
    AgentToolProgress,
    AgentDone,
)
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await memory.init_db()
    await vector_store.ensure_collection()
    await vector_store.ensure_entity_collection()
    import knowledge_graph as kg
    await kg.init_graph(user_id="default")
    yield
    await vector_store.close()


app = FastAPI(title="Agent Chat", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Static files + index
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ---------------------------------------------------------------------------
# Session REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def api_list_sessions(user_id: str = "default"):
    return await memory.list_sessions(user_id)


@app.post("/api/sessions")
async def api_create_session(user_id: str = "default"):
    session_id = await memory.create_session(user_id)
    return {"session_id": session_id}


@app.get("/api/sessions/{session_id}/messages")
async def api_get_messages(session_id: str):
    msgs = await memory.load_messages(session_id)
    return msgs


# ---------------------------------------------------------------------------
# Facts REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/facts")
async def api_list_facts(user_id: str = "default"):
    return await memory.load_facts(user_id)


@app.get("/api/facts/search")
async def api_search_facts(q: str, user_id: str = "default"):
    results = await memory.recall_facts_smart(user_id, q, top_k=20)
    return results


@app.get("/api/facts/stats")
async def api_facts_stats(user_id: str = "default"):
    facts = await memory.load_facts(user_id)
    return {"count": len(facts)}


@app.put("/api/facts/{fact_id}")
async def api_update_fact(fact_id: str, body: dict):
    content = body.get("content", "").strip()
    if not content:
        return {"updated": False, "error": "content is required"}
    updated = await memory.update_fact(fact_id, content)
    return {"updated": updated}


@app.delete("/api/facts/{fact_id}")
async def api_delete_fact(fact_id: str):
    deleted = await memory.delete_fact(fact_id)
    return {"deleted": deleted}


@app.post("/api/sessions/{session_id}/extract")
async def api_extract_facts(session_id: str, user_id: str = "default"):
    facts = await memory.extract_facts_from_session(session_id, user_id)
    return {"extracted": facts}


# ---------------------------------------------------------------------------
# Artifact REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions/{session_id}/artifacts")
async def api_list_session_artifacts(session_id: str):
    """List all artifacts in a session (metadata only, no content)."""
    return await memory.list_artifacts_for_session(session_id)


@app.get("/api/artifacts/{artifact_id}")
async def api_get_artifact(artifact_id: str, version: int | None = None):
    """Get a single artifact with content. Optional version query param."""
    art = await memory.get_artifact(artifact_id, version=version)
    if not art:
        return {"error": "not found"}
    return art


@app.get("/api/artifacts/{artifact_id}/versions")
async def api_list_artifact_versions(artifact_id: str):
    """List all versions of an artifact."""
    return await memory.list_artifact_versions(artifact_id)


@app.get("/api/artifacts/{artifact_id}/render")
async def api_render_artifact(artifact_id: str, version: int | None = None):
    """Render an artifact directly in the browser (HTML artifacts open natively)."""
    from fastapi.responses import HTMLResponse, PlainTextResponse
    art = await memory.get_artifact(artifact_id, version=version)
    if not art:
        return PlainTextResponse("Artifact not found", status_code=404)
    if art["content_type"] == "html":
        return HTMLResponse(content=art["content"])
    return PlainTextResponse(content=art["content"])


# ---------------------------------------------------------------------------
# Knowledge graph REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/graph/stats")
async def api_graph_stats(user_id: str = "default"):
    """Basic graph statistics: node/edge counts, entity type breakdown, top entities."""
    import knowledge_graph as kg
    return kg.get_graph_stats(user_id=user_id)


@app.get("/api/graph/entities")
async def api_graph_entities(user_id: str = "default", entity_type: str | None = None):
    """List all known entities for a user, optionally filtered by type."""
    return await memory.list_entities(user_id=user_id, entity_type=entity_type)


@app.get("/api/graph/neighbors/{entity_name}")
async def api_graph_neighbors(entity_name: str, user_id: str = "default"):
    """First-degree neighbors for a named entity."""
    import knowledge_graph as kg
    neighbors = kg.get_neighbors(entity_name, user_id=user_id)
    return {"entity": entity_name, "neighbors": neighbors}


@app.get("/api/graph/visualize")
async def api_graph_visualize(user_id: str = "default"):
    """Serve an interactive HTML graph visualization (via graphify)."""
    from fastapi.responses import HTMLResponse
    import knowledge_graph as kg
    html = kg.export_html(user_id=user_id)
    if not html:
        return HTMLResponse(
            content="<h2>No graph data yet. Start chatting to build your knowledge graph.</h2>",
            status_code=200,
        )
    return HTMLResponse(content=html)


@app.get("/api/graph/export")
async def api_graph_export(user_id: str = "default"):
    """Export the graph as node-link JSON (NetworkX format)."""
    import knowledge_graph as kg
    return kg.export_json(user_id=user_id)


@app.post("/api/graph/rebuild")
async def api_graph_rebuild(user_id: str = "default"):
    """Force a full graph rebuild from Postgres (admin/debug)."""
    import knowledge_graph as kg
    await kg.init_graph(user_id=user_id)
    stats = kg.get_graph_stats(user_id=user_id)
    return {"rebuilt": True, **stats}


@app.post("/api/graph/merge-duplicates")
async def api_graph_merge_duplicates(user_id: str = "default"):
    """Detect and merge duplicate person entities where one name is a prefix of another.

    Example: 'Gaurav' and 'Gaurav Kashyap' -> keeps 'Gaurav Kashyap', re-points
    all relationships from the shorter entity to the longer one, then deactivates
    the shorter entity and rebuilds the in-memory graph.
    """
    from sqlalchemy import select, update as sa_update
    from memory import (
        Entity, Relationship, _async_session,
    )
    from datetime import datetime, timezone
    import knowledge_graph as kg

    merged: list[dict] = []

    async with _async_session() as db:
        result = await db.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.entity_type == "person",
                Entity.active == True,
            )
        )
        persons = result.scalars().all()

        # Build list of (name_lower, entity) sorted longest first
        sorted_persons = sorted(persons, key=lambda e: len(e.name_lower), reverse=True)

        deactivated_ids: set[str] = set()

        for i, longer in enumerate(sorted_persons):
            if longer.id in deactivated_ids:
                continue
            for shorter in sorted_persons[i + 1:]:
                if shorter.id in deactivated_ids:
                    continue
                sl = shorter.name_lower.strip()
                ll = longer.name_lower.strip()
                # Shorter must be a word-boundary prefix of longer
                if ll.startswith(sl) and (len(ll) == len(sl) or ll[len(sl)] == " "):
                    # Re-point all relationships from shorter -> longer
                    await db.execute(
                        sa_update(Relationship)
                        .where(Relationship.source_entity_id == shorter.id)
                        .values(source_entity_id=longer.id)
                    )
                    await db.execute(
                        sa_update(Relationship)
                        .where(Relationship.target_entity_id == shorter.id)
                        .values(target_entity_id=longer.id)
                    )
                    # Deactivate the shorter/partial entity
                    shorter.active = False
                    shorter.last_seen = datetime.now(timezone.utc)
                    deactivated_ids.add(shorter.id)
                    merged.append({"removed": shorter.name, "kept": longer.name})

        await db.commit()

    # Rebuild in-memory graph to reflect the merge
    await kg.init_graph(user_id=user_id)

    return {"merged": merged, "merge_count": len(merged)}


# ---------------------------------------------------------------------------
# WebSocket chat
# ---------------------------------------------------------------------------

_extraction_tracker: dict[str, float] = {}
_EXTRACTION_COOLDOWN_SECS = 300
_MID_SESSION_EXTRACT_EVERY = 10


@app.websocket("/ws/{session_id}")
async def ws_chat(ws: WebSocket, session_id: str):
    await ws.accept()
    user_id = "default"

    await memory.ensure_session_exists(session_id, user_id)

    messages = await memory.load_messages(session_id)
    turn_count = 0

    try:
        while True:
            data = await ws.receive_text()
            payload = json.loads(data)
            user_text = payload.get("content", "").strip()
            if not user_text:
                continue

            messages.append({"role": "user", "content": user_text})
            turn_count += 1

            if len([m for m in messages if m["role"] == "user"]) == 1:
                title = user_text[:80]
                await memory.update_session_title(session_id, title)

            fact_strings = await memory.retrieve_relevant_facts(user_id, user_text)

            async for event in run_turn(messages, memory_facts=fact_strings, session_id=session_id, user_id=user_id):
                if isinstance(event, AgentTextDelta):
                    await ws.send_json({"type": "text", "content": event.text})
                elif isinstance(event, AgentThinking):
                    await ws.send_json({"type": "thinking", "content": event.text})
                elif isinstance(event, AgentToolGenerating):
                    await ws.send_json({
                        "type": "tool_generating",
                        "name": event.name,
                    })
                elif isinstance(event, AgentToolProgress):
                    payload = {
                        "type": "tool_progress",
                        "name": event.name,
                        "content": event.content,
                        "raw": event.raw_delta,
                    }
                    if event.metadata:
                        payload["metadata"] = event.metadata
                    await ws.send_json(payload)
                elif isinstance(event, AgentToolStart):
                    await ws.send_json({
                        "type": "tool_start",
                        "name": event.name,
                        "arguments": event.arguments,
                    })
                elif isinstance(event, AgentToolResult):
                    await ws.send_json({
                        "type": "tool_result",
                        "name": event.name,
                        "result": event.result[:2000],
                    })
                elif isinstance(event, AgentArtifact):
                    await ws.send_json({
                        "type": "artifact",
                        "artifact": event.artifact,
                    })
                elif isinstance(event, AgentDone):
                    await ws.send_json({"type": "done"})

            await memory.save_messages(session_id, messages)

            if (
                turn_count > 0
                and turn_count % _MID_SESSION_EXTRACT_EVERY == 0
            ):
                asyncio.create_task(
                    _extract_if_worthwhile(session_id, user_id, reason="mid-session")
                )

    except WebSocketDisconnect:
        await memory.save_messages(session_id, messages)
        asyncio.create_task(
            _extract_if_worthwhile(session_id, user_id, reason="disconnect")
        )
    except anthropic.APIStatusError as e:
        await memory.save_messages(session_id, messages)
        asyncio.create_task(
            _extract_if_worthwhile(session_id, user_id, reason="error")
        )
        is_overloaded = (
            e.status_code == 529
            or "overloaded" in str(e).lower()
        )
        if is_overloaded:
            log.warning("Anthropic overloaded for session %s (retries exhausted)", session_id)
            msg = "Anthropic's servers are currently overloaded. Please wait a moment and try again."
        else:
            log.exception("Anthropic API error for session %s: %s", session_id, e)
            msg = f"API error ({e.status_code}): {e.message}"
        try:
            await ws.send_json({"type": "error", "content": msg})
        except Exception:
            pass
    except Exception as e:
        log.exception("WebSocket error for session %s", session_id)
        await memory.save_messages(session_id, messages)
        asyncio.create_task(
            _extract_if_worthwhile(session_id, user_id, reason="error")
        )
        try:
            await ws.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass


async def _extract_if_worthwhile(
    session_id: str,
    user_id: str,
    *,
    reason: str = "unknown",
) -> None:
    """Run fact extraction in background. Guards against redundant runs."""
    last_run = _extraction_tracker.get(session_id, 0)
    if time.time() - last_run < _EXTRACTION_COOLDOWN_SECS:
        log.debug(
            "Skipping extraction for %s (%s): cooldown active (%.0fs remaining)",
            session_id, reason, _EXTRACTION_COOLDOWN_SECS - (time.time() - last_run),
        )
        return

    try:
        msgs = await memory.load_messages(session_id)
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        total_content_len = sum(
            len(m.get("content", "")) if isinstance(m.get("content"), str)
            else len(json.dumps(m.get("content", "")))
            for m in msgs
        )

        if len(user_msgs) < 2 or total_content_len < 200:
            log.debug(
                "Skipping extraction for %s (%s): too little content (%d msgs, %d chars)",
                session_id, reason, len(user_msgs), total_content_len,
            )
            return

        log.info(
            "Starting extraction for %s (%s): %d user msgs, %d total chars",
            session_id, reason, len(user_msgs), total_content_len,
        )
        _extraction_tracker[session_id] = time.time()

        extracted = await memory.extract_facts_from_session(session_id, user_id)
        if extracted:
            log.info(
                "Extracted %d actions from session %s (%s)",
                len(extracted), session_id, reason,
            )
    except Exception:
        log.exception("Background extraction failed for session %s (%s)", session_id, reason)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=settings.HOST,
        port=settings.PORT,
    )
