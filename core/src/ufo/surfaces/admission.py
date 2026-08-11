"""Shared turn admission: the one producer every surface, job, and extension-invoked turn calls to
place an inbound message on the durable turn queue — so the spend cap is evaluated once here, at the
boundary, and no caller can bypass it. A conversation-row lock serializes seq allocation; the turn
id is the DBOS workflow id, so a re-enqueue is idempotent. When an idempotency key is given, a
redelivery of the same message joins the turn already admitted for it instead of spawning a second.

Every turn executes as the conversation's bound agent: the member-admit path never names an
agent, and a caller holding a stored binding (an extension invoke, a scheduled fire) passes it as
an assertion that refuses on mismatch — a turn can never switch its conversation's agent.

A message arriving while the conversation's newest turn is still live — queued, running, or
parked — lands on the conversation's `inbound_message` queue instead of spawning a turn of its
own, whoever spoke it. The engine drains that queue into the live
turn at each round boundary as separate <context>-tagged messages, and the terminal commit
refuses to close over a non-empty queue, so one reply answers everything that arrived. Each
queue row carries its own idempotency key, so a redelivery joins the turn that consumed it.
Member admission returns which of the two it did, so a surface's per-turn side channel starts once
per run rather than once per delivery.

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
way. The seat gate runs first in the same commit: a speaking member without a seat — or a
scheduled fire into a seatless member's conversation — commits cancelled with the refusal, an
unseated speaker's message never folds into a live turn, and under a seat limit a member-surface
message whose speaker never resolved to a member is refused rather than answered as a ghost."""

import asyncio
from dataclasses import dataclass
from datetime import UTC
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from ufo.accounting import ALLOW, SpendEvaluator
from ufo.db import workspace_tx
from ufo.ext.surface import Admitted
from ufo.o11y import log
from ufo.scheduling import ONE_TIME_SCHEDULE, ScheduledTask, firing_key
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    SCHEDULED_ADMISSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    WRITEBACK_PENDING,
    TerminalFrame,
    TerminalStatus,
    ToolIntent,
    TurnContext,
    TurnStatus,
    turn_id_for,
)
from ufo.seats import SEAT_REFUSAL_MESSAGE, UNRESOLVED_SPEAKER_MESSAGE, Seats, gate_member

QUEUED: TurnStatus = "queued"
CANCELLED: TerminalStatus = "cancelled"


@dataclass(frozen=True)
class _PendingPause:
    workspace_id: UUID
    conversation_id: UUID


class _ScheduledInvocationSuperseded(Exception):
    pass


def _refused(
    holds_work_already_done: bool, message: str
) -> tuple[TurnStatus, TerminalFrame | None]:
    """What a refusal does to a turn the workspace has already paid for. A member's next message can
    be turned away and they can read why and decide what to do; a turn carrying a finished
    subagent's result holds work the ledger has already booked, and cancelling it discards that
    output with no one to tell — the member paid for the run and would simply never hear it. So the
    refusal holds the turn instead, and the dispatcher's own re-decision releases it when the cap
    is raised or the window rolls."""
    if holds_work_already_done:
        return PARKED, None
    return CANCELLED, TerminalFrame(status=CANCELLED, text=message)


@dataclass(frozen=True)
class Admission:
    dbos: DBOSClient
    durable_surfaces: frozenset[str]

    async def admit_member(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        body: str,
        speaker_member_id: UUID | None,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        intent: ToolIntent | None = None,
    ) -> Admitted:
        if intent is not None and speaker_member_id is None:
            raise ValueError("a prepared intent requires a speaking member")
        if intent is not None and body != intent.model_dump_json():
            raise ValueError("body and intent disagree — the envelope is the turn's inbound")
        return await self._admit(
            workspace_id,
            conversation_id,
            None,
            body,
            speaker_member_id,
            idempotency_key,
            context,
            _PendingPause(workspace_id, conversation_id),
            None,
            intent=intent,
        )

    async def invoke(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        on_behalf_of_member_id: UUID | None = None,
        holds_work_already_done: bool = False,
    ) -> UUID:
        """Admit an internal turn. `on_behalf_of_member_id` carries forward the authority the work
        already held — a subagent hands its result back to the conversation that delegated it, and
        a turn woken to read that result must not be able to do less than the turn that spawned it,
        or the shortfall surfaces later as a refusal no member can place."""
        admitted = await self._admit(
            workspace_id,
            conversation_id,
            agent_id,
            body,
            None,
            idempotency_key,
            context,
            None,
            None,
            on_behalf_of_member_id,
            holds_work_already_done=holds_work_already_done,
        )
        return admitted.turn_id

    async def invoke_scheduled(
        self,
        workspace_id: UUID,
        task: ScheduledTask,
        runtime_instruction: str | None = None,
    ) -> UUID | None:
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be invoked")
        if task.schedule == ONE_TIME_SCHEDULE and runtime_instruction is not None:
            raise ValueError("a one-time workflow pause cannot carry a runtime instruction")
        scheduled_fire_iso = task.next_run_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        fire_key = firing_key(task.id, task.next_run_at)
        inbound = task.prompt
        if task.schedule != ONE_TIME_SCHEDULE:
            inbound = (
                "<scheduled_task>\n"
                f"scheduled_fire: {scheduled_fire_iso}\n"
                "</scheduled_task>\n"
                f"{task.prompt}"
            )
            if runtime_instruction is not None:
                inbound += (
                    "\n<scheduled_task_instruction>\n"
                    f"{runtime_instruction}\n"
                    "</scheduled_task_instruction>"
                )
        try:
            admitted = await self._admit(
                workspace_id,
                task.conversation_id,
                task.agent_id,
                inbound,
                None,
                fire_key,
                None,
                None,
                task,
                task.created_by_member_id,
            )
        except _ScheduledInvocationSuperseded:
            return None
        return admitted.turn_id

    async def _admit(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        asserted_agent_id: UUID | None,
        body: str,
        speaker_member_id: UUID | None,
        idempotency_key: str | None,
        context: TurnContext | None,
        pending_pause: _PendingPause | None,
        scheduled_task: ScheduledTask | None,
        on_behalf_of_member_id: UUID | None = None,
        intent: ToolIntent | None = None,
        holds_work_already_done: bool = False,
    ) -> Admitted:
        dispatch_now = False
        opened_run = False
        folded_parked_turn: UUID | None = None
        redispatch_workflow_id: str | None = None
        admitted_at = None
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.member_id,
                        tables.conversation.c.surface,
                        tables.conversation.c.agent_id,
                    )
                    .where(tables.conversation.c.id == conversation_id)
                    .with_for_update()
                )
            ).one()
            if asserted_agent_id is not None and asserted_agent_id != conversation.agent_id:
                raise ValueError("conversation is bound to another agent")
            agent_id = conversation.agent_id
            if speaker_member_id is not None:
                speaker = (
                    await connection.execute(
                        sa.select(tables.member.c.id).where(
                            tables.member.c.id == speaker_member_id,
                            tables.member.c.workspace_id == workspace_id,
                        )
                    )
                ).one_or_none()
                if speaker is None:
                    raise ValueError("turn speaker is not a member of this workspace")
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
                            tables.turn.c.running_attempt,
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
                            tables.turn.c.running_attempt,
                        )
                        .where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if deduped is None:
                    queued_message = (
                        await connection.execute(
                            sa.select(
                                tables.inbound_message.c.id,
                                tables.inbound_message.c.conversation_id,
                                tables.inbound_message.c.admitted_turn_id,
                                tables.inbound_message.c.consumed_turn_id,
                                tables.inbound_message.c.body,
                                tables.inbound_message.c.context,
                                tables.inbound_message.c.speaker_member_id,
                                tables.inbound_message.c.created_at,
                            ).where(
                                tables.inbound_message.c.workspace_id == workspace_id,
                                tables.inbound_message.c.idempotency_key == idempotency_key,
                            )
                        )
                    ).one_or_none()
                    if queued_message is not None:
                        if queued_message.conversation_id != conversation_id:
                            raise RuntimeError("idempotency key reused for a different turn")
                        if queued_message.consumed_turn_id is not None:
                            return Admitted(queued_message.consumed_turn_id, opened_run=False)
                        target_live = (
                            await connection.execute(
                                sa.select(tables.turn.c.status.in_(NON_TERMINAL_STATUSES)).where(
                                    tables.turn.c.id == queued_message.admitted_turn_id
                                )
                            )
                        ).scalar_one()
                        if target_live:
                            return Admitted(queued_message.admitted_turn_id, opened_run=False)
                        await connection.execute(
                            sa.delete(tables.inbound_message).where(
                                tables.inbound_message.c.id == queued_message.id
                            )
                        )
                        body = queued_message.body
                        context = (
                            None
                            if queued_message.context is None
                            else TurnContext.model_validate(queued_message.context)
                        )
                        speaker_member_id = queued_message.speaker_member_id
                        admitted_at = queued_message.created_at
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
                            tables.turn.c.running_attempt,
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
                    timer_key = firing_key(timer_turn.pause_id, timer_turn.pause_due_at)
                    if timer_turn.idempotency_key == timer_key and (
                        await Seats(workspace_id).admits(connection, speaker_member_id)
                        if speaker_member_id is not None
                        else not await Seats(workspace_id).gated(connection)
                    ):
                        taken_over = await connection.execute(
                            sa.update(tables.turn)
                            .values(
                                inbound=body,
                                admission_source=MEMBER_ADMISSION,
                                speaker_member_id=speaker_member_id,
                                on_behalf_of_member_id=None,
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
                            opened_run = True
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
            if deduped is None and scheduled_task is None and intent is None:
                live_turn = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.seq,
                            tables.turn.c.status,
                            tables.turn.c.speaker_member_id,
                            tables.turn.c.admission_source,
                            tables.turn.c.on_behalf_of_member_id,
                        )
                        .where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                        )
                        .order_by(tables.turn.c.seq)
                        .limit(1)
                        .with_for_update()
                    )
                ).one_or_none()
                parked_gate = (
                    None
                    if live_turn is None or live_turn.status != PARKED
                    else gate_member(
                        live_turn.speaker_member_id,
                        live_turn.admission_source,
                        live_turn.on_behalf_of_member_id,
                    )
                )
                parked_members = {parked_gate} if parked_gate is not None else set()
                if live_turn is not None and live_turn.status == PARKED:
                    parked_members.update(
                        (
                            await connection.execute(
                                sa.select(tables.inbound_message.c.speaker_member_id)
                                .where(
                                    tables.inbound_message.c.workspace_id == workspace_id,
                                    tables.inbound_message.c.conversation_id == conversation_id,
                                    tables.inbound_message.c.consumed_turn_id.is_(None),
                                    tables.inbound_message.c.speaker_member_id.is_not(None),
                                )
                                .distinct()
                            )
                        )
                        .scalars()
                        .all()
                    )
                parked_seated = True
                seats = Seats(workspace_id)
                for parked_member in parked_members:
                    if parked_member is not None and not await seats.admits(
                        connection, parked_member
                    ):
                        parked_seated = False
                        break
                fold_admitted = (
                    live_turn is not None
                    and (
                        await seats.admits(connection, speaker_member_id)
                        if speaker_member_id is not None
                        else pending_pause is None or not await seats.gated(connection)
                    )
                    and parked_seated
                )
                fold_decision = (
                    None
                    if not fold_admitted
                    else await SpendEvaluator(
                        workspace_id, conversation.member_id, agent_id
                    ).decide(connection, 0)
                )
                if (
                    live_turn is not None
                    and fold_decision is not None
                    and fold_decision.outcome == ALLOW
                ):
                    message_seq = (
                        await connection.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.max(tables.inbound_message.c.seq), 0) + 1
                            ).where(tables.inbound_message.c.conversation_id == conversation_id)
                        )
                    ).scalar_one()
                    await connection.execute(
                        sa.insert(tables.inbound_message).values(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            seq=message_seq,
                            body=body,
                            admission_source=(
                                MEMBER_ADMISSION
                                if pending_pause is not None
                                else INTERNAL_ADMISSION
                            ),
                            context=None if context is None else context.model_dump(mode="json"),
                            speaker_member_id=speaker_member_id,
                            idempotency_key=idempotency_key,
                            admitted_turn_id=live_turn.id,
                            created_at=admitted_at if admitted_at is not None else sa.func.now(),
                        )
                    )
                    if pending_pause is not None:
                        await connection.execute(
                            sa.update(tables.scheduled_task)
                            .values(
                                resume_turn_id=live_turn.id,
                                next_run_at=sa.func.now(),
                                claimed_by=None,
                                claim_expires_at=None,
                                updated_at=sa.func.now(),
                            )
                            .where(
                                tables.scheduled_task.c.workspace_id == workspace_id,
                                tables.scheduled_task.c.conversation_id == conversation_id,
                                tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE,
                                tables.scheduled_task.c.origin_seq <= live_turn.seq,
                                tables.scheduled_task.c.resume_turn_id.is_(None),
                            )
                        )
                    if live_turn.status != PARKED:
                        return Admitted(live_turn.id, opened_run=False)
                    await connection.execute(
                        sa.update(tables.turn)
                        .values(
                            status=QUEUED,
                            dispatch_enqueued_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                        .where(tables.turn.c.id == live_turn.id, tables.turn.c.status == PARKED)
                    )
                    folded_parked_turn = live_turn.id
            if deduped is not None:
                turn_id = deduped.id
                turn_seq = deduped.seq
                retry_enqueue = deduped.status == QUEUED
                if retry_enqueue and deduped.running_attempt is not None:
                    redispatch_workflow_id = uuid4().hex
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
                    return Admitted(turn_id, opened_run=False)
                status = QUEUED
            if deduped is None and folded_parked_turn is None:
                seq = (
                    await connection.execute(
                        sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                            tables.turn.c.conversation_id == conversation_id
                        )
                    )
                ).scalar_one()
                turn_id = turn_id_for(workspace_id, conversation_id, seq)
                turn_seq = seq
                opened_run = True
                admission_source = (
                    INTENT_ADMISSION
                    if intent is not None
                    else MEMBER_ADMISSION
                    if pending_pause is not None
                    else SCHEDULED_ADMISSION
                    if scheduled_task is not None
                    else INTERNAL_ADMISSION
                )
                gate = gate_member(speaker_member_id, admission_source, on_behalf_of_member_id)
                terminal: TerminalFrame | None
                if (
                    gate is None
                    and pending_pause is not None
                    and await Seats(workspace_id).gated(connection)
                ):
                    status, terminal = (
                        CANCELLED,
                        TerminalFrame(status=CANCELLED, text=UNRESOLVED_SPEAKER_MESSAGE),
                    )
                elif gate is not None and not await Seats(workspace_id).admits(connection, gate):
                    status, terminal = _refused(holds_work_already_done, SEAT_REFUSAL_MESSAGE)
                else:
                    decision = await SpendEvaluator(
                        workspace_id, conversation.member_id, agent_id
                    ).decide(connection, 0)
                    match decision.outcome:
                        case "allow":
                            status, terminal = QUEUED, None
                        case "park":
                            status, terminal = PARKED, None
                        case _:
                            status, terminal = _refused(holds_work_already_done, decision.message)
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=seq,
                        status=status,
                        inbound=body,
                        admission_source=admission_source,
                        speaker_member_id=speaker_member_id,
                        on_behalf_of_member_id=on_behalf_of_member_id,
                        context=None if context is None else context.model_dump(mode="json"),
                        terminal=None if terminal is None else terminal.model_dump(mode="json"),
                        idempotency_key=idempotency_key,
                        created_at=admitted_at if admitted_at is not None else sa.func.now(),
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
            if pending_pause is not None and folded_parked_turn is None:
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
            if folded_parked_turn is None and status == QUEUED:
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
        if folded_parked_turn is not None:
            await self._enqueue(
                workspace_id, conversation_id, folded_parked_turn, workflow_id=uuid4().hex
            )
            return Admitted(folded_parked_turn, opened_run=True)
        if status != QUEUED:
            return Admitted(turn_id, opened_run=False)
        if dispatch_now:
            await self._enqueue(
                workspace_id, conversation_id, turn_id, workflow_id=redispatch_workflow_id
            )
        return Admitted(turn_id, opened_run=opened_run)

    async def _enqueue(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        workflow_id: str | None = None,
    ) -> None:
        """Place the turn on the DBOS queue. A never-claimed queued turn rides its own id, so a
        re-enqueue is idempotent; a turn that has ever been claimed — a parked turn resumed by a
        fold, or its later redispatch — rides a fresh id, because the run that claimed it consumed
        its own and DBOS would drop a duplicate as complete. The worker's claim keeps a duplicate
        fresh-id offer safe."""
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": workflow_id if workflow_id is not None else str(turn_id),
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
        on_behalf_of_member_id: UUID | None = None,
        holds_work_already_done: bool = False,
    ) -> UUID:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key=idempotency_key,
            context=context,
            on_behalf_of_member_id=on_behalf_of_member_id,
            holds_work_already_done=holds_work_already_done,
        )

    async def invoke_scheduled(
        self, task: ScheduledTask, runtime_instruction: str | None = None
    ) -> UUID | None:
        return await self.admission.invoke_scheduled(self.workspace_id, task, runtime_instruction)


@dataclass(frozen=True)
class MemberAdmission:
    """Member turn admission bound to one workspace. Surfaces receive only this capability, so
    every admitted member message consumes the pending one-time pause."""

    admission: Admission
    workspace_id: UUID

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
        intent: ToolIntent | None = None,
    ) -> Admitted:
        return await self.admission.admit_member(
            self.workspace_id,
            conversation_id,
            message,
            speaker_member_id,
            idempotency_key=idempotency_key,
            context=context,
            intent=intent,
        )
