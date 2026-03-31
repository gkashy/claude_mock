"""Tool: get the current date and time in any timezone."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TOOL_DEFINITION = {
    "name": "get_current_datetime",
    "description": (
        "Returns the current date, time, and day of the week in the specified "
        "IANA timezone (e.g. 'America/New_York', 'Asia/Tokyo', 'UTC')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name. Defaults to UTC.",
            }
        },
        "required": [],
    },
}


async def execute(timezone: str = "UTC") -> str:
    try:
        tz = ZoneInfo(timezone)
    except (KeyError, Exception):
        return f"Unknown timezone: '{timezone}'. Use IANA names like 'America/New_York'."

    now = datetime.now(tz)
    return (
        f"Current time in {timezone}: "
        f"{now.strftime('%A, %B %d, %Y at %I:%M:%S %p %Z')}"
    )
