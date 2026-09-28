import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOS, SetWorkflowID
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_sample.spend import SampleGate, allow
from ufo_testsupport.invoker import RecordingInvoker

from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import Message, ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.o11y import BACKGROUND_PROFILE
from ufo.product import (
    ADDRESS_KIND,
    APP_BUILT_STEP,
    APP_KIND,
    CENSUS_SURFACE,
    CONNECTOR_ATTACHED_STEP,
    CONNECTOR_KIND,
    CREDENTIAL_KIND,
    MEMBER_CHATTED_STEP,
    NO_PROVIDER,
    ONBOARDING_LATENCY_HISTOGRAM,
    ONBOARDING_STEP_METRIC,
    PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC,
    PRODUCT_ATTACH_METRIC,
    PRODUCT_STAGE_METRIC,
    STEP_COMPLETED,
    SURFACE_INSTALLED_STEP,
    SURFACE_KIND,
    WORKSPACE_CREATED_STEP,
    ProductCensus,
)
from ufo.runtime import jobs as jobs_module
from ufo.runtime.billing.accounting import OffTurnSpendRefused
from ufo.runtime.billing.spend import PARK, GateDeploy, SpendGates
from ufo.runtime.candidates import owner_candidates
from ufo.runtime.ext.context import ExtensionContext, ScopedStore
from ufo.runtime.ext.manifest import JOB_FAULT_MAX_CHARS, JobFault, JobSpec
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    JOB_FAILED_METRIC,
    JobRunner,
    bindings_from,
    spend_refusal_notice_key,
)
from ufo.runtime.turns.audience import (
    Audience,
    conversation_audience,
    foreign_room_audience,
)
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.records import (
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    SCHEDULED_ADMISSION,
    TerminalFrame,
    TurnRuntimeConfig,
    Usage,
)

FIRE_TIMEOUT_SECONDS = 25
MARKER_KEY = "fired"
MARKER_VALUE = {"ran": True}
DORMANT_CRON = "0 0 5 * * *"
BACKGROUND_MODEL = "gpt-5.6-luna"
RECOVERY_RUNS_KEY = "recovery-runs"


class _JobWorkerCrash(BaseException):
    pass


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


DOLLAR = 1_000_000
OWN_KEY_SLOT = "anthropic_api_key"


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
    await context.store.put(MARKER_KEY, MARKER_VALUE)


async def _await_marker(scoped: ScopedStore) -> object:
    async with asyncio.timeout(FIRE_TIMEOUT_SECONDS):
        while True:
            value = await scoped.get(MARKER_KEY)
            if value is not None:
                return value
            await asyncio.sleep(0.2)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_binds_each_candidate_workspace_and_never_runs_unbound(db: None) -> None:
    """The invariant: `fire` opens `with ws(id)` for each workspace the candidate names and runs
    the handler scoped to it — never once unbound."""
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_records_the_reason_a_handler_named_for_its_own_failure(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:shipper"

    async def _fault(context: ExtensionContext) -> None:
        raise JobFault("metronome customer lookup failed (500): Unexpected internal error")

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="shipper", schedule="* * * * * *", handler=_fault, candidates=_candidate)
    with caplog.at_level(logging.ERROR, logger="ufo"), pytest.raises(JobFault):
        await _runner((spec,)).fire(key, workspace_id)

    record = next(record for record in caplog.records if record.message == "jobs.failed")
    assert record.ufo["job"] == key
    assert record.ufo["error_class"] == "JobFault"
    assert record.ufo["fault"] == (
        "metronome customer lookup failed (500): Unexpected internal error"
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_records_no_fault_for_a_message_the_process_never_wrote(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Only a handler's own words ride."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:leaky"
    secret = "http://token:s3cr3t@proxy.internal:8080"

    async def _leak(context: ExtensionContext) -> None:
        raise RuntimeError(f"command failed, environment was HTTP_PROXY={secret}")

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="leaky", schedule="* * * * * *", handler=_leak, candidates=_candidate)
    with caplog.at_level(logging.ERROR, logger="ufo"), pytest.raises(RuntimeError):
        await _runner((spec,)).fire(key, workspace_id)

    record = next(record for record in caplog.records if record.message == "jobs.failed")
    assert "fault" not in record.ufo
    assert secret not in str(record.ufo)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_bounds_the_reason_a_handler_names(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A handler authors its reason but may build it out of a provider's answer, and no answer is
    bounded. The record holds what an operator reads and never a payload."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:verbose"

    async def _verbose(context: ExtensionContext) -> None:
        raise JobFault("x" * (JOB_FAULT_MAX_CHARS * 2))

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="verbose", schedule="* * * * * *", handler=_verbose, candidates=_candidate)
    with caplog.at_level(logging.ERROR, logger="ufo"), pytest.raises(JobFault):
        await _runner((spec,)).fire(key, workspace_id)

    record = next(record for record in caplog.records if record.message == "jobs.failed")
    assert record.ufo["fault"] == "x" * JOB_FAULT_MAX_CHARS


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_counts_the_failed_job_so_a_stalled_pipeline_alerts(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:broken"
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})

    async def _fail(context: ExtensionContext) -> None:
        raise TimeoutError("database connect timed out")

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="broken", schedule="* * * * * *", handler=_fail, candidates=_candidate)
    runner = _runner((spec,))
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await runner.fire(key, workspace_id)

    data = reader.get_metrics_data()
    assert data is not None
    counted = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == f"ufo.{JOB_FAILED_METRIC}"
        for point in metric.data.data_points
    ]
    assert sum(point.value for point in counted) == 2
    assert {point.attributes["job"] for point in counted} == {key}
    assert {point.attributes["error_class"] for point in counted} == {"TimeoutError"}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_that_returns_counts_no_failure(db: None, monkeypatch) -> None:
    """The counter is what a monitor reads as "this job is not running", so a job that ran must
    leave the series empty — a count on the success path would hold every alert on forever."""
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:quiet"
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})

    async def _quiet(context: ExtensionContext) -> None:
        return None

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="quiet", schedule="* * * * * *", handler=_quiet, candidates=_candidate)
    await _runner((spec,)).fire(key, workspace_id)

    data = reader.get_metrics_data()
    assert not [
        metric
        for resource in (data.resource_metrics if data else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == f"ufo.{JOB_FAILED_METRIC}"
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_fire_names_the_statement_the_database_refused(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A job that dies on a statement the schema will not accept logs that statement, so the
    column or table at fault is readable from the failure itself."""
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


async def _spoken_workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
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
                is_admin=True,
                seated_at=sa.func.now(),
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
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id, agent_id, member_id


async def _spoke_again_in(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    audience: Audience,
    surface: str,
    spoken_at: datetime,
) -> UUID:
    """One more conversation of this audience, with a member turn in it at `spoken_at` — the row
    that decides which conversation is the newest one the member spoke in."""
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=str(conversation_id),
                audience=str(audience),
                member_id=member_id if audience == conversation_audience(member_id) else None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
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
                speaker_member_id=member_id,
                created_at=spoken_at,
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_spend_notice_skips_an_externally_shared_room_for_the_member_s_own_conversation(
    db: None,
) -> None:
    """The notice states a workspace's spend hold and where to lift it, so it is founded only in
    a conversation the speaker reads as a member of this workspace."""
    workspace_id, _, agent_id, member_id = await _spoken_workspace()
    own_conversation_id = await _spoke_again_in(
        workspace_id,
        agent_id,
        member_id,
        conversation_audience(member_id),
        "web",
        datetime.now(UTC) + timedelta(hours=1),
    )
    await _spoke_again_in(
        workspace_id,
        agent_id,
        member_id,
        foreign_room_audience("slack", "C0FOREIGN"),
        "slack",
        datetime.now(UTC) + timedelta(hours=2),
    )
    key = f"{CORE_EXTENSION}:memory"

    async def _refused(context: ExtensionContext) -> None:
        raise OffTurnSpendRefused(PARK, "this workspace has no credit left", BACKGROUND_MODEL)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="memory", schedule="* * * * * *", handler=_refused, candidates=_candidate)
    invoker = RecordingInvoker()
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        invoker_factory=lambda _: invoker,
    )

    await runner.fire(key, workspace_id)

    assert [turn.conversation_id for turn in invoker.turns] == [own_conversation_id]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_that_spoke_only_in_a_foreign_room_is_not_told_at_all(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id, conversation_id, _, _ = await _spoken_workspace()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(audience=str(foreign_room_audience("slack", "C0FOREIGN")), surface="slack")
            .where(tables.conversation.c.id == conversation_id)
        )
    key = f"{CORE_EXTENSION}:memory"

    async def _refused(context: ExtensionContext) -> None:
        raise OffTurnSpendRefused(PARK, "this workspace has no credit left", BACKGROUND_MODEL)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="memory", schedule="* * * * * *", handler=_refused, candidates=_candidate)
    invoker = RecordingInvoker()
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        invoker_factory=lambda _: invoker,
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await runner.fire(key, workspace_id)

    assert invoker.turns == []
    assert [
        record.ufo["workspace_id"]
        for record in caplog.records
        if record.message == "jobs.spend_refusal_untold"
    ] == [str(workspace_id)]
    with ws(workspace_id):
        mark = spend_refusal_notice_key(BACKGROUND_MODEL)
        assert await ScopedStore(extension=CORE_EXTENSION).get(mark) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_spend_refusal_defers_the_job_and_tells_the_member_once(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id, conversation_id, agent_id, _member_id = await _spoken_workspace()
    key = f"{CORE_EXTENSION}:memory"

    async def _refused(context: ExtensionContext) -> None:
        raise OffTurnSpendRefused(PARK, "this workspace has no credit left", BACKGROUND_MODEL)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="memory", schedule="* * * * * *", handler=_refused, candidates=_candidate)
    invoker = RecordingInvoker()
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        invoker_factory=lambda _: invoker,
    )
    scoped = ScopedStore(extension=CORE_EXTENSION)
    mark = spend_refusal_notice_key(BACKGROUND_MODEL)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await runner.fire(key, workspace_id)

    assert [record for record in caplog.records if record.message == "jobs.failed"] == []
    deferred = next(
        record for record in caplog.records if record.message == "jobs.deferred_on_spend"
    )
    assert deferred.ufo["job"] == key
    assert deferred.ufo["outcome"] == PARK
    assert deferred.ufo["model"] == BACKGROUND_MODEL
    with ws(workspace_id):
        assert await scoped.get(mark) == PARK
    assert len(invoker.turns) == 1
    told = invoker.turns[0]
    assert told.conversation_id == conversation_id
    assert told.agent_id == agent_id
    assert "this workspace has no credit left" in told.message
    assert key in told.message
    assert told.runtime_config == TurnRuntimeConfig(internet_access=False)

    await runner.fire(key, workspace_id)
    assert len(invoker.turns) == 1

    with ws(workspace_id):
        await scoped.delete(mark)
    await runner.fire(key, workspace_id)
    assert len(invoker.turns) == 2


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_refusal_reads_the_mark_of_the_model_it_was_refused_on(db: None) -> None:
    """A hold stands per model, so the mark another model's refusal left says nothing about this
    one: the member is told about this model's hold, and the other mark is left as it was."""
    workspace_id, _, _, _ = await _spoken_workspace()
    key = f"{CORE_EXTENSION}:memory"
    other = spend_refusal_notice_key("claude-opus-4-8")
    scoped = ScopedStore(extension=CORE_EXTENSION)

    async def _refused(context: ExtensionContext) -> None:
        raise OffTurnSpendRefused(PARK, "this workspace has no credit left", BACKGROUND_MODEL)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    spec = JobSpec(name="memory", schedule="* * * * * *", handler=_refused, candidates=_candidate)
    invoker = RecordingInvoker()
    runner = JobRunner(
        bindings=bindings_from((), (spec,)),
        manifests=(),
        invoker_factory=lambda _: invoker,
    )

    with ws(workspace_id):
        await scoped.put(other, PARK)
    await runner.fire(key, workspace_id)

    assert len(invoker.turns) == 1
    with ws(workspace_id):
        assert await scoped.get(spend_refusal_notice_key(BACKGROUND_MODEL)) == PARK
        assert await scoped.get(other) == PARK


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_runs_its_own_model_calls_on_the_background_jobs_model(db: None) -> None:
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_model_call_meters_its_tokens_and_latency_under_the_key_that_fired(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every background job shares one model, so the model alone cannot say which work spent the
    tokens — the key the dispatcher fired is what the series carry."""
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_that_needs_the_deploy_model_keeps_it(db: None) -> None:
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


@pytest.fixture
async def dbos_loop_executor(dbos_launched: object) -> AsyncIterator[None]:
    yield
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_recurring_core_job_registers_at_boot_and_fires(
    db: None, dbos_loop_executor: None
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:tick"
    spec = JobSpec(
        name="tick", schedule="* * * * * *", handler=_write_marker, candidates=_every_workspace()
    )
    runner = _runner((spec,))
    runner.install()
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        await runner.launch()
        schedule = next(s for s in DBOS.list_schedules() if s["schedule_name"] == key)
        assert schedule.get("queue_name") is None
        with ws(workspace_id):
            assert await _await_marker(scoped) == MARKER_VALUE
    finally:
        DBOS.delete_schedule(key)
        jobs_module._firing = None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_tick_skips_a_key_this_process_registers_no_job_for(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """`apply_schedules` upserts and never deletes, so a schedule outlives the job that wrote it
    — an extension uninstalled, or a job only a newer peer registers."""
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_one_shot_core_job_fires_once_at_boot(db: None, dbos_loop_executor: None) -> None:
    workspace_id = await _workspace()
    spec = JobSpec(name="boot", schedule=None, handler=_write_marker, candidates=_every_workspace())
    runner = _runner((spec,))
    runner.install()
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        await runner.launch()
        with ws(workspace_id):
            assert await _await_marker(scoped) == MARKER_VALUE
    finally:
        jobs_module._firing = None


@pytest.mark.serial
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_job_recovery_replays_a_completed_handler_step_without_running_it_again(
    db: None,
    dbos_loop_executor: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:recover"

    async def _count(context: ExtensionContext) -> None:
        stored = await context.store.get(RECOVERY_RUNS_KEY)
        if stored is not None and not isinstance(stored, int):
            raise TypeError("recovery run count must be an integer")
        await context.store.put(RECOVERY_RUNS_KEY, (stored or 0) + 1)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    runner = _runner(
        (
            JobSpec(
                name="recover",
                schedule=DORMANT_CRON,
                handler=_count,
                candidates=_candidate,
            ),
        )
    )
    workflow_id = str(uuid4())
    original_fire = jobs_module.job_fire
    crashed = False

    async def _crash_after_step(job_key: str, scoped_workspace_id: str) -> None:
        nonlocal crashed
        await original_fire(job_key, scoped_workspace_id)
        if not crashed:
            crashed = True
            raise _JobWorkerCrash("worker stopped after the handler step")

    monkeypatch.setattr(jobs_module, "job_fire", _crash_after_step)
    saved = jobs_module._firing
    jobs_module._firing = runner
    scoped = ScopedStore(extension=CORE_EXTENSION)
    try:
        with SetWorkflowID(workflow_id), pytest.raises(_JobWorkerCrash):
            await jobs_module.job_workflow(datetime.now(UTC), key, str(workspace_id))

        with ws(workspace_id):
            assert await scoped.get(RECOVERY_RUNS_KEY) == 1

        DBOS._recover_pending_workflows(["local"])
        handle = await DBOS.retrieve_workflow_async(workflow_id)
        async with asyncio.timeout(FIRE_TIMEOUT_SECONDS):
            await handle.get_result(polling_interval_sec=0.05)

        with ws(workspace_id):
            assert await scoped.get(RECOVERY_RUNS_KEY) == 1
    finally:
        jobs_module._firing = saved


@dataclass
class _HeldJob:
    """A handler that counts its executions and holds each one open until released — the live
    predecessor the dedup contract is asserted against."""

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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_tick_skips_while_predecessor_runs_and_resumes_after_terminal(
    db: None, dbos_loop_executor: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace()
    key = f"{CORE_EXTENSION}:slow"
    job = _HeldJob()
    spec = JobSpec(
        name="slow", schedule="* * * * * *", handler=job.hold, candidates=_every_workspace()
    )
    runner = _runner((spec,))
    runner.install()
    try:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            await runner.launch()
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_one_shot_twin_boots_start_one_run(
    db: None, dbos_loop_executor: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Two replicas boot the same one-shot; the deduplication id collapses the twin enqueue while
    the first run is live — logged as `jobs.enqueue_skipped` — so exactly one execution starts."""
    await _workspace()
    key = f"{CORE_EXTENSION}:twin"
    job = _HeldJob()
    spec = JobSpec(name="twin", schedule=None, handler=job.hold, candidates=_every_workspace())
    runner = _runner((spec,))
    runner.install()
    try:
        with caplog.at_level(logging.WARNING, logger="ufo"):
            await runner.launch()
            await runner.launch()
        assert _skips(caplog, "jobs.enqueue_skipped", key=key)
        await job.await_started(1)
        await asyncio.sleep(2)
        assert job.started == 1
    finally:
        job.release.set()
        jobs_module._firing = None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_slow_workspace_does_not_starve_its_neighbors(
    db: None, dbos_loop_executor: None
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
    runner.install()
    try:
        await runner.launch()
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
    teammate: bool = False,
    connector: str | None = None,
    provisioned_app: str | None = None,
    own_app: bool = False,
    member_turn_days_ago: int | None = None,
    surface_installed: str | None = None,
    proved_address: str | None = None,
    claimed_address: str | None = None,
    credential_slot: str | None = None,
    founded_days_ago: int = 0,
) -> UUID:
    """One workspace standing at exactly the stages the arguments name, and no others."""
    workspace_id = uuid4()
    agent_id = uuid4()
    member_id = uuid4()
    teammate_id = uuid4()
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=datetime.now(UTC) - timedelta(days=founded_days_ago),
                updated_at=sa.func.now(),
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
        if teammate:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=teammate_id,
                    workspace_id=workspace_id,
                    email="teammate@work.com",
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_reports_every_stage_and_counts_only_the_stages_reached(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _seeded_workspace(member_turn_days_ago=0)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    points = _census_points(reader)
    stages = {
        point.attributes["stage"]: point.value for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]
    }
    assert {stage for stage, value in stages.items() if value == 1} == {
        "seated",
        "chatted",
        "active_1d",
        "active_7d",
    }
    assert set(stages) == {
        "seated",
        "connector",
        "app",
        "chatted",
        "active_1d",
        "active_7d",
    }
    assert f"ufo.{PRODUCT_ATTACH_METRIC}" not in points


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_counts_every_stage_a_finished_workspace_reached(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ladder is cumulative: a workspace that chatted this week also counts at every stage
    beneath it, which is what lets the board read one series as a funnel whose steps never rise."""
    workspace_id = await _seeded_workspace(
        connector="gmail",
        provisioned_app="wiki",
        own_app=True,
        member_turn_days_ago=0,
    )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    points = _census_points(reader)
    assert {point.attributes["stage"] for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]} == {
        "seated",
        "connector",
        "app",
        "chatted",
        "active_1d",
        "active_7d",
    }
    assert all(point.value == 1 for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"])


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_counts_the_app_stage_off_an_app_the_workspace_made_itself(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fleet provisions its own apps into every workspace, and `app_chat` provisions the main
    agent, so provenance stands on a workspace that built nothing."""
    provisioned = await _seeded_workspace(provisioned_app="wiki")
    built = await _seeded_workspace(provisioned_app="wiki", own_app=True)

    provisioned_reader = _census_reader(monkeypatch)
    with ws(provisioned):
        await ProductCensus(contributions=()).count()
    provisioned_stages = {
        point.attributes["stage"]
        for point in _census_points(provisioned_reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
        if point.value == 1
    }

    built_reader = _census_reader(monkeypatch)
    with ws(built):
        await ProductCensus(contributions=()).count()
    built_stages = {
        point.attributes["stage"]
        for point in _census_points(built_reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
        if point.value == 1
    }

    assert "app" not in provisioned_stages
    assert "app" in built_stages


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_ages_a_workspace_out_of_the_active_window_it_left(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chatted` is forever and `active_*` is a window, so a workspace whose only member turn is
    three days old must hold the week and have lost the day."""
    workspace_id = await _seeded_workspace(member_turn_days_ago=3)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    stages = {
        point.attributes["stage"]: point.value
        for point in _census_points(reader)[f"ufo.{PRODUCT_STAGE_METRIC}"]
    }
    assert stages["chatted"] == 1
    assert stages["active_7d"] == 1
    assert stages["active_1d"] == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_counts_members_active_on_two_of_seven_days(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _seeded_workspace(teammate=True)
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        members = (
            (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        intent_member = uuid4()
        await connection.execute(
            sa.insert(tables.member).values(
                id=intent_member,
                workspace_id=workspace_id,
                email="intent@work.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        turn_ids = [uuid4() for _ in range(9)]
        await connection.execute(
            sa.insert(tables.turn),
            [
                {
                    "id": turn_id,
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "seq": seq,
                    "status": "done",
                    "inbound": "activity",
                    "terminal": TerminalFrame(status="done", text="done").model_dump(mode="json"),
                    "admission_source": admission_source,
                    "speaker_member_id": member_id,
                    "parent_turn_id": parent_turn_id,
                    "created_at": now - timedelta(days=days_ago),
                    "updated_at": now,
                }
                for seq, (
                    turn_id,
                    member_id,
                    admission_source,
                    days_ago,
                    parent_turn_id,
                ) in enumerate(
                    [
                        (turn_ids[0], members[0], MEMBER_ADMISSION, 6, None),
                        (turn_ids[1], members[1], MEMBER_ADMISSION, 6, None),
                        (turn_ids[2], members[1], MEMBER_ADMISSION, 6, None),
                        (turn_ids[3], members[1], MEMBER_ADMISSION, 8, None),
                        (turn_ids[4], members[1], SCHEDULED_ADMISSION, 2, None),
                        (turn_ids[5], members[1], INTERNAL_ADMISSION, 1, None),
                        (turn_ids[6], intent_member, MEMBER_ADMISSION, 6, None),
                        (turn_ids[7], intent_member, INTENT_ADMISSION, 2, None),
                        (turn_ids[8], members[1], MEMBER_ADMISSION, 2, turn_ids[0]),
                    ],
                    start=1,
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.inbound_message),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "seq": 1,
                    "body": "more activity",
                    "admission_source": MEMBER_ADMISSION,
                    "speaker_member_id": members[0],
                    "admitted_turn_id": turn_ids[0],
                    "created_at": now - timedelta(days=2),
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "seq": 2,
                    "body": "automatic work",
                    "admission_source": INTERNAL_ADMISSION,
                    "speaker_member_id": members[1],
                    "admitted_turn_id": turn_ids[1],
                    "created_at": now - timedelta(days=2),
                },
            ],
        )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    points = _census_points(reader)
    active_members = points[f"ufo.{PRODUCT_ACTIVE_MEMBER_2D_7D_METRIC}"]
    assert [point.value for point in active_members] == [2]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_names_what_is_attached_without_holding_its_name(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack, GitHub, iMessage and every bring-your-own-key connector reach the board as values read
    off the column, so core counts them while naming none of them."""
    workspace_id = await _seeded_workspace(
        connector="gmail",
        provisioned_app="wiki",
        surface_installed="imessage",
        credential_slot="perplexity_api_key",
    )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    points = _census_points(reader)
    assert {
        (point.attributes["kind"], point.attributes["name"])
        for point in points[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    } == {
        (CONNECTOR_KIND, "gmail"),
        (APP_KIND, "wiki"),
        (SURFACE_KIND, "imessage"),
        (CREDENTIAL_KIND, "perplexity_api_key"),
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_counts_a_proved_address_apart_from_its_installation(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = await _seeded_workspace(surface_installed="imessage", claimed_address="imessage")
    reachable = await _seeded_workspace(surface_installed="imessage", proved_address="imessage")

    bound_reader = _census_reader(monkeypatch)
    with ws(bound):
        await ProductCensus(contributions=()).count()
    bound_kinds = {
        point.attributes["kind"]
        for point in _census_points(bound_reader)[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    }

    reachable_reader = _census_reader(monkeypatch)
    with ws(reachable):
        await ProductCensus(contributions=()).count()
    reachable_kinds = {
        point.attributes["kind"]
        for point in _census_points(reachable_reader)[f"ufo.{PRODUCT_ATTACH_METRIC}"]
    }

    assert bound_kinds == {SURFACE_KIND}
    assert reachable_kinds == {SURFACE_KIND, ADDRESS_KIND}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_census_sees_only_the_workspace_it_is_bound_to(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tick's increments are what the board divides by to get a workspace count, so a census
    that could see a neighbour's rows would multiply every number on it."""
    quiet = await _seeded_workspace()
    await _seeded_workspace(connector="gmail", member_turn_days_ago=0)
    reader = _census_reader(monkeypatch)

    with ws(quiet):
        await ProductCensus(contributions=()).count()

    points = _census_points(reader)
    assert {
        point.attributes["stage"]
        for point in points[f"ufo.{PRODUCT_STAGE_METRIC}"]
        if point.value == 1
    } == {"seated"}
    assert f"ufo.{PRODUCT_ATTACH_METRIC}" not in points


def _counted_steps(reader: InMemoryMetricReader) -> set[tuple[str, str, str, str]]:
    return {
        (
            point.attributes["step"],
            point.attributes["status"],
            point.attributes["surface"],
            point.attributes["provider"],
        )
        for point in _census_points(reader)[f"ufo.{ONBOARDING_STEP_METRIC}"]
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_onboarding_census_counts_every_step_a_workspace_just_took(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _seeded_workspace(
        connector="gmail",
        surface_installed="slack",
        own_app=True,
        member_turn_days_ago=0,
    )
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    assert _counted_steps(reader) == {
        (WORKSPACE_CREATED_STEP, STEP_COMPLETED, CENSUS_SURFACE, NO_PROVIDER),
        (MEMBER_CHATTED_STEP, STEP_COMPLETED, CENSUS_SURFACE, NO_PROVIDER),
        (CONNECTOR_ATTACHED_STEP, STEP_COMPLETED, CENSUS_SURFACE, "gmail"),
        (SURFACE_INSTALLED_STEP, STEP_COMPLETED, CENSUS_SURFACE, "slack"),
        (APP_BUILT_STEP, STEP_COMPLETED, CENSUS_SURFACE, NO_PROVIDER),
    }
    latencies = _census_points(reader)[f"ufo.{ONBOARDING_LATENCY_HISTOGRAM}"]
    assert {point.attributes["step"] for point in latencies} == {
        step for step, _status, _surface, _provider in _counted_steps(reader)
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_onboarding_census_counts_a_step_only_in_the_tick_it_first_arrived_in(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step happens once, so a step whose first row predates the tick just gone is already counted
    — counting it again would report one workspace's first chat once every ten minutes forever."""
    workspace_id = await _seeded_workspace(member_turn_days_ago=3)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    assert {step for step, _status, _surface, _provider in _counted_steps(reader)} == {
        WORKSPACE_CREATED_STEP
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_onboarding_census_measures_each_step_from_the_workspaces_founding(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The latency is what says where a slow setup stalls a team: a workspace founded three days
    before its first chat reports three days on that step, not the age of the turn."""
    workspace_id = await _seeded_workspace(member_turn_days_ago=0, founded_days_ago=3)
    reader = _census_reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    latencies = {
        point.attributes["step"]: point.sum
        for point in _census_points(reader)[f"ufo.{ONBOARDING_LATENCY_HISTOGRAM}"]
    }
    assert set(latencies) == {MEMBER_CHATTED_STEP}
    assert latencies[MEMBER_CHATTED_STEP] > 2 * 24 * 60 * 60 * 1000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_onboarding_census_sees_only_the_workspace_it_is_bound_to(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One step counted under a neighbour's binding would put a step on the board no team took."""
    quiet = await _seeded_workspace()
    await _seeded_workspace(connector="gmail", surface_installed="slack", member_turn_days_ago=0)
    reader = _census_reader(monkeypatch)

    with ws(quiet):
        await ProductCensus(contributions=()).count()

    assert {step for step, _status, _surface, _provider in _counted_steps(reader)} == {
        WORKSPACE_CREATED_STEP
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_spending_job_names_no_workspace_the_gates_hold(db: None) -> None:
    refused = await _workspace()
    allowed = await _workspace()
    own_key = await _workspace()
    async with workspace_tx() as connection:
        await allow(connection, refused, 0, "reject")
        await allow(connection, allowed, DOLLAR, "reject")
        await allow(connection, own_key, 0, "reject")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=own_key,
                slot=OWN_KEY_SLOT,
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    spends = JobSpec(
        name="spends",
        schedule=DORMANT_CRON,
        handler=_write_marker,
        candidates=_every_workspace(),
        spends=True,
    )
    free = JobSpec(
        name="free",
        schedule=DORMANT_CRON,
        handler=_write_marker,
        candidates=_every_workspace(),
    )
    runner = JobRunner(
        bindings=bindings_from((), (spends, free)),
        manifests=(),
        spend=SpendGates(
            gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),),
            own_key_slots=(OWN_KEY_SLOT,),
        ),
    )

    held = await runner.candidates(f"{CORE_EXTENSION}:spends")
    assert refused not in held
    assert allowed in held
    assert own_key in held

    assert refused in await runner.candidates(f"{CORE_EXTENSION}:free")
