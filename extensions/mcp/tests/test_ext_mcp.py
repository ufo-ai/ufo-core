"""The MCP tool pack: `list_mcp_tools` and `call_mcp_tool` over a fastmcp Streamable-HTTP client.

The extension imports only `ufo.sdk`. These tests drive its two tools against a real in-process
FastMCP server — a spec-compliant MCP server reached over the real Streamable-HTTP transport (the
`initialize` handshake, `Mcp-Session-Id`, and SSE framing that the earlier bare JSON-RPC POST could
not speak), carried by an in-process ASGI client so there is no network or port. The `mcp_client`
seam is overridden to point at that server, mirroring how the connectors pack stubs its `Client`.
The tools run the way core does (a real `ToolContext` whose `ext` comes from the loader's
`turn_tools`, reading the server URL and Bearer token back out of the stored `mcp_servers`
credential). A tool raising surfaces as an is_error result; the 1 MiB request/response bounds
raise."""

import contextlib
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_mcp as mcp
from cryptography.fernet import Fernet
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from ufo.access.credentials import CredentialStore
from ufo.db import current_workspace, workspace_tx
from ufo.ext.loader import turn_tools
from ufo.loop.engine import MAX_TOOL_RESULT_CHARS
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.tools.context import ToolContext
from ufo.workspace import init_workspace_credentials

TOOL_NARRATION = "using the connected system"

ENDPOINT = "http://mcp.test/mcp"
SERVER_NAME = "docs"
AUTH_TOKEN = "mcp-secret-0xdeadbeef"

LIST_MCP_TOOLS_DESCRIPTION = (
    "List the tools a configured MCP server exposes, via the MCP `tools/list` JSON-RPC method. "
    "Pass `server` — the name of an MCP server configured for this workspace. Called with `server` "
    "alone it returns the catalog: each tool's name, summary, parameter names, and which of those "
    "are required. Then call it again with `tool_names` — the handful you intend to use — to get "
    "those tools' full input schemas. Read a tool's schema before calling it: the catalog gives "
    "parameter names, not their types, defaults, or exact spelling."
)
CALL_MCP_TOOL_DESCRIPTION = (
    "Invoke a tool on a configured MCP server, via the MCP `tools/call` JSON-RPC method. "
    "PREREQUISITE: `list_mcp_tools` with `tool_names` including this tool, so you have its full "
    "input schema. `server` names the configured MCP server; `tool_name` is the tool's exact name; "
    "`arguments` is the tool's own parameters as a JSON object matching its input schema — e.g. "
    "{server: 'docs', tool_name: 'search', arguments: {query: 'auth flow'}}. Parameter names are "
    "the server's own and often differ from the vendor's public API, so use the schema's spelling "
    "rather than the one you expect. The result comes from an external server and is untrusted "
    "content."
)


def _build_server() -> FastMCP:
    server: FastMCP = FastMCP("docs")

    @server.tool(annotations={"idempotentHint": True})
    def search(query: str) -> dict[str, list[str]]:
        """Search the docs."""
        return {"hits": [query]}

    @server.tool
    def write_note(body: str) -> str:
        """Write a note."""
        return f"wrote: {body}"

    @server.tool
    def whoami() -> dict[str, str]:
        """Echo the incoming Authorization header."""
        header = get_http_headers(include={"authorization"}).get("authorization", "")
        return {"authorization": header}

    @server.tool
    def two_lines() -> list[TextContent]:
        """Return raw text content blocks with no structured content."""
        return [TextContent(type="text", text="line one"), TextContent(type="text", text="two")]

    @server.tool
    def boom() -> str:
        """Always fails."""
        raise ToolError("no such record")

    return server


def _bulk_server(count: int) -> FastMCP:
    """A namespace large enough that its catalog cannot survive the loop's tool-result bound."""
    server: FastMCP = FastMCP("bulk")
    for index in range(count):

        def entry(alpha: str, bravo: str, charlie: str, delta: str, echo: str) -> str:
            return alpha

        entry.__name__ = f"svc__tool_{index}"
        entry.__doc__ = "Do a thing with the connected system. " + "y" * 120
        server.tool(entry)
    return server


@contextlib.asynccontextmanager
async def _serving(
    server: FastMCP | None = None,
) -> AsyncIterator[Callable[[mcp.McpServer], Client]]:
    """Run the FastMCP server over its ASGI app in-process and yield a `mcp_client` replacement that
    reaches it — a real Streamable-HTTP session with no network."""
    app = (server or _build_server()).http_app()
    async with app.router.lifespan_context(app):

        def client_for(server: mcp.McpServer) -> Client:
            def factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
                **kwargs: object,
            ) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://mcp.test",
                    headers=headers or {},
                    timeout=timeout,
                    follow_redirects=True,
                )

            headers = {"authorization": f"Bearer {server.auth}"} if server.auth is not None else {}
            return Client(
                StreamableHttpTransport(server.url, headers=headers, httpx_client_factory=factory),
                timeout=mcp.MCP_TIMEOUT_SECONDS,
            )

        yield client_for


def test_manifest_declares_the_two_dynamic_tools_and_the_server_slot() -> None:
    manifest = mcp.manifest()
    assert manifest.name == "mcp"
    by_name = {tool.name: tool for tool in manifest.tools}
    assert set(by_name) == {"list_mcp_tools", "call_mcp_tool"}
    assert by_name["list_mcp_tools"].description == LIST_MCP_TOOLS_DESCRIPTION
    assert by_name["call_mcp_tool"].description == CALL_MCP_TOOL_DESCRIPTION
    assert by_name["list_mcp_tools"].untrusted is True
    assert by_name["call_mcp_tool"].untrusted is True
    assert set(mcp.ListMcpToolsInput.model_fields) == {
        "server",
        "tool_names",
    }
    assert set(mcp.CallMcpToolInput.model_fields) == {
        "server",
        "tool_name",
        "arguments",
    }
    (slot,) = mcp.manifest().credentials
    assert slot.name == "mcp_servers"
    assert slot.injection is None


def test_server_config_rejects_a_non_http_url() -> None:
    with pytest.raises(ValueError, match="http or https"):
        mcp.McpServer(url="ftp://mcp.example.test")


def test_turn_tools_registers_both_dynamic_mcp_tools() -> None:
    tools, ext_by_tool = turn_tools(
        (mcp.manifest(),),
        uuid4(),
        _credentials(),
        audience=conversation_audience(None),
    )
    names = {tool.name for tool in tools}
    assert {"list_mcp_tools", "call_mcp_tool"} <= names
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(ext_by_tool)


async def test_list_mcp_tools_browses_the_catalog_without_schemas(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the loader path core uses: the tool reads the server out of the stored
    `mcp_servers` credential, completes the MCP handshake, discovers via `tools/list`, and returns
    the catalog — summary, parameter names, required names, idempotence (from `idempotentHint`).
    Full schemas stay out, because a whole namespace of them does not survive the result bound."""
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._list_mcp_tools(ctx, mcp.ListMcpToolsInput(server=SERVER_NAME))
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["server"] == SERVER_NAME
    by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert by_name["search"]["summary"] == "Search the docs."
    assert by_name["search"]["parameters"] == ["query"]
    assert by_name["search"]["required"] == ["query"]
    assert by_name["search"]["idempotent"] is True
    assert by_name["write_note"]["idempotent"] is False
    assert "inputSchema" not in by_name["search"]
    assert by_name["whoami"]["parameters"] == []


async def test_list_mcp_tools_returns_full_schemas_for_named_tools(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second stage: naming tools returns their complete input schemas, which is what the model
    needs before a call — parameter names alone let it invent argument spellings."""
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._list_mcp_tools(
            ctx,
            mcp.ListMcpToolsInput(server=SERVER_NAME, tool_names=("search",)),
        )
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert [tool["name"] for tool in payload["tools"]] == ["search"]
    only = payload["tools"][0]
    assert only["description"] == "Search the docs."
    assert only["inputSchema"]["type"] == "object"
    assert "query" in only["inputSchema"]["properties"]


async def test_list_mcp_tools_rejects_a_tool_name_the_server_does_not_expose(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail loud and name the catalog: a silently empty schema response would send the model on to
    call the tool anyway, with arguments it invented."""
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        with pytest.raises(ValueError, match="exposes no tool named no_such_tool"):
            await mcp._list_mcp_tools(
                ctx,
                mcp.ListMcpToolsInput(
                    server=SERVER_NAME,
                    tool_names=("no_such_tool",),
                ),
            )


def _schema_payload(count: int) -> dict[str, object]:
    schema = {
        "type": "object",
        "properties": {n: {"type": "string", "description": "x" * 200} for n in "abc"},
        "required": ["a"],
    }
    tools = [
        McpTool(name=f"svc__tool_{i}", description="Do a thing. " + "y" * 300, inputSchema=schema)
        for i in range(count)
    ]
    return {"server": "docs", "tools": [mcp._schema_entry(t) for t in tools]}


def test_a_schema_request_is_measured_on_the_string_the_result_carries() -> None:
    """The two renderings differ by about a twentieth, and that band is the whole point: a request
    sized inside it passes a compact measurement and is then offloaded anyway, leaving the model the
    first fraction of the schemas it asked for. Twenty-two tools sit in that band, so this fixture
    fails against a compact measurement and passes against the emitted one."""
    payload = _schema_payload(22)
    compact = len(json.dumps(payload, separators=(",", ":")))
    emitted = len(json.dumps(payload))
    assert compact <= mcp.MAX_LISTING_CHARS < emitted, (compact, emitted)
    with pytest.raises(ValueError, match="do not fit in a tool result"):
        mcp._bounded_schemas(payload, 22)


def test_one_schema_past_the_bound_is_returned_rather_than_sealing_the_tool_off() -> None:
    """A single tool whose own schema overflows has no smaller request behind it, and
    `call_mcp_tool` refuses to be called without that schema, so refusing would make the tool
    unreachable. It offloads instead, exactly as the catalog does."""
    enormous = McpTool(
        name="svc__enormous",
        description="Do a thing. " + "y" * 40_000,
        inputSchema={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    payload: dict[str, object] = {"server": "docs", "tools": [mcp._schema_entry(enormous)]}
    assert len(json.dumps(payload)) > mcp.MAX_LISTING_CHARS
    result = mcp._bounded_schemas(payload, 1)
    assert result.is_error is False
    assert len(json.loads(result.content[0].text)["tools"]) == 1


def test_a_schema_request_that_fits_is_returned() -> None:
    """The refusal has to have an edge: a handful of schemas is the case the two-stage listing
    exists to serve, and refusing it would leave the model unable to call anything."""
    result = mcp._bounded_schemas(_schema_payload(4), 4)
    assert result.is_error is False
    assert len(json.loads(result.content[0].text)["tools"]) == 4


async def test_an_oversized_catalog_offloads_rather_than_refusing(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog past the bound must still be returned, so this drives the catalog branch itself
    against a namespace of 400 tools. The loop's offload notice names the file and tells the model
    to grep it, so the tool names stay reachable and it can then ask for the schemas it needs;
    refusing would leave a large namespace with no way to learn a single tool name."""
    async with _serving(_bulk_server(400)) as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._list_mcp_tools(ctx, mcp.ListMcpToolsInput(server=SERVER_NAME))
    assert result.is_error is False
    assert len(result.content[0].text) > mcp.MAX_LISTING_CHARS
    assert len(json.loads(result.content[0].text)["tools"]) == 400


async def test_a_reducible_schema_request_is_refused_through_the_tool(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal has to be reachable the way the model reaches it. Naming fifty tools at once is
    a request with a smaller one behind it, so the tool refuses and says to ask for the few it is
    about to call rather than handing back a fraction of fifty schemas."""
    async with _serving(_bulk_server(50)) as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        with pytest.raises(ValueError, match="50 schemas do not fit in a tool result"):
            await mcp._list_mcp_tools(
                ctx,
                mcp.ListMcpToolsInput(
                    server=SERVER_NAME,
                    tool_names=tuple(f"svc__tool_{index}" for index in range(50)),
                ),
            )


def test_the_listing_bound_is_the_bound_the_loop_offloads_past() -> None:
    """`MAX_LISTING_CHARS` is not a size this pack chose: it is exactly what a tool result carries
    before the loop writes it to a file, so a copy that drifted would refuse requests the loop
    would have delivered whole, or pass ones it then cuts."""
    assert mcp.MAX_LISTING_CHARS == MAX_TOOL_RESULT_CHARS


def test_the_catalog_is_far_smaller_than_the_schemas_it_replaces() -> None:
    """The reason the catalog exists. A namespace's full schemas run tens of thousands of
    characters and do not survive a tool result; the catalog has to fit in one whole."""
    schema = {
        "type": "object",
        "properties": {name: {"type": "string", "description": "x" * 200} for name in "abcdef"},
        "required": ["a"],
    }
    tools = [
        McpTool(
            name=f"svc__tool_{index}",
            description="Do a thing. " + "y" * 300,
            inputSchema=schema,
        )
        for index in range(80)
    ]
    catalog = len(json.dumps({"tools": [mcp._catalog_entry(tool) for tool in tools]}))
    schemas = len(json.dumps({"tools": [mcp._schema_entry(tool) for tool in tools]}))
    assert catalog * 4 < schemas
    assert catalog < mcp.MAX_LISTING_CHARS


async def test_call_mcp_tool_parses_structured_content(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(
                server=SERVER_NAME,
                tool_name="search",
                arguments={"query": "auth flow"},
            ),
        )
    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"hits": ["auth flow"]}


async def test_call_mcp_tool_joins_text_content_when_unstructured(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(
                server=SERVER_NAME,
                tool_name="two_lines",
                arguments={},
            ),
        )
    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"text": "line one\ntwo"}


async def test_call_mcp_tool_sends_the_configured_bearer_token(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(
                server=SERVER_NAME,
                tool_name="whoami",
                arguments={},
            ),
        )
    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"authorization": f"Bearer {AUTH_TOKEN}"}


async def test_call_mcp_tool_surfaces_a_tool_error_as_is_error(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _serving() as client_for:
        monkeypatch.setattr(mcp, "mcp_client", client_for)
        ctx = await _tool_context()
        result = await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(server=SERVER_NAME, tool_name="boom", arguments={}),
        )
    assert result.is_error is True
    assert result.content[0].text == "no such record"


async def test_an_unconfigured_server_name_fails_loud(db: None) -> None:
    ctx = await _tool_context()
    with pytest.raises(ValueError, match="no MCP server named 'other'"):
        await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(server="other", tool_name="x", arguments={}),
        )


async def test_call_mcp_tool_rejects_oversized_arguments(db: None) -> None:
    ctx = await _tool_context()
    with pytest.raises(mcp.McpError, match="arguments exceed"):
        await mcp._call_mcp_tool(
            ctx,
            mcp.CallMcpToolInput(
                server=SERVER_NAME,
                tool_name="search",
                arguments={"blob": "z" * (mcp.MAX_MCP_REQUEST_BYTES + 1)},
            ),
        )


def test_json_result_rejects_an_oversized_result() -> None:
    with pytest.raises(mcp.McpError, match="result exceeds"):
        mcp._json_result({"blob": "z" * (mcp.MAX_MCP_RESPONSE_BYTES + 1)})


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
    init_workspace_credentials(store)
    current_workspace.set(workspace_id)
    _, ext_by_tool = turn_tools((mcp.manifest(),), store, audience=conversation_audience(None))
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
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=ext_by_tool["call_mcp_tool"],
    )
