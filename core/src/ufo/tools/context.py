"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, the message requester who gates private authorization, the exact
conversation audience that scopes disclosure, and `artifact_token_secret` with which
`share_file` mints the signed download URLs the web surface verifies. `read_paths` is the working
set that lets `edit` refuse to touch a file the turn has not read first. `connector_account` hands
a connector tool the broker's connected-account id it passes to the broker's server-side execute
API, resolved from the turn-agent's grants admitted to the requester (their own plus shared)
so a tool reaches only the accounts its requester may use. `skills` is the loadable
skill set for the deploy (core plus the active packs') that `load_skill` resolves against; it
defaults to the core floor so a context built without the loader still resolves the core three.
`loaded_skills` is the turn's live record of which of those workflows the context already holds, so
a repeat `load_skill` re-mounts the files without injecting the instructions twice.
`cdp_provider` is the turn's selected browser transport and `find` its host-side element-ranking
hook — the browser tools build one per-turn surface from them on first use and register its `aclose`
on `cleanup`, the per-turn registry the loop drains at turn end so a CDP connection never outlives
its turn. `search_provider` is the deploy's selected web-search backend (None when no research
extension is active) — the research tools call it host-side, so the provider reads its key in the
serve process and the sandbox never sees it. `idempotency_key` is `{turn}/{name}/{call_id}`, folded
on only for a `side_effecting` tool: its dedup key against a cross-attempt resume — an external
write's header, a spawned child's identity — so the effect applies at most once; a read tool gets
`None`. `meter_images` and `meter_videos` book what a paid image or video generation cost onto this
turn's ledger: metering is core's, so a provider extension prices its own call and writes it through
here. An extension tool also gets `ext`, its owning extension's workspace-scoped ExtensionContext; a
builtin tool gets `ext=None`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field

from ufo.accounting import record_image_usage, record_video_usage
from ufo.audience import SHARED_AUDIENCE, Audience, audience_subjects, conversation_audience
from ufo.blob import BlobStore
from ufo.browser import CdpProvider, FindCompleter
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialRequests
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, SourceReader
from ufo.grants import ConnectUnavailable, Grant, GrantStore
from ufo.o11y import log
from ufo.sandbox.session import SandboxSession
from ufo.schema import tables
from ufo.schema.records import Agent, TerminalFrame, Turn
from ufo.search import SearchProvider
from ufo.seats import member_is_admin
from ufo.skills.runtime import CORE_SKILL_REGISTRY, LoadedSkills, SkillRegistry
from ufo.subjects import member_subject
from ufo.workspace import ws_current


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


class UntrustedContentError(Exception):
    """An error whose message embeds untrusted (page-derived, third-party) content — a subagent
    with `untrusted_output` returning output that fails validation. The engine walls the message
    exactly as it walls an untrusted result, so the content never reaches the model as
    instructions."""


class UnknownSubagentProfile(Exception):
    """A spawn named a profile the registry does not hold. Its message names the bad profile and
    lists the registered profile names, so the spawning tool surfaces an error the model retries
    against a valid name instead of dead-ending on a bare KeyError."""


class ToolResult(BaseModel):
    content: tuple[ContentBlock, ...]
    is_error: bool = False
    untrusted: bool = False


@dataclass(frozen=True)
class SpawnResult:
    """The exact child and, once finished, its terminal and validated output. A background spawn
    has neither terminal nor output yet. `untrusted` carries the profile's `untrusted_output`
    declaration, so the returning tool result is walled as data."""

    turn_id: UUID
    conversation_id: UUID
    output: BaseModel | None
    terminal: TerminalFrame | None = None
    untrusted: bool = False


@dataclass(frozen=True)
class SubagentStatus:
    """The terminal state of one already-spawned child turn as the lifecycle tools report it: its
    turn id, terminal status, and its final answer text (the profile's JSON output when it ended
    `done`, otherwise the terminal message). `untrusted` carries the profile's `untrusted_output`
    declaration."""

    turn_id: UUID
    status: str
    text: str
    untrusted: bool = False


class Spawn(Protocol):
    """Delegate a subtask to a named subagent profile: validate the payload against the profile's
    input schema, run a child turn, and (foreground) return its schema-validated output.

    `dedup_key` makes the child's identity deterministic from the parent turn and the key rather
    than random, so a caller that re-runs on crash recovery (a dispatch step dying mid-await, a
    fanned-out `wide_*` step re-executing) reconnects to the child it already spawned instead of
    respawning it — the same key yields the same child turn, its admit is idempotent, and a child
    that already finished is awaited, not recomputed. Every tool-step caller derives its key from
    `ctx.idempotency_key`: the recorded round freezes call ids, so distinct model calls still get
    distinct children while a re-executed step reconnects. A keyless spawn mints a fresh child per
    execution — on recovery that is a duplicate doing the same work.

    `delivers_result` says nobody will await this child: it hands its own output to the parent's
    conversation when it finishes. A caller that awaits — foreground, or background bounded by its
    own timeout — leaves it false, or the parent reads the same answer twice."""

    async def __call__(
        self,
        profile: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
    ) -> SpawnResult: ...


class SubagentControl(Protocol):
    """Operations on already-spawned background subagents, keyed by child turn id. `result` reads
    a finished child's exact terminal and validated output; `wait` bounds a hold inside one tool;
    `cancel` stops a running child; `message` admits an idempotent follow-up. A child's output
    otherwise arrives on the parent's conversation when the child ends. Threaded onto ToolContext
    from the same Subagents workflow that backs `spawn`."""

    async def result(self, turn_id: UUID) -> SpawnResult: ...

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]: ...

    async def cancel(self, turn_id: UUID) -> SubagentStatus: ...

    async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus: ...


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
class ConnectorConnection:
    id: UUID
    account_id: str
    owner_member_id: UUID


@dataclass(frozen=True)
class ToolContext:
    sandbox: SandboxSession
    blob: BlobStore
    turn: Turn
    agent: Agent
    spawn: Spawn
    speaker_member_id: UUID | None
    audience: Audience
    artifact_token_secret: str
    on_behalf_of_member_id: UUID | None = None
    grants: GrantStore | None = None
    subagents: SubagentControl | None = None
    read_paths: set[str] = field(default_factory=set)
    idempotency_key: str | None = None
    skills: SkillRegistry = CORE_SKILL_REGISTRY
    loaded_skills: LoadedSkills = field(default_factory=LoadedSkills)
    ext: ExtensionContext | None = None
    cdp_provider: CdpProvider | None = None
    search_provider: SearchProvider | None = None
    connectors: ConnectorRegistry | None = None
    find: FindCompleter | None = None
    requestable_credentials: CredentialRequests | None = None
    public_base_url: str | None = None
    cleanup: TurnCleanup = field(default_factory=TurnCleanup)

    @property
    def acting_member_id(self) -> UUID | None:
        """The member whose authority and capabilities this call may use: the author of its
        validated `requested_by` message, otherwise the initiator carried by a scheduled turn or
        subagent. Granting acts stay live-member-only."""
        return (
            self.speaker_member_id
            if self.speaker_member_id is not None
            else self.on_behalf_of_member_id
        )

    @property
    def effective_audience(self) -> Audience:
        """The exact audience a write belongs to: the conversation's own, taking the requester's
        private subject only in a workspace-shared conversation. A private room and a Slack Connect
        channel are memory spaces in their own right — what is said there belongs to that space, so
        stamping it with the requester would carry it into every other conversation that member
        speaks in, leaking a private room's fact to the next room and another org's to the
        workspace. A member who wants a private note makes it in their own conversation."""
        acting = self.acting_member_id
        if acting is None or self.audience != SHARED_AUDIENCE:
            return self.audience
        return conversation_audience(acting)

    @property
    def read_subjects(self) -> frozenset[str]:
        """What the conversation and exact requester may jointly read: the conversation's own
        subjects plus the requester's private one. The requester contributes only their own subject,
        never the workspace-shared atom their private audience also reads — a Slack Connect
        audience is sealed against internal content, and speaking there does not unseal it."""
        subjects = audience_subjects(self.audience)
        acting = self.acting_member_id
        if acting is None:
            return subjects
        return subjects | {member_subject(acting)}

    def source_reader(self) -> SourceReader:
        """Who is asking for a source's synced pages: this turn's agent, the member speaking right
        now, and what the two may jointly read. The requester is the live speaker rather than
        `acting_member_id`, because the main agent's owner exception is a live-work privilege — a
        scheduled run or a subagent carries its initiator's authority everywhere else, but reaches
        a source only through that agent's own grant."""
        return SourceReader(
            agent_id=self.turn.agent_id,
            requesting_member_id=self.speaker_member_id,
            subjects=self.read_subjects,
        )

    async def meter_images(self, model: str, images: int, micro_usd: int) -> None:
        """Book a generated image's provider charge onto this turn under the ledger's `images`
        dimension. An image model is priced per image rather than per token and is not in the
        `ModelRegistry`, so the extension that called the provider reads the charge off its own
        response; the ledger write is core's, and doing it here binds the spend to this turn's
        workspace, member and agent exactly as a token burn is bound."""
        async with workspace_tx() as connection:
            await record_image_usage(
                connection, self.turn.workspace_id, self.turn.id, model, images, micro_usd
            )

    async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None:
        """Book a generated video's provider charge onto this turn under the ledger's `videos`
        dimension, on the same terms as `meter_images`: a video model is priced per output second
        rather than per token and is not in the `ModelRegistry`, so the extension that called the
        provider reads the charge off its own response and core binds the spend to this turn."""
        async with workspace_tx() as connection:
            await record_video_usage(
                connection, self.turn.workspace_id, self.turn.id, model, videos, micro_usd
            )

    async def speaker_is_admin(self) -> bool:
        """Whether this call's requesting member is a workspace admin. Workspace-wide acts gate on
        the requester, so a background call cannot exercise admin authority."""
        if self.speaker_member_id is None:
            return False
        async with workspace_tx() as connection:
            return await member_is_admin(
                connection,
                self.turn.workspace_id,
                self.speaker_member_id,
            )

    async def agent_is_main(self) -> bool:
        async with workspace_tx() as connection:
            return bool(
                (
                    await connection.execute(
                        sa.select(tables.agent.c.is_main).where(
                            tables.agent.c.id == self.turn.agent_id,
                            tables.agent.c.workspace_id == self.turn.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
            )

    async def begin_credential_authorization(self, slot: str, payload: str) -> str:
        requests, member_id = await self._credential_authorization(slot)
        return requests.authorize(self.turn.workspace_id, member_id, slot, payload)

    async def open_credential_authorization(self, slot: str, sealed: str) -> str:
        requests, member_id = await self._credential_authorization(slot)
        return requests.open_authorization(sealed, self.turn.workspace_id, member_id, slot)

    async def fulfill_credential_authorization(
        self, slot: str, sealed: str, plaintext: str
    ) -> None:
        requests, member_id = await self._credential_authorization(slot)
        requests.open_authorization(sealed, self.turn.workspace_id, member_id, slot)
        await ws_current().put_credential(slot, plaintext)

    async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]:
        if self.speaker_member_id is None:
            raise ValueError("credential authorization requires a speaking member")
        if self.ext is None or slot not in self.ext.credentials.declared:
            raise ValueError(f"this extension does not declare credential slot {slot!r}")
        if self.requestable_credentials is None:
            raise ValueError("no credential key is configured — this deploy cannot store secrets")
        if not await self.speaker_is_admin():
            raise ValueError("only a workspace admin can authorize credential slots")
        return self.requestable_credentials, self.speaker_member_id

    async def connector_account(self, provider: str, account_id: str | None = None) -> str:
        """The broker's connected-account id a connector tool passes to the broker's server-side
        execute API (the broker holds the account's token and injects it itself, so no sentinel and
        no egress proxy). Resolved strictly from the turn's own workspace and agent, so a tool
        executes only against the turn-agent's accounts, never another agent's. `account_id`
        targets any account this turn may use; omitted, the acting member's own private grants are
        preferred and agent-shared ones are the fallback — exactly one account must exist in the
        winning tier. Fails loud when no grant subsystem is configured or the selection is absent
        or ambiguous."""
        return (await self.connector_connection(provider, account_id)).account_id

    async def connector_connection(
        self, provider: str, account_id: str | None = None
    ) -> ConnectorConnection:
        """The exact member-owned connection generation this turn may use. Source registration
        persists its id so disconnecting and reconnecting the same external account cannot revive a
        prior member's sync."""
        private, shared = await self._connector_account_tiers(provider)
        if account_id is not None:
            match = next(
                (grant for grant in (*private, *shared) if grant.account_id == account_id),
                None,
            )
            if match is not None:
                return ConnectorConnection(
                    id=match.connection_id,
                    account_id=match.account_id,
                    owner_member_id=match.owner_member_id,
                )
            raise ValueError(
                f"no active {provider!r} account {account_id!r} is available to this turn"
            )
        preferred = private or shared
        if not preferred:
            raise ValueError(
                f"no {provider!r} account is available to this turn — connect one with "
                "connect_account"
            )
        if len(preferred) > 1:
            raise ValueError(
                f"multiple active {provider!r} accounts; pass account_id as one of "
                f"{[grant.account_id for grant in preferred]!r}"
            )
        match = preferred[0]
        return ConnectorConnection(
            id=match.connection_id,
            account_id=match.account_id,
            owner_member_id=match.owner_member_id,
        )

    async def connector_accounts(self, provider: str) -> tuple[str, ...]:
        """The connected-account ids this turn may use for one provider: the acting member's own
        grants plus any grant shared with the agent's audience — the runtime check that makes
        a connection private by default. The acting member is the speaker, or the member the turn
        acts on behalf of (`on_behalf_of_member_id`) for a speakerless scheduled fire or subagent,
        so a member's own scheduled job and delegated subagents keep their private connections; a
        turn with no member at all resolves only shared grants."""
        private, shared = await self._connector_account_tiers(provider)
        return tuple(sorted({grant.account_id for grant in (*private, *shared)}))

    async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]:
        if self.grants is None:
            raise ConnectUnavailable("grants unavailable: no credential key configured")
        acting = self.acting_member_id
        granted = await self.grants.active_grants()
        private = sorted(
            (
                grant
                for grant in granted
                if grant.provider == provider
                and not grant.connection_shared
                and grant.owner_member_id == acting
            ),
            key=lambda grant: grant.account_id,
        )
        shared = sorted(
            (grant for grant in granted if grant.provider == provider and grant.connection_shared),
            key=lambda grant: grant.account_id,
        )
        return private, shared
