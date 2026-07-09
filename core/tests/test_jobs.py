import asyncio
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS

from ufo import jobs as jobs_module
from ufo.db import workspace_tx
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


async def test_fire_fans_a_core_job_across_every_workspace(db: None) -> None:
    """One `fire` enumerates every workspace through owner_tx and runs the handler bound to each, so
    a marker-writing core job lands its marker in BOTH workspaces' scoped stores — the witness the
    dispatcher fans a job across the fleet rather than a single workspace."""
    ws_a = await _workspace()
    ws_b = await _workspace()
    spec = JobSpec(name="tick", schedule="* * * * * *", handler=_write_marker)
    await _runner((spec,)).fire(f"{CORE_EXTENSION}:tick")
    with ws(ws_a):
        assert await ScopedStore(extension=CORE_EXTENSION).get(MARKER_KEY) == MARKER_VALUE
    with ws(ws_b):
        assert await ScopedStore(extension=CORE_EXTENSION).get(MARKER_KEY) == MARKER_VALUE
