"""One Composio Tool Router call over a streamable-HTTP MCP session.

Composio's Tool Router exposes semantic tool search as the `COMPOSIO_SEARCH_TOOLS` tool on an MCP
endpoint (the `mcp.url` a router session returns). `mcp_call_tool` opens a fastmcp streamable-HTTP
session to that endpoint, calls the tool once, and returns its structured result as a plain dict —
preferring the parsed `data`/`structured_content`, else the first JSON text block. Search only:
execution never rides Tool Router, so metering and the grant stay on Composio's execute API. The
`Client` is a module attribute so a test overrides it with a stub session and no live endpoint."""

import json
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import TextContent


async def mcp_call_tool(
    endpoint: str,
    tool: str,
    arguments: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, object]:
    async with Client(
        StreamableHttpTransport(endpoint, headers=headers), timeout=timeout_seconds
    ) as client:
        result = await client.call_tool(tool, dict(arguments), timeout=timeout_seconds)
    data: Any = result.data
    if isinstance(data, dict):
        return data
    structured: Any = result.structured_content
    if isinstance(structured, dict):
        return structured
    for item in result.content:
        if isinstance(item, TextContent):
            try:
                payload = json.loads(item.text)
            except json.JSONDecodeError:
                return {"text": item.text}
            return payload if isinstance(payload, dict) else {"result": payload}
    return {"result": data}
