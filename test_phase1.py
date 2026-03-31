"""Phase 1 smoke test: verify skill tools load and dispatch correctly."""
import asyncio
import sys
sys.path.insert(0, ".")

from tools import autodiscover, get_all_definitions, dispatch


async def main():
    autodiscover()
    defs = get_all_definitions()
    skill_names = {d["name"] for d in defs}

    assert "list_skills" in skill_names, "list_skills not registered"
    assert "load_skill" in skill_names, "load_skill not registered"
    print("OK - list_skills and load_skill registered")

    result = await dispatch("list_skills", {})
    assert "docx_resume" in result
    assert "web_research" in result
    assert "html_document" in result
    print(f"OK - list_skills output:\n{result}")

    result = await dispatch("load_skill", {"name": "docx_resume"})
    assert "Error: skill 'docx_resume' not found" in result
    print("OK - load_skill returns not-found for missing skill file (expected until Phase 2)")

    result = await dispatch("load_skill", {"name": "nonexistent"})
    assert "Error: skill 'nonexistent' not found" in result
    print("OK - load_skill returns error for unknown skill")

    print("\n--- Phase 1 PASSED ---")


if __name__ == "__main__":
    asyncio.run(main())
