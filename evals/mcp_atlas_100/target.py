from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath

from httpx import AsyncClient, HTTPError
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.target import TargetResult
from evals.mcp_atlas_100.profile import (
    APPROVED_EXTERNAL_SERVERS,
    APPROVED_TOOLS,
    EXECUTABLE_PROFILE,
    PUBLIC_SERVERS,
)
from ufo.schema.records import ReasoningEffort
from ufo.sdk.context import ModelAccess
from ufo.sdk.models import (
    Message,
    ModelRequest,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)

MAX_TOOL_CALLS = 100
MAX_ROUNDS = 256
MAX_TOOL_REQUEST_BYTES = 1_048_576
MAX_TOOL_RESULT_BYTES = 1_048_576
MAX_OUTPUT_TOKENS = 16_000
MET_OBJECT_TOOL = "met-museum_get-museum-object"
CLI_COMMAND_TOOL = "cli-mcp-server_run_command"
CLI_COMMANDS = frozenset({"cat", "find", "ls"})
DATA_ROOT = PurePosixPath("/data")
SHELL_CONTROL_CHARACTERS = frozenset(";&|`$><()\n\r")


class AtlasTool(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    description: str | None = None
    input_schema: dict[str, object] = Field(
        validation_alias=AliasChoices("inputSchema", "input_schema")
    )


@dataclass(frozen=True)
class McpAtlasTarget:
    public_client: AsyncClient
    model: ModelAccess
    model_name: str
    system: str
    reasoning: ReasoningEffort
    external_client: AsyncClient | None = None

    def __post_init__(self) -> None:
        if (
            self.external_client is not None
            and self.public_client.base_url == self.external_client.base_url
        ):
            raise ValueError("MCP-Atlas public and external endpoints must be distinct")

    async def preflight(self, required_tool_servers: dict[str, str]) -> frozenset[str]:
        EXECUTABLE_PROFILE.validate(required_tool_servers)
        required_tools = frozenset(required_tool_servers)
        catalog = await self._catalog_for(required_tool_servers)
        missing = sorted(required_tools - catalog.keys())
        if missing:
            raise RuntimeError(
                f"MCP-Atlas sandbox is missing {len(missing)} selected tools: {', '.join(missing)}"
            )
        return frozenset(catalog)

    async def run(
        self,
        prompt: str,
        enabled_tools: tuple[str, ...],
        tool_servers: dict[str, str],
    ) -> TargetResult:
        try:
            return await self._run(prompt, enabled_tools, tool_servers)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            return TargetResult(CapabilityOutput("", (), (reason,)), False, reason)

    async def _run(
        self,
        prompt: str,
        enabled_tools: tuple[str, ...],
        tool_servers: dict[str, str],
    ) -> TargetResult:
        enabled_mappings = {tool: tool_servers[tool] for tool in enabled_tools}
        EXECUTABLE_PROFILE.validate(enabled_mappings)
        catalog = await self._catalog_for(enabled_mappings)
        schemas = tuple(
            ToolSchema(
                name=name,
                description=catalog[name].description or "",
                input_schema=catalog[name].input_schema,
            )
            for name in enabled_tools
            if name in catalog
        )
        allowed_tools = frozenset(schema.name for schema in schemas)
        messages: list[Message] = [Message(role="user", content=prompt)]
        calls: list[ToolInvocation] = []
        for _ in range(MAX_ROUNDS):
            assistant = await self.model.turn(
                ModelRequest(
                    model=self.model_name,
                    system=self.system,
                    messages=tuple(messages),
                    max_tokens=MAX_OUTPUT_TOKENS,
                    tools=schemas,
                    reasoning=self.reasoning,
                )
            )
            messages.append(assistant)
            tool_uses = (
                ()
                if isinstance(assistant.content, str)
                else tuple(block for block in assistant.content if isinstance(block, ToolUseBlock))
            )
            if not tool_uses:
                return TargetResult(
                    CapabilityOutput(_answer(assistant), tuple(calls)),
                    clean=True,
                )
            if len(calls) + len(tool_uses) > MAX_TOOL_CALLS:
                reason = f"MCP-Atlas case exceeded {MAX_TOOL_CALLS} tool calls"
                return TargetResult(
                    CapabilityOutput(_answer(assistant), tuple(calls), (reason,)),
                    False,
                    reason,
                )
            results: list[ToolResultBlock] = []
            for call in tool_uses:
                arguments = dict(call.input)
                if call.name not in allowed_tools:
                    result = f"MCP-Atlas tool {call.name!r} is not enabled for this case"
                    is_error = True
                else:
                    try:
                        if call.name == CLI_COMMAND_TOOL:
                            arguments = _validated_cli_arguments(arguments)
                        if call.name == MET_OBJECT_TOOL:
                            arguments["returnImage"] = False
                    except ValueError as error:
                        result = str(error)
                        is_error = True
                    else:
                        result, is_error = await self._call(
                            self._client(enabled_mappings[call.name]), call.name, arguments
                        )
                calls.append(
                    ToolInvocation(
                        name=call.name,
                        input=arguments,
                        result=result,
                        has_result=True,
                        is_error=is_error,
                    )
                )
                results.append(
                    ToolResultBlock(tool_use_id=call.id, content=result, is_error=is_error)
                )
            messages.append(Message(role="user", content=tuple(results)))
        reason = f"MCP-Atlas case exceeded {MAX_ROUNDS} model rounds"
        return TargetResult(CapabilityOutput("", tuple(calls), (reason,)), False, reason)

    async def _catalog_for(self, tool_servers: dict[str, str]) -> dict[str, AtlasTool]:
        servers = frozenset(tool_servers.values())
        external_servers = servers & APPROVED_EXTERNAL_SERVERS
        external_client = self._client(min(external_servers)) if external_servers else None
        catalogs: list[dict[str, AtlasTool]] = []
        if servers - APPROVED_EXTERNAL_SERVERS:
            catalogs.append(
                await self._route_catalog(
                    self.public_client,
                    servers - APPROVED_EXTERNAL_SERVERS,
                    APPROVED_EXTERNAL_SERVERS,
                    "public",
                )
            )
        if external_client is not None:
            catalogs.append(
                await self._route_catalog(
                    external_client,
                    external_servers,
                    PUBLIC_SERVERS,
                    "external",
                )
            )
        return {name: tool for catalog in catalogs for name, tool in catalog.items()}

    async def _route_catalog(
        self,
        client: AsyncClient,
        route_servers: frozenset[str],
        rejected_servers: frozenset[str],
        route: str,
    ) -> dict[str, AtlasTool]:
        catalog = await self._catalog(client)
        misplaced = sorted(name for name in catalog if APPROVED_TOOLS.get(name) in rejected_servers)
        if misplaced:
            raise RuntimeError(
                f"MCP-Atlas {route} endpoint advertises tools from the other trust boundary: "
                f"{', '.join(misplaced)}"
            )
        return {
            name: tool
            for name, tool in catalog.items()
            if APPROVED_TOOLS.get(name) in route_servers
        }

    def _client(self, server: str) -> AsyncClient:
        if server not in APPROVED_EXTERNAL_SERVERS:
            return self.public_client
        if self.external_client is None:
            raise RuntimeError(
                "MCP-Atlas external tools require --mcp-atlas-external-url or "
                "MCP_ATLAS_EXTERNAL_URL"
            )
        return self.external_client

    async def _catalog(self, client: AsyncClient) -> dict[str, AtlasTool]:
        try:
            response = await client.post("/list-tools")
            response.raise_for_status()
            tools = tuple(AtlasTool.model_validate(item) for item in response.json())
        except (HTTPError, TypeError, ValueError, ValidationError) as error:
            raise RuntimeError(f"MCP-Atlas sandbox preflight failed: {error}") from error
        by_name = {tool.name: tool for tool in tools}
        if len(by_name) != len(tools):
            raise RuntimeError("MCP-Atlas sandbox returned duplicate tool names")
        return by_name

    async def _call(
        self,
        client: AsyncClient,
        name: str,
        arguments: dict[str, object],
    ) -> tuple[str, bool]:
        try:
            payload = {"tool_name": name, "tool_args": arguments, "use_cache": True}
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            if len(body) > MAX_TOOL_REQUEST_BYTES:
                return (
                    f"MCP-Atlas tool {name!r} request exceeds {MAX_TOOL_REQUEST_BYTES} bytes",
                    True,
                )
            response = await client.post(
                "/call-tool", content=body, headers={"content-type": "application/json"}
            )
            response.raise_for_status()
            text = json.dumps(response.json(), ensure_ascii=False, separators=(",", ":"))
        except (HTTPError, TypeError, ValueError) as error:
            return f"MCP-Atlas tool {name!r} failed: {error}", True
        if len(text.encode()) > MAX_TOOL_RESULT_BYTES:
            return f"MCP-Atlas tool {name!r} result exceeds {MAX_TOOL_RESULT_BYTES} bytes", True
        return text, False


def _answer(message: Message) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


def _validated_cli_arguments(arguments: dict[str, object]) -> dict[str, object]:
    if set(arguments) != {"command"} or not isinstance(arguments["command"], str):
        raise ValueError("MCP-Atlas CLI requires one string command")
    try:
        tokens = shlex.split(arguments["command"])
    except ValueError as error:
        raise ValueError("MCP-Atlas CLI command is invalid") from error
    if not tokens or tokens[0] not in CLI_COMMANDS:
        raise ValueError("MCP-Atlas CLI permits only ls, cat, or find with /data paths")
    if tokens[0] != "ls" and len(tokens) == 1:
        raise ValueError("MCP-Atlas CLI cat and find require a path")
    operands = tokens[1:] or [str(DATA_ROOT)]
    resolved: list[str] = []
    for token in operands:
        path = PurePosixPath(token)
        if (
            token.startswith("-")
            or ".." in path.parts
            or any(character in SHELL_CONTROL_CHARACTERS for character in token)
            or (path.is_absolute() and not path.is_relative_to(DATA_ROOT))
        ):
            raise ValueError("MCP-Atlas CLI permits only /data paths without options")
        resolved.append(str(path if path.is_absolute() else DATA_ROOT / path))
    return {"command": shlex.join((tokens[0], *resolved))}
