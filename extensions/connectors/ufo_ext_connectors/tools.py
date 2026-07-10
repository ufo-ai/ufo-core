"""The dynamic connector tool surface: discover connectors, describe a connector's real tools, and
execute one server-side — generic over every installed broker.

A broker fronts hundreds of services and thousands of tools, so the agent never holds a fixed
per-provider tool — it searches. Every call reads the turn's `ConnectorRegistry` and dispatches to
the broker that registered the provider: `list_external_tools` filters the registry locally;
`describe_external_tools` and `search_connector_tools` read the broker's catalog;
`call_external_tool` executes on the broker's server-side API, authenticated by the turn-agent's
connected account (bound through `/connect`). The broker holds the account's token and injects it
itself, so a dynamic tool never touches the sandbox egress proxy — it reaches only the broker's own
API."""

import json
import re

from pydantic import BaseModel, Field

from ufo.sdk.connectors import BrokerTool, ConnectorRegistry, UnknownBrokerTool
from ufo.sdk.context import JsonValue
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult


class ListExternalToolsInput(BaseModel):
    queries: tuple[str, ...] = Field(
        description="Search keywords. Use single-word queries — split multi-word searches into "
        "separate keywords, e.g. ['Microsoft', 'email'] not ['Microsoft email']. Multiple queries "
        "searched in parallel. Use 'select:<source_id>' to fetch a specific connector by exact ID."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class DescribeExternalToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'slack', 'gmail'.")
    tool_names: tuple[str, ...] = Field(
        default=(),
        description="Exact tool names to get schemas for, from list_external_tools results. Omit "
        "to discover the connector's tools via `query`.",
    )
    query: str = Field(
        default="", description="Discovery query to find matching tools when tool_names is omitted."
    )


class CallExternalToolInput(BaseModel):
    tool_name: str = Field(description="Exact tool name from describe_external_tools results.")
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'gmail'.")
    arguments: dict[str, JsonValue] = Field(
        description="Arguments for the connector tool as a dict. Pass {} for tools that take no "
        "parameters."
    )
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description shown in the activity timeline.",
    )


class SearchConnectorToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID to search within.")
    query: str = Field(description="Search query to find matching tools in the connector.")


async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult:
    registry = _registry(ctx)
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in args.queries:
        target = query.removeprefix("select:").strip().lower()
        for provider, entry in sorted(registry.entries.items()):
            if provider in seen:
                continue
            haystack = f"{provider} {entry.label}".lower()
            if target and target not in haystack:
                continue
            seen.add(provider)
            matches.append({"source_id": provider, "label": entry.label})
    return _json_result({"connectors": matches})


async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    workspace_id = ctx.turn.workspace_id
    schemas: dict[str, object] = {}
    unresolved: list[str] = []
    for name in args.tool_names:
        try:
            described = await entry.broker.schema(workspace_id, entry.provider, name)
        except UnknownBrokerTool:
            unresolved.append(name)
            continue
        schemas[name] = _tool_json(described)
    result: dict[str, object] = {"source_id": args.source_id, "schemas": schemas}
    if args.query or unresolved or not args.tool_names:
        listed = await entry.broker.tools(
            workspace_id, entry.provider, _discovery_query(args.query, unresolved)
        )
        result["availableTools"] = [
            {"slug": tool.slug, "description": tool.description} for tool in listed
        ]
    if unresolved:
        result["unresolved"] = unresolved
    return _json_result(result)


async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    account_id = await ctx.connector_account(args.source_id)
    response = await entry.broker.execute(
        ctx.turn.workspace_id,
        entry.provider,
        args.tool_name,
        args.arguments,
        account_id,
        ctx.idempotency_key,
    )
    return _json_result(response)


async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult:
    entry = _registry(ctx).entry(args.source_id)
    found = await entry.broker.search(ctx.turn.workspace_id, entry.provider, args.query)
    return _json_result(
        {
            "connector": args.source_id,
            "tools": [_tool_json(tool) for tool in found.tools],
            "plan": list(found.plan),
            "guidance": list(found.guidance),
            "pitfalls": list(found.pitfalls),
        }
    )


def _registry(ctx: ToolContext) -> ConnectorRegistry:
    if ctx.connectors is None:
        raise RuntimeError("connector tools dispatched without the turn's connector registry")
    return ctx.connectors


def _tool_json(tool: BrokerTool) -> dict[str, object]:
    return {"slug": tool.slug, "description": tool.description, "input_schema": tool.input_schema}


def _discovery_query(explicit: str, unresolved: list[str]) -> str:
    """The catalog search query: the caller's explicit keywords, else the deduped words of the slugs
    that missed (so a guessed `GITHUB_LIST_PULL_REQUEST_REVIEWS` searches 'github list pull request
    reviews' and surfaces the real slug)."""
    if explicit:
        return explicit
    words = re.sub(r"[^a-z0-9]+", " ", " ".join(unresolved).lower()).split()
    return " ".join(dict.fromkeys(words))


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


CONNECTOR_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="list_external_tools",
        description=(
            "List available external connectors (github, slack, ...), not their tools. Filter by "
            "queries to search connector name/label. Returns connector catalog rows: source_id, "
            "label. Call this before claiming you can't access something — there may be a "
            "connector available. Use 'select:<source_id>' syntax to fetch a specific connector by "
            "exact source ID. To find a connector's real tools, call "
            "describe_external_tools(source_id, query=...)."
        ),
        input_model=ListExternalToolsInput,
        handler=list_external_tools,
    ),
    ToolDef(
        name="describe_external_tools",
        description=(
            "Discover and describe a connector's real tools. Never guess slugs. Pass source_id "
            "plus a natural-language query (e.g. 'list pull request reviews') to get the "
            "connector's matching real slugs in 'availableTools'; pass source_id with no query to "
            "list its top tools. Pass exact tool_names to fetch their full input schemas — MUST be "
            "done before call_external_tool. Any name that is not a real slug is returned under "
            "'unresolved', with the connector's real slugs in 'availableTools' to use instead."
        ),
        input_model=DescribeExternalToolsInput,
        handler=describe_external_tools,
    ),
    ToolDef(
        name="search_connector_tools",
        description=(
            "Semantic tool discovery for one connector. Pass source_id plus a natural-language "
            "use case (e.g. 'comment on a pull request') to get matching real tool slugs and "
            "input schemas in 'tools', plus any 'plan' (recommended steps), 'guidance', and "
            "'pitfalls' the connector's broker surfaces for executing them. Richer than "
            "describe_external_tools when you know the goal but not the tool; still call "
            "call_external_tool to run a returned slug."
        ),
        input_model=SearchConnectorToolsInput,
        handler=search_connector_tools,
    ),
    ToolDef(
        name="call_external_tool",
        description=(
            "Execute an external connector tool. PREREQUISITE: Must call describe_external_tools "
            "first to get the input schema. The tool's own parameters go nested under 'arguments', "
            "never at the top level — e.g. {tool_name: 'GITHUB_LIST_PULL_REQUESTS', source_id: "
            "'github', arguments: {owner: 'acme', repo: 'widgets', state: 'open'}}."
        ),
        input_model=CallExternalToolInput,
        handler=call_external_tool,
        untrusted=True,
        side_effecting=True,
    ),
)
