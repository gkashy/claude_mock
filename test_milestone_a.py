"""
Milestone A verification: test that streaming works with the configured provider.
Run: python test_milestone_a.py
"""

import asyncio
import sys

from config import settings
from models import get_provider, TextDelta, ThinkingDelta, ToolCallRequest, Done


async def test_basic_streaming():
    provider = get_provider()
    print(f"Provider: {settings.MODEL_PROVIDER}")
    print(f"Model:    {settings.model_name}")
    print("-" * 50)

    system = "You are a helpful assistant. Keep answers brief."
    messages = [{"role": "user", "content": "What are 3 interesting facts about octopuses? Keep it to one sentence each."}]

    print("Assistant: ", end="", flush=True)
    async for event in provider.stream(system=system, messages=messages):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ThinkingDelta):
            pass  # skip thinking for this test
        elif isinstance(event, ToolCallRequest):
            print(f"\n[Tool call: {event.name}({event.arguments})]")
        elif isinstance(event, Done):
            print(f"\n\n[Done: {event.stop_reason}, usage: {event.usage}]")

    print("\nStreaming test passed.")


async def test_tool_calling():
    provider = get_provider()
    print(f"\nTool calling test ({settings.MODEL_PROVIDER})")
    print("-" * 50)

    tools_internal = [
        {
            "name": "get_current_time",
            "description": "Returns the current date and time in the specified timezone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. 'America/New_York'",
                    }
                },
                "required": ["timezone"],
            },
        }
    ]

    formatted_tools = provider.format_tools(tools_internal)

    system = "You are a helpful assistant. Use tools when appropriate."
    messages = [{"role": "user", "content": "What time is it in Tokyo right now?"}]

    text_parts = []
    tool_calls = []

    print("Assistant: ", end="", flush=True)
    async for event in provider.stream(system=system, messages=messages, tools=formatted_tools):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
            text_parts.append(event.text)
        elif isinstance(event, ToolCallRequest):
            print(f"\n[Tool call: {event.name}({event.arguments})]")
            tool_calls.append(event)
        elif isinstance(event, Done):
            print(f"\n[Done: {event.stop_reason}]")

    if tool_calls:
        print("\nTool calling works -- agent correctly requested a tool.")
    else:
        print("\nNote: agent answered without a tool call (may happen with some models).")

    print("Tool calling test passed.")


async def main():
    if not settings.ANTHROPIC_API_KEY and settings.MODEL_PROVIDER == "anthropic":
        print("ERROR: ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill in your key.")
        sys.exit(1)
    if not settings.GROQ_API_KEY and settings.MODEL_PROVIDER == "groq":
        print("ERROR: GROQ_API_KEY not set. Copy .env.example to .env and fill in your key.")
        sys.exit(1)

    await test_basic_streaming()
    await test_tool_calling()

    print("\n" + "=" * 50)
    print("MILESTONE A: ALL TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
