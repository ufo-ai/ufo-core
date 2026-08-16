from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ufo import o11y
from ufo.db import workspace_tx
from ufo.ext.surface import Admitted, fence_member_message, mint_marker
from ufo.loop.engine import _claim_turn
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


async def test_member_admission_persists_the_latest_valid_timezone(db: None) -> None:
    workspace_id, member_id, _agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await admission.admit_member(
        workspace_id,
        conversation_id,
        "hello",
        member_id,
        context=TurnContext(timezone="America/Los_Angeles"),
    )
    async with workspace_tx() as connection:
        timezone = (
            await connection.execute(
                sa.select(tables.member.c.timezone).where(tables.member.c.id == member_id)
            )
        ).scalar_one()
    assert timezone == "America/Los_Angeles"


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


async def test_the_opening_turn_names_the_conversation_and_no_later_one_renames_it(
    db: None,
) -> None:
    """A conversation is called what its first message said — the member's own words out of the
    fence, never the ambient digest a channel surface renders around them. Every later message
    leaves that name standing: a rail row that renamed itself on each reply would name a
    conversation something its member never chose."""
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    marker = mint_marker()
    opened = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        fence_member_message(
            marker,
            "<ambient>a bystander said something</ambient>\n",
            "roll the warehouse plan forward",
            "",
        ),
        "C0000001:1.5",
    )
    await _finish(opened)
    await admission.invoke(
        workspace_id, conversation_id, agent_id, "and one more thing", "C0000001:2.0"
    )

    async with workspace_tx() as connection:
        title = (
            await connection.execute(
                sa.select(tables.conversation.c.title).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()

    assert title == "roll the warehouse plan forward"


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


async def _unseat(member_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=None, updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
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
    await _unseat(member_id)
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


async def test_seated_speaker_enqueues(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hi", member_id
    )
    turn_id = admitted.turn_id
    assert admitted.opened_run
    assert dbos.enqueued == [str(turn_id)]
    status, _ = await _turn_row(turn_id)
    assert status == "queued"


async def test_internal_invoke_passes_the_seat_gate(db: None) -> None:
    """An internal turn has no speaker to gate on, so an unseated conversation member never holds
    up the work the workspace itself asked for."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    await _unseat(member_id)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "background job"
    )
    assert dbos.enqueued == [str(turn_id)]


async def test_unseated_speakers_message_does_not_fold_into_a_live_turn(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    await _unseat(member_id)
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
    unseated = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=unseated,
                workspace_id=workspace_id,
                email="unseated@example.com",
                seated_at=None,
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


async def test_speakerless_member_message_is_refused(db: None) -> None:
    """A member surface that could not name its speaker is answering a stranger, whatever else is
    true of the workspace: there is no seat bound left to make this conditional, and there never
    was a deploy where a speaker the surface failed to resolve was somebody the agent should
    answer in a conversation carrying the workspace's own audience."""
    workspace_id, _member_id, _, conversation_id = await _seed()
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


async def test_speakerless_member_message_does_not_fold(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "first", member_id)
    ghost = await admission.admit_member(workspace_id, conversation_id, "psst", None)
    assert ghost.turn_id != first.turn_id
    status, text = await _turn_row(ghost.turn_id)
    assert status == "cancelled"
    assert text == UNRESOLVED_SPEAKER_MESSAGE
    assert await _queued_bodies(conversation_id) == []


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


async def _admission_stamp(turn_id: UUID) -> tuple[str, UUID | None, UUID | None]:
    """What the turn says about who admitted it: its source, its speaker, the member it acts for."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.admission_source,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.on_behalf_of_member_id,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
    return row.admission_source, row.speaker_member_id, row.on_behalf_of_member_id


async def test_an_as_scheduled_fire_founds_its_own_turn_beside_a_live_one(db: None) -> None:
    """A fire asserting the scheduled meaning gets it without a scheduled row: its own turn while
    another runs, stamped scheduled, speakerless, acting for the member who armed it."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    live = await admission.invoke(workspace_id, conversation_id, agent_id, "background job")
    assert live is not None
    assert await _claim_turn(live, str(live))

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "run the report",
        "task:fire-1",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
    )

    assert fired is not None
    assert fired != live
    assert await _turn_count(conversation_id) == 2
    assert await _queued_bodies(conversation_id) == []
    assert await _admission_stamp(fired) == ("scheduled", None, member_id)
    status, _ = await _turn_row(fired)
    assert status == "queued"
    assert str(fired) in dbos.enqueued


async def test_an_as_scheduled_fire_is_seat_gated_on_the_member_it_acts_for(db: None) -> None:
    """The same gate a scheduled row's fire meets: an unseated creator's fire is refused, and held
    instead of discarded when it carries work the ledger already booked."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    await _unseat(member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    refused = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "run the report",
        "task:fire-1",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
    )
    held = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "<probe_result …>",
        "task:fire-2",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
        holds_work_already_done=True,
    )

    assert refused is not None
    assert held is not None
    assert await _turn_row(refused) == ("cancelled", SEAT_REFUSAL_MESSAGE)
    assert await _turn_row(held) == ("parked", None)
    assert dbos.enqueued == []


async def test_an_internal_fire_is_seat_gated_on_the_member_it_acts_for(db: None) -> None:
    """A monitor fire and a delivered subagent result carry an on-behalf member without the
    scheduled stamp; the gate is the authority, not the stamp — an admin's revoke stops that
    member's work whatever admitted it, and work the ledger already booked is held, not
    discarded."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    await _unseat(member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())

    refused = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "run the errand",
        "errand:1",
        on_behalf_of_member_id=member_id,
    )
    held = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "<subagent_result …>",
        "delivery:1",
        on_behalf_of_member_id=member_id,
        holds_work_already_done=True,
    )

    assert refused is not None
    assert held is not None
    assert await _turn_row(refused) == ("cancelled", SEAT_REFUSAL_MESSAGE)
    assert await _turn_row(held) == ("parked", None)
    assert dbos.enqueued == []


async def test_a_member_turn_past_the_armed_seq_supersedes_a_fire(db: None) -> None:
    """The member got there first, so the fire is refused and writes nothing — no turn to answer
    and no arrival waiting for one."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    spoke = await admission.admit_member(workspace_id, conversation_id, "never mind", member_id)

    superseded = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == []
    assert dbos.enqueued == [str(spoke.turn_id)]


async def test_a_refused_message_does_not_supersede_a_fire(db: None) -> None:
    """A turn this gate refused never joined the conversation: nothing will answer it, so it cannot
    end the wait the fire belongs to. Otherwise a member whose seat an admin removed could kill a
    paused workflow by typing once, and a stranger the surface could not resolve could do it too —
    the work would hang with nobody left to resume it."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await _unseat(member_id)
    refused = await admission.admit_member(workspace_id, conversation_id, "let me in", member_id)
    stranger = await admission.admit_member(workspace_id, conversation_id, "who am i", None)
    assert await _turn_row(refused.turn_id) == ("cancelled", SEAT_REFUSAL_MESSAGE)
    assert await _turn_row(stranger.turn_id) == ("cancelled", UNRESOLVED_SPEAKER_MESSAGE)

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    status, _ = await _turn_row(fired)
    assert status == "queued"


async def _drain_arrivals(turn_id: UUID) -> None:
    """What the engine's drain leaves behind: the pending rows stamped consumed by the turn that
    absorbed them."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.inbound_message)
            .values(consumed_turn_id=turn_id)
            .where(tables.inbound_message.c.consumed_turn_id.is_(None))
        )


async def test_a_member_message_the_live_turn_absorbed_supersedes_a_fire(db: None) -> None:
    """The fold window. A member message arriving while the arming turn is still live founds no turn
    of its own — it lands on that turn's queue — and stops being queued the moment the engine drains
    it. Read only as a turn or as a pending row, the member would have spoken and left no trace by
    the time the timer came due, and the agent would resume a workflow the member already resumed.
    The trace is the turn the message folded into."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    arming = await admission.invoke(workspace_id, conversation_id, agent_id, "the arming work")
    assert arming is not None
    folded = await admission.admit_member(
        workspace_id, conversation_id, "actually, do this instead", member_id
    )
    assert folded.turn_id == arming
    await _drain_arrivals(arming)

    superseded = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1


async def test_a_member_message_absorbed_before_the_arm_never_refuses_the_timer(db: None) -> None:
    """The inverse of the fold window, and the sharper failure of the two. A member message that
    folded into the arming turn BEFORE the wait was armed is history the agent had already read when
    it decided to wait — and it lands on the arming turn itself, so a rule that asks which turn a
    fold joined can never tell it apart from a reply that arrived afterwards. Counted, it refuses
    every fire this wait will ever have: the runner retires the row on the refusal and the workflow
    is left unfinished with nothing scheduled to finish it. The arrival watermark is what separates
    them, because it was taken when the wait was armed."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    arming = await admission.invoke(workspace_id, conversation_id, agent_id, "the arming work")
    assert arming is not None
    folded = await admission.admit_member(
        workspace_id, conversation_id, "one more thing before you start", member_id
    )
    assert folded.turn_id == arming
    await _drain_arrivals(arming)

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        as_scheduled=True,
        unless_member_since=1,
        unless_member_arrival_since=1,
    )

    assert fired is not None
    assert fired != arming
    status, _ = await _turn_row(fired)
    assert status == "queued"


async def test_a_wait_takes_both_watermarks_or_neither(db: None) -> None:
    """Half the question is worse than none of it: a caller passing only the turn watermark would
    read as guarded while every folded reply passed straight through it, which is the defect this
    pair replaced."""
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())

    for watermarks in ({"unless_member_since": 0}, {"unless_member_arrival_since": 0}):
        with pytest.raises(ValueError, match="both watermarks"):
            await admission.invoke(
                workspace_id, conversation_id, agent_id, "resume the plan", "pause:1", **watermarks
            )

    assert await _turn_count(conversation_id) == 0


async def test_an_absorbed_internal_arrival_does_not_supersede_a_fire(db: None) -> None:
    """Only a member ends a wait on a member. Internal work folding into the same live turn — a
    subagent's result, another job's invoke — is the system talking to itself."""
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    arming = await admission.invoke(workspace_id, conversation_id, agent_id, "the arming work")
    assert arming is not None
    assert await admission.invoke(workspace_id, conversation_id, agent_id, "more work") == arming
    await _drain_arrivals(arming)

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        as_scheduled=True,
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    assert fired != arming
    assert await _turn_count(conversation_id) == 2


async def test_a_member_message_absorbed_before_the_wait_does_not_supersede_it(db: None) -> None:
    """A fold that landed on an earlier turn than the one that armed the wait is history the agent
    had already read when it armed. Counting it would refuse every fire in a conversation a member
    had ever folded a message into."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    earlier = await admission.invoke(workspace_id, conversation_id, agent_id, "earlier work")
    assert earlier is not None
    folded = await admission.admit_member(workspace_id, conversation_id, "a note", member_id)
    assert folded.turn_id == earlier
    await _drain_arrivals(earlier)
    await _finish(earlier)
    arming = await admission.invoke(workspace_id, conversation_id, agent_id, "the arming work")
    assert arming is not None

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        as_scheduled=True,
        unless_member_since=2,
        unless_member_arrival_since=1,
    )

    assert fired is not None
    assert await _turn_count(conversation_id) == 3


async def test_a_queued_member_message_supersedes_a_fire(db: None) -> None:
    """A member message already waiting for the live turn is a resume in flight, whatever the turn
    sequence says."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    live = await admission.invoke(workspace_id, conversation_id, agent_id, "internal work")
    assert live is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="waiting on you",
                admission_source="member",
                speaker_member_id=member_id,
                admitted_turn_id=live,
                created_at=sa.func.now(),
            )
        )

    superseded = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["waiting on you"]


async def test_a_quiet_conversation_admits_a_guarded_fire(db: None) -> None:
    workspace_id, _member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    fired = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    status, _ = await _turn_row(fired)
    assert status == "queued"
    assert dbos.enqueued == [str(fired)]


async def test_the_armed_seq_itself_does_not_supersede_a_fire(db: None) -> None:
    """The member turn the wait was armed from is the origin, not a reply that overtook it."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    origin = await admission.admit_member(workspace_id, conversation_id, "watch this", member_id)
    await _finish(origin.turn_id)

    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        unless_member_since=1,
        unless_member_arrival_since=0,
    )

    assert fired is not None
    assert fired != origin.turn_id
    assert await _turn_count(conversation_id) == 2


async def test_a_redelivered_fire_keeps_its_turn_after_a_member_speaks(db: None) -> None:
    """A crash between fire and retire re-fires the same identity, and it must converge on the
    first outcome: the key already admitted a turn, so the redelivery answers that turn rather than
    reading the member who spoke afterwards and refusing."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    fired = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
        unless_member_since=0,
        unless_member_arrival_since=0,
    )
    assert fired is not None
    folded = await admission.admit_member(workspace_id, conversation_id, "never mind", member_id)
    assert folded.turn_id == fired

    redelivered = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert redelivered == fired
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["never mind"]


async def test_a_guarded_scheduled_fire_is_superseded_the_same_way(db: None) -> None:
    """The pause fire's shape: scheduled meaning plus the guard. The member's own turn resumed the
    work, so the fire adds nothing and is refused."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await admission.admit_member(workspace_id, conversation_id, "never mind", member_id)

    superseded = await admission.invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "resume the plan",
        "pause:1",
        on_behalf_of_member_id=member_id,
        as_scheduled=True,
        unless_member_since=0,
        unless_member_arrival_since=0,
    )

    assert superseded is None
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == []


async def _cancel_turn_row(turn_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="cancelled",
                terminal=TerminalFrame(status="cancelled").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == turn_id)
        )


async def test_redispatch_founds_a_run_on_the_oldest_pending_member_arrival(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    followup = await admission.admit_member(
        workspace_id, conversation_id, "follow up", member_id, idempotency_key="send-1"
    )
    assert not followup.opened_run
    assert followup.arrival_id is not None
    await _cancel_turn_row(first.turn_id)

    founded = await admission.redispatch(workspace_id, conversation_id)

    assert founded is not None
    new_turn_id, arrival_id = founded
    assert arrival_id == followup.arrival_id
    assert new_turn_id != first.turn_id
    status, _ = await _turn_row(new_turn_id)
    assert status == "queued"
    assert str(new_turn_id) in dbos.enqueued
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.speaker_member_id).where(
                    tables.turn.c.id == new_turn_id
                )
            )
        ).one()
    assert row.inbound == "follow up"
    assert row.speaker_member_id == member_id
    assert await _queued_bodies(conversation_id) == []
    assert await admission.redispatch(workspace_id, conversation_id) is None


async def test_redispatch_stamps_a_keyless_arrival(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await admission.admit_member(workspace_id, conversation_id, "keyless follow up", member_id)
    await _cancel_turn_row(first.turn_id)

    founded = await admission.redispatch(workspace_id, conversation_id)

    assert founded is not None
    new_turn_id, arrival_id = founded
    async with workspace_tx() as connection:
        key = (
            await connection.execute(
                sa.select(tables.turn.c.idempotency_key).where(tables.turn.c.id == new_turn_id)
            )
        ).scalar_one()
    assert key == f"redispatch:{arrival_id}"


async def test_redispatch_with_nothing_pending_is_none(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await _cancel_turn_row(first.turn_id)
    assert await admission.redispatch(workspace_id, conversation_id) is None


async def test_redispatch_joins_a_live_turn_without_founding(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await admission.admit_member(
        workspace_id, conversation_id, "follow up", member_id, idempotency_key="send-1"
    )

    assert await admission.redispatch(workspace_id, conversation_id) is None

    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["follow up"]


async def test_redispatch_leaves_internal_arrivals_for_the_next_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await admission.invoke(workspace_id, conversation_id, agent_id, "a child result")
    await _cancel_turn_row(first.turn_id)

    assert await admission.redispatch(workspace_id, conversation_id) is None

    assert await _queued_bodies(conversation_id) == ["a child result"]


async def test_redispatch_founds_on_the_oldest_and_leaves_the_rest_for_the_drain(db: None) -> None:
    workspace_id, member_id, _, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(workspace_id, conversation_id, "start", member_id)
    await admission.admit_member(
        workspace_id, conversation_id, "first follow up", member_id, idempotency_key="send-1"
    )
    await admission.admit_member(
        workspace_id, conversation_id, "second follow up", member_id, idempotency_key="send-2"
    )
    await _cancel_turn_row(first.turn_id)

    founded = await admission.redispatch(workspace_id, conversation_id)

    assert founded is not None
    new_turn_id, _ = founded
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.id == new_turn_id)
            )
        ).scalar_one()
    assert inbound == "first follow up"
    assert await _queued_bodies(conversation_id) == ["second follow up"]


async def test_member_admission_stores_its_trace_for_the_turn_span(db: None, monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(o11y.trace, "get_tracer", provider.get_tracer)
    workspace_id, member_id, _, conversation_id = await _seed()
    admitted = await Admission(dbos=StubDbos(), durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, "hi", member_id
    )
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.turn.c.traceparent).where(tables.turn.c.id == admitted.turn_id)
            )
        ).scalar_one()
    admission = next(s for s in exporter.get_finished_spans() if s.name == "admission")
    assert stored.split("-")[1] == format(admission.context.trace_id, "032x")
