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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal, Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, field_validator
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.requests import Request
from starlette.responses import Response

from ufo.accounting import SpendReport, SpendRollup
from ufo.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from ufo.blob import BlobNotFound, BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialStore,
    open_credential_request,
)
from ufo.db import owner_tx, workspace_tx
from ufo.grants import (
    ConnectHandoff,
    ConnectRequestInvalid,
    ConnectUnavailable,
    installed_connect_flow,
)
from ufo.hub import LiveFrame
from ufo.o11y import log
from ufo.schema import tables
from ufo.schema.records import (
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
    Turn,
    TurnContext,
)
from ufo.seats import create_member
from ufo.transcript import (
    CompactionRecord,
    Conversation,
    decode,
    read_compaction_record,
    transcript_key,
)
from ufo.workspace import ws, ws_current

WORKSPACE_SEGMENT = "workspace"
OPERATOR_EMAIL_DOMAIN = "metalcraft.ai"


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
    ) -> UUID: ...


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


def workspace_key(conversation_id: UUID, rel: str) -> str:
    """The blob key of a file inside the conversation's `workspace/` subtree — a sibling of the
    transcript under `conversations/<id>/`, the same subtree the sandbox bind-mounts. The relative
    path is scoped so a surface-supplied name can neither escape the subtree nor reach the
    transcript above it."""
    parts = [part for part in PurePosixPath(rel).parts if part not in ("", ".", "/")]
    if not parts or ".." in parts or rel.startswith("/"):
        raise ValueError(f"workspace path {rel!r} is not a relative path inside the workspace")
    return f"conversations/{conversation_id}/{WORKSPACE_SEGMENT}/{'/'.join(parts)}"


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


class WorkspaceFile(BaseModel):
    """One file in a conversation's `workspace/` subtree, as the file browser lists it — the
    relative path inside the subtree, never the blob key."""

    path: str
    size_bytes: int
    modified_at: datetime


def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str:
    """The blob marker one fulfilled prompt leaves, keyed by the seal's digest and the slot — the
    render gate reads it per prompt, so a stored slot stops prompting while its siblings keep
    asking, and a fresh request (a rotation) seals differently and prompts anew."""
    digest = hashlib.sha256(sealed.encode()).hexdigest()[:32]
    return f"workspaces/{workspace_id}/credential_requests/{digest}/{slot}"


def _email_domain(email: str) -> str:
    """The address's domain, lowercased — empty for anything that is not `local@domain`, so a
    malformed value can never satisfy a domain match."""
    local, _, domain = email.strip().lower().rpartition("@")
    return domain if local and domain else ""


async def _earliest_agent(workspace_id: UUID) -> UUID:
    """The workspace's earliest agent by (created_at, id) — the agent a binding or conversation
    lands on when no surface binding names one, mirroring how the earliest member is the owner.
    Onboarding creates the first agent, so a workspace without one is broken configuration."""
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id)
                .where(tables.agent.c.workspace_id == workspace_id)
                .order_by(tables.agent.c.created_at, tables.agent.c.id)
                .limit(1)
            )
        ).one_or_none()
    if agent is None:
        raise RuntimeError(f"workspace {workspace_id} has no agent")
    return agent.id


async def _bind_surface_installation(
    workspace_id: UUID, surface: str, installation_id: str
) -> None:
    """Upsert one surface's installation binding for a workspace, replacing any prior binding for
    that (workspace, surface). A new binding lands on the workspace's earliest agent; rebinding
    replaces the installation identity and keeps the binding's agent. The fleet-wide uniqueness on
    (surface, installation_id) raises `SurfaceInstallationConflict` when the installation already
    belongs to another workspace. The one place the binding is written — a tool
    (`SurfaceInstallationAccess.bind`) and a surface's own OAuth callback
    (`SurfaceContext.bind_installation`) both land it here."""
    if not installation_id:
        raise ValueError("surface installation id is empty")
    agent_id = await _earliest_agent(workspace_id)
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
    identity). A **durable** surface (Slack) delivers through the poller and `artifact_link`; a
    **live** surface (web; core's CLI is the built-in twin) delivers by `tail`-ing the turn's
    frames off the hub in its own SSE route, reading
    `turn_owner` to gate a tail and `spend_rollup` for a spend view. Each calls only what it needs.
    `credential` reads the surface workspace's slots in-process (never through the sandbox proxy); a
    surface declaring no slots holds no store and never calls it."""

    workspace_id: UUID
    surface: str
    blob: BlobStore
    _admitter: MemberAdmitter
    _tailer: TurnTailer
    _credentials: CredentialStore | None
    _artifact_token_secret: str
    _public_base_url: str | None

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

    async def _owner_email(self) -> str | None:
        """The earliest member's email. Their domain doubles as the workspace's own domain: the
        owner onboarded through provisioning's vetted domain match, and the workspace stores no
        domain of its own (the shared tier derives its id from the domain; the enterprise tier
        labels the Tenant CR)."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.member.c.email)
                    .where(tables.member.c.workspace_id == self.workspace_id)
                    .order_by(tables.member.c.created_at.asc(), tables.member.c.id.asc())
                    .limit(1)
                )
            ).one_or_none()
        return None if row is None else row.email

    async def is_operator_workspace(self) -> bool:
        """Whether this workspace is the fleet operator's own — the workspace whose own domain
        (its owner's email domain, the same resolution hosted onboarding joins by) is
        `OPERATOR_EMAIL_DOMAIN`. Gates renderings meant for the operator alone, like Slack's
        accounting footer and its debugger link — never a tenant-facing capability; an ownerless
        workspace is never the operator's, so internals render nowhere rather than in a
        customer's thread."""
        owner = await self._owner_email()
        return owner is not None and _email_domain(owner) == OPERATOR_EMAIL_DOMAIN

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
        the workspace's own — the owner's email domain — creates the member and links it in one
        step, so a teammate becomes a member on first contact and only the owner ever onboards
        through provisioning. The surface asserting the email is the trust anchor: it calls this
        only with an email its channel verified. A foreign-domain email stays unlinked; a lost
        creation race collapses on the member's (workspace_id, email) uniqueness and links the
        surviving row."""
        linked = await self.link_member(external_id, email)
        if linked is not None:
            return linked
        owner = await self._owner_email()
        domain = _email_domain(email)
        if owner is None or not domain or domain != _email_domain(owner):
            return None
        async with workspace_tx() as connection:
            await create_member(connection, self.workspace_id, email.strip().lower())
        return await self.link_member(external_id, email)

    def _conversation_lookup(self, queue_key: str) -> sa.Select:
        return sa.select(tables.conversation.c.id, tables.conversation.c.member_id).where(
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

    async def conversation_for(self, queue_key: str, member_id: UUID | None) -> UUID:
        """Get-or-create the conversation this surface keys by `queue_key`, outside any admission
        transaction; a lost creation race re-reads the surviving row. A new conversation binds
        permanently to the surface's agent — the surface's installation binding when one exists,
        else the workspace's earliest agent — and admission derives every turn's agent from that
        binding. A memberless conversation whose resolver now names a member is claimed for
        them — a DM that began before its speaker could resolve (an unconfirmed email, a
        not-yet-joined teammate) becomes theirs, and their memory subject, from the turn that
        resolves them; a conversation another member already owns is never re-claimed."""
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        if found is not None:
            if member_id is not None and found.member_id is None:
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.conversation)
                        .where(
                            tables.conversation.c.id == found.id,
                            tables.conversation.c.member_id.is_(None),
                        )
                        .values(member_id=member_id, updated_at=sa.func.now())
                    )
            return found.id
        conversation_id = uuid4()
        agent_id = await self._surface_agent()
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
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.conversation_create_lost_race", surface=self.surface, queue_key=queue_key)
            async with workspace_tx() as connection:
                return (await connection.execute(self._conversation_lookup(queue_key))).one().id
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
        return await _earliest_agent(self.workspace_id)

    async def admit(
        self,
        conversation_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
    ) -> UUID:
        """Admit an inbound message onto the durable turn queue and return its turn id. The turn
        executes as the conversation's bound agent — a surface never names one. Delivery is
        admission's concern, derived from the conversation's surface: a durable-surface turn
        registers for the poller atomically with its row, a live surface's turn registers nothing
        and its member tails the hub — the surface supplies only the message, its idempotency
        key, and the ambient `TurnContext` (sender, timezone) the engine renders before the
        inbound. A redelivery deduped to the turn already admitted joins it."""
        return await self._admitter.admit(
            conversation_id,
            body,
            idempotency_key=idempotency_key,
            context=context,
            speaker_member_id=speaker_member_id,
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

    async def latest_turn(self, conversation_id: UUID) -> UUID | None:
        """The most recent turn admitted to a conversation, or None when it holds none — the
        turn a live surface resumes tailing when a held stream reconnects to drain an answer that
        outran the hold, a conversation-keyed poll the web surface never needs because its own
        stream route carries the turn id."""
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
        """Stream a file into the conversation's workspace subtree before its turn runs, so the
        sandbox mounts it already present. The bytes never buffer whole — the surface hands an async
        chunk iterator (a streamed download) straight to the blob store."""
        await self.blob.put_stream(workspace_key(conversation_id, rel), chunks)

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
        """Every file in the conversation's `workspace/` subtree — the sandbox's working files —
        as subtree-relative paths, sorted, bounded by the blob store's list cap."""
        if not await self._owned_conversation(conversation_id):
            return ()
        prefix = f"conversations/{conversation_id}/{WORKSPACE_SEGMENT}/"
        entries = await self.blob.list(prefix)
        return tuple(
            WorkspaceFile(
                path=entry.key.removeprefix(prefix),
                size_bytes=entry.size_bytes,
                modified_at=entry.modified_at,
            )
            for entry in entries
        )

    async def read_workspace_file(
        self, conversation_id: UUID, rel: str
    ) -> AsyncIterator[bytes] | None:
        """Stream one workspace file's bytes, or None when the conversation is not this
        workspace's or the path names nothing. The path is validated by `workspace_key`, so it can
        neither escape the subtree nor reach the transcript above it."""
        if not await self._owned_conversation(conversation_id):
            return None
        key = workspace_key(conversation_id, rel)
        if not await self.blob.exists(key):
            return None
        return self.blob.get_stream(key)

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
    hub in its own route, so the poller never sees its turns."""

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
                    .order_by(tables.shared_artifact.c.created_at)
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
