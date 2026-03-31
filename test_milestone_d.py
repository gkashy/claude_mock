"""
Milestone D verification: persistent sessions, fact storage, and LLM extraction.
Run: python test_milestone_d.py
"""

import asyncio
import os

import memory
from tools import autodiscover, dispatch

autodiscover()


async def test_session_persistence():
    print("[1] Session persistence")
    print("-" * 40)

    session_id = await memory.create_session(user_id="test_user", title="Test Session")
    print(f"    Created session: {session_id}")

    messages = [
        {"role": "user", "content": "My name is Gaurav and I'm building a recruiting platform."},
        {"role": "assistant", "content": "Nice to meet you, Gaurav! That sounds like an exciting project."},
        {"role": "user", "content": "I prefer Python over JavaScript for the backend."},
        {"role": "assistant", "content": "Python is a great choice for backend development."},
    ]
    await memory.save_messages(session_id, messages)
    print(f"    Saved {len(messages)} messages")

    loaded = await memory.load_messages(session_id)
    print(f"    Loaded {len(loaded)} messages back")
    assert len(loaded) == len(messages), f"Expected {len(messages)}, got {len(loaded)}"

    for orig, loaded_msg in zip(messages, loaded):
        assert orig["role"] == loaded_msg["role"], "Role mismatch"
    print("    Roles match across save/load cycle")

    sessions = await memory.list_sessions(user_id="test_user")
    assert any(s["id"] == session_id for s in sessions), "Session not in list"
    print(f"    Session appears in list ({len(sessions)} total)")

    await memory.update_session_title(session_id, "Recruiting Platform Chat")
    sessions = await memory.list_sessions(user_id="test_user")
    updated = next(s for s in sessions if s["id"] == session_id)
    assert updated["title"] == "Recruiting Platform Chat"
    print(f"    Title updated to: {updated['title']}")

    print("    PASSED\n")
    return session_id


async def test_fact_operations():
    print("[2] Fact CRUD operations")
    print("-" * 40)

    fid = await memory.save_fact("test_user", "User's name is Gaurav")
    print(f"    Saved fact: {fid}")

    fid2 = await memory.save_fact("test_user", "User prefers Python for backend")
    print(f"    Saved fact: {fid2}")

    all_facts = await memory.load_facts("test_user")
    print(f"    All facts: {len(all_facts)}")
    assert len(all_facts) >= 2

    search = await memory.search_facts("test_user", "Python")
    print(f"    Search 'Python': {len(search)} results")
    assert len(search) >= 1

    deleted = await memory.delete_fact(fid)
    assert deleted, "Should have deleted"
    remaining = await memory.load_facts("test_user")
    assert not any(f["id"] == fid for f in remaining), "Fact should be gone"
    print(f"    Deleted fact {fid}, {len(remaining)} remaining")

    print("    PASSED\n")


async def test_memory_tools_wired():
    print("[3] Memory tools (wired to SQLite)")
    print("-" * 40)

    result = await dispatch("remember_fact", {"fact": "User likes iterative development"})
    print(f"    remember: {result}")
    assert "Remembered" in result

    result = await dispatch("recall_facts", {"query": "iterative"})
    print(f"    recall: {result}")
    assert "iterative" in result

    fact_id = result.split("[")[1].split("]")[0]
    result = await dispatch("forget_fact", {"fact_id": fact_id})
    print(f"    forget: {result}")
    assert "Forgotten" in result

    print("    PASSED\n")


async def test_fact_extraction(session_id: str):
    print("[4] LLM fact extraction")
    print("-" * 40)

    extracted = await memory.extract_facts_from_session(session_id, user_id="test_user")
    print(f"    Extracted {len(extracted)} facts:")
    for f in extracted:
        print(f"      - {f}")

    assert len(extracted) > 0, "Should have extracted at least one fact"

    stored = await memory.load_facts("test_user")
    print(f"    Total facts in DB: {len(stored)}")

    print("    PASSED\n")


async def main():
    await memory.init_db()

    print("=" * 50)
    print("Milestone D -- Persistent Memory")
    print("=" * 50 + "\n")

    session_id = await test_session_persistence()
    await test_fact_operations()
    await test_memory_tools_wired()
    await test_fact_extraction(session_id)

    print("=" * 50)
    print("MILESTONE D: ALL TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
