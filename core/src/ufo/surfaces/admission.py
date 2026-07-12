"""Shared turn admission: the one producer every surface, job, and extension-invoked turn calls to
place an inbound message on the durable turn queue — so the spend cap is evaluated once here, at the
boundary, and no caller can bypass it. A conversation-row lock serializes seq allocation; the turn
id is the DBOS workflow id, so a re-enqueue is idempotent. When an idempotency key is given, a
redelivery of the same message joins the turn already admitted for it instead of spawning a second.

Delivery is derived here too: a turn entering a conversation whose surface is durable registers a
writeback row atomically with its turn row, so the poller delivers the reply no matter who admitted
it — a surface ingest, a scheduled fire, or an extension invoke. A live surface's conversations
register nothing; their members tail the hub.

A pause records its originating conversation sequence and the one turn accepted to resume it.
Member admission and the timer choose that turn under the conversation lock; a queued timer turn
can become the member's turn instead of producing two resumptions. The row stays as durable
recovery until the worker claims that turn, so a crash or ambiguous enqueue response retries the
same identity. Internal invocation never consumes a member's pause.

The inbound spend decision routes the turn before it is enqueued: allow queues it; a breached cap
either parks it (held, not enqueued — the resume job re-admits it when the cap is raised) or, when
the cap rejects, commits it cancelled with the reason, so a client's wait ends in-surface either
way."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from ufo.accounting import SpendEvaluator
from ufo.db import workspace_tx
from ufo.o11y import log
from ufo.scheduling import ONE_TIME_SCHEDULE, ScheduledTask
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    PARKED,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    WRITEBACK_PENDING,
    TerminalFrame,
    TerminalStatus,
    TurnContext,
    TurnStatus,
    turn_id_for,
)

QUEUED: TurnStatus = "queued"
CANCELLED: TerminalStatus = "cancelled"


@dataclass(frozen=True)
class _PendingPause:
    workspace_id: UUID
    conversation_id: UUID


class _ScheduledInvocationSuperseded(Exception):
    pass


@dataclass(frozen=True)
class Admission:
    dbos: DBOSClient
    durable_surfaces: frozenset[str]

    async def admit_member(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
    ) -> UUID:
        return await self._admit(
            workspace_id,
            conversation_id,
            agent_id,
            body,
            idempotency_key,
            context,
            _PendingPause(workspace_id, conversation_id),
            None,
        )

    async def invoke(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
    ) -> UUID:
        return await self._admit(
            workspace_id,
            conversation_id,
            agent_id,
            body,
            idempotency_key,
            context,
            None,
            None,
        )

    async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask) -> UUID | None:
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be invoked")
        firing_key = f"{task.id}:{task.next_run_at.isoformat()}"
        try:
            return await self._admit(
                workspace_id,
                task.conversation_id,
                task.agent_id,
                task.prompt,
                firing_key,
                None,
                None,
                task,
            )
        except _ScheduledInvocationSuperseded:
            return None

    async def _admit(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None,
        context: TurnContext | None,
        pending_pause: _PendingPause | None,
        scheduled_task: ScheduledTask | None,
    ) -> UUID:
        dispatch_now = False
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id, tables.conversation.c.surface)
                    .where(tables.conversation.c.id == conversation_id)
                    .with_for_update()
                )
            ).one()
            resumed_turn_id: UUID | None = None
            if scheduled_task is not None:
                claimed = (
                    await connection.execute(
                        sa.select(
                            tables.scheduled_task.c.id,
                            tables.scheduled_task.c.resume_turn_id,
                        )
                        .where(
                            tables.scheduled_task.c.workspace_id == workspace_id,
                            tables.scheduled_task.c.id == scheduled_task.id,
                            tables.scheduled_task.c.conversation_id == conversation_id,
                            tables.scheduled_task.c.agent_id == agent_id,
                            tables.scheduled_task.c.schedule == scheduled_task.schedule,
                            tables.scheduled_task.c.claimed_by == scheduled_task.claim_id,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if claimed is None:
                    raise _ScheduledInvocationSuperseded
                resumed_turn_id = (
                    claimed.resume_turn_id if scheduled_task.schedule == ONE_TIME_SCHEDULE else None
                )
            deduped = None
            if resumed_turn_id is not None:
                deduped = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.status,
                            tables.turn.c.seq,
                        )
                        .where(
                            tables.turn.c.id == resumed_turn_id,
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.agent_id == agent_id,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if deduped is None:
                    raise RuntimeError("scheduled pause references no matching turn")
            elif idempotency_key is not None:
                deduped = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.status,
                            tables.turn.c.seq,
                            tables.turn.c.conversation_id,
                            tables.turn.c.agent_id,
                        )
                        .where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if deduped is not None:
                    if deduped.conversation_id != conversation_id or deduped.agent_id != agent_id:
                        raise RuntimeError("idempotency key reused for a different turn")
            if deduped is None and pending_pause is not None:
                later_turn = tables.turn.alias("later_turn")
                timer_turn = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.status,
                            tables.turn.c.seq,
                            tables.turn.c.idempotency_key,
                            tables.scheduled_task.c.id.label("pause_id"),
                            tables.scheduled_task.c.next_run_at.label("pause_due_at"),
                        )
                        .select_from(
                            tables.scheduled_task.join(
                                tables.turn,
                                tables.turn.c.id == tables.scheduled_task.c.resume_turn_id,
                            )
                        )
                        .where(
                            tables.scheduled_task.c.workspace_id == workspace_id,
                            tables.scheduled_task.c.conversation_id == conversation_id,
                            tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE,
                            tables.turn.c.status == QUEUED,
                            ~sa.exists(
                                sa.select(later_turn.c.id).where(
                                    later_turn.c.workspace_id == workspace_id,
                                    later_turn.c.conversation_id == conversation_id,
                                    later_turn.c.seq > tables.turn.c.seq,
                                )
                            ),
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if timer_turn is not None:
                    timer_key = f"{timer_turn.pause_id}:{timer_turn.pause_due_at.isoformat()}"
                    if timer_turn.idempotency_key == timer_key:
                        taken_over = await connection.execute(
                            sa.update(tables.turn)
                            .values(
                                inbound=body,
                                admission_source=MEMBER_ADMISSION,
                                context=None
                                if context is None
                                else context.model_dump(mode="json"),
                                idempotency_key=idempotency_key,
                                created_at=sa.func.now(),
                                updated_at=sa.func.now(),
                            )
                            .where(
                                tables.turn.c.id == timer_turn.id,
                                tables.turn.c.status == QUEUED,
                            )
                        )
                        if taken_over.rowcount == 1:
                            await connection.execute(
                                sa.update(tables.scheduled_task)
                                .values(
                                    next_run_at=sa.func.now(),
                                    claimed_by=None,
                                    claim_expires_at=None,
                                    updated_at=sa.func.now(),
                                )
                                .where(tables.scheduled_task.c.id == timer_turn.pause_id)
                            )
                            deduped = timer_turn
            if deduped is not None:
                turn_id = deduped.id
                turn_seq = deduped.seq
                retry_enqueue = deduped.status == QUEUED
                if not retry_enqueue:
                    if (
                        scheduled_task is not None
                        and scheduled_task.schedule == ONE_TIME_SCHEDULE
                        and deduped.status != PARKED
                    ):
                        await connection.execute(
                            sa.delete(tables.scheduled_task).where(
                                tables.scheduled_task.c.workspace_id == workspace_id,
                                tables.scheduled_task.c.id == scheduled_task.id,
                                tables.scheduled_task.c.resume_turn_id == turn_id,
                            )
                        )
                    if pending_pause is not None:
                        await connection.execute(
                            sa.delete(tables.scheduled_task).where(
                                tables.scheduled_task.c.workspace_id == workspace_id,
                                tables.scheduled_task.c.resume_turn_id == turn_id,
                            )
                        )
                    return turn_id
                status = QUEUED
            if deduped is None:
                seq = (
                    await connection.execute(
                        sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                            tables.turn.c.conversation_id == conversation_id
                        )
                    )
                ).scalar_one()
                turn_id = turn_id_for(workspace_id, conversation_id, seq)
                turn_seq = seq
                decision = await SpendEvaluator(
                    workspace_id, conversation.member_id, agent_id
                ).decide(connection, 0)
                match decision.outcome:
                    case "allow":
                        status, terminal = QUEUED, None
                    case "park":
                        status, terminal = PARKED, None
                    case _:
                        status = CANCELLED
                        terminal = TerminalFrame(status=CANCELLED, text=decision.message)
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=seq,
                        status=status,
                        inbound=body,
                        admission_source=(
                            MEMBER_ADMISSION if pending_pause is not None else INTERNAL_ADMISSION
                        ),
                        context=None if context is None else context.model_dump(mode="json"),
                        terminal=None if terminal is None else terminal.model_dump(mode="json"),
                        idempotency_key=idempotency_key,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
                if conversation.surface in self.durable_surfaces:
                    await connection.execute(
                        sa.insert(tables.writeback).values(
                            turn_id=turn_id,
                            workspace_id=workspace_id,
                            status=WRITEBACK_PENDING,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                    )
            if (
                scheduled_task is not None
                and scheduled_task.schedule == ONE_TIME_SCHEDULE
                and resumed_turn_id is None
            ):
                linked = await connection.execute(
                    sa.update(tables.scheduled_task)
                    .values(resume_turn_id=turn_id, updated_at=sa.func.now())
                    .where(
                        tables.scheduled_task.c.workspace_id == workspace_id,
                        tables.scheduled_task.c.id == scheduled_task.id,
                        tables.scheduled_task.c.claimed_by == scheduled_task.claim_id,
                        tables.scheduled_task.c.resume_turn_id.is_(None),
                    )
                )
                if linked.rowcount == 0:
                    raise _ScheduledInvocationSuperseded
                if status not in (QUEUED, PARKED):
                    await connection.execute(
                        sa.delete(tables.scheduled_task).where(
                            tables.scheduled_task.c.workspace_id == workspace_id,
                            tables.scheduled_task.c.id == scheduled_task.id,
                            tables.scheduled_task.c.resume_turn_id == turn_id,
                        )
                    )
            if pending_pause is not None:
                await connection.execute(
                    sa.update(tables.scheduled_task)
                    .values(
                        resume_turn_id=turn_id,
                        next_run_at=sa.func.now(),
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=sa.func.now(),
                    )
                    .where(
                        tables.scheduled_task.c.workspace_id == pending_pause.workspace_id,
                        tables.scheduled_task.c.conversation_id == pending_pause.conversation_id,
                        tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE,
                        tables.scheduled_task.c.origin_seq < turn_seq,
                        tables.scheduled_task.c.resume_turn_id.is_(None),
                    )
                )
                if status != QUEUED:
                    await connection.execute(
                        sa.delete(tables.scheduled_task).where(
                            tables.scheduled_task.c.workspace_id == workspace_id,
                            tables.scheduled_task.c.resume_turn_id == turn_id,
                        )
                    )
            if status == QUEUED:
                earlier_turn = tables.turn.alias("earlier_turn")
                earlier_queued = (
                    await connection.execute(
                        sa.select(
                            sa.exists(
                                sa.select(earlier_turn.c.id).where(
                                    earlier_turn.c.workspace_id == workspace_id,
                                    earlier_turn.c.conversation_id == conversation_id,
                                    earlier_turn.c.status == QUEUED,
                                    earlier_turn.c.seq < turn_seq,
                                )
                            )
                        )
                    )
                ).scalar_one()
                dispatch_now = not earlier_queued
                if dispatch_now:
                    await connection.execute(
                        sa.update(tables.turn)
                        .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                        .where(tables.turn.c.id == turn_id, tables.turn.c.status == QUEUED)
                    )
        if status != QUEUED:
            return turn_id
        if dispatch_now:
            await self._enqueue(workspace_id, conversation_id, turn_id)
        return turn_id

    async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "queue_partition_key": str(conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        try:
            await self.dbos.enqueue_async(options, str(workspace_id), str(turn_id))
        except asyncio.CancelledError:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == QUEUED)
                )
            raise
        except Exception as error:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == QUEUED)
                )
            log(
                "turn.enqueue_deferred",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )


@dataclass(frozen=True)
class AdmissionInvoker:
    """Internal turn invocation bound to one workspace. Jobs and extension workflows receive only
    this capability, so they cannot consume a member's pending pause."""

    admission: Admission
    workspace_id: UUID

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
    ) -> UUID:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key=idempotency_key,
            context=context,
        )

    async def invoke_scheduled(self, task: ScheduledTask) -> UUID | None:
        return await self.admission.invoke_scheduled(self.workspace_id, task)


@dataclass(frozen=True)
class MemberAdmission:
    """Member turn admission bound to one workspace. Surfaces receive only this capability, so
    every admitted member message consumes the pending one-time pause."""

    admission: Admission
    workspace_id: UUID

    async def admit(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
    ) -> UUID:
        return await self.admission.admit_member(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key=idempotency_key,
            context=context,
        )
