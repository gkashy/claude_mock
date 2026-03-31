"""
FastAPI server: WebSocket chat streaming + REST endpoints for sessions and memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

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
