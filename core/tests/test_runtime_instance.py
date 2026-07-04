import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from selfhost import runtime_instance
from selfhost.config import BlobConfig, Config, DatabaseConfig, HubConfig
from selfhost.db import workspace_tx
from selfhost.runtime_instance import (
    STALE_AFTER_SECONDS,
    BootGuard,
    Heartbeat,
    fingerprint_of,
)
from selfhost.schema import tables

PRODUCTION_FINGERPRINT = "db=postgres;blob=s3;hub=in_process"


def _dev_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
    )


def _production_config() -> Config:
    return Config(
        database=DatabaseConfig(url="postgresql+asyncpg://u:p@h:5432/db"),
        blob=BlobConfig(backend="s3", bucket="b"),
    )


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
                started_at=when,
                heartbeat_at=when,
                fingerprint=PRODUCTION_FINGERPRINT,
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


def _shared_hub_config() -> Config:
    return Config(
        database=DatabaseConfig(url="postgresql+asyncpg://u:p@h:5432/db"),
        blob=BlobConfig(backend="s3", bucket="b"),
        hub=HubConfig(backend="redis", url="redis://cache:6379/0"),
    )


def test_fingerprint_names_the_backend_selection() -> None:
    assert fingerprint_of(_production_config()) == PRODUCTION_FINGERPRINT
    assert fingerprint_of(_dev_config()) == "db=sqlite;blob=filesystem;hub=in_process"
    assert fingerprint_of(_shared_hub_config()) == "db=postgres;blob=s3;hub=redis"


async def test_first_instance_admits_and_records_its_row(db: None) -> None:
    workspace_id = await _workspace()
    guard = BootGuard(config=_dev_config(), workspace_id=workspace_id, instance_id=uuid4())
    await guard.admit()
    assert await _row_present(guard.instance_id)


async def test_dev_default_instance_refuses_when_a_peer_is_live(db: None) -> None:
    workspace_id = await _workspace()
    await _insert_instance(workspace_id, heartbeat_age_seconds=0)
    guard = BootGuard(config=_dev_config(), workspace_id=workspace_id, instance_id=uuid4())
    with pytest.raises(RuntimeError, match="in-process hub"):
        await guard.admit()
    assert not await _row_present(guard.instance_id)


async def test_a_stale_peer_does_not_block_a_dev_default_instance(db: None) -> None:
    workspace_id = await _workspace()
    await _insert_instance(workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 10)
    guard = BootGuard(config=_dev_config(), workspace_id=workspace_id, instance_id=uuid4())
    await guard.admit()
    assert await _row_present(guard.instance_id)


async def test_a_second_live_instance_is_refused_even_with_production_backends(db: None) -> None:
    workspace_id = await _workspace()
    await _insert_instance(workspace_id, heartbeat_age_seconds=0)
    guard = BootGuard(config=_production_config(), workspace_id=workspace_id, instance_id=uuid4())
    with pytest.raises(RuntimeError, match="in-process hub"):
        await guard.admit()
    assert not await _row_present(guard.instance_id)


async def test_a_shared_hub_lifts_the_single_instance_refusal(db: None) -> None:
    workspace_id = await _workspace()
    await _insert_instance(workspace_id, heartbeat_age_seconds=0)
    guard = BootGuard(
        config=_shared_hub_config(), workspace_id=workspace_id, instance_id=uuid4()
    )
    await guard.admit()
    assert await _row_present(guard.instance_id)


async def test_a_shared_hub_with_a_dev_default_db_or_blob_still_refuses(db: None) -> None:
    workspace_id = await _workspace()
    await _insert_instance(workspace_id, heartbeat_age_seconds=0)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
        blob=BlobConfig(backend="filesystem", root=Path()),
        hub=HubConfig(backend="redis", url="redis://cache:6379/0"),
    )
    guard = BootGuard(config=config, workspace_id=workspace_id, instance_id=uuid4())
    with pytest.raises(RuntimeError, match="sqlite database"):
        await guard.admit()
    assert not await _row_present(guard.instance_id)


async def test_two_instances_booting_at_once_admit_exactly_one(db: None) -> None:
    workspace_id = await _workspace()
    config = Config(
        database=DatabaseConfig(url="postgresql+asyncpg://u:p@h:5432/db"),
        blob=BlobConfig(backend="s3", bucket="b"),
    )
    first = BootGuard(config=config, workspace_id=workspace_id, instance_id=uuid4())
    second = BootGuard(config=config, workspace_id=workspace_id, instance_id=uuid4())
    results = await asyncio.gather(first.admit(), second.admit(), return_exceptions=True)
    admitted = [
        guard for guard, outcome in zip((first, second), results, strict=True) if outcome is None
    ]
    refused = [outcome for outcome in results if isinstance(outcome, RuntimeError)]
    assert len(admitted) == 1
    assert len(refused) == 1
    assert await _row_present(admitted[0].instance_id)


async def test_a_peer_in_another_workspace_never_blocks(db: None) -> None:
    workspace_id = await _workspace()
    other = await _workspace()
    await _insert_instance(other, heartbeat_age_seconds=0)
    guard = BootGuard(config=_dev_config(), workspace_id=workspace_id, instance_id=uuid4())
    await guard.admit()
    assert await _row_present(guard.instance_id)


async def test_a_transient_error_does_not_kill_the_heartbeat_loop(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    instance_id = await _insert_instance(
        workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 20
    )
    real_tx = runtime_instance.workspace_tx
    ticks = {"n": 0}

    def flaky_tx() -> object:
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise sa.exc.SQLAlchemyError("transient connection reset")
        return real_tx()

    monkeypatch.setattr(runtime_instance, "workspace_tx", flaky_tx)
    monkeypatch.setattr(runtime_instance, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    heartbeat = Heartbeat(instance_id=instance_id, workspace_id=workspace_id)
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
    heartbeat = Heartbeat(instance_id=instance_id, workspace_id=workspace_id)
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
