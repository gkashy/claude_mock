"""Phase 5 smoke test: verify all 6 skills load correctly."""
import asyncio
import sys
sys.path.insert(0, ".")

from tools import autodiscover, dispatch


ALL_SKILLS = [
    "docx_writing",
    "web_search",
    "html_css",
    "code_generation",
    "data_analysis",
    "presentation",
]


async def main():
    autodiscover()

    result = await dispatch("list_skills", {})
    for name in ALL_SKILLS:
        assert name in result, f"{name} missing from list_skills output"
    print(f"OK - all {len(ALL_SKILLS)} skills listed in registry")

    for name in ALL_SKILLS:
        result = await dispatch("load_skill", {"name": name})
        assert f"=== SKILL: {name} ===" in result, f"{name} header missing"
        assert "Common Pitfalls" in result, f"{name} missing Common Pitfalls section"
        assert "Workflow" in result or "Narrative Structure" in result, f"{name} missing workflow/structure"
        print(f"  OK - {name} ({len(result)} chars)")

    result = await dispatch("load_skill", {"name": "nonexistent"})
    assert "Error" in result
    print("  OK - nonexistent skill returns error")

    print(f"\n--- Phase 5 PASSED ({len(ALL_SKILLS)} skills operational) ---")


if __name__ == "__main__":
    asyncio.run(main())
