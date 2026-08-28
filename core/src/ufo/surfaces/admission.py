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
own, whoever spoke it. Each fold is logged against the turn it landed on. The engine drains that
queue into the live turn at each round boundary as separate <context>-tagged messages, and the
terminal commit refuses to close over a non-empty queue, so no reply closes over an unread message.
The running turn can answer a folded message before it ends, by marking that answer in its own
output; the closing reply then carries what it has not already sent. Each
queue row carries its own idempotency key, so a redelivery joins the turn that consumed it.
Member admission returns which of the two it did, so a surface's per-turn side channel starts once
per run rather than once per delivery, and it names the queue row a fold landed on — the id the
turn's `Absorbed` frame carries when the drain takes that row up, so a surface already tailing the
turn can tell a message waiting for it from one that founded a turn of its own.

Delivery is derived here too: a turn entering a conversation whose surface is durable registers a
writeback row atomically with its turn row, so the poller delivers the reply no matter who admitted
it — a surface ingest, a scheduled fire, or an extension invoke. A live surface's conversations
register nothing; their members tail the hub.

A caller waiting on a member asks that question here rather than keeping a row of its own: an
invoke carrying the two member watermarks — one per sequence a member message can take, its own turn
or the live turn's arrival queue — is refused under the same conversation lock when a member has
spoken since the wait began, so a timer's fire and the member's own reply can never both resume one
wait.

The inbound spend decision routes the turn before it is enqueued: allow queues it; a breached cap
either parks it (held, not enqueued — the resume job re-admits it when the cap is raised) or, when
the cap rejects, commits it cancelled with the reason, so a client's wait ends in-surface either
way. The seat gate runs first in the same commit: a speaking member without a seat — or a
scheduled fire into a seatless member's conversation — commits cancelled with the refusal, an
unseated speaker's message never folds into a live turn, and a member-surface message whose
speaker never resolved to a member is refused rather than answered as a ghost — unconditionally,
because every member surface resolves its speaker, so one that did not is a stranger."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from opentelemetry.trace import SpanKind
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.billing.accounting import ALLOW, BalanceGate, SpendDecision, SpendEvaluator
from ufo.db import workspace_tx
from ufo.ext.context import AgentArchived
from ufo.ext.surface import Admitted, conversation_name
from ufo.hub import ArrivalQueued, Hub, Reply
from ufo.o11y import current_traceparent, emit_metric, log, span
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    DELIVERY_PENDING,
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    SCHEDULED_ADMISSION,
    SUBAGENT_SURFACE,
    SURFACE_COMMENT_ROUND_INDEX,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    WRITEBACK_PENDING,
    TerminalFrame,
    TerminalStatus,
    ToolIntent,
    TurnAdmissionSource,
    TurnContext,
    TurnStatus,
    admits_spent_balance,
    mid_turn_reply_id_for,
    turn_id_for,
)
from ufo.seats import SEAT_REFUSAL_MESSAGE, UNRESOLVED_SPEAKER_MESSAGE, Seats, gate_member
from ufo.workspace import ws_current

QUEUED: TurnStatus = "queued"
CANCELLED: TerminalStatus = "cancelled"
ADMITTED_TURN_METRIC = "admitted_turn_total"


class _SupersededByMember(Exception):
    pass


ARCHIVED_REFUSAL_MESSAGE = "This app is archived. Restore it from Applications to use it again."


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
    hub: Hub | None = None
    key_slot_for: Callable[[str], str | None] | None = None
    billing_url: str | None = None

    async def admit_member(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        body: str,
        speaker_member_id: UUID | None,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        intent: ToolIntent | None = None,
        comment: str | None = None,
    ) -> Admitted:
        if intent is not None and speaker_member_id is None:
            raise ValueError("a prepared intent requires a speaking member")
        if intent is not None and body != intent.model_dump_json():
            raise ValueError("body and intent disagree — the envelope is the turn's inbound")
        if comment == "":
            raise ValueError("a surface comment cannot be empty")
        with span("admission", kind=SpanKind.SERVER):
            admitted = await self._admit(
                workspace_id,
                conversation_id,
                None,
                body,
                speaker_member_id,
                idempotency_key,
                context,
                member_admission=True,
                intent=intent,
                comment=comment,
            )
        if admitted.arrival_id is not None and not admitted.opened_run and self.hub is not None:
            await self.hub.publish(admitted.turn_id, ArrivalQueued(arrival_id=admitted.arrival_id))
        if admitted.comment_id is not None and comment is not None and self.hub is not None:
            await self.hub.publish(
                admitted.turn_id,
                Reply(
                    id=admitted.comment_id,
                    message_ref=admitted.arrival_id or admitted.turn_id,
                    text=comment,
                    is_comment=True,
                ),
            )
        return admitted

    async def redispatch(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> tuple[UUID, UUID] | None:
        """Give a member message a cancelled turn left unconsumed its own run, now. The oldest
        member-spoken pending arrival is re-admitted under its delivery key — the same admission a
        resend rides, so a dead target founds a new turn on the message and a live one folds it —
        and later pending rows join that turn's first drain. Returns `(turn_id, arrival_id)` when
        this call founded a run on the arrival, None when nothing was pending or the message joined
        an existing turn. A pending row without a key is stamped one first, so every path through
        here is a re-admission and none can say a message twice."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.inbound_message.c.id,
                        tables.inbound_message.c.body,
                        tables.inbound_message.c.context,
                        tables.inbound_message.c.speaker_member_id,
                        tables.inbound_message.c.idempotency_key,
                    )
                    .where(
                        tables.inbound_message.c.workspace_id == workspace_id,
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                        tables.inbound_message.c.speaker_member_id.is_not(None),
                    )
                    .order_by(tables.inbound_message.c.seq)
                    .limit(1)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return None
            idempotency_key = row.idempotency_key
            if idempotency_key is None:
                idempotency_key = f"redispatch:{row.id}"
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(idempotency_key=idempotency_key)
                    .where(tables.inbound_message.c.id == row.id)
                )
        admitted = await self.admit_member(
            workspace_id,
            conversation_id,
            row.body,
            row.speaker_member_id,
            idempotency_key=idempotency_key,
            context=None if row.context is None else TurnContext.model_validate(row.context),
        )
        return (admitted.turn_id, row.id) if admitted.opened_run else None

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
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
    ) -> UUID | None:
        """Admit an internal turn. `on_behalf_of_member_id` carries forward the authority the work
        already held — a subagent hands its result back to the conversation that delegated it, and
        a turn woken to read that result must not be able to do less than the turn that spawned it,
        or the shortfall surfaces later as a refusal no member can place.

        A turn founded on a spawned conversation inherits that conversation's spawn identity from
        its founding turn — parent linkage, profile, display name, and whether it delivers — so a
        child woken by its own grandchild's result continues under the same contract and its
        answer still reaches the conversation that spawned it, never a contractless turn whose
        work reaches nobody.

        `as_scheduled` gives the turn a scheduled fire's meaning without a scheduled row: it founds
        its own turn beside a live one instead of folding, and it is seat-gated on the member it
        acts for, so an unseated member's fire is refused wherever it lands.

        The two `unless_member_*` watermarks refuse the admission and answer None when a member has
        spoken since a caller began waiting on them — the question asked under admission's own lock,
        so the member who got there first wins the race. They arrive as a pair because a member
        message becomes one of two different things: it founds a turn when nothing is live, and
        otherwise lands on the live turn's arrival queue. Those two carry independent
        per-conversation sequences, so one cannot bound both: `unless_member_since` is a `turn.seq`
        and `unless_member_arrival_since` an `inbound_message.seq`, each compared only against its
        own space, and passing one without the other raises rather than silently leaving half the
        question unasked.

        A founded turn must be strictly past its watermark, since the turn that armed the wait is
        the origin rather than a reply to it. An arrival must be strictly past its own, which is
        what separates a message the agent absorbed BEFORE arming — history it had already read, and
        which would otherwise refuse every fire forever, because a pre-arm fold lands on the arming
        turn itself — from one that arrived after and genuinely ended the wait. Consumption does not
        enter it: the engine stamps an arrival the moment it drains, and a wait that ended must stay
        ended after that.

        A turn this gate itself refused does not count: it never joined the conversation and
        nothing will answer it, so an unseated member or a breached cap cannot end someone else's
        wait by arriving beside it. A message a hook denied at drain time does still count, because
        the wait ends where the message is admitted and this gate runs before any hook can see it.
        Nothing is written on the refusal, and a redelivery under an idempotency key that already
        admitted keeps answering its turn rather than flipping to the refusal.

        None means one thing: a member spoke past a watermark the caller passed, so the wait this
        invocation served is over and the caller's row is done. An archived agent raises
        `AgentArchived` instead, because its work is owed rather than over — the caller leaves its
        row alone and a restore runs it."""
        try:
            admitted = await self._admit(
                workspace_id,
                conversation_id,
                agent_id,
                body,
                None,
                idempotency_key,
                context,
                on_behalf_of_member_id=on_behalf_of_member_id,
                holds_work_already_done=holds_work_already_done,
                as_scheduled=as_scheduled,
                unless_member_since=unless_member_since,
                unless_member_arrival_since=unless_member_arrival_since,
            )
        except _SupersededByMember:
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
        on_behalf_of_member_id: UUID | None = None,
        member_admission: bool = False,
        intent: ToolIntent | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        comment: str | None = None,
    ) -> Admitted:
        if (unless_member_since is None) != (unless_member_arrival_since is None):
            raise ValueError("waiting on a member takes both watermarks, turn and arrival")
        dispatch_now = False
        opened_run = False
        counted_source: TurnAdmissionSource | None = None
        folded_parked_turn: UUID | None = None
        arrival_id: UUID | None = None
        redispatch_workflow_id: str | None = None
        admitted_at = None
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.member_id,
                        tables.conversation.c.surface,
                        tables.conversation.c.agent_id,
                        sa.select(tables.agent.c.archived_at)
                        .where(
                            tables.agent.c.workspace_id == workspace_id,
                            tables.agent.c.id == tables.conversation.c.agent_id,
                        )
                        .scalar_subquery()
                        .label("archived_at"),
                    )
                    .where(tables.conversation.c.id == conversation_id)
                    .with_for_update()
                )
            ).one()
            if asserted_agent_id is not None and asserted_agent_id != conversation.agent_id:
                raise ValueError("conversation is bound to another agent")
            agent_id = conversation.agent_id
            archived = conversation.archived_at is not None
            if archived and not member_admission:
                raise AgentArchived(ARCHIVED_REFUSAL_MESSAGE)
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
                if context is not None and context.timezone is not None:
                    await connection.execute(
                        sa.update(tables.member)
                        .where(
                            tables.member.c.workspace_id == workspace_id,
                            tables.member.c.id == speaker_member_id,
                        )
                        .values(timezone=context.timezone, updated_at=sa.func.now())
                    )
            deduped = None
            if idempotency_key is not None:
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
                            return await self._record_comment(
                                connection,
                                workspace_id,
                                Admitted(queued_message.consumed_turn_id, opened_run=False),
                                comment,
                                queued_message.id,
                            )
                        target_live = (
                            await connection.execute(
                                sa.select(tables.turn.c.status.in_(NON_TERMINAL_STATUSES)).where(
                                    tables.turn.c.id == queued_message.admitted_turn_id
                                )
                            )
                        ).scalar_one()
                        if target_live:
                            return await self._record_comment(
                                connection,
                                workspace_id,
                                Admitted(
                                    queued_message.admitted_turn_id,
                                    opened_run=False,
                                    arrival_id=queued_message.id,
                                ),
                                comment,
                            )
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
            if (
                deduped is None
                and unless_member_since is not None
                and unless_member_arrival_since is not None
            ):
                superseded = (
                    await connection.execute(
                        sa.select(
                            sa.exists(
                                sa.select(tables.turn.c.id).where(
                                    tables.turn.c.workspace_id == workspace_id,
                                    tables.turn.c.conversation_id == conversation_id,
                                    tables.turn.c.speaker_member_id.is_not(None),
                                    tables.turn.c.status != CANCELLED,
                                    tables.turn.c.seq > unless_member_since,
                                )
                            )
                            | sa.exists(
                                sa.select(tables.inbound_message.c.id).where(
                                    tables.inbound_message.c.workspace_id == workspace_id,
                                    tables.inbound_message.c.conversation_id == conversation_id,
                                    tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                                    tables.inbound_message.c.seq > unless_member_arrival_since,
                                )
                            )
                        )
                    )
                ).scalar_one()
                if superseded:
                    raise _SupersededByMember
            if deduped is None and not as_scheduled and intent is None:
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
                seats = Seats(workspace_id)
                parked_seated = await seats.all_seated(
                    connection, [member for member in parked_members if member is not None]
                )
                fold_admitted = (
                    live_turn is not None
                    and not archived
                    and (
                        await seats.admits(connection, speaker_member_id)
                        if speaker_member_id is not None
                        else not member_admission
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
                fold_balance = (
                    None
                    if fold_decision is None or fold_decision.outcome != ALLOW
                    else await BalanceGate(workspace_id, self.billing_url).admits(
                        connection, agent_id, self.key_slot_for
                    )
                )
                if (
                    live_turn is not None
                    and fold_balance is not None
                    and fold_balance.outcome == ALLOW
                ):
                    message_seq = (
                        await connection.execute(
                            sa.select(
                                sa.func.coalesce(sa.func.max(tables.inbound_message.c.seq), 0) + 1
                            ).where(tables.inbound_message.c.conversation_id == conversation_id)
                        )
                    ).scalar_one()
                    arrival_id = uuid4()
                    arrival_source = MEMBER_ADMISSION if member_admission else INTERNAL_ADMISSION
                    await connection.execute(
                        sa.insert(tables.inbound_message).values(
                            id=arrival_id,
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            seq=message_seq,
                            body=body,
                            admission_source=arrival_source,
                            context=None if context is None else context.model_dump(mode="json"),
                            speaker_member_id=speaker_member_id,
                            idempotency_key=idempotency_key,
                            admitted_turn_id=live_turn.id,
                            created_at=admitted_at if admitted_at is not None else sa.func.now(),
                        )
                    )
                    log(
                        "arrival.queued",
                        turn_id=str(live_turn.id),
                        arrival_id=str(arrival_id),
                        conversation_id=str(conversation_id),
                        admission_source=arrival_source,
                        turn_status=live_turn.status,
                    )
                    if live_turn.status != PARKED:
                        return await self._record_comment(
                            connection,
                            workspace_id,
                            Admitted(live_turn.id, opened_run=False, arrival_id=arrival_id),
                            comment,
                        )
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
                    return await self._record_comment(
                        connection,
                        workspace_id,
                        Admitted(turn_id, opened_run=False),
                        comment,
                    )
                status = QUEUED
            if deduped is None and folded_parked_turn is None:
                seq = (
                    await connection.execute(
                        sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                            tables.turn.c.conversation_id == conversation_id
                        )
                    )
                ).scalar_one()
                spawned_identity = None
                if conversation.surface == SUBAGENT_SURFACE and seq > 1:
                    spawned_identity = (
                        await connection.execute(
                            sa.select(
                                tables.turn.c.parent_turn_id,
                                tables.turn.c.subagent_profile,
                                tables.turn.c.subagent_name,
                                tables.turn.c.result_delivery,
                            ).where(
                                tables.turn.c.conversation_id == conversation_id,
                                tables.turn.c.seq == 1,
                            )
                        )
                    ).one()
                turn_id = turn_id_for(workspace_id, conversation_id, seq)
                turn_seq = seq
                opened_run = True
                admission_source = (
                    INTENT_ADMISSION
                    if intent is not None
                    else MEMBER_ADMISSION
                    if member_admission
                    else SCHEDULED_ADMISSION
                    if as_scheduled
                    else INTERNAL_ADMISSION
                )
                gate = gate_member(speaker_member_id, on_behalf_of_member_id)
                terminal: TerminalFrame | None
                if archived:
                    status, terminal = _refused(holds_work_already_done, ARCHIVED_REFUSAL_MESSAGE)
                elif gate is None and member_admission:
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
                    balance = (
                        SpendDecision(outcome=ALLOW, message="")
                        if intent is not None and admits_spent_balance(intent)
                        else await BalanceGate(workspace_id, self.billing_url).admits(
                            connection, agent_id, self.key_slot_for
                        )
                    )
                    match decision.outcome:
                        case _ if balance.outcome != ALLOW:
                            status, terminal = _refused(holds_work_already_done, balance.message)
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
                        parent_turn_id=(
                            None if spawned_identity is None else spawned_identity.parent_turn_id
                        ),
                        subagent_profile=(
                            None if spawned_identity is None else spawned_identity.subagent_profile
                        ),
                        subagent_name=(
                            None if spawned_identity is None else spawned_identity.subagent_name
                        ),
                        result_delivery=(
                            DELIVERY_PENDING
                            if spawned_identity is not None
                            and spawned_identity.result_delivery is not None
                            else None
                        ),
                        context=None if context is None else context.model_dump(mode="json"),
                        terminal=None if terminal is None else terminal.model_dump(mode="json"),
                        idempotency_key=idempotency_key,
                        traceparent=current_traceparent(),
                        created_at=admitted_at if admitted_at is not None else sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
                counted_source = admission_source
                await connection.execute(
                    sa.update(tables.conversation)
                    .where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.title.is_(None),
                    )
                    .values(title=conversation_name(body))
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
            admitted = await self._record_comment(
                connection,
                workspace_id,
                (
                    Admitted(folded_parked_turn, opened_run=True, arrival_id=arrival_id)
                    if folded_parked_turn is not None
                    else Admitted(turn_id, opened_run=False)
                    if status != QUEUED
                    else Admitted(turn_id, opened_run=opened_run)
                ),
                comment,
            )
        if counted_source is not None:
            # Counted off the committed row, never beside the insert: the statements after it and
            # the commit itself can fail, and the surface then retries under the same idempotency
            # key with nothing committed to dedupe against. One row is one turn, so the count
            # follows the row.
            emit_metric(
                ADMITTED_TURN_METRIC,
                surface=conversation.surface,
                admission_source=counted_source,
            )
        if folded_parked_turn is not None:
            await self._enqueue(
                workspace_id, conversation_id, folded_parked_turn, workflow_id=uuid4().hex
            )
            return admitted
        if status != QUEUED:
            return admitted
        if dispatch_now:
            await self._enqueue(
                workspace_id, conversation_id, turn_id, workflow_id=redispatch_workflow_id
            )
        return admitted

    async def _record_comment(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        admitted: Admitted,
        comment: str | None,
        message_ref: UUID | None = None,
    ) -> Admitted:
        if comment is None:
            return admitted
        reference = message_ref or admitted.arrival_id or admitted.turn_id
        comment_id = mid_turn_reply_id_for(
            admitted.turn_id,
            SURFACE_COMMENT_ROUND_INDEX,
            0,
            f"surface:{reference}",
        )
        insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
        written = await connection.execute(
            insert(tables.mid_turn_reply)
            .values(
                id=comment_id,
                workspace_id=workspace_id,
                turn_id=admitted.turn_id,
                round_index=SURFACE_COMMENT_ROUND_INDEX,
                span_index=0,
                message_ref=reference,
                text=comment,
                status=WRITEBACK_PENDING,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(index_elements=[tables.mid_turn_reply.c.id])
        )
        return (
            Admitted(
                turn_id=admitted.turn_id,
                opened_run=admitted.opened_run,
                arrival_id=admitted.arrival_id,
                comment_id=comment_id,
            )
            if written.rowcount == 1
            else admitted
        )

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
    this capability, so a turn they admit can never claim to have been spoken by a member."""

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
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
    ) -> UUID | None:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key=idempotency_key,
            context=context,
            on_behalf_of_member_id=on_behalf_of_member_id,
            holds_work_already_done=holds_work_already_done,
            as_scheduled=as_scheduled,
            unless_member_since=unless_member_since,
            unless_member_arrival_since=unless_member_arrival_since,
        )


@dataclass(frozen=True)
class MemberAdmission:
    """Member turn admission bound to one workspace. Surfaces receive only this capability, so
    every turn they admit is a member's message, gated on that member's seat."""

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
        comment: str | None = None,
    ) -> Admitted:
        return await self.admission.admit_member(
            self.workspace_id,
            conversation_id,
            message,
            speaker_member_id,
            idempotency_key=idempotency_key,
            context=context,
            intent=intent,
            comment=comment,
        )


@dataclass(frozen=True)
class ConnectResume:
    """The connect callback's write back into the conversation that asked for the account. The
    member left that conversation for a browser and the turn there cannot learn the grant landed,
    so the outcome is admitted as the granting member's own message: a turn still waiting folds it
    in and carries on, and one that already ended is founded again by it.

    The workspace is the one the callback bound before it opened the sealed state, so this reads it
    rather than carrying it — the same way `GrantStore` does, and for the same reason.

    A failure here is logged and swallowed, and answered as False. The grant is committed by the
    time this runs, so a member who completed consent is owed that answer whether or not the queue
    took the message — but the page that tells them must not also promise work that never started,
    so the outcome rides back rather than being assumed.

    The prepared-intent lane takes nothing. A connect begun from a portal panel seals that panel's
    own conversation, and that lane dispatches one typed verb per turn with no model round: a
    free-text message admitted there would run a whole turn nothing reads (the surface declares no
    writeback and the chat index skips the lane), while holding the lane's single partition until
    it ended — so the member's next panel submit would wait behind it and time out. Left alone, the
    grant still lands and the page tells them to ask for the work in the conversation they can
    actually read."""

    admission: Admission

    async def resume(
        self,
        conversation_id: UUID,
        message: str,
        *,
        speaker_member_id: UUID,
        idempotency_key: str,
    ) -> bool:
        async with workspace_tx() as connection:
            lane = (
                await connection.execute(
                    sa.select(tables.turn.c.admission_source)
                    .where(tables.turn.c.conversation_id == conversation_id)
                    .order_by(tables.turn.c.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if lane == INTENT_ADMISSION:
            log("connect.resume_declined", conversation_id=str(conversation_id), lane=lane)
            return False
        try:
            await self.admission.admit_member(
                ws_current().workspace_id,
                conversation_id,
                message,
                speaker_member_id,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            log(
                "connect.resume_failed",
                conversation_id=str(conversation_id),
                error_class=type(error).__name__,
            )
            return False
        return True
