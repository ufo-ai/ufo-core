"""Expose Screenpipe search to UFO through a loopback MCP proxy."""

import os

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy
from fastmcp.server.transforms import Visibility

SCREENPIPE_PACKAGE = "screenpipe-mcp@0.20.0"
MCP_PORT = 3031


def screenpipe_proxy() -> FastMCP:
    """Connect the authenticated Screenpipe stdio server and expose only content search."""
    api_key = os.environ.get("SCREENPIPE_LOCAL_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Set SCREENPIPE_LOCAL_API_KEY from screenpipe auth token")
    transport = StdioTransport(
        command="npx",
        args=["--yes", SCREENPIPE_PACKAGE],
        env={
            "SCREENPIPE_LOCAL_API_KEY": api_key,
            "SCREENPIPE_LOCAL_API_URL": os.environ.get(
                "SCREENPIPE_LOCAL_API_URL", "http://127.0.0.1:3030"
            ),
        },
        keep_alive=False,
    )
    proxy = create_proxy(transport, name="Screenpipe search")
    proxy.add_transform(Visibility(False, match_all=True))
    proxy.add_transform(Visibility(True, names={"search-content"}, components={"tool"}))
    return proxy


if __name__ == "__main__":
    screenpipe_proxy().run(transport="http", host="127.0.0.1", port=MCP_PORT, path="/mcp")
