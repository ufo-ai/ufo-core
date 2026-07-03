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


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
