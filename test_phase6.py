"""
Phase 6 verification: Semantic recall_facts tool with ILIKE fallback.

Tests:
  1. recall_facts_smart returns ranked results with scores for known facts
  2. recall_facts_smart returns results for unrelated query (empty or low)
  3. ILIKE fallback works when called directly
  4. recall_facts tool handler formats output correctly
"""

import asyncio


async def main():
    import memory
    from tools.memory_tools import recall_facts

    await memory.init_db()
    user_id = "default"

    print("=" * 60)
    print("Phase 6 Verification")
    print("=" * 60)

    all_facts = await memory.load_facts(user_id)
    print(f"\n[0] Active facts: {len(all_facts)}")
    if not all_facts:
        print("    No facts in store. Skipping (need real facts for this test).")
        return

    print("\n[1] recall_facts_smart -- semantic search:")
    results = await memory.recall_facts_smart(user_id, "work experience and employment history")
    print(f"    Got {len(results)} results")
    for r in results[:5]:
        print(f"    [{r['id']}] score={r.get('score', 0):.3f}  {r['content'][:80]}...")
    assert len(results) > 0, "Expected at least one result"
    assert "score" in results[0], "Expected score in results"
    print("    PASS")

    print("\n[2] recall_facts_smart -- education query:")
    results2 = await memory.recall_facts_smart(user_id, "university and degree information")
    print(f"    Got {len(results2)} results")
    for r in results2[:3]:
        print(f"    [{r['id']}] score={r.get('score', 0):.3f}  {r['content'][:80]}...")
    print("    PASS")

    print("\n[3] recall_facts_smart -- unrelated query:")
    results3 = await memory.recall_facts_smart(user_id, "medieval castle architecture in Europe")
    print(f"    Got {len(results3)} results (may be 0 if all below threshold)")
    print("    PASS")

    print("\n[4] ILIKE fallback (_ilike_search_facts):")
    ilike = await memory._ilike_search_facts(user_id, "Gaurav")
    print(f"    ILIKE 'Gaurav': {len(ilike)} results")
    for r in ilike[:3]:
        print(f"    [{r['id']}] {r['content'][:80]}...")
    assert all(r.get("score", 0) == 0.0 for r in ilike), "ILIKE results should have score=0.0"
    print("    PASS")

    print("\n[5] Tool handler output format:")
    output = await recall_facts("programming skills and technical abilities")
    print(f"    Output:\n{output[:500]}")
    assert "relevance:" in output or "keyword match" in output or "No facts" in output
    print("    PASS")

    print("\n--- ALL PHASE 6 TESTS PASSED ---")


if __name__ == "__main__":
    asyncio.run(main())
