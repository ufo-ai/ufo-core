"""Named tool definitions, their wire schemas, and lookup.

A `ToolDef` binds a name and its input model to the handler that runs it; `schema()` renders the
pair the model client puts on the wire. `untrusted` marks a tool whose result carries
attacker-controllable content (a fetched page, a search snippet, a connector API response) — the
engine walls such a result in a data-only span so the model never reads it as instructions.
`ToolRegistry` is the frozen set the engine dispatches against — it rejects a duplicate name at
construction and fails loud on an unknown lookup."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from selfhost.models.interface import ToolSchema
from selfhost.tools.context import ToolContext, ToolResult


@dataclass(frozen=True)
class ToolDef[ModelT: BaseModel]:
    name: str
    description: str
    input_model: type[ModelT]
    handler: Callable[[ToolContext, ModelT], Awaitable[ToolResult]]
    untrusted: bool = False
    subagent_default: bool = False

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolDef[Any], ...]

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(tool.schema() for tool in self.tools)

    def get(self, name: str) -> ToolDef[Any]:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")
