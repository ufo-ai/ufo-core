"""The JSON contract and callable set for the live-turn sandbox tool bridge."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, model_validator

from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.objects import ObjectVerbs
from ufo.runtime.tools.registry import ToolDef, ToolRegistry

TOOL_BRIDGE_HOST = "tools.ufo.internal"
TOOL_BRIDGE_URL = f"https://{TOOL_BRIDGE_HOST}"
TOOL_BRIDGE_URL_ENV = "UFO_TOOL_BRIDGE_URL"

type BridgeToolName = Literal[
    "object_list",
    "object_get",
    "object_explain",
    "object_apply",
    "object_delete",
    "object_action",
    "list_external_tools",
    "describe_external_tools",
    "search_connector_tools",
    "call_external_tool",
]
BRIDGE_TOOL_NAMES = frozenset(
    {
        "object_list",
        "object_get",
        "object_explain",
        "object_apply",
        "object_delete",
        "object_action",
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
    }
)


class ToolBridgeRequest(BaseModel):
    request_id: UUID
    action: Literal["list", "get_schema", "execute"]
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _matches_action(self) -> "ToolBridgeRequest":
        if self.action == "list":
            if self.tool_name is not None or self.arguments:
                raise ValueError("list requests take no tool name or arguments")
        elif self.tool_name is None:
            raise ValueError(f"{self.action} requests require a tool name")
        return self


class ToolBridgeListedTool(BaseModel):
    name: str
    description: str


class ToolBridgeToolList(BaseModel):
    tools: list[ToolBridgeListedTool]


class ToolBridgeIntent(BaseModel):
    request_id: UUID
    tool: BridgeToolName
    input: dict[str, JsonValue]


class ToolBridgeSuccess(BaseModel):
    ok: Literal[True] = True
    result: JsonValue


class ToolBridgeFailure(BaseModel):
    ok: Literal[False] = False
    error: str


type ToolBridgeResponse = ToolBridgeSuccess | ToolBridgeFailure


@dataclass(frozen=True, slots=True)
class ToolBridgePrincipal:
    workspace_id: UUID
    turn_id: UUID
    connections: tuple[UUID, ...]


class ToolBridgeRequester(Protocol):
    """Describe or execute bridge tools under one resolved live-run principal."""

    async def request(
        self, run: ToolBridgePrincipal, request: ToolBridgeRequest
    ) -> ToolBridgeResponse: ...


def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]:
    """The bridge's callable set: the object verbs (with `object_action`, through which every
    bound action is reached) plus the connector gateway tools. A bound def never enters — it is
    named through `object_action`, and its short name may legally repeat a bridge tool's."""
    object_tools = ObjectVerbs({}).tools()
    extension_tools = tuple(
        tool
        for manifest in manifests
        for tool in (
            *manifest.tools,
            *(tool for connector in manifest.connectors for tool in connector.tools),
        )
        if tool.bound is None and tool.name in BRIDGE_TOOL_NAMES
    )
    tools = (*object_tools, *extension_tools)
    ToolRegistry(tools)
    return tools
