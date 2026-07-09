import asyncio
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS

from ufo import jobs as jobs_module
from ufo.candidates import owner_candidates
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore
from ufo.ext.manifest import JobSpec
from ufo.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.schema import tables
from ufo.workspace import ws, ws_current

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


def _every_workspace() -> object:
    """A test candidate naming every workspace — the boot jobs here have no work table of their
    own, so they run on each seeded workspace. Real jobs name only the workspaces holding work."""
    return owner_candidates(sa.select(tables.workspace.c.id).distinct())


async def _write_marker(context: ExtensionContext) -> None:
    """A handler on the durable fire path: it writes its marker to the workspace the dispatcher
    bound — never self-enumerating and never unbound, so `ws_current()` (through the ScopedStore)
    always resolves the workspace `fire` opened around it."""
    await context.store.put(MARKER_KEY, MARKER_VALUE)


async def _await_marker(scoped: ScopedStore) -> object:
    async with asyncio.timeout(FIRE_TIMEOUT_SECONDS):
        while True:
            value = await scoped.get(MARKER_KEY)
            if value is not None:
                return value
            await asyncio.sleep(0.2)


async def test_fire_binds_each_candidate_workspace_and_never_runs_unbound(db: None) -> None:
    """The invariant: `fire` opens `with ws(id)` for each workspace the candidate names and runs the
    handler scoped to it — never once unbound. Two workspaces exist but the selector names only
    ws_a, so the recording handler observes exactly ws_a as its bound workspace (a call outside a
    scope would have raised in `ws_current()`), and ws_b, not a candidate, is never opened."""
    ws_a = await _workspace()
    await _workspace()
    observed: list[UUID] = []

    async def _record(context: ExtensionContext) -> None:
        observed.append(ws_current().workspace_id)

    async def _only_a() -> tuple[UUID, ...]:
        return (ws_a,)

    spec = JobSpec(name="probe", schedule="* * * * * *", handler=_record, candidates=_only_a)
    await _runner((spec,)).fire(f"{CORE_EXTENSION}:probe")
    assert observed == [ws_a]


async def test_fire_with_empty_candidates_never_invokes_the_handler(db: None) -> None:
    """A selector that names no workspace fires the handler zero times — there is no unbound
    fallthrough, so a job with nothing to do runs no handler at all."""
    await _workspace()
    calls = 0

    async def _count(context: ExtensionContext) -> None:
        nonlocal calls
        calls += 1

    async def _none() -> tuple[UUID, ...]:
        return ()

    spec = JobSpec(name="idle", schedule="* * * * * *", handler=_count, candidates=_none)
    await _runner((spec,)).fire(f"{CORE_EXTENSION}:idle")
    assert calls == 0


async def test_recurring_core_job_registers_at_boot_and_fires(
    db: None, dbos_launched: object
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:tick"
    spec = JobSpec(
        name="tick", schedule="* * * * * *", handler=_write_marker, candidates=_every_workspace()
    )
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
    spec = JobSpec(name="boot", schedule=None, handler=_write_marker, candidates=_every_workspace())
    runner = _runner((spec,))
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        runner.launch()
        with ws(workspace_id):
            assert await _await_marker(scoped) == MARKER_VALUE
    finally:
        jobs_module._firing = None
