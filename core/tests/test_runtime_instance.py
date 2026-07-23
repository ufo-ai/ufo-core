import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo import runtime_instance
from ufo.db import workspace_tx
from ufo.runtime_instance import (
    STALE_AFTER_SECONDS,
    ExecutorRecovery,
    Heartbeat,
    record_fleet_seat,
)
from ufo.schema import tables


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
