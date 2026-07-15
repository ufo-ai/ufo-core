from dataclasses import dataclass, field
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.loop.engine import _claim_turn
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, TurnContext
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
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "first message", member_id, "C:1"
    )
    second = await admission.admit_member(
        workspace_id,
        conversation_id,
        agent_id,
        "second message",
        member_id,
        "C:2",
        TurnContext(sender="Pat Doe", timezone="UTC"),
    )
    assert second == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["second message"]
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(tables.turn.c.id == first)
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
    assert queued.admitted_turn_id == first
    assert dbos.enqueued == [str(first)]


async def test_message_while_a_turn_runs_joins_its_inbound_queue(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    assert await _claim_turn(first, str(first))
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["two"]


async def test_message_redelivery_joins_the_queued_row(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second == first
    assert redelivered == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["two"]


async def test_every_admission_source_joins_the_live_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "job prompt", "job:1")
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "hello", member_id, "C:2"
    )
    third = await admission.invoke(workspace_id, conversation_id, agent_id, "another job")
    assert second == first
    assert third == first
    assert await _turn_count(conversation_id) == 1
    assert await _queued_bodies(conversation_id) == ["hello", "another job"]


async def test_a_reject_cap_stops_arrivals_at_the_boundary(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
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
                turn_id=first,
                dimension="tokens",
                amount=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second != first
    assert await _queued_bodies(conversation_id) == []
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == second)
            )
        ).scalar_one()
    assert status == "cancelled"


async def test_message_after_a_terminal_turn_starts_a_new_turn(db: None) -> None:
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    admission = Admission(dbos=StubDbos(), durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    await _finish(first)
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second != first
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
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second == first
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                status="failed",
                terminal=TerminalFrame(status="failed", error_class="Boom").model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert redelivered != first
    async with workspace_tx() as connection:
        refounded = (
            await connection.execute(
                sa.select(
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                    tables.turn.c.idempotency_key,
                ).where(tables.turn.c.id == redelivered)
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
    assert str(redelivered) in dbos.enqueued


async def test_fold_onto_a_parked_turn_dispatches_its_resume(db: None) -> None:
    """Caps were raised since the park: a fresh message folds onto the parked turn AND resumes it
    now, under a fresh workflow id, instead of leaving the member staring at the park notice."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="parked", updated_at=sa.func.now())
            .where(tables.turn.c.id == first)
        )
    second = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "two", member_id, "C:2"
    )
    assert second == first
    assert await _queued_bodies(conversation_id) == ["two"]
    async with workspace_tx() as connection:
        resumed_status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == first)
            )
        ).scalar_one()
    assert resumed_status == "queued"
    assert dbos.enqueued == [str(first), str(first)]
    assert dbos.workflow_ids[0] == str(first)
    assert dbos.workflow_ids[1] != str(first)


async def test_redelivery_of_a_fold_resumed_turn_retries_under_a_fresh_workflow_id(
    db: None,
) -> None:
    """The fold's own enqueue was deferred, so the redelivered message must retry the dispatch —
    but the run that parked the turn consumed its own workflow id, so a retry riding it would
    dedup against the completed workflow while re-stamping the offer, holding the sweep off a
    grace window at a time."""
    workspace_id, member_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                running_attempt=uuid4().hex,
                dispatch_enqueued_at=None,
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == first)
        )
    redelivered = await admission.admit_member(
        workspace_id, conversation_id, agent_id, "one", member_id, "C:1"
    )
    assert redelivered == first
    assert dbos.enqueued == [str(first), str(first)]
    assert dbos.workflow_ids[0] == str(first)
    assert dbos.workflow_ids[1] != str(first)
