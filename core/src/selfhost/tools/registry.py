"""Named tool definitions, their wire schemas, and lookup.

A `ToolDef` binds a name and its input model to the handler that runs it; `schema()` renders the
pair the model client puts on the wire. `ToolRegistry` is the frozen set the engine dispatches
against — it rejects a duplicate name at construction and fails loud on an unknown lookup."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from selfhost.models import ToolSchema
from selfhost.tools.context import ToolContext, ToolResult


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolDef, ...]

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(tool.schema() for tool in self.tools)

    def get(self, name: str) -> ToolDef:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")
