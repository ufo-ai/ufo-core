from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.ext.surface import Admitted
from ufo.loop.engine import _claim_turn
from ufo.scheduling import ONE_TIME_SCHEDULE, ScheduledTask
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, TurnContext
from ufo.seats import SEAT_REFUSAL_MESSAGE, UNRESOLVED_SPEAKER_MESSAGE
from ufo.surfaces.admission import Admission


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)
        self.workflow_ids.append(options["workflow_id"])


class _AcceptedThenErroredDbos:
    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        await _claim_turn(UUID(turn_id), turn_id)
        raise RuntimeError("ambiguous enqueue response")


class _FailedDbos:
    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        raise RuntimeError("enqueue failed")


async def _seed() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="who@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, agent_id, conversation_id


async def _turn_count(conversation_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()


async def _queued_bodies(conversation_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body)
                    .where(
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .order_by(tables.inbound_message.c.seq)
                )
            ).scalars()
        )


async def _queued_ids(conversation_id: UUID) -> list[UUID]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.id)
                    .where(
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .order_by(tables.inbound_message.c.seq)
                )
            ).scalars()
        )


async def _finish(turn_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="done",
                terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == turn_id)
        )


async def test_repeated_delivery_dedups_to_one_turn(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await admission.invoke(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    assert first == second
    assert await _turn_count(conversation_id) == 1
    assert dbos.enqueued == [str(first), str(first)]
    assert dbos.workflow_ids == [str(first), str(first)]


async def test_idempotency_key_keeps_the_first_body(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await admission.invoke(
        workspace_id, conversation_id, agent_id, "different", "C0000001:1.5"
    )
    assert second == first
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.id == first)
            )
        ).scalar_one()
    assert inbound == "hi"


async def test_message_while_a_turn_is_queued_joins_its_inbound_queue(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, "first message", member_id, "C:1"
    )
    second = await admission.admit_member(
        workspace_id,
        conversation_id,
        "second message",
        member_id,
        "C:2",
        TurnContext(sender="Pat Doe", timezone="UTC"),
    )
    assert first.opened_run
    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["second message"]
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.id == first.turn_id)
            )
        ).scalar_one()
        queued = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.context,
                    tables.inbound_message.c.speaker_member_id,
                    tables.inbound_message.c.idempotency_key,
                    tables.inbound_message.c.admission_source,
                    tables.inbound_message.c.admitted_turn_id,
                ).where(tables.inbound_message.c.conversation_id == conversation_id)
            )
        ).one()
    assert inbound == "first message"
    assert TurnContext.model_validate(queued.context).sender == "Pat Doe"
    assert queued.speaker_member_id == member_id
    assert queued.idempotency_key == "C:2"
    assert queued.admission_source == "member"
    assert queued.admitted_turn_id == first.turn_id
    assert dbos.enqueued == [str(first.turn_id)]


async def test_message_while_a_turn_runs_joins_its_inbound_queue(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    assert await _claim_turn(first.turn_id, str(first.turn_id))
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["two"]


async def test_message_redelivery_joins_the_queued_row(db: None) -> None:
    """A redelivery lands on the row the first delivery queued and says so: the same turn and the
    same arrival, so a surface holding that message keeps holding the one arrival it admitted."""
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, "two", member_id, "C:2"
    )
    folded = Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert second == folded
    assert redelivered == folded
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["two"]


async def test_every_admission_source_joins_the_live_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "job prompt", "job:1")
    second = await admission.admit_member(workspace_id, conversation_id, "hello", member_id, "C:2")
    third = await admission.invoke(workspace_id, conversation_id, agent_id, "another job")
    assert second == Admitted(
        first, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert third == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["hello", "another job"]


async def test_a_reject_cap_stops_arrivals_at_the_boundary(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="reject",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=first.turn_id,
                dimension="tokens",
                amount=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second.turn_id != first.turn_id
    assert not second.opened_run
    assert await _queued_bodies(conversation_id) == []
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == second.turn_id)
            )
        ).scalar_one()
    assert status == "cancelled"


async def test_message_after_a_terminal_turn_starts_a_new_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    await _finish(first.turn_id)
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second.turn_id != first.turn_id
    assert second.opened_run
    assert await _turn_count(conversation_id) == 2
    assert await _queued_bodies(conversation_id) == []


async def test_enqueue_failure_keeps_the_turn_queued_for_dispatch(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    turn_id = await Admission(dbos=_FailedDbos(), durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.terminal,
                    tables.turn.c.dispatch_enqueued_at,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
    assert tuple(row) == ("queued", None, None)


async def test_enqueue_error_does_not_fail_a_turn_already_claimed_by_the_worker(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    turn_id = await Admission(dbos=_AcceptedThenErroredDbos(), durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert tuple(row) == ("running", None)


async def test_redelivery_refounds_a_row_whose_turn_died_undrained(db: None) -> None:
    """A provider retry of a message whose live turn failed before draining it must not point at
    the dead turn: the row re-founds a fresh queued turn carrying the same idempotency key."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=False, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="failed",
                terminal=TerminalFrame(status="failed", error_class="Boom").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first.turn_id)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, "two", member_id, "C:2"
    )
    assert redelivered.turn_id != first.turn_id
    assert redelivered.opened_run
    async with workspace_tx() as connection:
        refounded = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                    tables.turn.c.idempotency_key,
                ).where(tables.turn.c.id == redelivered.turn_id)
            )
        ).one()
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.inbound_message))
        ).scalar_one()
    assert (refounded.inbound, refounded.status, refounded.idempotency_key) == (
        "two",
        "queued",
        "C:2",
    )
    assert rows == 0
    assert str(redelivered.turn_id) in dbos.enqueued


async def test_fold_onto_a_parked_turn_dispatches_its_resume(db: None) -> None:
    """Caps were raised since the park: a fresh message folds onto the parked turn AND resumes it
    now, under a fresh workflow id, instead of leaving the member staring at the park notice."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
    second = await admission.admit_member(workspace_id, conversation_id, "two", member_id, "C:2")
    assert second == Admitted(
        first.turn_id, opened_run=True, arrival_id=(await _queued_ids(conversation_id))[0]
    )
    assert await _queued_bodies(conversation_id) == ["two"]
    async with workspace_tx() as connection:
        resumed_status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == first.turn_id)
            )
        ).scalar_one()
    assert resumed_status == "queued"
    assert dbos.enqueued == [str(first.turn_id), str(first.turn_id)]
    assert dbos.workflow_ids[0] == str(first.turn_id)
    assert dbos.workflow_ids[1] != str(first.turn_id)


async def test_redelivery_of_a_fold_resumed_turn_retries_under_a_fresh_workflow_id(
    db: None,
) -> None:
    """The fold's own enqueue was deferred, so the redelivered message must retry the dispatch —
    but the run that parked the turn consumed its own workflow id, so a retry riding it would
    dedup against the completed workflow while re-stamping the offer, holding the sweep off a
    grace window at a time."""
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                running_attempt=uuid4().hex,
                dispatch_enqueued_at=None,
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first.turn_id)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, "one", member_id, "C:1"
    )
    assert redelivered == Admitted(first.turn_id, opened_run=False)
    assert dbos.enqueued == [str(first.turn_id), str(first.turn_id)]
    assert dbos.workflow_ids[0] == str(first.turn_id)
    assert dbos.workflow_ids[1] != str(first.turn_id)


async def _set_limit(workspace_id: UUID, limit: int) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=limit, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )


async def _seat(member_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )


async def _turn_row(turn_id: UUID) -> tuple[str, str | None]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    text = None if row.terminal is None else TerminalFrame.model_validate(row.terminal).text
    return row.status, text


async def test_unseated_speaker_is_refused_with_the_seat_message(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 1)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}))
    turn_id = (await admission.admit_member(workspace_id, conversation_id, "hi", member_id)).turn_id
    assert dbos.enqueued == []
    status, text = await _turn_row(turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE
    async with workspace_tx() as connection:
        writebacks = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.writeback)
                .where(tables.writeback.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert writebacks == 1


async def test_seated_speaker_enqueues_under_a_limit(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 1)
    await _seat(member_id)
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hi", member_id
    )
    turn_id = admitted.turn_id
    assert admitted.opened_run
    assert dbos.enqueued == [str(turn_id)]
    status, _ = await _turn_row(turn_id)
    assert status == "queued"


async def test_internal_invoke_passes_a_seat_limit(db: None) -> None:
    workspace_id, _, agent_id, conversation_id = await _seed()
    await _set_limit(workspace_id, 1)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "background job"
    )
    assert dbos.enqueued == [str(turn_id)]


async def test_scheduled_fire_into_an_unseated_members_conversation_is_refused(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    await _set_limit(workspace_id, 1)
    task_id = uuid4()
    next_run_at = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.scheduled_task).values(
                id=task_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                name="daily",
                schedule="0 9 * * *",
                prompt="run the report",
                description="daily report",
                next_run_at=next_run_at,
                claimed_by="claim-1",
                created_by_member_id=_member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    task = ScheduledTask(
        id=task_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        name="daily",
        schedule="0 9 * * *",
        prompt="run the report",
        description="daily report",
        next_run_at=next_run_at,
        last_run_at=None,
        expires_at=None,
        origin_seq=None,
        resume_turn_id=None,
        claim_id="claim-1",
        paused=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by_member_id=_member_id,
    )
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke_scheduled(
        workspace_id, task
    )
    assert turn_id is not None
    assert dbos.enqueued == []
    status, text = await _turn_row(turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE


async def test_unseated_speakers_message_does_not_fold_into_a_live_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    await _set_limit(workspace_id, 1)
    second = await admission.admit_member(workspace_id, conversation_id, "second", member_id)
    assert second.turn_id != first.turn_id
    status, text = await _turn_row(second.turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE
    first_status, _ = await _turn_row(first.turn_id)
    assert first_status == "queued"
    assert await _queued_bodies(conversation_id) == []


async def test_seated_reply_does_not_resume_a_seat_blocked_parked_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 2)
    await _seat(member_id)
    second_member = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member,
                workspace_id=workspace_id,
                email="second@example.com",
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=None, updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )
    second = await admission.admit_member(workspace_id, conversation_id, "second", second_member)
    assert second.turn_id != first.turn_id
    first_status, _ = await _turn_row(first.turn_id)
    assert first_status == "parked"
    second_status, _ = await _turn_row(second.turn_id)
    assert second_status == "queued"
    assert await _queued_bodies(conversation_id) == []


async def test_seated_reply_does_not_resume_an_aggregate_with_an_unseated_speaker(
    db: None,
) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 2)
    await _seat(member_id)
    unseated = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=unseated,
                workspace_id=workspace_id,
                email="unseated@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first.turn_id)
        )
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="pending",
                admission_source="member",
                speaker_member_id=unseated,
                admitted_turn_id=first.turn_id,
                created_at=sa.func.now(),
            )
        )

    second = await admission.admit_member(workspace_id, conversation_id, "resume", member_id)

    assert second.turn_id != first.turn_id
    first_status, _ = await _turn_row(first.turn_id)
    second_status, _ = await _turn_row(second.turn_id)
    assert first_status == "parked"
    assert second_status == "queued"
    assert await _queued_bodies(conversation_id) == ["pending"]


async def test_unseated_speakers_message_does_not_take_over_a_timer_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    task_id, timer_turn_id = uuid4(), uuid4()
    due = datetime.now(UTC)
    timer_key = f"{task_id}:{due.isoformat()}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=timer_turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="resume the plan",
                admission_source="scheduled",
                idempotency_key=timer_key,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.scheduled_task).values(
                id=task_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                name="resume",
                schedule=ONE_TIME_SCHEDULE,
                prompt="resume the plan",
                description="one-time resume",
                next_run_at=due,
                resume_turn_id=timer_turn_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _set_limit(workspace_id, 1)
    dbos = StubDbos()
    refused = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hey", member_id
    )
    assert refused.turn_id != timer_turn_id
    status, text = await _turn_row(refused.turn_id)
    assert status == "cancelled"
    assert text == SEAT_REFUSAL_MESSAGE
    async with workspace_tx() as connection:
        timer = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.status,
                ).where(tables.turn.c.id == timer_turn_id)
            )
        ).one()
    assert timer.inbound == "resume the plan"
    assert timer.speaker_member_id is None
    assert timer.status == "queued"


async def test_speakerless_member_message_is_refused_under_a_seat_limit(db: None) -> None:
    workspace_id, _member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 1)
    dbos = StubDbos()
    turn_id = (
        await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
            workspace_id, conversation_id, "who am i", None
        )
    ).turn_id
    assert dbos.enqueued == []
    status, text = await _turn_row(turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE


async def test_speakerless_member_message_passes_an_ungated_workspace(db: None) -> None:
    workspace_id, _member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    turn_id = (
        await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
            workspace_id, conversation_id, "hello", None
        )
    ).turn_id
    assert dbos.enqueued == [str(turn_id)]


async def test_speakerless_member_message_does_not_fold_under_a_seat_limit(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    await _set_limit(workspace_id, 2)
    await _seat(member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    ghost = await admission.admit_member(workspace_id, conversation_id, "psst", None)
    assert ghost.turn_id != first.turn_id
    status, text = await _turn_row(ghost.turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE
    assert await _queued_bodies(conversation_id) == []


async def test_ghost_message_does_not_take_over_a_timer_turn_under_a_seat_limit(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    task_id, timer_turn_id = uuid4(), uuid4()
    due = datetime.now(UTC)
    timer_key = f"{task_id}:{due.isoformat()}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=timer_turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="resume the plan",
                admission_source="scheduled",
                idempotency_key=timer_key,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.scheduled_task).values(
                id=task_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                name="resume-ghost",
                schedule=ONE_TIME_SCHEDULE,
                prompt="resume the plan",
                description="one-time resume",
                next_run_at=due,
                resume_turn_id=timer_turn_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _set_limit(workspace_id, 1)
    dbos = StubDbos()
    refused = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hey", None
    )
    assert refused.turn_id != timer_turn_id
    status, text = await _turn_row(refused.turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE
    async with workspace_tx() as connection:
        timer = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.status).where(
                    tables.turn.c.id == timer_turn_id
                )
            )
        ).one()
    assert timer.inbound == "resume the plan"
    assert timer.status == "queued"


async def test_a_reject_cap_holds_a_turn_carrying_work_already_paid_for(db: None) -> None:
    """A member's next message can be turned away — they read why and decide what to do. A turn
    carrying a finished subagent's result holds work the ledger already booked, and cancelling it
    discards that output with nobody to tell: the member paid for the run and would never hear it.
    So the same reject cap that cancels an ordinary invoke parks this one, for the dispatcher to
    release when the cap is raised or its window rolls."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "one", member_id, "C:1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="reject",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=first.turn_id,
                dimension="tokens",
                amount=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    turned_away = await admission.invoke(workspace_id, conversation_id, agent_id, "ordinary")
    delivered = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "<subagent_result …>",
        "subagent-result:held",
        holds_work_already_done=True,
    )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id.in_([turned_away, delivered])
                )
            )
        ).all()
    state = {row.id: (row.status, row.terminal) for row in rows}
    assert state[turned_away][0] == "cancelled"
    assert state[delivered] == ("parked", None)
