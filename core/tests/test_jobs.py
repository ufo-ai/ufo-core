import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOS
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ufo import o11y
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore
from ufo.ext.manifest import JobSpec
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import Message, ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.o11y import BACKGROUND_PROFILE
from ufo.runtime import jobs as jobs_module
from ufo.runtime.candidates import owner_candidates
from ufo.runtime.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.workspace import ws, ws_current

FIRE_TIMEOUT_SECONDS = 25
MARKER_KEY = "fired"
MARKER_VALUE = {"ran": True}
DORMANT_CRON = "0 0 5 * * *"
BACKGROUND_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True)
class _StubModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="one summary")
        yield Usage(input_tokens=9, output_tokens=4)


def _stub_registry() -> ModelRegistry:
    """The deploy registry with every core spec served by one stub client, so a job's model call
    runs the real seam — client resolution, pricing, metering — without a provider."""
    return ModelRegistry(
        specs={
            spec.id: replace(spec, client=lambda spec, key: _StubModel(), key_slot="", key_env="")
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-5",
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


async def test_a_job_runs_its_own_model_calls_on_the_background_jobs_model(db: None) -> None:
    """A job's `ctx.model` is the deploy registry with its default replaced by the background-jobs
    model, so the one-shot a handler runs — fact extraction, consolidation, a chat title — calls and
    bills the cheap model. The deploy registry the runner holds is left as it is: a member turn
    resolves `auto` through it and keeps `auto_model`."""
    workspace_id = await _workspace()
    registry = ModelRegistry(specs={}, pricing=CORE_PRICING, auto_model="claude-opus-5")
    seen: list[str] = []

    async def _record_model(context: ExtensionContext) -> None:
        assert context.model is not None
        seen.append(context.model.model)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(
        name="titles", schedule="* * * * * *", handler=_record_model, candidates=_candidate
    )
    runner = JobRunner(
        bindings=bindings_from((), (spec,)), registry=registry, background_model="gpt-5.6-luna"
    )
    await runner.fire(f"{CORE_EXTENSION}:titles", workspace_id)
    assert seen == ["gpt-5.6-luna"]
    assert registry.auto_model == "claude-opus-5"


async def test_a_job_model_call_meters_its_tokens_and_latency_under_the_key_that_fired(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every background job shares one model, so the model alone cannot say which work spent the
    tokens — the key the dispatcher fired is what the series carry. The handler never passes it: the
    label comes off the fire path, so a job cannot mislabel its own spend."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:summaries"
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})

    async def _summarize(context: ExtensionContext) -> None:
        assert context.model is not None
        await context.model.turn(
            ModelRequest(
                model="auto",
                system="summarize",
                messages=(Message(role="user", content="two facts"),),
                max_tokens=64,
                conversation_cache_ttl="5m",
            )
        )

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(
        name="summaries", schedule="* * * * * *", handler=_summarize, candidates=_candidate
    )
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        registry=_stub_registry(),
        background_model=BACKGROUND_MODEL,
    )
    await runner.fire(key, workspace_id)

    data = reader.get_metrics_data()
    assert data is not None
    points = {
        metric.name: metric.data.data_points
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {
        (point.attributes["job"], point.attributes["kind"], point.value)
        for point in points["ufo.model_round_tokens_total"]
    } == {(key, "input", 9), (key, "output", 4)}
    assert [
        (point.attributes["job"], point.attributes["profile"], point.attributes["model"])
        for point in points["ufo.model_round_ms"]
    ] == [(key, BACKGROUND_PROFILE, BACKGROUND_MODEL)]


async def test_a_job_that_needs_the_deploy_model_keeps_it(db: None) -> None:
    """One call site does not fit the cheap model: a self-improvement replay re-sends a whole
    archived transcript, compacted against the deploy default's context window and bounded by
    nothing else. Such a job declares `needs_deploy_model` and its seam stays on `auto_model`."""
    workspace_id = await _workspace()
    registry = ModelRegistry(specs={}, pricing=CORE_PRICING, auto_model="claude-opus-5")
    seen: list[str] = []

    async def _record_model(context: ExtensionContext) -> None:
        assert context.model is not None
        seen.append(context.model.model)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(
        name="replay",
        schedule="* * * * * *",
        handler=_record_model,
        candidates=_candidate,
        needs_deploy_model=True,
    )
    runner = JobRunner(
        bindings=bindings_from((), (spec,)), registry=registry, background_model="gpt-5.6-luna"
    )
    await runner.fire(f"{CORE_EXTENSION}:replay", workspace_id)
    assert seen == ["claude-opus-5"]


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


async def test_tick_skips_a_key_this_process_registers_no_job_for(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """`apply_schedules` upserts and never deletes, so a schedule outlives the job that wrote it —
    an extension uninstalled, or a job only a newer peer registers. The binding set is discovered
    per process at boot, so such a tick is a normal condition: it names itself and returns, never
    reaching the candidate selector. Deleting the schedule instead would let an older peer silently
    drop one a newer peer owns, since one schedule table serves every process."""
    vanished = f"{CORE_EXTENSION}:uninstalled"

    async def _never() -> tuple[UUID, ...]:
        raise AssertionError("an unregistered key must not reach a candidate selector")

    spec = JobSpec(
        name="installed", schedule=DORMANT_CRON, handler=_write_marker, candidates=_never
    )
    with caplog.at_level(logging.WARNING, logger="ufo"):
        await _runner((spec,)).tick(datetime.now(UTC), vanished)

    record = next(r for r in caplog.records if r.message == "jobs.tick_unregistered")
    assert record.ufo == {"key": vanished}


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
