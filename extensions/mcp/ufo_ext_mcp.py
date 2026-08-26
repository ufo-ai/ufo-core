"""The MCP tool pack: use the tools a workspace's configured MCP servers expose.

MCP (Model Context Protocol) servers expose their own tool catalogs, so the agent never holds a
fixed per-tool registration — it discovers and invokes dynamically, the shape the connectors pack
uses for Composio. `list_mcp_tools` discovers a server's tools; `call_mcp_tool` invokes one,
parsing `structuredContent` (else joined text `content`) out of the result. Both ride a fastmcp
Streamable-HTTP client — the same transport the connectors pack ships — so the MCP `initialize`
handshake, `Mcp-Session-Id`, SSE framing, and `tools/list` cursor pagination are fastmcp's, and a
spec-compliant or FastMCP server works. The serialized `arguments` are bounded to 1 MiB before the
call and the serialized result is bounded to 1 MiB after it, next to the call.

A workspace names its servers in the `mcp_servers` BYOK credential slot — a JSON map of
`name -> {url, auth}` the extension reads in-process (the token stays in the core process, never
enters the sandbox and never rides the egress proxy, since the server URL is workspace-supplied and
unknown at manifest time). A failing tool call surfaces to the model as an is_error tool result.

An MCP server is external and its results are attacker-controllable content — the tool descriptions
say so to the model, and both tool defs are the untrusted-content boundary the loop marks."""

import json
import re

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import TextContent as McpTextContent
from mcp.types import Tool as McpTool
from pydantic import BaseModel, Field, field_validator

from ufo.sdk.context import JsonValue
from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "mcp"
VERSION = "0.1.0"
LIST_MCP_TOOLS = "list_mcp_tools"
CALL_MCP_TOOL = "call_mcp_tool"
MCP_SERVERS_SLOT = "mcp_servers"
ONE_MIB = 1_048_576
MAX_MCP_REQUEST_BYTES = ONE_MIB
MAX_MCP_RESPONSE_BYTES = ONE_MIB
MCP_TIMEOUT_SECONDS = 30.0
MCP_URL_RE = re.compile(r"^https?://.+")
MAX_SUMMARY_CHARS = 160
# What the loop's tool-result bound carries before it offloads.
MAX_LISTING_CHARS = 25_600

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


class McpError(RuntimeError):
    """An MCP round-trip breached a byte bound — fail loud, never a silent truncation."""


class McpServer(BaseModel):
    url: str
    auth: str | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if MCP_URL_RE.fullmatch(value) is None:
            raise ValueError("MCP server url must be http or https")
        return value


class McpServersConfig(BaseModel):
    """The `mcp_servers` credential value: the workspace's named MCP servers. Crosses the boundary
    from the stored BYOK secret, so it validates at construction (each url is http/https)."""

    servers: dict[str, McpServer]


class ListMcpToolsInput(BaseModel):
    server: str = Field(description="The name of an MCP server configured for this workspace.")
    tool_names: tuple[str, ...] = Field(
        default=(),
        description="Exact tool names to return full input schemas for. Omit to browse the "
        "server's catalog first.",
    )


class CallMcpToolInput(BaseModel):
    server: str = Field(description="The configured MCP server's name.")
    tool_name: str = Field(description="The tool's exact name, from list_mcp_tools.")
    arguments: dict[str, JsonValue] = Field(
        description="The tool's parameters as a JSON object matching its input schema."
    )


def mcp_client(server: McpServer) -> Client:
    """A fastmcp Streamable-HTTP client bound to one server — the seam a test overrides to point at
    an in-process FastMCP server (or a stub) with no live endpoint. fastmcp owns the `initialize`
    handshake, `Mcp-Session-Id`, SSE framing, and `tools/list` pagination; the Bearer token (when
    the workspace configured one) rides the `authorization` header."""
    headers = {"authorization": f"Bearer {server.auth}"} if server.auth is not None else {}
    return Client(StreamableHttpTransport(server.url, headers=headers), timeout=MCP_TIMEOUT_SECONDS)


async def _server(ctx: ToolContext, name: str) -> McpServer:
    """Resolve one workspace-configured MCP server by name from the `mcp_servers` credential slot.
    Fails loud when the slot is unset, undeclared, or names no such server — never a blind call."""
    if ctx.ext is None:
        raise RuntimeError("mcp tool dispatched without its ExtensionContext")
    config = McpServersConfig.model_validate_json(await ctx.ext.credentials.get(MCP_SERVERS_SLOT))
    server = config.servers.get(name)
    if server is None:
        raise ValueError(
            f"no MCP server named {name!r} is configured; "
            f"configured servers: {sorted(config.servers)}"
        )
    return server


async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult:
    """Two stages, because one server's full catalog does not fit in a tool result.

    A sizable namespace answers `tools/list` with every tool's complete JSON Schema — tens of
    thousands of characters, well past the result bound. So the default response carries each
    tool's summary and parameter names, small enough to survive whole, and full schemas come back
    only for the tools the model asks for."""
    server = await _server(ctx, args.server)
    async with mcp_client(server) as client:
        tools = await client.list_tools()
    if not args.tool_names:
        catalog: list[JsonValue] = [_catalog_entry(tool) for tool in tools]
        return _json_result({"server": args.server, "tools": catalog})
    by_name = {tool.name: tool for tool in tools}
    unknown = [name for name in args.tool_names if name not in by_name]
    if unknown:
        raise ValueError(
            f"server {args.server!r} exposes no tool named {', '.join(unknown)}; "
            f"list without tool_names to see the catalog"
        )
    schemas: list[JsonValue] = [_schema_entry(by_name[name]) for name in args.tool_names]
    return _bounded_schemas({"server": args.server, "tools": schemas}, len(args.tool_names))


def _idempotent(tool: McpTool) -> bool:
    annotations = tool.annotations
    return bool(annotations.idempotentHint) if annotations is not None else False


def _catalog_entry(tool: McpTool) -> JsonValue:
    """One tool as the catalog shows it: what it does, what it takes, what it insists on. The
    summary is the description's first sentence — enough to choose between tools, and the rest
    arrives with the schema."""
    schema = tool.inputSchema or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    summary = _summary(tool.description or "")
    parameters: list[JsonValue] = list(sorted(properties))
    mandatory: list[JsonValue] = list(sorted(n for n in required if isinstance(n, str)))
    return {
        "name": tool.name,
        "summary": summary,
        "parameters": parameters,
        "required": mandatory,
        "idempotent": _idempotent(tool),
    }


def _summary(description: str) -> str:
    """The first sentence of the first line. MCP descriptions are commonly docstrings whose opening
    line is the summary and whose remainder is an `Args:` block, so splitting on sentences alone
    drags that block in and truncates mid-word."""
    first_line = description.strip().split("\n")[0].strip()
    sentence, _, _ = first_line.partition(". ")
    return sentence[:MAX_SUMMARY_CHARS]


def _schema_entry(tool: McpTool) -> JsonValue:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": tool.inputSchema,
        "idempotent": _idempotent(tool),
    }


async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult:
    server = await _server(ctx, args.server)
    if len(json.dumps(args.arguments, separators=(",", ":")).encode()) > MAX_MCP_REQUEST_BYTES:
        raise McpError("MCP request arguments exceed the byte bound")
    async with mcp_client(server) as client:
        result = await client.call_tool(args.tool_name, dict(args.arguments), raise_on_error=False)
    if result.is_error:
        text = _joined_text(result.content) or "MCP tool call failed"
        return ToolResult(content=(TextContent(text=_bounded(text)),), is_error=True)
    structured = result.structured_content
    if isinstance(structured, dict):
        return _json_result(structured)
    return _json_result({"text": _joined_text(result.content)})


def _joined_text(content: list[object]) -> str:
    return "\n".join(block.text for block in content if isinstance(block, McpTextContent))


def _bounded(text: str) -> str:
    if len(text.encode()) > MAX_MCP_RESPONSE_BYTES:
        raise McpError("MCP result exceeds the byte bound")
    return text


def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult:
    """Refuse a schema request the loop would offload, and only when a smaller one exists.

    A catalog that overflows still offloads usefully: the loop's notice names the file and tells
    the model to grep it, so the tool names — all the catalog carries — stay reachable, and it can
    then ask for the few schemas it needs. Refusing would leave it with nothing. Several schemas
    are the opposite: the model asked for exact spellings, and a preview of the first fraction is
    what sent it guessing in the first place. A single schema past the bound has no smaller request
    behind it and `call_mcp_tool` requires it, so it offloads rather than sealing that tool off.

    Measured on the string `_json_result` emits, not a compact rendering: the two differ by about a
    twentieth, which is the width of the band where a request passes the check and is cut anyway."""
    if tools > 1 and len(json.dumps(payload)) > MAX_LISTING_CHARS:
        raise ValueError(
            f"{tools} schemas do not fit in a tool result; ask for the few tools you are about to "
            "call"
        )
    return _json_result(payload)


def _json_result(payload: dict[str, JsonValue]) -> ToolResult:
    return ToolResult(content=(TextContent(text=_bounded(json.dumps(payload))),))


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=LIST_MCP_TOOLS,
                description=LIST_MCP_TOOLS_DESCRIPTION,
                input_model=ListMcpToolsInput,
                handler=_list_mcp_tools,
                untrusted=True,
            ),
            ToolDef(
                name=CALL_MCP_TOOL,
                description=CALL_MCP_TOOL_DESCRIPTION,
                input_model=CallMcpToolInput,
                handler=_call_mcp_tool,
                untrusted=True,
            ),
        ),
        credentials=(
            CredentialSlot(
                name=MCP_SERVERS_SLOT,
                description=(
                    "BYOK MCP servers as a JSON map of name -> {url, auth}. Read in-process to "
                    "reach each server's endpoint; auth (optional) is sent as a Bearer token. No "
                    "injection target: the server URL is workspace-supplied, so the call runs in "
                    "the core process, not through the sandbox egress proxy."
                ),
            ),
        ),
    )
