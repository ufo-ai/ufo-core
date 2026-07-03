"""Model access: one ModelClient interface, provider clients behind it."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

from selfhost.schema.records import Usage


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock, Field(discriminator="type")
]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | tuple[ContentBlock, ...]


class ToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ModelRequest(BaseModel):
    model: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int
    tools: tuple[ToolSchema, ...] = ()


class TextDelta(BaseModel):
    text: str


class ToolCallStart(BaseModel):
    id: str
    name: str


class ToolCallDelta(BaseModel):
    id: str
    partial_json: str


ModelEvent = TextDelta | ToolCallStart | ToolCallDelta | Usage


class ModelResponseTruncated(RuntimeError):
    """The model hit its max_tokens budget mid-response (Anthropic stop_reason=max_tokens /
    OpenAI finish_reason=length). Raised loudly rather than delivering a cut-off completion as a
    finished turn — the budget is reasoning-inclusive, so raise max_tokens if it recurs."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
