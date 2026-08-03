"""The surface seam: the privileged capabilities a surface extension reaches into core for.

A surface is trusted infrastructure — it asserts a member's identity and admits turns as that member
— so unlike the scoped `ExtensionContext` (a ScopedStore, declared credential slots, a read-only
trajectory corpus), a surface context carries privileged capabilities a scoped extension may not
hold: admit a member turn onto the durable queue, resolve an external id to a member and a
conversation (linking a `surface_identity` on first contact — and joining a channel-verified email
whose domain is the workspace's own as a new member), and read the workspace's credential slots
in-process. Member admission consumes a pending one-time pause; the `invoke` capability held by
scheduled tasks and evals cannot.

One `SurfaceSpec`/`SurfaceContext` expresses both shapes of surface, differing only in how the reply
gets back and thus in how much of the one context each uses:

- A **durable** surface (Slack) is delivered to — its member is elsewhere. Declaring a two-phase
  delivery (`post` then best-effort `attach`) is what marks it durable; core runs the
  `WritebackPoller` that delivers at-least-once from the durable terminal frame (the hub is lossy,
  so never from a live frame). It declares one route (its ingest) and never tails.
- A **live** surface (web; core's built-in CLI is the twin) holds the member's connection open and
  tails the turn's frames off the hub as they publish, so no poller row is written for its
  conversations. It declares its own routes (page, admit, SSE tail, spend) and reaches the hub
  through the injected `TurnTailer`.

The poller only ever processes turns that registered a writeback — admission registers one for
every turn entering a durable-surface conversation, whoever admits it (a surface ingest, a
scheduled fire, an extension invoke) — so it is a no-op for a live surface, the efficient
downgrade, not a second seam."""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, JsonValue, ValidationError, field_validator
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request
from starlette.responses import Response

from ufo.accounting import AgentSpendReport, MemberSpendReport, SpendReport, SpendRollup
from ufo.agent_scope import agent as bind_agent
from ufo.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from ufo.audience import (
    SHARED_AUDIENCE,
    Audience,
    audience_member,
    conversation_audience,
    narrow_audience,
    parse_audience,
)
from ufo.blob import BlobNotFound, BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.connectors import DIRECT_ACCOUNT
from ufo.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialStore,
    DeclaredSlot,
    named_slots,
    open_credential_request,
)
from ufo.db import owner_tx, workspace_tx
from ufo.governance import prompt_digest
from ufo.grants import (
    ConnectHandoff,
    ConnectRequestInvalid,
    ConnectUnavailable,
    account_object_name,
    installed_connect_flow,
)
from ufo.hub import LiveFrame
from ufo.listings import page_of, page_query
from ufo.o11y import log
from ufo.sandbox.conversation import (
    WORKSPACE_WRITE_MAX_BYTES,
    ConversationSandbox,
    WorkspaceFile,
)
from ufo.sandbox.ingress_host import site_label
from ufo.sandbox.ingress_token import (
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    INGRESS_VIEW_TTL_SECONDS,
    IngressClaims,
    mint_ingress_token,
)
from ufo.scheduling import ScheduleStore
from ufo.schema import tables
from ufo.schema.records import (
    SUBAGENT_SURFACE,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_PENDING,
    AskUserInput,
    ConnectRequest,
    CredentialRequest,
    ReasoningEffort,
    TerminalFrame,
    TerminalStatus,
    ToolIntent,
    Turn,
    TurnContext,
)
from ufo.seats import SeatEntry, Seats, create_member, email_domain, workspace_domain
from ufo.skills.runtime import RuntimeSkill, SkillRegistry
from ufo.sources.backend import ConnectorSourceConfig, binding_name
from ufo.subjects import SHARED_SUBJECT
from ufo.transcript import (
    CompactionRecord,
    Conversation,
    decode,
    read_compaction_record,
    transcript_key,
)
from ufo.workspace import ws, ws_current

if TYPE_CHECKING:
    from ufo.ext.context import SourceReader
    from ufo.listings import ListingCursor, ListingPage
    from ufo.memory import MemoryMatch, MemorySearch
    from ufo.objects import BoundKind, ObjectPage

OPERATOR_EMAIL_DOMAIN = "metalcraft.ai"


AMBIENT_CONTEXT_ELEMENT = "channel_context"
MEMBER_MESSAGE_ELEMENT = "member_message"
ATTACHMENTS_ELEMENT = "attachments"
MARKER_BYTES = 4
_MEMBER_MESSAGE_RE = re.compile(
    rf"<{MEMBER_MESSAGE_ELEMENT}_(?P<marker>[0-9a-f]{{{MARKER_BYTES * 2}}})>\n"
    rf"(?P<said>.*)\n</{MEMBER_MESSAGE_ELEMENT}_(?P=marker)>",
    re.DOTALL,
)


def mint_marker() -> str:
    """The token one member message's elements are named with.

    Minted per message, so no text the prompt carries can name one: a bystander's words were already
    frozen in the ambient digest when it did not exist, and the member's own text is their own
    message anyway. That is what makes the elements a boundary rather than a convention, and why
    nothing escapes anybody's words: Slack's `<@U…>` mentions and `<https://…|label>` links, an
    inequality, a tag a member typed on purpose all reach the model as written."""
    return token_hex(MARKER_BYTES)


def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str:
    """The inbound text one member message becomes: already-rendered ambient context, the member's
    own words, then what their attachments delivered — each in its own element, named with `marker`.

    Run together as plain text these are one transcript whose last line is the member's message, so
    a message that trails off — an attachment that never arrived, a sentence ending on a colon —
    reads as a log with more to come and the turn answers by writing the member's next message
    instead of its own."""
    member = f"{MEMBER_MESSAGE_ELEMENT}_{marker}"
    fenced = f"{ambient}<{member}>\n{body}\n</{member}>"
    if not attachments:
        return fenced
    delivered = f"{ATTACHMENTS_ELEMENT}_{marker}"
    return f"{fenced}\n<{delivered}>\n{attachments}\n</{delivered}>"


def member_message_text(inbound: str) -> str:
    """The member's own words back out of a fenced inbound, for a projection that renders what the
    member said rather than the prompt the turn ran on.

    Exact, not a guess: the element is named with a marker minted for that one message, so the only
    text that can close it is text `fence_member_message` wrote. A member who types
    `</member_message>` closes nothing, which is why their bubble in the portal reads back as they
    wrote it. An inbound no surface fenced — a prepared intent, a subagent payload — is its own
    text."""
    found = _MEMBER_MESSAGE_RE.search(inbound)
    return inbound if found is None else found.group("said")


@dataclass(frozen=True)
class Admitted:
    """What one member admission did: the turn the message belongs to, and whether this admission is
    the one that put that turn's current run on the queue. Founding a turn, taking over a queued
    timer turn, and resuming a parked one each open a run; a message folded into a live turn, or
    deduped to a turn already admitted, joins a run another admission opened. The call is made under
    the conversation-row lock, so exactly one admission opens any run however many deliveries and
    replicas race for it — the line a surface starts a per-turn reporter on, since a reporter posts
    messages and a second one doubles the member's updates for the turn's whole life."""

    turn_id: UUID
    opened_run: bool


class MemberAdmitter(Protocol):
    """Admit a member message and consume the conversation's pending one-time pause."""

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
        intent: ToolIntent | None = None,
    ) -> Admitted: ...


class TurnTailer(Protocol):
    """Tail one turn's live frames until it ends, each frame with its replay cursor — the live
    surface's read half, mirroring `MemberAdmitter`'s write half. The concrete tailer binds the
    process hub and ends the stream on the durable terminal-or-parked state; a live surface never
    touches the hub directly, it reaches it through this one primitive."""

    def tail(self, turn_id: UUID, since: str = "") -> AsyncIterator[tuple[str, LiveFrame]]: ...


TERMINAL_TURN_STATUSES: tuple[str, ...] = ("done", "failed", "cancelled")
MAX_WRITEBACK_ERROR_CHARS = 2_048
WRITEBACK_POLL_SECONDS = 1.0
WRITEBACK_CLAIM_SECONDS = 300
WRITEBACK_CLAIM_REFRESH_SECONDS = 60
WRITEBACK_RETRY_BACKOFF_SECONDS = 60
WRITEBACK_MAX_AGE_SECONDS = 3600
WRITEBACK_CLAIM_BATCH = 16
WRITEBACK_WORKSPACE_BATCH = 16
WRITEBACK_WORKSPACE_CONCURRENCY = 4
WRITEBACK_WORKSPACE_IN_FLIGHT = WRITEBACK_WORKSPACE_BATCH * 2


@dataclass(frozen=True)
class ListedArtifact:
    """One row of the portal's artifacts view: the shared file (the exact record the link
    minter signs) and when its turn shared it."""

    artifact: "SharedArtifact"
    created_at: datetime


@dataclass(frozen=True)
class SharedArtifact:
    """A file a turn shared, as the writeback poller hands it to a surface's `attach`: the blob key
    to stream from, the download name, an optional human caption (`subject`), and its media type and
    size — the size lets a chunked-upload API reserve the exact length up front."""

    blob_key: str
    filename: str
    subject: str | None
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class Writeback:
    """One terminal turn ready for delivery: the conversation's `queue_key` (the surface decodes its
    own channel/thread from it), the terminal outcome and its accounting/model metadata, the files
    the turn shared, and any structured final handoff. A surface renders the reply and metadata,
    uploads `artifacts`, renders `question` as its own answer affordance, collects credentials
    privately, or exposes `connect_request` only through its authenticated member channel."""

    turn_id: UUID
    queue_key: str
    status: TerminalStatus
    text: str
    tokens: int
    cost_micro_usd: int
    cache_percent: int
    model: str
    reasoning: ReasoningEffort | None
    artifacts: tuple[SharedArtifact, ...]
    question: AskUserInput | None
    credential_request: CredentialRequest | None
    connect_request: ConnectRequest | None


LIST_CONVERSATIONS_LIMIT = 200
LIST_TURNS_LIMIT = 500
TRANSCRIPT_ACCESS_WINDOW = timedelta(hours=1)


class AgentSummary(BaseModel):
    """One workspace agent as a surface lists it — the read a surface whose member picks an agent
    (the web portal's switcher) filters through its own audience authority.
    `internet_access_allowed` is the agent's narrowing of the deploy's sandbox public-internet
    capability, carried for the administration view."""

    id: UUID
    name: str
    main: bool
    model: str
    internet_access_allowed: bool


class InstallationSummary(BaseModel):
    """One surface installation of the workspace — which surface is bound and the agent its
    conversations land on. The workspace-administration read behind the portal's agents view;
    binding stays each surface's own act."""

    surface: str
    agent_id: UUID


class AgentDetail(BaseModel):
    """One agent as a portal overview reads it: the row's configuration beside its prompt digest
    and the chat surfaces whose installations bind to it."""

    name: str
    main: bool
    model: str
    internet_access_allowed: bool
    reasoning: ReasoningEffort
    prompt: str
    prompt_digest: str
    surfaces: tuple[str, ...]
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PortalTask:
    """One recurring task as the portal lists it, already shaped for its viewer: `prompt` and
    `description` are None when the viewer may not read the task's content. A value object — the
    web surface renders it and nothing persists it."""

    name: str
    schedule: str
    prompt: str | None
    description: str | None
    created_by_email: str | None
    paused: bool
    next_run_at: datetime
    last_run_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class PortalSkill:
    """One skill as the portal lists it: a member-authored skill of the selected agent
    (`origin="member"`) or a deploy-provided loadable skill (`origin="deploy"`)."""

    name: str
    description: str
    origin: Literal["member", "deploy"]


class ConnectionView(BaseModel):
    """One connector account reaching one agent, as the portal's connections panel lists it: the
    provider identity, the `connector_grant` object name a prepared intent mutates it by, the
    consenting owner (named only to an admin or the owner), the edge's disclosure, and when the
    grant landed."""

    provider: str
    account_id: str
    grant: str
    owner_email: str | None
    shared: bool
    connected_at: datetime

    @field_validator("connected_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SpendCapView(BaseModel):
    """One spend cap as the administration view lists it: the scope, its subject named for the
    reader (an agent's name, a member's email, nothing for the workspace's own cap), the window,
    the limit, and what a breach does."""

    scope: str
    subject: str | None
    window_seconds: int
    limit_micro_usd: int
    on_breach: str


class DeployExtensionView(BaseModel):
    """One installed extension as the administration view lists it: the manifest's name and
    version, and whether its sandbox tools require metered public egress — deploy shape only,
    never an agent's resources or secrets."""

    name: str
    version: str
    sandbox_internet: bool


class CredentialSlotView(BaseModel):
    """One declared BYOK slot and whether the workspace holds a value for it — never the value.
    Slots come from installed manifests, the same declarations the `credential` object kind
    projects, minus the slots only the deploy's own code writes (`member_filled=False`, a provider
    callback's seal): those are machinery a member can neither fill nor rotate, so the panel
    leaves them out. Deploy config (model keys, signing secrets) is not a slot at all and cannot
    appear. `name` is the `credential` object kind's name for the slot — the address a prepared
    intent mutates."""

    slot: str
    name: str
    extension: str
    description: str
    filled: bool


def _binding_fields(backend: str, config: dict[str, JsonValue]) -> dict[str, str | None]:
    """The `source` kind's identity for one row, from the stored connector config — the binding
    name plus the spec fields a panel act echoes back. A row whose config is not a connector's
    (a config-registered folder, a feed) is not kind-managed and carries None throughout."""
    try:
        parsed = ConnectorSourceConfig.model_validate(config)
    except ValidationError:
        return {"name": None, "stream": None, "account_id": None, "base_url": None}
    return {
        "name": binding_name(backend, parsed.account, parsed.base_url),
        "stream": parsed.stream,
        "account_id": "" if parsed.account == DIRECT_ACCOUNT else parsed.account,
        "base_url": parsed.base_url or "",
    }


class SourceView(BaseModel):
    """One live source stream as the portal lists it: the backend, its disclosure subject
    (member-private pages stay gated to their member; `shared` means the agent's audience), the
    registering owner (named only to an admin or the owner — None is also a source with no owner
    member), sync health, and — for a connector-registered row — the `source` kind's binding
    name plus the spec fields that reconstruct the binding, so the panel's per-binding acts
    (resync, share, remove) submit the same object the chat verbs mutate. A config- or
    feed-registered row is not kind-managed and carries None."""

    backend: str
    shared: bool
    owner_email: str | None
    consecutive_errors: int
    next_sync_at: datetime
    name: str | None = None
    stream: str | None = None
    account_id: str | None = None
    base_url: str | None = None

    @field_validator("next_sync_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ConversationSummary(BaseModel):
    """One conversation as a read view lists it: identity and keying, the owning member's email,
    and its activity aggregates — deliberately spanning every surface in the workspace (the
    context's own `surface` scopes keying and admission, never these reads)."""

    id: UUID
    surface: str
    queue_key: str
    member_email: str | None
    created_at: datetime
    turn_count: int
    last_turn_at: datetime | None

    @field_validator("created_at", "last_turn_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TranscriptAccess(BaseModel):
    """One recorded disclosure: the moment an admin acknowledged that another member's private
    transcript may hold private information and read it. Written by `record_transcript_access`
    before any content is served; the row it names is the operator's record, read by `ufoctl
    transcript-reads`, and the subject it names is what the acknowledging admin is told back."""

    reader_email: str
    subject_email: str


async def record_transcript_access(
    workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID
) -> TranscriptAccess | None:
    """Record that an admin acknowledged another member's private transcript may hold private
    information and is reading it, and open that conversation's content to them for
    `TRANSCRIPT_ACCESS_WINDOW`. This is the disclosure's one writer, and it is a tool the portal
    reaches through the prepared-intent lane rather than a route of its own: the row is a grant —
    it is what `readable_conversation` answers on — and a granting act is speaker-gated and rides
    a turn, so the turn is its audit record and this row is the gate it opens. The same act emits
    `surface.transcript_disclosed`, which is the operator's read of who opened whose conversation;
    no member surface lists the rows.

    Each acknowledgement writes its own row, so a second visit is a second access rather than a
    silent re-read. None — the caller's refusal — for a conversation this agent does not hold, for
    a room or externally-shared channel (content nobody reads here), and for the reader's own or
    the workspace-shared one, which need no disclosure. The caller establishes that the member is
    an admin."""
    async with workspace_tx() as connection:
        found = (
            await connection.execute(
                sa.select(tables.conversation.c.audience).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.id == conversation_id,
                    tables.conversation.c.agent_id == agent_id,
                )
            )
        ).one_or_none()
        if found is None:
            return None
        subject_id = audience_member(parse_audience(found.audience))
        if subject_id is None or subject_id == member_id:
            return None
        named = (
            await connection.execute(
                sa.select(tables.member.c.id, tables.member.c.email).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.id.in_((member_id, subject_id)),
                )
            )
        ).all()
        emails: dict[UUID, str] = {row.id: row.email for row in named}
        recorded_at = datetime.now(UTC)
        await connection.execute(
            sa.insert(tables.transcript_access).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                reader_member_id=member_id,
                subject_member_id=subject_id,
                created_at=recorded_at,
            )
        )
    log(
        "surface.transcript_disclosed",
        conversation_id=str(conversation_id),
        reader_email=emails[member_id],
        subject_email=emails[subject_id],
    )
    return TranscriptAccess(
        reader_email=emails[member_id],
        subject_email=emails[subject_id],
    )


class ListedConversation(BaseModel):
    """One conversation as the portal's per-agent conversations view lists it: `ConversationSummary`
    plus whether this viewer may read its content now and whether they may disclose it to
    themselves by acknowledging (an admin, another member's private conversation — never a
    room's). Disclosures recorded against it are not here and no portal read lists them: the
    record is the operator's, kept in `transcript_access` and reported by
    `surface.transcript_disclosed`."""

    summary: ConversationSummary
    readable: bool
    disclosable: bool


class LedgerEntry(BaseModel):
    """One accounting row of a turn — a dimension's metered amount and its priced cost."""

    dimension: str
    amount: int
    priced_micro_usd: int
    model: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TurnDetail(BaseModel):
    """One turn with everything durable that hangs off it: the row itself (terminal outcome and
    context included), its accounting, and the subagent turns it spawned (`parent_turn_id`
    children, each living in its own conversation)."""

    turn: Turn
    ledger: tuple[LedgerEntry, ...]
    children: tuple[Turn, ...]


def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str:
    """The blob marker one fulfilled prompt leaves, keyed by the seal's digest and the slot — the
    render gate reads it per prompt, so a stored slot stops prompting while its siblings keep
    asking, and a fresh request (a rotation) seals differently and prompts anew."""
    digest = hashlib.sha256(sealed.encode()).hexdigest()[:32]
    return f"workspaces/{workspace_id}/credential_requests/{digest}/{slot}"


def _readable_audience_values(member_id: UUID) -> tuple[str, ...]:
    """The two conversation audiences whose content this member reads: the workspace-shared one and
    their own. A room and an externally-shared channel are absent by construction — the one
    definition `list_agent_conversations` and `readable_conversation` both answer from."""
    return (str(SHARED_AUDIENCE), str(conversation_audience(member_id)))


async def _main_agent(workspace_id: UUID) -> UUID:
    """The workspace's main agent, used when no surface binding names an agent."""
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.is_main,
                )
            )
        ).one_or_none()
    if agent is None:
        raise RuntimeError(f"workspace {workspace_id} has no main agent")
    return agent.id


async def _bind_surface_installation(
    workspace_id: UUID, surface: str, installation_id: str
) -> None:
    """Upsert one surface's installation binding for a workspace, replacing any prior binding for
    that (workspace, surface). A new binding lands on the workspace's main agent; rebinding
    replaces the installation identity and keeps the binding's agent. The fleet-wide uniqueness on
    (surface, installation_id) raises `SurfaceInstallationConflict` when the installation already
    belongs to another workspace. The one place the binding is written — a tool
    (`SurfaceInstallationAccess.bind`) and a surface's own OAuth callback
    (`SurfaceContext.bind_installation`) both land it here."""
    if not installation_id:
        raise ValueError("surface installation id is empty")
    agent_id = await _main_agent(workspace_id)
    try:
        async with workspace_tx() as connection:
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.surface_installation)
                .values(
                    workspace_id=workspace_id,
                    surface=surface,
                    installation_id=installation_id,
                    agent_id=agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=("workspace_id", "surface"),
                    set_={"installation_id": installation_id, "updated_at": sa.func.now()},
                )
            )
    except sa.exc.IntegrityError as error:
        raise SurfaceInstallationConflict(surface) from error


@dataclass(frozen=True)
class SurfaceContext:
    """The privileged handle a surface's route handlers receive — one context spanning both delivery
    modes. `blob` and the admit/identity reach are deliberately unscoped for a workspace's trusted
    surface (the distinction from a scoped extension context, which never admits a turn or asserts
    identity). A **durable** surface (Slack) delivers through the poller; a **live** surface (web;
    core's CLI is the built-in twin) delivers by `tail`-ing the turn's frames off the hub in its
    own SSE route, reading `turn_owner` to gate a tail, `spend_rollup` for a workspace spend view
    and `member_spend` for the reader's own, and the per-agent projections a portal renders —
    `list_agent_tasks`, `agent_skills`, `agent_spend`, and `memory_available`/`search_memory` —
    and either mode renders a turn's
    shared files, the poller handing them to `attach` while a live surface reads
    `shared_artifacts` and links each through `artifact_link`. Each calls only what it needs.
    `credential` reads the surface workspace's slots in-process (never through the sandbox proxy),
    and the sealed member handoff (`credential_prompt_pending`, `fulfill_credential_request`)
    rides the same store — a surface with neither declared slots nor a member credential handoff
    never calls it."""

    workspace_id: UUID
    surface: str
    blob: BlobStore
    _sandboxes: ConversationSandbox
    _admitter: MemberAdmitter
    _tailer: TurnTailer
    _credentials: CredentialStore | None
    _artifact_token_secret: str
    _public_base_url: str | None
    _ingress_public_url: str | None
    _deploy_sandbox_internet: bool
    _models: tuple[str, ...]
    _skills: SkillRegistry
    _user_skills: Callable[[], Awaitable[tuple[RuntimeSkill, ...]]]
    _declared_slots: tuple[DeclaredSlot, ...]
    _object_schemas: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    _deploy_extensions: tuple[DeployExtensionView, ...] = ()
    _memory: "MemorySearch | None" = None
    _objects: "Mapping[str, BoundKind]" = MappingProxyType({})

    @property
    def deploy_extensions(self) -> tuple[DeployExtensionView, ...]:
        """The deploy's installed extensions — the administration view's deploy-status read,
        fixed at boot from the manifest set the process loaded."""
        return self._deploy_extensions

    @property
    def deploy_sandbox_internet(self) -> bool:
        """Whether this deploy's active extensions grant sandbox public internet at all — the
        ceiling a portal shows an agent's `internet_access_allowed` narrowing."""
        return self._deploy_sandbox_internet

    @property
    def models(self) -> tuple[str, ...]:
        """The model ids this deploy's registry serves, `auto` first — the closed set a portal
        offers where an agent's model is chosen, the same records the runtime routes and bills
        on."""
        return self._models

    async def credential(self, slot: str) -> str:
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} reads a credential but holds no store")
        return await self._credentials.get(self.workspace_id, slot)

    async def credential_prompt_pending(self, sealed: str, slot: str) -> bool:
        """Whether one prompt of a sealed credential request still awaits its value — the per-slot
        render gate, so a fulfilled, expired, or foreign prompt is never re-presented on reconnect
        while an unanswered sibling keeps asking (and a rotation, sealing afresh, asks anew)."""
        if self._credentials is None:
            return False
        try:
            state = open_credential_request(
                self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
            )
        except CredentialRequestInvalid:
            return False
        if state.workspace_id != self.workspace_id or slot not in state.slots:
            return False
        return not await self.blob.exists(_fulfilled_marker_key(self.workspace_id, sealed, slot))

    def open_credential_authorization(self, sealed: str) -> CredentialRequestState:
        """Open a sealed credential-authorization handoff, returning its claims (workspace, member,
        slot, provider state). The Fernet's authenticity and TTL are the trust — a surface that
        completes a provider handoff at a browser callback (a Slack OAuth install) recovers the
        sealed member and slot to fulfill against, having no turn to bind them from. Raises
        `CredentialRequestInvalid` on a tampered or expired seal."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} opens a seal but holds no store")
        return open_credential_request(
            self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )

    async def fulfill_credential_request(
        self, sealed: str, slot: str, value: str, member_id: UUID | None
    ) -> None:
        """Verify the seal and store one requested slot: the fulfiller must be the member the
        request was sealed for, the slot one it named, and the seal fresh — anything else raises
        `CredentialRequestInvalid` before a byte is written. The value lands through the same
        encrypted store `ufoctl credential set` writes, and the slot's own marker stops its prompt
        from rendering again."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} stores a credential but holds no store")
        state = open_credential_request(
            self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )
        if state.workspace_id != self.workspace_id:
            raise CredentialRequestInvalid("credential request was sealed for another workspace")
        if member_id is None or member_id != state.member_id:
            raise CredentialRequestInvalid(
                "credential request can only be fulfilled by the member who asked"
            )
        if slot not in state.slots:
            raise CredentialRequestInvalid(f"credential request does not name slot {slot!r}")
        await self._credentials.put(self.workspace_id, slot, value)
        await self.blob.put(
            _fulfilled_marker_key(self.workspace_id, sealed, slot),
            json.dumps({"at": datetime.now(UTC).timestamp()}).encode(),
        )

    async def bind_installation(self, installation_id: str) -> None:
        """Bind this surface's external installation identity (a Slack team) to this workspace,
        replacing any prior binding for this workspace's surface. A surface completing its own OAuth
        install at a callback records the team→workspace mapping shared ingress later resolves by;
        the fleet-wide uniqueness on (surface, installation_id) rejects a team already bound to
        another workspace with `SurfaceInstallationConflict`."""
        await _bind_surface_installation(self.workspace_id, self.surface, installation_id)

    @property
    def public_base_url(self) -> str | None:
        """The deploy's public base (`[connect] public_base_url`), or None when unset — a
        channel's callback URL (Slack's Events request URL) renders from it."""
        return self._public_base_url

    async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]:
        """The files a turn shared, in share order (key-tiebroken within one timestamp) — the rows
        the writeback poller hands a durable surface's `attach`, read directly by a live surface
        that renders each as a download link (`artifact_link`) on its own stream."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                    )
                    .where(
                        tables.shared_artifact.c.workspace_id == self.workspace_id,
                        tables.shared_artifact.c.turn_id == turn_id,
                    )
                    .order_by(
                        tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key
                    )
                )
            ).all()
        return tuple(
            SharedArtifact(
                blob_key=row.blob_key,
                filename=row.filename,
                subject=row.subject,
                media_type=row.media_type,
                size_bytes=row.size_bytes,
            )
            for row in rows
        )

    def artifact_link(self, artifact: SharedArtifact) -> str | None:
        """A TTL download link for a shared file the surface cannot upload inline, or None when
        artifact delivery is unconfigured (no token secret or no public base URL) — the surface then
        names the file without a link. Mints the same signed token the web download route verifies,
        so the link opens for anyone holding it until it expires."""
        if not self._artifact_token_secret or not self._public_base_url:
            return None
        expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_TOKEN_TTL_SECONDS
        token = mint_artifact_token(
            self._artifact_token_secret, artifact.blob_key, artifact.filename, expires_at
        )
        return f"{self._public_base_url.rstrip('/')}{ARTIFACT_DOWNLOAD_PATH}?token={token}"

    def ingress_url(self, conversation_id: UUID, port: int) -> str | None:
        """The URL that opens one conversation's sandbox port in a browser, or None when the ingress
        is unconfigured (no `[sandbox] ingress_public_url`) — the surface then serves no site. The
        port gets its own signed origin, and the view token the ingress trades for that origin's
        session cookie. The origin is stable per `(conversation, port)`, so a bookmark and the
        site's stored state survive a redeploy, while the token expires, so a leaked URL stops
        opening new sessions. Mints the view token the ingress verifies — never a session token,
        the other kind — so no surface holds the deploy secret or a credential a site accepts."""
        if not self._ingress_public_url:
            return None
        base = urlsplit(self._ingress_public_url)
        token = mint_ingress_token(
            IngressClaims(
                workspace_id=self.workspace_id,
                conversation_id=conversation_id,
                port=port,
                expires_at=int(datetime.now(UTC).timestamp()) + INGRESS_VIEW_TTL_SECONDS,
            ),
            INGRESS_VIEW_KIND,
        )
        label = site_label(conversation_id, port)
        return f"{base.scheme}://{label}.{base.netloc}{INGRESS_VIEW_PATH}/{token}"

    async def _identity_member(self, surface: str, external_id: str) -> UUID | None:
        """The member a surface's external id is linked to, or None. `linked_member` reads this
        surface's own identity; `adopt_identity` reads a peer surface's."""
        async with workspace_tx() as connection:
            linked = (
                await connection.execute(
                    sa.select(tables.surface_identity.c.member_id).where(
                        tables.surface_identity.c.workspace_id == self.workspace_id,
                        tables.surface_identity.c.surface == surface,
                        tables.surface_identity.c.external_id == external_id,
                    )
                )
            ).one_or_none()
        return None if linked is None else linked.member_id

    async def linked_member(self, external_id: str) -> UUID | None:
        return await self._identity_member(self.surface, external_id)

    async def is_operator_workspace(self) -> bool:
        """Whether this workspace is the fleet operator's own — the workspace whose own domain
        (its initial member's vetted email domain, the same resolution hosted onboarding joins by)
        is `OPERATOR_EMAIL_DOMAIN`. Gates renderings meant for the operator alone, like Slack's
        accounting footer and its debugger link — never a tenant-facing capability; an
        unidentified workspace is never the operator's, so internals render nowhere rather than
        in a customer's thread."""
        return await self.workspace_domain() == OPERATOR_EMAIL_DOMAIN

    async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None:
        """Link this surface's external id to the member a peer surface already knows it by, so one
        human spans both surfaces under one member and one memory subject — web's session token,
        whose canonical home is the CLI, adopts the member the same digest already names there. A
        peer with no such identity is unknown and leaves this surface unlinked; a lost race
        collapses on the identity's primary key."""
        peer_member = await self._identity_member(peer_surface, external_id)
        if peer_member is None:
            return None
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=self.workspace_id,
                        member_id=peer_member,
                        surface=self.surface,
                        external_id=external_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.identity_adopt_race", surface=self.surface, external_id=external_id)
        return peer_member

    async def link_member(self, external_id: str, email: str) -> UUID | None:
        """Link this surface's external id to the workspace member whose email matches, the first
        time they speak. No matching member leaves the external id unlinked (a shared conversation
        with no memory subject); a lost race collapses on the identity's primary key."""
        async with workspace_tx() as connection:
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == self.workspace_id,
                        sa.func.lower(tables.member.c.email) == email.lower(),
                    )
                )
            ).one_or_none()
        if member is None:
            return None
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=self.workspace_id,
                        member_id=member.id,
                        surface=self.surface,
                        external_id=external_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.identity_link_race", surface=self.surface, external_id=external_id)
        return member.id

    async def join_member(self, external_id: str, email: str) -> UUID | None:
        """`link_member`, plus the domain-match join: an email with no member row whose domain is
        the workspace's own — the initial member's vetted email domain — creates the member and
        links it in one step, so a teammate becomes a member on first contact and only the initial
        member onboards through provisioning. The surface asserting the email is the trust anchor:
        it calls this only with an email its channel verified. A foreign-domain email stays
        unlinked; a lost creation race collapses on the member's (workspace_id, email) uniqueness
        and links the surviving row."""
        linked = await self.link_member(external_id, email)
        if linked is not None:
            return linked
        own = await self.workspace_domain()
        domain = email_domain(email)
        if own is None or not domain or domain != own:
            return None
        async with workspace_tx() as connection:
            await create_member(connection, self.workspace_id, email.strip().lower())
        return await self.link_member(external_id, email)

    def _conversation_lookup(self, queue_key: str) -> sa.Select:
        return sa.select(
            tables.conversation.c.id,
            tables.conversation.c.member_id,
            tables.conversation.c.audience,
        ).where(
            tables.conversation.c.workspace_id == self.workspace_id,
            tables.conversation.c.surface == self.surface,
            tables.conversation.c.queue_key == queue_key,
        )

    async def find_conversation(self, queue_key: str) -> UUID | None:
        """The conversation this surface keys by `queue_key`, or None before its first turn — how a
        surface reads participation without creating a conversation as a side effect (Slack admits
        an un-mentioned reply only in a thread whose key already names a conversation)."""
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        return None if found is None else found.id

    async def conversation_for(
        self,
        queue_key: str,
        audience: Audience,
        agent_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> UUID:
        """Get-or-create the conversation this surface keys by `queue_key`, outside any admission
        transaction; a lost creation race re-reads the surviving row. A caller may name the new
        conversation's id so state it keys by that id can be written before the conversation
        exists — a crash between the two leaves inert keyed state, never a conversation missing
        its state; an existing conversation keeps its own id regardless. A new conversation binds
        permanently to the surface's agent — an explicit `agent_id` when the surface's member
        picks the agent (the web portal, after its own audience check), else the surface's
        installation binding when one exists, else the workspace's main agent — and admission
        derives every turn's agent from that binding. A shared conversation is narrowed when the
        surface learns its exact member or room; an audience is never widened, and a room becoming
        externally shared seals as foreign."""
        audience = parse_audience(audience)
        member_id = audience_member(audience)
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        if found is not None:
            narrowed = narrow_audience(parse_audience(found.audience), audience)
            if narrowed != found.audience:
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.conversation)
                        .where(
                            tables.conversation.c.id == found.id,
                            tables.conversation.c.audience == found.audience,
                        )
                        .values(
                            member_id=audience_member(narrowed),
                            audience=str(narrowed),
                            updated_at=sa.func.now(),
                        )
                    )
                return await self.conversation_for(queue_key, audience)
            return found.id
        conversation_id = conversation_id or uuid4()
        if agent_id is None:
            agent_id = await self._surface_agent()
        else:
            async with workspace_tx() as connection:
                known = (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == self.workspace_id,
                            tables.agent.c.id == agent_id,
                        )
                    )
                ).one_or_none()
            if known is None:
                raise ValueError(f"agent {agent_id} is not an agent of this workspace")
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
                        agent_id=agent_id,
                        surface=self.surface,
                        queue_key=queue_key,
                        member_id=member_id,
                        audience=str(audience),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.conversation_create_lost_race", surface=self.surface, queue_key=queue_key)
            async with workspace_tx() as connection:
                found = (
                    await connection.execute(self._conversation_lookup(queue_key))
                ).one_or_none()
            if found is None:
                raise
            return await self.conversation_for(queue_key, audience)
        return conversation_id

    async def _surface_agent(self) -> UUID:
        async with workspace_tx() as connection:
            bound = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.agent_id).where(
                        tables.surface_installation.c.workspace_id == self.workspace_id,
                        tables.surface_installation.c.surface == self.surface,
                    )
                )
            ).one_or_none()
        if bound is not None:
            return bound.agent_id
        return await _main_agent(self.workspace_id)

    async def admit(
        self,
        conversation_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
        intent: ToolIntent | None = None,
    ) -> Admitted:
        """Admit an inbound message onto the durable turn queue and return its turn, with whether
        this delivery opened that turn's run. The turn executes as the conversation's bound agent —
        a surface never names one. Delivery is admission's concern, derived from the conversation's
        surface: a durable-surface turn registers for the poller atomically with its row, a live
        surface's turn registers nothing and its member tails the hub — the surface supplies only
        the message, its idempotency key, and the ambient `TurnContext` (sender, timezone, and the
        source — where the member said it, in whatever form this surface can name) the engine
        renders before the inbound. A redelivery deduped to the turn already admitted joins it, as
        does a follow-up folded into a live one. A prepared `intent` (a panel's form submit) admits
        a turn that dispatches that one tool call verbatim instead of running model rounds — it
        requires the speaking member, its admission never folds into a live turn (each submit
        founds its own queued turn on the member's durable intent conversation with the agent, and
        the per-conversation partition runs them in order), and its `body` must equal the
        envelope's serialization, which is the turn's inbound."""
        return await self._admitter.admit(
            conversation_id,
            body,
            idempotency_key=idempotency_key,
            context=context,
            speaker_member_id=speaker_member_id,
            intent=intent,
        )

    async def connect_url(self, turn_id: UUID, member_id: UUID) -> str:
        """Open a terminal connect request as its speaking member."""
        try:
            flow = installed_connect_flow()
        except ConnectUnavailable as error:
            raise ConnectRequestInvalid("connect flow is unavailable") from error
        return await ConnectHandoff(flow).authorize(self.workspace_id, turn_id, member_id)

    async def admitted_body(self, idempotency_key: str) -> str | None:
        """The message body an idempotency key admitted — the founding inbound of the turn the key
        opened, or the queue row it landed as — or None when the key admitted nothing. How a
        surface whose answer affordance raced (an idempotent admit joins whatever the first click
        won) confirms which answer landed: only the click whose body was stored may rewrite the
        affordance into its answer."""
        async with workspace_tx() as connection:
            turn_row = (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.idempotency_key == idempotency_key,
                    )
                )
            ).one_or_none()
            if turn_row is not None:
                return turn_row.inbound
            message_row = (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body).where(
                        tables.inbound_message.c.workspace_id == self.workspace_id,
                        tables.inbound_message.c.idempotency_key == idempotency_key,
                    )
                )
            ).one_or_none()
        return None if message_row is None else message_row.body

    async def turn_owner(self, turn_id: UUID) -> UUID | None:
        """The member whose conversation owns a turn, or None when no such turn exists — the check a
        live surface gates its per-turn tail on, so a member cannot tail another member's turn."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id)
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return None if row is None else row.member_id

    async def turn_is_terminal(self, turn_id: UUID) -> bool:
        """Whether a turn has committed its terminal state, read from the row rather than the hub.

        A side-channel writer that posts on a timer needs this: the tail learns a turn ended by
        polling, so between the commit and the frame there is a window in which the poller has
        already delivered the turn's reply. A checkpoint firing inside that window would post an
        update *after* the answer it was reporting progress towards, which the member sees in their
        thread. A missing turn reads as terminal — there is nothing left to report on."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return row is None or row.status in TERMINAL_TURN_STATUSES

    async def latest_turn(self, conversation_id: UUID) -> UUID | None:
        """The most recent turn admitted to a conversation, or None when it holds none — the
        turn a live surface resumes tailing when a held stream reconnects to drain an answer that
        outran the hold, and the turn whose open handoffs (an unanswered question, pending
        credential prompts, shared files) a reload re-renders."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.id)
                    .where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                    )
                    .order_by(tables.turn.c.seq.desc())
                    .limit(1)
                )
            ).one_or_none()
        return None if row is None else row.id

    def tail(self, turn_id: UUID, since: str = "") -> AsyncIterator[tuple[str, LiveFrame]]:
        """Tail a turn's live frames off the hub until it ends — a live surface streams these to the
        member's held connection (SSE), reaching the hub only through the injected tailer."""
        return self._tailer.tail(turn_id, since)

    async def spend_rollup(self, window_seconds: int) -> SpendReport:
        """The workspace spend rollup over a window — the same sums `ufoctl spend` prints — for a
        surface's spend view."""
        async with workspace_tx() as connection:
            return await SpendRollup(self.workspace_id).read(connection, window_seconds)

    async def write_workspace_file(
        self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]
    ) -> None:
        """Land a file in the conversation's `/workspace` before its turn runs, so the agent's file
        tools find it already present. The workspace lives in the sandbox, so the write goes through
        the carrier, which takes the whole body at once — bounded while it accumulates, so a stream
        with no cap of its own is refused at the limit instead of first sitting whole in memory."""
        body = bytearray()
        async for chunk in chunks:
            body += chunk
            if len(body) > WORKSPACE_WRITE_MAX_BYTES:
                raise ValueError(
                    f"{rel} exceeds the {WORKSPACE_WRITE_MAX_BYTES}-byte limit for a "
                    "workspace write"
                )
        await self._sandboxes.write(conversation_id, rel, bytes(body))

    async def list_agents(self) -> tuple[AgentSummary, ...]:
        """Every agent of this workspace, main first then by name — the read a surface whose
        member picks an agent filters through its own audience authority before showing."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.is_main,
                        tables.agent.c.model,
                        tables.agent.c.internet_access_allowed,
                    )
                    .where(tables.agent.c.workspace_id == self.workspace_id)
                    .order_by(tables.agent.c.is_main.desc(), tables.agent.c.name)
                )
            ).all()
        return tuple(
            AgentSummary(
                id=row.id,
                name=row.name,
                main=row.is_main,
                model=row.model,
                internet_access_allowed=row.internet_access_allowed,
            )
            for row in rows
        )

    async def agent_detail(self, agent_id: UUID) -> AgentDetail | None:
        """One agent's configuration for a portal overview — the row beside its prompt digest and
        bound surfaces, or None when no such agent exists in this workspace. The surface's own
        audience authority gates who may read it, exactly as `list_agents`."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.name,
                        tables.agent.c.is_main,
                        tables.agent.c.model,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.reasoning,
                        tables.agent.c.prompt,
                        tables.agent.c.updated_at,
                    ).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.id == agent_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            surfaces = (
                (
                    await connection.execute(
                        sa.select(tables.surface_installation.c.surface)
                        .where(
                            tables.surface_installation.c.workspace_id == self.workspace_id,
                            tables.surface_installation.c.agent_id == agent_id,
                        )
                        .order_by(tables.surface_installation.c.surface)
                    )
                )
                .scalars()
                .all()
            )
        return AgentDetail(
            name=row.name,
            main=row.is_main,
            model=row.model,
            internet_access_allowed=row.internet_access_allowed,
            reasoning=row.reasoning,
            prompt=row.prompt,
            prompt_digest=prompt_digest(row.prompt),
            surfaces=tuple(surfaces),
            updated_at=row.updated_at,
        )

    async def list_agent_tasks(
        self, agent_id: UUID, viewer_member_id: UUID, viewer_is_admin: bool
    ) -> tuple[PortalTask, ...]:
        """The selected agent's recurring tasks, shaped for the viewer by the same contract the
        scheduled_task object kind enforces in chat (`MemberOwnedObjects._visible` +
        `_content_visible`): a task is visible to its creator or a workspace admin — a creatorless
        task only to an admin — and its content, prompt and description, only to its creator, or
        to an admin when it has no creator. Everyone else sees nothing."""
        with bind_agent(agent_id):
            tasks = await ScheduleStore().list()
        visible = tuple(
            task
            for task in tasks
            if viewer_is_admin
            or (
                task.created_by_member_id is not None
                and task.created_by_member_id == viewer_member_id
            )
        )
        creators = {task.created_by_member_id for task in visible} - {None}
        emails: dict[UUID, str] = {}
        if creators:
            async with workspace_tx() as connection:
                emails = {
                    row.id: row.email
                    for row in await connection.execute(
                        sa.select(tables.member.c.id, tables.member.c.email).where(
                            tables.member.c.workspace_id == self.workspace_id,
                            tables.member.c.id.in_(creators),
                        )
                    )
                }

        shaped: list[PortalTask] = []
        for task in visible:
            content = (
                task.created_by_member_id is None or task.created_by_member_id == viewer_member_id
            )
            shaped.append(
                PortalTask(
                    name=task.name,
                    schedule=task.schedule,
                    prompt=task.prompt if content else None,
                    description=task.description if content else None,
                    created_by_email=(
                        None
                        if task.created_by_member_id is None
                        else emails.get(task.created_by_member_id)
                    ),
                    paused=task.paused,
                    next_run_at=task.next_run_at,
                    last_run_at=task.last_run_at,
                    expires_at=task.expires_at,
                )
            )
        return tuple(shaped)

    def object_spec_schema(self, kind: str) -> dict[str, Any] | None:
        """The named object kind's spec schema, or None when this deploy registers no such kind —
        a portal form renders its fields from the same schema `object_explain` reports, never a
        parallel description."""
        return self._object_schemas.get(kind)

    async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]:
        """The selected agent's loadable skills — exactly the composition a turn loads (the deploy
        registry merged with the agent's saved skills, base winning on a name collision) as the
        system prompt's `{{skill_index}}` renders it: top-level skills in registration order,
        member-authored ones appended last. One answer to "what skills does this agent load", never
        a second derivation."""
        with bind_agent(agent_id):
            merged = self._skills.merged_with(await self._user_skills())
        deploy_names = frozenset(self._skills.by_name)
        return tuple(
            PortalSkill(
                name=skill.name,
                description=skill.description,
                origin="deploy" if skill.name in deploy_names else "member",
            )
            for skill in merged.by_name.values()
            if skill.parent is None
        )

    @property
    def memory_available(self) -> bool:
        """Whether this deploy resolved a memory-search provider — the surface's render gate for
        its memory view, answered the same on every path so a provider-less deploy never claims
        otherwise."""
        return self._memory is not None

    async def search_memory(
        self, reader: "SourceReader", queries: tuple[str, ...]
    ) -> "tuple[MemoryMatch, ...]":
        """Search the memory a reader may see: the selected agent, the requesting member, and
        exactly the readable subjects — the same reader shape a turn's tools search under, so
        source-gated pages answer the portal and the agent identically. Gates on
        `memory_available` first: searching a deploy that resolved no provider is a programming
        error, not an empty result."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return await self._memory.search(reader, queries)

    async def recent_memory(
        self,
        subjects: frozenset[str],
        limit: int,
        kinds: "frozenset[str] | None" = None,
        cursor: "ListingCursor | None" = None,
    ) -> "ListingPage[MemoryMatch]":
        """One page of the live memory items the subjects may read, newest first — the browse half
        of the memory seam, for the portal's listing: no query, no similarity, source pages stay
        search-only. Keyset-paged through `cursor` and narrowable to item classes through `kinds`.
        Gates on `memory_available` like `search_memory`."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return await self._memory.list_recent(subjects, limit, kinds, cursor)

    @property
    def memory_kinds(self) -> tuple[str, ...]:
        """The item classes the installed provider writes — the closed set the portal's filter
        offers, so a class the provider adds cannot go missing from it."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return self._memory.listable_kinds()

    async def agent_spend(self, agent_id: UUID, window_seconds: int) -> AgentSpendReport:
        """One agent's rolling-window spend and its agent-scoped caps — the member-visible slice,
        distinct from the workspace-wide `spend_rollup` an admin reads."""
        async with workspace_tx() as connection:
            return await SpendRollup(workspace_id=self.workspace_id).read_agent(
                connection, agent_id, window_seconds
            )

    async def member_spend(self, member_id: UUID, window_seconds: int) -> MemberSpendReport:
        """One member's own rolling-window spend and their member-scoped caps — what a member may
        read about their own burn, naming no other member and no agent, so a surface answers it to
        the member themself without the admin gate `spend_rollup` carries."""
        async with workspace_tx() as connection:
            return await SpendRollup(workspace_id=self.workspace_id).read_member(
                connection, member_id, window_seconds
            )

    async def list_agent_connections(
        self, agent_id: UUID, member_id: UUID, *, admin: bool
    ) -> tuple[ConnectionView, ...]:
        """The connector accounts granted to one agent that this member may see — the member gate
        in the query, never the caller: an admin sees every edge, everyone else their own private
        grants plus agent-shared ones (#645's resolution rule, read-side). The wall stays the
        query's `agent_id`; another agent's edges are simply absent. A shared edge names its
        owner only to an admin or the owner: the roster names every colleague, but which of them
        holds a given account is the owner's to disclose, and chat names it to nobody else."""
        query = (
            sa.select(
                tables.connection.c.provider,
                tables.connection.c.account_id,
                tables.member.c.email,
                tables.connection.c.owner_member_id,
                tables.connector_grant.c.shared,
                tables.connector_grant.c.created_at,
            )
            .select_from(
                tables.connector_grant.join(
                    tables.connection,
                    tables.connector_grant.c.connection_id == tables.connection.c.id,
                ).join(tables.member, tables.connection.c.owner_member_id == tables.member.c.id)
            )
            .where(
                tables.connector_grant.c.workspace_id == self.workspace_id,
                tables.connector_grant.c.agent_id == agent_id,
            )
            .order_by(tables.connection.c.provider, tables.connection.c.account_id)
        )
        if not admin:
            query = query.where(
                sa.or_(
                    tables.connector_grant.c.shared,
                    tables.connection.c.owner_member_id == member_id,
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            ConnectionView(
                provider=row.provider,
                account_id=row.account_id,
                grant=account_object_name(row.provider, row.account_id),
                owner_email=row.email if admin or row.owner_member_id == member_id else None,
                shared=row.shared,
                connected_at=row.created_at,
            )
            for row in rows
        )

    async def list_artifacts(
        self,
        member_id: UUID,
        *,
        admin: bool,
        limit: int,
        cursor: "ListingCursor | None" = None,
    ) -> "ListingPage[ListedArtifact]":
        """One keyset page of the files turns have shared, as the portal's artifacts view lists
        them: a member sees their own conversations' artifacts, an admin the workspace's — newest
        first, bounded, `shared_artifact.id` breaking a `created_at` tie so two files one turn
        shared in the same instant page without repeating or skipping either. Each entry carries
        the `SharedArtifact` the link minter signs, so the view links exactly what the writeback
        delivery would."""
        query = (
            sa.select(
                tables.shared_artifact.c.id,
                tables.shared_artifact.c.blob_key,
                tables.shared_artifact.c.filename,
                tables.shared_artifact.c.subject,
                tables.shared_artifact.c.media_type,
                tables.shared_artifact.c.size_bytes,
                tables.shared_artifact.c.created_at,
            )
            .select_from(
                tables.shared_artifact.join(
                    tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                ).join(
                    tables.conversation,
                    tables.turn.c.conversation_id == tables.conversation.c.id,
                )
            )
            .where(tables.shared_artifact.c.workspace_id == self.workspace_id)
        )
        if not admin:
            query = query.where(tables.conversation.c.member_id == member_id)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    page_query(
                        query,
                        cursor,
                        limit,
                        created_at=tables.shared_artifact.c.created_at,
                        ident=tables.shared_artifact.c.id,
                    )
                )
            ).all()
        return page_of(
            rows,
            cursor,
            limit,
            render=lambda row: ListedArtifact(
                artifact=SharedArtifact(
                    blob_key=row.blob_key,
                    filename=row.filename,
                    subject=row.subject,
                    media_type=row.media_type,
                    size_bytes=row.size_bytes,
                ),
                created_at=row.created_at,
            ),
            position=lambda row: (
                row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
                str(row.id),
            ),
        )

    async def list_member_objects(
        self, kind: str, member_id: UUID | None, *, admin: bool
    ) -> "ObjectPage | None":
        """One object kind's listing for a signed-in member — the portal's projection over the
        deploy's registry, answering through the kind's own visibility gate (`member_page`), or
        None when the deploy installs no such kind. Only a member-owned kind lists here; asking
        for any other kind is a programming error, not an empty page."""
        from ufo.objects import MemberOwnedObjects, ObjectListQuery

        bound = self._objects.get(kind)
        if bound is None:
            return None
        store = bound.kind.store
        if not isinstance(store, MemberOwnedObjects):
            raise RuntimeError(f"object kind {kind!r} does not list for a member")
        return await store.member_page(
            bound.context, member_id=member_id, admin=admin, query=ObjectListQuery()
        )

    async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]:
        """The member-fillable declared slots with their fill state — never a value. The
        `credential` object kind lists every slot; this read drops `member_filled=False` ones
        (deploy-written seals a member can neither fill nor rotate), because the panel exists to
        show a member what they can act on."""
        async with workspace_tx() as connection:
            filled = set(
                (
                    await connection.execute(
                        sa.select(tables.credential.c.slot).where(
                            tables.credential.c.workspace_id == self.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        names = {
            slot.extension + "/" + slot.name: name
            for name, slot in named_slots(self._declared_slots).items()
        }
        return tuple(
            CredentialSlotView(
                slot=slot.name,
                name=names[slot.extension + "/" + slot.name],
                extension=slot.extension,
                description=slot.description,
                filled=slot.name in filled,
            )
            for slot in sorted(self._declared_slots, key=lambda slot: (slot.extension, slot.name))
            if slot.member_filled
        )

    async def workspace_domain(self) -> str | None:
        """The workspace's own email domain, read through the one derivation `add_member` admits
        by, so the domain a panel advertises and the addresses the verb accepts cannot diverge.
        None only when the workspace has no member yet, since every stored address carries a
        domain; a caller reading None has no domain to advertise and admits no added address."""
        async with workspace_tx() as connection:
            return await workspace_domain(connection, self.workspace_id)

    async def list_members(self) -> tuple[SeatEntry, ...]:
        """The workspace roster a portal session reads, ordered by email so the panel's rows are
        stable — the same rows the `member` kind lists to a member asking the main agent in an
        internal conversation, which a portal session always is (its audience is the signed-in
        member's own, never a shared room). Adding a member and changing a role or seat stay
        admin-gated in their own verbs."""
        async with workspace_tx() as connection:
            snapshot = await Seats(self.workspace_id).snapshot(connection)
        return tuple(sorted(snapshot.members, key=lambda entry: entry.email))

    async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]:
        """The live source bindings this member may see — an admin all of them, everyone else
        their own registrations plus shared ones. Removed sources stay gone; a member-subject
        source's pages remain gated to that member wherever they land. A shared source names its
        owner only to an admin or the owner: chat omits a shared source's owner entirely, so which
        colleague registered a binding stays the owner's to disclose even though the roster names
        every colleague."""
        query = (
            sa.select(
                tables.source.c.backend,
                tables.source.c.subject,
                tables.member.c.email,
                tables.source.c.owner_member_id,
                tables.source.c.consecutive_errors,
                tables.source.c.next_sync_at,
                tables.source.c.config,
            )
            .select_from(
                tables.source.outerjoin(
                    tables.member, tables.source.c.owner_member_id == tables.member.c.id
                )
            )
            .where(
                tables.source.c.workspace_id == self.workspace_id,
                tables.source.c.removed_at.is_(None),
            )
            .order_by(tables.source.c.backend, tables.source.c.created_at)
        )
        if not admin:
            query = query.where(
                sa.or_(
                    tables.source.c.subject == SHARED_SUBJECT,
                    tables.source.c.owner_member_id == member_id,
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            SourceView(
                backend=row.backend,
                shared=row.subject == SHARED_SUBJECT,
                owner_email=row.email if admin or row.owner_member_id == member_id else None,
                consecutive_errors=row.consecutive_errors,
                next_sync_at=row.next_sync_at,
                **_binding_fields(row.backend, row.config),
            )
            for row in rows
        )

    async def spend_caps(self) -> tuple[SpendCapView, ...]:
        """Every spend cap of this workspace with its subject named for the reader — the
        workspace-administration read behind the portal's billing view. Caps are set by the
        deploy's operators today; no object kind owns them, so this stays a read."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.spend_cap.c.scope,
                        tables.spend_cap.c.subject_id,
                        tables.spend_cap.c.window_seconds,
                        tables.spend_cap.c.limit_micro_usd,
                        tables.spend_cap.c.on_breach,
                        tables.agent.c.name.label("agent_name"),
                        tables.member.c.email.label("member_email"),
                    )
                    .select_from(
                        tables.spend_cap.outerjoin(
                            tables.agent,
                            sa.and_(
                                tables.spend_cap.c.scope == "agent",
                                tables.spend_cap.c.subject_id == tables.agent.c.id,
                            ),
                        ).outerjoin(
                            tables.member,
                            sa.and_(
                                tables.spend_cap.c.scope == "member",
                                tables.spend_cap.c.subject_id == tables.member.c.id,
                            ),
                        )
                    )
                    .where(tables.spend_cap.c.workspace_id == self.workspace_id)
                    .order_by(
                        tables.spend_cap.c.scope,
                        tables.spend_cap.c.subject_id,
                        tables.spend_cap.c.window_seconds,
                    )
                )
            ).all()
        return tuple(
            SpendCapView(
                scope=row.scope,
                subject=row.agent_name if row.scope == "agent" else row.member_email,
                window_seconds=row.window_seconds,
                limit_micro_usd=row.limit_micro_usd,
                on_breach=row.on_breach,
            )
            for row in rows
        )

    async def list_installations(self) -> tuple[InstallationSummary, ...]:
        """Every surface installation of this workspace with the agent it binds, ordered by
        surface — the workspace-administration read behind the portal's agents view."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.surface_installation.c.surface,
                        tables.surface_installation.c.agent_id,
                    )
                    .where(tables.surface_installation.c.workspace_id == self.workspace_id)
                    .order_by(tables.surface_installation.c.surface)
                )
            ).all()
        return tuple(
            InstallationSummary(surface=row.surface, agent_id=row.agent_id) for row in rows
        )

    async def list_conversations(
        self, limit: int = LIST_CONVERSATIONS_LIMIT
    ) -> tuple[ConversationSummary, ...]:
        """The workspace's conversations, newest activity first, across every surface — the read
        half a debug view lists. Bounded, and RLS-scoped like every read on this context."""
        activity = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.count().label("turn_count"),
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(
                tables.conversation.c.id,
                tables.conversation.c.surface,
                tables.conversation.c.queue_key,
                tables.conversation.c.created_at,
                tables.member.c.email,
                activity.c.turn_count,
                activity.c.last_turn_at,
            )
            .select_from(
                tables.conversation.outerjoin(
                    tables.member, tables.member.c.id == tables.conversation.c.member_id
                ).outerjoin(activity, activity.c.conversation_id == tables.conversation.c.id)
            )
            .where(tables.conversation.c.workspace_id == self.workspace_id)
            .order_by(
                sa.func.coalesce(activity.c.last_turn_at, tables.conversation.c.created_at).desc()
            )
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            ConversationSummary(
                id=row.id,
                surface=row.surface,
                queue_key=row.queue_key,
                member_email=row.email,
                created_at=row.created_at,
                turn_count=row.turn_count or 0,
                last_turn_at=row.last_turn_at,
            )
            for row in rows
        )

    async def list_agent_conversations(
        self,
        agent_id: UUID,
        member_id: UUID,
        *,
        admin: bool,
        limit: int,
        surface: str | None = None,
    ) -> tuple[ListedConversation, ...]:
        """One agent's conversations as the portal lists them, newest activity first and bounded:
        the member's own plus the workspace-shared ones, every one of the agent's for an admin.
        `surface` narrows to one surface's conversations in the query, before the bound, so a
        member's rows are never displaced by another surface's newer traffic under the cap. Each
        entry carries `readable` (content this viewer reads now) and `disclosable` (an admin may
        acknowledge and read another member's private one — `record_transcript_access` is the
        act). Subagent conversations are absent: they are the agent's own work on a request,
        listed nested under the turn that spawned them, never beside it."""
        activity = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.count().label("turn_count"),
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(
                tables.conversation.c.id,
                tables.conversation.c.surface,
                tables.conversation.c.queue_key,
                tables.conversation.c.audience,
                tables.member.c.email,
                tables.conversation.c.created_at,
                activity.c.turn_count,
                activity.c.last_turn_at,
            )
            .select_from(
                tables.conversation.outerjoin(
                    tables.member, tables.member.c.id == tables.conversation.c.member_id
                ).outerjoin(activity, activity.c.conversation_id == tables.conversation.c.id)
            )
            .where(
                tables.conversation.c.workspace_id == self.workspace_id,
                tables.conversation.c.agent_id == agent_id,
                tables.conversation.c.surface != SUBAGENT_SURFACE,
            )
            .order_by(
                sa.func.coalesce(activity.c.last_turn_at, tables.conversation.c.created_at).desc()
            )
            .limit(limit)
        )
        if surface is not None:
            query = query.where(tables.conversation.c.surface == surface)
        if not admin:
            query = query.where(
                tables.conversation.c.audience.in_(_readable_audience_values(member_id))
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        mine = str(conversation_audience(member_id))
        return tuple(
            ListedConversation(
                summary=ConversationSummary(
                    id=row.id,
                    surface=row.surface,
                    queue_key=row.queue_key,
                    member_email=row.email,
                    created_at=row.created_at,
                    turn_count=row.turn_count or 0,
                    last_turn_at=row.last_turn_at,
                ),
                readable=row.audience in _readable_audience_values(member_id),
                disclosable=admin
                and row.audience != mine
                and audience_member(parse_audience(row.audience)) is not None,
            )
            for row in rows
        )

    async def readable_conversation(
        self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool = False
    ) -> bool:
        """Whether this member may read that conversation's content — its turns, the subagent turns
        it spawned, and its workspace files. True for their own conversations and the
        workspace-shared ones. Another member's private one answers true for an admin who has
        recorded a disclosure against it inside `TRANSCRIPT_ACCESS_WINDOW`
        (`record_transcript_access` — the acknowledgement is the act, and the row is the audit
        record), and false for that same admin until they do. A room (a private channel or group
        DM) and an externally-shared channel stay false for everyone, admin included, because
        participation there is the peer surface's live roster and no portal read can check it
        (#645 — live reachability is the audience authority). The agent is the wall: another
        agent's conversation is unreadable even by id, so every content route fails closed on this
        one answer."""
        async with workspace_tx() as connection:
            found = (
                await connection.execute(
                    sa.select(tables.conversation.c.audience).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.agent_id == agent_id,
                    )
                )
            ).one_or_none()
            if found is None:
                return False
            if found.audience in _readable_audience_values(member_id):
                return True
            if not admin or audience_member(parse_audience(found.audience)) is None:
                return False
            disclosed = (
                await connection.execute(
                    sa.select(tables.transcript_access.c.id).where(
                        tables.transcript_access.c.workspace_id == self.workspace_id,
                        tables.transcript_access.c.conversation_id == conversation_id,
                        tables.transcript_access.c.reader_member_id == member_id,
                        tables.transcript_access.c.created_at
                        >= datetime.now(UTC) - TRANSCRIPT_ACCESS_WINDOW,
                    )
                )
            ).first()
        return disclosed is not None

    async def conversation_subagent_turns(
        self, conversation_id: UUID, limit: int = LIST_TURNS_LIMIT
    ) -> tuple[Turn, ...]:
        """Every turn spawned beneath this conversation's turns, transitively — a subagent that
        spawns its own is nested again. Each runs in its own conversation carrying the parent's
        audience and agent (`SubagentRunner`), so a caller authorized for the parent conversation is
        authorized for these; the tree a view nests is `parent_turn_id` over this set. Breadth
        first, so a parent always precedes the turns it spawned however the timestamps tie, and
        bounded."""
        roots = sa.select(tables.turn.c.id).where(
            tables.turn.c.workspace_id == self.workspace_id,
            tables.turn.c.conversation_id == conversation_id,
        )
        descendants = (
            sa.select(tables.turn.c.id, sa.literal(1).label("depth"))
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.parent_turn_id.in_(roots),
            )
            .cte("subagent_turns", recursive=True)
        )
        descendants = descendants.union_all(
            sa.select(tables.turn.c.id, (descendants.c.depth + 1).label("depth")).where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.parent_turn_id == descendants.c.id,
            )
        )
        query = (
            self._turn_query()
            .add_columns(descendants.c.depth)
            .join(descendants, descendants.c.id == tables.turn.c.id)
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .order_by(descendants.c.depth, tables.turn.c.created_at, tables.turn.c.id)
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(self._turn_record(row) for row in rows)

    async def list_turns(
        self, conversation_id: UUID, limit: int = LIST_TURNS_LIMIT
    ) -> tuple[Turn, ...]:
        """The conversation's turns in admission order, oldest first, bounded to the `limit` most
        recent — each the full durable row (terminal outcome, context, subagent parentage)."""
        query = (
            self._turn_query()
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == conversation_id,
            )
            .order_by(tables.turn.c.seq.desc())
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(self._turn_record(row) for row in reversed(rows))

    async def turn_detail(self, turn_id: UUID) -> TurnDetail | None:
        """One turn with its accounting rows and the subagent turns it spawned, or None when no
        such turn exists in this workspace."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    self._turn_query().where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            children = (
                await connection.execute(
                    self._turn_query()
                    .where(
                        tables.turn.c.parent_turn_id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                    .order_by(tables.turn.c.created_at)
                )
            ).all()
            ledger = (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.dimension,
                        tables.ledger.c.amount,
                        tables.ledger.c.priced_micro_usd,
                        tables.ledger.c.model,
                        tables.ledger.c.created_at,
                    )
                    .where(tables.ledger.c.turn_id == turn_id)
                    .order_by(tables.ledger.c.created_at)
                )
            ).all()
        return TurnDetail(
            turn=self._turn_record(row),
            ledger=tuple(
                LedgerEntry(
                    dimension=entry.dimension,
                    amount=entry.amount,
                    priced_micro_usd=entry.priced_micro_usd,
                    model=entry.model,
                    created_at=entry.created_at,
                )
                for entry in ledger
            ),
            children=tuple(self._turn_record(child) for child in children),
        )

    async def read_transcript(self, conversation_id: UUID) -> Conversation | None:
        """The conversation's durable message transcript — assistant text and tool_use/tool_result
        blocks — or None when the conversation is not this workspace's or has no transcript yet.
        The ownership gate runs first because the blob store is unscoped: a foreign conversation id
        must yield nothing, never another tenant's transcript."""
        if not await self._owned_conversation(conversation_id):
            return None
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None
        return decode(body)

    async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]:
        """The indices of the conversation's persisted compaction records, ascending — each one
        readable via `read_compaction`."""
        if not await self._owned_conversation(conversation_id):
            return ()
        entries = await self.blob.list(f"conversations/{conversation_id}/compactions/")
        indices = {
            int(parts[3])
            for entry in entries
            if len(parts := entry.key.split("/")) == 5 and parts[3].isdigit()
        }
        return tuple(sorted(indices))

    async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None:
        """One compaction's durable record — the pre-compaction window, its replacement, and the
        typed summary — or None when the conversation is not this workspace's or the index holds
        no record."""
        if not await self._owned_conversation(conversation_id):
            return None
        return await read_compaction_record(self.blob, conversation_id, index)

    async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]:
        """Every file in the conversation's `/workspace` — the sandbox's own live state, listed in
        the container — as workspace-relative paths, sorted, bounded by the in-container walk's cap.
        Empty for a conversation that is not this workspace's or has no sandbox yet."""
        if not await self._owned_conversation(conversation_id):
            return ()
        return await self._sandboxes.entries(conversation_id)

    async def read_workspace_file(
        self, conversation_id: UUID, rel: str
    ) -> AsyncIterator[bytes] | None:
        """Stream one workspace file's bytes out of the live sandbox, or None when the
        conversation is not this workspace's, has no sandbox, or the path names nothing. The path
        is workspace-scoped in the session, so it escapes neither the workspace nor the
        container."""
        if not await self._owned_conversation(conversation_id):
            return None
        return await self._sandboxes.read(conversation_id, rel)

    async def installation(self, peer_surface: str) -> str | None:
        """The workspace's installation identity on a peer surface (e.g. Slack's `team:<id>`), or
        None when that surface holds no installation here — how a read view renders deep links
        into the surface a conversation actually lives on."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == self.workspace_id,
                        tables.surface_installation.c.surface == peer_surface,
                    )
                )
            ).one_or_none()
        return None if row is None else row.installation_id

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A transaction over the tables the surface's own extension shipped a migration for — the
        same raw whole-database connection `ExtensionContext.transaction` yields, under the
        workspace this request already resolved. A surface that renders its extension's rows (the
        sites frame reading a `hosted_site`) has no turn and so no `ExtensionContext` to reach them
        through. Scoping every query to `workspace_id` is the surface's responsibility exactly as
        it is a tool handler's: the SDK import boundary is a static gate over imports, never over
        runtime SQL. Commits on exit, rolls back on error."""
        async with workspace_tx() as connection:
            yield connection

    async def _owned_conversation(self, conversation_id: UUID) -> bool:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return row is not None

    def _turn_query(self) -> sa.Select:
        return sa.select(
            tables.turn.c.id,
            tables.turn.c.workspace_id,
            tables.turn.c.conversation_id,
            tables.turn.c.agent_id,
            tables.turn.c.seq,
            tables.turn.c.status,
            tables.turn.c.inbound,
            tables.turn.c.admission_source,
            tables.turn.c.speaker_member_id,
            tables.turn.c.created_at,
            tables.turn.c.updated_at,
            tables.turn.c.context,
            tables.turn.c.terminal,
            tables.turn.c.parent_turn_id,
            tables.turn.c.subagent_profile,
            tables.turn.c.traceparent,
        )

    def _turn_record(self, row: sa.Row) -> Turn:
        return Turn(
            id=row.id,
            workspace_id=row.workspace_id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            seq=row.seq,
            status=row.status,
            inbound=row.inbound,
            admission_source=row.admission_source,
            speaker_member_id=row.speaker_member_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            context=None if row.context is None else TurnContext.model_validate(row.context),
            terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
            parent_turn_id=row.parent_turn_id,
            subagent_profile=row.subagent_profile,
            traceparent=row.traceparent,
        )


class SurfaceWorkspaceUnknown(LookupError):
    """A shared surface request names no workspace this deployment serves."""


class SurfaceInstallationConflict(LookupError):
    """A shared surface installation is already bound to another workspace."""


class UndeclaredSurface(KeyError):
    """A tool tried to register an installation for a surface its manifest does not declare."""


@dataclass(frozen=True)
class SurfaceInstallationAccess:
    """A tool's manifest-scoped installation registry under the ambient workspace."""

    declared: frozenset[str]

    async def bind(self, surface: str, installation_id: str) -> None:
        """Bind one declared surface's installation to this workspace. Reconfiguration replaces
        this workspace's binding; the fleet-wide identity constraint rejects another workspace."""
        if surface not in self.declared:
            raise UndeclaredSurface(surface)
        await _bind_surface_installation(ws_current().workspace_id, surface, installation_id)


@dataclass(frozen=True)
class SurfaceAuth:
    """The pre-binding gate for a shared surface resolver. Fleet lookup returns only the workspace
    owning an exact installation identity; credential reads remain manifest-slot gated."""

    _credentials: CredentialStore | None
    _declared: frozenset[str]
    _surface: str

    async def workspace(self, installation_id: str) -> UUID | None:
        async with owner_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.workspace_id).where(
                        tables.surface_installation.c.surface == self._surface,
                        tables.surface_installation.c.installation_id == installation_id,
                    )
                )
            ).one_or_none()
        return None if row is None else row.workspace_id

    def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None:
        """Recover a sealed credential-authorization handoff's claims before any workspace is bound,
        or None when the seal is tampered or expired. A shared resolver completing a surface's own
        OAuth install reads the sealed workspace from the `state` the browser carries — the Fernet's
        authenticity is the trust, exactly as the connect callback verifies its own sealed state."""
        if self._credentials is None:
            return None
        try:
            return open_credential_request(
                self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
            )
        except CredentialRequestInvalid:
            return None

    async def credential(self, workspace_id: UUID, slot: str) -> str:
        if slot not in self._declared:
            raise ValueError(f"surface resolver reads undeclared credential slot {slot!r}")
        if self._credentials is None:
            raise RuntimeError("surface resolver reads a credential but no store is configured")
        with ws(workspace_id):
            async with workspace_tx() as connection:
                exists = (
                    await connection.execute(
                        sa.select(tables.workspace.c.id).where(
                            tables.workspace.c.id == workspace_id
                        )
                    )
                ).one_or_none()
            if exists is None:
                raise SurfaceWorkspaceUnknown(str(workspace_id))
            return await self._credentials.get(workspace_id, slot)


RouteHandler = Callable[[SurfaceContext, Request], Awaitable[Response]]
PostHandler = Callable[[SurfaceContext, Writeback], Awaitable[str]]
AttachHandler = Callable[[SurfaceContext, Writeback, str], Awaitable[None]]
WorkspaceResolver = Callable[[Request, SurfaceAuth], Awaitable[UUID | Response | None]]
SurfaceContextFactory = Callable[[UUID, str], SurfaceContext]


@dataclass(frozen=True)
class SurfaceIdentityContext:
    """The live workspace dependencies a surface uses to resolve the external user it speaks as."""

    workspace_id: UUID
    blob: BlobStore
    credential: Callable[[str], Awaitable[str]]


SurfaceIdentityResolver = Callable[[SurfaceIdentityContext], Awaitable[str | None]]


class SurfaceDeliveryError(RuntimeError):
    """A durable surface failure carrying the provider's Retry-After, so the poller schedules the
    next attempt when the provider asked instead of on the fixed backoff."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be nonnegative")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


@dataclass(frozen=True)
class SurfaceRoute:
    """One HTTP route a surface serves. Core mounts `handler` for `method` at
    `/surface/<name>/<path>` bound to the surface's `SurfaceContext` (the handler reads path and
    query params off the Request and returns the Response)."""

    method: Literal["GET", "POST"]
    path: str
    handler: RouteHandler


@dataclass(frozen=True)
class SurfaceSpec:
    """One surface an extension registers. Core mounts each of `routes` under `/surface/<name>`
    bound to the surface's `SurfaceContext`. A **durable** surface also declares its two-phase
    writeback delivery: `post` sends the reply and returns its durable reference (recorded before
    any upload, so recovery skips the re-post), then `attach` uploads the turn's shared files into
    that reply. Recovery repeats `attach`: attachment delivery is at-least-once because a crash
    after upload but before the delivered commit cannot distinguish the completed upload. A
    surface may make individual files best effort so one rejection does not block its siblings.
    The poller drives these for every turn its ingest admitted with writeback. A **live**
    surface omits them (`post=attach=None`): it admits without writeback and delivers by tailing the
    hub in its own route, so the poller never sees its turns. `self_user_id` resolves the surface's
    current external speaker for a same-named source before each fetch, preventing its own output
    from becoming source pages without coupling the two extensions."""

    name: str
    routes: tuple[SurfaceRoute, ...]
    identify: WorkspaceResolver
    """How the shared fleet resolves a request's workspace before binding it. The async resolver
    uses `SurfaceAuth` to map an installation and verify that workspace's credential. A UUID binds
    that workspace, None rejects the request, and a Response completes a bounded side-effect-free
    pre-binding handshake. Required because the shared fleet is the only runtime and every request
    must resolve its workspace before touching any data — a surface cannot mount without it."""
    post: PostHandler | None = None
    attach: AttachHandler | None = None
    self_user_id: SurfaceIdentityResolver | None = None


def _writeback_due(now: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(
        tables.turn.c.status.in_(TERMINAL_TURN_STATUSES),
        sa.or_(
            sa.and_(
                tables.writeback.c.status == WRITEBACK_PENDING,
                sa.or_(
                    tables.writeback.c.claim_expires_at.is_(None),
                    tables.writeback.c.claim_expires_at <= now,
                ),
            ),
            sa.and_(
                tables.writeback.c.status == WRITEBACK_CLAIMED,
                tables.writeback.c.claim_expires_at <= now,
            ),
        ),
    )


def writeback_workspaces() -> WorkspaceCandidates:
    """A rotating bounded page of workspace ids holding deliverable writebacks. This is the
    poller's only owner read; every claim, build, credential read, post, attachment, and state
    transition happens after the returned id is bound through the normal workspace boundary."""

    cursor: UUID | None = None

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        query = (
            sa.select(tables.writeback.c.workspace_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(_writeback_due(now))
            .group_by(tables.writeback.c.workspace_id)
            .order_by(tables.writeback.c.workspace_id)
            .limit(WRITEBACK_WORKSPACE_BATCH)
        )
        if cursor is not None:
            query = query.where(tables.writeback.c.workspace_id > cursor)
        return query

    read_due = owner_candidates(due)

    async def candidates() -> tuple[UUID, ...]:
        nonlocal cursor
        workspace_ids = await read_due()
        if not workspace_ids and cursor is not None:
            cursor = None
            workspace_ids = await read_due()
        if workspace_ids:
            cursor = workspace_ids[-1]
        return workspace_ids

    return candidates


class _WritebackClaimLost(RuntimeError):
    pass


class _WritebackDeliveryFailed(RuntimeError):
    def __init__(self, phase: Literal["post", "attach"], error: Exception) -> None:
        self.phase = phase
        self.error = error
        super().__init__(str(error) or type(error).__name__)


@dataclass(frozen=True)
class WritebackPoller:
    """Durable, at-least-once delivery across every registered surface. The hub is lossy, so a reply
    is never posted from a live frame: this poller claims writebacks whose turn reached a terminal
    state, dispatches each to its surface (by the conversation's surface), records the reply ref in
    its own commit before marking delivered, and retries a failed post with backoff until it ages
    out and is terminally failed — so an undeliverable reply neither hot-loops nor lingers. A claim
    (a worker id plus an expiry) is safe under concurrent instances: Postgres skips a peer's locked
    rows, SQLite's single writer serializes them, and a compare-and-swap on the owner means only the
    worker still holding the claim advances it. The worker refreshes its lease while external
    delivery is live; a crash after the ref is recorded resumes attachment delivery without
    re-posting. Attachments are at-least-once and can repeat after a crash between upload and the
    delivered commit. A reply can repeat only after a crash between a successful post and its ref
    commit — the trade is guaranteed delivery over a never-doubled one."""

    worker_id: str
    surfaces: Mapping[str, SurfaceSpec]
    context_for: SurfaceContextFactory
    candidates: WorkspaceCandidates

    async def run(self) -> None:
        semaphore = asyncio.Semaphore(WRITEBACK_WORKSPACE_CONCURRENCY)
        in_flight: dict[UUID, asyncio.Task[None]] = {}
        try:
            while True:
                for workspace_id, task in tuple(in_flight.items()):
                    if not task.done():
                        continue
                    del in_flight[workspace_id]
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        log(
                            "surface.writeback_workspace_failed",
                            workspace_id=str(workspace_id),
                            error_class=type(error).__name__,
                        )
                if len(in_flight) <= WRITEBACK_WORKSPACE_IN_FLIGHT - WRITEBACK_WORKSPACE_BATCH:
                    try:
                        workspace_ids = await self.candidates()
                    except Exception as error:
                        log("surface.writeback_drain_failed", error_class=type(error).__name__)
                    else:
                        for workspace_id in workspace_ids:
                            if workspace_id not in in_flight:
                                in_flight[workspace_id] = asyncio.create_task(
                                    self._drain_workspace(workspace_id, semaphore)
                                )
                await asyncio.sleep(WRITEBACK_POLL_SECONDS)
        finally:
            for task in in_flight.values():
                task.cancel()
            await asyncio.gather(*in_flight.values(), return_exceptions=True)

    async def drain(self) -> None:
        workspace_ids = await self.candidates()
        semaphore = asyncio.Semaphore(WRITEBACK_WORKSPACE_CONCURRENCY)
        results = await asyncio.gather(
            *(self._drain_workspace(workspace_id, semaphore) for workspace_id in workspace_ids),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("writeback workspace drains failed", errors)

    async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            with ws(workspace_id):
                rows = await self._claim(workspace_id)
                renewals = [asyncio.create_task(self._renew_claim(row.turn_id)) for row in rows]
                try:
                    for row, renewal in zip(rows, renewals, strict=True):
                        if row.last_error is not None:
                            log(
                                "surface.writeback_retry",
                                turn_id=str(row.turn_id),
                                last_error=row.last_error,
                            )
                        await self._deliver(workspace_id, row.turn_id, row.reply_ref, renewal)
                finally:
                    for renewal in renewals:
                        if not renewal.done():
                            renewal.cancel()
                    await asyncio.gather(*renewals, return_exceptions=True)

    async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.writeback.c.turn_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(
                tables.writeback.c.workspace_id == workspace_id,
                _writeback_due(now),
            )
            .order_by(tables.writeback.c.created_at)
            .limit(WRITEBACK_CLAIM_BATCH)
            .with_for_update(skip_locked=True, of=tables.writeback)
            .cte("claimable")
        )
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.update(tables.writeback)
                    .where(tables.writeback.c.turn_id == claimable.c.turn_id)
                    .values(
                        status=WRITEBACK_CLAIMED,
                        claimed_by=self.worker_id,
                        claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.writeback.c.turn_id,
                        tables.writeback.c.reply_ref,
                        tables.writeback.c.last_error,
                    )
                )
            ).all()

    async def _deliver(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        reply_ref: str | None,
        renewal: asyncio.Task[None],
    ) -> None:
        started_at = datetime.now(UTC)
        try:
            await self._deliver_with_lease(workspace_id, turn_id, reply_ref, renewal)
        except _WritebackClaimLost:
            log("surface.writeback_claim_lost", turn_id=str(turn_id))
        except _WritebackDeliveryFailed as error:
            outcome, last_error, next_attempt_at = await self._fail_or_retry(turn_id, error)
            if outcome == "claim_lost":
                log("surface.writeback_claim_lost", turn_id=str(turn_id))
                return
            log(
                "surface.writeback_failed",
                turn_id=str(turn_id),
                phase=error.phase,
                outcome=outcome,
                error_class=type(error.error).__name__,
                last_error=last_error,
                next_attempt_at=next_attempt_at,
                elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
            )
        else:
            log(
                "surface.writeback_delivered",
                turn_id=str(turn_id),
                elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
            )

    async def _deliver_with_lease(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        reply_ref: str | None,
        renewal: asyncio.Task[None],
    ) -> None:
        """The lease covers exactly the external delivery, and ends before the commit that closes
        it. The renewal and `_mark_delivered` write the same writeback row, so a refresh still in
        flight holds the row lock the commit needs: the lease is stopped and awaited first, and only
        then does the terminal compare-and-swap run — never against this row's own refresher."""
        delivery = asyncio.create_task(self._deliver_claimed(workspace_id, turn_id, reply_ref))
        try:
            done, _pending = await asyncio.wait(
                (delivery, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if delivery not in done:
                if renewal.cancelled():
                    raise asyncio.CancelledError
                error = renewal.exception()
                if error is None:
                    raise RuntimeError("writeback claim renewal stopped")
                raise error
            await delivery
        finally:
            for task in (delivery, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, renewal, return_exceptions=True)
        await self._mark_delivered(turn_id)

    async def _deliver_claimed(
        self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None
    ) -> None:
        writeback, surface_name = await self._build(turn_id)
        entry = self.surfaces.get(surface_name)
        if entry is None:
            log("surface.writeback_no_surface", turn_id=str(turn_id), surface=surface_name)
            return
        spec = entry
        if spec.post is None or spec.attach is None:
            log("surface.writeback_no_delivery", turn_id=str(turn_id), surface=surface_name)
            return
        context = self.context_for(workspace_id, surface_name)
        if reply_ref is None:
            try:
                reply_ref = await spec.post(context, writeback)
            except Exception as error:
                raise _WritebackDeliveryFailed("post", error) from error
            await self._record_ref(turn_id, reply_ref)
        try:
            await spec.attach(context, writeback, reply_ref)
        except Exception as error:
            raise _WritebackDeliveryFailed("attach", error) from error

    async def _renew_claim(self, turn_id: UUID) -> None:
        while True:
            await asyncio.sleep(WRITEBACK_CLAIM_REFRESH_SECONDS)
            await self._refresh_claim(turn_id)

    async def _refresh_claim(self, turn_id: UUID) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            renewed = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                    updated_at=sa.func.now(),
                )
            )
        if renewed.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _build(self, turn_id: UUID) -> tuple[Writeback, str]:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.terminal,
                        tables.conversation.c.queue_key,
                        tables.conversation.c.surface,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == turn_id)
                )
            ).one()
            artifacts = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                    )
                    .where(tables.shared_artifact.c.turn_id == turn_id)
                    .order_by(
                        tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key
                    )
                )
            ).all()
        terminal = TerminalFrame.model_validate(row.terminal)
        writeback = Writeback(
            turn_id=turn_id,
            queue_key=row.queue_key,
            status=terminal.status,
            text=terminal.text,
            tokens=terminal.tokens,
            cost_micro_usd=terminal.cost_micro_usd,
            cache_percent=terminal.cache_percent,
            model=terminal.model,
            reasoning=terminal.reasoning,
            question=terminal.question,
            credential_request=terminal.credential_request,
            connect_request=terminal.connect_request,
            artifacts=tuple(
                SharedArtifact(
                    blob_key=artifact.blob_key,
                    filename=artifact.filename,
                    subject=artifact.subject,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                )
                for artifact in artifacts
            ),
        )
        return writeback, row.surface

    async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(reply_ref=reply_ref, updated_at=sa.func.now())
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _mark_delivered(self, turn_id: UUID) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    status=WRITEBACK_DELIVERED,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _fail_or_retry(
        self, turn_id: UUID, error: _WritebackDeliveryFailed
    ) -> tuple[str, str, datetime | None]:
        """Release the row for its next attempt — at the provider's Retry-After when the failure
        carried one, else the fixed backoff — or mark it failed once it outlives the writeback
        window. Both delays are bounded by that window, so a hostile Retry-After cannot park a
        row forever."""
        now = datetime.now(UTC)
        give_up_before = now - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS)
        match error.error:
            case SurfaceDeliveryError() as delivery_error:
                retry_after_seconds = delivery_error.retry_after_seconds
            case _:
                retry_after_seconds = None
        retry_seconds = min(
            retry_after_seconds
            if retry_after_seconds is not None
            else WRITEBACK_RETRY_BACKOFF_SECONDS,
            WRITEBACK_MAX_AGE_SECONDS,
        )
        retry_detail = (
            f"; retry_after_seconds={retry_after_seconds}"
            if retry_after_seconds is not None
            else ""
        )
        last_error = f"{error}{retry_detail}"[:MAX_WRITEBACK_ERROR_CHARS]
        retry_at = now + timedelta(seconds=retry_seconds)
        terminal = tables.writeback.c.created_at <= give_up_before
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.update(tables.writeback)
                    .where(
                        tables.writeback.c.turn_id == turn_id,
                        tables.writeback.c.claimed_by == self.worker_id,
                    )
                    .values(
                        status=sa.case((terminal, WRITEBACK_FAILED), else_=WRITEBACK_PENDING),
                        claim_expires_at=sa.case((terminal, None), else_=retry_at),
                        claimed_by=None,
                        last_error=last_error,
                        updated_at=sa.func.now(),
                    )
                    .returning(tables.writeback.c.status, tables.writeback.c.claim_expires_at)
                )
            ).one_or_none()
        outcome = (
            "claim_lost" if row is None else "failed" if row.status == WRITEBACK_FAILED else "retry"
        )
        return outcome, last_error, None if row is None else row.claim_expires_at
