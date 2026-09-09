"""The memory condenser: the fact deriver (page_change → durable facts), the consolidator (aged
facts → semantic summary) and the section pass (a band's live facts → the paragraph that opens it),
the producers that make recall's dead machinery fire and the wiki's headings read.

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
from typing import get_args
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.condenser import (
    DEDUP_CURSOR_KEY,
    FACT_EXTRACT_TOOL,
    MACHINE_STATUS_STREAMS,
    MEMBER_SECTION_HEADINGS,
    MIN_CLUSTER_FACTS,
    MIN_OLDEST_AGE,
    MIN_OVERVIEW_FACTS,
    MIN_SECTION_FACTS,
    PAGE_PASS_MIN_ROWS,
    PAGE_PASS_TOOL,
    PROFILE_TOOL,
    WORKSPACE_SECTION_HEADINGS,
    FactDeriver,
    MemoryConsolidator,
    MemoryDeduper,
    OverviewWriter,
    PagePass,
    ProfileWriter,
    RetiredRow,
    SectionWriter,
    _restates,
    admitted_curation,
    memory_profile,
)
from ufo_ext_memory.manifest import RebuildPageFactsInput
from ufo_ext_memory.objects import (
    MEMORY_OBJECT,
    MemoryObjects,
)
from ufo_ext_memory.store import (
    FACT,
    KIND_FACT,
    MEMORY_BODY_MAX_CHARS,
    OVERVIEW,
    SECTION,
    SEMANTIC,
    MemoryIndexer,
    MemoryKind,
    MemoryStore,
    MemoryWrite,
    PageIndexer,
    mem_page,
    memory_item,
    memory_source,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import (
    PROVIDER_ANTHROPIC,
    ModelClient,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.runtime.billing.accounting import Pricing
from ufo.runtime.ext.context import (
    JsonValue,
    ModelAccess,
    PageState,
    ScopedStore,
    SourceReader,
    context_for,
)
from ufo.runtime.ext.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
)
from ufo.runtime.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    Chunk,
    EmbedClient,
    Hit,
    IndexScope,
    TextChunker,
)
from ufo.runtime.jobs import PageChangeRunner
from ufo.runtime.objects import ObjectListQuery
from ufo.runtime.sources.sync import CorePageFeed, PageChange, feed_handle_for
from ufo.runtime.tools.context import SpeakerRequired, ToolContext
from ufo.runtime.turns.audience import conversation_audience, foreign_room_audience
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import (
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    ResolvedModelClient,
    ws,
)
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, Turn, Usage
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.sdk.delivery_register import DELIVERY_REGISTER_BLOCK

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

WHEN = datetime(2026, 1, 1, tzinfo=UTC)
AUTO_MODEL = "claude-opus-4-8"
CONSOLIDATE_MODEL_JOB = f"memory:{memory_manifest.CONSOLIDATE_JOB}"
REBUILD_TOOL = "rebuild_page_facts"
DERIVE_MODEL_JOB = "core:page_change:memory:derive_facts"
PAGE_BODY = "The acquisition codename is polaris and the deal closes in the third quarter."
EDITED_PAGE_BODY = "The acquisition codename is meridian and the deal closes in the third quarter."
MACHINE_STATUS_STREAM = "workflow_runs"
MACHINE_STATUS_ROW = "the nightly billing build ran for twelve minutes"
MACHINE_STATUS_REDERIVED = "the nightly billing build ran for eleven minutes"
LAST_PUBLISHABLE_CHECK = 2


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
class MappedEmbed:
    """A stand-in EmbedClient keyed by body, so one group can hold copies that cluster and facts
    that do not — the dependency the dedup decision reads, never the asserted thing. A body it was
    not given raises, which is how a test poisons one group's sweep; `calls` counts the passes,
    which is how a test witnesses a group skipped rather than re-embedded."""

    vectors: dict[str, tuple[float, ...]]
    calls: int = 0

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        missing = [text for text in texts if text not in self.vectors]
        if missing:
            raise LookupError(f"no vector for {missing}")
        return tuple(self.vectors[text] for text in texts)


@dataclass
class SupersedingEmbed:
    """Retires one row from inside the embed call — the window between the sweep's read of a group
    and its locked stamp, where another writer can retire a copy the pass is about to move."""

    vector: tuple[float, ...]
    retire: UUID

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=uuid4())
                .where(memory_item.c.id == self.retire)
            )
        return tuple(self.vector for _ in texts)


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

    async def restamp(self, scope: IndexScope, subject: str, keep: frozenset[str]) -> bool:
        return await self.backend.restamp(scope, subject, keep)

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

    async def client_for(self, model: str) -> ResolvedModelClient:
        return ResolvedModelClient(self.client, PLATFORM_FUNDED, PLATFORM_PAYER)

    def key_slot_for(self, model: str) -> str | None:
        return None

    def provider_for(self, model: str) -> str:
        return PROVIDER_ANTHROPIC


def _model(payload: str) -> ModelAccess:
    return ModelAccess(
        _Resolver(
            AUTO_MODEL,
            CORE_PRICING,
            StubModelClient(payload, Usage(input_tokens=10, output_tokens=5)),
        ),
        CONSOLIDATE_MODEL_JOB,
    )


def _extraction(page_id: UUID, body: str) -> str:
    return json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
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


async def _seed_connection(connection: AsyncConnection, workspace_id: UUID, backend: str) -> UUID:
    """The workspace-shared connection one seeded source hangs off. Reach is the connection's, so
    one source here is one grantable feed."""
    connection_id = uuid4()
    await connection.execute(
        sa.insert(tables.connection).values(
            id=connection_id,
            workspace_id=workspace_id,
            provider=backend,
            account_id=connection_id.hex,
            host="",
            owner_member_id=None,
            shared=True,
            created_at=WHEN,
            updated_at=WHEN,
        )
    )
    return connection_id


async def _seed_page(
    blob: FilesystemBlobStore,
    workspace_id: UUID,
    body: str,
    title: str = "",
    stream: str = "",
) -> tuple[UUID, UUID]:
    source_id, page_id = uuid4(), uuid4()
    await blob.put(f"pages/{page_id}", body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=uuid7(),
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=await _seed_connection(connection, workspace_id, "folder"),
                cursor=None,
                next_sync_at=WHEN,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=uuid7(),
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                body_ref=f"pages/{page_id}",
                subject=SHARED_SUBJECT,
                title=title,
                stream=stream,
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
                uid=uuid7(),
                id=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=await _seed_connection(connection, workspace_id, "test"),
                next_sync_at=WHEN,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=uuid7(),
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
    """An agent holding a connector grant on the connection behind each named source, as recall's
    source authority reads it: a page-derived fact reaches recall only through a reader that may
    read the feed it came from."""
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
            granted = (
                await connection.execute(
                    sa.select(tables.source.c.connection_id).where(tables.source.c.id == source_id)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=granted,
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


async def _seed_admin(workspace_id: UUID, admin: bool = True) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id}@example.com",
                timezone="UTC",
                is_admin=admin,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


def _scripted(store: MemoryStore, *payloads: str) -> FactDeriver:
    return FactDeriver(
        store=store,
        model=ModelAccess(
            _Resolver(AUTO_MODEL, CORE_PRICING, ScriptedExtractionClient(payloads)),
            DERIVE_MODEL_JOB,
        ),
    )


def _change(
    page_id: UUID,
    source_id: UUID,
    subject: str,
    body: str,
    revision: int,
    digest: str,
    stream: str = "notes",
) -> PageChange:
    return PageChange(
        page_id=page_id,
        source_id=source_id,
        subject=subject,
        stream=stream,
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


async def test_derive_facts_writes_each_source_supported_fact_through_page_change(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, _source_id = await _seed_page(
        blob, workspace_id, "Acme ships the widget to the whole team on friday in blue packaging."
    )
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "memory_kind": "event",
                    "confidence": 8,
                    "body": "Acme ships the widget on friday",
                },
                {
                    "page_id": str(page_id),
                    "memory_kind": "fact",
                    "confidence": 3,
                    "body": "Acme uses blue packaging",
                },
            ]
        }
    )
    client = ExtractionModelClient(payload, Usage(input_tokens=50, output_tokens=20))
    runner = _runner(blob, vec((0, 1.0)), _registry(client))
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert {row.body for row in rows} == {
        "Acme ships the widget on friday",
        "Acme uses blue packaging",
    }
    fact = next(row for row in rows if row.body == "Acme ships the widget on friday")
    assert fact.body == "Acme ships the widget on friday"
    assert fact.subject == SHARED_SUBJECT
    assert fact.memory_kind == "event"
    assert fact.confidence == 8
    assert fact.created_from_page_id == page_id


def test_an_entry_that_covers_another_is_a_restatement_and_a_distinct_claim_is_not() -> None:
    """The measure is how much of the shorter entry the longer one covers, not how much the two
    share of everything they say between them. The two real Idler.ai sentences score 0.23 by the
    second measure and are caught by the first."""
    covered = "Ivan suggested that Marshall book a call with Nalu, who was identified as a "
    "co-founder of Idler.ai."
    carrying_more = (
        "Ivan suggested that Marshall book a call with Nalu, identified as an Idler.ai "
        "co-founder, using the Idler scheduling link."
    )
    apart = "Nalu Concepcion looked forward to the call and to learning how Idler.ai might help."
    assert _restates(covered, carrying_more)
    assert not _restates(covered, apart)
    assert not _restates("Acme ships friday", "Acme ships")


async def test_derive_facts_cuts_an_oversized_model_body_back_to_a_whole_word(
    db: None, tmp_path: object
) -> None:
    """`ExtractedFact.body` is untrusted model output carrying the row budget the prompt states, so
    a body the model ran past arrives cut back to its last whole word rather than dropped or left
    for the object index to end mid-word. The fact still lands: a batch keeps its recall."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_id, _source_id = await _seed_page(
        blob, workspace_id, "Acme ships the widget to the whole team on friday."
    )
    oversized = "the widget ships friday " * 200
    assert len(oversized) > MEMORY_BODY_MAX_CHARS
    payload = json.dumps(
        {
            "facts": [
                {
                    "page_id": str(page_id),
                    "memory_kind": "event",
                    "confidence": 8,
                    "body": oversized,
                }
            ]
        }
    )
    client = ExtractionModelClient(payload, Usage(input_tokens=50, output_tokens=20))
    runner = _runner(blob, vec((0, 1.0)), _registry(client))
    with ws(workspace_id):
        await runner.drive(_derive_consumer(runner))

    rows = [row for row in await _facts(workspace_id) if row.item_class == FACT]
    assert len(rows) == 1
    assert len(rows[0].body) <= MEMORY_BODY_MAX_CHARS
    assert rows[0].body.endswith("…")
    clipped = rows[0].body.removesuffix("…")
    assert oversized.startswith(clipped)
    assert oversized[len(clipped)] == " "


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
                    "body": "the acquisition plan has been redacted",
                }
            ]
        }
    )
    client = ExtractionModelClient(payload, Usage(input_tokens=10, output_tokens=5))
    model = ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client), DERIVE_MODEL_JOB)
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


async def test_a_machine_status_pages_facts_retire_when_its_next_change_lands(db: None) -> None:
    """The gate that keeps a machine-status page out of the extraction is what makes its rows
    unreplaceable: no derivation will ever settle that page again, so a fact an earlier one recorded
    from it would stand in the wiki and in recall for good, telling a member a build duration they
    could read from the source and holding the place of a row that tells them something they could
    not. The page is therefore retired outright, the treatment a page that has gone gets, on the
    next change delivered for it — while the page beside it on a stream this tier reads keeps its
    facts and derives new ones in the same batch. An extraction naming the machine-status page
    settles nothing for it either: the gate keeps that page out of the group the model is sent, so a
    row offered for it is a row naming no page the payload carried."""
    assert MACHINE_STATUS_STREAM in MACHINE_STATUS_STREAMS
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    machine, machine_revision = await _seed_wiki_source_page(
        workspace_id, source_id, WHEN, stream=MACHINE_STATUS_STREAM
    )
    read, read_revision = await _seed_wiki_source_page(workspace_id, source_id, WHEN)
    for page_id, revision, body in (
        (machine, machine_revision, MACHINE_STATUS_ROW),
        (read, read_revision, "the acquisition codename is polaris"),
    ):
        await _seed_copy(
            workspace_id,
            SHARED_SUBJECT,
            FACT,
            body,
            WHEN,
            created_from_page_id=page_id,
            created_from_page_revision=revision,
            source_id=source_id,
            memory_kind="event",
        )
    store = _store(workspace_id, vec((19, 1.0)))
    offered = json.dumps(
        {
            "facts": [
                {"page_id": str(page_id), "memory_kind": "fact", "confidence": 7, "body": body}
                for page_id, body in (
                    (machine, MACHINE_STATUS_REDERIVED),
                    (read, "the acquisition codename is meridian"),
                )
            ]
        }
    )
    deriver = _scripted(store, offered)
    with ws(workspace_id):
        await deriver.apply(
            (
                _change(
                    machine,
                    source_id,
                    SHARED_SUBJECT,
                    PAGE_BODY,
                    machine_revision,
                    "sha256:machine",
                    stream=MACHINE_STATUS_STREAM,
                ),
                _change(read, source_id, SHARED_SUBJECT, PAGE_BODY, read_revision, "sha256:read"),
            )
        )

    assert await _page_facts(machine) == {}
    assert await _page_facts(read) == {"the acquisition codename is meridian": read_revision}


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


OVERVIEW_PARAGRAPH = " ".join(
    (
        "The zephyr protocol handshake rotates every hour, and each rotation issues a fresh nonce"
        " that the client must echo before the gateway will open an authenticated session for it.",
        "Rob Ryan owns the rotation schedule and reviews it with the platform infrastructure group"
        " at the start of every quarter, alongside the audit record the gateway writes for each"
        " issued credential.",
        "A token that is not redeemed within sixty seconds expires, and the client falls back to"
        " the previous nonce only when the gateway has already acknowledged that older credential.",
        "Acme Corp runs the same handshake in its staging infrastructure, where the hour is"
        " shortened to five minutes so that a rotation defect surfaces in a working day rather"
        " than a fortnight.",
        "The team measured the change over thirteen months and recorded no session lost to a"
        " rotation, so the shortened window stands as the default for every new deployment.",
    )
)
"""A summary written to the whole of the paragraph shape the consolidation prompt asks a model
for: five sentences at the word budget."""


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


LEDGER_COPIES = (
    "the ops ledger tracks every invoice the finance team files",
    "the ops ledger tracks each invoice that the finance team files",
    "the ops ledger tracks all invoices the finance team files",
)


async def _seed_copy(
    workspace_id: UUID,
    subject: str,
    item_class: str,
    body: str,
    created_at: datetime,
    superseded_by: UUID | None = None,
    created_from_page_id: UUID | None = None,
    created_from_page_revision: int = 1,
    source_id: UUID | None = None,
    memory_kind: MemoryKind = KIND_FACT,
    confidence: int = 5,
) -> UUID:
    """One memory row exactly as history left it, seeded at a chosen `created_at` rather than
    committed — the ages the sweep reads are what a test fixes, and they are what makes the newest
    copy of a group deterministic. `memory_kind` is what files a row under one band of the wiki, so
    it is what a section test seeds by. A page-derived row carries the `memory_source` link `commit`
    writes beside its primary binding, because that link is what a granted reader reaches the row
    through — a binding without one is a row no reader could have found."""
    item_id = uuid4()
    page_source_id = None
    if created_from_page_id is not None:
        page_source_id = uuid4() if source_id is None else source_id
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject=subject,
                body=body,
                item_class=item_class,
                memory_kind=memory_kind,
                confidence=confidence,
                source_ref=None,
                created_from_page_id=created_from_page_id,
                created_from_page_revision=(
                    created_from_page_revision if created_from_page_id is not None else None
                ),
                source_id=page_source_id,
                embedding_digest="sha256:seeded",
                superseded_by=superseded_by,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        if created_from_page_id is not None:
            await connection.execute(
                sa.insert(memory_source).values(
                    workspace_id=workspace_id,
                    memory_item_id=item_id,
                    source_id=page_source_id,
                    source_uid=uuid7(),
                    page_id=created_from_page_id,
                    page_uid=uuid7(),
                    revision=created_from_page_revision,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    return item_id


def _deduper(workspace_id: UUID, embed: EmbedClient) -> MemoryDeduper:
    return MemoryDeduper(
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        store=ScopedStore(extension=memory_manifest.NAME),
    )


def _live(rows: list[sa.Row]) -> set[UUID]:
    return {row.id for row in rows if row.superseded_by is None}


async def _by_body(workspace_id: UUID) -> dict[str, sa.Row]:
    return {row.body: row for row in await _facts(workspace_id)}


async def _age(item_id: UUID, created_at: datetime, superseded_by: UUID | None = None) -> None:
    """Put one row where history left it — at a chosen age, optionally already retired — so a test
    drives the revival that follows through the real `commit` instead of simulating it. Ages are
    chosen hours apart because `now()` is second-resolution on sqlite, where two rows written in
    one test would otherwise tie."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(created_at=created_at, superseded_by=superseded_by)
            .where(memory_item.c.id == item_id)
        )


async def _cursor_value(value: JsonValue) -> None:
    await ScopedStore(extension=memory_manifest.NAME).put(DEDUP_CURSOR_KEY, value)


async def _let_time_pass(workspace_id: UUID, elapsed: timedelta) -> None:
    """Age every row in the workspace by `elapsed`, which is what the clock does between a write and
    the sweep that first sees it — the sweep reads nothing under DEDUP_MIN_AGE. Rows move relative
    to the stamps they already carry, so a test that drives real commits still proves which of them
    the write path made newer."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.id, memory_item.c.created_at).where(
                    memory_item.c.workspace_id == workspace_id
                )
            )
        ).all()
        for row in rows:
            await connection.execute(
                sa.update(memory_item)
                .values(created_at=row.created_at - elapsed)
                .where(memory_item.c.id == row.id)
            )


async def test_dedup_leaves_distinct_facts_and_page_derived_rows(db: None) -> None:
    """The sweep collapses restatements, never a group. Two facts of one subject that merely share
    it stay live and unstamped, and a page-derived row restating one of them word for word is not
    a copy the sweep may touch at all — its lifecycle belongs to supersede_page_facts."""
    workspace_id = await _workspace()
    start = datetime.now(UTC) - timedelta(hours=3)
    alpha, beta = "the alpha release ships in march", "the beta program opens to twelve customers"
    kept = [
        await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, alpha, start),
        await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, beta, start + timedelta(minutes=1)),
        await _seed_copy(
            workspace_id,
            SHARED_SUBJECT,
            FACT,
            alpha,
            start + timedelta(minutes=2),
            created_from_page_id=uuid4(),
        ),
    ]
    embed = MappedEmbed({alpha: vec((26, 1.0)), beta: vec((27, 1.0))})
    with ws(workspace_id):
        await _deduper(workspace_id, embed).run()

    assert _live(await _facts(workspace_id)) == set(kept)


async def test_dedup_never_collapses_across_subject_or_class(db: None) -> None:
    """Every body here embeds to one vector, so only the `(subject, item_class)` grouping keeps a
    member's own note and a semantic ledger out of a shared fact's collapse. Three groups of two
    copies collapse to three heads, each its own group's newest — a sweep that clustered across
    groups would leave one head for all six."""
    workspace_id = await _workspace()
    member = member_subject(uuid4())
    start = datetime.now(UTC) - timedelta(hours=3)
    groups = {
        (member, FACT): ("a private note", "a private note restated"),
        (SHARED_SUBJECT, SEMANTIC): ("a semantic ledger", "the ledger restated"),
        (SHARED_SUBJECT, FACT): ("a shared fact", "the shared fact restated"),
    }
    heads: dict[UUID, UUID] = {}
    for index, ((subject, item_class), (older, newer)) in enumerate(groups.items()):
        offset = start + timedelta(minutes=index)
        donor = await _seed_copy(workspace_id, subject, item_class, older, offset)
        heads[donor] = await _seed_copy(
            workspace_id, subject, item_class, newer, offset + timedelta(seconds=30)
        )
    with ws(workspace_id):
        deduper = _deduper(workspace_id, StubEmbed(vec((43, 1.0))))
        for _ in groups:
            await deduper.run()

    stamps = {row.id: row.superseded_by for row in await _facts(workspace_id)}
    assert _live(await _facts(workspace_id)) == set(heads.values())
    assert {donor: stamps[donor] for donor in heads} == heads


async def test_dedup_stamps_live_rows_only_and_leaves_an_earlier_stamp_alone(db: None) -> None:
    """A superseded row is not the sweep's to move. A row an earlier rewrite stamped keeps that
    stamp rather than being re-pointed at the newest copy, and is never read as a copy to collapse:
    only a member restating that body may bring it back, and `commit` — not this sweep — is what
    clears the stamp when they do."""
    workspace_id = await _workspace()
    start = datetime.now(UTC) - timedelta(hours=3)
    middle = await _seed_copy(
        workspace_id, SHARED_SUBJECT, FACT, LEDGER_COPIES[1], start + timedelta(minutes=1)
    )
    newest = await _seed_copy(
        workspace_id, SHARED_SUBJECT, FACT, LEDGER_COPIES[2], start + timedelta(minutes=2)
    )
    oldest = await _seed_copy(
        workspace_id, SHARED_SUBJECT, FACT, LEDGER_COPIES[0], start, superseded_by=middle
    )
    with ws(workspace_id):
        await _deduper(workspace_id, StubEmbed(vec((31, 1.0)))).run()

    stamps = {row.id: row.superseded_by for row in await _facts(workspace_id)}
    assert stamps == {oldest: middle, middle: newest, newest: None}


async def test_dedup_candidates_name_a_workspace_once_per_duplicate_backlog(db: None) -> None:
    """The dedup JobSpec binds a workspace exactly once however many duplicate groups it holds —
    two groups are two grouped rows and one candidate. A workspace whose groups hold a single row,
    one whose only repeats are page-derived, and one whose copies are all younger than the sweep's
    own floor are never bound at all, so a fleet's healed and freshly-written workspaces run no
    transaction on the tick."""
    duplicated, single, page_derived, fresh = (
        await _workspace(),
        await _workspace(),
        await _workspace(),
        await _workspace(),
    )
    for index in range(2):
        await _seed_copy(
            fresh, SHARED_SUBJECT, FACT, f"a statement just written {index}", datetime.now(UTC)
        )
    stamp = datetime.now(UTC) - timedelta(hours=3)
    for subject in (SHARED_SUBJECT, member_subject(uuid4())):
        for index in range(2):
            await _seed_copy(duplicated, subject, FACT, f"a repeated statement {index}", stamp)
    await _seed_copy(single, SHARED_SUBJECT, FACT, "the only statement", stamp)
    for index in range(2):
        await _seed_copy(
            page_derived,
            SHARED_SUBJECT,
            FACT,
            f"a derived statement {index}",
            stamp,
            created_from_page_id=uuid4(),
        )
    dedup = next(
        job for job in memory_manifest.manifest().jobs if job.name == memory_manifest.DEDUP_JOB
    )
    assert await dedup.candidates() == (duplicated,)


async def test_the_sweep_keeps_the_copy_a_member_just_re_asserted(db: None) -> None:
    """The revive and the sweep have to agree on which copy is current, or they undo each other
    every hour. A member restates a body an earlier tick retired; `commit` revives that row and
    moves its `created_at` to the restatement, so when the next sweep reads both copies live the
    restatement is the newest and the copy that replaced it is what retires. Without the moved stamp
    the sweep hands the member's wording straight back to the version they replaced, and restating
    again never wins."""
    workspace_id = await _workspace()
    probe = vec((33, 1.0))
    original = "the deploy has no code_review profile"
    reworded = "re-confirmed: the deploy still has no code_review profile"
    store = _store(workspace_id, probe)
    with ws(workspace_id):
        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=original))
        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=reworded))
        rows = await _by_body(workspace_id)
        now = datetime.now(UTC)
        await _age(rows[reworded].id, now - timedelta(hours=3))
        await _age(rows[original].id, now - timedelta(hours=6), rows[reworded].id)

        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=original))
        assert _live(await _facts(workspace_id)) == {rows[original].id, rows[reworded].id}

        await _let_time_pass(workspace_id, timedelta(hours=2))
        await _deduper(workspace_id, StubEmbed(probe)).run()

    settled = await _by_body(workspace_id)
    assert settled[original].superseded_by is None
    assert settled[reworded].superseded_by == settled[original].id


async def test_an_unchanged_group_is_skipped_instead_of_re_embedded_every_tick(db: None) -> None:
    """A healed group must not pay for its own health forever. Every workspace holding two shared
    facts is a standing candidate, so a tick that re-embeds the whole group whether or not anything
    moved bills the fleet for up to DEDUP_GROUP_MAX embeddings an hour in perpetuity. The sweep
    records what the group looked like and skips it while that still holds; a row entering the
    group makes it due again."""
    workspace_id = await _workspace()
    start = datetime.now(UTC) - timedelta(hours=3)
    alpha, beta = "the alpha release ships in march", "the beta program opens to twelve customers"
    added = "the gamma trial starts in june"
    await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, alpha, start)
    await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, beta, start + timedelta(minutes=1))
    embed = MappedEmbed({alpha: vec((36, 1.0)), beta: vec((37, 1.0)), added: vec((38, 1.0))})
    with ws(workspace_id):
        deduper = _deduper(workspace_id, embed)
        await deduper.run()
        swept = embed.calls
        await deduper.run()
        skipped = embed.calls

        await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, added, start + timedelta(minutes=2))
        await deduper.run()

    assert swept == 1
    assert skipped == 1
    assert embed.calls == 2


async def test_a_copy_retired_mid_pass_leaves_its_whole_cluster_unstamped(db: None) -> None:
    """The guard on the locked-donor pattern, exercised where it is actually reachable: another
    writer retires one copy in the window between the sweep's read and its stamp. The locked read
    no longer matches what the pass decided on, so nothing is stamped at all — a partial stamp
    would move rows the sweep never verified, onto a head chosen from a set that no longer
    exists."""
    workspace_id = await _workspace()
    start = datetime.now(UTC) - timedelta(hours=3)
    ids = [
        await _seed_copy(
            workspace_id, SHARED_SUBJECT, SEMANTIC, body, start + timedelta(minutes=index)
        )
        for index, body in enumerate(LEDGER_COPIES)
    ]
    with ws(workspace_id):
        await _deduper(workspace_id, SupersedingEmbed(vec((39, 1.0)), ids[0])).run()

    stamps = {row.id: row.superseded_by for row in await _facts(workspace_id)}
    assert stamps[ids[1]] is None
    assert stamps[ids[2]] is None
    assert stamps[ids[0]] not in (None, ids[2])


async def test_a_fresh_copy_survives_the_sweep_of_the_family_it_joins(db: None) -> None:
    """The floor fences the rows the sweep reads, not just the groups it visits. A member writing a
    third wording of a statement whose older copies are already a swept family must keep it: the
    aged copies collapse around it while the minutes-old row is neither read nor stamped, so no
    tick can retire what a member wrote just before it landed. The row joins the family on the
    rotation that first reads it as history."""
    workspace_id = await _workspace()
    now = datetime.now(UTC)
    aged_bodies = ("the vault key rotates on sunday", "the vault key is rotated each sunday")
    accreted = [
        await _seed_copy(
            workspace_id, SHARED_SUBJECT, FACT, body, now - timedelta(hours=3, minutes=index)
        )
        for index, body in enumerate(aged_bodies)
    ]
    fresh = await _seed_copy(
        workspace_id, SHARED_SUBJECT, FACT, "the vault key is rotated every sunday", now
    )
    with ws(workspace_id):
        await _deduper(workspace_id, StubEmbed(vec((42, 1.0)))).run()

    stamps = {row.id: row.superseded_by for row in await _facts(workspace_id)}
    assert stamps[fresh] is None
    assert stamps[accreted[0]] is None
    assert stamps[accreted[1]] == accreted[0]


@pytest.mark.parametrize(
    "stored",
    ["shared", ["shared"], ["shared", FACT, "extra"], [1, 2]],
    ids=["not-a-list", "one-element", "three-elements", "not-strings"],
)
async def test_a_cursor_the_sweep_did_not_write_fails_loud(db: None, stored: JsonValue) -> None:
    """Only an absent key means no tick has run. A value of any other shape is a corrupted key
    space, and silently reading it as "start from the beginning" would hide that for good while
    quietly re-walking the prefix every tick."""
    workspace_id = await _workspace()
    stamp = datetime.now(UTC) - timedelta(hours=3)
    for index, body in enumerate(LEDGER_COPIES[:2]):
        await _seed_copy(workspace_id, SHARED_SUBJECT, FACT, body, stamp + timedelta(minutes=index))
    with ws(workspace_id):
        await _cursor_value(stored)
        with pytest.raises(RuntimeError, match=DEDUP_CURSOR_KEY):
            await _deduper(workspace_id, StubEmbed(vec((40, 1.0)))).run()


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


async def test_a_page_read_again_at_one_revision_keeps_only_its_latest_reading(db: None) -> None:
    """What makes a rebuild a rebuild. The cursor is sent back over pages whose content never
    changed, so a page is read a second time at the revision it already had and the statement being
    replaced is bound to that very revision — a test on the revision alone would find nothing stale
    and leave the member reading one page twice. The pass retires every link the page carries other
    than the rows it just committed, so the older reading goes and its replacement stands."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(workspace_id, vec((31, 1.0)))
    change = _change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page")
    with ws(workspace_id):
        await _scripted(store, _extraction(page_id, "the codename is polaris")).apply((change,))
        assert await _page_facts(page_id) == {"the codename is polaris": 1}
        await _scripted(store, _extraction(page_id, "the codename is meridian")).apply((change,))

    assert await _page_facts(page_id) == {"the codename is meridian": 1}


async def test_a_page_read_again_that_derives_no_fact_keeps_the_reading_it_has(db: None) -> None:
    """Nothing goes before its replacement is committed. A pass that records no fact for a page
    settles nothing for it, so the retirement never runs — a rebuild changes what the member reads
    only where it has something to put there."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(workspace_id, vec((32, 1.0)))
    change = _change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page")
    with ws(workspace_id):
        await _scripted(store, _extraction(page_id, "the codename is polaris")).apply((change,))
        await _scripted(store, json.dumps({"facts": []})).apply((change,))

    assert await _page_facts(page_id) == {"the codename is polaris": 1}


async def test_a_machine_status_pages_facts_are_retired_when_the_pass_reaches_it_again(
    db: None,
) -> None:
    """A workspace that synced before the stream gate holds rows derived from workflow runs and
    stargazers, and no derivation of those pages will ever settle a replacement to retire them by:
    a finished run never moves again, so the revision fence never hides them either. The gate is a
    refusal of the page, not only of its next reading, so the pass retires what the page derived
    the next time it is delivered — which is the pass the wiki panel's rebuild sends over every
    page — and recall loses the row with the wiki band."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page_authority(workspace_id, page_id, source_id, SHARED_SUBJECT)
    probe = vec((4, 1.0))
    store = _store(workspace_id, probe)
    change = _change(page_id, source_id, SHARED_SUBJECT, PAGE_BODY, 1, "sha256:page")
    with ws(workspace_id):
        await _scripted(store, _extraction(page_id, "the codename is polaris")).apply((change,))
        await _index_memory(store, probe)
        assert await _page_facts(page_id) == {"the codename is polaris": 1}

        await _scripted(store, _extraction(page_id, "the codename is polaris")).apply(
            (replace(change, stream="workflow_runs"),)
        )
        assert await _page_facts(page_id) == {}
        assert (
            await store.index.lexical(
                "codename polaris", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 5
            )
            == ()
        )


async def test_only_an_admin_can_ask_for_the_page_facts_to_be_written_again(db: None) -> None:
    """The tool is the whole act the portal button submits, so its gate is the button's gate: the
    cursor stands where it was and the pass reads nothing twice."""
    workspace_id = await _workspace()
    scoped = ScopedStore(extension=memory_manifest.NAME)
    (tool,) = (one for one in memory_manifest.manifest().tools if one.name == REBUILD_TOOL)
    with ws(workspace_id):
        await scoped.put(memory_manifest.DERIVE_CURSOR_KEY, "a-cursor")
        member_id = await _seed_admin(workspace_id, admin=False)
        with pytest.raises(ValueError, match=memory_manifest.REBUILD_ADMIN_ONLY):
            await tool.handler(_rebuild_ctx(workspace_id, member_id), RebuildPageFactsInput())
        assert await scoped.get(memory_manifest.DERIVE_CURSOR_KEY) == "a-cursor"


async def test_a_rebuild_asked_for_by_nobody_is_refused_for_want_of_a_speaker(db: None) -> None:
    """A turn nobody is speaking on holds no admin either, so an admin gate read alone answers a
    call that merely omitted `requested_by` with an authority the model cannot obtain. Who is
    asking is answered first, and the refusal names the repair."""
    workspace_id = await _workspace()
    scoped = ScopedStore(extension=memory_manifest.NAME)
    (tool,) = (one for one in memory_manifest.manifest().tools if one.name == REBUILD_TOOL)
    with ws(workspace_id):
        await scoped.put(memory_manifest.DERIVE_CURSOR_KEY, "a-cursor")
        await _seed_admin(workspace_id)
        with pytest.raises(SpeakerRequired, match="requested_by"):
            await tool.handler(_rebuild_ctx(workspace_id, None), RebuildPageFactsInput())
        assert await scoped.get(memory_manifest.DERIVE_CURSOR_KEY) == "a-cursor"


def _rebuild_ctx(workspace_id: UUID, member_id: UUID | None) -> ToolContext:
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="rebuild",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="auto"),
        spawn=None,  # type: ignore[arg-type]
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=context_for(memory_manifest.NAME, frozenset()),
    )


SECTION_MODEL_JOB = f"memory:{memory_manifest.SECTION_JOB}"
WIKI_BANDS: tuple[MemoryKind, ...] = ("preference", "decision", "task", "event", KIND_FACT)
DECISION_ROWS = (
    "Acme Corp — Chose Postgres over MySQL for the billing store.",
    "Acme Corp — Settled the billing migration for 4 March.",
    "Acme Corp — Named Rob Ryan the owner of the billing migration.",
)
DECISION_PARAGRAPH = (
    "Acme Corp keeps its billing store on Postgres, and Rob Ryan owns the migration onto it, which"
    " is set for 4 March."
)
MOVED_ROW = "Acme Corp — Moved the billing migration to 11 March."
MOVED_PARAGRAPH = (
    "Acme Corp keeps its billing store on Postgres, and Rob Ryan owns the migration onto it, which"
    " has moved to 11 March."
)
TASK_ROWS = (
    "Ivan Petrov — Owes the platform group a rollback plan for pull request 2482.",
    "Nalu Idler — Has yet to sign the renewed data processing agreement.",
    "Rob Ryan — Still has to move the staging gateway onto the loopback proxy.",
)
TASK_PARAGRAPH = (
    "Three pieces of work are outstanding: Ivan Petrov owes the platform group a rollback plan for"
    " pull request 2482, Nalu Idler has not signed the renewed data processing agreement, and Rob"
    " Ryan has the staging gateway still to move onto the loopback proxy."
)
MEMBER_WRITTEN_TASK = "Rob Ryan — Asked for the rollback plan to be chased on Friday."


@dataclass
class RecordingCompletionClient:
    """Streams one canned completion and keeps the requests it was asked with, so a test reads the
    heading and the facts the section pass actually sent."""

    payload: str
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta(text=self.payload)
        yield Usage(input_tokens=10, output_tokens=5)


def _section_model(client: ModelClient) -> ModelAccess:
    return ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client), SECTION_MODEL_JOB)


def _paragraph_model(payload: str) -> ModelAccess:
    return _section_model(StubModelClient(payload, Usage(input_tokens=10, output_tokens=5)))


def _section_writer(workspace_id: UUID, model: ModelAccess | None) -> SectionWriter:
    return SectionWriter(transaction=workspace_tx, workspace_id=workspace_id, model=model)


async def _sections(workspace_id: UUID) -> list[sa.Row]:
    return [row for row in await _facts(workspace_id) if row.item_class == SECTION]


def test_every_memory_kind_names_the_band_a_member_reads() -> None:
    """The headings the page renders, which the pass sends the model as the section it is writing.
    A kind missing from either set is a band whose paragraph would be written blind, and a heading
    that drifts from the page is a paragraph answering a heading nobody reads. The workspace's page
    and a member's own differ in three of the five, because one addresses a company and the other a
    person."""
    assert WORKSPACE_SECTION_HEADINGS == {
        "preference": "How the team works",
        "decision": "Decisions",
        "task": "Open work",
        "event": "History",
        "fact": "Facts",
    }
    assert MEMBER_SECTION_HEADINGS == {
        "preference": "How you work",
        "decision": "Decisions",
        "task": "Tasks",
        "event": "History",
        "fact": "Facts",
    }
    assert set(WORKSPACE_SECTION_HEADINGS) == set(get_args(MemoryKind))
    assert set(MEMBER_SECTION_HEADINGS) == set(get_args(MemoryKind))


def test_a_section_body_is_admitted_at_the_budget_and_refused_past_it() -> None:
    """A section paragraph commits under the one bound every class shares: the budget bounds what
    a statement may run to, never which class carries it."""
    written = MemoryWrite(
        subject=SHARED_SUBJECT, body="x" * MEMORY_BODY_MAX_CHARS, item_class=SECTION
    )
    assert len(written.body) == MEMORY_BODY_MAX_CHARS
    with pytest.raises(ValidationError):
        MemoryWrite(
            subject=SHARED_SUBJECT, body="x" * (MEMORY_BODY_MAX_CHARS + 1), item_class=SECTION
        )


async def test_a_band_under_the_floor_is_left_without_a_paragraph(db: None) -> None:
    """One paragraph per band, and only where the band holds enough rows to amount to more than the
    member reads underneath it. A workspace whose History band is two rows keeps its Decisions
    paragraph and pays no model pass for the other."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    start = datetime.now(UTC) - timedelta(hours=3)
    for index, body in enumerate(DECISION_ROWS):
        await _seed_wiki_row(
            workspace_id, source_id, body, start + timedelta(minutes=index), "decision"
        )
    for index in range(MIN_SECTION_FACTS - 1):
        await _seed_wiki_row(
            workspace_id,
            source_id,
            f"Acme Corp — Shipped release {index}.",
            start + timedelta(minutes=20 + index),
            "event",
        )
    client = RecordingCompletionClient(DECISION_PARAGRAPH)
    with ws(workspace_id):
        await _section_writer(workspace_id, _section_model(client)).run()

    assert len(client.requests) == 1
    assert [row.memory_kind for row in await _sections(workspace_id)] == ["decision"]


COLLAPSED_HISTORY = "Acme Corp shipped four releases while the billing migration was being planned."


async def test_the_section_pass_without_a_model_writes_nothing(db: None) -> None:
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    start = datetime.now(UTC) - timedelta(hours=3)
    for index, body in enumerate(DECISION_ROWS):
        await _seed_wiki_row(
            workspace_id, source_id, body, start + timedelta(minutes=index), "decision"
        )
    with ws(workspace_id):
        await _section_writer(workspace_id, None).run()

    assert await _sections(workspace_id) == []


async def test_section_candidates_name_only_workspaces_holding_a_band_worth_a_paragraph(
    db: None,
) -> None:
    """The daily section JobSpec binds only workspaces where a pass has work: at least
    MIN_SECTION_FACTS live facts under one heading, page-derived rows counted, since that is what a
    wiki is made of — or a paragraph still standing over a band that has fallen under the floor,
    which is the only other work this pass does and which no other pass would ever reach. A
    workspace whose rows are spread one to a band, and one whose rows are all superseded, are never
    candidates, so a fleet's thin workspaces run no daily transaction. The schedule stands clear of
    the other two, so no workspace pays two model jobs in one minute."""
    banded, spread, retired = await _workspace(), await _workspace(), await _workspace()
    collapsed = await _workspace()
    start = datetime.now(UTC) - timedelta(hours=3)
    for index in range(MIN_SECTION_FACTS):
        await _seed_copy(
            workspace_id=banded,
            subject=SHARED_SUBJECT,
            item_class=FACT,
            body=f"a page-derived decision {index}",
            created_at=start,
            created_from_page_id=uuid4(),
            memory_kind="decision",
        )
        await _seed_copy(
            workspace_id=spread,
            subject=SHARED_SUBJECT,
            item_class=FACT,
            body=f"a lone row {index}",
            created_at=start,
            memory_kind=WIKI_BANDS[index],
        )
        await _seed_copy(
            workspace_id=retired,
            subject=SHARED_SUBJECT,
            item_class=FACT,
            body=f"a superseded decision {index}",
            created_at=start,
            superseded_by=uuid4(),
            memory_kind="decision",
        )
    await _seed_copy(
        workspace_id=collapsed,
        subject=SHARED_SUBJECT,
        item_class=FACT,
        body="the one row the curation left",
        created_at=start,
        memory_kind="event",
    )
    await _seed_copy(
        workspace_id=collapsed,
        subject=SHARED_SUBJECT,
        item_class=SECTION,
        body=COLLAPSED_HISTORY,
        created_at=start,
        memory_kind="event",
    )
    section = next(
        job for job in memory_manifest.manifest().jobs if job.name == memory_manifest.SECTION_JOB
    )
    assert set(await section.candidates()) == {banded, collapsed}
    assert len(memory_manifest.SECTION_SCHEDULE.split()) == 6
    assert memory_manifest.SECTION_SCHEDULE not in {
        memory_manifest.MEMORY_INDEX_SCHEDULE,
        memory_manifest.CONSOLIDATE_SCHEDULE,
        memory_manifest.DEDUP_SCHEDULE,
    }


async def test_a_members_band_is_written_under_the_heading_they_read(db: None) -> None:
    """The heading travels with the facts, and a member's page titles its bands differently from the
    workspace's. A paragraph written for `Open work` and drawn under `Tasks` was written to open a
    band that is not the one the member reads it over, and the prompt is explicitly told which band
    it is writing — so the wording follows the subject the rows belong to."""
    workspace_id = await _workspace()
    member_id = await _seed_admin(workspace_id)
    subject = member_subject(member_id)
    shared_feed = await _seed_wiki_feed(workspace_id)
    member_feed = await _seed_wiki_feed(workspace_id)
    start = datetime.now(UTC) - timedelta(hours=3)
    for index, body in enumerate(TASK_ROWS):
        await _seed_wiki_row(
            workspace_id, shared_feed, body, start + timedelta(minutes=index), "task"
        )
        await _seed_wiki_row(
            workspace_id,
            member_feed,
            f"You — {body}",
            start + timedelta(minutes=20 + index),
            "task",
            subject=subject,
        )
    client = RecordingCompletionClient(TASK_PARAGRAPH)
    with ws(workspace_id):
        await _section_writer(workspace_id, _section_model(client)).run()

    sent = {
        json.loads(request.messages[0].content)["section"]: json.loads(request.messages[0].content)[
            "facts"
        ]
        for request in client.requests
    }
    assert set(sent) == {"Tasks", "Open work"}
    assert sent["Open work"] == list(reversed(TASK_ROWS))
    assert sent["Tasks"] == [f"You — {body}" for body in reversed(TASK_ROWS)]
    assert {row.subject for row in await _sections(workspace_id)} == {SHARED_SUBJECT, subject}


OVERVIEW_MODEL_JOB = f"memory:{memory_manifest.OVERVIEW_JOB}"
PAGE_OVERVIEW = (
    "Acme Corp runs its billing on Postgres and is moving the last of it across on 4 March, with"
    " Rob Ryan owning the cutover. Three pieces of work stand open against that date."
)
MOVED_OVERVIEW = "Acme Corp has moved the billing cutover to 11 March, and Rob Ryan still owns it."
RETIRED_FACT = "Acme Corp — Queued the nightly billing job."
SUPERSEDED_FACT = "Acme Corp — Ran the billing migration rehearsal."


def _overview_writer(workspace_id: UUID, model: ModelAccess | None) -> OverviewWriter:
    return OverviewWriter(transaction=workspace_tx, workspace_id=workspace_id, model=model)


def _overview_model(client: ModelClient) -> ModelAccess:
    return ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client), OVERVIEW_MODEL_JOB)


async def _overviews(workspace_id: UUID) -> list[sa.Row]:
    return [row for row in await _facts(workspace_id) if row.item_class == OVERVIEW]


async def _seed_page_facts(
    workspace_id: UUID, subject: str = SHARED_SUBJECT, count: int = MIN_OVERVIEW_FACTS
) -> tuple[str, ...]:
    """A page's worth of live rows spread across the wiki's bands, a minute apart — the population
    an overview is written from, and enough of it to clear the pass's own floor. Each row is
    distilled from a synced page of its own and bound to the revision that page stands at, because
    a row off its page is one no pass here may write a paragraph from."""
    source_id = await _seed_wiki_feed(workspace_id)
    start = datetime.now(UTC) - timedelta(hours=3)
    bodies = tuple(f"Acme Corp — Shipped release {index}." for index in range(count))
    for index, body in enumerate(bodies):
        await _seed_wiki_row(
            workspace_id,
            source_id,
            body,
            start + timedelta(minutes=index),
            WIKI_BANDS[index % len(WIKI_BANDS)],
            subject=subject,
        )
    return bodies


async def test_a_page_under_the_floor_is_left_without_an_overview(db: None) -> None:
    """Under the floor the whole page is a glance, and a paragraph over it would restate what the
    member reads underneath at the cost of a model pass."""
    workspace_id = await _workspace()
    await _seed_page_facts(workspace_id, count=MIN_OVERVIEW_FACTS - 1)
    client = RecordingCompletionClient(PAGE_OVERVIEW)
    with ws(workspace_id):
        await _overview_writer(workspace_id, _overview_model(client)).run()

    assert client.requests == []
    assert await _overviews(workspace_id) == []


async def test_the_overview_is_the_workspaces_page_alone(db: None) -> None:
    """The paragraph says where the company stands — what it does, the numbers it steers by, what is
    in flight. A member's own page is not a company, so their rows neither earn an overview nor
    reach the payload of the workspace's."""
    workspace_id = await _workspace()
    member_id = await _seed_admin(workspace_id)
    subject = member_subject(member_id)
    shared = await _seed_page_facts(workspace_id)
    await _seed_page_facts(workspace_id, subject=subject)
    client = RecordingCompletionClient(PAGE_OVERVIEW)
    with ws(workspace_id):
        await _overview_writer(workspace_id, _overview_model(client)).run()

    assert len(client.requests) == 1
    assert json.loads(client.requests[0].messages[0].content)["facts"] == list(reversed(shared))
    assert [row.subject for row in await _overviews(workspace_id)] == [SHARED_SUBJECT]


async def test_the_overview_pass_without_a_model_writes_nothing(db: None) -> None:
    workspace_id = await _workspace()
    await _seed_page_facts(workspace_id)
    with ws(workspace_id):
        await _overview_writer(workspace_id, None).run()

    assert await _overviews(workspace_id) == []


async def test_overview_candidates_name_only_workspaces_holding_a_shared_page(db: None) -> None:
    """The daily JobSpec binds only workspaces where a pass has work: at least MIN_OVERVIEW_FACTS
    live facts on the workspace-shared subject, or a paragraph still opening a page that has since
    fallen under that floor — the pass's other act, and one no other pass would reach. A workspace
    whose rows are all one member's own, and one whose page is still a handful of rows with nothing
    standing over them, are never bound. The schedule stands clear of the other jobs and sits in the
    same night as the section pass."""
    paged, private, thin = await _workspace(), await _workspace(), await _workspace()
    collapsed = await _workspace()
    await _seed_page_facts(paged)
    await _seed_page_facts(private, subject=member_subject(await _seed_admin(private)))
    await _seed_page_facts(thin, count=MIN_OVERVIEW_FACTS - 1)
    await _seed_page_facts(collapsed, count=MIN_OVERVIEW_FACTS - 1)
    await _seed_copy(
        collapsed,
        SHARED_SUBJECT,
        OVERVIEW,
        PAGE_OVERVIEW,
        datetime.now(UTC) - timedelta(hours=2),
    )
    job = next(
        spec
        for spec in memory_manifest.manifest().jobs
        if spec.name == memory_manifest.OVERVIEW_JOB
    )
    assert set(await job.candidates()) == {paged, collapsed}
    assert len(memory_manifest.OVERVIEW_SCHEDULE.split()) == 6
    assert memory_manifest.OVERVIEW_SCHEDULE not in {
        memory_manifest.MEMORY_INDEX_SCHEDULE,
        memory_manifest.CONSOLIDATE_SCHEDULE,
        memory_manifest.DEDUP_SCHEDULE,
        memory_manifest.SECTION_SCHEDULE,
        memory_manifest.PROFILE_SCHEDULE,
        memory_manifest.PAGE_PASS_SCHEDULE,
    }
    assert _daily_time(memory_manifest.OVERVIEW_SCHEDULE) > _daily_time(
        memory_manifest.SECTION_SCHEDULE
    )
    assert _daily_time(memory_manifest.OVERVIEW_SCHEDULE) > _daily_time(
        memory_manifest.PAGE_PASS_SCHEDULE
    )


PROFILE_MODEL_JOB = f"memory:{memory_manifest.PROFILE_JOB}"
ROLE = "Cofounder, product and finance"
FOCUS = "Owns the billing migration onto Postgres, which is set for 4 March."
SECOND_ROLE = "Contract sales"
SECOND_FOCUS = "Carrying the renewed data processing agreement with Nalu Idler."
MOVED_FOCUS = "Owns the billing migration onto Postgres, which has moved to 11 March."
PRIVATE_FACT = "You — Told the assistant to keep replies to three sentences."


@dataclass
class PeopleClient:
    """Streams one canned `write_people` call — the compelled tool the People pass offers — and
    keeps the requests it was asked with, so a test reads the roster and the facts it actually
    sent."""

    arguments: str
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ToolCallStart(id="call-1", name=PROFILE_TOOL)
        yield ToolCallDelta(id="call-1", partial_json=self.arguments)
        yield Usage(input_tokens=10, output_tokens=5)


def _people(*entries: tuple[str, str, str]) -> str:
    return json.dumps(
        {"people": [{"name": name, "role": role, "focus": focus} for name, role, focus in entries]}
    )


def _profile_writer(workspace_id: UUID, client: ModelClient | None) -> ProfileWriter:
    return ProfileWriter(
        transaction=workspace_tx,
        workspace_id=workspace_id,
        model=(
            None
            if client is None
            else ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client), PROFILE_MODEL_JOB)
        ),
    )


async def _profiles(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        memory_profile.c.member_id,
                        memory_profile.c.role,
                        memory_profile.c.focus,
                        memory_profile.c.written_at,
                    )
                    .where(memory_profile.c.workspace_id == workspace_id)
                    .order_by(memory_profile.c.member_id)
                )
            ).all()
        )


def _profile_ctx(workspace_id: UUID) -> ToolContext:
    """A turn in a channel another organization sits in: an audience carrying no workspace-shared
    subject, which is the one fence the People band has."""
    foreign = foreign_room_audience("slack", "shared-channel")
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="who is who",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="auto"),
        spawn=None,  # type: ignore[arg-type]
        speaker_member_id=None,
        audience=foreign,
        artifact_token_secret="",
        ext=context_for(memory_manifest.NAME, frozenset(), audience=foreign),
    )


async def _member_email(member_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.email).where(tables.member.c.id == member_id)
            )
        ).scalar_one()


async def test_a_member_holds_one_profile_row_however_often_the_pass_runs(db: None) -> None:
    """One entry per member, rewritten in place: the key is (workspace, member), so a second pass
    over facts that have moved replaces what the People band shows rather than stacking a second
    account of the same person beside it."""
    workspace_id = await _workspace()
    member_id = await _seed_admin(workspace_id)
    email = await _member_email(member_id)
    await _seed_page_facts(workspace_id)
    with ws(workspace_id):
        await _profile_writer(workspace_id, PeopleClient(_people((email, ROLE, FOCUS)))).run()

    (first,) = await _profiles(workspace_id)
    assert (first.member_id, first.role, first.focus) == (member_id, ROLE, FOCUS)

    with ws(workspace_id):
        await _profile_writer(workspace_id, PeopleClient(_people((email, ROLE, MOVED_FOCUS)))).run()

    (rewritten,) = await _profiles(workspace_id)
    assert (rewritten.member_id, rewritten.role, rewritten.focus) == (member_id, ROLE, MOVED_FOCUS)


async def test_the_people_pass_without_a_model_writes_nothing(db: None) -> None:
    workspace_id = await _workspace()
    await _seed_admin(workspace_id)
    await _seed_page_facts(workspace_id)
    with ws(workspace_id):
        await _profile_writer(workspace_id, None).run()

    assert await _profiles(workspace_id) == []


async def test_people_candidates_name_only_workspaces_with_a_shared_fact(db: None) -> None:
    """The daily JobSpec binds only workspaces an entry could be drawn in: one holding a live
    workspace-shared fact. A workspace whose rows are all one member's own is never bound, since
    nothing a colleague may read has been recorded there. The schedule stands clear of every other
    job and sits in the same night as the two paragraph passes, after them."""
    shared, private = await _workspace(), await _workspace()
    await _seed_page_facts(shared)
    await _seed_page_facts(private, subject=member_subject(await _seed_admin(private)))
    job = next(
        spec for spec in memory_manifest.manifest().jobs if spec.name == memory_manifest.PROFILE_JOB
    )
    assert await job.candidates() == (shared,)
    assert len(memory_manifest.PROFILE_SCHEDULE.split()) == 6
    assert memory_manifest.PROFILE_SCHEDULE not in {
        memory_manifest.MEMORY_INDEX_SCHEDULE,
        memory_manifest.CONSOLIDATE_SCHEDULE,
        memory_manifest.DEDUP_SCHEDULE,
        memory_manifest.SECTION_SCHEDULE,
        memory_manifest.OVERVIEW_SCHEDULE,
        memory_manifest.PAGE_PASS_SCHEDULE,
    }
    assert _daily_time(memory_manifest.PROFILE_SCHEDULE) > _daily_time(
        memory_manifest.OVERVIEW_SCHEDULE
    )
    assert _daily_time(memory_manifest.PROFILE_SCHEDULE) > _daily_time(
        memory_manifest.PAGE_PASS_SCHEDULE
    )


PAGE_PASS_MODEL_JOB = f"memory:{memory_manifest.PAGE_PASS_JOB}"
DUPLICATE_ROWS = (
    "Acme Corp — Merged pull request 2482, the loopback proxy for sandbox clients, on 4 March.",
    "Acme Corp — Pull request 2482 landed the loopback proxy.",
    "Acme Corp — Ivan Petrov merged pull request 2482.",
    "Acme Corp — Pull request 2482 was merged.",
    "Acme Corp — The loopback proxy shipped in pull request 2482.",
    "Acme Corp — Pull request 2482 is merged.",
)
"""One claim as six source pages wrote it. Extraction reads one page and sees one fact; the section
pass reads one band and writes prose over all six. Only a reader holding the page at once can say
that five of these rows are the first one said again, which is what this pass exists for."""
KEPT_DUPLICATE = DUPLICATE_ROWS[0]
SEEDED_HISTORY = (
    "Acme Corp merged pull request 2482, and the loopback proxy for sandbox clients went with it."
)
REWRITTEN_HISTORY = (
    "Acme Corp merged pull request 2482 on 4 March, which gave sandbox clients the loopback proxy."
)
CURATION_REASON = "restates the row that carries the claim"
LEFT_BEHIND_ROW = "Acme Corp — 2482 gave sandbox clients a loopback proxy."
"""What one revision of one source page derived, kept live at that revision by the revision after it
landing no fact of its own. The listing and recall fence it out; the wiki never draws it again."""
AGENT_WRITTEN_ROW = "Acme Corp — Rob Ryan confirmed 2482 shipped on 4 March."
"""A row an agent committed through `memory_update`: no page, no source, and no other writer that
could restate it. This tier exists for the rows several source pages wrote about one claim."""
REFUSED_SUBJECT = "member:a-page-read-badly"
CURATED_SUBJECT = "member:a-page-read-well"


@dataclass
class CurationClient:
    """Streams one canned `curate_page` call — the compelled tool the page pass offers — and keeps
    the requests it was asked with, so a test reads the page the pass actually sent and the ids it
    put on the rows."""

    arguments: str
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ToolCallStart(id="call-1", name=PAGE_PASS_TOOL)
        yield ToolCallDelta(id="call-1", partial_json=self.arguments)
        yield Usage(input_tokens=10, output_tokens=5)


def _curation(retire: tuple[int, ...] = (), keeper: int = 1) -> str:
    """A curation answer. Every retirement names the row that keeps the claim, because that is the
    only retirement the pass admits — a row nothing else states is one it has no reason to take."""
    return json.dumps(
        {
            "retire": [
                {"id": row_id, "reason": CURATION_REASON, "duplicate_of": keeper}
                for row_id in retire
            ]
        }
    )


def _page_pass(workspace_id: UUID, client: ModelClient | None) -> PagePass:
    return PagePass(
        transaction=workspace_tx,
        workspace_id=workspace_id,
        model=(
            None
            if client is None
            else ModelAccess(_Resolver(AUTO_MODEL, CORE_PRICING, client), PAGE_PASS_MODEL_JOB)
        ),
    )


async def _seed_wiki_feed(workspace_id: UUID) -> UUID:
    """The synced feed a subject's wiki is written from. A page-derived row reaches a member only
    through a grant on the connection behind the feed its page came from, so one grant on this
    covers the whole page. Which subject its rows carry is the page's, never the feed's."""
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=uuid7(),
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=await _seed_connection(connection, workspace_id, "folder"),
                next_sync_at=WHEN,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
    return source_id


async def _seed_wiki_source_page(
    workspace_id: UUID,
    source_id: UUID,
    created_at: datetime,
    subject: str = SHARED_SUBJECT,
    stream: str = "notes",
) -> tuple[UUID, int]:
    """One synced document of `source_id` with the `mem_page` mirror row the page indexer keeps of
    it, and the revision the feed counter gave it. Both ends are what make a fact derived from this
    page readable at all: the pass joins the mirror, and the listing and recall a member reads join
    the core page through the reader's grant, so a fact seeded without them is one nobody sees. The
    revision is read back rather than chosen, because the feed assigns it on insert: a fact bound to
    any other number is a fact off its page."""
    page_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=uuid7(),
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:page",
                body_ref=f"pages/{page_id}",
                subject=subject,
                title="Page",
                stream=stream,
                tombstone=False,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id,
                page_uid=uuid7(),
                workspace_id=workspace_id,
                subject=subject,
                revision=revision,
                created_at=created_at,
            )
        )
    return page_id, revision


async def _seed_wiki_row(
    workspace_id: UUID,
    source_id: UUID,
    body: str,
    created_at: datetime,
    memory_kind: MemoryKind,
    vector: tuple[float, ...] | None = None,
    subject: str = SHARED_SUBJECT,
    confidence: int = 5,
) -> UUID:
    """One row of a wiki page at a chosen age, distilled from a source page of its own and carrying
    its already-derived chunk when a vector is given. Every pass that writes what a member reads
    reads a page-derived row at its live revision and nothing else, so a row of the page a test
    seeds is one page each. The ages fix the order a band is read in, and so the id the curation
    pass puts on each row."""
    page_id, revision = await _seed_wiki_source_page(workspace_id, source_id, created_at, subject)
    item_id = await _seed_copy(
        workspace_id,
        subject,
        FACT,
        body,
        created_at,
        created_from_page_id=page_id,
        created_from_page_revision=revision,
        source_id=source_id,
        memory_kind=memory_kind,
        confidence=confidence,
    )
    if vector is None:
        return item_id
    with ws(workspace_id):
        await DefaultIndex(transaction=workspace_tx).upsert(
            (
                Chunk(
                    "d-" + item_id.hex,
                    OWNER_KIND_MEMORY_ITEM,
                    str(item_id),
                    subject,
                    0,
                    body,
                    vector,
                ),
            )
        )
    return item_id


async def _seed_wiki_page(
    workspace_id: UUID, source_id: UUID, vector: tuple[float, ...] | None = None
) -> dict[str, UUID]:
    """One subject's whole wiki page: three Decisions, three Open work, the six History rows six
    source pages of `source_id` wrote about one pull request, and the paragraph standing over that
    band. Rows are seeded a minute apart, so the page the pass sends is decisions 1-3, open work 4-6
    and history 7-12, each band newest first."""
    start = datetime.now(UTC) - timedelta(hours=3)
    banded: tuple[tuple[MemoryKind, str], ...] = (
        *(("decision", body) for body in DECISION_ROWS),
        *(("task", body) for body in TASK_ROWS),
        *(("event", body) for body in DUPLICATE_ROWS),
    )
    seeded = {
        body: await _seed_wiki_row(
            workspace_id, source_id, body, start + timedelta(minutes=index), memory_kind, vector
        )
        for index, (memory_kind, body) in enumerate(banded)
    }
    seeded[SEEDED_HISTORY] = await _seed_copy(
        workspace_id,
        SHARED_SUBJECT,
        SECTION,
        SEEDED_HISTORY,
        start,
        memory_kind="event",
    )
    return seeded


async def _page_moved_on(page_id: UUID) -> None:
    """One page synced to a revision that landed no fact — a body too thin to send, an extraction
    carrying none, a stream this tier never reads. The feed moves the revision and the page indexer
    mirrors it, and nothing supersedes what the revision before derived, so those facts stay live at
    a revision their page has left: out of the listing, out of recall, and out of the page."""
    revision = await _rewrite_page(page_id, "sha256:moved")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(mem_page).values(revision=revision).where(mem_page.c.page_id == page_id)
        )


async def _every_page_moved_on(workspace_id: UUID) -> None:
    """Every page the workspace's facts were derived from, synced to a revision that landed no fact.
    What is left is a wiki of live rows a member can reach none of, which is the state each pass
    that writes what a member reads has to agree with."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.created_from_page_id)
                .where(
                    memory_item.c.workspace_id == workspace_id,
                    memory_item.c.created_from_page_id.is_not(None),
                )
                .distinct()
            )
        ).all()
    for row in rows:
        await _page_moved_on(row.created_from_page_id)


def _history_band(client: CurationClient) -> dict[str, object]:
    """The History band of the page the pass actually sent, which is where every row of one claim
    lands and so where a row that should never have been sent would show."""
    sent = json.loads(client.requests[0].messages[0].content)
    return next(band for band in sent["sections"] if band["section"] == "History")


async def _retirements(workspace_id: UUID) -> dict[str, datetime | None]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.body, memory_item.c.retired_at).where(
                    memory_item.c.workspace_id == workspace_id
                )
            )
        ).all()
    return {row.body: row.retired_at for row in rows}


async def _stamp_retired(item_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(retired_at=sa.func.now())
            .where(memory_item.c.id == item_id)
        )


async def _wiki_listing(workspace_id: UUID, reader: SourceReader) -> set[str]:
    """The rows the wiki page reads, through the `memory` object listing its homepage fetches, for
    the agent holding the feed its pages came from — the listing fences a page-derived row on that
    grant and on the revision its page carries now, so a row off either is a row a member cannot
    find."""
    reading = ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=reader.agent_id,
            seq=1,
            status="running",
            inbound="the wiki",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="auto"),
        spawn=None,  # type: ignore[arg-type]
        speaker_member_id=None,
        audience=SHARED_AUDIENCE,
        artifact_token_secret="",
        ext=context_for(memory_manifest.NAME, frozenset(), audience=SHARED_AUDIENCE),
    )
    with ws(workspace_id):
        listed = await MemoryObjects().list(
            reading, ObjectListQuery(supported_fields=MEMORY_OBJECT.list_fields)
        )
    return {str(row.fields["text"]) for row in listed.rows}


async def test_a_retired_row_stays_retired_when_the_next_derivation_commits_it_again(
    db: None,
) -> None:
    """The judgement this pass makes has to outlive the pass that produced the row. A page still
    synced re-derives the same body every rebuild and every revision, and `commit` addresses it to
    the same uuid5 row — so a retirement kept in `superseded_by`, which the upsert clears, would be
    undone by the next tick and the row would be back on the wiki by morning. `retired_at` is the
    one column the upsert leaves alone, and this is what proves it."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    page_id, revision = await _seed_wiki_source_page(workspace_id, source_id, datetime.now(UTC))
    await _seed_wiki_page(workspace_id, source_id)
    store = _store(workspace_id, vec((11, 1.0)))
    derived = MemoryWrite(
        subject=SHARED_SUBJECT,
        body="Acme Corp — 2482 is merged.",
        item_class=FACT,
        memory_kind="event",
        created_from_page_id=page_id,
        created_from_page_revision=revision,
        source_id=source_id,
    )
    with ws(workspace_id):
        landed = await store.commit(derived)
        await _age(landed, datetime.now(UTC))
        client = CurationClient(_curation(retire=(7,)))
        await _page_pass(workspace_id, client).run()

    sent = json.loads(client.requests[0].messages[0].content)
    history = next(band for band in sent["sections"] if band["section"] == "History")
    assert history["rows"][0] == {"id": 7, "body": derived.body}
    retired = (await _retirements(workspace_id))[derived.body]
    assert retired is not None

    with ws(workspace_id):
        again = await store.commit(derived)

    assert again == landed
    stayed = await _retirements(workspace_id)
    assert stayed[derived.body] == retired
    assert stayed[KEPT_DUPLICATE] is None
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.superseded_by, memory_item.c.embedding_digest).where(
                    memory_item.c.id == landed
                )
            )
        ).one()
    assert row.superseded_by is None
    assert row.embedding_digest is None


async def test_the_pass_retires_the_rows_several_source_pages_wrote_about_one_claim(
    db: None,
) -> None:
    """The tier's whole reason to exist: six rows written from six documents about one pull request
    are one claim, and only the reader holding the page at once can say so. The page it sends is
    every band with the paragraph standing over it and its rows under compact ids, the tool is
    compelled, and each way an entry fails — the contract unsatisfied, an id naming no row this page
    sent, no row named to carry the claim at all — drops that entry without costing the rest their
    judgement."""
    workspace_id = await _workspace()
    await _seed_wiki_page(workspace_id, await _seed_wiki_feed(workspace_id))
    arguments = json.dumps(
        {
            "retire": [
                {"id": 1, "reason": CURATION_REASON},
                {"id": 7, "reason": CURATION_REASON, "duplicate_of": 12},
                {"id": 8, "reason": CURATION_REASON, "duplicate_of": 12},
                {"id": 9, "reason": CURATION_REASON, "duplicate_of": 12},
                {"id": 10, "duplicate_of": 12},
                {"id": 11, "reason": CURATION_REASON, "duplicate_of": 12},
                {"id": 4096, "reason": "a row this page never sent", "duplicate_of": 12},
            ],
        }
    )
    client = CurationClient(arguments)
    with ws(workspace_id):
        await _page_pass(workspace_id, client).run()

    sent = json.loads(client.requests[0].messages[0].content)
    assert [band["section"] for band in sent["sections"]] == ["Decisions", "Open work", "History"]
    history = sent["sections"][2]
    assert history["summary"] == SEEDED_HISTORY
    assert [row["id"] for row in history["rows"]] == [7, 8, 9, 10, 11, 12]
    assert [row["body"] for row in history["rows"]] == list(reversed(DUPLICATE_ROWS))
    assert client.requests[0].tool_choice == PAGE_PASS_TOOL
    assert [tool.name for tool in client.requests[0].tools] == [PAGE_PASS_TOOL]
    assert client.requests[0].system.startswith(DELIVERY_REGISTER_BLOCK)

    retired = await _retirements(workspace_id)
    assert {body for body, stamp in retired.items() if stamp is not None} == {
        DUPLICATE_ROWS[5],
        DUPLICATE_ROWS[4],
        DUPLICATE_ROWS[3],
        DUPLICATE_ROWS[1],
    }
    assert retired[DUPLICATE_ROWS[2]] is None
    assert retired[KEPT_DUPLICATE] is None
    assert all(retired[body] is None for body in DECISION_ROWS + TASK_ROWS)


async def test_a_fact_its_page_has_moved_past_is_never_sent_to_the_pass(db: None) -> None:
    """A revision that lands no fact supersedes nothing, so the facts of the revision before it stay
    live bound to a revision their page has left. The listing and recall both drop such a row on the
    revision test, and this pass reads the same page a member does: sent, it would be judged against
    rows the wiki draws and retired against one of them, and `retired_at` is the one column nothing
    in this repository clears."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    await _seed_wiki_page(workspace_id, source_id)
    page_id, revision = await _seed_wiki_source_page(workspace_id, source_id, datetime.now(UTC))
    await _seed_copy(
        workspace_id,
        SHARED_SUBJECT,
        FACT,
        LEFT_BEHIND_ROW,
        datetime.now(UTC),
        created_from_page_id=page_id,
        created_from_page_revision=revision,
        source_id=source_id,
        memory_kind="event",
    )
    await _page_moved_on(page_id)
    client = CurationClient(_curation(retire=(7,)))
    with ws(workspace_id):
        await _page_pass(workspace_id, client).run()

    history = _history_band(client)
    assert [row["body"] for row in history["rows"]] == list(reversed(DUPLICATE_ROWS))
    assert [row["id"] for row in history["rows"]] == [7, 8, 9, 10, 11, 12]
    retired = await _retirements(workspace_id)
    assert retired[LEFT_BEHIND_ROW] is None
    assert retired[DUPLICATE_ROWS[5]] is not None


async def test_a_row_an_agent_wrote_is_never_sent_to_the_pass(db: None) -> None:
    """A row committed through `memory_update` carries no page at all: no source page restated it,
    and this tier judges a row only by the rows other source pages wrote about the same claim. What
    a member wrote is theirs to correct, and correcting it could not undo a retirement — `commit`
    never clears `retired_at`, so the member restating the claim lands the same row back under the
    stamp the pass left on it."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    await _seed_wiki_page(workspace_id, source_id)
    store = _store(workspace_id, vec((14, 1.0)))
    client = CurationClient(_curation(retire=(7,)))
    with ws(workspace_id):
        written = await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=AGENT_WRITTEN_ROW,
                item_class=FACT,
                memory_kind="event",
            )
        )
        await _age(written, datetime.now(UTC))
        await _page_pass(workspace_id, client).run()

    history = _history_band(client)
    assert [row["body"] for row in history["rows"]] == list(reversed(DUPLICATE_ROWS))
    retired = await _retirements(workspace_id)
    assert retired[AGENT_WRITTEN_ROW] is None
    assert retired[DUPLICATE_ROWS[5]] is not None


async def test_the_row_a_member_reads_is_never_retired_onto_one_they_cannot(db: None) -> None:
    """What reading past the fence costs a member. `admitted_curation` admits a keeper only from the
    ids the page sent, so a row its page has moved past, once sent, is a keeper this pass accepts
    — and the row it retires for restating it is the one a member could actually read. The claim
    would then be on neither: the retired row gone for good, the named keeper drawn nowhere."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    await _seed_wiki_page(workspace_id, source_id)
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    page_id, revision = await _seed_wiki_source_page(workspace_id, source_id, datetime.now(UTC))
    await _seed_copy(
        workspace_id,
        SHARED_SUBJECT,
        FACT,
        LEFT_BEHIND_ROW,
        datetime.now(UTC),
        created_from_page_id=page_id,
        created_from_page_revision=revision,
        source_id=source_id,
        memory_kind="event",
    )
    await _page_moved_on(page_id)
    onto_the_newest_row = json.dumps(
        {"retire": [{"id": 8, "reason": CURATION_REASON, "duplicate_of": 7}]}
    )
    with ws(workspace_id):
        await _page_pass(workspace_id, CurationClient(onto_the_newest_row)).run()

    listed = await _wiki_listing(workspace_id, reader)
    assert LEFT_BEHIND_ROW not in listed
    assert DUPLICATE_ROWS[5] in listed
    assert DUPLICATE_ROWS[4] not in listed
    retired = await _retirements(workspace_id)
    assert retired[LEFT_BEHIND_ROW] is None
    assert retired[DUPLICATE_ROWS[5]] is None
    assert retired[DUPLICATE_ROWS[4]] is not None


async def test_a_retired_row_leaves_the_wiki_listing_recall_and_the_bands_next_paragraph(
    db: None,
) -> None:
    """A retirement a reader cannot see is not a retirement. The row goes from the listing the wiki
    homepage fetches, from recall's read-back, and from the facts the section pass writes the band's
    next paragraph out of — the last one being the one the pass cannot do without, since a paragraph
    blind to it would describe the rows this pass just took off the page."""
    workspace_id = await _workspace()
    probe = vec((12, 1.0))
    source_id = await _seed_wiki_feed(workspace_id)
    await _seed_wiki_page(workspace_id, source_id, probe)
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    with ws(workspace_id):
        await _page_pass(workspace_id, CurationClient(_curation(retire=(7, 8)))).run()
        recalled = await _store(workspace_id, probe).recall(
            "pull request 2482",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=reader,
        )
        section = RecordingCompletionClient(REWRITTEN_HISTORY)
        await _section_writer(workspace_id, _section_model(section)).run()

    gone = {DUPLICATE_ROWS[5], DUPLICATE_ROWS[4]}
    assert len(recalled) >= 3
    assert gone.isdisjoint({item.body for item in recalled})
    assert KEPT_DUPLICATE in {item.body for item in recalled}

    listed = await _wiki_listing(workspace_id, reader)
    assert gone.isdisjoint(listed)
    assert KEPT_DUPLICATE in listed

    history = next(
        request
        for request in section.requests
        if json.loads(request.messages[0].content)["section"] == "History"
    )
    assert json.loads(history.messages[0].content)["facts"] == list(reversed(DUPLICATE_ROWS[:4]))


async def test_a_retired_rows_chunks_leave_the_index_on_the_next_job_tick(db: None) -> None:
    """The index is a reader too: chunks left published spend the candidate window recall reads
    before it fences anything. Stamping the retirement clears the row's digest, so the per-minute
    index job claims it, withdraws what it had published and settles it — the row leaves the due set
    rather than holding a claim slot forever."""
    workspace_id = await _workspace()
    probe = vec((13, 1.0))
    seeded = await _seed_wiki_page(workspace_id, await _seed_wiki_feed(workspace_id), probe)
    store = _store(workspace_id, probe)
    scope = IndexScope(OWNER_KIND_MEMORY_ITEM, str(seeded[DUPLICATE_ROWS[5]]))
    kept = IndexScope(OWNER_KIND_MEMORY_ITEM, str(seeded[KEPT_DUPLICATE]))
    with ws(workspace_id):
        assert await store.index.has_chunks(scope) is True
        await _page_pass(workspace_id, CurationClient(_curation(retire=(7,)))).run()
        await _index_memory(store, probe)
        assert await store.index.has_chunks(scope) is False
        assert await store.index.has_chunks(kept) is True

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.embedding_digest, memory_item.c.embedding_claimed_at).where(
                    memory_item.c.id == seeded[DUPLICATE_ROWS[5]]
                )
            )
        ).one()
    assert row.embedding_digest is not None
    assert row.embedding_claimed_at is None


async def test_a_page_asserted_onto_one_row_is_refused_and_stands(db: None) -> None:
    """The risk the bound answers for. Naming the row that keeps a claim is a claim the model makes
    and not one this code can check, so an answer is free to say every row on the page restates row
    1 — obeyed, that leaves a member one row where twelve stood. The pass destroys what a member
    reads, nightly and unattended, so it refuses instead, and refuses the whole page rather than the
    band it broke on: a judgement this wrong about one band is not a judgement to trust about
    another."""
    workspace_id = await _workspace()
    await _seed_wiki_page(workspace_id, await _seed_wiki_feed(workspace_id))
    client = CurationClient(_curation(retire=tuple(range(2, 13))))
    with ws(workspace_id):
        await _page_pass(workspace_id, client).run()

    assert all(stamp is None for stamp in (await _retirements(workspace_id)).values())
    assert [row.body for row in await _sections(workspace_id)] == [SEEDED_HISTORY]


async def test_a_page_refused_leaves_the_next_subjects_page_curated(db: None) -> None:
    """A refusal costs the subject its curation and nothing else. The pass reads every subject a
    workspace holds in one run, so a refusal that ended the run would leave every page after it
    uncurated — and the next night reads the same page to the same refusal, so those pages would
    never be curated at all. The two pages differ only in how many rows they hold, so the same nine
    retirements sit past the bar on one and under it on the other."""
    workspace_id = await _workspace()
    refused_feed = await _seed_wiki_feed(workspace_id)
    curated_feed = await _seed_wiki_feed(workspace_id)
    start = datetime.now(UTC) - timedelta(hours=3)
    for index in range(PAGE_PASS_MIN_ROWS):
        await _seed_wiki_row(
            workspace_id,
            refused_feed,
            f"a refused row {index}",
            start,
            "event",
            subject=REFUSED_SUBJECT,
        )
    for index in range(PAGE_PASS_MIN_ROWS + 2):
        await _seed_wiki_row(
            workspace_id,
            curated_feed,
            f"a curated row {index}",
            start,
            "event",
            subject=CURATED_SUBJECT,
        )
    with ws(workspace_id):
        await _page_pass(
            workspace_id, CurationClient(_curation(retire=tuple(range(1, 10)), keeper=10))
        ).run()

    stamped = {body for body, stamp in (await _retirements(workspace_id)).items() if stamp}
    assert not [body for body in stamped if body.startswith("a refused")]
    assert len([body for body in stamped if body.startswith("a curated")]) == 9


async def test_two_rows_retired_against_each_other_leave_the_wiki_one_of_them(db: None) -> None:
    """The whole chain the fix answers for: two rows carrying one claim, each recorded against the
    other, and the wiki a member reads afterwards. One stamp lands, and the claim is still on the
    page the homepage fetches — which is the only place a member could have found it."""
    workspace_id = await _workspace()
    source_id = await _seed_wiki_feed(workspace_id)
    await _seed_wiki_page(workspace_id, source_id)
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_id)
    mutual = json.dumps(
        {
            "retire": [
                {"id": 7, "reason": "Duplicates row 8.", "duplicate_of": 8},
                {"id": 8, "reason": "Duplicates row 7.", "duplicate_of": 7},
            ]
        }
    )
    with ws(workspace_id):
        await _page_pass(workspace_id, CurationClient(mutual)).run()

    retired = await _retirements(workspace_id)
    pair = (DUPLICATE_ROWS[5], DUPLICATE_ROWS[4])
    assert len([body for body in pair if retired[body] is not None]) == 1
    assert len(set(pair) & await _wiki_listing(workspace_id, reader)) == 1


def test_duplicates_that_point_only_at_each_other_leave_the_claim_a_row() -> None:
    """How a claim leaves a page with none of the rows that carried it: each of two duplicates
    retires by naming the other, and both stamps land. Naming a row is a promise that the row keeps
    the claim, so the promise is checked against what this same answer leaves standing."""
    admitted = admitted_curation(
        ((1, 2, 3, 4),),
        (
            RetiredRow(id=2, reason="Duplicates row 3.", duplicate_of=3),
            RetiredRow(id=3, reason="Duplicates row 2.", duplicate_of=2),
        ),
    )

    assert admitted.retiring == frozenset({3})
    assert admitted.refused == ""


def test_a_row_retired_against_a_row_this_page_never_sent_stays() -> None:
    """The other way the promise goes unkept: the row named carries the claim nowhere a member can
    read it, so the row that deferred to it is the last copy of that claim on the page."""
    admitted = admitted_curation(
        ((1, 2, 3, 4),), (RetiredRow(id=2, reason="Duplicates row 91.", duplicate_of=91),)
    )

    assert admitted.retiring == frozenset()


def test_a_retirement_naming_no_row_that_states_the_claim_is_dropped() -> None:
    """There is one reason to retire a row and it is that another row on this page states its claim,
    so an answer that names no such row has not made the case and the row stays. Each entry is
    judged alone: one that made no case does not cost the others theirs."""
    admitted = admitted_curation(
        ((1, 2, 3, 4),),
        (
            RetiredRow(id=2, reason="The routine motion of a tool."),
            RetiredRow(id=3, reason="Duplicates row 1.", duplicate_of=1),
        ),
    )

    assert admitted.retiring == frozenset({3})
    assert admitted.refused == ""


def test_a_band_may_lose_its_only_row() -> None:
    """A band of one is what a page holds where one source page wrote one row into it, and the row
    that states its claim can stand in another band — a page's bands are its memory kinds, and one
    claim reaches two of them often enough. A bound read as a fraction of one row admits nothing,
    which refused that page's whole curation every night it ran."""
    admitted = admitted_curation(
        ((1,), (2, 3, 4)), (RetiredRow(id=1, reason="Duplicates row 2.", duplicate_of=2),)
    )

    assert admitted.retiring == frozenset({1})
    assert admitted.refused == ""


async def test_the_page_pass_retires_and_writes_no_paragraph(db: None) -> None:
    """Retirement is the whole of what this pass does. The section and overview passes own every
    paragraph on the page, so a curation leaves the band's opening exactly as they wrote it — one
    writer per paragraph, on one schedule, and no second answer to who wrote what a member reads.
    The tool it is compelled to call offers nothing else to answer with."""
    workspace_id = await _workspace()
    seeded = await _seed_wiki_page(workspace_id, await _seed_wiki_feed(workspace_id))
    client = CurationClient(
        json.dumps(
            {
                "retire": [{"id": 7, "reason": CURATION_REASON, "duplicate_of": 12}],
                "rewrite": [{"section": "History", "text": REWRITTEN_HISTORY}],
            }
        )
    )
    with ws(workspace_id):
        await _page_pass(workspace_id, client).run()

    (schema,) = client.requests[0].tools
    assert set(schema.input_schema["properties"]) == {"retire"}
    sections = await _sections(workspace_id)
    assert [(row.body, row.superseded_by) for row in sections] == [(SEEDED_HISTORY, None)]
    assert [row.id for row in sections] == [seeded[SEEDED_HISTORY]]
    assert (await _retirements(workspace_id))[DUPLICATE_ROWS[5]] is not None


async def test_the_page_pass_without_a_model_writes_nothing(db: None) -> None:
    workspace_id = await _workspace()
    await _seed_wiki_page(workspace_id, await _seed_wiki_feed(workspace_id))
    with ws(workspace_id):
        await _page_pass(workspace_id, None).run()

    assert all(stamp is None for stamp in (await _retirements(workspace_id)).values())
    assert [row.body for row in await _sections(workspace_id)] == [SEEDED_HISTORY]


def _daily_time(schedule: str) -> tuple[int, int]:
    _second, minute, hour, *_rest = schedule.split()
    return (int(hour), int(minute))


async def test_page_pass_candidates_name_only_workspaces_holding_a_page_worth_reading_whole(
    db: None,
) -> None:
    """The nightly pass runs on the deploy's own model, so the candidate read carries its own floor:
    a subject holding at least PAGE_PASS_MIN_ROWS live rows. A workspace whose wiki is still a
    handful of rows, and one whose rows this pass has already retired, are never bound. The schedule
    stands clear of the other four and lands after the section pass, whose paragraphs it reads."""
    curatable, thin, curated = await _workspace(), await _workspace(), await _workspace()
    feeds = {holder: await _seed_wiki_feed(holder) for holder in (curatable, thin, curated)}
    start = datetime.now(UTC) - timedelta(hours=3)
    for index in range(PAGE_PASS_MIN_ROWS):
        await _seed_wiki_row(curatable, feeds[curatable], f"a page row {index}", start, "event")
        already = await _seed_wiki_row(
            curated, feeds[curated], f"a curated row {index}", start, "event"
        )
        await _stamp_retired(already)
        if index < PAGE_PASS_MIN_ROWS - 1:
            await _seed_wiki_row(thin, feeds[thin], f"a lone row {index}", start, "event")
    job = next(
        spec
        for spec in memory_manifest.manifest().jobs
        if spec.name == memory_manifest.PAGE_PASS_JOB
    )
    assert await job.candidates() == (curatable,)
    assert job.needs_deploy_model is True
    assert len(memory_manifest.PAGE_PASS_SCHEDULE.split()) == 6
    assert memory_manifest.PAGE_PASS_SCHEDULE not in {
        memory_manifest.MEMORY_INDEX_SCHEDULE,
        memory_manifest.CONSOLIDATE_SCHEDULE,
        memory_manifest.DEDUP_SCHEDULE,
        memory_manifest.SECTION_SCHEDULE,
    }
    assert _daily_time(memory_manifest.PAGE_PASS_SCHEDULE) < min(
        _daily_time(memory_manifest.SECTION_SCHEDULE),
        _daily_time(memory_manifest.OVERVIEW_SCHEDULE),
        _daily_time(memory_manifest.PROFILE_SCHEDULE),
    )
