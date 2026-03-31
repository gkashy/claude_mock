"""Tool: read, write, and list files in a sandboxed workspace directory."""

import os
from pathlib import Path

from config import settings

_WORKSPACE = settings.DATA_DIR / "workspace"
_WORKSPACE.mkdir(parents=True, exist_ok=True)

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path within the workspace.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file in the workspace directory. "
            "Creates the file (and parent directories) if it doesn't exist, "
            "or overwrites it if it does."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path within the workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files and directories in the workspace. "
            "Optionally provide a subdirectory path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path within the workspace. Defaults to root.",
                },
            },
            "required": [],
        },
    },
]


def _safe_path(relative: str) -> Path:
    """Resolve path and ensure it stays within the workspace."""
    resolved = (_WORKSPACE / relative).resolve()
    if not str(resolved).startswith(str(_WORKSPACE.resolve())):
        raise ValueError(f"Path escapes workspace: {relative}")
    return resolved


async def read_file(path: str) -> str:
    target = _safe_path(path)
    if not target.is_file():
        return f"File not found: {path}"
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"


async def write_file(path: str, content: str) -> str:
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


async def list_files(path: str = "") -> str:
    target = _safe_path(path) if path else _WORKSPACE
    if not target.is_dir():
        return f"Directory not found: {path}"

    entries = []
    for item in sorted(target.iterdir()):
        prefix = "[dir] " if item.is_dir() else "      "
        entries.append(f"{prefix}{item.name}")

    if not entries:
        return "(empty directory)"
    return "\n".join(entries)
