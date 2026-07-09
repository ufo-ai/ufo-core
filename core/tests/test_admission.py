from dataclasses import dataclass, field
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.surfaces.admission import Admission


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


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
    first = await admission.admit(workspace_id, conversation_id, agent_id, "hi", "C0000001:1.5")
    second = await admission.admit(
        workspace_id, conversation_id, agent_id, "hi again", "C0000001:1.5"
    )
    assert first == second
    assert await _turn_count(conversation_id) == 1
    assert dbos.enqueued == [str(first)]


async def test_distinct_keys_allocate_sequential_turns(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit(workspace_id, conversation_id, agent_id, "one", "C:1")
    second = await admission.admit(workspace_id, conversation_id, agent_id, "two", "C:2")
    assert first != second
    async with workspace_tx() as connection:
        seqs = (
            await connection.execute(
                sa.select(tables.turn.c.seq)
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq)
            )
        ).scalars()
    assert list(seqs) == [1, 2]
    assert dbos.enqueued == [str(first), str(second)]


async def test_keyless_admission_enqueues_each_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    first = await admission.admit(workspace_id, conversation_id, agent_id, "one")
    second = await admission.admit(workspace_id, conversation_id, agent_id, "two")
    assert first != second
    assert await _turn_count(conversation_id) == 2
    assert dbos.enqueued == [str(first), str(second)]
