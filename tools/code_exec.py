"""Tool: execute Python code in a sandboxed subprocess."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from config import settings

TOOL_DEFINITION = {
    "name": "execute_python",
    "description": (
        "Execute Python code and return stdout and stderr. Use for calculations, "
        "data processing, generating output, or verifying code behavior. "
        "The code runs in an isolated subprocess with a 30-second timeout. "
        "The working directory is the shared workspace, so files created by "
        "write_file are accessible and any files you write here can be read by read_file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    },
}

_TIMEOUT = 30
_WORKSPACE = settings.DATA_DIR / "workspace"
_WORKSPACE.mkdir(parents=True, exist_ok=True)


async def execute(code: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        script_path = f.name

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_WORKSPACE),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"Execution timed out after {_TIMEOUT} seconds."

        output_parts = []
        if stdout:
            output_parts.append(f"STDOUT:\n{stdout.decode('utf-8', errors='replace')}")
        if stderr:
            output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")
        if proc.returncode != 0:
            output_parts.append(f"Exit code: {proc.returncode}")

        return "\n".join(output_parts) if output_parts else "(no output)"
    finally:
        Path(script_path).unlink(missing_ok=True)
