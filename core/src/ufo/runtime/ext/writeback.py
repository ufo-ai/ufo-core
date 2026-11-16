"""The two delivery pollers: a turn's closing reply and the replies it speaks mid-turn.

Both claim a delivery row, hand it to the surface the turn was admitted on, and commit the
outcome — so both carry the same claim, refresh and backoff constants, and a delivery that
outlives its claim is retried rather than spoken twice."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import log
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.runtime.ext.surface import (
    TERMINAL_TURN_STATUSES,
    MidTurnReply,
    NothingDelivered,
    SharedArtifact,
    SurfaceContextFactory,
    SurfaceDeliveryError,
    SurfaceSpec,
    Writeback,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    SURFACE_COMMENT_ROUND_INDEX,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_PENDING,
    TerminalFrame,
    TurnRuntimeConfig,
)

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


def _writeback_due(now: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(
        tables.turn.c.status.in_(TERMINAL_TURN_STATUSES),
        ~sa.exists().where(
            tables.mid_turn_reply.c.turn_id == tables.turn.c.id,
            tables.mid_turn_reply.c.status.in_((WRITEBACK_PENDING, WRITEBACK_CLAIMED)),
        ),
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
        """The renewal and `_mark_delivered` write the same row, so a refresh in flight holds the
        lock the commit needs: the lease is stopped and awaited first."""
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
                posted = await spec.post(context, writeback)
            except Exception as error:
                raise _WritebackDeliveryFailed("post", error) from error
            if isinstance(posted, NothingDelivered):
                log(
                    "surface.writeback_nothing_delivered",
                    turn_id=str(turn_id),
                    surface=surface_name,
                )
                return
            reply_ref = posted
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
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.runtime_config,
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
                        tables.shared_artifact.c.id,
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                        tables.shared_artifact.c.role,
                    )
                    .where(
                        tables.shared_artifact.c.turn_id == turn_id,
                        tables.shared_artifact.c.attached_by_member.is_(False),
                    )
                    .order_by(
                        tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key
                    )
                )
            ).all()
        writeback = Writeback(
            turn_id=turn_id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            queue_key=row.queue_key,
            terminal=TerminalFrame.model_validate(row.terminal),
            artifacts=tuple(
                SharedArtifact(
                    id=artifact.id,
                    blob_key=artifact.blob_key,
                    filename=artifact.filename,
                    subject=artifact.subject,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    role=artifact.role,
                )
                for artifact in artifacts
            ),
            speaker_member_id=row.speaker_member_id,
            post_in_thread=TurnRuntimeConfig.model_validate(
                row.runtime_config or {}
            ).post_in_thread,
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


def mid_turn_reply_workspaces() -> WorkspaceCandidates:
    """A rotating bounded page of workspace ids holding deliverable mid-turn replies. Nothing here
    waits on a terminal status: the turn is still running, which is the whole point of the row."""

    cursor: UUID | None = None

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        query = (
            sa.select(tables.mid_turn_reply.c.workspace_id)
            .where(_mid_turn_reply_due(now))
            .group_by(tables.mid_turn_reply.c.workspace_id)
            .order_by(tables.mid_turn_reply.c.workspace_id)
            .limit(WRITEBACK_WORKSPACE_BATCH)
        )
        if cursor is not None:
            query = query.where(tables.mid_turn_reply.c.workspace_id > cursor)
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


def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(
        sa.and_(
            tables.mid_turn_reply.c.status == WRITEBACK_PENDING,
            sa.or_(
                tables.mid_turn_reply.c.claim_expires_at.is_(None),
                tables.mid_turn_reply.c.claim_expires_at <= now,
            ),
        ),
        sa.and_(
            tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
            tables.mid_turn_reply.c.claim_expires_at <= now,
        ),
    )


@dataclass(frozen=True)
class MidTurnReplyPoller:
    """Exactly-once delivery of replies and source-surface comment notices before a turn ends.

    Three independent guards, because all three failures are real. The engine writes one row per
    span under the span's own identity; admission does the same for one portal comment, so a replay
    or request redelivery inserts nothing. This poller claims a row with its worker id and an expiry
    and renews every row in the batch from the moment it is claimed, including rows waiting behind
    an earlier send. It advances a row only while it still holds the claim, so a second replica
    never delivers the row this one has. The surface keys its own delivery record on `reply.id`,
    which closes the one window where two workers can both call out — a claim lost while a post is
    in flight.

    Order is the model's: rows are claimed and delivered oldest first and, within one moment, in
    span order — a resumed run counts its rounds from one again, so the round and the span alone
    would rank its spans against a parked attempt's by nothing at all. A turn's terminal writeback
    waits behind every span of its own. A surface that declares no `speak` marks its rows delivered
    untouched, because a live surface's member read the reply off the hub as the round produced it
    and there is nothing left to send."""

    worker_id: str
    surfaces: Mapping[str, SurfaceSpec]
    context_for: SurfaceContextFactory
    candidates: WorkspaceCandidates

    async def run(self) -> None:
        while True:
            try:
                await self.drain()
            except Exception as error:
                log("surface.mid_turn_reply_drain_failed", error_class=type(error).__name__)
            await asyncio.sleep(WRITEBACK_POLL_SECONDS)

    async def drain(self) -> None:
        for workspace_id in await self.candidates():
            with ws(workspace_id):
                rows = await self._claim(workspace_id)
                renewals = [asyncio.create_task(self._renew_claim(row.id)) for row in rows]
                try:
                    for row, renewal in zip(rows, renewals, strict=True):
                        if row.last_error is not None:
                            log(
                                "surface.mid_turn_reply_retry",
                                reply_id=str(row.id),
                                last_error=row.last_error,
                            )
                        await self._deliver(workspace_id, row, renewal)
                finally:
                    for renewal in renewals:
                        if not renewal.done():
                            renewal.cancel()
                    await asyncio.gather(*renewals, return_exceptions=True)

    async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.mid_turn_reply.c.id)
            .where(
                tables.mid_turn_reply.c.workspace_id == workspace_id,
                _mid_turn_reply_due(now),
            )
            .order_by(
                tables.mid_turn_reply.c.created_at,
                tables.mid_turn_reply.c.round_index,
                tables.mid_turn_reply.c.span_index,
            )
            .limit(WRITEBACK_CLAIM_BATCH)
            .with_for_update(skip_locked=True, of=tables.mid_turn_reply)
            .cte("claimable")
        )
        async with workspace_tx() as connection:
            claimed = (
                await connection.execute(
                    sa.update(tables.mid_turn_reply)
                    .where(tables.mid_turn_reply.c.id == claimable.c.id)
                    .values(
                        status=WRITEBACK_CLAIMED,
                        claimed_by=self.worker_id,
                        claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.mid_turn_reply.c.id,
                        tables.mid_turn_reply.c.turn_id,
                        tables.mid_turn_reply.c.round_index,
                        tables.mid_turn_reply.c.span_index,
                        tables.mid_turn_reply.c.message_ref,
                        tables.mid_turn_reply.c.text,
                        tables.mid_turn_reply.c.reply_ref,
                        tables.mid_turn_reply.c.last_error,
                        tables.mid_turn_reply.c.created_at,
                    )
                )
            ).all()
        return sorted(claimed, key=lambda row: (row.created_at, row.round_index, row.span_index))

    async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None:
        started_at = datetime.now(UTC)
        try:
            await self._deliver_with_lease(workspace_id, row, renewal)
        except _WritebackClaimLost:
            log("surface.mid_turn_reply_claim_lost", reply_id=str(row.id))
            return
        except Exception as error:
            outcome, last_error, next_attempt_at = await self._fail_or_retry(row.id, error)
            log(
                "surface.mid_turn_reply_failed",
                reply_id=str(row.id),
                turn_id=str(row.turn_id),
                outcome=outcome,
                error_class=type(error).__name__,
                last_error=last_error,
                next_attempt_at=next_attempt_at,
            )
            return
        log(
            "surface.mid_turn_reply_delivered",
            reply_id=str(row.id),
            turn_id=str(row.turn_id),
            message_ref=str(row.message_ref or ""),
            elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
        )

    async def _deliver_with_lease(
        self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]
    ) -> None:
        delivery = asyncio.create_task(self._speak(workspace_id, row))
        try:
            done, _pending = await asyncio.wait(
                (delivery, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if delivery not in done:
                if renewal.cancelled():
                    raise asyncio.CancelledError
                error = renewal.exception()
                if error is None:
                    raise RuntimeError("mid-turn reply claim renewal stopped")
                raise error
            reply_ref = await delivery
        finally:
            for task in (delivery, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, renewal, return_exceptions=True)
        await self._mark_delivered(row.id, reply_ref)

    async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None:
        """A recorded `reply_ref` means an earlier post landed and only its commit was lost."""
        if row.reply_ref is not None:
            return str(row.reply_ref)
        async with workspace_tx() as connection:
            turn = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.runtime_config,
                        tables.conversation.c.queue_key,
                        tables.conversation.c.surface,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == row.turn_id)
                )
            ).one()
        spec = self.surfaces.get(turn.surface)
        if spec is None:
            log("surface.mid_turn_reply_no_surface", reply_id=str(row.id), surface=turn.surface)
            return None
        if spec.speak is None:
            return None
        return await spec.speak(
            self.context_for(workspace_id, turn.surface),
            MidTurnReply(
                id=row.id,
                turn_id=row.turn_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                queue_key=turn.queue_key,
                message_ref=row.message_ref,
                text=row.text,
                is_comment=row.round_index == SURFACE_COMMENT_ROUND_INDEX,
                speaker_member_id=turn.speaker_member_id,
                post_in_thread=TurnRuntimeConfig.model_validate(
                    turn.runtime_config or {}
                ).post_in_thread,
            ),
        )

    async def _renew_claim(self, reply_id: UUID) -> None:
        while True:
            await asyncio.sleep(WRITEBACK_CLAIM_REFRESH_SECONDS)
            await self._refresh_claim(reply_id)

    async def _refresh_claim(self, reply_id: UUID) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            renewed = await connection.execute(
                sa.update(tables.mid_turn_reply)
                .where(
                    tables.mid_turn_reply.c.id == reply_id,
                    tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
                    tables.mid_turn_reply.c.claimed_by == self.worker_id,
                )
                .values(
                    claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                    updated_at=sa.func.now(),
                )
            )
        if renewed.rowcount != 1:
            raise _WritebackClaimLost

    async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.mid_turn_reply)
                .where(
                    tables.mid_turn_reply.c.id == reply_id,
                    tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
                    tables.mid_turn_reply.c.claimed_by == self.worker_id,
                )
                .values(
                    status=WRITEBACK_DELIVERED,
                    reply_ref=reply_ref,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost

    async def _fail_or_retry(
        self, reply_id: UUID, error: Exception
    ) -> tuple[str, str, datetime | None]:
        now = datetime.now(UTC)
        give_up_before = now - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS)
        match error:
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
        last_error = (str(error) or type(error).__name__)[:MAX_WRITEBACK_ERROR_CHARS]
        aged_out = tables.mid_turn_reply.c.created_at <= give_up_before
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.update(tables.mid_turn_reply)
                    .where(
                        tables.mid_turn_reply.c.id == reply_id,
                        tables.mid_turn_reply.c.claimed_by == self.worker_id,
                    )
                    .values(
                        status=sa.case((aged_out, WRITEBACK_FAILED), else_=WRITEBACK_PENDING),
                        claim_expires_at=sa.case(
                            (aged_out, None), else_=now + timedelta(seconds=retry_seconds)
                        ),
                        claimed_by=None,
                        last_error=last_error,
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.mid_turn_reply.c.status,
                        tables.mid_turn_reply.c.claim_expires_at,
                    )
                )
            ).one_or_none()
        outcome = (
            "claim_lost" if row is None else "failed" if row.status == WRITEBACK_FAILED else "retry"
        )
        return outcome, last_error, None if row is None else row.claim_expires_at
