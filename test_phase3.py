"""Phase 3 smoke test: verify web_search skill loads correctly."""
import asyncio
import sys
sys.path.insert(0, ".")

from tools import autodiscover, dispatch


async def main():
    autodiscover()

    result = await dispatch("load_skill", {"name": "web_search"})
    assert "=== SKILL: web_search ===" in result
    assert "Query Formulation" in result
    assert "Source Evaluation" in result
    assert "Synthesis" in result
    assert "Common Pitfalls" in result
    assert "web_search" in result
    assert "max_results" in result
    print(f"OK - web_search skill loaded ({len(result)} chars)")

    print("\n--- Phase 3 PASSED ---")


if __name__ == "__main__":
    asyncio.run(main())
