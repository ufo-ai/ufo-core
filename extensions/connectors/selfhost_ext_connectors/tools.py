"""The dynamic Composio tool surface: discover connectors, describe a connector's real tools, and
execute one server-side.

Composio brokers hundreds of services and thousands of tools, so the agent never holds a fixed
per-provider tool — it searches. `list_external_tools` filters the connector catalog locally;
`describe_external_tools` fetches a connector's real tool slugs and input schemas from Composio;
`call_external_tool` executes a tool on Composio's server-side execute API, authenticated by the
deploy's Composio key and the turn-agent's connected account (bound through `/connect`). Composio
holds the account's OAuth token and injects it itself, so a dynamic tool never touches the sandbox
egress proxy — it reaches only Composio's own API."""

import json
import re

from pydantic import BaseModel, Field

from selfhost.sdk.context import JsonValue
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from selfhost_ext_connectors import composio
from selfhost_ext_connectors.composio import CONNECTORS, EXTERNAL_USER_PREFIX, ComposioError

DISCOVERY_DESCRIPTION_CAP = 240
NOT_FOUND = 404


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
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'slack', 'gcal'.")
    tool_names: tuple[str, ...] = Field(
        default=(),
        description="Exact tool names to get schemas for, from list_external_tools results. Omit "
        "to discover the connector's tools via `query`.",
    )
    query: str = Field(
        default="", description="Discovery query to find matching tools when tool_names is omitted."
    )


class CallExternalToolInput(BaseModel):
    tool_name: str = Field(description="Exact tool name from list_external_tools results.")
    source_id: str = Field(description="The connector source ID, e.g. 'github', 'gcal'.")
    arguments: dict[str, JsonValue] = Field(
        description="Arguments for the connector tool as a dict. Pass {} for tools that take no "
        "parameters."
    )


class SearchConnectorToolsInput(BaseModel):
    source_id: str = Field(description="The connector source ID to search within.")
    query: str = Field(description="Search query to find matching tools in the connector.")


async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult:
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in args.queries:
        target = query.removeprefix("select:").strip().lower()
        for connector, spec in sorted(CONNECTORS.items()):
            if connector in seen:
                continue
            haystack = f"{connector} {spec.toolkit} {spec.label}".lower()
            if target and target not in haystack:
                continue
            seen.add(connector)
            matches.append({"source_id": connector, "toolkit": spec.toolkit, "label": spec.label})
    return _json_result({"connectors": matches})


async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult:
    client = composio.composio_client()
    schemas: dict[str, object] = {}
    unresolved: list[str] = []
    for name in args.tool_names:
        try:
            schemas[name] = await client.tool_schema(name)
        except ComposioError as error:
            if error.status != NOT_FOUND:
                raise
            unresolved.append(name)
    result: dict[str, object] = {"source_id": args.source_id, "schemas": schemas}
    if args.query or unresolved or not args.tool_names:
        toolkit = _toolkit(args.source_id)
        listed = await client.list_tools(toolkit, _discovery_query(args.query, unresolved))
        result["availableTools"] = _discovered_tools(listed)
    if unresolved:
        result["unresolved"] = unresolved
    return _json_result(result)


async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult:
    client = composio.composio_client()
    connected_account_id = await ctx.connector_account(args.source_id)
    user_id = f"{EXTERNAL_USER_PREFIX}{ctx.turn.workspace_id}"
    try:
        response = await client.execute_tool(
            args.tool_name, args.arguments, user_id, connected_account_id
        )
    except ComposioError as error:
        if error.status != NOT_FOUND:
            raise
        raise await _tool_not_found(client, args, error) from error
    return _json_result(response)


async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult:
    client = composio.composio_client()
    payload = await composio.search_connector_tools(
        client, ctx.turn.workspace_id, args.source_id, args.query
    )
    return _json_result(payload)


async def _tool_not_found(
    client: composio.ComposioClient, args: CallExternalToolInput, error: ComposioError
) -> ComposioError:
    """A 404 from execute, augmented with the source's real tool slugs so the model's next attempt
    is informed instead of another blind guess at the naming convention. Augmentation is
    best-effort: if the discovery lookup fails, the original 404 stands."""
    try:
        toolkit = _toolkit(args.source_id)
        query = _discovery_query("", [args.tool_name])
        tools = _discovered_tools(await client.list_tools(toolkit, query))
        if not tools and query:
            tools = _discovered_tools(await client.list_tools(toolkit, ""))
    except (ComposioError, ValueError, KeyError):
        return error
    if not tools:
        return error
    names = ", ".join(tool["slug"] for tool in tools)
    return ComposioError(error.status, f"{error.body} — tools available on {toolkit}: {names}")


def _toolkit(source_id: str) -> str:
    spec = CONNECTORS.get(source_id)
    return spec.toolkit if spec is not None else source_id


def _discovery_query(explicit: str, unresolved: list[str]) -> str:
    """The catalog search query: the caller's explicit keywords, else the deduped words of the slugs
    that missed (so a guessed `GITHUB_LIST_PULL_REQUEST_REVIEWS` searches 'github list pull request
    reviews' and surfaces the real slug)."""
    if explicit:
        return explicit
    words = re.sub(r"[^a-z0-9]+", " ", " ".join(unresolved).lower()).split()
    return " ".join(dict.fromkeys(words))


def _discovered_tools(listed: dict[str, object]) -> list[dict[str, str]]:
    """Project a Composio `list_tools` response to the connector's real slugs and short
    descriptions."""
    items = listed.get("items")
    tools: list[dict[str, str]] = []
    if not isinstance(items, list):
        return tools
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("name")
        if not isinstance(slug, str) or not slug:
            continue
        description = item.get("description")
        tools.append(
            {
                "slug": slug,
                "description": description[:DISCOVERY_DESCRIPTION_CAP]
                if isinstance(description, str)
                else "",
            }
        )
    return tools


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


CONNECTOR_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="list_external_tools",
        description=(
            "List available external connectors (github, slack, ...), not their tools. Filter by "
            "queries to search connector name/toolkit/label. Returns connector catalog rows: "
            "source_id, toolkit, label. Call this before claiming you can't access something — "
            "there may be a connector available. Use 'select:<source_id>' syntax to fetch a "
            "specific connector by exact source ID. To find a connector's real tools, call "
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
            "Semantic tool discovery for one connector via Composio's Tool Router. Pass source_id "
            "plus a natural-language use case (e.g. 'comment on a pull request') to get matching "
            "real tool slugs and input schemas in 'tools', plus the router's 'plan' (recommended "
            "steps), 'guidance', and 'pitfalls' for executing them. Richer than "
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
    ),
)
