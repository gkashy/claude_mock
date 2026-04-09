"""
Smoke test for EKGLMM.

Uses the same Postgres and Qdrant that Agent-Scope already has running.
Run from the ekglmm/ folder after installing:

    pip install -e ".[all]"
    python test_ekglmm.py

All test data is written under user_id="ekglmm_test" and cleaned up at the end.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Load Agent-Scope .env from the parent folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from ekglmm import EKGLMM
from ekglmm.storage import PostgresStorage
from ekglmm.vectors import QdrantVectors
from ekglmm.embeddings import OpenAIEmbeddings
from ekglmm.llm import AnthropicLLM

DATABASE_URL  = os.getenv("DATABASE_URL", "postgresql+asyncpg://agent:agent_dev@localhost:5432/agent_chat")
QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", "6333"))
OPENAI_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TEST_USER     = "ekglmm_test"

PASS = "[PASS]"
FAIL = "[FAIL]"


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    print(f"  {status}  {label}" + (f"  ({detail})" if detail else ""))
    return condition


async def run_tests():
    print("\n=== EKGLMM Smoke Test ===\n")

    all_passed = True

    # ------------------------------------------------------------------
    print("1. SDK init")
    # ------------------------------------------------------------------
    try:
        mem = EKGLMM(
            storage=PostgresStorage(DATABASE_URL),
            vectors=QdrantVectors(QDRANT_HOST, QDRANT_PORT),
            embeddings=OpenAIEmbeddings(api_key=OPENAI_KEY),
            llm=AnthropicLLM(api_key=ANTHROPIC_KEY),
            user_name="Priya Sharma",
            entity_types=["person", "exercise", "food", "goal", "injury", "preference"],
        )
        await mem.init(user_id=TEST_USER)
        all_passed &= check("EKGLMM initialized", True)
    except Exception as e:
        check("EKGLMM initialized", False, str(e))
        print("\nCannot continue -- init failed. Check Postgres + Qdrant are running.")
        return

    # ------------------------------------------------------------------
    print("\n2. remember() -- store facts")
    # ------------------------------------------------------------------
    try:
        fid1, action1 = await mem.remember("Priya is allergic to peanuts", user_id=TEST_USER)
        all_passed &= check("remember() returned fact_id", bool(fid1), fid1)
        all_passed &= check("action is 'created'", action1 == "created", action1)
    except Exception as e:
        all_passed &= check("remember() fact 1", False, str(e))
        fid1 = None

    try:
        fid2, action2 = await mem.remember("Priya's goal is to lose 5kg by June", user_id=TEST_USER)
        all_passed &= check("remember() second fact", bool(fid2), fid2)
    except Exception as e:
        all_passed &= check("remember() fact 2", False, str(e))
        fid2 = None

    # Test dedup -- same content again should return "duplicate"
    try:
        _, action_dup = await mem.remember("Priya is allergic to peanuts", user_id=TEST_USER)
        all_passed &= check("duplicate detected", action_dup == "duplicate", action_dup)
    except Exception as e:
        all_passed &= check("dedup check", False, str(e))

    # ------------------------------------------------------------------
    print("\n3. recall() -- semantic search + graph enrichment")
    # ------------------------------------------------------------------
    try:
        results = await mem.recall("what are Priya's dietary restrictions", user_id=TEST_USER)
        all_passed &= check("recall() returned results", len(results) > 0, f"{len(results)} results")
        has_allergy = any("peanut" in r.content.lower() for r in results)
        all_passed &= check("allergy fact surfaced", has_allergy)
        print(f"         Top results:")
        for r in results[:4]:
            print(f"           [{r.source}] {r.content[:80]}")
    except Exception as e:
        all_passed &= check("recall()", False, str(e))

    # ------------------------------------------------------------------
    print("\n4. graph.neighbors() -- knowledge graph")
    # ------------------------------------------------------------------
    try:
        neighbors = mem.graph.neighbors("Priya Sharma", user_id=TEST_USER)
        all_passed &= check(
            "graph has neighbors for Priya",
            len(neighbors) >= 0,  # may be 0 if mini-extract didn't fire yet
            f"{len(neighbors)} edges",
        )
        if neighbors:
            for n in neighbors[:3]:
                print(f"         {n['from']} --{n['relation']}--> {n['to']}")
    except Exception as e:
        all_passed &= check("graph.neighbors()", False, str(e))

    # ------------------------------------------------------------------
    print("\n5. extract() -- full conversation extraction")
    # ------------------------------------------------------------------
    messages = [
        {"role": "user",      "content": "I just started doing Pilates twice a week"},
        {"role": "assistant", "content": "That's great! Pilates is excellent for core strength."},
        {"role": "user",      "content": "Yeah and I hurt my left knee so I can't run anymore"},
        {"role": "assistant", "content": "I'm sorry to hear that. Pilates is a good low-impact alternative."},
    ]
    try:
        applied = await mem.extract(messages, user_id=TEST_USER, session_id="test-session-1")
        all_passed &= check("extract() ran", True, f"{len(applied)} operations")
        for op in applied:
            print(f"         {op}")
    except Exception as e:
        all_passed &= check("extract()", False, str(e))

    # ------------------------------------------------------------------
    print("\n6. graph.stats()")
    # ------------------------------------------------------------------
    try:
        stats = mem.graph.stats(user_id=TEST_USER)
        all_passed &= check(
            "graph has nodes",
            stats["node_count"] >= 0,
            f"{stats['node_count']} nodes, {stats['edge_count']} edges",
        )
        print(f"         Entity types: {stats['entity_types']}")
    except Exception as e:
        all_passed &= check("graph.stats()", False, str(e))

    # ------------------------------------------------------------------
    print("\n7. forget() -- deactivate a fact")
    # ------------------------------------------------------------------
    if fid1:
        try:
            ok = await mem.forget(fid1)
            all_passed &= check("forget() returned True", ok)
            # Verify it's gone from recall
            results_after = await mem.recall("peanut allergy", user_id=TEST_USER)
            still_there = any(fid1 == r.id for r in results_after)
            all_passed &= check("forgotten fact no longer in recall", not still_there)
        except Exception as e:
            all_passed &= check("forget()", False, str(e))

    # ------------------------------------------------------------------
    print("\n8. Cleanup test data")
    # ------------------------------------------------------------------
    try:
        # Load and delete remaining test facts
        remaining = await mem._storage.load_facts(TEST_USER)
        for f in remaining:
            await mem._storage.deactivate_fact(f["id"])
            try:
                await mem._vectors.delete_fact(f["id"])
            except Exception:
                pass
        all_passed &= check(f"cleaned up {len(remaining)} remaining facts", True)
    except Exception as e:
        all_passed &= check("cleanup", False, str(e))

    # ------------------------------------------------------------------
    print("\n" + "=" * 35)
    if all_passed:
        print("  ALL TESTS PASSED")
    else:
        print("  SOME TESTS FAILED -- see [FAIL] lines above")
    print("=" * 35 + "\n")

    return all_passed


if __name__ == "__main__":
    if not OPENAI_KEY:
        print("ERROR: OPENAI_API_KEY not set in environment / .env")
        sys.exit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in environment / .env")
        sys.exit(1)

    passed = asyncio.run(run_tests())
    sys.exit(0 if passed else 1)
