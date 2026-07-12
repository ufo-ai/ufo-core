from dataclasses import dataclass, field
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.loop.engine import _claim_turn
from ufo.schema import tables
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


async def _seed() -> tuple[UUID, UUID, UUID]:
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
    return workspace_id, agent_id, conversation_id


async def _turn_count(conversation_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()


async def test_repeated_delivery_dedups_to_one_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await admission.invoke(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    assert first == second
    assert await _turn_count(conversation_id) == 1
    assert dbos.enqueued == [str(first), str(first)]
    assert dbos.workflow_ids == [str(first), str(first)]


async def test_idempotency_key_keeps_the_first_body(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
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


async def test_distinct_keys_allocate_sequential_turns(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "one", "C:1")
    second = await admission.invoke(workspace_id, conversation_id, agent_id, "two", "C:2")
    assert first != second
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.turn.c.seq,
                    tables.turn.c.status,
                    tables.turn.c.dispatch_enqueued_at,
                )
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert [(row.seq, row.status) for row in rows] == [(1, "queued"), (2, "queued")]
    assert rows[0].dispatch_enqueued_at is not None
    assert rows[1].dispatch_enqueued_at is None
    assert dbos.enqueued == [str(first)]


async def test_keyless_admission_enqueues_each_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.invoke(workspace_id, conversation_id, agent_id, "one")
    second = await admission.invoke(workspace_id, conversation_id, agent_id, "two")
    assert first != second
    assert await _turn_count(conversation_id) == 2
    assert dbos.enqueued == [str(first)]


async def test_enqueue_failure_keeps_the_turn_queued_for_dispatch(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
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
    workspace_id, agent_id, conversation_id = await _seed()
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
