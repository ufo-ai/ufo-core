import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from ufo_ext_mcp import McpServer, mcp_client

from examples.screenpipe.bridge import screenpipe_proxy

TOKEN = "synthetic-screenpipe-key"
TIMESTAMP = "2026-09-27T12:00:00Z"


def test_screenpipe_requires_an_explicit_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCREENPIPE_LOCAL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SCREENPIPE_LOCAL_API_KEY"):
        screenpipe_proxy()


@asynccontextmanager
async def _http_proxy() -> AsyncIterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from examples.screenpipe.bridge import screenpipe_proxy; "
        f"screenpipe_proxy().run(transport='http', host='127.0.0.1', port={port}, path='/mcp')",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            while True:
                if process.returncode is not None:
                    _, stderr = await process.communicate()
                    raise RuntimeError(stderr.decode())
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except ConnectionRefusedError:
                    await asyncio.sleep(0.05)
                else:
                    writer.close()
                    await writer.wait_closed()
                    break
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.integration
async def test_published_screenpipe_search_through_ufo(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[dict[str, list[str]]] = []
    revoked = False

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            lines = request.decode().split("\r\n")
            headers = {
                key.lower(): value
                for key, value in (line.split(": ", 1) for line in lines[1:] if ": " in line)
            }
            url = urlsplit(lines[0].split()[1])
            if url.path == "/search":
                queries.append(parse_qs(url.query))
            authorized = not revoked and headers.get("authorization") == f"Bearer {TOKEN}"
            body = json.dumps(
                {
                    "data": [
                        {
                            "type": "OCR",
                            "content": {
                                "text": "synthetic-ufo-screenpipe-evidence",
                                "timestamp": TIMESTAMP,
                                "app_name": "Fixture",
                                "window_name": "Fixture",
                            },
                        }
                    ],
                    "pagination": {"total": 1, "offset": 0, "limit": 1},
                }
                if authorized
                else {"error": "unauthorized"}
            ).encode()
            status = "200 OK" if authorized else "403 Forbidden"
            writer.write(
                (
                    f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    api = await asyncio.start_server(upstream, "127.0.0.1", 0)
    api_port = api.sockets[0].getsockname()[1]
    monkeypatch.setenv("SCREENPIPE_LOCAL_API_URL", f"http://127.0.0.1:{api_port}")
    monkeypatch.setenv("SCREENPIPE_LOCAL_API_KEY", TOKEN)
    try:
        async with _http_proxy() as url:
            async with mcp_client(McpServer("screenpipe", url, None)) as client:
                catalog = await client.list_tools()
                assert [tool.name for tool in catalog] == ["search-content"]
                assert "start_time" in catalog[0].inputSchema["properties"]
                assert await client.list_resources() == []
                assert await client.list_prompts() == []
                blocked = await client.call_tool(
                    "create-memory", {"text": "forbidden"}, raise_on_error=False
                )
                assert blocked.is_error
                assert queries == []
                result = await client.call_tool(
                    "search-content",
                    {"q": "synthetic", "limit": 1, "start_time": TIMESTAMP},
                    raise_on_error=False,
                )
                assert not result.is_error
                rendered = "\n".join(item.text for item in result.content if item.type == "text")
                assert TOKEN not in rendered
                assert "synthetic-ufo-screenpipe-evidence" in rendered
                assert TIMESTAMP in rendered
                assert len(queries) == 1
                assert queries[0]["q"] == ["synthetic"]
                assert queries[0]["limit"] == ["1"]
                assert queries[0]["start_time"] == [TIMESTAMP]
                revoked = True
                rejected = await client.call_tool(
                    "search-content", {"q": "synthetic", "limit": 1}, raise_on_error=False
                )
                assert rejected.is_error
                error = "\n".join(item.text for item in rejected.content if item.type == "text")
                assert "403" in error
                assert TOKEN not in error
                assert len(queries) == 2
    finally:
        api.close()
        await api.wait_closed()
