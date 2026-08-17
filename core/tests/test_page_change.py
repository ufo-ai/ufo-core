"""The data-plane page_change seam and its core runner, proven through the installed sample.

The sample registers a page_change hook that records — through its own scoped store — the page ids
each delivered batch carried and whether the runner wired the model into its off-turn context. A
core test seeds a real page, drives the runner, and reads those rows back through the public
ScopedStore: the runner delivers changed pages, advances each consumer's own cursor (a second drive
over the same window delivers only the newly-changed page), and builds the jobs-way context with
the model wired. `core_jobs` registers one `page_change:<ext>:<hook>` job per consumer, so a
consumer that raises fails only its own workflow — proven by driving one consumer that raises and
confirming its
cursor did not advance while a second consumer still makes progress. A consumer that advances its
own cursor from inside its handler stands in for the writer that overlaps a slow tick: the runner
compare-and-sets through `ScopedStore.put_if`, so the newer value survives and the drive stops
instead of rewinding the cursor and replaying the batch. No mock call-log — a real consumer records
through its capability APIs."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ufo import o11y
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import HookContext, HookOutcome, HookSpec, Manifest, PageChangeBatch
from ufo.jobs import (
    CORE_EXTENSION,
    PAGE_CHANGE_BATCH,
    PAGE_CHANGE_CURSOR_KEY,
    PAGE_CHANGE_JOB,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
)
from ufo.loop.delivery import DeliverySweep
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import Message, ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.o11y import BACKGROUND_PROFILE
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.sources.sync import (
    CorePageFeed,
    FolderSource,
    PageBatch,
    PageChange,
    SyncDriver,
    page_cursor,
)
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import ws

BACKGROUND_MODEL = "gpt-5.6-luna"
RACER_EXTENSION = "racer_ext"
RACER_CURSOR_KEY = f"{PAGE_CHANGE_CURSOR_KEY}:_advance_the_cursor_then_record"
CONCURRENT_CURSOR = f"9000|{UUID(int=9000)}"
DELIVERED_SIZES_KEY = "hook:delivered_batch_sizes"


def _sample_manifest() -> object:
    found = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert found is not None, "sample extension not discovered — run `uv sync`"
    return found


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_page(blob: FilesystemBlobStore, workspace_id: UUID, body: str) -> UUID:
    source_id, page_id = uuid4(), uuid4()
    when = datetime.now(UTC)
    await blob.put(f"pages/{page_id}", body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                cursor=None,
                next_sync_at=when,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                body_ref=f"pages/{page_id}",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=when,
                updated_at=when,
            )
        )
    return page_id


def _runner(
    blob: FilesystemBlobStore,
    manifests: tuple[object, ...] = (),
    registry: ModelRegistry | None = None,
) -> PageChangeRunner:
    return PageChangeRunner(
        manifests=manifests or (_sample_manifest(),),
        pages=CorePageFeed(blob=blob),
        registry=registry,
    )


async def _drive_all(runner: PageChangeRunner) -> None:
    for consumer in runner.consumers():
        await runner.drive(consumer)


@dataclass(frozen=True)
class _StubModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="one fact")
        yield Usage(input_tokens=6, output_tokens=2)


def _stub_registry() -> ModelRegistry:
    return ModelRegistry(
        specs={
            spec.id: replace(spec, client=lambda spec, key: _StubModel(), key_slot="", key_env="")
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-5",
    )


async def test_runner_delivers_changed_pages_and_advances_the_cursor(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_one = await _seed_page(blob, workspace_id, "the first page body")
    runner = _runner(blob)
    with ws(workspace_id):
        await _drive_all(runner)

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        first = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert first == {"page_ids": [str(page_one)], "model_wired": False}

    page_two = await _seed_page(blob, workspace_id, "the second page body")
    with ws(workspace_id):
        await _drive_all(runner)
    with ws(workspace_id):
        second = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert second == {"page_ids": [str(page_two)], "model_wired": False}


async def test_runner_wires_the_model_into_the_off_turn_context(db: None, tmp_path: object) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "a page for model wiring")
    registry = ModelRegistry(specs={}, pricing=CORE_PRICING, auto_model="claude-opus-4-8")
    with ws(workspace_id):
        await _drive_all(_runner(blob, registry=registry))

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert record is not None
    assert record["model_wired"] is True


async def test_a_page_change_consumer_runs_on_the_background_jobs_model(
    db: None, tmp_path: object
) -> None:
    """The fact deriver's distillation pass is a page_change consumer, so its one metered call is a
    background job's call: the runner hands it the deploy registry with its default replaced by the
    background-jobs model, and leaves the deploy registry a member turn resolves through alone."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "a page for the background model")
    registry = ModelRegistry(specs={}, pricing=CORE_PRICING, auto_model="claude-opus-5")
    seen: list[str] = []

    async def _record_model(ctx: HookContext) -> HookOutcome:
        assert ctx.ext.model is not None
        seen.append(ctx.ext.model.model)
        return None

    runner = PageChangeRunner(
        manifests=(
            Manifest(
                name="deriver_ext",
                version="0",
                hooks=(HookSpec(event="page_change", handler=_record_model),),
            ),
        ),
        pages=CorePageFeed(blob=blob),
        registry=registry,
        background_model="gpt-5.6-luna",
    )
    with ws(workspace_id):
        await _drive_all(runner)

    assert seen == ["gpt-5.6-luna"]
    assert registry.auto_model == "claude-opus-5"


async def test_a_consumer_meters_its_model_call_under_its_own_page_change_job(
    db: None, tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each consumer drives as its own job, so each one's model spend reads apart: the fact
    deriver's distillation is labelled with the `core:page_change:<ext>:<hook>` key its JobSpec is
    registered under, not with the extension that declared the hook."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "a page the deriver distills")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})

    async def _derive_facts(ctx: HookContext) -> HookOutcome:
        assert ctx.ext.model is not None
        await ctx.ext.model.turn(
            ModelRequest(
                model="auto",
                system="distill",
                messages=(Message(role="user", content="one page"),),
                max_tokens=64,
            )
        )
        return None

    runner = PageChangeRunner(
        manifests=(
            Manifest(
                name="deriver_ext",
                version="0",
                hooks=(HookSpec(event="page_change", handler=_derive_facts),),
            ),
        ),
        pages=CorePageFeed(blob=blob),
        registry=_stub_registry(),
        background_model=BACKGROUND_MODEL,
    )
    with ws(workspace_id):
        await _drive_all(runner)

    data = reader.get_metrics_data()
    assert data is not None
    tokens = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.model_round_tokens_total"
        for point in metric.data.data_points
    ]
    assert {point.attributes["job"] for point in tokens} == {
        f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:deriver_ext:_derive_facts"
    }
    assert {point.attributes["profile"] for point in tokens} == {BACKGROUND_PROFILE}


async def _raise(ctx: HookContext) -> HookOutcome:
    raise RuntimeError("page_change consumer exploded")


def test_each_page_change_consumer_registers_as_its_own_job(tmp_path: object) -> None:
    """core_jobs fans a `page_change:<ext>:<hook>` job out per registered consumer, so the memory
    page indexer and fact deriver are independent DBOS workflows — each keyed in the core namespace
    under its extension and its handler-name discriminator, so two hooks in one extension get two
    jobs rather than colliding on one name."""
    blob = FilesystemBlobStore(root=tmp_path)
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = PageChangeRunner(
        manifests=(_sample_manifest(), boom),
        pages=CorePageFeed(blob=blob),
    )
    specs = core_jobs(
        SyncDriver(backends={"folder": FolderSource()}, blob=blob, postgres=False),
        TurnDispatcher(client=None),
        runner,
        DeliverySweep(invoker_for=lambda _: None, registry=SubagentRegistry(())),
    )
    page_change = [spec.name for spec in specs if spec.name.startswith(f"{PAGE_CHANGE_JOB}:")]
    assert page_change == [
        f"{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change",
        f"{PAGE_CHANGE_JOB}:boom_ext:_raise",
    ]
    keys = {binding.key for binding in bindings_from((), specs)}
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change" in keys
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:boom_ext:_raise" in keys


async def test_a_failing_consumer_neither_advances_its_cursor_nor_blocks_another(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page = await _seed_page(blob, workspace_id, "a page both consumers replay")
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = _runner(blob, manifests=(boom, _sample_manifest()))
    consumers = {consumer.extension: consumer for consumer in runner.consumers()}
    boom_consumer, sample_consumer = consumers["boom_ext"], consumers[sample.NAME]

    with pytest.raises(RuntimeError), ws(workspace_id):
        await runner.drive(boom_consumer)
    with ws(workspace_id):
        boom_cursor = await ScopedStore(extension="boom_ext").get(
            f"{PAGE_CHANGE_CURSOR_KEY}:{boom_consumer.discriminator}"
        )
    assert boom_cursor is None

    with ws(workspace_id):
        await runner.drive(sample_consumer)
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
        sample_cursor = await scoped.get(
            f"{PAGE_CHANGE_CURSOR_KEY}:{sample_consumer.discriminator}"
        )
    assert record == {"page_ids": [str(page)], "model_wired": False}
    assert isinstance(sample_cursor, str) and sample_cursor != boom_cursor


@dataclass(frozen=True)
class _SyntheticPages:
    revisions: int

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        after = 0 if cursor is None else page_cursor(cursor)[0]
        window = tuple(
            self._page(revision)
            for revision in range(after + 1, min(after + limit, self.revisions) + 1)
        )
        if not window:
            return PageBatch(changes=(), next_cursor=None)
        return PageBatch(changes=window, next_cursor=f"{window[-1].revision}|{window[-1].page_id}")

    def _page(self, revision: int) -> PageChange:
        when = datetime(2026, 8, 1, tzinfo=UTC)
        return PageChange(
            page_id=UUID(int=revision),
            source_id=UUID(int=0),
            subject=SHARED_SUBJECT,
            stream="pages",
            title=f"page {revision}",
            body=f"the body of page {revision}",
            digest=f"sha256:{revision:064x}",
            revision=revision,
            tombstone=False,
            created_at=when,
            as_of=when,
            changed_at=when,
        )


async def _advance_the_cursor_then_record(ctx: HookContext) -> HookOutcome:
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            delivered = await ctx.ext.store.get(DELIVERED_SIZES_KEY)
            sizes = [] if delivered is None else list(delivered)
            await ctx.ext.store.put(DELIVERED_SIZES_KEY, [*sizes, len(changes)])
            await ctx.ext.store.put(RACER_CURSOR_KEY, CONCURRENT_CURSOR)
    return None


async def test_a_cursor_another_writer_advanced_is_not_rewound_by_the_drive(db: None) -> None:
    """A consumer whose handler runs long enough for another writer — an overlapping tick of the
    same per-minute job, or the eval settling the deriver — to move the cursor on. The drive holds
    the older value it read, so writing it back would rewind the cursor and replay the batch the
    other writer already accounted for. It compare-and-sets instead: the newer value stands and the
    drive stops, delivering one batch and no replay."""
    workspace_id = await _workspace()
    racer = Manifest(
        name=RACER_EXTENSION,
        version="0",
        hooks=(HookSpec(event="page_change", handler=_advance_the_cursor_then_record),),
    )
    runner = PageChangeRunner(
        manifests=(racer,), pages=_SyntheticPages(revisions=PAGE_CHANGE_BATCH + 1)
    )
    (consumer,) = runner.consumers()

    with ws(workspace_id):
        await runner.drive(consumer)
        scoped = ScopedStore(extension=RACER_EXTENSION)
        cursor = await scoped.get(f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}")
        delivered = await scoped.get(DELIVERED_SIZES_KEY)
    assert cursor == CONCURRENT_CURSOR
    assert delivered == [PAGE_CHANGE_BATCH]


async def test_put_if_writes_only_while_the_stored_value_still_matches(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        scoped = ScopedStore(extension=RACER_EXTENSION)
        await scoped.put("cursor", "10|a")
        assert await scoped.put_if("cursor", "11|b", expected="9|z") is False
        assert await scoped.get("cursor") == "10|a"
        assert await scoped.put_if("cursor", "11|b", expected="10|a") is True
        assert await scoped.get("cursor") == "11|b"


async def test_put_if_inserts_a_missing_key_but_never_clobbers_one_a_racer_created(
    db: None,
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        scoped = ScopedStore(extension=RACER_EXTENSION)
        assert await scoped.put_if("cursor", "10|a", expected=None) is True
        assert await scoped.get("cursor") == "10|a"
        assert await scoped.put_if("cursor", "11|b", expected=None) is False
        assert await scoped.get("cursor") == "10|a"
