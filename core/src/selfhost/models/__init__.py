"""Model access: one ModelClient interface, provider clients behind it."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel

from selfhost.schema.records import Usage


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ModelRequest(BaseModel):
    model: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int


class TextDelta(BaseModel):
    text: str


ModelEvent = TextDelta | Usage


class ModelResponseTruncated(RuntimeError):
    """The model hit its max_tokens budget mid-response (Anthropic stop_reason=max_tokens /
    OpenAI finish_reason=length). Raised loudly rather than delivering a cut-off completion as a
    finished turn — the budget is reasoning-inclusive, so raise max_tokens if it recurs."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
