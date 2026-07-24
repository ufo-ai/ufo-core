"""The dynamic connector tool surface: discover connectors, describe a connector's real tools, and
execute one server-side — generic over every installed broker.

A broker fronts hundreds of services and thousands of tools, so the agent never holds a fixed
per-provider tool — it searches. Every call reads the turn's `ConnectorRegistry` and dispatches to
the broker that registered the provider: `list_external_tools` filters the registry locally;
`describe_external_tools` and `search_connector_tools` read the broker's catalog;
`call_external_tool` executes on the broker's server-side API, authenticated by the turn-agent's
connected account (bound through `/connect`). The broker holds the account's token and injects it
itself, so an execute reaches only the broker's own API.

Files cross through the workspace, moved by the sandbox itself: an argument carrying the
`workspace_file` vocabulary is hashed in the container, staged to where the broker mints
(`stage_upload`, a presigned PUT), and replaced by the broker's own argument value; every file a
tool produces (`file_outputs`, presigned URLs on the broker's file store) is fetched into
`/workspace/connector_files/` and listed in the result. Both transfers ride the egress proxy under
the grant's declared transfer hosts — the bytes never cross the serve process."""

import asyncio
import json
import mimetypes
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from pydantic import BaseModel, Field

from ufo.sdk.connectors import (
    WORKSPACE_FILE_KEY,
    BrokerFile,
    BrokerTool,
    ConnectorEntry,
    ConnectorRegistry,
    UnknownBrokerTool,
)
from ufo.sdk.context import JsonValue
from ufo.sdk.sandbox import WORKSPACE_DIR, workspace_path
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

CONNECTOR_FILES_DIR = "connector_files"
FALLBACK_FILENAME = "download"
FALLBACK_MIMETYPE = "application/octet-stream"
TRANSFER_TIMEOUT_SECONDS = 600
TRANSFER_MAX_BYTES = 100 * 1024 * 1024
WORKSPACE_FILES_RESULT_KEY = "workspace_files"
CATALOG_SEARCH_LIMIT = 10
MAX_LIST_QUERIES = 8

MD5_PREFLIGHT_PROG = """
import hashlib, sys
h = hashlib.md5(usedforsecurity=False)
size = 0
with open(sys.argv[1], "rb") as f:
    while chunk := f.read(1048576):
        h.update(chunk)
        size += len(chunk)
print(h.hexdigest())
print(size)
"""


class ListExternalToolsInput(BaseModel):
    queries: tuple[str, ...] = Field(
        max_length=MAX_LIST_QUERIES,
        description="Search keywords. Use single-word queries — split multi-word searches into "
        "separate keywords, e.g. ['Microsoft', 'email'] not ['Microsoft email']. Multiple queries "
        "searched in parallel. Use 'select:<source_id>' to fetch a specific connector by exact ID.",
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
    account_id: str | None = Field(
        default=None,
        description="Connected-account ID. Required when this agent has multiple accounts.",
    )
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
    targets = list(dict.fromkeys(q.removeprefix("select:").strip().lower() for q in args.queries))
    for target in targets:
        for provider, entry in sorted(registry.entries.items()):
            if provider in seen:
                continue
            haystack = f"{provider} {entry.label}".lower()
            if target and target not in haystack:
                continue
            seen.add(provider)
            matches.append({"source_id": provider, "label": entry.label})
    catalogs = await asyncio.gather(
        *(registry.search_catalog(target, CATALOG_SEARCH_LIMIT) for target in targets)
    )
    for rows in catalogs:
        for row in rows:
            if row.provider in seen:
                continue
            seen.add(row.provider)
            matches.append({"source_id": row.provider, "label": row.label})
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
    account_id = await ctx.connector_account(args.source_id, args.account_id)
    call = _ConnectorCall(ctx=ctx, entry=entry, slug=args.tool_name)
    return _json_result(await call.run(args.arguments, account_id))


@dataclass(frozen=True)
class _ConnectorCall:
    """One connector tool execution, top to bottom: stage every `workspace_file` argument to the
    broker's file store, execute server-side with the granted account, and fetch the produced
    files back into the workspace — the private steps below in execution order. Both transfers run
    inside the sandbox, so the bytes never cross the serve process."""

    ctx: ToolContext
    entry: ConnectorEntry
    slug: str

    async def run(self, arguments: dict[str, JsonValue], account_id: str) -> dict[str, object]:
        staged = {key: await self._staged_value(item) for key, item in arguments.items()}
        response = await self.entry.broker.execute(
            self.ctx.turn.workspace_id,
            self.entry.provider,
            self.slug,
            staged,
            account_id,
            self.ctx.idempotency_key,
        )
        files = await self._fetched_files(self.entry.broker.file_outputs(response))
        return {**response, WORKSPACE_FILES_RESULT_KEY: files} if files else response

    async def _staged_value(self, value: object) -> object:
        """An argument value with every `{"workspace_file": path}` staged to the broker's file
        store and replaced by the broker's own argument naming the staged object — a file crosses
        as that reference, its bytes PUT by the sandbox."""
        match value:
            case dict() if set(value) == {WORKSPACE_FILE_KEY}:
                path = value[WORKSPACE_FILE_KEY]
                if not isinstance(path, str) or not path:
                    raise ValueError(f"{WORKSPACE_FILE_KEY} must be a workspace path string")
                return await self._stage_file(path)
            case dict():
                return {key: await self._staged_value(item) for key, item in value.items()}
            case list():
                return [await self._staged_value(item) for item in value]
            case _:
                return value

    async def _stage_file(self, path: str) -> dict[str, object]:
        """Stage one workspace file: hash it in the container, ask the broker where it goes, PUT
        the bytes there from inside the sandbox, and return the argument value that names it. A
        broker answering a dedup hit (no put_url) already holds the bytes, so the PUT is skipped."""
        scoped = workspace_path(path)
        preflight = await self.ctx.sandbox.bash(
            f"python3 -c {shlex.quote(MD5_PREFLIGHT_PROG)} {shlex.quote(scoped)}",
            timeout_s=TRANSFER_TIMEOUT_SECONDS,
        )
        if preflight.exit_code != 0:
            raise ValueError(preflight.stderr.strip() or f"cannot read workspace file {path!r}")
        digest, _, size = preflight.stdout.strip().partition("\n")
        if int(size) > TRANSFER_MAX_BYTES:
            raise ValueError(
                f"workspace file {path!r} is {size} bytes, over the {TRANSFER_MAX_BYTES}-byte limit"
            )
        filename = PurePosixPath(scoped).name
        mimetype = mimetypes.guess_type(filename)[0] or FALLBACK_MIMETYPE
        staged = await self.entry.broker.stage_upload(
            self.ctx.turn.workspace_id, self.entry.provider, self.slug, filename, mimetype, digest
        )
        if staged.put_url is None:
            return staged.argument
        content_type = shlex.quote(f"Content-Type: {staged.content_type}")
        put = await self.ctx.sandbox.bash(
            f"curl -fsS -T {shlex.quote(scoped)} -H {content_type} "
            f"--url {shlex.quote(staged.put_url)}",
            timeout_s=TRANSFER_TIMEOUT_SECONDS,
        )
        if put.exit_code != 0:
            raise RuntimeError(put.stderr.strip() or f"staging workspace file {path!r} failed")
        return staged.argument

    async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]:
        """Fetch each produced file from its presigned URL into the workspace, from inside the
        sandbox — under a fresh `connector_files/<uuid>/` so no fetch clobbers another file."""
        saved: list[dict[str, str]] = []
        for file in files:
            basename = PurePosixPath(file.name.replace("\\", "/")).name
            safe = basename if basename not in ("", ".", "..") else FALLBACK_FILENAME
            target = f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/{uuid4()}/{safe}"
            fetched = await self.ctx.sandbox.bash(
                f"curl -fsSL --create-dirs --max-filesize {TRANSFER_MAX_BYTES} "
                f"-o {shlex.quote(target)} --url {shlex.quote(file.url)}",
                timeout_s=TRANSFER_TIMEOUT_SECONDS,
            )
            if fetched.exit_code != 0:
                raise RuntimeError(
                    fetched.stderr.strip()
                    or f"fetching produced file {safe!r} into the workspace failed"
                )
            saved.append({"name": safe, "workspace_path": target})
        return saved


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
            "Search available external connectors (github, slack, notion, ...), not their tools. "
            "The broker brokers hundreds of services, so always search by keyword rather than "
            "assuming — queries match the live catalog. Returns connector catalog rows: source_id, "
            "label. Call this before claiming you can't access something — there is very likely a "
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
            "'github', arguments: {owner: 'acme', repo: 'widgets', state: 'open'}}. Pass "
            "account_id when this agent has more than one connected account for the source. A "
            "parameter whose schema asks for 'workspace_file' takes a file from the workspace — "
            'pass {"workspace_file": "/workspace/<path>"} and the file is staged to the '
            "connector automatically. Files a tool returns are saved into the workspace and "
            "listed under 'workspace_files' in the result with their paths."
        ),
        input_model=CallExternalToolInput,
        handler=call_external_tool,
        untrusted=True,
        side_effecting=True,
    ),
)
