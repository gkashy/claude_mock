"""Tool: search the web using DuckDuckGo."""

from ddgs import DDGS

TOOL_DEFINITION = {
    "name": "web_search",
    "description": (
        "Search the public web for current information. Returns top results "
        "with titles, URLs, and text snippets. Use for real-time data, recent "
        "events, prices, news, or anything beyond your training data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results to return (1-10). Defaults to 5.",
            },
        },
        "required": ["query"],
    },
}


async def execute(query: str, max_results: int = 5) -> str:
    max_results = max(1, min(10, max_results))
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Search failed: {type(e).__name__}: {e}"

    if not results:
        return f"No results found for: {query}"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", "")
        body = r.get("body", "No snippet")
        lines.append(f"{i}. **{title}**\n   {url}\n   {body}")

    return "\n\n".join(lines)
