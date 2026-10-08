"""The data-plane page_change seam and its core runner, proven through the installed sample.

The sample registers a page_change hook that records — through its own scoped store — the page ids
each delivered batch carried and whether the runner wired the model into its off-turn context. A
core test seeds a real page, drives the runner, and reads those rows back through the public
ScopedStore: the runner delivers changed pages, advances each consumer's own cursor (a second drive
over the same window delivers only the newly-changed page), and builds the jobs-way context with
the model wired. `core_jobs` registers one `page_change:<ext>:<hook>` job per consumer, so a
consumer that raises fails only its own workflow — proven by driving one consumer that refuses the
page and confirming it parked the page in its own key space while a second consumer still indexes
it. A refusal a batch earns as a whole is retried a page at a time; a page refused on its own is
parked, stepped over, and re-delivered on its own hour, so no one page holds a workspace's
consumer — and past PAGE_CHANGE_PARK_MAX parked pages the drive stops at its cursor instead,
because a consumer refusing everything is not a page's fault. A consumer that advances its
own cursor from inside its handler stands in for the writer that overlaps a slow tick: the runner
compare-and-sets through `ScopedStore.put_if`, so the newer value survives and the drive stops
instead of rewinding the cursor and replaying the batch. No mock call-log — a real consumer records
through its capability APIs."""

import hashlib
import json
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import JsonValue
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from ufo_ext_sample.hooks import HOOK_PAGE_CHANGE_KEY
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.sources_service import SOURCES_WIRE, SourcesServiceStandIn

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import Message, ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.o11y import BACKGROUND_PROFILE
from ufo.harness.sandbox.session import ExecResult
from ufo.host.ext.loader import load_manifests
from ufo.product import ProductCensus
from ufo.runtime.background_tasks import BackgroundTaskSweep
from ufo.runtime.delivery import DeliverySweep
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY,
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
    PageChangeBatch,
)
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    PAGE_CHANGE_BATCH,
    PAGE_CHANGE_BATCH_REFUSED_KEY,
    PAGE_CHANGE_JOB,
    PAGE_CHANGE_PARK_MAX,
    PAGE_CHANGE_PARK_RETRY_SECONDS,
    PAGE_CHANGE_PARK_STRIKES,
    PAGE_CHANGE_PARKED_KEY,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
    model_key_slots,
)
from ufo.runtime.pages import page_cursor
from ufo.runtime.sources.sync import FolderSource, SyncDriver
from ufo.runtime.sources_api import SourceLinks, SourcesFeed
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Usage

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


GOLDEN_ITEM = json.loads(SOURCES_WIRE.read_text(encoding="utf-8"))["sources.changes"]["answer"][
    "body"
]["items"][0]


@dataclass
class _Sources:
    stand_in: SourcesServiceStandIn = field(default_factory=SourcesServiceStandIn)
    items: list[dict[str, object]] = field(default_factory=list)

    def add(self, body: str) -> UUID:
        page_id = uuid4()
        self.items.append(
            {
                **GOLDEN_ITEM,
                "page_id": str(page_id),
                "title": f"page {len(self.items) + 1}",
                "body": body,
                "digest": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                "revision": len(self.items) + 1,
            }
        )
        return page_id

    def feed(self) -> SourcesFeed:
        return SourcesFeed(
            apis=cloud_apis_for(
                Starlette(routes=[Route("/v1/sources/changes", self)]),
                self.stand_in.app,
            ),
            links=SourceLinks(entries={}),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        query = parse_qs(scope["query_string"].decode())
        after = (0, UUID(int=0)) if "cursor" not in query else page_cursor(query["cursor"][0])
        window = [
            item for item in self.items if (item["revision"], UUID(str(item["page_id"]))) > after
        ][: int(query["limit"][0])]
        cursor = None if not window else f"{window[-1]['revision']}|{window[-1]['page_id']}"
        self.stand_in.queue("sources.changes", [(200, {"items": window, "next_cursor": cursor})])
        await self.stand_in.app(scope, receive, send)


def _runner(
    sources: _Sources,
    manifests: tuple[object, ...] = (),
    registry: ModelRegistry | None = None,
) -> PageChangeRunner:
    return PageChangeRunner(
        manifests=manifests or (_sample_manifest(),),
        pages=sources.feed(),
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


async def test_runner_delivers_changed_pages_and_advances_the_cursor(db: None) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    page_one = sources.add("the first page body")
    runner = _runner(sources)
    with ws(workspace_id):
        await _drive_all(runner)

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        first = await scoped.get(HOOK_PAGE_CHANGE_KEY)
    assert first == {"page_ids": [str(page_one)], "model_wired": False}

    page_two = sources.add("the second page body")
    with ws(workspace_id):
        await _drive_all(runner)
    with ws(workspace_id):
        second = await scoped.get(HOOK_PAGE_CHANGE_KEY)
    assert second == {"page_ids": [str(page_two)], "model_wired": False}


async def test_runner_wires_the_model_into_the_off_turn_context(db: None) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    sources.add("a page for model wiring")
    registry = ModelRegistry(specs={}, pricing=CORE_PRICING, auto_model="claude-opus-4-8")
    with ws(workspace_id):
        await _drive_all(_runner(sources, registry=registry))

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(HOOK_PAGE_CHANGE_KEY)
    assert record is not None
    assert record["model_wired"] is True


async def test_a_page_change_consumer_runs_on_the_background_jobs_model(db: None) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    sources.add("a page for the background model")
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
        pages=sources.feed(),
        registry=registry,
        background_model="gpt-5.6-luna",
    )
    with ws(workspace_id):
        await _drive_all(runner)

    assert seen == ["gpt-5.6-luna"]
    assert registry.auto_model == "claude-opus-5"


async def test_a_consumer_meters_its_model_call_under_its_own_page_change_job(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    sources.add("a page the deriver distills")
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
                conversation_cache_ttl="5m",
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
        pages=sources.feed(),
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


class _NoProbes:
    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult:
        raise AssertionError("no probe expected")


def test_each_page_change_consumer_registers_as_its_own_job(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = PageChangeRunner(manifests=(_sample_manifest(), boom), pages=_Sources().feed())
    specs = core_jobs(
        SyncDriver(backends={"folder": FolderSource()}, blob=blob, postgres=False),
        TurnDispatcher(client=None),
        runner,
        DeliverySweep(invoker_for=lambda _: None, registry=SubagentRegistry(())),
        BackgroundTaskSweep(probes=_NoProbes(), invoker_for=lambda _: None),
        None,
        ProductCensus(contributions=()),
    )
    page_change = [spec.name for spec in specs if spec.name.startswith(f"{PAGE_CHANGE_JOB}:")]
    assert page_change == [
        f"{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change",
        f"{PAGE_CHANGE_JOB}:boom_ext:_raise",
    ]
    keys = {binding.key for binding in bindings_from((), specs)}
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change" in keys
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:boom_ext:_raise" in keys


async def test_a_page_a_consumer_keeps_refusing_is_parked_and_blocks_no_other(db: None) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    page = sources.add("a page both consumers replay")
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = _runner(sources, manifests=(boom, _sample_manifest()))
    consumers = {consumer.extension: consumer for consumer in runner.consumers()}
    boom_consumer, sample_consumer = consumers["boom_ext"], consumers[sample.NAME]
    boom_store = ScopedStore(extension="boom_ext")
    cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{boom_consumer.discriminator}"
    parked_key = f"{PAGE_CHANGE_PARKED_KEY}:{boom_consumer.discriminator}"

    for _ in range(PAGE_CHANGE_PARK_STRIKES - 1):
        with pytest.raises(RuntimeError), ws(workspace_id):
            await runner.drive(boom_consumer)
        with ws(workspace_id):
            assert await boom_store.get(cursor_key) is None
            assert await boom_store.get(parked_key) is None

    with ws(workspace_id):
        await runner.drive(boom_consumer)
        boom_cursor = await boom_store.get(cursor_key)
        boom_parked = await boom_store.get(parked_key)
    assert isinstance(boom_parked, list)
    assert [entry["page_id"] for entry in boom_parked] == [str(page)]
    assert isinstance(boom_cursor, str) and boom_cursor.endswith(f"|{page}")

    with ws(workspace_id):
        await runner.drive(sample_consumer)
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(HOOK_PAGE_CHANGE_KEY)
        sample_parked = await scoped.get(
            f"{PAGE_CHANGE_PARKED_KEY}:{sample_consumer.discriminator}"
        )
    assert record == {"page_ids": [str(page)], "model_wired": False}
    assert sample_parked is None


@dataclass
class _Taker:
    refused: set[UUID] = field(default_factory=set)
    taken: list[UUID] = field(default_factory=list)
    refuses_batches: bool = False

    def reset(self, refused: Iterable[UUID] = (), refuses_batches: bool = False) -> None:
        self.refused = set(refused)
        self.taken = []
        self.refuses_batches = refuses_batches


_TAKER = _Taker()


async def _take_unrefused_pages(ctx: HookContext) -> HookOutcome:
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            if _TAKER.refuses_batches and len(changes) > 1:
                raise RuntimeError(f"a batch of {len(changes)} is over the provider's cap")
            for change in changes:
                if change.page_id in _TAKER.refused:
                    raise RuntimeError(f"never taking {change.page_id}")
            _TAKER.taken.extend(change.page_id for change in changes)
    return None


def _taker_manifest() -> Manifest:
    return Manifest(
        name="taker_ext",
        version="0",
        hooks=(HookSpec(event="page_change", handler=_take_unrefused_pages),),
    )


async def _taker_state(discriminator: str) -> tuple[JsonValue | None, JsonValue | None]:
    store = ScopedStore(extension="taker_ext")
    return (
        await store.get(f"{PAGE_CHANGE_CURSOR_KEY}:{discriminator}"),
        await store.get(f"{PAGE_CHANGE_PARKED_KEY}:{discriminator}"),
    )


async def test_a_batch_the_handler_refuses_whole_is_retried_a_page_at_a_time(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A refusal a batch earns as a whole — the embedding request its bodies add up to is over
    the provider's token cap — is not any one page's."""
    workspace_id = await _workspace()
    sources = _Sources()
    pages = [sources.add(f"page {n}") for n in range(3)]
    _TAKER.reset(refuses_batches=True)
    runner = _runner(sources, manifests=(_taker_manifest(),))
    (consumer,) = runner.consumers()

    with caplog.at_level(logging.WARNING, logger="ufo"), ws(workspace_id):
        await runner.drive(consumer)
        cursor, parked = await _taker_state(consumer.discriminator)

    assert _TAKER.taken == pages
    assert parked is None
    assert isinstance(cursor, str) and cursor.endswith(f"|{pages[-1]}")
    narrowed = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "jobs.page_change_narrowed"
    ]
    assert len(narrowed) == 1
    assert narrowed[0]["pages"] == 3
    assert narrowed[0]["extension"] == "taker_ext"


async def test_a_batch_scoped_handler_retries_then_narrows(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_id = await _workspace()
    sources = _Sources()
    pages = [
        sources.add("first"),
        sources.add("second"),
    ]
    _TAKER.reset(refuses_batches=True)
    manifest = Manifest(
        name="taker_ext",
        version="0",
        hooks=(
            HookSpec(
                event="page_change",
                handler=_take_unrefused_pages,
                page_change_failure_scope="batch",
            ),
        ),
    )
    runner = _runner(sources, manifests=(manifest,))
    (consumer,) = runner.consumers()
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})

    for n in range(PAGE_CHANGE_PARK_STRIKES - 1):
        with pytest.raises(RuntimeError), ws(workspace_id):
            await runner.drive(consumer)
        pages.append(sources.add(f"new {n}"))

    with caplog.at_level(logging.WARNING, logger="ufo"), ws(workspace_id):
        await runner.drive(consumer)
        cursor, parked = await _taker_state(consumer.discriminator)
        held = await ScopedStore(extension="taker_ext").get(
            f"{PAGE_CHANGE_BATCH_REFUSED_KEY}:{consumer.discriminator}"
        )

    assert _TAKER.taken == pages
    assert isinstance(cursor, str) and cursor.endswith(f"|{pages[-1]}")
    assert parked is None
    assert held is None
    assert _counted(reader, "ufo.page_change_stalled_total") == PAGE_CHANGE_PARK_STRIKES - 1
    narrowed = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "jobs.page_change_narrowed"
    ]
    assert len(narrowed) == 1
    assert narrowed[0]["pages"] == len(pages)


async def test_a_page_the_handler_never_takes_is_parked_and_the_rest_move_past_it(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One page the handler can never accept used to hold every later page in the workspace
    behind it, replayed every tick forever."""
    workspace_id = await _workspace()
    sources = _Sources()
    pages = [sources.add(f"page {n}") for n in range(3)]
    _TAKER.reset(refused=[pages[1]])
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    runner = _runner(sources, manifests=(_taker_manifest(),))
    (consumer,) = runner.consumers()

    for _ in range(PAGE_CHANGE_PARK_STRIKES - 1):
        with pytest.raises(RuntimeError), ws(workspace_id):
            await runner.drive(consumer)
    assert _TAKER.taken == [pages[0]]

    with caplog.at_level(logging.ERROR, logger="ufo"), ws(workspace_id):
        await runner.drive(consumer)
        cursor, parked = await _taker_state(consumer.discriminator)

    assert _TAKER.taken == [pages[0], pages[2]]
    assert isinstance(cursor, str) and cursor.endswith(f"|{pages[2]}")
    assert isinstance(parked, list)
    assert [entry["page_id"] for entry in parked] == [str(pages[1])]
    assert [str(entry["cursor"]).endswith(f"|{pages[0]}") for entry in parked] == [True]
    logged = [
        record.ufo for record in caplog.records if record.getMessage() == "jobs.page_change_parked"
    ]
    assert len(logged) == 1
    assert logged[0]["page_id"] == str(pages[1])
    assert logged[0]["error_class"] == "RuntimeError"
    assert _counted(reader, "ufo.page_change_parked_total") == 1
    assert _counted(reader, "ufo.page_change_stalled_total") == PAGE_CHANGE_PARK_STRIKES - 1


async def test_a_parked_page_waits_its_hour_then_lands_on_its_own(db: None) -> None:
    """A parked page is retried off the main line, so whatever refused it — fixed — indexes it
    without anyone rewinding a cursor. Until its hour is up it costs the tick nothing."""
    workspace_id = await _workspace()
    sources = _Sources()
    pages = [sources.add(f"page {n}") for n in range(2)]
    _TAKER.reset(refused=[pages[0]])
    runner = _runner(sources, manifests=(_taker_manifest(),))
    (consumer,) = runner.consumers()
    parked_key = f"{PAGE_CHANGE_PARKED_KEY}:{consumer.discriminator}"

    for _ in range(PAGE_CHANGE_PARK_STRIKES - 1):
        with pytest.raises(RuntimeError), ws(workspace_id):
            await runner.drive(consumer)

    with ws(workspace_id):
        await runner.drive(consumer)
        assert _TAKER.taken == [pages[1]]

        _TAKER.reset()
        await runner.drive(consumer)
        assert _TAKER.taken == []

        store = ScopedStore(extension="taker_ext")
        entries = await store.get(parked_key)
        assert isinstance(entries, list)
        aged = datetime.now(UTC) - timedelta(seconds=PAGE_CHANGE_PARK_RETRY_SECONDS + 60)
        await store.put(parked_key, [{**entries[0], "tried_at": aged.isoformat()}])

        await runner.drive(consumer)
        assert _TAKER.taken == [pages[0]]
        assert await store.get(parked_key) == []


async def test_a_consumer_refusing_every_page_stops_at_its_cursor_and_counts(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Parking is for a page, not for a consumer."""
    workspace_id = await _workspace()
    sources = _Sources()
    sources.add("a page the handler will never accept")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = _runner(sources, manifests=(boom,))
    (consumer,) = runner.consumers()
    store = ScopedStore(extension="boom_ext")
    parked_key = f"{PAGE_CHANGE_PARKED_KEY}:{consumer.discriminator}"
    cursor_key = f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}"
    full = [
        {
            "cursor": f"{n}|{uuid4()}",
            "page_id": str(uuid4()),
            "tried_at": datetime.now(UTC).isoformat(),
        }
        for n in range(PAGE_CHANGE_PARK_MAX)
    ]
    replays = PAGE_CHANGE_PARK_STRIKES + 1

    with ws(workspace_id):
        await store.put(parked_key, full)
    with caplog.at_level(logging.ERROR, logger="ufo"):
        for _ in range(replays):
            with pytest.raises(RuntimeError), ws(workspace_id):
                await runner.drive(consumer)
    with ws(workspace_id):
        held, cursor = await store.get(parked_key), await store.get(cursor_key)

    assert held == full
    assert cursor is None
    stalled = [
        record.ufo for record in caplog.records if record.getMessage() == "jobs.page_change_stalled"
    ]
    assert len(stalled) == replays
    assert stalled[-1]["extension"] == "boom_ext"
    assert stalled[-1]["workspace_id"] == str(workspace_id)
    assert stalled[-1]["error_class"] == "RuntimeError"
    assert stalled[-1]["parked"] == PAGE_CHANGE_PARK_MAX
    assert _counted(reader, "ufo.page_change_stalled_total") == replays
    assert _counted(reader, "ufo.page_change_parked_total") == 0


def _counted(reader: InMemoryMetricReader, name: str) -> float:
    data = reader.get_metrics_data()
    assert data is not None
    return sum(
        point.value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
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
    same per-minute job, or the eval settling the deriver — to move the cursor on."""
    workspace_id = await _workspace()
    racer = Manifest(
        name=RACER_EXTENSION,
        version="0",
        hooks=(HookSpec(event="page_change", handler=_advance_the_cursor_then_record),),
    )
    sources = _Sources()
    for revision in range(PAGE_CHANGE_BATCH + 1):
        sources.add(f"the body of page {revision}")
    runner = PageChangeRunner(manifests=(racer,), pages=sources.feed())
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


def test_model_key_slots_name_every_slot_the_deploys_models_key_from() -> None:
    """The hold reads every key slot a model of the deploy keys from, once each and without the
    keyless specs; none at all with no registry, which is a deploy with no model to key."""
    keyed, keyless, *rest = sorted(spec.id for spec in CORE_MODEL_SPECS)
    specs = {
        spec.id: replace(spec, key_slot="" if spec.id == keyless else f"{spec.id}-key")
        for spec in CORE_MODEL_SPECS
    }
    specs[keyed] = replace(specs[keyed], key_slot=f"{rest[0]}-key")
    registry = ModelRegistry(specs=specs, pricing=CORE_PRICING, auto_model=keyed)
    assert model_key_slots(registry) == tuple(sorted(f"{model}-key" for model in rest))
    assert model_key_slots(None) == ()
