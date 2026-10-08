"""A member stop ends one turn: the workflow is cancelled, the row goes terminal, and the
cancelled terminal reaches the hub so a live tail ends immediately — plus the refusals that keep a
stop inside the conversation the surface authorized."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.hub import Absorbed, InProcessHub, Terminal
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.surfaces.stop import MemberStop
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, TerminalFrame


@dataclass
class _RecordingClient:
    cancelled: list[str] = field(default_factory=list)
    enqueued: list[str] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)

    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


def _stopper(client: _RecordingClient, hub: InProcessHub) -> MemberStop:
    return MemberStop(
        client=client,
        hub=hub,
        admission=Admission(dbos=client, durable_surfaces=frozenset(), hub=hub),
        sessions=None,
    )


async def _seed(status: str = "running") -> tuple[UUID, UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = None if status in ("queued", "running", "parked") else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="a",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(turn_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="x",
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id, turn_id


async def test_stop_cancels_publishes_and_reports_true(db: None) -> None:
    workspace_id, conversation_id, turn_id = await _seed()
    client = _RecordingClient()
    hub = InProcessHub()
    with ws(workspace_id):
        subscription = hub.subscribe(turn_id)
        first = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        stopped = await _stopper(client, hub).stop(workspace_id, conversation_id, turn_id)
        assert stopped.ended is True
        assert stopped.founded_turn_id is None
        assert client.cancelled == [str(turn_id)]
        _cursor, frame = await first
        assert isinstance(frame, Terminal)
        assert frame.frame.status == "cancelled"
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
        assert status == "cancelled"


async def test_stop_of_a_terminal_turn_is_a_no_op(db: None) -> None:
    workspace_id, conversation_id, turn_id = await _seed(status="done")
    client = _RecordingClient()
    hub = InProcessHub()
    with ws(workspace_id):
        stopped = await _stopper(client, hub).stop(workspace_id, conversation_id, turn_id)
    assert stopped.ended is False
    assert client.cancelled == []
    assert await hub.covers(turn_id, "1") is False


async def test_stop_refuses_a_turn_of_another_conversation(db: None) -> None:
    workspace_id, _conversation_id, turn_id = await _seed()
    client = _RecordingClient()
    with ws(workspace_id):
        with pytest.raises(ValueError, match="not a turn of conversation"):
            await _stopper(client, InProcessHub()).stop(workspace_id, uuid4(), turn_id)
    assert client.cancelled == []


async def test_stop_refuses_a_turn_outside_the_workspace(db: None) -> None:
    _workspace_id, conversation_id, turn_id = await _seed()
    other_workspace, _, _ = await _seed()
    client = _RecordingClient()
    with ws(other_workspace):
        with pytest.raises(ValueError, match="not a turn of conversation"):
            await _stopper(client, InProcessHub()).stop(other_workspace, conversation_id, turn_id)
    assert client.cancelled == []


async def test_stop_redispatches_a_pending_follow_up(db: None) -> None:
    workspace_id, conversation_id, turn_id = await _seed()
    member_id, arrival_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
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
            sa.insert(tables.inbound_message).values(
                id=arrival_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="follow up",
                admission_source=MEMBER_ADMISSION,
                speaker_member_id=member_id,
                idempotency_key="send-1",
                admitted_turn_id=turn_id,
                created_at=sa.func.now(),
            )
        )
    client = _RecordingClient()
    hub = InProcessHub()
    with ws(workspace_id):
        stopped = await _stopper(client, hub).stop(workspace_id, conversation_id, turn_id)
        assert stopped.ended is True
        async with workspace_tx() as connection:
            new_turn = (
                await connection.execute(
                    sa.select(tables.turn.c.id, tables.turn.c.status, tables.turn.c.inbound)
                    .where(
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.id != turn_id,
                    )
                    .order_by(tables.turn.c.seq.desc())
                )
            ).one()
        assert new_turn.status == "queued"
        assert new_turn.inbound == "follow up"
        assert stopped.founded_turn_id == new_turn.id
        assert client.enqueued == [str(new_turn.id)]
        subscription = hub.subscribe(new_turn.id)
        _cursor, frame = await anext(subscription)
        assert isinstance(frame, Absorbed)
        assert frame.arrivals == (arrival_id,)


async def test_stop_without_a_follow_up_founds_nothing(db: None) -> None:
    workspace_id, conversation_id, turn_id = await _seed()
    client = _RecordingClient()
    with ws(workspace_id):
        stopped = await _stopper(client, InProcessHub()).stop(
            workspace_id, conversation_id, turn_id
        )
    assert stopped.ended is True
    assert stopped.founded_turn_id is None
    assert client.enqueued == []
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()
    assert count == 1
