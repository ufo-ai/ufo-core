from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_sample.metrics import (
    CALL_ACTIVE_METRIC,
    CALL_DIMENSION,
    CALL_LATENCY_METRIC,
    CALL_METRIC,
)
from ufo_ext_sample.tools import TOOL_NAME, EchoInput, echo
from ufo_ext_todos import LIST_WRITTEN_METRIC

import ufo.host.ext.loader as loader
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.o11y import MetricSpec, emit_histogram, emit_metric
from ufo.host.ext.loader import load_manifests
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn


def _reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    monkeypatch.setattr(o11y, "_up_down_counters", {})
    return reader


def _points(reader: InMemoryMetricReader) -> dict[str, list[object]]:
    data = reader.get_metrics_data()
    return {
        metric.name: list(metric.data.data_points)
        for resource in (data.resource_metrics if data is not None else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _entry(manifest: Manifest) -> SimpleNamespace:
    return SimpleNamespace(
        dist=SimpleNamespace(name="ufo"),
        module=f"ufo_ext_{manifest.name}",
        load=lambda: lambda: manifest,
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_active_extension_emits_the_metrics_its_manifest_declares(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    load_manifests()
    reader = _reader(monkeypatch)
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
            inbound="hi",
            created_at=datetime(2026, 9, 24, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        ext=context_for(sample.NAME, frozenset()),
    )

    with ws(workspace_id):
        result = await echo(ctx, EchoInput(message="counted"))

    assert result.content[0].text == "counted"
    points = _points(reader)
    assert [(dict(p.attributes), p.value) for p in points[f"ufo.{CALL_METRIC}"]] == [
        ({CALL_DIMENSION: TOOL_NAME, "workspace_id": str(workspace_id)}, 1)
    ]
    assert [p.count for p in points[f"ufo.{CALL_LATENCY_METRIC}"]] == [1]
    assert [p.value for p in points[f"ufo.{CALL_ACTIVE_METRIC}"]] == [0]


def test_a_pack_that_leaves_an_extension_out_leaves_its_metrics_undeclared() -> None:
    load_manifests("assistant")

    with pytest.raises(ValueError, match=f"unknown metric: {CALL_METRIC}"):
        emit_metric(CALL_METRIC, call=TOOL_NAME)
    emit_metric(LIST_WRITTEN_METRIC, tasks="1")


def test_a_declared_name_holds_its_kind_and_its_dimensions() -> None:
    load_manifests()

    with pytest.raises(ValueError, match=f"unknown histogram: {CALL_METRIC}"):
        emit_histogram(CALL_METRIC, 1, call=TOOL_NAME)
    with pytest.raises(ValueError, match=f"undeclared dimensions on {CALL_METRIC}: reason"):
        emit_metric(CALL_METRIC, call=TOOL_NAME, reason="late")
    with pytest.raises(ValueError, match="unknown metric: sample_undeclared_total"):
        emit_metric("sample_undeclared_total")


@pytest.mark.parametrize(
    ("name", "refusal"),
    [
        (CALL_METRIC, f"extensions 'sample' and 'rogue' both declare metric '{CALL_METRIC}'"),
        (
            "turn_started_total",
            "extension 'rogue' declares metric 'turn_started_total', which core",
        ),
    ],
)
def test_a_metric_declared_twice_fails_the_load(
    monkeypatch: pytest.MonkeyPatch, name: str, refusal: str
) -> None:
    rogue = Manifest(name="rogue", version="0", metrics=(MetricSpec(name=name, kind="counter"),))
    monkeypatch.setattr(
        loader, "entry_points", lambda group: (_entry(sample.manifest()), _entry(rogue))
    )

    with pytest.raises(RuntimeError, match=refusal):
        load_manifests()
