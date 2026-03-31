"""
Per-turn context for tools that need session/user info without polluting
the LLM tool schema. Set once per turn in the agent loop; read by any tool.
"""

from __future__ import annotations

from contextvars import ContextVar

_session_id: ContextVar[str] = ContextVar("session_id", default="")
_user_id: ContextVar[str] = ContextVar("user_id", default="default")


def set_turn_context(session_id: str, user_id: str = "default") -> None:
    _session_id.set(session_id)
    _user_id.set(user_id)


def get_session_id() -> str:
    return _session_id.get()


def get_user_id() -> str:
    return _user_id.get()
