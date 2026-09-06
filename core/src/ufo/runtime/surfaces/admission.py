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
way. A spent balance parks a member's message rather than refusing it, and writes the hold as a
reply the surface delivers: cancelled, the message is dead the moment the credit runs out, where the
same sweep resumes a parked one the minute an admin adds credit; parked in silence, the member
reads nothing. A member's later messages fold into that held turn and add no notice of their own,
so one thread holds one turn, one notice, and one answer once the credit lands. Only a member's
message is held: a prepared intent's panel has already read the refusal and moved on, and a
scheduled fire or an internal delivery re-fires on its own schedule — those are refused as
before. The hold is also the balance's alone: a cap that rejects or parks in the same breath
refuses as it always did, because credit does not lift a cap and the sweep would hold such a turn
past the one thing its notice promises. The seat gate runs first in the same commit: a speaking
member without a seat — or a scheduled fire into a seatless member's conversation — commits
cancelled with the refusal, an unseated speaker's message never folds into a live turn, and a
member-surface message whose speaker never resolved to a member is refused rather than answered as
a ghost — unconditionally, because every member surface resolves its speaker, so one that did not
is a stranger."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from opentelemetry.trace import SpanKind
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.o11y import current_traceparent, emit_metric, log, span
from ufo.runtime.authority import (
    ExecutionAuthority,
    authority_from_member_id,
    authority_member_id,
    turn_authority,
)
from ufo.runtime.billing.accounting import ALLOW, BalanceGate, SpendDecision, SpendEvaluator
from ufo.runtime.billing.balance import balance_park_message
from ufo.runtime.ext.context import AgentArchived, MemberReach
from ufo.runtime.ext.surface import Admitted, conversation_name
from ufo.runtime.hub import Absorbed, ArrivalQueued, Hub, Reply
from ufo.runtime.seats import SEAT_REFUSAL_MESSAGE, UNRESOLVED_SPEAKER_MESSAGE, Seats
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import (
    BALANCE_PARK_ROUND_INDEX,
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
    TURN_WORKFLOW_NAME,
    WRITEBACK_PENDING,
    TerminalFrame,
    TerminalStatus,
    ToolIntent,
    TurnAdmissionSource,
    TurnContext,
    TurnRuntimeConfig,
    TurnStatus,
    admits_spent_balance,
    mid_turn_reply_id_for,
    turn_id_for,
    turn_queue_for,
)

QUEUED: TurnStatus = "queued"
CANCELLED: TerminalStatus = "cancelled"
ADMITTED_TURN_METRIC = "admitted_turn_total"


class _SupersededByMember(Exception):
    pass


ARCHIVED_REFUSAL_MESSAGE = "This app is archived. Restore it from Applications to use it again."


@dataclass(frozen=True)
class _Inbound:
    body: str
    speaker_member_id: UUID | None
    context: TurnContext | None
    authority: ExecutionAuthority
    admitted_at: datetime | None = None


@dataclass(frozen=True)
class _ExistingTurn:
    id: UUID
    status: TurnStatus
    seq: int
    running_attempt: str | None


@dataclass(frozen=True)
class _DedupeResult:
    existing: _ExistingTurn | None
    inbound: _Inbound
    admitted: Admitted | None = None


@dataclass(frozen=True)
class _FoldResult:
    parked_turn_id: UUID | None = None
    arrival_id: UUID | None = None
    admitted: Admitted | None = None
    waits_for_live_turn: bool = False
    """The arrival founds its own turn and that turn waits for the live one to end. The turn is
    left unstamped rather than enqueued, so the live turn's own exit offers it — a conversation
    runs one turn at a time, and work that could not fold does not become a second runner."""


@dataclass(frozen=True)
class _CreatedTurn:
    id: UUID
    seq: int
    status: TurnStatus
    admission_source: TurnAdmissionSource


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
        runtime_config: TurnRuntimeConfig | None = None,
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
                authority=authority_from_member_id(speaker_member_id),
                member_admission=True,
                intent=intent,
                comment=comment,
                runtime_config=runtime_config,
            )
        await self._wake_live_turn(admitted)
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
        self, workspace_id: UUID, conversation_id: UUID, ended_turn_id: UUID
    ) -> UUID | None:
        """Re-admit the oldest arrival the ended turn left pending under its delivery key — the
        same admission a resend rides, so a live successor folds it and an idle conversation founds
        a turn on it. Every workflow exit and a member's stop pass here, so a message that reached
        a turn which then failed or was cancelled is read by the next turn. Returns the founded
        turn's id, None when nothing was pending or the message joined a live turn.

        Only a row its sender waits on is re-admitted: a root turn's are the messages members
        sent, a spawned turn's the ones its parent sent — unless the spawned turn was cancelled,
        since a cancel ends the child's work and the parent that sent the follow-up is the one who
        cancelled, or was cancelled with it. A child's result or an extension's prompt folded into
        a root turn stays pending for the conversation's next turn, as the drain leaves it, so a
        stop ends the work it stopped. Every row is re-admitted as work already accepted: its
        sender was answered when it was first admitted, so no gate answers them again — a seat or
        balance refusal holds the turn for the return that releases it rather than cancelling it
        with a reason nobody reads, and a pin the live turn no longer matches folds under the live
        one's. The row carries the ended turn's runtime
        config, which its fold matched, and a speakerless row takes the ended turn's authority,
        since it records none. A member's founded run is announced as its first `Absorbed` frame,
        since founding consumes the row outside any drain. A keyless row is stamped a key first, so
        every path here is a re-admission and none can say a message twice."""
        async with workspace_tx() as connection:
            ended = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.on_behalf_of_member_id,
                        tables.turn.c.runtime_config,
                        tables.turn.c.parent_turn_id,
                    ).where(
                        tables.turn.c.id == ended_turn_id,
                        tables.turn.c.workspace_id == workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                    )
                )
            ).one()
            if ended.parent_turn_id is not None and ended.status == CANCELLED:
                return None
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
                        (
                            tables.inbound_message.c.admission_source == INTERNAL_ADMISSION
                            if ended.parent_turn_id is not None
                            else tables.inbound_message.c.speaker_member_id.is_not(None)
                        ),
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
        context = None if row.context is None else TurnContext.model_validate(row.context)
        runtime_config = (
            None
            if ended.runtime_config is None
            else TurnRuntimeConfig.model_validate(ended.runtime_config)
        )
        try:
            admitted = await self._admit(
                workspace_id,
                conversation_id,
                None,
                row.body,
                row.speaker_member_id,
                idempotency_key,
                context,
                authority=(
                    authority_from_member_id(row.speaker_member_id)
                    if row.speaker_member_id is not None
                    else turn_authority(ended.speaker_member_id, ended.on_behalf_of_member_id)
                ),
                member_admission=row.speaker_member_id is not None,
                holds_work_already_done=True,
                runtime_config=runtime_config,
            )
        except AgentArchived:
            return None
        await self._wake_live_turn(admitted)
        if not admitted.opened_run:
            return None
        if row.speaker_member_id is not None and self.hub is not None:
            await self.hub.publish(admitted.turn_id, Absorbed(arrivals=(row.id,)))
        return admitted.turn_id

    async def invoke(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        authority: ExecutionAuthority,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> UUID | None:
        """Admit an internal turn. `authority` carries forward the authority the work already held
        — a subagent hands its result back to the conversation that delegated it, and
        a turn woken to read that result must not be able to do less than the turn that spawned it,
        or the shortfall surfaces later as a refusal no member can place.

        A turn founded on a spawned conversation inherits that conversation's spawn identity from
        its founding turn — parent linkage, profile, display name — and delivers its result:
        nothing awaits a turn past the founding one, so a child woken by its grandchild's result
        or by a `message_spawn` follow-up continues under the same contract and its answer reaches
        the conversation that spawned it, never a contractless turn whose work reaches nobody.
        An arrival that joins a live turn is announced on that turn's hub exactly as a member's is.

        `as_scheduled` gives the turn a scheduled fire's meaning without a scheduled row: it founds
        its own turn beside a live one instead of folding, and it is seat-gated on the member it
        acts for, so an unseated member's fire is refused wherever it lands.

        `standalone` also founds its own turn beside a live one, but keeps an internal admission's
        meaning. Event delivery uses it when folding would discard that event's authority or
        idempotency boundary.

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
                authority=authority,
                holds_work_already_done=holds_work_already_done,
                as_scheduled=as_scheduled,
                standalone=standalone,
                unless_member_since=unless_member_since,
                unless_member_arrival_since=unless_member_arrival_since,
                runtime_config=runtime_config,
            )
        except _SupersededByMember:
            return None
        await self._wake_live_turn(admitted)
        return admitted.turn_id

    async def _wake_live_turn(self, admitted: Admitted) -> None:
        """Announce an arrival that joined a live turn on that turn's hub, after its row has
        committed — one rendezvous for a member's message and an internal one alike, so whatever
        waits on the turn reads the same frame whichever surface the message came from. A fold
        that resumed a parked turn opened a run instead, and that run drains the row itself."""
        if admitted.arrival_id is not None and not admitted.opened_run and self.hub is not None:
            await self.hub.publish(admitted.turn_id, ArrivalQueued(arrival_id=admitted.arrival_id))

    async def _admit(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        asserted_agent_id: UUID | None,
        body: str,
        speaker_member_id: UUID | None,
        idempotency_key: str | None,
        context: TurnContext | None,
        authority: ExecutionAuthority,
        member_admission: bool = False,
        intent: ToolIntent | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        comment: str | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> Admitted:
        self._validate_member_watermarks(unless_member_since, unless_member_arrival_since)
        dispatch_now = False
        waits_for_live_turn = False
        opened_run = False
        counted_source: TurnAdmissionSource | None = None
        folded_parked_turn: UUID | None = None
        status: TurnStatus | None = None
        arrival_id: UUID | None = None
        redispatch_workflow_id: str | None = None
        inbound = _Inbound(body, speaker_member_id, context, authority)
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
            dedupe = await self._deduplicate(
                connection,
                workspace_id,
                conversation_id,
                agent_id,
                idempotency_key,
                runtime_config,
                inbound,
                comment,
            )
            if dedupe.admitted is not None:
                return dedupe.admitted
            deduped = dedupe.existing
            inbound = dedupe.inbound
            body = inbound.body
            context = inbound.context
            speaker_member_id = inbound.speaker_member_id
            authority = inbound.authority
            await self._guard_member_watermark(
                connection,
                workspace_id,
                conversation_id,
                deduped,
                unless_member_since,
                unless_member_arrival_since,
            )
            if deduped is None and not as_scheduled and not standalone and intent is None:
                folded = await self._fold_live(
                    connection,
                    workspace_id,
                    conversation_id,
                    conversation.member_id,
                    conversation.surface,
                    agent_id,
                    archived,
                    member_admission,
                    authority,
                    holds_work_already_done,
                    runtime_config,
                    idempotency_key,
                    inbound,
                    comment,
                )
                if folded.admitted is not None:
                    return folded.admitted
                folded_parked_turn = folded.parked_turn_id
                arrival_id = folded.arrival_id
                waits_for_live_turn = folded.waits_for_live_turn
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
                created = await self._create_turn(
                    connection,
                    workspace_id,
                    conversation_id,
                    conversation.member_id,
                    conversation.surface,
                    agent_id,
                    archived,
                    member_admission,
                    intent,
                    holds_work_already_done,
                    as_scheduled,
                    authority,
                    idempotency_key,
                    runtime_config,
                    inbound,
                )
                turn_id = created.id
                turn_seq = created.seq
                status = created.status
                opened_run = True
                counted_source = created.admission_source
            if folded_parked_turn is None and status == QUEUED and not waits_for_live_turn:
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
        return await self._finish_admission(
            workspace_id,
            conversation_id,
            conversation.surface,
            admitted.turn_id,
            status,
            admitted,
            counted_source,
            folded_parked_turn,
            dispatch_now,
            redispatch_workflow_id,
        )

    async def _guard_member_watermark(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        conversation_id: UUID,
        deduped: _ExistingTurn | None,
        turn_watermark: int | None,
        arrival_watermark: int | None,
    ) -> None:
        if deduped is not None or turn_watermark is None or arrival_watermark is None:
            return
        superseded = (
            await connection.execute(
                sa.select(
                    sa.exists(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.speaker_member_id.is_not(None),
                            tables.turn.c.status != CANCELLED,
                            tables.turn.c.seq > turn_watermark,
                        )
                    )
                    | sa.exists(
                        sa.select(tables.inbound_message.c.id).where(
                            tables.inbound_message.c.workspace_id == workspace_id,
                            tables.inbound_message.c.conversation_id == conversation_id,
                            tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                            tables.inbound_message.c.seq > arrival_watermark,
                        )
                    )
                )
            )
        ).scalar_one()
        if superseded:
            raise _SupersededByMember

    @staticmethod
    def _validate_member_watermarks(
        turn_watermark: int | None, arrival_watermark: int | None
    ) -> None:
        if (turn_watermark is None) != (arrival_watermark is None):
            raise ValueError("waiting on a member takes both watermarks, turn and arrival")

    async def _finish_admission(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        surface: str,
        turn_id: UUID,
        status: TurnStatus | None,
        admitted: Admitted,
        counted_source: TurnAdmissionSource | None,
        folded_parked_turn: UUID | None,
        dispatch_now: bool,
        redispatch_workflow_id: str | None,
    ) -> Admitted:
        if counted_source is not None:
            emit_metric(
                ADMITTED_TURN_METRIC,
                surface=surface,
                admission_source=counted_source,
            )
        if folded_parked_turn is not None:
            await self._enqueue(
                workspace_id, conversation_id, folded_parked_turn, workflow_id=uuid4().hex
            )
            return admitted
        if status is None:
            raise RuntimeError("admission finished without a turn status")
        if status != QUEUED:
            return admitted
        if dispatch_now:
            await self._enqueue(
                workspace_id, conversation_id, turn_id, workflow_id=redispatch_workflow_id
            )
        return admitted

    async def _deduplicate(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        idempotency_key: str | None,
        runtime_config: TurnRuntimeConfig | None,
        inbound: _Inbound,
        comment: str | None,
    ) -> _DedupeResult:
        """Settle a repeated idempotency key: the turn it already founded, the live turn it already
        arrived on, or the orphaned arrival it re-founds here.

        A re-founded arrival takes the queued row's body, context, and speaker. It keeps this
        call's authority when the row names no speaker, because an internal arrival records no
        member: the caller carries the authority the work already held, and reading it off a
        speakerless row would found the turn as workspace work."""
        if idempotency_key is None:
            return _DedupeResult(None, inbound)
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.status,
                    tables.turn.c.seq,
                    tables.turn.c.conversation_id,
                    tables.turn.c.agent_id,
                    tables.turn.c.running_attempt,
                    tables.turn.c.runtime_config,
                )
                .where(
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is not None:
            if row.conversation_id != conversation_id or row.agent_id != agent_id:
                raise RuntimeError("idempotency key reused for a different turn")
            if runtime_config is not None and (
                row.runtime_config is None
                or TurnRuntimeConfig.model_validate(row.runtime_config) != runtime_config
            ):
                raise ValueError("turn already has a different runtime config")
            return _DedupeResult(
                _ExistingTurn(row.id, row.status, row.seq, row.running_attempt), inbound
            )
        queued = (
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
        if queued is None:
            return _DedupeResult(None, inbound)
        if queued.conversation_id != conversation_id:
            raise RuntimeError("idempotency key reused for a different turn")
        if queued.consumed_turn_id is not None:
            admitted = await self._record_comment(
                connection,
                workspace_id,
                Admitted(queued.consumed_turn_id, opened_run=False),
                comment,
                queued.id,
            )
            return _DedupeResult(None, inbound, admitted)
        target_live = (
            await connection.execute(
                sa.select(tables.turn.c.status.in_(NON_TERMINAL_STATUSES)).where(
                    tables.turn.c.id == queued.admitted_turn_id
                )
            )
        ).scalar_one()
        if target_live:
            admitted = await self._record_comment(
                connection,
                workspace_id,
                Admitted(
                    queued.admitted_turn_id,
                    opened_run=False,
                    arrival_id=queued.id,
                ),
                comment,
            )
            return _DedupeResult(None, inbound, admitted)
        await connection.execute(
            sa.delete(tables.inbound_message).where(tables.inbound_message.c.id == queued.id)
        )
        return _DedupeResult(
            None,
            replace(
                inbound,
                body=queued.body,
                context=(
                    None if queued.context is None else TurnContext.model_validate(queued.context)
                ),
                speaker_member_id=queued.speaker_member_id,
                authority=(
                    authority_from_member_id(queued.speaker_member_id)
                    if queued.speaker_member_id is not None
                    else inbound.authority
                ),
                admitted_at=queued.created_at,
            ),
        )

    async def _fold_live(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        conversation_id: UUID,
        conversation_member_id: UUID | None,
        surface: str,
        agent_id: UUID,
        archived: bool,
        member_admission: bool,
        authority: ExecutionAuthority,
        holds_work_already_done: bool,
        runtime_config: TurnRuntimeConfig | None,
        idempotency_key: str | None,
        inbound: _Inbound,
        comment: str | None,
    ) -> _FoldResult:
        live_turn = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.status,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.on_behalf_of_member_id,
                    tables.turn.c.runtime_config,
                    sa.or_(
                        tables.turn.c.retry_at.is_(None),
                        tables.turn.c.retry_at <= sa.func.now(),
                    ).label("retry_due"),
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
        live_runtime_config = (
            None
            if live_turn is None or live_turn.runtime_config is None
            else TurnRuntimeConfig.model_validate(live_turn.runtime_config)
        )
        if (
            runtime_config is not None
            and live_turn is not None
            and live_runtime_config != runtime_config
            and not holds_work_already_done
        ):
            raise ValueError("running turn has a different runtime config")
        effective_runtime_config = live_runtime_config if live_turn is not None else runtime_config
        live_authority = (
            None
            if live_turn is None
            else turn_authority(live_turn.speaker_member_id, live_turn.on_behalf_of_member_id)
        )
        if live_authority is not None and not member_admission and authority != live_authority:
            return _FoldResult(waits_for_live_turn=True)
        parked_member = (
            None
            if live_turn is None or live_turn.status != PARKED or live_authority is None
            else authority_member_id(live_authority)
        )
        parked_members = {parked_member} if parked_member is not None else set()
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
        parked_seated = await seats.all_seated(connection, list(parked_members))
        fold_admitted = (
            live_turn is not None
            and not archived
            and (
                await seats.admits(connection, authority_from_member_id(inbound.speaker_member_id))
                if inbound.speaker_member_id is not None
                else not member_admission
            )
            and parked_seated
        )
        fold_decision = (
            None
            if not fold_admitted
            else await SpendEvaluator(workspace_id, conversation_member_id, agent_id).decide(
                connection, 0
            )
        )
        fold_balance = (
            None
            if fold_decision is None or fold_decision.outcome != ALLOW
            else await BalanceGate(workspace_id, self.billing_url).admits(
                connection,
                agent_id,
                self.key_slot_for,
                model=(
                    None if effective_runtime_config is None else effective_runtime_config.model
                ),
            )
        )
        absorbs = fold_balance is not None and fold_balance.outcome == ALLOW
        held_by_balance = (
            member_admission
            and live_turn is not None
            and live_turn.status == PARKED
            and fold_balance is not None
            and fold_balance.outcome != ALLOW
        )
        if live_turn is None or not (absorbs or held_by_balance):
            return _FoldResult()
        message_seq = (
            await connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(tables.inbound_message.c.seq), 0) + 1).where(
                    tables.inbound_message.c.conversation_id == conversation_id
                )
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
                body=inbound.body,
                admission_source=arrival_source,
                context=(
                    None if inbound.context is None else inbound.context.model_dump(mode="json")
                ),
                speaker_member_id=inbound.speaker_member_id,
                idempotency_key=idempotency_key,
                admitted_turn_id=live_turn.id,
                created_at=(
                    inbound.admitted_at if inbound.admitted_at is not None else sa.func.now()
                ),
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
        if held_by_balance and surface in self.durable_surfaces:
            await self._record_park_notice(
                connection, workspace_id, live_turn.id, balance_park_message(self.billing_url)
            )
        if live_turn.status != PARKED or held_by_balance or not live_turn.retry_due:
            admitted = await self._record_comment(
                connection,
                workspace_id,
                Admitted(live_turn.id, opened_run=False, arrival_id=arrival_id),
                comment,
            )
            return _FoldResult(admitted=admitted)
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status=QUEUED,
                dispatch_enqueued_at=sa.func.now(),
                retry_at=None,
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == live_turn.id, tables.turn.c.status == PARKED)
        )
        return _FoldResult(parked_turn_id=live_turn.id, arrival_id=arrival_id)

    async def _create_turn(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        conversation_id: UUID,
        conversation_member_id: UUID | None,
        surface: str,
        agent_id: UUID,
        archived: bool,
        member_admission: bool,
        intent: ToolIntent | None,
        holds_work_already_done: bool,
        as_scheduled: bool,
        authority: ExecutionAuthority,
        idempotency_key: str | None,
        runtime_config: TurnRuntimeConfig | None,
        inbound: _Inbound,
    ) -> _CreatedTurn:
        seq = (
            await connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                    tables.turn.c.conversation_id == conversation_id
                )
            )
        ).scalar_one()
        spawned_identity = None
        if surface == SUBAGENT_SURFACE and seq > 1:
            spawned_identity = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.subagent_name,
                    ).where(
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.seq == 1,
                    )
                )
            ).one()
        turn_id = turn_id_for(workspace_id, conversation_id, seq)
        admission_source = (
            INTENT_ADMISSION
            if intent is not None
            else MEMBER_ADMISSION
            if member_admission
            else SCHEDULED_ADMISSION
            if as_scheduled
            else INTERNAL_ADMISSION
        )
        terminal: TerminalFrame | None
        park_notice: str | None = None
        refusal = await self._authority_refusal(
            connection,
            workspace_id,
            authority,
            archived,
            member_admission,
            holds_work_already_done,
        )
        if refusal is None:
            decision = await SpendEvaluator(workspace_id, conversation_member_id, agent_id).decide(
                connection, 0
            )
            balance = (
                SpendDecision(outcome=ALLOW, message="")
                if intent is not None and admits_spent_balance(intent)
                else await BalanceGate(workspace_id, self.billing_url).admits(
                    connection,
                    agent_id,
                    self.key_slot_for,
                    model=None if runtime_config is None else runtime_config.model,
                )
            )
            match decision.outcome:
                case "allow" if balance.outcome != ALLOW and member_admission and intent is None:
                    status, terminal = PARKED, None
                    park_notice = balance_park_message(self.billing_url)
                case _ if balance.outcome != ALLOW:
                    status, terminal = _refused(holds_work_already_done, balance.message)
                case "allow":
                    status, terminal = QUEUED, None
                case "park":
                    status, terminal = PARKED, None
                case _:
                    status, terminal = _refused(holds_work_already_done, decision.message)
        else:
            status, terminal = refusal
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound=inbound.body,
                admission_source=admission_source,
                speaker_member_id=inbound.speaker_member_id,
                on_behalf_of_member_id=(
                    None
                    if inbound.speaker_member_id is not None
                    else authority_member_id(authority)
                ),
                parent_turn_id=(
                    None if spawned_identity is None else spawned_identity.parent_turn_id
                ),
                subagent_profile=(
                    None if spawned_identity is None else spawned_identity.subagent_profile
                ),
                subagent_name=(
                    None if spawned_identity is None else spawned_identity.subagent_name
                ),
                result_delivery=None if spawned_identity is None else DELIVERY_PENDING,
                context=(
                    None if inbound.context is None else inbound.context.model_dump(mode="json")
                ),
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                traceparent=current_traceparent(),
                runtime_config=(
                    None if runtime_config is None else runtime_config.model_dump(mode="json")
                ),
                created_at=(
                    inbound.admitted_at if inbound.admitted_at is not None else sa.func.now()
                ),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.conversation)
            .where(
                tables.conversation.c.workspace_id == workspace_id,
                tables.conversation.c.id == conversation_id,
                tables.conversation.c.title.is_(None),
            )
            .values(title=conversation_name(inbound.body))
        )
        if surface in self.durable_surfaces:
            await connection.execute(
                sa.insert(tables.writeback).values(
                    turn_id=turn_id,
                    workspace_id=workspace_id,
                    status=WRITEBACK_PENDING,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            if park_notice is not None:
                await self._record_park_notice(connection, workspace_id, turn_id, park_notice)
        return _CreatedTurn(turn_id, seq, status, admission_source)

    async def _authority_refusal(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        authority: ExecutionAuthority,
        archived: bool,
        member_admission: bool,
        holds_work_already_done: bool,
    ) -> tuple[TurnStatus, TerminalFrame | None] | None:
        """An archived app admits no turn, whatever the work holds: an archive is not a cap the
        dispatcher re-decides, so a turn held under it would sit until the sweep ran it on the
        archived app. The seat and balance refusals hold work already accepted, since a seat or
        credit returning is exactly what releases them."""
        if archived:
            return CANCELLED, TerminalFrame(status=CANCELLED, text=ARCHIVED_REFUSAL_MESSAGE)
        if authority_member_id(authority) is None and member_admission:
            return CANCELLED, TerminalFrame(status=CANCELLED, text=UNRESOLVED_SPEAKER_MESSAGE)
        if not await Seats(workspace_id).admits(connection, authority):
            return _refused(holds_work_already_done, SEAT_REFUSAL_MESSAGE)
        return None

    async def _record_park_notice(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        turn_id: UUID,
        notice: str,
    ) -> None:
        """Tell the thread it is held. The turn is parked, not terminal, so its writeback delivers
        nothing until it runs and answers — a durable surface would otherwise go silent from the
        moment the balance ran out until an admin noticed. This is the same row a spoken span rides;
        a live surface reads the same sentence off the status poll that ends its stream instead.

        Two paths reach it: the message the balance holds at the door, and a member's next message
        folding onto a turn already held — which is what covers a turn the engine parked mid-flight,
        since that park ends the live stream and writes nothing durable. The row's id is the turn's
        own, so however many messages fold on, the thread reads the hold exactly once."""
        insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.mid_turn_reply)
            .values(
                id=mid_turn_reply_id_for(turn_id, BALANCE_PARK_ROUND_INDEX, 0),
                workspace_id=workspace_id,
                turn_id=turn_id,
                round_index=BALANCE_PARK_ROUND_INDEX,
                span_index=0,
                message_ref=None,
                text=notice,
                status=WRITEBACK_PENDING,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(index_elements=[tables.mid_turn_reply.c.id])
        )

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
        async with workspace_tx() as connection:
            kind = (
                await connection.execute(
                    sa.select(tables.turn.c.parent_turn_id, tables.turn.c.admission_source).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one()
        options: EnqueueOptions = {
            "queue_name": turn_queue_for(kind.parent_turn_id, kind.admission_source),
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": workflow_id if workflow_id is not None else str(turn_id),
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
        *,
        authority: ExecutionAuthority,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> UUID | None:
        return await self.admission.invoke(
            self.workspace_id,
            conversation_id,
            agent_id,
            message,
            idempotency_key=idempotency_key,
            context=context,
            authority=authority,
            holds_work_already_done=holds_work_already_done,
            as_scheduled=as_scheduled,
            standalone=standalone,
            unless_member_since=unless_member_since,
            unless_member_arrival_since=unless_member_arrival_since,
            runtime_config=runtime_config,
        )

    async def redispatch(self, conversation_id: UUID, ended_turn_id: UUID) -> UUID | None:
        return await self.admission.redispatch(self.workspace_id, conversation_id, ended_turn_id)

    async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]:
        """The conversations an invoke reaches `member_id` through: on a surface this admission
        registers writebacks for, bound to a live agent, with the member's own audience, and
        holding a turn the member spoke — newest such turn first. `speaker_member_id` is the
        privacy fence as well as the recency signal: only a conversation this member personally
        spoke in, on their own private audience, is ever returned. An archived agent's
        conversation admits no turn, so it is no reach."""
        last_spoke_at = sa.func.max(tables.turn.c.created_at).label("last_spoke_at")
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.surface,
                        tables.conversation.c.id,
                        tables.conversation.c.agent_id,
                        last_spoke_at,
                    )
                    .select_from(
                        tables.conversation.join(
                            tables.turn,
                            tables.turn.c.conversation_id == tables.conversation.c.id,
                        ).join(tables.agent, tables.agent.c.id == tables.conversation.c.agent_id)
                    )
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.agent.c.archived_at.is_(None),
                        tables.conversation.c.surface.in_(tuple(self.admission.durable_surfaces)),
                        tables.conversation.c.audience == str(conversation_audience(member_id)),
                        tables.turn.c.speaker_member_id == member_id,
                    )
                    .group_by(
                        tables.conversation.c.surface,
                        tables.conversation.c.id,
                        tables.conversation.c.agent_id,
                    )
                    .order_by(last_spoke_at.desc(), tables.conversation.c.id)
                    .limit(limit)
                )
            ).all()
        return tuple(
            MemberReach(
                surface=row.surface,
                conversation_id=row.id,
                agent_id=row.agent_id,
                last_spoke_at=_aware(row.last_spoke_at),
            )
            for row in rows
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
        runtime_config: TurnRuntimeConfig | None = None,
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
            runtime_config=runtime_config,
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
    writeback and the chat index skips the lane), while holding the lane's conversation until
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
