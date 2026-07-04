"""The surface seam: the privileged capabilities a surface extension reaches into core for.

A surface is trusted infrastructure — it asserts a member's identity and admits turns as that member
— so unlike the scoped `ExtensionContext` (a ScopedStore, declared credential slots, a read-only
trajectory corpus), a `SurfaceContext` carries the three privileged capabilities a surface needs and
nothing a scoped extension may hold: (1) admit a turn onto the durable queue (the same `invoke`
scheduled tasks and the eval harness call), registering it for writeback; (2) resolve an external id
to a member and a conversation, linking a `surface_identity` on first contact; (3) stream an inbound
file into the conversation's workspace subtree. A surface declares its ingest and its two-phase
writeback delivery (`post` then best-effort `attach`) as a `SurfaceSpec`; core mounts the ingest and
runs the `WritebackPoller` that drives delivery — at-least-once, because the hub is lossy, so the
reply is posted from the durable terminal frame, never a live frame."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from starlette.requests import Request
from starlette.responses import Response

from selfhost.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from selfhost.blob import BlobStore
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME, TerminalFrame, TerminalStatus

WORKSPACE_SEGMENT = "workspace"


class TurnInvoker(Protocol):
    """Admit an inbound message onto the durable turn queue and return its turn id — the one
    boundary that evaluates the spend cap. The concrete invoker binds the workspace; a surface, a
    job, or an extension `invoke` all reach the queue through this one primitive."""

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str | None = None,
    ) -> UUID: ...


WRITEBACK_PENDING = "pending"
WRITEBACK_CLAIMED = "claimed"
WRITEBACK_DELIVERED = "delivered"
WRITEBACK_FAILED = "failed"
TERMINAL_TURN_STATUSES: tuple[str, ...] = ("done", "failed", "cancelled")
MAX_WRITEBACK_ERROR_CHARS = 2_048
WRITEBACK_POLL_SECONDS = 1.0
WRITEBACK_CLAIM_SECONDS = 300
WRITEBACK_RETRY_BACKOFF_SECONDS = 60
WRITEBACK_MAX_AGE_SECONDS = 3600
WRITEBACK_CLAIM_BATCH = 16


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
    own channel/thread from it), the terminal outcome (`status` + the agent's `text`), and the files
    the turn shared. A surface renders the reply from `status`/`text` and uploads `artifacts`."""

    turn_id: UUID
    queue_key: str
    status: TerminalStatus
    text: str
    artifacts: tuple[SharedArtifact, ...]


@dataclass(frozen=True)
class SurfaceContext:
    """The privileged handle a surface's handlers receive. `blob` and the admit/identity reach
    are deliberately unscoped for a workspace's trusted surface — the distinction from a scoped
    extension context, which never admits a turn or asserts identity. `credential` reads the surface
    workspace's slots (the bot token, the signing secret) in-process, never through the sandbox
    proxy."""

    workspace_id: UUID
    surface: str
    blob: BlobStore
    _invoker: TurnInvoker
    _credentials: CredentialStore
    _artifact_token_secret: str
    _public_base_url: str | None

    async def credential(self, slot: str) -> str:
        return await self._credentials.get(self.workspace_id, slot)

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

    async def linked_member(self, external_id: str) -> UUID | None:
        async with workspace_tx() as connection:
            linked = (
                await connection.execute(
                    sa.select(tables.surface_identity.c.member_id).where(
                        tables.surface_identity.c.workspace_id == self.workspace_id,
                        tables.surface_identity.c.surface == self.surface,
                        tables.surface_identity.c.external_id == external_id,
                    )
                )
            ).one_or_none()
        return None if linked is None else linked.member_id

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

    async def conversation_for(self, queue_key: str, member_id: UUID | None) -> UUID:
        """Get-or-create the conversation this surface keys by `queue_key`, outside any admission
        transaction; a lost creation race re-reads the surviving row."""
        lookup = sa.select(tables.conversation.c.id).where(
            tables.conversation.c.workspace_id == self.workspace_id,
            tables.conversation.c.surface == self.surface,
            tables.conversation.c.queue_key == queue_key,
        )
        async with workspace_tx() as connection:
            found = (await connection.execute(lookup)).one_or_none()
        if found is not None:
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
                return (await connection.execute(lookup)).one().id
        return conversation_id

    async def admit(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
    ) -> UUID:
        """Admit an inbound message onto the durable turn queue and register it for writeback. A
        redelivery deduped to the turn already admitted joins it — the writeback insert hits the
        primary key the poller already owns, so the collision is dropped, not doubled."""
        turn_id = await self._invoker.invoke(
            conversation_id, agent_id, body, idempotency_key=idempotency_key
        )
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.writeback).values(
                        turn_id=turn_id,
                        workspace_id=self.workspace_id,
                        status=WRITEBACK_PENDING,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.writeback_exists", turn_id=str(turn_id))
        return turn_id

    async def write_workspace_file(
        self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]
    ) -> None:
        """Stream a file into the conversation's workspace subtree before its turn runs, so the
        sandbox mounts it already present. The bytes never buffer whole — the surface hands an async
        chunk iterator (a streamed download) straight to the blob store."""
        await self.blob.put_stream(workspace_key(conversation_id, rel), chunks)


IngestHandler = Callable[[SurfaceContext, Request], Awaitable[Response]]
PostHandler = Callable[[SurfaceContext, Writeback], Awaitable[str]]
AttachHandler = Callable[[SurfaceContext, Writeback, str], Awaitable[None]]


@dataclass(frozen=True)
class SurfaceSpec:
    """One surface an extension registers. Core mounts `ingest` at `/surface/<name>` bound to the
    surface's `SurfaceContext`, and the writeback poller drives delivery in two phases: `post` sends
    the reply and returns its durable reference (recorded before any upload, so a recovered delivery
    skips the re-post), then `attach` uploads the turn's shared files into that reply — best effort,
    so a rejected file never re-posts the reply or blocks the rest."""

    name: str
    ingest: IngestHandler
    post: PostHandler
    attach: AttachHandler


@dataclass(frozen=True)
class WritebackPoller:
    """Durable, at-least-once delivery across every registered surface. The hub is lossy, so a reply
    is never posted from a live frame: this poller claims writebacks whose turn reached a terminal
    state, dispatches each to its surface (by the conversation's surface), records the reply ref in
    its own commit before marking delivered, and retries a failed post with backoff until it ages
    out and is terminally failed — so an undeliverable reply neither hot-loops nor lingers. A claim
    (a worker id plus an expiry) is safe under concurrent instances: Postgres skips a peer's locked
    rows, SQLite's single writer serializes them, and a compare-and-swap on the owner means only the
    worker still holding the claim advances it. A crash after the ref is recorded re-finalizes
    without re-posting or re-uploading; the only double is a crash between a successful post and its
    ref commit — the trade is guaranteed delivery over a never-doubled one."""

    workspace_id: UUID
    worker_id: str
    surfaces: Mapping[str, tuple[SurfaceSpec, SurfaceContext]]

    async def run(self) -> None:
        while True:
            try:
                await self.drain()
            except Exception as error:
                log("surface.writeback_drain_failed", error_class=type(error).__name__)
            await asyncio.sleep(WRITEBACK_POLL_SECONDS)

    async def drain(self) -> None:
        for row in await self._claim():
            if row.last_error is not None:
                log("surface.writeback_retry", turn_id=str(row.turn_id), last_error=row.last_error)
            await self._deliver(row.turn_id, row.reply_ref)

    async def _claim(self) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.writeback.c.turn_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(
                tables.writeback.c.workspace_id == self.workspace_id,
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

    async def _deliver(self, turn_id: UUID, reply_ref: str | None) -> None:
        writeback, surface_name = await self._build(turn_id)
        entry = self.surfaces.get(surface_name)
        if entry is None:
            log("surface.writeback_no_surface", turn_id=str(turn_id), surface=surface_name)
            await self._mark_delivered(turn_id)
            return
        spec, context = entry
        try:
            if reply_ref is None:
                reply_ref = await spec.post(context, writeback)
                await self._record_ref(turn_id, reply_ref)
                await spec.attach(context, writeback, reply_ref)
            await self._mark_delivered(turn_id)
        except Exception as error:
            log(
                "surface.writeback_post_failed",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )
            await self._fail_or_retry(turn_id, str(error)[:MAX_WRITEBACK_ERROR_CHARS])

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
            await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(reply_ref=reply_ref, updated_at=sa.func.now())
            )

    async def _mark_delivered(self, turn_id: UUID) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
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
