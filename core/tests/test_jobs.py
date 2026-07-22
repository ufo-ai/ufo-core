import asyncio
import logging
import threading
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
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
    return owner_candidates(lambda: sa.select(tables.workspace.c.id).distinct())


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
    runner = _runner((spec,))
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:probe"):
        await runner.fire(f"{CORE_EXTENSION}:probe", workspace_id)
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
    runner = _runner((spec,))
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:idle"):
        await runner.fire(f"{CORE_EXTENSION}:idle", workspace_id)
    assert calls == 0


async def test_fire_logs_the_exact_failed_job_and_reraises(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:broken"

    async def _fail(context: ExtensionContext) -> None:
        raise TimeoutError("database connect timed out")

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="broken", schedule="* * * * * *", handler=_fail, candidates=_candidate)
    with (
        caplog.at_level(logging.ERROR, logger="ufo"),
        pytest.raises(TimeoutError, match="database connect timed out"),
    ):
        await _runner((spec,)).fire(key, workspace_id)

    record = next(record for record in caplog.records if record.message == "jobs.failed")
    assert record.levelno == logging.ERROR
    assert record.ufo == {
        "workspace_id": str(workspace_id),
        "job": key,
        "error_class": "TimeoutError",
    }


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
        schedule = next(s for s in DBOS.list_schedules() if s["schedule_name"] == key)
        assert schedule.get("queue_name") is None
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


@dataclass
class _HeldJob:
    """A handler that counts its executions and holds each one open until released — the live
    predecessor the dedup contract is asserted against. Threading primitives because the handler
    runs on DBOS's background loop, not the test's."""

    started: int = 0
    release: threading.Event = field(default_factory=threading.Event)
    milestones: tuple[threading.Event, threading.Event] = field(
        default_factory=lambda: (threading.Event(), threading.Event())
    )

    async def hold(self, context: ExtensionContext) -> None:
        self.started += 1
        if self.started <= len(self.milestones):
            self.milestones[self.started - 1].set()
        await asyncio.to_thread(self.release.wait)

    async def await_started(self, count: int) -> None:
        assert await asyncio.to_thread(self.milestones[count - 1].wait, FIRE_TIMEOUT_SECONDS)


def _skips(caplog: pytest.LogCaptureFixture, event: str, **fields: str) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records if record.getMessage() == event and record.ufo == fields
    ]


async def test_tick_skips_while_predecessor_runs_and_resumes_after_terminal(
    db: None, dbos_launched: object, caplog: pytest.LogCaptureFixture
) -> None:
    """A tick landing while the job's previous execution is still live starts nothing — the
    deduplication id (the job key) is held from enqueue to terminal, and the absorbed tick logs
    `jobs.tick_skipped` — and the first tick after completion starts the next execution, so ticks
    are absorbed, never stacked and never lost."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:slow"
    job = _HeldJob()
    spec = JobSpec(
        name="slow", schedule="* * * * * *", handler=job.hold, candidates=_every_workspace()
    )
    runner = _runner((spec,))
    try:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            runner.launch()
            await job.await_started(1)
            await asyncio.sleep(3.5)
            assert job.started == 1
            job.release.set()
            await job.await_started(2)
        assert _skips(caplog, "jobs.tick_skipped", key=key, workspace_id=str(workspace_id))
    finally:
        job.release.set()
        DBOS.delete_schedule(key)
        jobs_module._firing = None


async def test_one_shot_twin_boots_start_one_run(
    db: None, dbos_launched: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Two replicas boot the same one-shot; the deduplication id collapses the twin enqueue while
    the first run is live — logged as `jobs.enqueue_skipped` — so exactly one execution starts."""
    await _workspace()
    key = f"{CORE_EXTENSION}:twin"
    job = _HeldJob()
    spec = JobSpec(name="twin", schedule=None, handler=job.hold, candidates=_every_workspace())
    runner = _runner((spec,))
    try:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            runner.launch()
            runner.launch()
        assert _skips(caplog, "jobs.enqueue_skipped", key=key)
        await job.await_started(1)
        await asyncio.sleep(2)
        assert job.started == 1
    finally:
        job.release.set()
        jobs_module._firing = None


async def test_slow_workspace_does_not_starve_its_neighbors(
    db: None, dbos_launched: object
) -> None:
    """The dedup instance is (job, workspace): workspace A's still-running execution absorbs only
    A's ticks, so B's executions keep landing while A holds."""
    ws_a = await _workspace()
    await _workspace()
    key = f"{CORE_EXTENSION}:mixed"
    a_started = threading.Event()
    release = threading.Event()
    b_second = threading.Event()
    b_count = 0

    async def _hold_a(context: ExtensionContext) -> None:
        nonlocal b_count
        if ws_current().workspace_id == ws_a:
            a_started.set()
            await asyncio.to_thread(release.wait)
            return
        b_count += 1
        if b_count == 2:
            b_second.set()

    spec = JobSpec(
        name="mixed", schedule="* * * * * *", handler=_hold_a, candidates=_every_workspace()
    )
    runner = _runner((spec,))
    try:
        runner.launch()
        assert await asyncio.to_thread(a_started.wait, FIRE_TIMEOUT_SECONDS)
        assert await asyncio.to_thread(b_second.wait, FIRE_TIMEOUT_SECONDS)
        assert not release.is_set()
    finally:
        release.set()
        DBOS.delete_schedule(key)
        jobs_module._firing = None
