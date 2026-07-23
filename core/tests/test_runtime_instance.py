import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo import runtime_instance
from ufo.db import workspace_tx
from ufo.runtime_instance import (
    STALE_AFTER_SECONDS,
    CancelReconciler,
    ExecutorRecovery,
    Heartbeat,
    record_fleet_seat,
)
from ufo.schema import tables
from ufo.schema.records import TerminalFrame


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _insert_instance(workspace_id: UUID, heartbeat_age_seconds: float) -> UUID:
    instance_id = uuid4()
    when = datetime.now(UTC) - timedelta(seconds=heartbeat_age_seconds)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance).values(
                id=instance_id,
                workspace_id=workspace_id,
                heartbeat_at=when,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return instance_id


async def _row_present(instance_id: UUID) -> bool:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.id).where(
                    tables.runtime_instance.c.id == instance_id
                )
            )
        ).one_or_none()
    return row is not None


async def _row_live(instance_id: UUID) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.id).where(
                    tables.runtime_instance.c.id == instance_id,
                    tables.runtime_instance.c.heartbeat_at >= cutoff,
                )
            )
        ).one_or_none()
    return row is not None


async def test_a_transient_error_does_not_kill_the_heartbeat_loop(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    instance_id = await _insert_instance(
        workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 20
    )
    real_tx = runtime_instance.owner_tx
    ticks = {"n": 0}

    def flaky_tx() -> object:
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise sa.exc.SQLAlchemyError("transient connection reset")
        return real_tx()

    monkeypatch.setattr(runtime_instance, "owner_tx", flaky_tx)
    monkeypatch.setattr(runtime_instance, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    heartbeat = Heartbeat(instance_id=instance_id)
    task = asyncio.create_task(heartbeat.run())
    try:
        async with asyncio.timeout(5):
            while True:
                if await _row_live(instance_id):
                    break
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert ticks["n"] >= 2
    assert await _row_live(instance_id)


async def test_heartbeat_refreshes_a_stale_row_and_retire_removes_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    instance_id = await _insert_instance(
        workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 20
    )
    assert not await _row_live(instance_id)
    monkeypatch.setattr(runtime_instance, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    heartbeat = Heartbeat(instance_id=instance_id)
    task = asyncio.create_task(heartbeat.run())
    try:
        async with asyncio.timeout(5):
            while True:
                if await _row_live(instance_id):
                    break
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert await _row_live(instance_id)
    await heartbeat.retire()
    assert not await _row_present(instance_id)


async def test_fleet_seat_has_no_workspace_and_counts_as_a_live_executor(db: None) -> None:
    """The shared fleet's seat: recorded with no workspace (it serves them all), refreshed by the
    same heartbeat, and read as live by the executor-recovery sweep — so a booting fleet process is
    never swept as stranded and its retirement frees the seat like any instance's."""
    instance_id = uuid4()
    await record_fleet_seat(instance_id)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.workspace_id).where(
                    tables.runtime_instance.c.id == instance_id
                )
            )
        ).one()
    assert row.workspace_id is None
    assert str(instance_id) in await ExecutorRecovery()._live_executors()
    heartbeat = Heartbeat(instance_id=instance_id)
    await heartbeat.beat()
    assert await _row_live(instance_id)
    await heartbeat.retire()
    assert not await _row_present(instance_id)
    assert str(instance_id) not in await ExecutorRecovery()._live_executors()


@dataclass
class _RecordingClient:
    cancelled: list[str] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


async def _agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
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
    return agent_id


async def _turn(workspace_id: UUID, agent_id: UUID, status: str, parent_id: UUID | None) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = None if status in ("queued", "running", "parked") else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
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
                parent_turn_id=parent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _turn_status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_cancel_reconciler_cancels_turns_left_live_under_a_cancelled_parent(db: None) -> None:
    """The backstop: a turn left live under a cancelled parent — a fault mid-cancel, or an orphan
    DBOS recovery re-dispatched — is cancelled, along with its own descendants; a turn under a
    still-live parent is left untouched."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    cancelled_parent = await _turn(workspace_id, agent_id, "cancelled", None)
    orphan = await _turn(workspace_id, agent_id, "running", cancelled_parent)
    grandchild = await _turn(workspace_id, agent_id, "running", orphan)
    live_parent = await _turn(workspace_id, agent_id, "running", None)
    kept = await _turn(workspace_id, agent_id, "running", live_parent)
    client = _RecordingClient()
    await CancelReconciler(client=client).sweep()
    assert await _turn_status(orphan) == "cancelled"
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(kept) == "running"
    assert set(client.cancelled) == {str(orphan), str(grandchild)}
    idle = _RecordingClient()
    await CancelReconciler(client=idle).sweep()
    assert idle.cancelled == []


async def test_cancel_reconciler_reaches_a_live_turn_under_a_done_intermediate(db: None) -> None:
    """The deep-orphan case: a cancelled root R has a child C that finished `done` on its own while
    a grandchild G it spawned is still running. G's immediate parent is `done`, not cancelled, but G
    has a cancelled ancestor (R), so the ancestor-climbing sweep still reaches and cancels it — not
    only direct children of the cancelled turn."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    root = await _turn(workspace_id, agent_id, "cancelled", None)
    done_child = await _turn(workspace_id, agent_id, "done", root)
    grandchild = await _turn(workspace_id, agent_id, "running", done_child)
    great_grandchild = await _turn(workspace_id, agent_id, "running", grandchild)
    client = _RecordingClient()
    await CancelReconciler(client=client).sweep()
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(great_grandchild) == "cancelled"
    assert await _turn_status(done_child) == "done"
    assert set(client.cancelled) == {str(grandchild), str(great_grandchild)}
