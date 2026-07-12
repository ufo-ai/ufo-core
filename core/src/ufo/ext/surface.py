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
from ufo.blob import BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.credentials import (
    CredentialRequestInvalid,
    CredentialStore,
    open_credential_request,
)
from ufo.db import owner_tx, workspace_tx
from ufo.hub import LiveFrame
from ufo.o11y import log
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_AGENT_NAME,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_PENDING,
    AskUserInput,
    CredentialRequest,
    ReasoningEffort,
    TerminalFrame,
    TerminalStatus,
    TurnContext,
)
from ufo.workspace import ws, ws_current

WORKSPACE_SEGMENT = "workspace"


class MemberAdmitter(Protocol):
    """Admit a member message and consume the conversation's pending one-time pause."""

    async def admit(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
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
    the turn shared, and the structured `question` when asking the user was the turn's final act. A
    surface renders the reply and metadata, uploads `artifacts`, and may render `question` as its
    own answer affordance (buttons) — the answer arrives as the conversation's next turn."""

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
            state = open_credential_request(self._credentials.fernet, sealed)
        except CredentialRequestInvalid:
            return False
        if state.workspace_id != self.workspace_id or slot not in state.slots:
            return False
        return not await self.blob.exists(_fulfilled_marker_key(self.workspace_id, sealed, slot))

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
        state = open_credential_request(self._credentials.fernet, sealed)
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

    async def default_agent(self) -> UUID:
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.name == DEFAULT_AGENT_NAME,
                    )
                )
            ).one_or_none()
        if agent is None:
            raise RuntimeError(f"no {DEFAULT_AGENT_NAME!r} agent for surface {self.surface!r}")
        return agent.id

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
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.member).values(
                        id=uuid4(),
                        workspace_id=self.workspace_id,
                        email=email.strip().lower(),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.member_join_race", surface=self.surface, external_id=external_id)
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
        transaction; a lost creation race re-reads the surviving row. A memberless conversation
        whose resolver now names a member is claimed for them — a DM that began before its speaker
        could resolve (an unconfirmed email, a not-yet-joined teammate) becomes theirs, and their
        memory subject, from the turn that resolves them; a conversation another member already
        owns is never re-claimed."""
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
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
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

    async def admit(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
    ) -> UUID:
        """Admit an inbound message onto the durable turn queue and return its turn id. Delivery is
        admission's concern, derived from the conversation's surface: a durable-surface turn
        registers for the poller atomically with its row, a live surface's turn registers nothing
        and its member tails the hub — the surface supplies only the message, its idempotency
        key, and the ambient `TurnContext` (sender, timezone) the engine renders before the
        inbound. A redelivery deduped to the turn already admitted joins it."""
        return await self._admitter.admit(
            conversation_id,
            agent_id,
            body,
            idempotency_key=idempotency_key,
            context=context,
        )

    async def turn_inbound(self, turn_id: UUID) -> str | None:
        """The inbound message a turn was admitted with, or None when no such turn exists — how a
        surface whose answer affordance raced (an idempotent admit joins the turn the first click
        won) confirms which answer landed: only the click whose body is the stored inbound may
        rewrite the affordance into its answer."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return None if row is None else row.inbound

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
        if not installation_id:
            raise ValueError("surface installation id is empty")
        workspace_id = ws_current().workspace_id
        try:
            async with workspace_tx() as connection:
                match connection.dialect.name:
                    case "postgresql":
                        bound = (
                            await connection.execute(
                                postgres_insert(tables.surface_installation)
                                .values(
                                    workspace_id=workspace_id,
                                    surface=surface,
                                    installation_id=installation_id,
                                    created_at=sa.func.now(),
                                    updated_at=sa.func.now(),
                                )
                                .on_conflict_do_update(
                                    index_elements=("workspace_id", "surface"),
                                    set_={
                                        "installation_id": installation_id,
                                        "updated_at": sa.func.now(),
                                    },
                                )
                                .returning(tables.surface_installation.c.installation_id)
                            )
                        ).scalar_one()
                    case "sqlite":
                        bound = (
                            await connection.execute(
                                sqlite_insert(tables.surface_installation)
                                .values(
                                    workspace_id=workspace_id,
                                    surface=surface,
                                    installation_id=installation_id,
                                    created_at=sa.func.now(),
                                    updated_at=sa.func.now(),
                                )
                                .on_conflict_do_update(
                                    index_elements=("workspace_id", "surface"),
                                    set_={
                                        "installation_id": installation_id,
                                        "updated_at": sa.func.now(),
                                    },
                                )
                                .returning(tables.surface_installation.c.installation_id)
                            )
                        ).scalar_one()
                    case name:
                        raise RuntimeError(f"surface installation binding does not support {name}")
                if bound != installation_id:
                    raise RuntimeError("surface installation binding returned another identity")
        except sa.exc.IntegrityError as error:
            raise SurfaceInstallationConflict(surface) from error


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
    post: PostHandler | None = None
    attach: AttachHandler | None = None
    identify: WorkspaceResolver | None = None
    """How shared serve resolves a request's workspace before binding it. The async resolver
    uses `SurfaceAuth` to map an installation and verify that workspace's credential. A UUID binds
    that workspace, None rejects the request, and a Response completes a bounded side-effect-free
    pre-binding handshake. A dedicated deploy pins one workspace and never calls this; a surface
    that omits it is not mounted on shared serve."""


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
    pass


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
        try:
            await self._deliver_with_lease(workspace_id, turn_id, reply_ref, renewal)
        except _WritebackClaimLost:
            log("surface.writeback_claim_lost", turn_id=str(turn_id))
        except _WritebackDeliveryFailed as error:
            log(
                "surface.writeback_post_failed",
                turn_id=str(turn_id),
                error_class=type(error.__cause__).__name__,
            )
            await self._fail_or_retry(turn_id, str(error)[:MAX_WRITEBACK_ERROR_CHARS])

    async def _deliver_with_lease(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        reply_ref: str | None,
        renewal: asyncio.Task[None],
    ) -> None:
        delivery = asyncio.create_task(self._deliver_claimed(workspace_id, turn_id, reply_ref))
        try:
            done, _pending = await asyncio.wait(
                (delivery, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if delivery in done:
                await delivery
                return
            if renewal.cancelled():
                raise asyncio.CancelledError
            error = renewal.exception()
            if error is None:
                raise RuntimeError("writeback claim renewal stopped")
            raise error
        finally:
            for task in (delivery, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, renewal, return_exceptions=True)

    async def _deliver_claimed(
        self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None
    ) -> None:
        writeback, surface_name = await self._build(turn_id)
        entry = self.surfaces.get(surface_name)
        if entry is None:
            log("surface.writeback_no_surface", turn_id=str(turn_id), surface=surface_name)
            await self._mark_delivered(turn_id)
            return
        spec = entry
        if spec.post is None or spec.attach is None:
            log("surface.writeback_no_delivery", turn_id=str(turn_id), surface=surface_name)
            await self._mark_delivered(turn_id)
            return
        context = self.context_for(workspace_id, surface_name)
        if reply_ref is None:
            try:
                reply_ref = await spec.post(context, writeback)
            except Exception as error:
                raise _WritebackDeliveryFailed(str(error)) from error
            await self._record_ref(turn_id, reply_ref)
        try:
            await spec.attach(context, writeback, reply_ref)
        except Exception as error:
            raise _WritebackDeliveryFailed(str(error)) from error
        await self._mark_delivered(turn_id)

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
                    last_error=None,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _fail_or_retry(self, turn_id: UUID, last_error: str) -> None:
        now = datetime.now(UTC)
        give_up_before = now - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS)
        aged_out = tables.writeback.c.created_at <= give_up_before
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    status=sa.case((aged_out, WRITEBACK_FAILED), else_=WRITEBACK_PENDING),
                    claim_expires_at=sa.case(
                        (aged_out, None),
                        else_=now + timedelta(seconds=WRITEBACK_RETRY_BACKOFF_SECONDS),
                    ),
                    claimed_by=None,
                    last_error=last_error,
                    updated_at=sa.func.now(),
                )
            )
