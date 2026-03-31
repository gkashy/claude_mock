"""
Milestone C verification: test all tools independently (no LLM needed).
Run: python test_milestone_c.py
"""

import asyncio
from tools import autodiscover, get_all_definitions, dispatch

autodiscover()


async def main():
    defs = get_all_definitions()
    print(f"Registered tools: {[d['name'] for d in defs]}")
    print("=" * 50)

    # 1. datetime
    print("\n[1] get_current_datetime")
    result = await dispatch("get_current_datetime", {"timezone": "Europe/London"})
    print(f"    {result}")
    assert "Current time" in result, "datetime tool failed"

    # 2. web_search
    print("\n[2] web_search")
    result = await dispatch("web_search", {"query": "Python programming language", "max_results": 2})
    print(f"    {result[:300]}...")
    assert "Error" not in result or "No results" not in result, "web_search tool failed"

    # 3. execute_python
    print("\n[3] execute_python")
    result = await dispatch("execute_python", {"code": "print(sum(range(101)))"})
    print(f"    {result}")
    assert "5050" in result, "code_exec tool failed"

    # 4. write_file
    print("\n[4] write_file")
    result = await dispatch("write_file", {"path": "test_output.txt", "content": "Hello from Milestone C!"})
    print(f"    {result}")
    assert "Written" in result, "write_file tool failed"

    # 5. read_file
    print("\n[5] read_file")
    result = await dispatch("read_file", {"path": "test_output.txt"})
    print(f"    {result}")
    assert "Hello from Milestone C!" in result, "read_file tool failed"

    # 6. list_files
    print("\n[6] list_files")
    result = await dispatch("list_files", {})
    print(f"    {result}")
    assert "test_output.txt" in result, "list_files tool failed"

    # 7. remember_fact
    print("\n[7] remember_fact")
    result = await dispatch("remember_fact", {"fact": "User prefers Python over JavaScript"})
    print(f"    {result}")
    assert "Remembered" in result, "remember_fact tool failed"

    # 8. recall_facts
    print("\n[8] recall_facts")
    result = await dispatch("recall_facts", {"query": "Python"})
    print(f"    {result}")
    assert "Python" in result, "recall_facts tool failed"

    # 9. forget_fact (extract id from recall result)
    print("\n[9] forget_fact")
    fact_id = result.split("[")[1].split("]")[0]
    result = await dispatch("forget_fact", {"fact_id": fact_id})
    print(f"    {result}")
    assert "Forgotten" in result, "forget_fact tool failed"

    # 10. Verify forgotten
    result = await dispatch("recall_facts", {"query": "Python"})
    assert "No facts found" in result, "fact should have been forgotten"
    print(f"    Verified: fact is gone")

    # 11. Path traversal guard
    print("\n[10] Security: path traversal blocked")
    result = await dispatch("read_file", {"path": "../../etc/passwd"})
    print(f"    {result}")
    assert "escapes" in result or "not found" in result.lower() or "Error" in result, "path traversal should be blocked"

    print("\n" + "=" * 50)
    print("MILESTONE C: ALL TOOLS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
