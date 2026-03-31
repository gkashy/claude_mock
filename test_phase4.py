"""Phase 4 smoke test: verify html_css skill loads correctly."""
import asyncio
import sys
sys.path.insert(0, ".")

from tools import autodiscover, dispatch


async def main():
    autodiscover()

    result = await dispatch("load_skill", {"name": "html_css"})
    assert "=== SKILL: html_css ===" in result
    assert "Typography" in result
    assert "Layout" in result
    assert "Spacing System" in result
    assert "Print Styles" in result
    assert "Common Pitfalls" in result
    assert "create_artifact" in result
    print(f"OK - html_css skill loaded ({len(result)} chars)")

    # Verify all 3 skills now load
    for name in ("docx_writing", "web_search", "html_css"):
        r = await dispatch("load_skill", {"name": name})
        assert "=== SKILL:" in r, f"{name} failed to load"
    print("OK - all 3 skills load successfully")

    print("\n--- Phase 4 PASSED ---")


if __name__ == "__main__":
    asyncio.run(main())
