"""Phase 2 smoke test: verify restructured registry + docx_writing skill loads."""
import asyncio
import sys
sys.path.insert(0, ".")

from tools import autodiscover, dispatch


async def main():
    autodiscover()

    result = await dispatch("list_skills", {})
    assert "docx_writing" in result, f"docx_writing not in list: {result}"
    assert "web_search" in result, f"web_search not in list: {result}"
    assert "html_css" in result, f"html_css not in list: {result}"
    assert "docx_resume" not in result, "Old task-specific name still present"
    print("OK - registry uses capability-oriented names")
    print(f"  {result}\n")

    result = await dispatch("load_skill", {"name": "docx_writing"})
    assert "=== SKILL: docx_writing ===" in result
    assert "python-docx" in result
    assert "Typography Principles" in result
    assert "Common Pitfalls" in result
    assert "execute_python" in result
    print(f"OK - docx_writing skill loaded ({len(result)} chars)")

    result = await dispatch("load_skill", {"name": "web_search"})
    assert "Error: skill 'web_search' not found" in result
    print("OK - web_search not yet written (expected, Phase 3)")

    print("\n--- Phase 2 PASSED ---")


if __name__ == "__main__":
    asyncio.run(main())
