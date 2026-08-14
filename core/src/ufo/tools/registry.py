"""Named tool definitions, their wire schemas, and lookup.

A `ToolDef` binds a name and its input model to the handler that runs it; `schema()` renders the
pair the model client puts on the wire. `untrusted` marks a tool whose result carries
attacker-controllable content (a fetched page, a search snippet, a connector API response) — the
engine walls such a result in a data-only span so the model never reads it as instructions.
`side_effecting` marks a tool whose handler performs an external write or egress (a connector POST,
an MCP call, a local durable write): the engine folds a per-call `idempotency_key` onto the context
such a handler receives, so a cross-attempt resume can dedup the external effect at the provider. A
deterministic read (a bash read, a search, a page read) leaves it `False` and gets no key — DBOS
step memoization already makes re-executing it harmless. The same declaration decides an adopted
turn's guidance preemption: a keyed re-execution dedups (a spawn reattaches to its child, a send
dedups at the provider) and must run, since skipping it strands the keyed work and a re-issued
call would duplicate it under a fresh call id; only an unkeyed redo yields to queued member
guidance. `ToolRegistry` is the frozen set the engine
dispatches against — it rejects a duplicate name at construction and fails loud on an unknown
lookup."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ufo.models.interface import ToolSchema
from ufo.tools.context import ToolContext, ToolResult

REQUESTED_BY = "requested_by"
REQUESTED_BY_DESCRIPTION = (
    "Message ref that explicitly requested this call. Required for any member-specific authority "
    "or capability, including admin actions; omit only for conversation-common work."
)


@dataclass(frozen=True)
class ToolDef[ModelT: BaseModel]:
    name: str
    description: str
    input_model: type[ModelT]
    handler: Callable[[ToolContext, ModelT], Awaitable[ToolResult]]
    untrusted: bool = False
    side_effecting: bool = False
    subagent_default: bool = False
    parallel_safe: bool = False
    profile_only: bool = False

    def schema(self) -> ToolSchema:
        input_schema = self.input_model.model_json_schema()
        properties = input_schema.setdefault("properties", {})
        properties[REQUESTED_BY] = {
            "type": "string",
            "format": "uuid",
            "description": REQUESTED_BY_DESCRIPTION,
        }
        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema=input_schema,
        )


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolDef[Any], ...]

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")
        collisions = sorted(
            tool.name for tool in self.tools if REQUESTED_BY in tool.input_model.model_fields
        )
        if collisions:
            raise ValueError(f"tool inputs reserve {REQUESTED_BY!r}: {', '.join(collisions)}")

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(tool.schema() for tool in self.tools)

    def get(self, name: str) -> ToolDef[Any]:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")
