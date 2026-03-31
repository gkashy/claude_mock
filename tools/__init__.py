"""
Tool registry: discovers, registers, and dispatches tool calls.

Each tool module exposes:
  TOOL_DEFINITION  - dict with name, description, parameters (JSON Schema)
  execute(**kwargs) - async callable that runs the tool
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Callable, Awaitable

_tools: dict[str, dict] = {}
_executors: dict[str, Callable[..., Awaitable[str]]] = {}


def register_tool(definition: dict, executor: Callable[..., Awaitable[str]]) -> None:
    name = definition["name"]
    _tools[name] = definition
    _executors[name] = executor


def get_all_definitions() -> list[dict]:
    return list(_tools.values())


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    executor = _executors.get(name)
    if not executor:
        return f"Error: unknown tool '{name}'"
    try:
        return await executor(**arguments)
    except Exception as e:
        return f"Error executing {name}: {type(e).__name__}: {e}"


def autodiscover() -> None:
    """Import all modules in the tools/ package to trigger registration.

    Supports two patterns:
      - Single tool:  TOOL_DEFINITION (dict) + execute (async func)
      - Multi tool:   TOOL_DEFINITIONS (list of dicts), each with a matching
                      async function named after the tool (e.g. read_file).
    """
    package_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"tools.{module_info.name}")

        single_defn = getattr(module, "TOOL_DEFINITION", None)
        single_exe = getattr(module, "execute", None)
        if single_defn and single_exe:
            register_tool(single_defn, single_exe)
            continue

        multi_defns = getattr(module, "TOOL_DEFINITIONS", None)
        if multi_defns:
            for defn in multi_defns:
                name = defn["name"]
                exe = getattr(module, name, None)
                if exe:
                    register_tool(defn, exe)
