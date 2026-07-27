from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.cancellation import cancel_one_turn
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.schema.records import TerminalFrame


@dataclass
class _RecordingClient:
    """Records each turn id a cancel targets, so a test reads back what the primitive asked DBOS to
    cancel — never asserting DBOS itself. cancel_workflow_async is a no-op here, matching the real
    client's conditional-update behavior for an absent or already-complete workflow."""

    cancelled: list[str] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


async def _workspace_agent() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
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
    return workspace_id, agent_id


async def _turn(workspace_id: UUID, agent_id: UUID, status: str) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = None if status in ("queued", "running", "parked") else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
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
    return turn_id


async def _status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_cancel_one_turn_cancels_the_workflow_then_commits_the_terminal(db: None) -> None:
    """A live turn: cancel_one_turn asks DBOS to cancel the workflow and commits a cancelled
    terminal, returning True."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")
    client = _RecordingClient()
    assert await cancel_one_turn(client, turn_id) is True
    assert client.cancelled == [str(turn_id)]
    assert await _status(turn_id) == "cancelled"
    async with workspace_tx() as connection:
        terminal = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
    assert TerminalFrame.model_validate(terminal).status == "cancelled"


async def test_cancel_one_turn_cancels_a_queued_turn_with_no_live_workflow(db: None) -> None:
    """A queued turn never enqueued has no live workflow — the cancel is a no-op UPDATE, yet the row
    is still committed cancelled, so nothing downstream re-dispatches it."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "queued")
    assert await cancel_one_turn(_RecordingClient(), turn_id) is True
    assert await _status(turn_id) == "cancelled"


async def test_cancel_one_turn_leaves_a_terminal_turn_untouched(db: None) -> None:
    """A turn that already reached its own terminal (a cancel racing its done commit) is not
    disturbed: no workflow cancel is requested and it returns False."""
    workspace_id, agent_id = await _workspace_agent()
    done = await _turn(workspace_id, agent_id, "done")
    client = _RecordingClient()
    assert await cancel_one_turn(client, done) is False
    assert client.cancelled == []
    assert await _status(done) == "done"


async def test_cancel_one_turn_leaves_the_row_live_when_the_workflow_cancel_faults(
    db: None,
) -> None:
    """Cancel-before-commit: a DBOS/DB fault cancelling the workflow propagates and the row is NOT
    committed cancelled — it stays live for a retry, never a cancelled row whose workflow was never
    told to stop. This is the guard that fails if the commit is moved before the cancel."""
    workspace_id, agent_id = await _workspace_agent()
    turn_id = await _turn(workspace_id, agent_id, "running")

    @dataclass
    class _FaultyClient:
        async def cancel_workflow_async(self, workflow_id: str) -> None:
            raise sa.exc.SQLAlchemyError("cancel failed")

    with pytest.raises(sa.exc.SQLAlchemyError):
        await cancel_one_turn(_FaultyClient(), turn_id)
    assert await _status(turn_id) == "running"
