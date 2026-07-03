"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, and the turn/agent it runs under. `read_paths` is the working
set that lets `edit` refuse to touch a file the turn has not read first."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from selfhost.blob import BlobStore
from selfhost.sandbox.session import SandboxSession
from selfhost.schema.records import Agent, Turn


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


ContentBlock = TextContent


class ToolResult(BaseModel):
    content: tuple[ContentBlock, ...]
    is_error: bool = False


@dataclass(frozen=True)
class ToolContext:
    sandbox: SandboxSession
    blob: BlobStore
    turn: Turn
    agent: Agent
    read_paths: set[str] = field(default_factory=set)
