from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_sample.census import NOTED_STAGE, NOTED_STEP, NOTER_CHATTED_STEP, first_noted
from ufo_ext_sample.tools import NoteInput, note
from ufo_testsupport.invoker import RecordingInvoker

from ufo import serve
from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.registry import ModelRegistry
from ufo.product import (
    ACTIVE_1D_STAGE,
    ACTIVE_7D_STAGE,
    CHATTED_STAGE,
    CORE_STAGES,
    MEMBER_CHATTED_STEP,
    ONBOARDING_STEP_METRIC,
    PRODUCT_CENSUS_JOB,
    PRODUCT_STAGE_METRIC,
    SEATED_STAGE,
    WORKSPACE_CREATED_STEP,
    CensusSpec,
    ProductCensus,
)
from ufo.runtime.ext.context import CORE_EXTENSION, context_for
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.jobs import JobRunner
from ufo.runtime.sources.sync import CorePageFeed, SyncDriver
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, Agent, TerminalFrame, Turn

MISSING_TABLE = sa.Table(
    "sample_ext_missing", sa.MetaData(), sa.Column("noted_at", sa.DateTime(timezone=True))
)


def _reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    return reader


def _points(reader: InMemoryMetricReader) -> dict[str, list[object]]:
    data = reader.get_metrics_data()
    return {
        metric.name: list(metric.data.data_points)
        for resource in (data.resource_metrics if data is not None else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _stages(reader: InMemoryMetricReader) -> dict[str, int]:
    return {
        point.attributes["stage"]: point.value
        for point in _points(reader).get(f"ufo.{PRODUCT_STAGE_METRIC}", [])
    }


def _steps(reader: InMemoryMetricReader) -> set[str]:
    return {
        point.attributes["step"]
        for point in _points(reader).get(f"ufo.{ONBOARDING_STEP_METRIC}", [])
    }


async def _workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, noter, chatter, agent_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member_id, email in ((noter, "noter@work.com"), (chatter, "chatter@work.com")):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
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
    return workspace_id, noter, chatter, agent_id


async def _member_turn(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> None:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
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


async def _write_note(workspace_id: UUID, member_id: UUID, tmp_path: Path) -> None:
    ctx = ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="note this",
            created_at=datetime(2026, 9, 24, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=context_for(sample.NAME, frozenset()),
    )
    with ws(workspace_id):
        await note(ctx, NoteInput(text="first"))


def _served_jobs(
    manifests: tuple[Manifest, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> JobRunner:
    launched: list[JobRunner] = []

    def _launch(runner: JobRunner) -> None:
        launched.append(runner)

    monkeypatch.setattr(JobRunner, "launch", _launch)
    blob = FilesystemBlobStore(root=tmp_path)
    registry = ModelRegistry(
        specs={spec.id: spec for spec in CORE_MODEL_SPECS},
        pricing=CORE_PRICING,
        auto_model=CORE_MODEL_SPECS[0].id,
    )
    spend, ledger = serve.deploy_spend(manifests, registry, None)
    runtime = SimpleNamespace(
        config=Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
            blob=BlobConfig(backend="filesystem", root=tmp_path),
            sandbox=SandboxConfig(backend="local", proxy_port=0),
        ),
        manifests=manifests,
        dbos=None,
        index=None,
        embed=None,
        blob=blob,
        registry=registry,
        sandboxes=None,
        subagents=None,
        spend=spend,
        ledger=ledger,
    )
    serve._launch_jobs(
        runtime,
        lambda _workspace_id: RecordingInvoker(),
        SyncDriver(backends={}, blob=blob, postgres=False),
        CorePageFeed(blob=blob),
        None,
    )
    return launched[0]


async def test_a_contributed_stage_and_step_count_by_the_census_rules(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, noter, _chatter, agent_id = await _workspace()
    await _member_turn(workspace_id, agent_id, noter)
    census = ProductCensus(contributions=sample.manifest().census)

    before = _reader(monkeypatch)
    with ws(workspace_id):
        await census.count()
    assert _stages(before) == {
        **dict.fromkeys(CORE_STAGES, 0),
        SEATED_STAGE: 1,
        CHATTED_STAGE: 1,
        ACTIVE_1D_STAGE: 1,
        ACTIVE_7D_STAGE: 1,
        NOTED_STAGE: 0,
    }
    assert _steps(before) == {WORKSPACE_CREATED_STEP, MEMBER_CHATTED_STEP}

    await _write_note(workspace_id, noter, tmp_path)
    after = _reader(monkeypatch)
    with ws(workspace_id):
        await census.count()
    assert _stages(after)[NOTED_STAGE] == 1
    assert _steps(after) == {
        WORKSPACE_CREATED_STEP,
        MEMBER_CHATTED_STEP,
        NOTED_STEP,
        NOTER_CHATTED_STEP,
    }


async def test_a_member_step_reads_the_first_turn_of_the_member_it_names(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, noter, chatter, agent_id = await _workspace()
    await _member_turn(workspace_id, agent_id, chatter)
    await _write_note(workspace_id, noter, tmp_path)
    reader = _reader(monkeypatch)

    with ws(workspace_id):
        await ProductCensus(contributions=sample.manifest().census).count()

    assert NOTED_STEP in _steps(reader)
    assert NOTER_CHATTED_STEP not in _steps(reader)


async def test_a_contribution_whose_sql_fails_fails_the_tick_and_counts_nothing(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, _noter, _chatter, _agent_id = await _workspace()
    census = ProductCensus(
        contributions=(
            *sample.manifest().census,
            CensusSpec(
                stage="sample_missing",
                step=None,
                first_at=lambda refs: sa.select(
                    sa.func.min(MISSING_TABLE.c.noted_at)
                ).scalar_subquery(),
            ),
        )
    )
    reader = _reader(monkeypatch)

    with ws(workspace_id), pytest.raises(sa.exc.DBAPIError):
        await census.count()

    assert _stages(reader) == {}
    assert _steps(reader) == set()


@pytest.mark.parametrize(
    ("stage", "step", "refusal"),
    [
        (SEATED_STAGE, None, f"census stage {SEATED_STAGE!r} is counted twice"),
        (None, MEMBER_CHATTED_STEP, f"census step {MEMBER_CHATTED_STEP!r} is counted twice"),
        (NOTED_STAGE, None, f"census stage {NOTED_STAGE!r} is counted twice"),
    ],
)
def test_a_contribution_naming_a_counted_stage_or_step_fails_the_boot(
    stage: str | None,
    step: str | None,
    refusal: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = sample.manifest()
    colliding = replace(
        manifest,
        census=(*manifest.census, CensusSpec(stage=stage, step=step, first_at=first_noted)),
    )

    with pytest.raises(RuntimeError, match=refusal):
        _served_jobs((colliding,), tmp_path, monkeypatch)


async def test_serve_counts_the_census_every_active_manifest_contributes(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, noter, _chatter, agent_id = await _workspace()
    await _member_turn(workspace_id, agent_id, noter)
    await _write_note(workspace_id, noter, tmp_path)
    runner = _served_jobs((sample.manifest(),), tmp_path, monkeypatch)
    reader = _reader(monkeypatch)

    await runner.fire(f"{CORE_EXTENSION}:{PRODUCT_CENSUS_JOB}", workspace_id)

    assert _stages(reader)[NOTED_STAGE] == 1
    assert {NOTED_STEP, NOTER_CHATTED_STEP} <= _steps(reader)


def test_a_contribution_names_a_stage_or_a_step() -> None:
    with pytest.raises(ValueError, match="names a stage, a step, or both"):
        CensusSpec(stage=None, step=None, first_at=first_noted)
