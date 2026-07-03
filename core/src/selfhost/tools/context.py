"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, `memory` (with the conversation's `member_id`) for recall and commit
scoped to the turn's subject, and `artifact_token_secret` with which `share_file` mints the signed
download URLs the web surface verifies. `read_paths` is the working set that lets `edit` refuse to
touch a file the turn has not read first. `connector_authorization` hands a connector tool the
sentinel `Authorization` value the egress proxy swaps for the turn-agent's real OAuth token, scoped
so a tool can only authenticate the turn-agent's own grants. An extension tool also gets `ext`, its
owning extension's workspace-scoped ExtensionContext; a builtin tool gets `ext=None`."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from selfhost.blob import BlobStore
from selfhost.ext.context import ExtensionContext
from selfhost.grants import ConnectUnavailable, GrantStore
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
    artifact_token_secret: str
    grants: GrantStore | None = None
    read_paths: set[str] = field(default_factory=set)
    ext: ExtensionContext | None = None

    async def connector_authorization(self, provider: str, account_id: str | None = None) -> str:
        """The `Authorization` header value a connector tool sends to reach `provider`: the exact
        sentinel the egress proxy swaps for the turn-agent's real OAuth token, so the raw secret
        never enters the sandbox. Resolves strictly the turn's own workspace and agent, so a tool
        builds a sentinel for the turn-agent's grants alone, never another agent's. `account_id`
        targets a specific account when the agent holds several for one provider (its sentinel draws
        only its own token at the proxy); omitted, any of the agent's grants for the provider
        answers. Fails loud when no grant subsystem is configured or the agent holds no matching
        grant."""
        if self.grants is None:
            raise ConnectUnavailable("grants unavailable: no credential key configured")
        granted = await self.grants.active_grants(self.turn.workspace_id, self.turn.agent_id)
        grant = next(
            (
                g
                for g in granted
                if g.provider == provider and (account_id is None or g.account_id == account_id)
            ),
            None,
        )
        if grant is None:
            target = f"{provider!r} account {account_id!r}" if account_id else f"{provider!r}"
            raise ValueError(f"agent has no active {target} grant to authenticate")
        return grant.sentinel_header
