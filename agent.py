"""
ReAct agent loop: streams responses, dispatches tool calls, manages turn history.
No framework -- just direct model calls and a while loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

from config import settings
from models import (
    get_provider,
    TextDelta,
    ThinkingDelta,
    ToolCallRequest,
    ToolGenerating,
    ToolInputDelta,
    Done,
    StreamEvent,
)
from tools import autodiscover, get_all_definitions, dispatch
from tool_context import set_turn_context

autodiscover()

_system_prompt_template: str = (
    Path(__file__).parent / "prompts" / "system.md"
).read_text(encoding="utf-8")

# Rough token estimate: 1 token ~= 4 chars for English text
_MAX_CONTEXT_CHARS = 600_000  # ~150k tokens, leaves headroom in 200k window
_KEEP_RECENT_TURNS = 10


def _build_system_prompt(
    memory_facts: list[str] | None = None,
    session_artifacts: list[dict] | None = None,
) -> str:
    if memory_facts:
        facts_block = "\n".join(f"- {f}" for f in memory_facts)
    else:
        facts_block = "(No stored memories yet.)"

    if session_artifacts:
        art_lines = []
        for a in session_artifacts:
            ver = a.get("current_version", 1)
            art_lines.append(
                f"- [id: {a['id']}] \"{a['title']}\" ({a['filename']}) "
                f"-- v{ver}, {a['content_type']}"
            )
        artifacts_block = "\n".join(art_lines)
    else:
        artifacts_block = "(No artifacts in this session yet.)"

    from config import settings as _settings
    if _settings.USER_NAME:
        identity_block = f"You are speaking with **{_settings.USER_NAME}**. Always use this exact name when referring to the user in memory, facts, or knowledge graph operations."
    else:
        identity_block = ""

    prompt = _system_prompt_template.replace("{{USER_IDENTITY}}", identity_block)
    prompt = prompt.replace("{{MEMORY_FACTS}}", facts_block)
    prompt = prompt.replace("{{SESSION_ARTIFACTS}}", artifacts_block)
    return prompt


def _estimate_chars(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += len(json.dumps(content))
        else:
            total += len(str(content))
    return total


def _strip_invalid_thinking(messages: list[dict]) -> list[dict]:
    """Strip thinking blocks that lack a valid signature.

    The Anthropic API requires a ``signature`` field on every thinking block
    in conversation history.  Blocks persisted before the signature-capture fix
    lack this field and cause 400 errors.  Valid blocks (with signatures from
    the current session) are preserved so the model retains its reasoning chain.
    """
    cleaned: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue
        filtered = [
            block for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "thinking"
                and not block.get("signature")
            )
        ]
        if filtered:
            cleaned.append({**msg, "content": filtered})
        elif msg.get("role") == "assistant":
            cleaned.append({**msg, "content": [{"type": "text", "text": ""}]})
        else:
            cleaned.append(msg)
    return cleaned


def _trim_history(messages: list[dict]) -> list[dict]:
    """Drop oldest messages when context is too large, keeping recent turns."""
    if _estimate_chars(messages) <= _MAX_CONTEXT_CHARS:
        return messages

    keep_tail = _KEEP_RECENT_TURNS * 2  # each turn = user + assistant
    if len(messages) <= keep_tail:
        return messages

    recent = messages[-keep_tail:]
    summary_msg = {
        "role": "user",
        "content": (
            "[System note: Earlier messages in this conversation were trimmed "
            "to fit the context window. The conversation continues below.]"
        ),
    }
    return [summary_msg] + recent


# ---- Normalized event types yielded to callers (UI / CLI) ----

class AgentTextDelta:
    __slots__ = ("text",)
    def __init__(self, text: str):
        self.text = text

class AgentThinking:
    __slots__ = ("text",)
    def __init__(self, text: str):
        self.text = text

class AgentToolStart:
    __slots__ = ("name", "arguments")
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments

class AgentToolResult:
    __slots__ = ("name", "result")
    def __init__(self, name: str, result: str):
        self.name = name
        self.result = result

class AgentArtifact:
    __slots__ = ("artifact",)
    def __init__(self, artifact: dict):
        self.artifact = artifact

class AgentToolGenerating:
    __slots__ = ("name",)
    def __init__(self, name: str):
        self.name = name

class AgentToolProgress:
    __slots__ = ("name", "content", "metadata", "raw_delta")
    def __init__(self, name: str, content: str, metadata: dict | None = None, raw_delta: str = ""):
        self.name = name
        self.content = content
        self.metadata = metadata
        self.raw_delta = raw_delta

class AgentDone:
    __slots__ = ("usage",)
    def __init__(self, usage: dict):
        self.usage = usage

AgentEvent = (
    AgentTextDelta | AgentThinking | AgentToolStart | AgentToolResult
    | AgentArtifact | AgentToolGenerating | AgentToolProgress | AgentDone
)


async def run_turn(
    messages: list[dict],
    memory_facts: list[str] | None = None,
    session_id: str = "",
    user_id: str = "default",
) -> AsyncIterator[AgentEvent]:
    """
    Run one full agent turn (may involve multiple model calls if tools are used).

    Args:
        messages: Full conversation history (user/assistant messages in provider format).
                  The new user message should already be appended.
        memory_facts: Optional list of extracted facts to inject into the system prompt.
        session_id: Current session ID (used by tools that need persistence context).
        user_id: Current user ID.

    Yields:
        AgentEvent instances as the turn progresses.

    Side effect:
        Appends assistant messages (text + tool calls + tool results) to `messages` in place.
    """
    set_turn_context(session_id, user_id)

    import memory as _memory
    try:
        session_artifacts = await _memory.list_artifacts_for_session(session_id) if session_id else []
    except Exception:
        session_artifacts = []

    provider = get_provider()
    system = _build_system_prompt(memory_facts, session_artifacts)
    tool_defs = get_all_definitions()
    formatted_tools = provider.format_tools(tool_defs) if tool_defs else None

    for iteration in range(settings.MAX_ITERATIONS):
        trimmed = _trim_history(_strip_invalid_thinking(messages))

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        stop_reason = "end_turn"

        async for event in provider.stream(
            system=system,
            messages=trimmed,
            tools=formatted_tools,
        ):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
                yield AgentTextDelta(event.text)
            elif isinstance(event, ThinkingDelta):
                yield AgentThinking(event.text)
            elif isinstance(event, ToolGenerating):
                yield AgentToolGenerating(event.name)
            elif isinstance(event, ToolInputDelta):
                yield AgentToolProgress(event.tool_name, event.content_delta, event.metadata, event.raw_delta)
            elif isinstance(event, ToolCallRequest):
                tool_calls.append(event)
                yield AgentToolStart(event.name, event.arguments)
            elif isinstance(event, Done):
                stop_reason = event.stop_reason

        if tool_calls:
            assistant_msg = provider.format_tool_call_message(tool_calls, text_parts)
            messages.append(assistant_msg)

            for tc in tool_calls:
                result = await dispatch(tc.name, tc.arguments)
                yield AgentToolResult(tc.name, result)
                tool_result_msg = provider.format_tool_result(tc.id, result)
                messages.append(tool_result_msg)

                if tc.name in ("create_artifact", "update_artifact", "get_artifact_content"):
                    from tools.artifacts import get_last_artifact, clear_last_artifact
                    artifact = get_last_artifact()
                    if artifact:
                        yield AgentArtifact(artifact)
                        clear_last_artifact()

            continue  # next iteration: model sees tool results

        # No tool calls -- final text response
        if text_parts:
            full_text = "".join(text_parts)
            messages.append({"role": "assistant", "content": full_text})

        yield AgentDone(usage={})
        return

    # Exhausted max iterations
    fallback = "I've reached my reasoning limit for this turn. Could you rephrase or simplify your request?"
    messages.append({"role": "assistant", "content": fallback})
    yield AgentTextDelta(fallback)
    yield AgentDone(usage={})
