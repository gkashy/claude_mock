"""One-time cleanup: strip emojis from all existing entity names in the DB."""
import asyncio
import re
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://agent:agent_dev@localhost:5432/agent_chat"
engine = create_async_engine(DATABASE_URL, echo=False)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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


def strip_emojis(s: str) -> str:
    return _EMOJI_RE.sub("", s).strip()


async def clean():
    async with Session() as db:
        result = await db.execute(text("SELECT id, name, name_lower FROM kg_entities"))
        rows = result.fetchall()
        updated = 0
        for row in rows:
            eid, name, name_lower = row
            clean_name = strip_emojis(name)
            clean_lower = strip_emojis(name_lower)
            if clean_name != name or clean_lower != name_lower:
                print(f"  [{eid}] '{name}' -> '{clean_name}'")
                await db.execute(
                    text("UPDATE kg_entities SET name = :n, name_lower = :nl WHERE id = :id"),
                    {"n": clean_name, "nl": clean_lower, "id": eid},
                )
                updated += 1
        await db.commit()
        print(f"Updated {updated} entity/entities.")
    await engine.dispose()


asyncio.run(clean())
