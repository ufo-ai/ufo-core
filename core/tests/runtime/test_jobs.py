import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOS
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import Message, ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.o11y import BACKGROUND_PROFILE
from ufo.product import (
    ADDRESS_KIND,
    APP_KIND,
    CONNECTOR_KIND,
    CREDENTIAL_KIND,
    PRODUCT_ATTACH_METRIC,
    PRODUCT_STAGE_METRIC,
    SURFACE_KIND,
    product_census,
)
from ufo.runtime import jobs as jobs_module
from ufo.runtime.candidates import owner_candidates
from ufo.runtime.ext.context import ExtensionContext, ScopedStore
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, TerminalFrame, Usage

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
    return JobRunner(bindings=bindings_from((), core_jobs), manifests=())


def test_bindings_exclude_only_named_jobs() -> None:
    first = JobSpec(
        name="first",
        schedule=DORMANT_CRON,
        handler=_write_marker,
        candidates=_every_workspace(),
    )
    second = JobSpec(
        name="second",
        schedule=DORMANT_CRON,
        handler=_write_marker,
        candidates=_every_workspace(),
    )

    bindings = bindings_from((), (first, second), disabled=frozenset({"core:first"}))

    assert tuple(binding.key for binding in bindings) == ("core:second",)
    with pytest.raises(ValueError, match="disabled jobs are not registered: core:missing"):
        bindings_from((), (first,), disabled=frozenset({"core:missing"}))


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
    assert record.ufo["workspace_id"] == str(workspace_id)
    assert record.ufo["job"] == key
    assert record.ufo["error_class"] == "TimeoutError"
    assert "_fail" in record.ufo["stack"]
    assert "statement" not in record.ufo
    assert "sqlstate" not in record.ufo


async def test_fire_names_the_statement_the_database_refused(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A job that dies on a statement the schema will not accept logs that statement, so the column
    or table at fault is readable from the failure itself.

    `error_class` alone cannot be diagnosed: it says the driver refused the SQL and nothing about
    which SQL, and by the time anyone reads the line the schema the process met is gone — a
    migration that reshaped a table under a fleet still serving the previous image leaves exactly
    this and no second chance to ask."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:refused"

    async def _refused(context: ExtensionContext) -> None:
        async with workspace_tx() as connection:
            await connection.execute(sa.text("select agent_id from workspace"))

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="refused", schedule="* * * * * *", handler=_refused, candidates=_candidate)
    with caplog.at_level(logging.ERROR, logger="ufo"), pytest.raises(sa.exc.DBAPIError):
        await _runner((spec,)).fire(key, workspace_id)

    record = next(record for record in caplog.records if record.message == "jobs.failed")
    assert record.ufo["job"] == key
    assert record.ufo["workspace_id"] == str(workspace_id)
    assert record.ufo["statement"] == "select agent_id from workspace"
    assert "_refused" in record.ufo["stack"]


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
        bindings=bindings_from((), (spec,)),
        manifests=(),
        registry=registry,
        background_model="gpt-5.6-luna",
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
        manifests=(),
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
        bindings=bindings_from((), (spec,)),
        manifests=(),
        registry=registry,
        background_model="gpt-5.6-luna",
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


async def _seeded_workspace(
    *,
    seated: bool = True,
    invited: bool = False,
    connector: str | None = None,
    provisioned_app: str | None = None,
    own_app: bool = False,
    member_turn_days_ago: int | None = None,
    charged_micro_usd: int = 0,
    surface_installed: str | None = None,
    proved_address: str | None = None,
    claimed_address: str | None = None,
    credential_slot: str | None = None,
) -> UUID:
    """One workspace standing at exactly the stages the arguments name, and no others.

    Every row here is the row the product really writes, so a stage the census claims is a stage the
    fleet would claim. The defaults seat a member and stop, which is the top of the funnel."""
    workspace_id = uuid4()
    agent_id = uuid4()
    member_id = uuid4()
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@work.com",
                seated_at=sa.func.now() if seated else None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if invited:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    email="invitee@work.com",
                    invited_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="Main",
                prompt="help",
                model="auto",
                provisioned_by=None if provisioned_app is None else "app_wiki",
                provisioned_name=provisioned_app,
                provisioned_version=None if provisioned_app is None else "1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if own_app:
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name="Standup",
                    prompt="post the standup",
                    model="auto",
                    owner_member_id=member_id,
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
                queue_key=str(conversation_id),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if connector is not None:
            connection_id = uuid4()
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=connection_id,
                    workspace_id=workspace_id,
                    provider=connector,
                    account_id="acct",
                    host="composio",
                    owner_member_id=member_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        if member_turn_days_ago is not None:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound="hello",
                    terminal=TerminalFrame(status="done", text="hi").model_dump(mode="json"),
                    admission_source=MEMBER_ADMISSION,
                    created_at=datetime.now(UTC) - timedelta(days=member_turn_days_ago),
                    updated_at=sa.func.now(),
                )
            )
        if charged_micro_usd:
            await connection.execute(
                sa.insert(tables.balance_purchase).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    granted_micro_usd=charged_micro_usd,
                    charged_micro_usd=charged_micro_usd,
                    reference="stripe/pi_1",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        if surface_installed is not None:
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    workspace_id=workspace_id,
                    surface=surface_installed,
                    installation_id=str(workspace_id),
                    agent_id=agent_id,
                    routes_ingress=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        if proved_address is not None:
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface=proved_address,
                    address=f"proved:{workspace_id}",
                    workspace_id=workspace_id,
                    member_id=member_id,
                    proved_by="code",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        if claimed_address is not None:
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface=claimed_address,
                    address=f"claimed:{workspace_id}",
                    workspace_id=workspace_id,
                    member_id=member_id,
                    claim_expires_at=datetime.now(UTC) + timedelta(hours=1),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        if credential_slot is not None:
            await connection.execute(
                sa.insert(tables.credential).values(
                    workspace_id=workspace_id,
                    slot=credential_slot,
                    ciphertext=b"sealed",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id


def _census_points(reader: InMemoryMetricReader) -> dict[str, list[object]]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: list(metric.data.data_points)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _census_reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    return reader


async def test_the_census_counts_only_the_stages_a_workspace_has_reached(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage is a claim about a row, so a workspace holding no connector, no invite and no
    purchase must produce no such series at all — a stage that counts every workspace whatever it
    did reads as progress that never happened."""
    workspace_id = await _seeded_workspace(member_turn_days_ago=0)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await product_census()

    points = _census_points(reader)
    assert {point.attributes["stage"] for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]} == {
        "seated",
        "chatted",
        "active_1d",
        "active_7d",
    }
    assert f"ufo.{PRODUCT_ATTACH_METRIC}" not in points


async def test_the_census_counts_every_stage_a_finished_workspace_reached(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ladder is cumulative: a workspace that paid also counts at every stage beneath it, which
    is what lets the board read one series as a funnel whose steps never rise."""
    workspace_id = await _seeded_workspace(
        invited=True,
        connector="gmail",
        provisioned_app="wiki",
        own_app=True,
        member_turn_days_ago=0,
        charged_micro_usd=25_000_000,
    )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await product_census()

    points = _census_points(reader)
    assert {point.attributes["stage"] for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]} == {
        "seated",
        "connector",
        "invited",
        "app",
        "chatted",
        "active_1d",
        "active_7d",
        "paid",
    }
    assert all(point.value == 1 for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"])


async def test_the_census_counts_the_app_stage_off_an_app_the_workspace_made_itself(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fleet provisions its own apps into every workspace, and `app_chat` provisions the main
    agent, so provenance stands on a workspace that built nothing. Only an owner marks a member's
    own act, and a stage every workspace reaches would report adoption nobody performed."""
    provisioned = await _seeded_workspace(provisioned_app="wiki")
    built = await _seeded_workspace(provisioned_app="wiki", own_app=True)

    provisioned_reader = _census_reader(monkeypatch)
    with ws(provisioned):
        await product_census()
    provisioned_stages = {
        point.attributes["stage"]
        for point in _census_points(provisioned_reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
    }

    built_reader = _census_reader(monkeypatch)
    with ws(built):
        await product_census()
    built_stages = {
        point.attributes["stage"]
        for point in _census_points(built_reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
    }

    assert "app" not in provisioned_stages
    assert "app" in built_stages


async def test_the_census_ages_a_workspace_out_of_the_active_window_it_left(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chatted` is forever and `active_*` is a window, so a workspace whose only member turn is
    three days old must hold the week and have lost the day. Counting it active would make the
    engagement graph a signup graph."""
    workspace_id = await _seeded_workspace(member_turn_days_ago=3)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await product_census()

    stages = {
        point.attributes["stage"] for point in _census_points(reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
    }
    assert "chatted" in stages
    assert "active_7d" in stages
    assert "active_1d" not in stages


async def test_the_census_names_what_is_attached_without_holding_its_name(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack, GitHub, iMessage and every bring-your-own-key connector reach the board as values read
    off the column, so core counts them while naming none of them."""
    workspace_id = await _seeded_workspace(
        connector="gmail",
        provisioned_app="wiki",
        surface_installed="imessage",
        credential_slot="github_app_installation",
    )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await product_census()

    points = _census_points(reader)
    assert {
        (point.attributes["kind"], point.attributes["name"])
        for point in points[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    } == {
        (CONNECTOR_KIND, "gmail"),
        (APP_KIND, "wiki"),
        (SURFACE_KIND, "imessage"),
        (CREDENTIAL_KIND, "github_app_installation"),
    }


async def test_the_census_counts_a_proved_address_apart_from_its_installation(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim a member started and never finished routes nothing, so it counts as a surface and
    never as an address — conflating them would report an iMessage number for a workspace nobody
    could message. The unreachable workspace holds a real unproved claim row, not an absent one:
    absence would pass this test whether the proof were checked or not."""
    bound = await _seeded_workspace(surface_installed="imessage", claimed_address="imessage")
    reachable = await _seeded_workspace(surface_installed="imessage", proved_address="imessage")

    bound_reader = _census_reader(monkeypatch)
    with ws(bound):
        await product_census()
    bound_kinds = {
        point.attributes["kind"]
        for point in _census_points(bound_reader)[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    }

    reachable_reader = _census_reader(monkeypatch)
    with ws(reachable):
        await product_census()
    reachable_kinds = {
        point.attributes["kind"]
        for point in _census_points(reachable_reader)[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    }

    assert bound_kinds == {SURFACE_KIND}
    assert reachable_kinds == {SURFACE_KIND, ADDRESS_KIND}


async def test_the_census_sees_only_the_workspace_it_is_bound_to(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tick's increments are what the board divides by to get a workspace count, so a census
    that could see a neighbour's rows would multiply every number on it."""
    quiet = await _seeded_workspace()
    await _seeded_workspace(connector="gmail", member_turn_days_ago=0, charged_micro_usd=25_000_000)
    reader = _census_reader(monkeypatch)

    with ws(quiet):
        await product_census()

    points = _census_points(reader)
    assert {point.attributes["stage"] for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]} == {
        "seated"
    }
    assert f"ufo.{PRODUCT_ATTACH_METRIC}" not in points
