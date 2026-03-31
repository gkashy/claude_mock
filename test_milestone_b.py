"""
Milestone B verification: interactive CLI chat with the ReAct agent.
Tests: multi-turn conversation, tool calling (datetime), streaming output.
Run: python test_milestone_b.py
"""

import asyncio
import sys

from agent import (
    run_turn,
    AgentTextDelta,
    AgentThinking,
    AgentToolStart,
    AgentToolResult,
    AgentDone,
)


async def main():
    print("=" * 50)
    print("Milestone B -- ReAct Agent CLI")
    print("Type 'quit' to exit. Try asking for the current time!")
    print("=" * 50)

    messages: list[dict] = []

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        messages.append({"role": "user", "content": user_input})

        print("\nAssistant: ", end="", flush=True)
        async for event in run_turn(messages):
            if isinstance(event, AgentTextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, AgentThinking):
                pass  # hide thinking in CLI for now
            elif isinstance(event, AgentToolStart):
                print(f"\n  [calling {event.name}...]", flush=True)
            elif isinstance(event, AgentToolResult):
                print(f"  [result: {event.result[:200]}]", flush=True)
                print("  ", end="", flush=True)
            elif isinstance(event, AgentDone):
                print()  # newline after response


if __name__ == "__main__":
    asyncio.run(main())
