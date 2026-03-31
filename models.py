"""
Thin abstraction over Anthropic (Claude) and OpenAI-compatible (Groq) chat APIs.
Normalizes both into the same streaming interface so the agent loop doesn't care
which provider is active.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator

import anthropic
import openai

from config import settings


# ---------------------------------------------------------------------------
# Normalized event types the agent loop consumes
# ---------------------------------------------------------------------------

@dataclass
class TextDelta:
    text: str


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolGenerating:
    """Emitted the moment a tool_use block starts (before arguments stream)."""
    name: str
    id: str


@dataclass
class ToolInputDelta:
    """Partial content extracted from a streaming tool call (e.g. artifact body)."""
    tool_name: str
    content_delta: str
    metadata: dict | None = None
    raw_delta: str = ""


@dataclass
class Done:
    stop_reason: str  # "end_turn", "tool_use", "max_tokens"
    usage: dict = field(default_factory=dict)


StreamEvent = TextDelta | ThinkingDelta | ToolCallRequest | ToolGenerating | ToolInputDelta | Done


class _ArtifactContentExtractor:
    """Parses streaming input_json_delta chunks to extract the 'content' field
    from create_artifact tool calls, yielding text as it arrives.

    Also emits metadata (title, filename, etc.) as soon as those fields
    appear in the partial JSON, so the UI can update the header immediately
    rather than waiting for the content body to start.
    """

    def __init__(self):
        self._streaming = False
        self._yielded_up_to = 0
        self._pending_backslash = False
        self._last_meta: dict = {}

    def feed(self, full_buf: str) -> tuple[str | None, dict | None]:
        """Return (content_delta, metadata_or_None).

        Before the content field starts, returns (None, meta_update) whenever
        new metadata fields are detected. During content streaming, returns
        (delta, None). Returns (None, None) when there's nothing new.
        """
        if not self._streaming:
            for marker in ['"content":"', '"content": "']:
                idx = full_buf.find(marker)
                if idx < 0:
                    continue
                self._streaming = True
                self._yielded_up_to = idx + len(marker)
                raw = full_buf[self._yielded_up_to:]
                meta = self._extract_metadata(full_buf[:idx])
                self._last_meta = meta
                delta = self._unescape(raw) if raw else ""
                if delta:
                    self._yielded_up_to = len(full_buf)
                return delta, meta

            new_meta = self._extract_metadata(full_buf)
            if new_meta and new_meta != self._last_meta:
                self._last_meta = new_meta
                return None, new_meta
            return None, None

        if len(full_buf) <= self._yielded_up_to:
            return None, None
        raw = full_buf[self._yielded_up_to:]
        self._yielded_up_to = len(full_buf)
        delta = self._unescape(raw)
        return delta, None

    def _extract_metadata(self, buf: str) -> dict:
        """Best-effort extraction of title/content_type from the partial JSON."""
        meta = {}
        for key in ("title", "content_type", "filename", "language"):
            for pattern in [f'"{key}":"', f'"{key}": "']:
                idx = buf.find(pattern)
                if idx < 0:
                    continue
                start = idx + len(pattern)
                end = buf.find('"', start)
                if end > start:
                    meta[key] = buf[start:end]
                break
        return meta

    def _unescape(self, s: str) -> str:
        if self._pending_backslash:
            s = '\\' + s
            self._pending_backslash = False

        if s.endswith('\\'):
            s = s[:-1]
            self._pending_backslash = True

        if not s:
            return ''

        out = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                nxt = s[i + 1]
                if nxt == 'n':
                    out.append('\n')
                elif nxt == 't':
                    out.append('\t')
                elif nxt == '"':
                    out.append('"')
                elif nxt == '\\':
                    out.append('\\')
                elif nxt == '/':
                    out.append('/')
                elif nxt == 'r':
                    out.append('\r')
                else:
                    out.append(s[i])
                    out.append(nxt)
                i += 2
            else:
                out.append(s[i])
                i += 1
        return ''.join(out)


# ---------------------------------------------------------------------------
# Anthropic (Claude) provider
# ---------------------------------------------------------------------------

class AnthropicProvider:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL

    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
            "thinking": {
                "type": "enabled",
                "budget_tokens": 10000,
            },
        }
        if tools:
            kwargs["tools"] = tools

        self._last_thinking_blocks: list[dict] = []

        async with self.client.messages.stream(**kwargs) as stream:
            current_tool_name: str | None = None
            current_tool_id: str | None = None
            tool_json_buf = ""
            thinking_buf = ""
            thinking_signature = ""
            current_block_type: str | None = None
            content_extractor: _ArtifactContentExtractor | None = None

            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    current_block_type = block.type
                    if block.type == "tool_use":
                        current_tool_name = block.name
                        current_tool_id = block.id
                        tool_json_buf = ""
                        content_extractor = (
                            _ArtifactContentExtractor()
                            if block.name in ("create_artifact", "update_artifact")
                            else None
                        )
                        yield ToolGenerating(name=block.name, id=block.id)
                    elif block.type == "thinking":
                        thinking_buf = ""
                        thinking_signature = ""

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield TextDelta(text=delta.text)
                    elif delta.type == "thinking_delta":
                        thinking_buf += delta.thinking
                        yield ThinkingDelta(text=delta.thinking)
                    elif delta.type == "signature_delta":
                        thinking_signature += delta.signature
                    elif delta.type == "input_json_delta":
                        chunk = delta.partial_json
                        tool_json_buf += chunk
                        if content_extractor is not None:
                            content_delta, meta = content_extractor.feed(tool_json_buf)
                            yield ToolInputDelta(
                                tool_name=current_tool_name or "create_artifact",
                                content_delta=content_delta or "",
                                metadata=meta,
                                raw_delta=chunk,
                            )

                elif event.type == "content_block_stop":
                    if current_block_type == "thinking" and thinking_buf:
                        self._last_thinking_blocks.append({
                            "type": "thinking",
                            "thinking": thinking_buf,
                            "signature": thinking_signature,
                        })
                        thinking_buf = ""
                        thinking_signature = ""

                    if current_tool_name and current_tool_id:
                        try:
                            args = json.loads(tool_json_buf) if tool_json_buf else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield ToolCallRequest(
                            id=current_tool_id,
                            name=current_tool_name,
                            arguments=args,
                        )
                        current_tool_name = None
                        current_tool_id = None
                        tool_json_buf = ""
                        content_extractor = None

                    current_block_type = None

                elif event.type == "message_delta":
                    yield Done(
                        stop_reason=event.delta.stop_reason or "end_turn",
                        usage={"output_tokens": getattr(event.usage, "output_tokens", 0)},
                    )

    def format_tool_result(self, tool_call_id: str, result: str) -> dict:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result,
                }
            ],
        }

    def format_tool_call_message(self, tool_calls: list[ToolCallRequest], text_parts: list[str]) -> dict:
        content = []
        for block in self._last_thinking_blocks:
            content.append(block)
        self._last_thinking_blocks = []
        for text in text_parts:
            if text:
                content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": content}

    def format_tools(self, tools: list[dict]) -> list[dict]:
        """Convert internal tool defs to Anthropic tool format."""
        formatted = []
        for t in tools:
            formatted.append({
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            })
        return formatted


# ---------------------------------------------------------------------------
# Groq (OpenAI-compatible) provider
# ---------------------------------------------------------------------------

class GroqProvider:
    def __init__(self):
        self.client = openai.AsyncOpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = settings.GROQ_MODEL

    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        oai_messages = [{"role": "system", "content": system}]

        for msg in messages:
            oai_messages.append(self._convert_message(msg))

        kwargs: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": oai_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]

        tool_calls_buf: dict[int, dict] = {}

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta

            if delta.content:
                yield TextDelta(text=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    buf = tool_calls_buf[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        buf["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        buf["arguments"] += tc_delta.function.arguments

            if choice.finish_reason:
                for _idx, buf in sorted(tool_calls_buf.items()):
                    try:
                        args = json.loads(buf["arguments"]) if buf["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield ToolCallRequest(id=buf["id"], name=buf["name"], arguments=args)

                yield Done(
                    stop_reason="tool_use" if tool_calls_buf else "end_turn",
                    usage={},
                )

    def _convert_message(self, msg: dict) -> dict:
        """Convert Anthropic-style messages to OpenAI format."""
        role = msg.get("role", "user")
        content = msg.get("content")

        if isinstance(content, str):
            return {"role": role, "content": content}

        if isinstance(content, list):
            # Anthropic uses content blocks; flatten for OpenAI
            text_parts = []
            tool_calls = []
            tool_results = []

            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block["text"])
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"]),
                            },
                        })
                    elif btype == "tool_result":
                        tool_results.append(block)

            if tool_results:
                return {
                    "role": "tool",
                    "tool_call_id": tool_results[0].get("tool_use_id", ""),
                    "content": tool_results[0].get("content", ""),
                }

            if tool_calls:
                return {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                    "tool_calls": tool_calls,
                }

            return {"role": role, "content": "\n".join(text_parts)}

        return {"role": role, "content": str(content) if content else ""}

    def format_tool_result(self, tool_call_id: str, result: str) -> dict:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result,
                }
            ],
        }

    def format_tool_call_message(self, tool_calls: list[ToolCallRequest], text_parts: list[str]) -> dict:
        content = []
        for text in text_parts:
            if text:
                content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": content}

    def format_tools(self, tools: list[dict]) -> list[dict]:
        """Internal tool defs are already in OpenAI-compatible format."""
        return tools


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> AnthropicProvider | GroqProvider:
    if settings.MODEL_PROVIDER == "groq":
        return GroqProvider()
    return AnthropicProvider()
