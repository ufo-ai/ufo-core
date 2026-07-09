import asyncio
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS

from ufo import jobs as jobs_module
from ufo.db import owner_tx, workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore
from ufo.ext.manifest import JobSpec
from ufo.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.schema import tables
from ufo.workspace import ws

FIRE_TIMEOUT_SECONDS = 25
MARKER_KEY = "fired"
MARKER_VALUE = {"ran": True}


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _runner(core_jobs: tuple[JobSpec, ...]) -> JobRunner:
    return JobRunner(bindings=bindings_from((), core_jobs))


async def _write_marker(context: ExtensionContext) -> None:
    """A core-sweep-shaped handler: it finds its workspaces through the one owner_tx read and writes
    the marker scoped to each, so it runs correctly on the durable fire path where no workspace is
    bound — the same self-enumerate-then-`with ws(...)` shape the real sweeps use."""
    async with owner_tx() as connection:
        rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
    for row in rows:
        with ws(row.id):
            await context.store.put(MARKER_KEY, MARKER_VALUE)


async def _await_marker(scoped: ScopedStore) -> object:
    async with asyncio.timeout(FIRE_TIMEOUT_SECONDS):
        while True:
            value = await scoped.get(MARKER_KEY)
            if value is not None:
                return value
            await asyncio.sleep(0.2)


async def test_recurring_core_job_registers_at_boot_and_fires(
    db: None, dbos_launched: object
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:tick"
    spec = JobSpec(name="tick", schedule="* * * * * *", handler=_write_marker)
    runner = _runner((spec,))
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        runner.launch()
        assert key in {schedule["schedule_name"] for schedule in DBOS.list_schedules()}
        with ws(workspace_id):
            assert await _await_marker(scoped) == MARKER_VALUE
    finally:
        DBOS.delete_schedule(key)
        jobs_module._firing = None


async def test_one_shot_core_job_fires_once_at_boot(db: None, dbos_launched: object) -> None:
    workspace_id = await _workspace()
    spec = JobSpec(name="boot", schedule=None, handler=_write_marker)
    runner = _runner((spec,))
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        runner.launch()
        with ws(workspace_id):
            assert await _await_marker(scoped) == MARKER_VALUE
    finally:
        jobs_module._firing = None


async def test_fire_dispatches_the_handler_once_not_per_workspace(db: None) -> None:
    """`fire` hands the handler off a single time — it no longer loops the fleet binding each
    workspace. Two workspaces exist, yet a counting handler fired once records exactly one call:
    the dispatcher dispatches once and a core sweep self-selects the workspaces it touches, so no
    transaction runs against an idle workspace on every tick."""
    await _workspace()
    await _workspace()
    calls = 0

    async def _count(context: ExtensionContext) -> None:
        nonlocal calls
        calls += 1

    spec = JobSpec(name="count", schedule="* * * * * *", handler=_count)
    await _runner((spec,)).fire(f"{CORE_EXTENSION}:count")
    assert calls == 1
