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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.condenser import (
    FACT_EXTRACT_TOOL,
    MIN_CLUSTER_FACTS,
    MIN_OLDEST_AGE,
    FactDeriver,
    MemoryConsolidator,
)
from ufo_ext_memory.store import (
    FACT,
    KIND_FACT,
    SEMANTIC,
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    PageIndexer,
    memory_item,
    memory_source,
)

from ufo.accounting import Pricing
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ModelAccess, PageState, ScopedStore, SourceReader, context_for
from ufo.ext.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
)
from ufo.indexing import OWNER_KIND_MEMORY_ITEM, Chunk, Hit, IndexScope, TextChunker
from ufo.jobs import PageChangeRunner, TurnDispatcher, core_jobs
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import (
    ModelClient,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
)
from ufo.models.registry import ModelRegistry
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.sources.sync import CorePageFeed, FolderSource, PageChange, SyncDriver
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import ws

WHEN = datetime(2026, 1, 1, tzinfo=UTC)
AUTO_MODEL = "claude-opus-4-8"
PAGE_BODY = "The acquisition codename is polaris and the deal closes in the third quarter."
EDITED_PAGE_BODY = "The acquisition codename is meridian and the deal closes in the third quarter."
LAST_PUBLISHABLE_CHECK = 2


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


def _reader(subjects: frozenset[str]) -> SourceReader:
    return SourceReader(agent_id=uuid4(), requesting_member_id=None, subjects=subjects)


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


@dataclass
class ExtractionModelClient:
    """Streams one canned `record_facts` call — the compelled tool the fact extraction offers — and
    one usage event, counting completions so a test can witness that a resumed cursor replays
    nothing. `arguments` is the raw argument JSON the provider streams, so a test can also hand the
    seam arguments no decoder can read."""

    arguments: str
    usage: Usage
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield ToolCallStart(id=f"call-{self.calls}", name=FACT_EXTRACT_TOOL)
        yield ToolCallDelta(id=f"call-{self.calls}", partial_json=self.arguments)
        yield self.usage


@dataclass
class RecordingExtractionClient:
    """Streams one canned `record_facts` call and keeps the requests it was asked with, so a test
    reads the tools, forced choice, and reasoning the extraction actually sent."""

    arguments: str
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ToolCallStart(id="call-1", name=FACT_EXTRACT_TOOL)
        yield ToolCallDelta(id="call-1", partial_json=self.arguments)
        yield Usage(input_tokens=10, output_tokens=5)


@dataclass
class ScriptedExtractionClient:
    """Streams one canned `record_facts` call per completion, in order, repeating the last — the
    two-phase derivations a page edit drives need different facts for the page's new revision."""

    payloads: tuple[str, ...]
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        arguments = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        yield ToolCallStart(id=f"call-{self.calls}", name=FACT_EXTRACT_TOOL)
        yield ToolCallDelta(id=f"call-{self.calls}", partial_json=arguments)
        yield Usage(input_tokens=10, output_tokens=5)


@dataclass
class CountingIndex:
    """A real DefaultIndex whose scope deletions are counted: retiring an already-retired fact is
    invisible in the surviving rows, so the recorded deletions are the only witness that a replayed
    batch retires each superseded revision exactly once."""

    backend: DefaultIndex
    deleted: list[str] = field(default_factory=list)

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        await self.backend.upsert(chunks)

    async def delete(self, scope: IndexScope) -> None:
        self.deleted.append(scope.owner_id)
        await self.backend.delete(scope)

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        await self.backend.prune(scope, keep)

    async def has_chunks(self, scope: IndexScope) -> bool:
        return await self.backend.has_chunks(scope)

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        return await self.backend.lexical(query, subjects, owner_kind, limit)

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        return await self.backend.vector(embedding, subjects, owner_kind, limit)


class MovingPage:
    """The page-state seam under one index run, landing the page's move — and the page-index pass
    that follows it — the instant the run's last publishability check has read the state. The run
    then reaches its digest stamp holding a decision the page has already invalidated: the one
    interleaving that could leave a superseded revision's chunks published for good."""

    def __init__(self, move: Callable[[], Awaitable[None]]) -> None:
        self.states = context_for("memory", frozenset()).page_states
        self.move = move
        self.calls = 0

    async def __call__(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]:
        self.calls += 1
        read = await self.states(page_ids)
        if self.calls == LAST_PUBLISHABLE_CHECK:
            await self.move()
        return read


@dataclass
class SupersedingModelClient:
    donor_id: UUID

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=uuid4())
                .where(memory_item.c.id == self.donor_id)
            )
        yield TextDelta(text="the consolidated summary must not land")
        yield Usage(input_tokens=10, output_tokens=5)


def _registry(client: ModelClient) -> ModelRegistry:
    return ModelRegistry(
        specs={
            spec.id: replace(spec, client=lambda spec, key: client, key_slot="", key_env="")
            for spec in CORE_MODEL_SPECS
        },
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


def _extraction(page_id: UUID, body: str) -> str:
    return json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "memory_kind": "fact",
                    "confidence": 7,
                    "body": body,
                }
            ]
        }
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


async def _seed_page(blob: FilesystemBlobStore, workspace_id: UUID, body: str) -> tuple[UUID, UUID]:
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
    return page_id, source_id


async def _seed_page_authority(
    workspace_id: UUID, page_id: UUID, source_id: UUID, subject: str
) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                subject=subject,
                next_sync_at=WHEN,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:page",
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject=subject,
                tombstone=False,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )


async def _seed_aged_fact(
    workspace_id: UUID,
    body: str,
    vector: tuple[float, ...],
    confidence: int,
    created_from_page_id: UUID | None = None,
) -> UUID:
    """Insert a fact aged past MIN_OLDEST_AGE with its one already-derived chunk, so the
    consolidator's aged-fact query admits it and recall can surface it through the index legs."""
    item_id = uuid4()
    source_id = uuid4() if created_from_page_id is not None else None
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
                created_from_page_id=created_from_page_id,
                created_from_page_revision=(1 if created_from_page_id is not None else None),
                source_id=source_id,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created,
                updated_at=created,
            )
        )
    with ws(workspace_id):
        await DefaultIndex(transaction=workspace_tx).upsert(
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
                        memory_item.c.created_from_page_id,
                        memory_item.c.as_of,
                        memory_item.c.superseded_by,
                    ).where(memory_item.c.workspace_id == workspace_id)
                )
            ).all()
        )


def _store(workspace_id: UUID, vector: tuple[float, ...]) -> MemoryStore:
    embed = StubEmbed(vector)
    ext = context_for("memory", frozenset())
    return MemoryStore(
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )


async def _granted_reader(workspace_id: UUID, subject: str, *source_ids: UUID) -> SourceReader:
    """An agent holding the grant for each named source, as recall's source authority reads it: a
    page-derived fact reaches recall only through a reader granted the feed it came from."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="reader",
                prompt="p",
                model="m",
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
        for source_id in source_ids:
            await connection.execute(
                sa.insert(tables.source_grant).values(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    agent_id=agent_id,
                    created_at=WHEN,
                    updated_at=WHEN,
                )
            )
    return SourceReader(agent_id=agent_id, requesting_member_id=None, subjects=frozenset({subject}))


def _runner(
    blob: FilesystemBlobStore,
    vector: tuple[float, ...],
    registry: ModelRegistry | None,
) -> PageChangeRunner:
    embed = StubEmbed(vector)
    return PageChangeRunner(
        manifests=(memory_manifest.manifest(),),
        pages=CorePageFeed(blob=blob),
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
        registry=registry,
    )


def _derive_consumer(runner: PageChangeRunner) -> object:
    return next(c for c in runner.consumers() if c.discriminator == "derive_facts")


def _scripted(store: MemoryStore, *payloads: str) -> FactDeriver:
    return FactDeriver(
        store=store,
        model=ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, ScriptedExtractionClient(payloads))),
    )


def _change(
    page_id: UUID, source_id: UUID, subject: str, body: str, revision: int, digest: str
) -> PageChange:
    return PageChange(
        page_id=page_id,
        source_id=source_id,
        subject=subject,
        stream="notes",
        title="Acquisition",
        body=body,
        digest=digest,
        revision=revision,
        tombstone=False,
        created_at=WHEN,
        as_of=WHEN,
        changed_at=WHEN,
    )


async def _rewrite_page(page_id: UUID, digest: str) -> int:
    """Edit the page's content the way a source sync does and read back the revision the database
    assigned, so a test binds to the real workspace-monotonic counter rather than guessing it."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(digest=digest).where(tables.page.c.id == page_id)
        )
        return (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()


async def _page_facts(page_id: UUID) -> dict[str, int | None]:
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(memory_item.c.body, memory_item.c.created_from_page_revision).where(
                        memory_item.c.created_from_page_id == page_id
                    )
                )
            )
            .mappings()
            .all()
        )
    return {row["body"]: row["created_from_page_revision"] for row in rows}


async def _embedding_state(page_id: UUID) -> tuple[str | None, datetime | None]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.embedding_digest, memory_item.c.embedding_claimed_at).where(
                    memory_item.c.created_from_page_id == page_id
                )
            )
        ).one()
    return row.embedding_digest, row.embedding_claimed_at


async def _index_memory(store: MemoryStore, probe: tuple[float, ...]) -> None:
    await MemoryIndexer(
        index=store.index,
        embed=StubEmbed(probe),
        transaction=workspace_tx,
        chunker=TextChunker(),
        page_states=context_for("memory", frozenset()).page_states,
    ).run()


async def _index_pages(
    store: MemoryStore, probe: tuple[float, ...], workspace_id: UUID, change: PageChange
) -> None:
    await PageIndexer(
        index=store.index,
        embed=StubEmbed(probe),
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    ).apply((change,))


# --- fact derivation ---------------------------------------------------------


async def test_derive_facts_writes_subject_scoped_facts_through_page_change(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, _source_id = await _seed_page(
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
    client = ExtractionModelClient(payload, Usage(input_tokens=50, output_tokens=20))
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
    assert fact.created_from_page_id == page_id


async def test_derive_facts_rides_its_own_cursor_independent_of_the_indexer(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, _source_id = await _seed_page(
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
    client = ExtractionModelClient(payload, Usage(input_tokens=50, output_tokens=20))
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
    source_id = uuid4()
    subject = f"member:{uuid4()}"
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
        source_id=source_id,
        subject=subject,
        stream="notes",
        title="Office location",
        body="The office is in the old cannery building by the water.",
        digest="sha256:page",
        revision=1,
        tombstone=False,
        created_at=WHEN,
        as_of=WHEN - timedelta(days=365),
        changed_at=WHEN,
    )
    await _seed_page_authority(workspace_id, page_id, source_id, subject)
    deriver = _scripted(_store(workspace_id, vec((2, 1.0))), payload)
    with ws(workspace_id):
        await deriver.apply((change,))
        await deriver.apply((change,))

    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert len(rows) == 1
    assert rows[0].body == "the office is in the old cannery building"
    assert rows[0].subject == subject
    assert rows[0].as_of.replace(tzinfo=UTC) == change.as_of


async def test_derive_facts_binds_each_fact_to_its_pages_source(db: None) -> None:
    """The deriver is the one production writer of `memory_item.source_id` and its `memory_source`
    link: it stamps every fact it commits with the source of the page it read, so a reader granted
    that source reaches the fact and one without it does not. Binding a fact to the wrong feed would
    mis-scope the grant invisibly, so the source it writes is read straight back."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "memory_kind": "fact",
                    "confidence": 6,
                    "body": "the acquisition codename is polaris",
                }
            ]
        }
    )
    change = _change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page")
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    deriver = _scripted(_store(workspace_id, vec((2, 1.0))), payload)
    with ws(workspace_id):
        await deriver.apply((change,))
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.id, memory_item.c.source_id).where(
                    memory_item.c.created_from_page_id == page_id
                )
            )
        ).one()
        links = (
            await connection.execute(
                sa.select(memory_source.c.source_id, memory_source.c.page_id).where(
                    memory_source.c.memory_item_id == row.id
                )
            )
        ).all()
    assert row.source_id == source_id
    assert set(links) == {(source_id, page_id)}


async def test_fact_deriver_ignores_a_stale_private_payload_after_sanitization(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "notability": "high",
                    "body": "the acquisition plan has been redacted",
                }
            ]
        }
    )
    client = ExtractionModelClient(payload, Usage(input_tokens=10, output_tokens=5))
    model = ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client))
    private_subject = member_subject(uuid4())
    private = PageChange(
        page_id=page_id,
        source_id=source_id,
        subject=private_subject,
        stream="notes",
        title="Private plan",
        body="the private acquisition codename is polaris",
        digest="sha256:private",
        revision=0,
        tombstone=False,
        created_at=WHEN,
        as_of=WHEN,
        changed_at=WHEN,
    )
    sanitized = replace(
        private,
        subject=SHARED_SUBJECT,
        body="the acquisition plan has been redacted before sharing with the team",
        digest="sha256:page",
        revision=1,
    )
    deriver = FactDeriver(store=_store(workspace_id, vec((2, 1.0))), model=model)
    with ws(workspace_id):
        await deriver.apply((private,))
        assert client.calls == 0
        await deriver.apply((sanitized,))
    assert client.calls == 1
    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert len(rows) == 1
    assert rows[0].body == "the acquisition plan has been redacted"
    assert rows[0].subject == SHARED_SUBJECT


async def test_derive_facts_without_a_model_holds_its_cursor_instead_of_skipping(
    db: None, tmp_path: object
) -> None:
    """The deriver is the one writer that retires a page-derived fact, so a batch it cannot derive
    is a batch whose replacements do not exist: with no model wired the hook raises, the runner
    never advances the cursor, and the next tick replays exactly those pages. A skip that advanced
    would strand every changed page's facts behind a replacement that never comes."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "A page whose facts nobody derives without a model wired.")
    runner = _runner(blob, vec((3, 1.0)), registry=None)
    with ws(workspace_id), pytest.raises(RuntimeError, match="requires the model seam"):
        await runner.drive(_derive_consumer(runner))

    assert [row for row in await _facts(workspace_id) if row.item_class == FACT] == []
    with ws(workspace_id):
        scoped = ScopedStore(extension=memory_manifest.NAME)
        assert await scoped.get("page_change_cursor:derive_facts") is None


async def test_the_extraction_compels_the_recording_tool_instead_of_asking_for_json_prose(
    db: None, tmp_path: object
) -> None:
    """The facts are a tool contract, never structured data read out of a completion: the pass
    offers the recording tool alone, compels it, and runs with reasoning off (a forced choice cannot
    run under extended thinking), so the entries arrive as arguments the provider decoded. A body
    carrying the quote and newline that break a hand-decoded reply lands verbatim."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, _source_id = await _seed_page(
        blob, workspace_id, 'The buyer said "polaris" is the codename, on two lines.'
    )
    body = 'the buyer calls the deal "polaris"\nand closes it in Q3'
    client = RecordingExtractionClient(_extraction(page_id, body))
    runner = _runner(blob, vec((21, 1.0)), _registry(client))
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    request = client.requests[0]
    assert [tool.name for tool in request.tools] == [FACT_EXTRACT_TOOL]
    assert request.tool_choice == FACT_EXTRACT_TOOL
    assert request.reasoning == "off"
    assert request.tools[0].input_schema["properties"]["facts"]["type"] == "array"
    assert await _page_facts(page_id) == {body: 1}


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        '{"facts":[{"page_id":"x","body":"the buyer said "polaris" is the codename"}]}',
        '{"notes":[]}',
        '{"facts":"polaris"}',
    ],
    ids=["no-recorded-call", "arguments-no-decoder-reads", "facts-absent", "facts-not-a-list"],
)
async def test_an_extraction_it_cannot_read_holds_the_cursor_instead_of_dropping_the_pages_facts(
    db: None, tmp_path: object, arguments: str | None
) -> None:
    """Every way an extraction can come back unreadable is the same answer — not "this page holds
    nothing" — and none of them may cost the page its facts. A reply that records no call at all,
    one whose arguments no decoder reads, one carrying no `facts` key, and one whose `facts` is not
    a list each raise, so the cursor stays where it stands and the fact bound to the revision before
    the edit survives. A pass that settled the group and advanced would strand the live revision
    behind a derivation that never comes, unretried and invisible."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, source_id = await _seed_page(
        blob, workspace_id, "The acquisition codename is polaris and the deal closes in Q3."
    )
    store = _store(workspace_id, vec((12, 1.0)))
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the acquisition codename is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    assert await _rewrite_page(page_id, "sha256:edited") > 1
    client: ModelClient = (
        StubModelClient(
            "I'm sorry, I can't help with that.", Usage(input_tokens=10, output_tokens=5)
        )
        if arguments is None
        else ExtractionModelClient(arguments, Usage(input_tokens=10, output_tokens=5))
    )
    runner = _runner(blob, vec((12, 1.0)), _registry(client))
    with ws(workspace_id), pytest.raises(ValueError):
        await runner.drive(_derive_consumer(runner))

    with ws(workspace_id):
        assert (
            await ScopedStore(extension=memory_manifest.NAME).get("page_change_cursor:derive_facts")
            is None
        )
    assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}


async def test_the_tick_after_an_unreadable_extraction_derives_the_pages_the_last_one_held(
    db: None, tmp_path: object
) -> None:
    """The held cursor is what makes the loss recoverable: the pages an unreadable extraction left
    underived are exactly the pages the next tick replays, so the facts of the edited page land one
    tick late instead of never, and the revision they replace retires then."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, source_id = await _seed_page(
        blob, workspace_id, "The acquisition codename is polaris and the deal closes in Q3."
    )
    store = _store(workspace_id, vec((17, 1.0)))
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the acquisition codename is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    revision = await _rewrite_page(page_id, "sha256:edited")
    unreadable = _runner(
        blob,
        vec((17, 1.0)),
        _registry(
            ExtractionModelClient(
                '{"facts":[{"page_id":"x","body":"the buyer said "polaris""}]}',
                Usage(input_tokens=10, output_tokens=5),
            )
        ),
    )
    with ws(workspace_id), pytest.raises(ValueError):
        await unreadable.drive(_derive_consumer(unreadable))

    readable = _runner(
        blob,
        vec((17, 1.0)),
        _registry(
            ExtractionModelClient(
                _extraction(page_id, "the acquisition codename is meridian"),
                Usage(input_tokens=10, output_tokens=5),
            )
        ),
    )
    with ws(workspace_id):
        await readable.drive(_derive_consumer(readable))
    assert await _page_facts(page_id) == {"the acquisition codename is meridian": revision}


async def test_a_body_too_thin_to_derive_keeps_the_pages_prior_fact(db: None) -> None:
    """A page trimmed below the extraction floor never reaches the model, so its live revision is
    settled by nothing and the fact from the revision before the trim survives. The eligibility
    filter and the retirement read the same committed replacement, so a page the pass skips cannot
    lose what it has."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(workspace_id, vec((18, 1.0)))
    deriver = _scripted(store, _extraction(page_id, "the acquisition codename is polaris"))
    with ws(workspace_id):
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page"),)
        )
        assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}

        revision = await _rewrite_page(page_id, "sha256:redacted")
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, "redacted.", revision, "sha256:redacted"),)
        )

    assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}


async def test_an_extraction_carrying_no_facts_keeps_the_pages_prior_fact(db: None) -> None:
    """An extraction that returns an empty `facts` list commits no replacement, so the revision it
    read settles nothing and the prior revision's fact stays — unrecallable until a later derivation
    replaces it, never destroyed by the pass that could not produce its successor."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(workspace_id, vec((19, 1.0)))
    deriver = _scripted(
        store,
        _extraction(page_id, "the acquisition codename is polaris"),
        json.dumps({"facts": []}),
    )
    with ws(workspace_id):
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page"),)
        )
        assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}

        revision = await _rewrite_page(page_id, "sha256:edited")
        await deriver.apply(
            (
                _change(
                    page_id, source_id, SHARED_SUBJECT, EDITED_PAGE_BODY, revision, "sha256:edited"
                ),
            )
        )

    assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}


async def test_a_page_fact_outlives_its_edit_until_the_derivation_replaces_it(db: None) -> None:
    """The delete-before-replace regression, over all three page-derived consumers. An edit fences
    the page's fact out of recall, but nothing may remove it before the derivation for the new
    revision commits — not the page indexer on its own cursor, not the index job re-claiming the row
    with its embedding cleared. The moment the replacement lands, recall serves it and the row it
    replaced is gone."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    probe = vec((13, 1.0))
    store = _store(workspace_id, probe)
    deriver = _scripted(
        store,
        _extraction(page_id, "the acquisition codename is polaris"),
        _extraction(page_id, "the acquisition codename is meridian"),
    )
    shared = frozenset({SHARED_SUBJECT})
    with ws(workspace_id):
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page"),)
        )
        await _index_memory(store, probe)
        assert [
            item.body
            for item in await store.recall("acquisition codename", shared, 5, source_reader=reader)
        ] == ["the acquisition codename is polaris"]

        revision = await _rewrite_page(page_id, "sha256:edited")
        edited = _change(
            page_id, source_id, SHARED_SUBJECT, EDITED_PAGE_BODY, revision, "sha256:edited"
        )
        await _index_pages(store, probe, workspace_id, edited)
        await _index_memory(store, probe)

        assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}
        assert await store.recall("acquisition codename", shared, 5, source_reader=reader) == ()

        await deriver.apply((edited,))
        await _index_memory(store, probe)
        assert await _page_facts(page_id) == {"the acquisition codename is meridian": revision}
        assert [
            item.body
            for item in await store.recall("acquisition codename", shared, 5, source_reader=reader)
        ] == ["the acquisition codename is meridian"]


async def test_a_committed_replacement_retires_the_prior_revision_exactly_once(db: None) -> None:
    """Replacement precedes removal and happens once: the pass that settles the new revision writes
    its fact, then retires the row bound to the old one and that row's index scope — and a replay of
    the same change retires nothing further, so the count of scope deletions stays at one."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((14, 1.0))
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    index = CountingIndex(DefaultIndex(transaction=workspace_tx))
    ext = context_for("memory", frozenset())
    store = MemoryStore(
        index=index,
        embed=StubEmbed(probe),
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )
    deriver = _scripted(
        store,
        _extraction(page_id, "the acquisition codename is polaris"),
        _extraction(page_id, "the acquisition codename is meridian"),
    )
    with ws(workspace_id):
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page"),)
        )
        await _index_memory(store, probe)
        retired = next(
            item.memory_id
            for item in await store.recall(
                "acquisition codename",
                frozenset({SHARED_SUBJECT}),
                5,
                source_reader=reader,
            )
        )
        revision = await _rewrite_page(page_id, "sha256:edited")
        edited = _change(
            page_id, source_id, SHARED_SUBJECT, EDITED_PAGE_BODY, revision, "sha256:edited"
        )
        await deriver.apply((edited,))
        assert index.deleted == [str(retired)]
        await deriver.apply((edited,))

    assert index.deleted == [str(retired)]
    assert await _page_facts(page_id) == {"the acquisition codename is meridian": revision}


async def test_replaying_a_settled_batch_writes_and_retires_nothing_new(db: None) -> None:
    """Replay is idempotent across the whole pass: the content-addressed commit lands on the same
    row and the retirement finds nothing left, so a page delivered twice settles on exactly the rows
    the first delivery produced."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    probe = vec((15, 1.0))
    store = _store(workspace_id, probe)
    deriver = _scripted(store, _extraction(page_id, "the acquisition codename is polaris"))
    change = _change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page")
    with ws(workspace_id):
        await deriver.apply((change,))
        await _index_memory(store, probe)
        settled = await store.recall(
            "acquisition codename", frozenset({SHARED_SUBJECT}), 5, source_reader=reader
        )
        await deriver.apply((change,))
        await _index_memory(store, probe)
        replayed = await store.recall(
            "acquisition codename", frozenset({SHARED_SUBJECT}), 5, source_reader=reader
        )

    assert [item.body for item in settled] == ["the acquisition codename is polaris"]
    assert [item.memory_id for item in replayed] == [item.memory_id for item in settled]
    assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}


async def test_a_tombstoned_pages_facts_are_retired_by_the_derivation_that_settles_it(
    db: None,
) -> None:
    """A removed page will never produce a replacement, so its facts are the one removal that waits
    on nothing — and the deriver, which owns them, is still what performs it. The page indexer's own
    pass over the tombstone leaves the rows alone."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((16, 1.0))
    store = _store(workspace_id, probe)
    deriver = _scripted(store, _extraction(page_id, "the acquisition codename is polaris"))
    with ws(workspace_id):
        await deriver.apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page"),)
        )
        await _index_memory(store, probe)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page).values(tombstone=True).where(tables.page.c.id == page_id)
            )
        retired = replace(
            _change(page_id, source_id, SHARED_SUBJECT, "", 2, "sha256:page"), tombstone=True
        )
        await _index_pages(store, probe, workspace_id, retired)
        assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}

        await deriver.apply((retired,))
        assert await _page_facts(page_id) == {}
        assert (
            await store.index.lexical(
                "acquisition codename", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 5
            )
            == ()
        )


# --- index publication -------------------------------------------------------


async def test_the_index_withholds_facts_bound_to_a_superseded_revision(db: None) -> None:
    """Recall asks the index for exactly its candidate window and fences page-derived rows only
    afterwards, so a fact bound to a revision the page has left must never occupy a slot: the index
    job publishes the live revision's fact alone, and the superseded siblings cannot crowd it out of
    a reader's window."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((20, 1.0))
    store = _store(workspace_id, probe)
    for index in range(5):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=f"the acquisition codename polaris note {index}",
                created_from_page_id=page_id,
                created_from_page_revision=1,
                source_id=source_id,
            )
        )
    revision = await _rewrite_page(page_id, "sha256:edited")
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the acquisition codename is meridian",
            created_from_page_id=page_id,
            created_from_page_revision=revision,
            source_id=source_id,
        )
    )
    shared = frozenset({SHARED_SUBJECT})
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    with ws(workspace_id):
        await _index_memory(store, probe)
        published = await store.index.lexical(
            "acquisition codename", shared, OWNER_KIND_MEMORY_ITEM, 100
        )
        recalled = await store.recall("acquisition codename", shared, 3, source_reader=reader)

    assert [hit.text for hit in published] == ["the acquisition codename is meridian"]
    assert [item.body for item in recalled] == ["the acquisition codename is meridian"]
    assert len(await _page_facts(page_id)) == 6


async def test_a_withheld_fact_leaves_the_index_jobs_due_set(db: None) -> None:
    """A fact the index job may not publish is settled, not left claimed: it stamps the digest and
    releases the lease, so the row cannot sit in the due set forever holding the claim slots and the
    per-tick candidate binding that a newly committed fact needs. The row itself stays — retiring it
    is the derivation's to do."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((21, 1.0))
    store = _store(workspace_id, probe)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the acquisition codename is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    await _rewrite_page(page_id, "sha256:edited")
    job = next(
        spec
        for spec in memory_manifest.manifest().jobs
        if spec.name == memory_manifest.MEMORY_INDEX_JOB
    )
    assert workspace_id in set(await job.candidates())

    with ws(workspace_id):
        await _index_memory(store, probe)

    assert await _page_facts(page_id) == {"the acquisition codename is polaris": 1}
    assert workspace_id not in set(await job.candidates())
    digest, claimed_at = await _embedding_state(page_id)
    assert digest is not None
    assert claimed_at is None


async def test_a_fact_carried_to_the_new_revision_is_published_again(db: None) -> None:
    """A fact withheld while its revision was stale is not lost to the index: the derivation that
    carries the same body forward rebinds it to the live revision, which makes it due again, so the
    next index tick publishes it and recall serves it. Without that, a fact the index settled while
    fenced would stay unrecallable for as long as the row lived."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((22, 1.0))
    store = _store(workspace_id, probe)
    body = "the acquisition codename is polaris"
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body=body,
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    revision = await _rewrite_page(page_id, "sha256:edited")
    shared = frozenset({SHARED_SUBJECT})
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    with ws(workspace_id):
        await _index_memory(store, probe)
        assert await store.recall("acquisition codename", shared, 5, source_reader=reader) == ()

        await _scripted(store, _extraction(page_id, body)).apply(
            (_change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, revision, "sha256:edited"),)
        )
        digest, _claimed_at = await _embedding_state(page_id)
        assert digest is None

        await _index_memory(store, probe)
        assert [
            item.body
            for item in await store.recall("acquisition codename", shared, 5, source_reader=reader)
        ] == [body]
    assert await _page_facts(page_id) == {body: revision}


async def test_a_pages_move_withdraws_the_chunks_it_published_while_current(db: None) -> None:
    """Facts published while their revision was the page's own must not keep their chunks once the
    page moves on. The page-index pass makes the revisions the page left due again, so the index job
    withdraws them without waiting on a derivation — a revision that derives nothing derives no
    replacement, and five superseded siblings would otherwise fill recall's whole candidate window
    and leave another page's live fact unrecallable for as long as the rows lived. Every row stays
    exactly where the derivation left it."""
    workspace_id = await _workspace()
    edited_page, edited_source = uuid4(), uuid4()
    live_page, live_source = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, edited_page, edited_source, SHARED_SUBJECT)
    await _seed_page_authority(workspace_id, live_page, live_source, SHARED_SUBJECT)
    probe = vec((23, 1.0))
    store = _store(workspace_id, probe)
    live_body = "the acquisition codename is meridian and the deal closes"
    superseded = {f"the acquisition codename polaris note {note}" for note in range(5)}
    shared = frozenset({SHARED_SUBJECT})
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, edited_source, live_source)
    with ws(workspace_id):
        states = await context_for("memory", frozenset()).page_states((edited_page, live_page))
        for body in superseded:
            await store.commit(
                MemoryWrite(
                    subject=SHARED_SUBJECT,
                    body=body,
                    created_from_page_id=edited_page,
                    created_from_page_revision=states[edited_page].revision,
                    source_id=edited_source,
                )
            )
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=live_body,
                created_from_page_id=live_page,
                created_from_page_revision=states[live_page].revision,
                source_id=live_source,
            )
        )
        await _index_memory(store, probe)
        published = await store.index.lexical(
            "acquisition codename", shared, OWNER_KIND_MEMORY_ITEM, 100
        )
        assert {hit.text for hit in published} == superseded | {live_body}

        revision = await _rewrite_page(edited_page, "sha256:edited")
        await _index_pages(
            store,
            probe,
            workspace_id,
            _change(
                edited_page,
                edited_source,
                SHARED_SUBJECT,
                EDITED_PAGE_BODY,
                revision,
                "sha256:edited",
            ),
        )
        await _index_memory(store, probe)

        assert [
            item.body
            for item in await store.recall("acquisition codename", shared, 3, source_reader=reader)
        ] == [live_body]
        withdrawn = await store.index.lexical(
            "acquisition codename", shared, OWNER_KIND_MEMORY_ITEM, 100
        )
        assert {hit.text for hit in withdrawn} == {live_body}
    assert set(await _page_facts(edited_page)) == superseded


async def test_a_page_moving_before_the_settle_leaves_the_row_due(db: None) -> None:
    """The index job may not settle a decision the page has already invalidated. A page moving
    between the job's last check and its digest stamp releases the row's claim, so the stamp finds
    no claim to release and the row stays due — the next tick withdraws the chunks the interrupted
    run published. Without that, one interleaving leaves a superseded revision's chunks published
    for the life of the row, exactly what the page-index pass exists to prevent."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((24, 1.0))
    store = _store(workspace_id, probe)
    body = "the acquisition codename is polaris"
    shared = frozenset({SHARED_SUBJECT})

    async def move() -> None:
        revision = await _rewrite_page(page_id, "sha256:edited")
        await _index_pages(
            store,
            probe,
            workspace_id,
            _change(
                page_id, source_id, SHARED_SUBJECT, EDITED_PAGE_BODY, revision, "sha256:edited"
            ),
        )

    with ws(workspace_id):
        states = await context_for("memory", frozenset()).page_states((page_id,))
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=states[page_id].revision,
                source_id=source_id,
            )
        )
        await MemoryIndexer(
            index=store.index,
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=MovingPage(move),
        ).run()
        digest, claimed_at = await _embedding_state(page_id)
        assert digest is None
        assert claimed_at is None

        await _index_memory(store, probe)
        assert (
            await store.index.lexical("acquisition codename", shared, OWNER_KIND_MEMORY_ITEM, 10)
            == ()
        )
    assert await _page_facts(page_id) == {body: states[page_id].revision}


# --- consolidation -----------------------------------------------------------


async def test_consolidation_supersedes_originals_and_recall_surfaces_the_summary(
    db: None,
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
            "zephyr protocol handshake",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
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


async def test_consolidation_without_a_model_writes_nothing(db: None) -> None:
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


async def test_consolidation_excludes_page_derived_facts(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((6, 1.0))
    page_id = uuid4()
    originals = [
        await _seed_aged_fact(
            workspace_id,
            f"the page-derived atlas statement {index}",
            probe,
            5,
            created_from_page_id=page_id,
        )
        for index in range(MIN_CLUSTER_FACTS)
    ]
    with ws(workspace_id):
        await MemoryConsolidator(
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            workspace_id=workspace_id,
            model=_model("a summary"),
        ).run()
    rows = await _facts(workspace_id)
    assert [row for row in rows if row.item_class == SEMANTIC] == []
    assert all(row.superseded_by is None for row in rows if row.id in originals)


async def test_consolidation_revalidates_donors_after_the_model_call(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((7, 1.0))
    originals = [
        await _seed_aged_fact(
            workspace_id,
            f"the orion protocol statement {index}",
            probe,
            5,
        )
        for index in range(MIN_CLUSTER_FACTS)
    ]
    model = ModelAccess(
        _Resolver(
            AUTO_MODEL,
            CORE_PRICING,
            SupersedingModelClient(originals[0]),
        )
    )
    with ws(workspace_id):
        await MemoryConsolidator(
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            workspace_id=workspace_id,
            model=model,
        ).run()
    rows = await _facts(workspace_id)
    assert [row for row in rows if row.item_class == SEMANTIC] == []
    assert next(row for row in rows if row.id == originals[0]).superseded_by is not None
    assert all(row.superseded_by is None for row in rows if row.id in set(originals[1:]))


async def _insert_fact(
    workspace_id: UUID, created_at: datetime, created_from_page_id: UUID | None = None
) -> None:
    source_id = uuid4() if created_from_page_id is not None else None
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
                created_from_page_id=created_from_page_id,
                created_from_page_revision=(1 if created_from_page_id is not None else None),
                source_id=source_id,
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
    clusterable, thin, young, page_derived = (
        await _workspace(),
        await _workspace(),
        await _workspace(),
        await _workspace(),
    )
    aged = datetime.now(UTC) - MIN_OLDEST_AGE - timedelta(hours=1)
    fresh = datetime.now(UTC)
    for workspace_id, stamps in (
        (clusterable, (aged,) * MIN_CLUSTER_FACTS),
        (thin, (aged,) * (MIN_CLUSTER_FACTS - 1)),
        (young, (fresh,) * MIN_CLUSTER_FACTS),
    ):
        for stamp in stamps:
            await _insert_fact(workspace_id, stamp)
    for _ in range(MIN_CLUSTER_FACTS):
        await _insert_fact(page_derived, aged, created_from_page_id=uuid4())
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


async def test_one_pass_settles_only_the_pages_its_own_facts_replace(
    db: None, tmp_path: object
) -> None:
    """The pass is per page, not per group. Two edited pages share one model call and the reply
    restates only the first, so the first retires its prior revision and the second keeps its fact —
    the settlement is the committed replacement's, and a page the reply passed over is untouched. A
    regression to batch-wide retirement takes the second page's fact with the first's."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    restated, restated_source = await _seed_page(
        blob, workspace_id, "The acquisition codename is polaris and the deal closes in Q3."
    )
    passed_over, passed_over_source = await _seed_page(
        blob, workspace_id, "The security review is scheduled and the auditor is booked."
    )
    store = _store(workspace_id, vec((12, 1.0)))
    for page_id, source_id, body in (
        (restated, restated_source, "the acquisition codename is polaris"),
        (passed_over, passed_over_source, "the security review is scheduled"),
    ):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=1,
                source_id=source_id,
            )
        )
    restated_revision = await _rewrite_page(restated, "sha256:restated")
    assert await _rewrite_page(passed_over, "sha256:untouched") > 1
    reply = _extraction(restated, "the acquisition codename is meridian")
    runner = _runner(
        blob,
        vec((12, 1.0)),
        _registry(ExtractionModelClient(reply, Usage(input_tokens=10, output_tokens=5))),
    )
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    assert await _page_facts(restated) == {
        "the acquisition codename is meridian": restated_revision
    }
    assert await _page_facts(passed_over) == {"the security review is scheduled": 1}
