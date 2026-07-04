"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, the conversation's `member_id`, and `artifact_token_secret` with which
`share_file` mints the signed download URLs the web surface verifies. `read_paths` is the working
set that lets `edit` refuse to touch a file the turn has not read first. `connector_account` hands
a connector tool the broker's connected-account id it passes to the broker's server-side execute
API, resolved strictly from the turn-agent's own grants so a tool reaches only the turn-agent's
accounts. `skills` is the loadable
skill set for the deploy (core plus the active packs') that `load_skill` resolves against; it
defaults to the core floor so a context built without the loader still resolves the core three.
`cdp_provider` is the turn's selected browser transport and `find` its host-side element-ranking
hook — the browser tools build one per-turn surface from them on first use and register its `aclose`
on `cleanup`, the per-turn registry the loop drains at turn end so a CDP connection never outlives
its turn. An extension tool also gets `ext`, its owning extension's workspace-scoped
ExtensionContext; a builtin tool gets `ext=None`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from selfhost.blob import BlobStore
from selfhost.browser import CdpProvider, FindCompleter
from selfhost.ext.context import ExtensionContext
from selfhost.grants import ConnectUnavailable, GrantStore
from selfhost.o11y import log
from selfhost.sandbox.session import SandboxSession
from selfhost.schema.records import Agent, Turn
from selfhost.skills.runtime import CORE_SKILL_REGISTRY, SkillRegistry


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """A tool's visual output — the model sees the image, not a base64 string. `data` is base64;
    the engine folds it into the tool_result's ImageBlock the model client puts on the wire."""

    type: Literal["image"] = "image"
    media_type: str
    data: str


ContentBlock = Annotated[TextContent | ImageContent, Field(discriminator="type")]


class ToolResult(BaseModel):
    content: tuple[ContentBlock, ...]
    is_error: bool = False


@dataclass(frozen=True)
class SpawnResult:
    """What a spawn hands back: the child turn's id, plus the validated typed output when the
    parent awaited it (foreground). A background spawn returns the id and no output yet."""

    turn_id: UUID
    output: BaseModel | None


@dataclass(frozen=True)
class SubagentStatus:
    """The terminal state of one already-spawned child turn as the lifecycle tools report it: its
    turn id, terminal status, and its final answer text (the profile's JSON output when it ended
    `done`, otherwise the terminal message)."""

    turn_id: UUID
    status: str
    text: str


class Spawn(Protocol):
    """Delegate a subtask to a named subagent profile: validate the payload against the profile's
    input schema, run a child turn, and (foreground) return its schema-validated output."""

    async def __call__(
        self, profile: str, payload: dict[str, Any], background: bool = False
    ) -> SpawnResult: ...


class SubagentControl(Protocol):
    """Lifecycle operations on already-spawned background subagents, keyed by the child turn id a
    background `spawn` returns: await their terminals, or cancel a running one. Threaded onto the
    ToolContext from the same Subagents workflow that backs `spawn`."""

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]: ...

    async def cancel(self, turn_id: UUID) -> SubagentStatus: ...


@dataclass(eq=False)
class TurnCleanup:
    """Per-turn async cleanup registry: a tool registers an `aclose` here on first use of a resource
    it opens for the turn (the browser surface's CDP connection, a hosted-session lease), and the
    loop drains it once at turn end — closing in reverse order of registration — so the turn never
    leaks a connection whether it ended done, failed, or cancelled. Identity-keyed (`eq=False`), so
    a tool building a once-per-turn resource can cache it against this registry across the per-call
    context copies dispatch hands it."""

    _closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def register(self, aclose: Callable[[], Awaitable[None]]) -> None:
        self._closers.append(aclose)

    async def drain(self) -> None:
        while self._closers:
            closer = self._closers.pop()
            try:
                await closer()
            except Exception as error:
                log("turn.cleanup.failed", error=repr(error))


@dataclass(frozen=True)
class ToolContext:
    sandbox: SandboxSession
    blob: BlobStore
    turn: Turn
    agent: Agent
    spawn: Spawn
    member_id: UUID | None
    artifact_token_secret: str
    grants: GrantStore | None = None
    subagents: SubagentControl | None = None
    read_paths: set[str] = field(default_factory=set)
    skills: SkillRegistry = CORE_SKILL_REGISTRY
    ext: ExtensionContext | None = None
    cdp_provider: CdpProvider | None = None
    find: FindCompleter | None = None
    cleanup: TurnCleanup = field(default_factory=TurnCleanup)

    async def connector_account(self, provider: str, account_id: str | None = None) -> str:
        """The broker's connected-account id a connector tool passes to the broker's server-side
        execute API (the broker holds the account's token and injects it itself, so no sentinel and
        no egress proxy). Resolved strictly from the turn's own workspace and agent, so a tool
        executes only against the turn-agent's accounts, never another agent's. `account_id` targets
        a specific account when the agent holds several; omitted, any provider grant answers. Fails
        loud when no grant subsystem is configured or the agent holds no matching grant."""
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
        return grant.account_id
