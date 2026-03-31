"""
Persistent memory layer backed by PostgreSQL via async SQLAlchemy.

Two stores:
  1. Session memory   -- full message history per session (episodic)
  2. Fact memory       -- extracted cross-session facts (semantic)

Plus an LLM-driven extraction pass that distills sessions into facts.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, String, Text, Integer, Boolean, DateTime, ForeignKey, Index,
    select, delete, text,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import settings


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, default="default")
    title = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)  # JSON-encoded
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Fact(Base):
    __tablename__ = "facts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    user_id = Column(String, nullable=False, default="default", index=True)
    content = Column(Text, nullable=False)
    source_session_id = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default=text('true'), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, index=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    title = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    language = Column(String, nullable=True, default="")
    filename = Column(String, nullable=False)
    current_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_artifacts_session_user", "session_id", "user_id"),
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    artifact_id = Column(String, ForeignKey("artifacts.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    title = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    language = Column(String, nullable=True, default="")
    filename = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_artver_artifact_version", "artifact_id", "version", unique=True),
    )


# ---------------------------------------------------------------------------
# Database engine (module-level singleton)
# ---------------------------------------------------------------------------

_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_async_session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# Session memory operations
# ---------------------------------------------------------------------------

async def create_session(user_id: str = "default", title: str | None = None) -> str:
    session_id = str(uuid.uuid4())
    async with _async_session() as db:
        db.add(Session(id=session_id, user_id=user_id, title=title))
        await db.commit()
    return session_id


async def ensure_session_exists(session_id: str, user_id: str = "default") -> None:
    async with _async_session() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        if not result.scalar_one_or_none():
            db.add(Session(id=session_id, user_id=user_id))
            await db.commit()


async def list_sessions(user_id: str = "default") -> list[dict]:
    async with _async_session() as db:
        result = await db.execute(
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.updated_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "title": r.title or "(untitled)",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


async def save_messages(session_id: str, messages: list[dict]) -> None:
    """Overwrite all messages for a session (simple full-replace strategy)."""
    async with _async_session() as db:
        await db.execute(delete(Message).where(Message.session_id == session_id))
        for msg in messages:
            db.add(Message(
                session_id=session_id,
                role=msg.get("role", "unknown"),
                content=json.dumps(msg.get("content", ""), ensure_ascii=False),
            ))
        # Update session timestamp
        result = await db.execute(select(Session).where(Session.id == session_id))
        sess = result.scalar_one_or_none()
        if sess:
            sess.updated_at = datetime.now(timezone.utc)
        await db.commit()


async def load_messages(session_id: str) -> list[dict]:
    async with _async_session() as db:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at)
        )
        rows = result.scalars().all()
        messages = []
        for r in rows:
            try:
                content = json.loads(r.content)
            except json.JSONDecodeError:
                content = r.content
            messages.append({"role": r.role, "content": content})
        return messages


async def update_session_title(session_id: str, title: str) -> None:
    async with _async_session() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        sess = result.scalar_one_or_none()
        if sess:
            sess.title = title
            await db.commit()


# ---------------------------------------------------------------------------
# Fact memory operations (dual-write: PostgreSQL + Qdrant)
# ---------------------------------------------------------------------------

import logging as _logging
_log = _logging.getLogger(__name__)


async def save_fact(
    user_id: str,
    content: str,
    source_session_id: str | None = None,
    *,
    _embedding=None,
) -> str:
    """Save a fact to PostgreSQL and its embedding to Qdrant.

    If ``_embedding`` is provided it is used directly, avoiding an extra
    OpenAI API call (used by the dedup path which already computed it).
    """
    import vector_store
    from embeddings import embed_text

    fact_id = str(uuid.uuid4())[:8]

    async with _async_session() as db:
        db.add(Fact(
            id=fact_id,
            user_id=user_id,
            content=content,
            source_session_id=source_session_id,
            active=True,
        ))
        await db.commit()

    try:
        embedding = _embedding if _embedding is not None else await embed_text(content)
        await vector_store.upsert_fact(
            fact_id=fact_id,
            embedding=embedding,
            payload={"user_id": user_id, "content": content},
        )
    except Exception:
        _log.warning("Failed to embed fact %s; Qdrant out of sync", fact_id, exc_info=True)

    return fact_id


async def update_fact(fact_id: str, new_content: str, *, _embedding=None) -> bool:
    """Update a fact's content in PG and re-embed into Qdrant.

    If ``_embedding`` is provided it is reused directly.
    """
    import vector_store
    from embeddings import embed_text

    async with _async_session() as db:
        result = await db.execute(select(Fact).where(Fact.id == fact_id))
        fact = result.scalar_one_or_none()
        if not fact:
            return False
        fact.content = new_content
        fact.updated_at = datetime.now(timezone.utc)
        user_id = fact.user_id
        await db.commit()

    try:
        embedding = _embedding if _embedding is not None else await embed_text(new_content)
        await vector_store.upsert_fact(
            fact_id=fact_id,
            embedding=embedding,
            payload={"user_id": user_id, "content": new_content},
        )
    except Exception:
        _log.warning("Failed to re-embed fact %s", fact_id, exc_info=True)

    return True


_DEDUP_SIMILARITY_THRESHOLD = 0.85


async def save_fact_with_dedup(
    user_id: str,
    content: str,
    source_session_id: str | None = None,
) -> tuple[str, str]:
    """Save a fact with semantic dedup guard.

    Embeds the content once, searches Qdrant for near-duplicates.
    If a highly similar fact exists (>= threshold), updates it instead.

    Returns:
        (fact_id, action) where action is "created", "updated", or "created_no_embed".
    """
    from embeddings import embed_text

    try:
        embedding = await embed_text(content)
    except Exception:
        _log.warning("Embedding failed during dedup; saving without dedup", exc_info=True)
        fact_id = await save_fact(user_id, content, source_session_id)
        return fact_id, "created_no_embed"

    results = await semantic_search_facts(user_id, embedding, top_k=3)

    for r in results:
        if r.get("score", 0) >= _DEDUP_SIMILARITY_THRESHOLD:
            existing_content = r["content"]
            if existing_content.strip() == content.strip():
                _log.info("Exact duplicate fact skipped (id=%s)", r["id"])
                return r["id"], "duplicate"

            _log.info(
                "Dedup: updating fact %s (score=%.3f) instead of creating new",
                r["id"], r["score"],
            )
            await update_fact(r["id"], content, _embedding=embedding)
            return r["id"], "updated"

    fact_id = await save_fact(user_id, content, source_session_id, _embedding=embedding)
    return fact_id, "created"


async def deactivate_fact(fact_id: str) -> bool:
    """Soft-delete: set active=False in PG and remove vector from Qdrant."""
    import vector_store

    async with _async_session() as db:
        result = await db.execute(select(Fact).where(Fact.id == fact_id))
        fact = result.scalar_one_or_none()
        if not fact:
            return False
        fact.active = False
        fact.updated_at = datetime.now(timezone.utc)
        await db.commit()

    try:
        await vector_store.delete_fact(fact_id)
    except Exception:
        _log.warning("Failed to delete fact %s from Qdrant", fact_id, exc_info=True)

    return True


async def load_facts(user_id: str = "default") -> list[dict]:
    """Load all active facts for a user from PostgreSQL."""
    async with _async_session() as db:
        result = await db.execute(
            select(Fact)
            .where(Fact.user_id == user_id)
            .where(Fact.active == True)
            .order_by(Fact.created_at)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


async def semantic_search_facts(
    user_id: str,
    query_embedding,
    top_k: int = 15,
) -> list[dict]:
    """Search facts by vector similarity via Qdrant, enrich from PG."""
    import vector_store

    results = await vector_store.search(
        query_embedding=query_embedding,
        top_k=top_k,
        user_id=user_id,
    )

    if not results:
        return []

    fact_ids = [r["id"] for r in results]
    score_map = {r["id"]: r["score"] for r in results}

    async with _async_session() as db:
        result = await db.execute(
            select(Fact).where(
                Fact.id.in_(fact_ids),
                Fact.active == True,
            )
        )
        rows = {r.id: r for r in result.scalars().all()}

    enriched = []
    for fid in fact_ids:
        row = rows.get(fid)
        if not row:
            continue
        enriched.append({
            "id": row.id,
            "content": row.content,
            "score": score_map.get(fid, 0.0),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })

    return enriched


_MIN_RELEVANCE_SCORE = 0.25


async def retrieve_relevant_facts(
    user_id: str,
    query: str,
    top_k: int | None = None,
    token_budget: int | None = None,
) -> list[str]:
    """Embed the user query, retrieve top-K facts from Qdrant, trim to token budget.

    Returns a list of fact content strings ordered by relevance (highest first).
    Gracefully degrades: returns empty list on embedding failure or empty store.
    """
    from embeddings import embed_text

    if top_k is None:
        top_k = settings.MEMORY_TOP_K
    if token_budget is None:
        token_budget = settings.MEMORY_TOKEN_BUDGET

    try:
        query_embedding = await embed_text(query)
    except Exception:
        _log.warning("Failed to embed query for retrieval; returning no facts", exc_info=True)
        return []

    results = await semantic_search_facts(user_id, query_embedding, top_k=top_k)
    if not results:
        return []

    budget_chars = token_budget * 4
    selected: list[str] = []
    used_chars = 0

    for r in results:
        if r.get("score", 0) < _MIN_RELEVANCE_SCORE:
            continue
        content = r["content"]
        if used_chars + len(content) > budget_chars:
            break
        selected.append(content)
        used_chars += len(content)

    _log.info(
        "Retrieved %d/%d facts (%.0f chars, budget %d tokens) for query: %.60s...",
        len(selected), len(results), used_chars, token_budget, query,
    )
    return selected


async def recall_facts_smart(
    user_id: str,
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """Semantic search with ILIKE fallback. Returns dicts with id, content, score.

    Primary path: embed query -> Qdrant similarity search.
    Fallback: PostgreSQL ILIKE (score set to 0.0).
    """
    from embeddings import embed_text

    try:
        query_embedding = await embed_text(query)
        results = await semantic_search_facts(user_id, query_embedding, top_k=top_k)
        if results:
            return results
    except Exception:
        _log.warning("Semantic recall failed; falling back to ILIKE", exc_info=True)

    return await _ilike_search_facts(user_id, query)


async def _ilike_search_facts(user_id: str, query: str) -> list[dict]:
    """Keyword search via PostgreSQL ILIKE (fallback)."""
    async with _async_session() as db:
        result = await db.execute(
            select(Fact)
            .where(Fact.user_id == user_id)
            .where(Fact.active == True)
            .where(Fact.content.ilike(f"%{query}%"))
            .order_by(Fact.created_at)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "content": r.content,
                "score": 0.0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def search_facts(user_id: str, query: str) -> list[dict]:
    """Legacy keyword search -- delegates to _ilike_search_facts."""
    return await _ilike_search_facts(user_id, query)


async def delete_fact(fact_id: str) -> bool:
    """Hard delete a fact from PG and Qdrant (used by UI delete button)."""
    import vector_store

    async with _async_session() as db:
        result = await db.execute(select(Fact).where(Fact.id == fact_id))
        fact = result.scalar_one_or_none()
        if not fact:
            return False
        await db.delete(fact)
        await db.commit()

    try:
        await vector_store.delete_fact(fact_id)
    except Exception:
        _log.warning("Failed to delete fact %s from Qdrant", fact_id, exc_info=True)

    return True


# ---------------------------------------------------------------------------
# Artifact operations
# ---------------------------------------------------------------------------

async def create_artifact(
    session_id: str,
    user_id: str,
    title: str,
    content_type: str,
    filename: str,
    content: str,
    language: str = "",
) -> dict:
    """Create a new artifact with its first version. Returns the full artifact dict."""
    artifact_id = str(uuid.uuid4())[:8]
    version_id = str(uuid.uuid4())

    async with _async_session() as db:
        db.add(Artifact(
            id=artifact_id,
            session_id=session_id,
            user_id=user_id,
            title=title,
            content_type=content_type,
            language=language,
            filename=filename,
            current_version=1,
        ))
        db.add(ArtifactVersion(
            id=version_id,
            artifact_id=artifact_id,
            version=1,
            content=content,
            title=title,
            content_type=content_type,
            language=language,
            filename=filename,
        ))
        await db.commit()

    return {
        "id": artifact_id,
        "title": title,
        "content_type": content_type,
        "language": language,
        "filename": filename,
        "content": content,
        "version": 1,
        "session_id": session_id,
    }


async def update_artifact(
    artifact_id: str,
    content: str,
    title: str | None = None,
    content_type: str | None = None,
    language: str | None = None,
    filename: str | None = None,
) -> dict | None:
    """Create a new version of an existing artifact. Returns the updated artifact dict or None."""
    async with _async_session() as db:
        result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
        artifact = result.scalar_one_or_none()
        if not artifact:
            return None

        new_version = artifact.current_version + 1
        new_title = title or artifact.title
        new_ct = content_type or artifact.content_type
        new_lang = language if language is not None else artifact.language
        new_fn = filename or artifact.filename

        artifact.current_version = new_version
        artifact.title = new_title
        artifact.content_type = new_ct
        artifact.language = new_lang
        artifact.filename = new_fn
        artifact.updated_at = datetime.now(timezone.utc)

        db.add(ArtifactVersion(
            id=str(uuid.uuid4()),
            artifact_id=artifact_id,
            version=new_version,
            content=content,
            title=new_title,
            content_type=new_ct,
            language=new_lang,
            filename=new_fn,
        ))
        await db.commit()

    return {
        "id": artifact_id,
        "title": new_title,
        "content_type": new_ct,
        "language": new_lang,
        "filename": new_fn,
        "content": content,
        "version": new_version,
    }


async def get_artifact(artifact_id: str, version: int | None = None) -> dict | None:
    """Load an artifact. If version is None, loads the latest version."""
    async with _async_session() as db:
        result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
        artifact = result.scalar_one_or_none()
        if not artifact:
            return None

        target_version = version or artifact.current_version

        ver_result = await db.execute(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact_id,
                ArtifactVersion.version == target_version,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if not ver:
            return None

        return {
            "id": artifact.id,
            "title": ver.title,
            "content_type": ver.content_type,
            "language": ver.language,
            "filename": ver.filename,
            "content": ver.content,
            "version": ver.version,
            "total_versions": artifact.current_version,
            "session_id": artifact.session_id,
            "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
            "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
        }


async def list_artifacts_for_session(session_id: str) -> list[dict]:
    """List all artifacts in a session (metadata only, no content)."""
    async with _async_session() as db:
        result = await db.execute(
            select(Artifact)
            .where(Artifact.session_id == session_id)
            .order_by(Artifact.created_at)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "content_type": r.content_type,
                "language": r.language,
                "filename": r.filename,
                "current_version": r.current_version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


async def list_artifact_versions(artifact_id: str) -> list[dict]:
    """List all versions for an artifact (metadata + content)."""
    async with _async_session() as db:
        result = await db.execute(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact_id)
            .order_by(ArtifactVersion.version)
        )
        rows = result.scalars().all()
        return [
            {
                "version": r.version,
                "title": r.title,
                "content_type": r.content_type,
                "language": r.language,
                "filename": r.filename,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Fact extraction (diff-aware LLM pass)
# ---------------------------------------------------------------------------

_DIFF_EXTRACTION_PROMPT = """\
You are a precise memory manager for an AI assistant.

## Existing facts
{existing_facts}

## New conversation transcript
{transcript}

## Your task
Compare the conversation against the existing facts and produce a JSON object with an "actions" array. Each action is one of:

- **ADD**: A new fact not already captured. Content must be a single concise sentence.
- **UPDATE**: An existing fact whose content is now outdated or incomplete. Provide the fact `id` and the corrected full content.
- **DELETE**: An existing fact that is now wrong, redundant, or explicitly contradicted. Provide the fact `id` and a brief reason.

### Rules
1. Only ADD facts that are genuinely new and worth remembering long-term.
2. Prefer UPDATE over ADD when a fact is a refinement of an existing one.
3. DELETE only when a fact is clearly wrong or superseded (not just unused).
4. If nothing changed, return {{"actions": []}}.
5. Do NOT re-add facts that already exist with equivalent meaning.
6. Focus on: user preferences, personal info, decisions, technical choices, ongoing plans.
7. Ignore: small talk, tool mechanics, transient context.

### Output format (strict JSON, no markdown fences)
{{"actions": [
  {{"action": "ADD", "content": "..."}},
  {{"action": "UPDATE", "id": "abc123", "content": "updated content here"}},
  {{"action": "DELETE", "id": "def456", "reason": "superseded by ..."}}
]}}
"""


def _build_transcript(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, list):
            text = " ".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)
        if text.strip():
            lines.append(f"{role}: {text[:500]}")
    return "\n".join(lines)


def _parse_extraction_response(raw: str) -> list[dict]:
    """Parse the LLM response into a list of action dicts, tolerating minor issues."""
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
        return parsed.get("actions", [])
    if isinstance(parsed, list):
        return parsed
    return []


async def extract_facts_from_session(
    session_id: str,
    user_id: str = "default",
) -> list[str]:
    """Diff-aware extraction: compares transcript against existing facts,
    then applies ADD/UPDATE/DELETE actions with semantic dedup."""
    from models import get_provider, TextDelta, Done

    messages = await load_messages(session_id)
    if not messages:
        return []

    transcript = _build_transcript(messages)
    if len(transcript) < 50:
        return []

    existing = await load_facts(user_id)
    if existing:
        facts_block = "\n".join(
            f"- [{f['id']}] {f['content']}" for f in existing
        )
    else:
        facts_block = "(none)"

    prompt_text = _DIFF_EXTRACTION_PROMPT.format(
        existing_facts=facts_block,
        transcript=transcript,
    )

    provider = get_provider()
    extraction_messages = [{"role": "user", "content": prompt_text}]

    response_text = ""
    async for event in provider.stream(
        system="You are a precise memory manager. Output only valid JSON.",
        messages=extraction_messages,
    ):
        if isinstance(event, TextDelta):
            response_text += event.text

    actions = _parse_extraction_response(response_text)
    if not actions:
        _log.info("Extraction produced no actions for session %s", session_id)
        return []

    applied: list[str] = []

    for action in actions:
        act = action.get("action", "").upper()
        content = action.get("content", "").strip()
        fact_id = action.get("id", "").strip()

        if act == "ADD" and content:
            fid, result = await save_fact_with_dedup(
                user_id, content, source_session_id=session_id
            )
            applied.append(f"ADD({result}): {content}")
            _log.info("Extraction ADD [%s] %s: %s", fid, result, content[:80])

        elif act == "UPDATE" and fact_id and content:
            ok = await update_fact(fact_id, content)
            if ok:
                applied.append(f"UPDATE({fact_id}): {content}")
                _log.info("Extraction UPDATE %s: %s", fact_id, content[:80])
            else:
                _log.warning("Extraction UPDATE failed: fact %s not found", fact_id)

        elif act == "DELETE" and fact_id:
            reason = action.get("reason", "")
            ok = await deactivate_fact(fact_id)
            if ok:
                applied.append(f"DELETE({fact_id}): {reason}")
                _log.info("Extraction DELETE %s: %s", fact_id, reason[:80])
            else:
                _log.warning("Extraction DELETE failed: fact %s not found", fact_id)

    _log.info("Extraction applied %d actions for session %s", len(applied), session_id)
    return applied
