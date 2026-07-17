"""The memory condenser: the fact deriver (page_change → durable facts) and the consolidator (aged
facts → semantic summary), the two producers that make recall's dead machinery fire.

The model is driven through a stub ModelClient returning canned output — never a live model — wired
either through a ModelRegistry (the fact deriver rides the real core page-change runner) or a direct
ModelAccess (the consolidator). The embed client and DefaultIndex are real dependencies, never the
asserted thing: every assertion reads memory_item rows and Recalled values back through the public
store. The seam test proves the memory extension registers two independent page_change consumers."""

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.condenser import (
    MIN_CLUSTER_FACTS,
    MIN_OLDEST_AGE,
    FactDeriver,
    MemoryConsolidator,
)
from ufo_ext_memory.store import (
    FACT,
    KIND_FACT,
    SEMANTIC,
    MemoryStore,
    memory_item,
)

from ufo.accounting import CORE_PRICING, Pricing
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ModelAccess, ScopedStore
from ufo.ext.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
    ModelProviderSpec,
)
from ufo.indexing import OWNER_KIND_MEMORY_ITEM, Chunk
from ufo.jobs import PageChangeRunner, SandboxReaper, TurnDispatcher, core_jobs
from ufo.models.interface import ModelClient, ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.local import LocalCarrier
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.sources.sync import CorePageFeed, FolderSource, PageChange, SyncDriver
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import ws

WHEN = datetime(2026, 1, 1, tzinfo=UTC)
AUTO_MODEL = "claude-opus-4-8"


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """Deterministic stand-in EmbedClient: a dependency of clustering and recall, never asserted."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


@dataclass
class StubModelClient:
    """Streams one canned completion and one usage event, counting completions so a test can witness
    that a resumed cursor replays nothing — never a live model."""

    payload: str
    usage: Usage
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield TextDelta(text=self.payload)
        yield self.usage


def _registry(client: StubModelClient) -> ModelRegistry:
    return ModelRegistry(
        providers=(
            ModelProviderSpec(
                name="stub", matches=lambda model: True, client=lambda model, key: client
            ),
        ),
        pricing=CORE_PRICING,
        auto_model=AUTO_MODEL,
    )


@dataclass
class _Resolver:
    """A ModelResolver standing in for the registry: fixes the deploy default and price table and
    hands back the stub client, so the direct ModelAccess meters without a registry."""

    auto_model: str
    pricing: Pricing
    client: ModelClient

    async def client_for(self, model: str) -> ModelClient:
        return self.client

    def key_slot_for(self, model: str) -> str | None:
        return None


def _model(payload: str) -> ModelAccess:
    return ModelAccess(
        _Resolver(
            AUTO_MODEL,
            CORE_PRICING,
            StubModelClient(payload, Usage(input_tokens=10, output_tokens=5)),
        )
    )


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        await connection.execute(sa.text("delete from memory_item"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    yield


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
    await blob.put(f"pages/{page_id}", body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                cursor=None,
                next_sync_at=WHEN,
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
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
    return page_id


async def _seed_aged_fact(
    workspace_id: UUID, body: str, vector: tuple[float, ...], confidence: int
) -> UUID:
    """Insert a fact aged past MIN_OLDEST_AGE with its one already-derived chunk, so the
    consolidator's aged-fact query admits it and recall can surface it through the index legs."""
    item_id = uuid4()
    created = datetime.now(UTC) - timedelta(hours=48)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                body=body,
                item_class=FACT,
                memory_kind=KIND_FACT,
                confidence=confidence,
                source_ref=None,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created,
                updated_at=created,
            )
        )
    await DefaultIndex(embed=StubEmbed(vector), transaction=workspace_tx).upsert(
        (
            Chunk(
                "d-" + item_id.hex,
                OWNER_KIND_MEMORY_ITEM,
                str(item_id),
                SHARED_SUBJECT,
                0,
                body,
                vector,
            ),
        )
    )
    return item_id


async def _facts(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.subject,
                        memory_item.c.body,
                        memory_item.c.item_class,
                        memory_item.c.memory_kind,
                        memory_item.c.confidence,
                        memory_item.c.source_ref,
                        memory_item.c.superseded_by,
                    ).where(memory_item.c.workspace_id == workspace_id)
                )
            ).all()
        )


def _store(workspace_id: UUID, vector: tuple[float, ...]) -> MemoryStore:
    embed = StubEmbed(vector)
    return MemoryStore(
        index=DefaultIndex(embed=embed, transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
    )


def _runner(
    blob: FilesystemBlobStore,
    vector: tuple[float, ...],
    registry: ModelRegistry | None,
) -> PageChangeRunner:
    embed = StubEmbed(vector)
    return PageChangeRunner(
        manifests=(memory_manifest.manifest(),),
        pages=CorePageFeed(blob=blob),
        index=DefaultIndex(embed=embed, transaction=workspace_tx),
        embed=embed,
        registry=registry,
    )


def _derive_consumer(runner: PageChangeRunner) -> object:
    return next(c for c in runner.consumers() if c.discriminator == "derive_facts")


# --- fact derivation ---------------------------------------------------------


async def test_derive_facts_writes_subject_scoped_facts_through_page_change(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id = await _seed_page(
        blob, workspace_id, "Acme ships the widget to the whole team on friday."
    )
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "memory_kind": "event",
                    "confidence": 8,
                    "body": "Acme ships the widget on friday",
                },
                {
                    "page_id": str(page_id),
                    "notability": "low",
                    "memory_kind": "fact",
                    "confidence": 3,
                    "body": "a low-notability aside",
                },
            ]
        }
    )
    client = StubModelClient(payload, Usage(input_tokens=50, output_tokens=20))
    runner = _runner(blob, vec((0, 1.0)), _registry(client))
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert len(rows) == 1
    fact = rows[0]
    assert fact.body == "Acme ships the widget on friday"
    assert fact.subject == SHARED_SUBJECT
    assert fact.memory_kind == "event"
    assert fact.confidence == 8
    assert fact.source_ref == str(page_id)


async def test_derive_facts_rides_its_own_cursor_independent_of_the_indexer(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id = await _seed_page(
        blob, workspace_id, "Beatrix leads the platform team from Berlin now."
    )
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "memory_kind": "fact",
                    "confidence": 7,
                    "body": "Beatrix leads the platform team",
                }
            ]
        }
    )
    client = StubModelClient(payload, Usage(input_tokens=50, output_tokens=20))
    runner = _runner(blob, vec((1, 1.0)), _registry(client))
    consumers = {c.discriminator: c for c in runner.consumers()}

    with ws(workspace_id):
        await runner.drive(consumers["derive_facts"])
    assert client.calls == 1
    assert len([row for row in await _facts(workspace_id) if row.item_class == FACT]) == 1

    with ws(workspace_id):
        scoped = ScopedStore(extension=memory_manifest.NAME)
        derive_cursor = await scoped.get("page_change_cursor:derive_facts")
        assert isinstance(derive_cursor, str)
        assert await scoped.get("page_change_cursor:index_pages") is None

        await runner.drive(consumers["derive_facts"])
        assert client.calls == 1

        await runner.drive(consumers["index_pages"])
        assert isinstance(await scoped.get("page_change_cursor:index_pages"), str)
        assert await scoped.get("page_change_cursor:derive_facts") == derive_cursor


async def test_derive_facts_is_idempotent(db: None) -> None:
    workspace_id = await _workspace()
    page_id = uuid4()
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "memory_kind": "fact",
                    "confidence": 6,
                    "body": "the office is in the old cannery building",
                }
            ]
        }
    )
    change = PageChange(
        page_id=page_id,
        subject=SHARED_SUBJECT,
        body="The office is in the old cannery building by the water.",
        digest="sha256:x",
        tombstone=False,
        created_at=WHEN,
        changed_at=WHEN,
    )
    deriver = FactDeriver(store=_store(workspace_id, vec((2, 1.0))), model=_model(payload))
    with ws(workspace_id):
        await deriver.apply((change,))
        await deriver.apply((change,))

    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert len(rows) == 1
    assert rows[0].body == "the office is in the old cannery building"


async def test_derive_facts_without_a_model_skips_but_advances_cursor(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "A page whose facts nobody derives without a model wired.")
    runner = _runner(blob, vec((3, 1.0)), registry=None)
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    assert [row for row in await _facts(workspace_id) if row.item_class == FACT] == []
    with ws(workspace_id):
        scoped = ScopedStore(extension=memory_manifest.NAME)
        assert isinstance(await scoped.get("page_change_cursor:derive_facts"), str)


# --- consolidation -----------------------------------------------------------


async def test_consolidation_supersedes_originals_and_recall_surfaces_the_summary(
    clean: None,
) -> None:
    workspace_id = await _workspace()
    probe = vec((9, 1.0))
    originals = [
        await _seed_aged_fact(
            workspace_id, "the zephyr protocol handshake rotates every hour", probe, 6
        ),
        await _seed_aged_fact(
            workspace_id, "the zephyr protocol handshake uses a fresh nonce", probe, 8
        ),
        await _seed_aged_fact(
            workspace_id, "the zephyr protocol handshake token expires fast", probe, 4
        ),
    ]
    summary_text = (
        "the zephyr protocol handshake rotates hourly with a nonce and a short-lived token"
    )
    with ws(workspace_id):
        await MemoryConsolidator(
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            workspace_id=workspace_id,
            model=_model(summary_text),
        ).run()

    rows = await _facts(workspace_id)
    summaries = [row for row in rows if row.item_class == SEMANTIC]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.body == summary_text
    assert summary.subject == SHARED_SUBJECT
    assert summary.confidence == 8
    assert {row.superseded_by for row in rows if row.id in originals} == {summary.id}

    with ws(workspace_id):
        recalled = await _store(workspace_id, probe).recall(
            "zephyr protocol handshake", frozenset({SHARED_SUBJECT}), 10
        )
    assert [item.memory_id for item in recalled] == [summary.id]
    assert recalled[0].item_class == SEMANTIC

    with ws(workspace_id):
        await MemoryConsolidator(
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            workspace_id=workspace_id,
            model=_model(summary_text),
        ).run()
    after = [row for row in await _facts(workspace_id) if row.item_class == SEMANTIC]
    assert len(after) == 1


async def test_consolidation_without_a_model_writes_nothing(clean: None) -> None:
    workspace_id = await _workspace()
    probe = vec((5, 1.0))
    originals = [
        await _seed_aged_fact(
            workspace_id, "the atlas ledger closes on the last business day", probe, 5
        ),
        await _seed_aged_fact(
            workspace_id, "the atlas ledger closes after the audit sign-off", probe, 5
        ),
        await _seed_aged_fact(
            workspace_id, "the atlas ledger closes once reconciliations finish", probe, 5
        ),
    ]
    with ws(workspace_id):
        await MemoryConsolidator(
            embed=StubEmbed(probe), transaction=workspace_tx, workspace_id=workspace_id, model=None
        ).run()

    rows = await _facts(workspace_id)
    assert [row for row in rows if row.item_class == SEMANTIC] == []
    assert all(row.superseded_by is None for row in rows if row.id in originals)


async def _insert_fact(workspace_id: UUID, created_at: datetime) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=uuid4(),
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                body="a fact",
                item_class=FACT,
                memory_kind=KIND_FACT,
                confidence=5,
                source_ref=None,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )


async def test_consolidate_candidates_name_only_workspaces_with_a_clusterable_backlog(
    db: None,
) -> None:
    """The hourly consolidate JobSpec binds only workspaces where a pass could form a cluster — at
    least MIN_CLUSTER_FACTS live facts aged past MIN_OLDEST_AGE, the consolidator's own floor. A
    workspace below the floor and one whose facts are all young are never candidates, so a fleet's
    consolidation-free workspaces run no hourly transaction."""
    clusterable, thin, young = await _workspace(), await _workspace(), await _workspace()
    aged = datetime.now(UTC) - MIN_OLDEST_AGE - timedelta(hours=1)
    fresh = datetime.now(UTC)
    for workspace_id, stamps in (
        (clusterable, (aged,) * MIN_CLUSTER_FACTS),
        (thin, (aged,) * (MIN_CLUSTER_FACTS - 1)),
        (young, (fresh,) * MIN_CLUSTER_FACTS),
    ):
        for stamp in stamps:
            await _insert_fact(workspace_id, stamp)
    consolidate = next(
        job
        for job in memory_manifest.manifest().jobs
        if job.name == memory_manifest.CONSOLIDATE_JOB
    )
    assert await consolidate.candidates() == (clusterable,)


# --- seam --------------------------------------------------------------------


def test_memory_registers_two_independent_page_change_consumers(tmp_path: object) -> None:
    """The memory manifest declares two page_change hooks; the runner yields two consumers keyed by
    their handler-name discriminator, and core_jobs names each its own `page_change:memory:<hook>`
    workflow — so the indexer and the fact deriver never collide on JobSpec name or cursor key."""
    blob = FilesystemBlobStore(root=tmp_path)
    runner = _runner(blob, vec((0, 1.0)), registry=None)
    discriminators = {consumer.discriminator for consumer in runner.consumers()}
    assert discriminators == {"index_pages", "derive_facts"}

    specs = core_jobs(
        SyncDriver(backends={"folder": FolderSource()}, blob=blob, postgres=False),
        TurnDispatcher(client=None),
        SandboxReaper(carrier=LocalCarrier(), backend="local"),
        runner,
    )
    page_change = {spec.name for spec in specs if spec.name.startswith("page_change:")}
    assert page_change == {
        f"page_change:{memory_manifest.NAME}:index_pages",
        f"page_change:{memory_manifest.NAME}:derive_facts",
    }


def _same_named_handler() -> Callable[[HookContext], Awaitable[HookOutcome]]:
    async def _handler(ctx: HookContext) -> HookOutcome:
        return None

    return _handler


def test_two_page_change_hooks_sharing_a_discriminator_fail_loud(tmp_path: object) -> None:
    """The seam keys both the JobSpec name and the cursor by handler `__name__`; two page_change
    hooks in one extension whose handlers share that name would silently collide (the second never
    fires and shares the first's cursor), so the runner fails loud when it enumerates them."""
    collision = Manifest(
        name="collide_ext",
        version="0",
        hooks=(
            HookSpec(event="page_change", handler=_same_named_handler()),
            HookSpec(event="page_change", handler=_same_named_handler()),
        ),
    )
    runner = PageChangeRunner(
        manifests=(collision,),
        pages=CorePageFeed(blob=FilesystemBlobStore(root=tmp_path)),
    )
    with pytest.raises(RuntimeError, match="share the discriminator"):
        runner.consumers()
