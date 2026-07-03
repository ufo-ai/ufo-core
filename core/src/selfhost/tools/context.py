"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, and `memory` (with the conversation's `member_id`) for recall and commit
scoped to the turn's subject. `read_paths` is the working set that lets `edit` refuse to touch a
file the turn has not read first. An extension tool also gets `ext`, its owning extension's
workspace-scoped ExtensionContext; a builtin tool gets `ext=None`."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from selfhost.blob import BlobStore
from selfhost.ext.context import ExtensionContext
from selfhost.memory.service import MemoryService
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
class SpawnResult:
    """What a spawn hands back: the child turn's id, plus the validated typed output when the
    parent awaited it (foreground). A background spawn returns the id and no output yet."""

    turn_id: UUID
    output: BaseModel | None


class Spawn(Protocol):
    """Delegate a subtask to a named subagent profile: validate the payload against the profile's
    input schema, run a child turn, and (foreground) return its schema-validated output."""

    async def __call__(
        self, profile: str, payload: dict[str, Any], background: bool = False
    ) -> SpawnResult: ...


@dataclass(frozen=True)
class ToolContext:
    sandbox: SandboxSession
    blob: BlobStore
    turn: Turn
    agent: Agent
    spawn: Spawn
    memory: MemoryService
    member_id: UUID | None
    read_paths: set[str] = field(default_factory=set)
    ext: ExtensionContext | None = None
