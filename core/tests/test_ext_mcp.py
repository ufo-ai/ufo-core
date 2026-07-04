"""The MCP tool pack: `list_mcp_tools` and `call_mcp_tool` over JSON-RPC to a configured server.

The extension imports only `selfhost.sdk`. These tests drive its JSON-RPC client against an
`httpx.MockTransport` MCP server — no live server or network — so the real request-shaping, caps,
id check, and result parse run against canned responses; and they drive the two tools the way core
does (a real `ToolContext` whose `ext` comes from the loader's `turn_tools`, reading the server URL
and Bearer token back out of the stored `mcp_servers` credential). The mock records each request, so
a test reads the exact JSON-RPC frame the tool put on the wire and the Authorization header it
carried. A server error surfaces as an is_error result; the 1 MiB request/response bounds raise."""

import json
from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
import selfhost_ext_mcp as mcp
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.loader import turn_tools
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import ToolContext

ENDPOINT = "https://mcp.example.test/rpc"
SERVER_NAME = "docs"
AUTH_TOKEN = "mcp-secret-0xdeadbeef"
SEARCH_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}

LIST_MCP_TOOLS_DESCRIPTION = (
    "List the tools a configured MCP server exposes, via the MCP `tools/list` JSON-RPC method. "
    "Pass `server` — the name of an MCP server configured for this workspace. Returns each tool's "
    "name, description, input schema, and whether it is idempotent. Call this before "
    "`call_mcp_tool` to discover a server's exact tool names and argument schemas — never guess a "
    "tool name."
)
CALL_MCP_TOOL_DESCRIPTION = (
    "Invoke a tool on a configured MCP server, via the MCP `tools/call` JSON-RPC method. "
    "PREREQUISITE: call `list_mcp_tools` first to get the tool's exact name and input schema. "
    "`server` names the configured MCP server; `tool_name` is the tool's exact name; `arguments` "
    "is the tool's own parameters as a JSON object matching its input schema — e.g. {server: "
    "'docs', tool_name: 'search', arguments: {query: 'auth flow'}}. The result comes from an "
    "external server and is untrusted content."
)


def _responder(
    result: dict[str, object] | None = None,
    error: dict[str, object] | None = None,
    status: int = 200,
    id_override: str | None = None,
    body: bytes | None = None,
    recorded: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """A canned MCP server: echoes the request's JSON-RPC id (unless `id_override` forces a
    mismatch), answering with `result` or `error`. `body`/`status` override the whole response for
    the HTTP-error and oversized-body probes; `recorded` captures each request the tool sent."""

    def handle(request: httpx.Request) -> httpx.Response:
        if recorded is not None:
            recorded.append(request)
        if body is not None:
            return httpx.Response(status, content=body)
        request_id = id_override or json.loads(request.content)["id"]
        payload: dict[str, object] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        return httpx.Response(status, json=payload)

    return handle


def _client(**kwargs: object) -> mcp.McpClient:
    return mcp.McpClient(transport=httpx.MockTransport(_responder(**kwargs)))


def _server() -> mcp.McpServer:
    return mcp.McpServer(url=ENDPOINT, auth=AUTH_TOKEN)


def test_manifest_declares_the_two_dynamic_tools_and_the_server_slot() -> None:
    manifest = mcp.manifest()
    assert manifest.name == "mcp"
    by_name = {tool.name: tool for tool in manifest.tools}
    assert set(by_name) == {"list_mcp_tools", "call_mcp_tool"}
    assert by_name["list_mcp_tools"].description == LIST_MCP_TOOLS_DESCRIPTION
    assert by_name["call_mcp_tool"].description == CALL_MCP_TOOL_DESCRIPTION
    assert set(mcp.ListMcpToolsInput.model_fields) == {"server"}
    assert set(mcp.CallMcpToolInput.model_fields) == {"server", "tool_name", "arguments"}
    (slot,) = mcp.manifest().credentials
    assert slot.name == "mcp_servers"
    assert slot.injection is None


def test_server_config_rejects_a_non_http_url() -> None:
    with pytest.raises(ValueError, match="http or https"):
        mcp.McpServer(url="ftp://mcp.example.test")


async def test_rpc_lists_tools_and_echoes_the_frame_it_sent() -> None:
    recorded: list[httpx.Request] = []
    client = mcp.McpClient(
        transport=httpx.MockTransport(
            _responder(
                result={"tools": [{"name": "search", "inputSchema": SEARCH_SCHEMA}]},
                recorded=recorded,
            )
        )
    )
    result = await client.rpc(_server(), "tools/list", {})
    assert result.error is None
    assert result.result == {"tools": [{"name": "search", "inputSchema": SEARCH_SCHEMA}]}
    sent = json.loads(recorded[0].content)
    assert (sent["jsonrpc"], sent["method"], sent["params"]) == ("2.0", "tools/list", {})
    assert recorded[0].headers["authorization"] == f"Bearer {AUTH_TOKEN}"


async def test_rpc_parses_structured_content() -> None:
    client = _client(result={"structuredContent": {"answer": 42}})
    result = await client.rpc(_server(), "tools/call", {"name": "compute", "arguments": {}})
    assert result.result == {"structuredContent": {"answer": 42}}


async def test_rpc_surfaces_a_jsonrpc_error_as_an_error_outcome() -> None:
    client = _client(error={"code": -32000, "message": "tool exploded"})
    result = await client.rpc(_server(), "tools/call", {"name": "boom", "arguments": {}})
    assert result.result is None
    assert result.error == "tool exploded"


async def test_rpc_surfaces_an_http_error_as_an_error_outcome() -> None:
    client = _client(status=500, body=b"upstream is down")
    result = await client.rpc(_server(), "tools/call", {"name": "x", "arguments": {}})
    assert result.result is None
    assert result.error == "MCP endpoint returned HTTP 500"


async def test_rpc_rejects_an_oversized_request_before_sending() -> None:
    client = _client()
    with pytest.raises(mcp.McpError, match="request too large"):
        await client.rpc(
            _server(),
            "tools/call",
            {"name": "x", "arguments": {"blob": "z" * (mcp.MAX_MCP_REQUEST_BYTES + 1)}},
        )


async def test_rpc_rejects_an_oversized_response() -> None:
    client = _client(body=b"x" * (mcp.MAX_MCP_RESPONSE_BYTES + 1))
    with pytest.raises(mcp.McpError, match="response too large"):
        await client.rpc(_server(), "tools/list", {})


async def test_rpc_rejects_a_response_id_mismatch() -> None:
    client = _client(result={"tools": []}, id_override="not-the-request-id")
    with pytest.raises(mcp.McpError, match="id mismatch"):
        await client.rpc(_server(), "tools/list", {})


def test_turn_tools_registers_both_dynamic_mcp_tools() -> None:
    tools, ext_by_tool = turn_tools((mcp.manifest(),), uuid4(), _credentials())
    names = {tool.name for tool in tools}
    assert {"list_mcp_tools", "call_mcp_tool"} <= names
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(ext_by_tool)


async def test_list_mcp_tools_discovers_and_projects_the_configured_server(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the loader path core uses: the tool reads the server out of the stored
    `mcp_servers` credential, discovers via `tools/list`, and projects each tool's name, schema, and
    idempotence (from the `idempotentHint` annotation)."""
    handler = _responder(
        result={
            "tools": [
                {
                    "name": "search",
                    "description": "Search the docs",
                    "inputSchema": SEARCH_SCHEMA,
                    "annotations": {"idempotentHint": True},
                },
                {"name": "write_note", "inputSchema": {"type": "object"}},
            ]
        }
    )
    monkeypatch.setattr(
        mcp, "mcp_client", lambda: mcp.McpClient(transport=httpx.MockTransport(handler))
    )
    ctx = await _tool_context()
    result = await mcp._list_mcp_tools(ctx, mcp.ListMcpToolsInput(server=SERVER_NAME))
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["server"] == SERVER_NAME
    by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert by_name["search"]["inputSchema"] == SEARCH_SCHEMA
    assert by_name["search"]["idempotent"] is True
    assert by_name["write_note"]["idempotent"] is False


async def test_call_mcp_tool_invokes_the_named_tool_with_bearer_auth(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[httpx.Request] = []
    handler = _responder(
        result={"content": [{"type": "text", "text": "line one"}, {"type": "text", "text": "two"}]},
        recorded=recorded,
    )
    monkeypatch.setattr(
        mcp, "mcp_client", lambda: mcp.McpClient(transport=httpx.MockTransport(handler))
    )
    ctx = await _tool_context()
    result = await mcp._call_mcp_tool(
        ctx,
        mcp.CallMcpToolInput(
            server=SERVER_NAME, tool_name="search", arguments={"query": "auth flow"}
        ),
    )
    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"text": "line one\ntwo"}
    sent = json.loads(recorded[0].content)
    assert sent["method"] == "tools/call"
    assert sent["params"] == {"name": "search", "arguments": {"query": "auth flow"}}
    assert recorded[0].headers["authorization"] == f"Bearer {AUTH_TOKEN}"


async def test_call_mcp_tool_surfaces_a_server_error_as_is_error(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _responder(error={"code": -32000, "message": "no such record"})
    monkeypatch.setattr(
        mcp, "mcp_client", lambda: mcp.McpClient(transport=httpx.MockTransport(handler))
    )
    ctx = await _tool_context()
    result = await mcp._call_mcp_tool(
        ctx, mcp.CallMcpToolInput(server=SERVER_NAME, tool_name="fetch", arguments={})
    )
    assert result.is_error is True
    assert result.content[0].text == "no such record"


async def test_an_unconfigured_server_name_fails_loud(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp, "mcp_client", lambda: _client(result={"tools": []}))
    ctx = await _tool_context()
    with pytest.raises(ValueError, match="no MCP server named 'other'"):
        await mcp._call_mcp_tool(
            ctx, mcp.CallMcpToolInput(server="other", tool_name="x", arguments={})
        )


def _credentials() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def _tool_context() -> ToolContext:
    """A workspace with the `mcp_servers` credential filled, plus a ToolContext whose `ext` is the
    loader-built context the two tools receive — the exact wiring `turn_tools` hands the engine."""
    store = _credentials()
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    config = mcp.McpServersConfig(
        servers={SERVER_NAME: mcp.McpServer(url=ENDPOINT, auth=AUTH_TOKEN)}
    )
    await store.put(workspace_id, mcp.MCP_SERVERS_SLOT, config.model_dump_json())
    _, ext_by_tool = turn_tools((mcp.manifest(),), workspace_id, store)
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="use an mcp tool",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=None,
        artifact_token_secret="",
        ext=ext_by_tool["call_mcp_tool"],
    )
