"""The capability-scoped view a tool handler receives, and the result it returns.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, the speaking member who gates authorization, the conversation audience
member that scopes disclosure, and `artifact_token_secret` with which
`share_file` mints the signed download URLs the web surface verifies. `read_paths` is the working
set that lets `edit` refuse to touch a file the turn has not read first. `connector_account` hands
a connector tool the broker's connected-account id it passes to the broker's server-side execute
API, resolved from the turn-agent's grants admitted to the speaking member (their own plus shared)
so a tool reaches only the accounts the speaker may use. `skills` is the loadable
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
on only for a `side_effecting` tool: it passes it to its external write as a dedup header so a
cross-attempt resume applies the effect at most once; a read tool gets `None`. An extension tool
also gets `ext`, its owning extension's workspace-scoped ExtensionContext; a builtin tool gets
`ext=None`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.blob import BlobStore
from ufo.browser import CdpProvider, FindCompleter
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialRequests
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext
from ufo.grants import ConnectUnavailable, GrantStore
from ufo.o11y import log
from ufo.sandbox.session import SandboxSession
from ufo.schema.records import Agent, Turn
from ufo.search import SearchProvider
from ufo.seats import owner_member_id
from ufo.skills.runtime import CORE_SKILL_REGISTRY, LoadedSkills, SkillRegistry
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
    """What a spawn hands back: the child turn's id, plus the validated typed output when the
    parent awaited it (foreground). A background spawn returns the id and no output yet.
    `untrusted` carries the profile's `untrusted_output` declaration, so the returning tool result
    is walled as data."""

    turn_id: UUID
    output: BaseModel | None
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
    than random, so a caller that re-runs on crash recovery (a fanned-out `wide_*` tool step
    re-executing) reconnects to the child it already spawned instead of respawning it — the same
    key yields the same child turn, its admit is idempotent, and a child that already finished is
    awaited, not recomputed. A caller that wants a fresh child each call (`browser_task`,
    `spawn_subagent`) omits it."""

    async def __call__(
        self,
        profile: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult: ...


class SubagentControl(Protocol):
    """Lifecycle operations on already-spawned background subagents, keyed by the child turn id a
    background `spawn` returns: await their terminals, cancel a running one, or message one a
    follow-up that runs as its next turn. Threaded onto the ToolContext from the same Subagents
    workflow that backs `spawn`."""

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]: ...

    async def cancel(self, turn_id: UUID) -> SubagentStatus: ...

    async def message(self, turn_id: UUID, text: str) -> SubagentStatus: ...


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
    speaker_member_id: UUID | None
    audience_member_id: UUID | None
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
        """The member this turn acts on behalf of when using that member's own resources: the
        speaking member when one authored the turn, otherwise the initiator carried as
        `on_behalf_of_member_id` — the member who created the schedule a fire re-enters, or who
        spawned a subagent chain (copied forward at spawn). Capability USE resolves against this
        so a member's scheduled job or delegated subagent keeps their private connections; the
        granting acts (connect_account, credential slots) stay speaker-only, so a speakerless turn
        can use what its member already connected but can never grant anew. A turn with no member
        at all (an anonymous internal turn) resolves nothing private."""
        return (
            self.speaker_member_id
            if self.speaker_member_id is not None
            else self.on_behalf_of_member_id
        )

    async def speaker_is_owner(self) -> bool:
        """Whether this turn's speaking member is the workspace owner — the earliest-created member
        (there is no owner column; roles are deferred). Workspace-wide acts a tool drives (filling a
        shared credential slot) gate on this, so a joined teammate cannot rewrite what every member
        shares. No speaker is never the owner."""
        if self.speaker_member_id is None:
            return False
        async with workspace_tx() as connection:
            owner = await owner_member_id(connection, self.turn.workspace_id)
        return owner is not None and owner == self.speaker_member_id

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
        if self.audience_member_id != self.speaker_member_id:
            raise ValueError("credential authorization requires the speaker's private audience")
        if self.ext is None or slot not in self.ext.credentials.declared:
            raise ValueError(f"this extension does not declare credential slot {slot!r}")
        if self.requestable_credentials is None:
            raise ValueError("no credential key is configured — this deploy cannot store secrets")
        if not await self.speaker_is_owner():
            raise ValueError("only the workspace owner can authorize credential slots")
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
        private, shared = await self._connector_account_tiers(provider)
        if account_id is not None:
            if account_id in private or account_id in shared:
                return account_id
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
                f"{list(preferred)!r}"
            )
        return preferred[0]

    async def connector_accounts(self, provider: str) -> tuple[str, ...]:
        """The connected-account ids this turn may use for one provider: the acting member's own
        grants plus any grant shared with the agent's audience — the runtime check that makes
        a connection private by default. The acting member is the speaker, or the member the turn
        acts on behalf of (`on_behalf_of_member_id`) for a speakerless scheduled fire or subagent,
        so a member's own scheduled job and delegated subagents keep their private connections; a
        turn with no member at all resolves only shared grants."""
        private, shared = await self._connector_account_tiers(provider)
        return tuple(sorted({*private, *shared}))

    async def _connector_account_tiers(self, provider: str) -> tuple[list[str], list[str]]:
        if self.grants is None:
            raise ConnectUnavailable("grants unavailable: no credential key configured")
        acting = self.acting_member_id
        granted = await self.grants.active_grants(self.turn.workspace_id, self.turn.agent_id)
        private = sorted(
            grant.account_id
            for grant in granted
            if grant.provider == provider and not grant.shared and grant.grantor_member_id == acting
        )
        shared = sorted(
            grant.account_id for grant in granted if grant.provider == provider and grant.shared
        )
        return private, shared
