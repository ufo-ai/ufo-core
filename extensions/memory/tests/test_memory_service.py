"""The memory extension's MemoryStore: commit derives nothing, recall fuses the index legs.

MemoryStore is the memory extension's domain workflow, driven here exactly as the extension's tools
and hook drive it — over the deploy index/embed backends and the workspace-scoped transaction core
threads onto the context. The embed client and the DefaultIndex are real dependencies, never the
asserted thing: every assertion reads the Recalled/SourceMatch values back."""

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
import ufo_ext_memory.store as memory_store
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import (
    FACT,
    RECALL_COSINE_FLOOR,
    RECALL_ITEM_MAX_CHARS,
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    PageIndexer,
    Recalled,
    UnindexedPageDrain,
    body_digest,
    decay_factor,
    drop_near_duplicates,
    enforce_type_diversity,
    fuse_recall,
    mem_page,
    memory_item,
)
from ufo_testsupport.index import StubEmbed, default_index, vec

from ufo.db import workspace_tx
from ufo.runtime.ext.context import PageState, context_for
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    Chunk,
    Hit,
    IndexScope,
    TextChunker,
)
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.pages import PageChange
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

PAGE_DIGEST = "sha256:page"
PAGE_REVISION = 1


def _at_cosine(target: float) -> tuple[float, ...]:
    """A unit vector whose cosine against `_at_cosine(1.0)` is `target`, so a test can sit a row an
    exact distance either side of the recall floor rather than hand-rolling axes."""
    return vec((0, target), (1, math.sqrt(max(0.0, 1.0 - target * target))))


class BrokenEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("embed provider unreachable")


class CountingEmbed:
    """Counts embed calls: re-embedding an unchanged body is metered model spend the idempotent
    chunk upsert hides, so the call count is the only witness of what a re-commit costs."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector
        self.calls = 0

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple(self._vector for _ in texts)


class ReclassifyingPage:
    def __init__(self, page_id: UUID, before: str, after: str) -> None:
        self.page_id = page_id
        self.before = before
        self.after = after
        self.calls = 0

    async def __call__(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]:
        self.calls += 1
        return {
            self.page_id: PageState(
                subject=self.before if self.calls == 1 else self.after,
                revision=PAGE_REVISION,
                digest=PAGE_DIGEST,
                title="Q3 pricing rollout",
                stream="pull_requests",
                indexed=True,
                as_of="2026-03-01",
                backend="github",
                source_id=UUID(int=1),
                connection_id=UUID(int=2),
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        }


@dataclass
class RebindingIndex:
    backend: DefaultIndex
    callback: Callable[[], Awaitable[None]]
    rebound: bool = False

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        await self.backend.upsert(chunks)

    async def delete(self, scope: IndexScope) -> None:
        if scope.owner_kind == OWNER_KIND_MEMORY_ITEM and not self.rebound:
            self.rebound = True
            await self.callback()
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


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_page(
    workspace_id: UUID, page_id: UUID, source_id: UUID, subject: str, indexed: bool = True
) -> None:
    """One page under its own source, under its own workspace-shared connection — the authority a
    reader is granted, so one source is one grantable feed in these tests."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    connection_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="test",
                account_id=connection_id.hex,
                host="",
                owner_member_id=None,
                shared=True,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest=PAGE_DIGEST,
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject=subject,
                tombstone=False,
                indexed=indexed,
                created_at=now,
                updated_at=now,
            )
        )


def _store(embed: object, workspace_id: UUID, *, sources: bool = True) -> MemoryStore:
    ext = context_for("memory", frozenset())

    async def readable(page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]:
        return await ext.page_states(page_ids)

    async def readable_ids(reader: SourceReader) -> frozenset[UUID]:
        if not sources:
            return frozenset()
        async with workspace_tx() as connection:
            return frozenset(
                (
                    await connection.execute(
                        sa.select(tables.source.c.uid).where(
                            tables.source.c.workspace_id == workspace_id,
                        )
                    )
                ).scalars()
            )

    return MemoryStore(
        index=DefaultIndex(transaction=workspace_tx, workspace=lambda: workspace_id),
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=readable,
        readable_source_ids=readable_ids,
    )


def _reader(subjects: frozenset[str]) -> SourceReader:
    return SourceReader(agent_id=uuid4(), requesting_member_id=None, subjects=subjects)


async def _granted_reader(workspace_id: UUID, subject: str, *source_ids: UUID) -> SourceReader:
    """An agent holding a connector grant on the connection behind each named source: a
    page-derived fact reaches recall only through a reader that may read the feed it came from."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="reader",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for source_id in source_ids:
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=await _connection_of(connection, source_id),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return SourceReader(agent_id=agent_id, requesting_member_id=None, subjects=frozenset({subject}))


async def _connection_of(connection: AsyncConnection, source_id: UUID) -> UUID:
    return (
        await connection.execute(
            sa.select(tables.source.c.connection_id).where(tables.source.c.uid == source_id)
        )
    ).scalar_one()


async def _seed_item(
    workspace_id: UUID,
    subject: str,
    body: str,
    vector: tuple[float, ...],
    created_at: datetime | None = None,
    as_of: datetime | None = None,
    created_from_page_id: UUID | None = None,
) -> UUID:
    """Insert a memory_item and its one already-derived chunk directly, so recall can be exercised
    without the derivation job in these unit tests."""
    item_id = uuid4()
    async with workspace_tx() as connection:
        page_authority = (
            (None, None)
            if created_from_page_id is None
            else (
                await connection.execute(
                    sa.select(tables.page.c.revision, tables.source.c.uid)
                    .select_from(
                        tables.page.join(
                            tables.source, tables.page.c.source_uid == tables.source.c.uid
                        )
                    )
                    .where(tables.page.c.uid == created_from_page_id)
                )
            ).one()
        )
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject=subject,
                body=body,
                body_digest=body_digest(body),
                item_class=FACT,
                source_ref=None,
                created_from_page_uid=created_from_page_id,
                created_from_page_revision=page_authority[0],
                source_uid=page_authority[1],
                as_of=as_of,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created_at if created_at is not None else sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    chunk = Chunk(
        "d-" + item_id.hex,
        OWNER_KIND_MEMORY_ITEM,
        str(item_id),
        subject,
        0,
        body,
        vector,
    )
    with ws(workspace_id):
        await default_index().upsert((chunk,))
    return item_id


async def _seed_page_chunk(
    workspace_id: UUID,
    subject: str,
    body: str,
    vector: tuple[float, ...],
    indexed: bool = True,
) -> UUID:
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, subject, indexed=indexed)
    chunk = Chunk(
        "p-" + page_id.hex,
        OWNER_KIND_PAGE,
        str(page_id),
        subject,
        0,
        body,
        vector,
    )
    with ws(workspace_id):
        await default_index().upsert((chunk,))
    async with workspace_tx() as connection:
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_uid=page_id,
                workspace_id=workspace_id,
                subject=subject,
                revision=revision,
                created_at=sa.func.now(),
            )
        )
    return page_id


async def test_commit_persists_item_and_derives_no_chunk(db: None) -> None:
    workspace_id = await _workspace()
    await _store(StubEmbed(vec((0, 1.0))), workspace_id).commit(
        MemoryWrite(subject=SHARED_SUBJECT, body="the sky is blue today", item_class=FACT)
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.body,
                    memory_item.c.item_class,
                    memory_item.c.embedding_digest,
                )
            )
        ).one()
        chunks = (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()
    assert row.body == "the sky is blue today"
    assert row.item_class == FACT
    assert row.embedding_digest is None
    assert chunks == 0


async def test_recommitting_a_fact_updates_in_place_not_duplicated(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((6, 1.0))
    embed = StubEmbed(probe)
    store = _store(embed, workspace_id)
    await store.commit(
        MemoryWrite(subject=SHARED_SUBJECT, body="the vault code is 4821", confidence=3)
    )
    await store.commit(
        MemoryWrite(subject=SHARED_SUBJECT, body="the vault code is 4821", confidence=9)
    )

    async with workspace_tx() as connection:
        confidences = (
            (
                await connection.execute(
                    sa.select(memory_item.c.confidence).where(
                        memory_item.c.subject == SHARED_SUBJECT
                    )
                )
            )
            .scalars()
            .all()
        )
    assert confidences == [9]

    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    hits = await store.recall(
        "vault code",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert len(hits) == 1
    assert "4821" in hits[0].body


async def test_a_partial_page_origin_is_refused(db: None) -> None:
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    for revision, source in ((None, source_id), (PAGE_REVISION, None)):
        with pytest.raises(IntegrityError, match="memory_item_page_source"):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(memory_item).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        subject=SHARED_SUBJECT,
                        body="the vault code is 4821",
                        body_digest=body_digest("the vault code is 4821"),
                        memory_kind="fact",
                        confidence=5,
                        item_class=FACT,
                        source_ref=None,
                        created_from_page_uid=page_id,
                        created_from_page_revision=revision,
                        source_uid=source,
                        as_of=None,
                        embedding_digest=None,
                        superseded_by=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )


def test_fuse_recall_drops_a_vector_only_row_no_nearer_than_the_floor() -> None:
    """A nearest-neighbour search always answers: ask it about a random string and it returns its
    closest chunks, however far away they are."""
    far = (
        Hit("f", OWNER_KIND_MEMORY_ITEM, "F", SHARED_SUBJECT, 0, "far", RECALL_COSINE_FLOOR / 2),
    )
    assert fuse_recall((), far, (), 10) == ()


def test_fuse_recall_keeps_a_lexically_matched_row_however_far_its_embedding() -> None:
    """The floor governs the vector leg alone."""
    near_none = 0.0
    lexical = (Hit("l", OWNER_KIND_MEMORY_ITEM, "L", SHARED_SUBJECT, 0, "priced", 4.0),)
    vector = (Hit("l", OWNER_KIND_MEMORY_ITEM, "L", SHARED_SUBJECT, 0, "priced", near_none),)
    assert [fused.owner_id for fused in fuse_recall(lexical, vector, (), 10)] == ["L"]

    tail_only = (Hit("tail:T", OWNER_KIND_MEMORY_ITEM, "T", SHARED_SUBJECT, 0, "fresh", 2.0),)
    assert [fused.owner_id for fused in fuse_recall((), (), tail_only, 10)] == ["T"]


def test_fuse_recall_keeps_a_vector_only_row_at_the_floor() -> None:
    """A query worded nothing like the memory it wants — no shared word to match — still recalls
    it, so the floor admits a row that reaches it."""
    near = (Hit("n", OWNER_KIND_MEMORY_ITEM, "N", SHARED_SUBJECT, 0, "near", RECALL_COSINE_FLOOR),)
    assert [fused.owner_id for fused in fuse_recall((), near, (), 10)] == ["N"]


async def test_recall_admits_a_wordless_row_at_the_floor_and_drops_one_under_it(db: None) -> None:
    workspace_id = await _workspace()
    near = await _seed_item(
        workspace_id, SHARED_SUBJECT, "budget review notes", _at_cosine(RECALL_COSINE_FLOOR + 0.02)
    )
    await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "hiring plan headcount",
        _at_cosine(RECALL_COSINE_FLOOR - 0.02),
    )

    recalled = await _store(StubEmbed(_at_cosine(1.0)), workspace_id).recall(
        "zzqrfl mmbtwv",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )

    assert [item.memory_id for item in recalled] == [near]


async def test_recall_keeps_a_worded_row_the_floor_would_have_dropped(db: None) -> None:
    """The exemption, end to end: a row the lexical leg matched holds words the member typed,
    which no meaningless string can fake, so it stands at a cosine far under the floor."""
    workspace_id = await _workspace()
    worded = await _seed_item(
        workspace_id, SHARED_SUBJECT, "the refund window is thirty days", vec((1, 1.0))
    )

    recalled = await _store(StubEmbed(vec((0, 1.0))), workspace_id).recall(
        "refund window",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )

    assert [item.memory_id for item in recalled] == [worded]


async def test_recall_keeps_an_un_embedded_row_the_tail_leg_matched(db: None) -> None:
    """A fact committed a moment ago has no chunk yet, so it carries no cosine at all and reaches
    recall only through the lexical tail leg."""
    workspace_id = await _workspace()
    store = _store(StubEmbed(_at_cosine(0.0)), workspace_id)
    await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body="the refund window is thirty days"))

    recalled = await store.recall(
        "refund window",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )

    assert [item.body for item in recalled] == ["the refund window is thirty days"]


async def test_recall_blend_promotes_the_semantically_closer_fact(db: None) -> None:
    workspace_id = await _workspace()
    when = datetime(2025, 1, 1, tzinfo=UTC)
    close = await _seed_item(
        workspace_id, SHARED_SUBJECT, "budget review notes near", vec((0, 1.0)), created_at=when
    )
    far = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "budget review notes far",
        vec((0, 0.3), (1, 0.95)),
        created_at=when,
    )
    recalled = await _store(StubEmbed(vec((0, 1.0))), workspace_id).recall(
        "budget review notes",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert [item.memory_id for item in recalled] == [close, far]


async def test_recall_keeps_a_row_worded_by_part_of_a_whole_sentence(db: None) -> None:
    workspace_id = await _workspace()
    worded = await _seed_item(
        workspace_id, SHARED_SUBJECT, "the refund window is thirty days", vec((1, 1.0))
    )

    recalled = await _store(StubEmbed(vec((0, 1.0))), workspace_id).recall(
        "ok, file an issue for the refund window confusing people",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )

    assert [item.memory_id for item in recalled] == [worded]


async def test_recall_skips_a_superseded_item(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((2, 1.0))
    stale = await _seed_item(workspace_id, SHARED_SUBJECT, "the release ship date is friday", probe)
    head = await _seed_item(workspace_id, member_subject(uuid4()), "the ship date moved", probe)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(superseded_by=head, updated_at=sa.func.now())
            .where(memory_item.c.id == stale)
        )
    empty = await _store(StubEmbed(probe), workspace_id).recall(
        "release ship date",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert empty == ()


async def test_the_quoted_head_answers_to_the_readers_source_reach(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((7, 1.0))
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    dated = await _seed_item(workspace_id, SHARED_SUBJECT, "the ledger sync runs hourly", probe)
    head = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "the ledger sync runs every four hours",
        probe,
        created_from_page_id=page_id,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(overtaken_by=head, updated_at=sa.func.now())
            .where(memory_item.c.id == dated)
        )

    with ws(workspace_id):
        granted = await _store(StubEmbed(probe), workspace_id).recall(
            "ledger sync",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
        withheld = await _store(StubEmbed(probe), workspace_id, sources=False).recall(
            "ledger sync",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )

    assert [item.head for item in granted if item.memory_id == dated] == [
        "the ledger sync runs every four hours"
    ]
    assert [item.head for item in withheld if item.memory_id == dated] == [None]


async def test_the_listing_quotes_the_head_the_way_search_does(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((8, 1.0))
    dated = await _seed_item(workspace_id, SHARED_SUBJECT, "the ledger sync runs hourly", probe)
    head = await _seed_item(
        workspace_id, SHARED_SUBJECT, "the ledger sync runs every four hours", probe
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item).values(overtaken_by=head).where(memory_item.c.id == dated)
        )
    with ws(workspace_id):
        service = memory_manifest.MemorySearchService(
            ctx=context_for(
                "memory",
                frozenset(),
                index=default_index(),
                embed=StubEmbed(probe),
                cloud_client=True,
            )
        )
        page = await service.list_recent(frozenset({SHARED_SUBJECT}), 10)
    texts = {str(match.ref.name): match.text for match in page.rows if match.ref is not None}
    assert (
        texts[str(dated)]
        == "the ledger sync runs hourly (now: the ledger sync runs every four hours)"
    )
    assert texts[str(head)] == "the ledger sync runs every four hours"


async def test_recall_degrades_to_lexical_when_embed_fails(db: None) -> None:
    workspace_id = await _workspace()
    await _seed_item(workspace_id, SHARED_SUBJECT, "the mascot is named zoltar", vec((3, 1.0)))
    hits = await _store(BrokenEmbed(), workspace_id).recall(
        "zoltar",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert len(hits) == 1
    assert "zoltar" in hits[0].body


async def test_connector_grants_filter_before_recall_ranking(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = uuid4()
    strong = vec((4, 1.0))
    weak = vec((5, 1.0))
    ungranted_page = await _seed_page_chunk(
        workspace_id,
        SHARED_SUBJECT,
        "quarterly forecast " * 20 + "ungranted",
        strong,
    )
    granted_page = await _seed_page_chunk(
        workspace_id,
        SHARED_SUBJECT,
        "quarterly forecast granted",
        weak,
    )
    ungranted_fact = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "quarterly forecast " * 20 + "ungranted fact",
        strong,
        created_from_page_id=ungranted_page,
    )
    granted_fact = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "quarterly forecast granted fact",
        weak,
        created_from_page_id=granted_page,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="research",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        granted_source = (
            await connection.execute(
                sa.select(tables.source.c.uid)
                .select_from(
                    tables.page.join(tables.source, tables.page.c.source_uid == tables.source.c.uid)
                )
                .where(tables.page.c.uid == granted_page)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=await _connection_of(connection, granted_source),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    ext = context_for("memory", frozenset())
    store = MemoryStore(
        index=default_index(),
        embed=StubEmbed(strong),
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )
    subjects = frozenset({SHARED_SUBJECT})
    reader = SourceReader(agent_id=agent_id, requesting_member_id=None, subjects=subjects)
    with ws(workspace_id):
        pages = await store.search_sources(
            "quarterly forecast",
            subjects,
            1,
            source_reader=reader,
        )
        facts = await store.recall(
            "quarterly forecast",
            subjects,
            1,
            source_reader=reader,
        )
    assert [page.page_id for page in pages] == [granted_page]
    assert [fact.memory_id for fact in facts] == [granted_fact]
    assert ungranted_page not in {page.page_id for page in pages}
    assert ungranted_fact not in {fact.memory_id for fact in facts}


def test_enforce_type_diversity_caps_a_class_and_backfills() -> None:
    facts = tuple(
        Recalled(uuid4(), "shared", "fact", f"f{i}", None, float(10 - i)) for i in range(5)
    )
    episodic = Recalled(uuid4(), "shared", "episodic", "e", None, 0.5)
    kept = enforce_type_diversity((*facts, episodic), 4)
    assert len(kept) == 4
    assert sum(1 for row in kept if row.item_class == "episodic") == 1
    assert sum(1 for row in kept if row.item_class == "fact") == 3


def test_drop_near_duplicates_keeps_the_highest_ranked_copy() -> None:
    a = Recalled(
        uuid4(), "shared", "fact", "the deploy has no code_review profile, only coding", None, 0.9
    )
    b = Recalled(
        uuid4(),
        "shared",
        "fact",
        "re-confirmed: the deploy has no code_review profile, only coding",
        None,
        0.8,
    )
    c = Recalled(
        uuid4(), "shared", "fact", "invoices are net-30 on the first business day", None, 0.7
    )

    assert drop_near_duplicates((a, b, c), 8) == (a, c)


def test_drop_near_duplicates_keeps_distinct_bodies_in_order() -> None:
    items = tuple(
        Recalled(uuid4(), "shared", "fact", f"fact number {n} about system {n}", None, 1 - n / 10)
        for n in range(4)
    )

    assert drop_near_duplicates(items, 8) == items


def test_drop_near_duplicates_shingles_nothing_past_the_items_it_was_asked_to_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard runs on every member turn over a candidate pool far larger than the slots recall
    can fill, and its cost is one trigram set per body it examines."""
    shingled: list[str] = []
    shingles = memory_store._body_shingles

    def counting(body: str) -> frozenset[str]:
        shingled.append(body)
        return shingles(body)

    monkeypatch.setattr(memory_store, "_body_shingles", counting)
    items = tuple(
        Recalled(uuid4(), "shared", "fact", f"fact number {n} about system {n}", None, 1 - n / 10)
        for n in range(6)
    )

    assert drop_near_duplicates(items, 2) == items[:2]
    assert shingled == [item.body for item in items[:2]]


def test_drop_near_duplicates_collapses_on_a_matching_injectable_prefix() -> None:
    shared_prefix = " ".join(f"clause{i}" for i in range(400))
    assert len(shared_prefix) > RECALL_ITEM_MAX_CHARS
    tail_a = " ".join(f"onlyinA{i}" for i in range(400))
    tail_b = " ".join(f"onlyinB{i}" for i in range(400))
    higher = Recalled(uuid4(), "shared", "fact", f"{shared_prefix} {tail_a}", None, 0.9)
    lower = Recalled(uuid4(), "shared", "fact", f"{shared_prefix} {tail_b}", None, 0.8)

    assert drop_near_duplicates((higher, lower), 8) == (higher,)


async def test_recall_drops_the_lower_ranked_near_duplicate_and_backfills_the_freed_slot(
    db: None,
) -> None:
    workspace_id = await _workspace()
    when = datetime(2025, 1, 1, tzinfo=UTC)
    strong = vec((6, 1.0))
    original = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "the deploy has no code_review profile, only coding",
        strong,
        created_at=when,
    )
    duplicate = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "re-confirmed: the deploy has no code_review profile, only coding",
        vec((6, 0.85), (7, 0.4)),
        created_at=when,
    )
    distinct = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "the deploy retry queue backs off exponentially",
        vec((6, 0.5), (8, 0.7)),
        created_at=when,
    )
    recalled = await _store(StubEmbed(strong), workspace_id).recall(
        "deploy",
        frozenset({SHARED_SUBJECT}),
        2,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert [item.memory_id for item in recalled] == [original, distinct]
    assert duplicate not in [item.memory_id for item in recalled]


async def test_mem_page_carries_workspace_id(db: None) -> None:
    """The migration end state: `mem_page.workspace_id` is NOT NULL with a CASCADE FK to workspace,
    on whichever dialect the migration just ran against."""
    async with workspace_tx() as connection:
        columns = await connection.run_sync(lambda sync: sa.inspect(sync).get_columns("mem_page"))
        foreign_keys = await connection.run_sync(
            lambda sync: sa.inspect(sync).get_foreign_keys("mem_page")
        )
    workspace_column = next(column for column in columns if column["name"] == "workspace_id")
    assert workspace_column["nullable"] is False
    workspace_fk = next(fk for fk in foreign_keys if fk["referred_table"] == "workspace")
    assert workspace_fk["constrained_columns"] == ["workspace_id"]
    assert workspace_fk["options"]["ondelete"].upper() == "CASCADE"


async def test_page_indexer_writes_the_contexts_workspace_id(db: None) -> None:
    """The `page_change` writer populates `workspace_id` from the context it holds: the mirror row
    it upserts for a page change carries the indexer's workspace, not a NULL."""
    workspace_id = await _workspace()
    probe = vec((5, 1.0))
    change = PageChange(
        page_id=uuid4(),
        source_id=uuid4(),
        connection_id=uuid4(),
        provider="folder",
        subject=SHARED_SUBJECT,
        stream="notes",
        title="Merger timing",
        body="the merger closes in the third quarter",
        digest=PAGE_DIGEST,
        revision=PAGE_REVISION,
        tombstone=False,
        indexed=True,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        changed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await _seed_page(workspace_id, change.page_id, change.source_id, SHARED_SUBJECT)
    with ws(workspace_id):
        await PageIndexer(
            index=default_index(),
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply((change,))

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(mem_page.c.workspace_id).where(mem_page.c.page_uid == change.page_id)
            )
        ).one()
    assert row.workspace_id == workspace_id


async def test_page_index_write_is_deleted_when_the_subject_changes_during_embed(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id = uuid4()
    source_id = uuid4()
    index = default_index()
    now = datetime(2025, 1, 1, tzinfo=UTC)
    with ws(workspace_id):
        await PageIndexer(
            index=index,
            embed=StubEmbed(vec((7, 1.0))),
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=ReclassifyingPage(
                page_id,
                SHARED_SUBJECT,
                member_subject(uuid4()),
            ),
        ).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    connection_id=uuid4(),
                    provider="folder",
                    subject=SHARED_SUBJECT,
                    stream="notes",
                    title="Stale page",
                    body="the stale page codename is polaris",
                    digest="sha256:stale",
                    revision=PAGE_REVISION,
                    tombstone=False,
                    indexed=True,
                    created_at=now,
                    as_of=now,
                    changed_at=now,
                ),
            )
        )
        assert (
            await index.lexical(
                "page codename",
                frozenset({SHARED_SUBJECT}),
                OWNER_KIND_PAGE,
                10,
            )
            == ()
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(mem_page.c.page_uid).where(mem_page.c.page_uid == page_id)
            )
        ).one_or_none()
    assert row is None


async def test_stale_private_payload_is_never_indexed_after_a_shared_sanitized_edit(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id, source_id, member_id = uuid4(), uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(digest="sha256:sanitized")
            .where(tables.page.c.uid == page_id)
        )
    now = datetime(2025, 1, 2, tzinfo=UTC)
    index = default_index()
    indexer = PageIndexer(
        index=index,
        embed=StubEmbed(vec((8, 1.0))),
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    stale = PageChange(
        page_id=page_id,
        source_id=source_id,
        connection_id=uuid4(),
        provider="folder",
        subject=member_subject(member_id),
        stream="notes",
        title="Private plan",
        body="the private acquisition codename is polaris",
        digest="sha256:private",
        revision=PAGE_REVISION,
        tombstone=False,
        indexed=True,
        created_at=now,
        as_of=now,
        changed_at=now,
    )
    sanitized = replace(
        stale,
        subject=SHARED_SUBJECT,
        body="the acquisition plan has been redacted",
        digest="sha256:sanitized",
        revision=PAGE_REVISION + 1,
    )
    with ws(workspace_id):
        await indexer.apply((stale,))
        assert (
            await index.lexical("polaris", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10) == ()
        )
        await indexer.apply((sanitized,))
        assert (
            await index.lexical("polaris", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10) == ()
        )
        assert (
            len(await index.lexical("redacted", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10))
            == 1
        )


async def test_same_subject_redaction_hides_stale_facts_and_no_page_pass_removes_them(
    db: None,
) -> None:
    """Redacting a page fences its facts out of recall the moment the revision moves — and that
    is the whole guarantee the read path needs."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    probe = vec((8, 1.0))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(StubEmbed(probe), workspace_id)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the retired acquisition codename is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(digest="sha256:redacted")
                .where(tables.page.c.uid == page_id)
            )
        assert (
            await store.recall(
                "acquisition codename",
                frozenset({SHARED_SUBJECT}),
                10,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
        now = datetime(2025, 1, 2, tzinfo=UTC)
        await PageIndexer(
            index=store.index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    connection_id=uuid4(),
                    provider="folder",
                    subject=SHARED_SUBJECT,
                    stream="notes",
                    title="Redacted plan",
                    body="the acquisition plan has been redacted",
                    digest="sha256:redacted",
                    revision=PAGE_REVISION + 1,
                    tombstone=False,
                    indexed=True,
                    created_at=now,
                    as_of=now,
                    changed_at=now,
                ),
            )
        )
        assert (
            len(
                await store.index.lexical(
                    "polaris",
                    frozenset({SHARED_SUBJECT}),
                    OWNER_KIND_MEMORY_ITEM,
                    10,
                )
            )
            == 1
        )
        assert (
            await store.recall(
                "acquisition codename",
                frozenset({SHARED_SUBJECT}),
                10,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(memory_item)
                .where(memory_item.c.created_from_page_uid == page_id)
            )
        ).scalar_one() == 1


async def test_retirement_requeues_a_fact_rebound_while_its_index_is_deleted(
    db: None,
) -> None:
    """Retirement deletes the superseded row and then its index scope."""
    workspace_id, page_id, source_id = await _workspace(), uuid4(), uuid4()
    body = "the acquisition plan has been redacted"
    probe = vec((8, 1.0))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(StubEmbed(probe), workspace_id)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body=body,
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    new_digest = "sha256:redacted"
    new_revision = PAGE_REVISION + 1
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(digest=new_digest).where(tables.page.c.uid == page_id)
        )

    async def recommit() -> None:
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=new_revision,
                source_id=source_id,
            )
        )

    index = RebindingIndex(store.index, recommit)
    replacement = uuid4()
    with ws(workspace_id):
        await replace(store, index=index).supersede_page_facts(page_id, frozenset({replacement}))
        await MemoryIndexer(
            index=index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        assert (
            len(
                await index.lexical(
                    "acquisition plan", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 10
                )
            )
            == 1
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.created_from_page_revision,
                    memory_item.c.embedding_digest,
                ).where(memory_item.c.created_from_page_uid == page_id)
            )
        ).one()
    assert row.created_from_page_revision == new_revision
    assert row.embedding_digest is not None


async def test_a_revision_rebind_with_an_unchanged_body_is_not_re_embedded(db: None) -> None:
    workspace_id, page_id, source_id = await _workspace(), uuid4(), uuid4()
    body = "the quarterly revenue target is four million"
    embed = CountingEmbed(vec((8, 1.0)))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(embed, workspace_id)

    def _run() -> Awaitable[None]:
        return MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()

    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body=body,
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await _run()
    assert embed.calls == 1

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(digest="sha256:rev2").where(tables.page.c.uid == page_id)
        )
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body=body,
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION + 1,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        embeds_before = embed.calls
        await _run()
        assert embed.calls == embeds_before
        assert (
            len(
                await store.recall(
                    "quarterly revenue",
                    frozenset({SHARED_SUBJECT}),
                    10,
                    source_reader=_reader(frozenset({SHARED_SUBJECT})),
                )
            )
            == 1
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.embedding_digest, memory_item.c.created_from_page_revision
                ).where(memory_item.c.created_from_page_uid == page_id)
            )
        ).one()
    assert row.embedding_digest is not None
    assert row.created_from_page_revision == PAGE_REVISION + 1


async def test_a_narrowed_pages_wider_fact_is_never_published_and_is_removed(
    db: None,
) -> None:
    """A page that narrows to one member takes the fact derived under the wider subject with it: no
    read serves that row and no derivation keyed on the page's new subject could ever land on it."""
    workspace_id = await _workspace()
    member_id, page_id, source_id = uuid4(), uuid4(), uuid4()
    subject = member_subject(member_id)
    probe = vec((8, 1.0))
    embed = StubEmbed(probe)
    store = _store(embed, workspace_id)
    stale_body = "the shared disclosure token is helios"
    kept_body = "the private continuity token is selene"
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body=stale_body,
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page).values(subject=subject).where(tables.page.c.uid == page_id)
            )
        await store.commit(
            MemoryWrite(
                subject=subject,
                body=kept_body,
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION + 1,
                source_id=source_id,
            )
        )
        await MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        await PageIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    connection_id=uuid4(),
                    provider="folder",
                    subject=subject,
                    stream="messages",
                    title="Private mailbox",
                    body="the mailbox page is now private",
                    digest=PAGE_DIGEST,
                    revision=PAGE_REVISION + 1,
                    tombstone=False,
                    indexed=True,
                    created_at=datetime(2025, 1, 1, tzinfo=UTC),
                    as_of=datetime(2025, 1, 1, tzinfo=UTC),
                    changed_at=datetime(2025, 1, 2, tzinfo=UTC),
                ),
            )
        )

        assert (
            await store.index.lexical(
                "disclosure token", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 10
            )
            == ()
        )
        assert (
            await store.recall(
                "disclosure token",
                frozenset({SHARED_SUBJECT}),
                10,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
        assert (
            len(
                await store.index.lexical(
                    "continuity token", frozenset({subject}), OWNER_KIND_MEMORY_ITEM, 10
                )
            )
            == 1
        )

    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(memory_item.c.subject, memory_item.c.body).where(
                        memory_item.c.created_from_page_uid == page_id
                    )
                )
            )
            .mappings()
            .all()
        )
    assert {row["body"]: row["subject"] for row in rows} == {kept_body: subject}


async def test_recommitting_an_unchanged_body_at_the_same_binding_never_re_embeds(
    db: None,
) -> None:
    workspace_id, page_id, source_id = await _workspace(), uuid4(), uuid4()
    body = "the acme renewal closes on september 30"
    embed = CountingEmbed(vec((12, 1.0)))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(embed, workspace_id)
    indexer = MemoryIndexer(
        index=store.index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )

    async def poll() -> None:
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION,
                source_id=source_id,
            )
        )
        with ws(workspace_id):
            await indexer.run()

    await poll()
    after_first = embed.calls
    async with workspace_tx() as connection:
        first = (
            await connection.execute(
                sa.select(memory_item.c.embedding_digest).where(
                    memory_item.c.created_from_page_uid == page_id
                )
            )
        ).scalar_one()
    await poll()
    async with workspace_tx() as connection:
        second = (
            await connection.execute(
                sa.select(
                    memory_item.c.embedding_digest, memory_item.c.created_from_page_revision
                ).where(memory_item.c.created_from_page_uid == page_id)
            )
        ).one()
    assert after_first == 1
    assert first is not None
    assert second == (first, PAGE_REVISION)
    assert embed.calls == after_first


async def test_page_tombstone_drops_the_pages_own_chunks_and_mirror_only(db: None) -> None:
    """A tombstone retires what the page indexer owns — the page's chunks and its mirror row —
    and deletes nothing else."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    probe = vec((9, 1.0))
    embed = StubEmbed(probe)
    store = _store(embed, workspace_id)
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the retired source fact is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    live = PageChange(
        page_id=page_id,
        source_id=source_id,
        connection_id=uuid4(),
        provider="folder",
        subject=SHARED_SUBJECT,
        stream="notes",
        title="Retired page",
        body="the retired source page names polaris",
        digest=PAGE_DIGEST,
        revision=PAGE_REVISION,
        tombstone=False,
        indexed=True,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        changed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    indexer = PageIndexer(
        index=store.index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        await indexer.apply((live,))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page).values(tombstone=True).where(tables.page.c.uid == page_id)
            )
        now = datetime(2025, 1, 2, tzinfo=UTC)
        await indexer.apply(
            (
                replace(
                    live,
                    body="",
                    revision=PAGE_REVISION + 1,
                    tombstone=True,
                    as_of=now,
                    changed_at=now,
                ),
            )
        )
        assert (
            await store.index.lexical(
                "retired source page", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10
            )
            == ()
        )
        assert (
            len(
                await store.index.lexical(
                    "retired source fact",
                    frozenset({SHARED_SUBJECT}),
                    OWNER_KIND_MEMORY_ITEM,
                    10,
                )
            )
            == 1
        )
        assert (
            await store.recall(
                "retired source fact",
                frozenset({SHARED_SUBJECT}),
                10,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
    async with workspace_tx() as connection:
        mirror = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(mem_page)
                .where(mem_page.c.page_uid == page_id)
            )
        ).scalar_one()
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(memory_item)
                .where(memory_item.c.created_from_page_uid == page_id)
            )
        ).scalar_one()
    assert mirror == 0
    assert count == 1


class _UnreachedEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("a page that does not reach memory is never embedded")


async def _mirrors(workspace_id: UUID) -> set[UUID]:
    async with workspace_tx() as connection:
        return set(
            (
                await connection.execute(
                    sa.select(mem_page.c.page_uid).where(mem_page.c.workspace_id == workspace_id)
                )
            ).scalars()
        )


async def _chunked(index: DefaultIndex, page_ids: tuple[UUID, ...]) -> dict[UUID, bool]:
    return {
        page_id: await index.has_chunks(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        for page_id in page_ids
    }


async def test_a_change_on_a_stream_that_does_not_reach_memory_drops_the_page_and_embeds_nothing(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    index = default_index()
    live = PageChange(
        page_id=page_id,
        source_id=source_id,
        connection_id=uuid4(),
        provider="folder",
        subject=SHARED_SUBJECT,
        stream="workflow_runs",
        title="ci #41",
        body="the nightly billing build ran for twelve minutes",
        digest=PAGE_DIGEST,
        revision=PAGE_REVISION,
        tombstone=False,
        indexed=True,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        changed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    def indexer(embed: object) -> PageIndexer:
        return PageIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        )

    with ws(workspace_id):
        await indexer(StubEmbed(vec((3, 1.0)))).apply((live,))
        assert await _chunked(index, (page_id,)) == {page_id: True}
        assert await _mirrors(workspace_id) == {page_id}
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page).values(indexed=False).where(tables.page.c.uid == page_id)
            )
        await indexer(_UnreachedEmbed()).apply((replace(live, indexed=False),))
        assert await _chunked(index, (page_id,)) == {page_id: False}
    assert await _mirrors(workspace_id) == set()


async def _mark_for_drain(workspace_id: UUID) -> None:
    with ws(workspace_id):
        await context_for("memory", frozenset()).store.put(
            memory_manifest.UNINDEXED_DRAIN_KEY, {"after": None}
        )


async def _marker(workspace_id: UUID) -> object:
    with ws(workspace_id):
        return await context_for("memory", frozenset()).store.get(
            memory_manifest.UNINDEXED_DRAIN_KEY
        )


def _drain(
    workspace_id: UUID, index: DefaultIndex, batch: int, transaction: object = workspace_tx
) -> UnindexedPageDrain:
    ext = context_for("memory", frozenset())
    return UnindexedPageDrain(
        index=index,
        transaction=transaction,
        store=ext.store,
        page_states=ext.page_states,
        workspace_id=workspace_id,
        marker_key=memory_manifest.UNINDEXED_DRAIN_KEY,
        batch=batch,
    )


async def test_the_drain_job_fires_on_marked_workspaces_and_removes_only_unindexed_pages(
    db: None,
) -> None:
    index = default_index()
    marked, unmarked = await _workspace(), await _workspace()
    kept = await _seed_page_chunk(
        marked, SHARED_SUBJECT, "the runway is eleven months", vec((1, 1))
    )
    gone = tuple(
        [
            await _seed_page_chunk(
                marked, SHARED_SUBJECT, f"ci run {n}", vec((2, 1)), indexed=False
            )
            for n in range(3)
        ]
    )
    elsewhere = await _seed_page_chunk(
        unmarked, SHARED_SUBJECT, "ci run", vec((2, 1)), indexed=False
    )
    await _mark_for_drain(marked)

    manifest = memory_manifest.manifest()
    job = next(j for j in manifest.jobs if j.name == memory_manifest.DRAIN_JOB)
    assert set(await job.candidates()) == {marked}
    runner = JobRunner(
        bindings=bindings_from((manifest,), ()),
        manifests=(manifest,),
        index=index,
        embed=StubEmbed(vec((0, 1.0))),
    )
    key = f"{memory_manifest.NAME}:{memory_manifest.DRAIN_JOB}"
    for workspace_id in await runner.candidates(key):
        await runner.fire(key, workspace_id)

    with ws(marked):
        assert await _chunked(index, (kept, *gone)) == {kept: True, **dict.fromkeys(gone, False)}
    with ws(unmarked):
        assert await _chunked(index, (elsewhere,)) == {elsewhere: True}
    assert await _mirrors(marked) == {kept}
    assert await _mirrors(unmarked) == {elsewhere}
    assert await _marker(marked) is None
    assert await job.candidates() == ()


async def test_the_drain_walks_in_batches_from_the_cursor_its_marker_carries(db: None) -> None:
    """A full batch advances the marker's cursor to the last mirror walked and keeps the marker; the
    next pass resumes past it and, finding a short batch, ends the walk and deletes the marker."""
    index = default_index()
    workspace_id = await _workspace()
    pages = sorted(
        [
            await _seed_page_chunk(
                workspace_id, SHARED_SUBJECT, f"ci run {n}", vec((2, 1)), indexed=False
            )
            for n in range(3)
        ]
    )
    await _mark_for_drain(workspace_id)

    with ws(workspace_id):
        first = await _drain(workspace_id, index, batch=2).run()
        assert (first, await _marker(workspace_id)) == (2, {"after": str(pages[1])})
        assert await _chunked(index, tuple(pages)) == {
            pages[0]: False,
            pages[1]: False,
            pages[2]: True,
        }
        second = await _drain(workspace_id, index, batch=2).run()
        assert (second, await _marker(workspace_id)) == (1, None)
        assert await _chunked(index, tuple(pages)) == dict.fromkeys(pages, False)
        assert await _drain(workspace_id, index, batch=2).run() == 0
    assert await _mirrors(workspace_id) == set()


class _RefusingIndex(DefaultIndex):
    refused: str = ""

    async def delete(self, scope: IndexScope) -> None:
        if scope.owner_id == self.refused:
            raise RuntimeError("index unavailable")
        await super().delete(scope)


async def test_a_page_whose_scope_the_index_refuses_keeps_its_mirror_while_the_batch_drains(
    db: None,
) -> None:
    workspace_id = await _workspace()
    pages = sorted(
        [
            await _seed_page_chunk(
                workspace_id, SHARED_SUBJECT, f"ci run {n}", vec((2, 1)), indexed=False
            )
            for n in range(3)
        ]
    )
    index = _RefusingIndex(transaction=workspace_tx, workspace=lambda: ws_current().workspace_id)
    index.refused = str(pages[1])
    await _mark_for_drain(workspace_id)
    opened = 0

    def counting_tx() -> object:
        nonlocal opened
        opened += 1
        return workspace_tx()

    with ws(workspace_id):
        with pytest.raises(RuntimeError, match="index unavailable"):
            await _drain(workspace_id, index, batch=10, transaction=counting_tx).run()
        assert await _chunked(index, tuple(pages)) == {
            pages[0]: False,
            pages[1]: True,
            pages[2]: False,
        }
        assert await _marker(workspace_id) == {"after": None}
    assert await _mirrors(workspace_id) == {pages[1]}
    assert opened == 2


def test_decay_factor_weights_recency_kind_and_confidence() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fresh = Recalled(
        uuid4(),
        "shared",
        "fact",
        "b",
        None,
        1.0,
        memory_kind="fact",
        confidence=10,
        created_at=now,
    )
    assert decay_factor(fresh, now) == 1.0
    old = replace(fresh, as_of=datetime(2020, 1, 1, tzinfo=UTC))
    assert 0.0 < decay_factor(old, now) < decay_factor(fresh, now)
    aged = replace(fresh, as_of=datetime(2025, 10, 1, tzinfo=UTC))
    assert decay_factor(replace(aged, memory_kind="task"), now) < decay_factor(aged, now)
    assert decay_factor(replace(aged, item_class="episodic"), now) == 1.0
    assert decay_factor(replace(aged, item_class="semantic"), now) == decay_factor(aged, now) < 1.0


async def _live_page(page_id: UUID) -> PageState:
    return (await context_for("memory", frozenset()).page_states((page_id,)))[page_id]


async def _move_page_subject(page_id: UUID, subject: str) -> PageState:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(subject=subject).where(tables.page.c.uid == page_id)
        )
        await connection.execute(
            sa.update(tables.page)
            .values(revision=tables.page.c.revision + 1)
            .where(tables.page.c.uid == page_id, tables.page.c.subject == subject)
        )
    return await _live_page(page_id)


async def test_a_page_that_moved_subject_leaves_no_fact_behind(db: None) -> None:
    """A page carries its facts wherever its disclosure goes: no read serves the row the move
    strands, and no derivation keyed on the page's new subject could ever land on it."""
    workspace_id = await _workspace()
    page_id, source_id, member_id = uuid4(), uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    index = default_index()
    embed = StubEmbed(vec((9, 1.0)))
    body = "the acquisition codename is polaris"
    with ws(workspace_id):
        landed = await _store(embed, workspace_id).commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                item_class=FACT,
                created_from_page_id=page_id,
                created_from_page_revision=(await _live_page(page_id)).revision,
                source_id=source_id,
            )
        )
        await MemoryIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        assert await index.has_chunks(IndexScope(OWNER_KIND_MEMORY_ITEM, str(landed)))

        moved = await _move_page_subject(page_id, member_subject(member_id))
        now = datetime(2025, 1, 1, tzinfo=UTC)
        await PageIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    connection_id=uuid4(),
                    provider="folder",
                    subject=moved.subject,
                    stream="notes",
                    title="Page",
                    body=body,
                    digest=moved.digest,
                    revision=moved.revision,
                    tombstone=False,
                    indexed=True,
                    created_at=now,
                    as_of=now,
                    changed_at=now,
                ),
            )
        )
        assert not await index.has_chunks(IndexScope(OWNER_KIND_MEMORY_ITEM, str(landed)))

    async with workspace_tx() as connection:
        left = (
            await connection.execute(
                sa.select(memory_item.c.id).where(memory_item.c.created_from_page_uid == page_id)
            )
        ).all()
    assert left == []


async def test_a_stranded_page_row_repoints_what_pointed_at_it(db: None) -> None:
    """A pointer aimed at a row the page's move deletes is cleared and its row made due, so the
    statement a correction hid never comes back live with chunks nobody withdrew."""
    workspace_id = await _workspace()
    page_id, source_id, member_id = uuid4(), uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    index = default_index()
    embed = StubEmbed(vec((11, 1.0)))
    body = "the ledger sync runs every four hours"
    with ws(workspace_id):
        store = _store(embed, workspace_id)
        hidden = await store.commit(
            MemoryWrite(subject=SHARED_SUBJECT, body="the ledger sync runs hourly")
        )
        landed = await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                item_class=FACT,
                created_from_page_id=page_id,
                created_from_page_revision=(await _live_page(page_id)).revision,
                source_id=source_id,
            )
        )
        assert await store.supersede(hidden, landed, frozenset({SHARED_SUBJECT}))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(embedding_digest="sha256:settled")
                .where(memory_item.c.id == hidden)
            )

        moved = await _move_page_subject(page_id, member_subject(member_id))
        now = datetime(2025, 1, 1, tzinfo=UTC)
        await PageIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    connection_id=uuid4(),
                    provider="folder",
                    subject=moved.subject,
                    stream="notes",
                    title="Page",
                    body=body,
                    digest=moved.digest,
                    revision=moved.revision,
                    tombstone=False,
                    indexed=True,
                    created_at=now,
                    as_of=now,
                    changed_at=now,
                ),
            )
        )

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.superseded_by,
                    memory_item.c.overtaken_by,
                    memory_item.c.embedding_digest,
                ).where(memory_item.c.id == hidden)
            )
        ).one()
    assert row.superseded_by is None
    assert row.overtaken_by is None
    assert row.embedding_digest is None


async def test_the_memory_listing_drops_a_row_its_page_has_left(db: None) -> None:
    """The browse half fences on the live page as recall and `object_get` do, before any job has
    caught up; a row no page backs is a member's own and stands."""
    workspace_id = await _workspace()
    page_id, source_id, member_id = uuid4(), uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    embed = StubEmbed(vec((9, 1.0)))
    with ws(workspace_id):
        store = _store(embed, workspace_id)
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="the acquisition codename is polaris",
                item_class=FACT,
                created_from_page_id=page_id,
                created_from_page_revision=(await _live_page(page_id)).revision,
                source_id=source_id,
            )
        )
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="the office moves in March",
                item_class=FACT,
            )
        )
        service = memory_manifest.MemorySearchService(
            ctx=context_for(
                "memory",
                frozenset(),
                index=default_index(),
                embed=embed,
                cloud_client=True,
            )
        )
        listed = await service.list_recent(frozenset({SHARED_SUBJECT}), 10)
        assert {match.text for match in listed.rows} == {
            "the acquisition codename is polaris",
            "the office moves in March",
        }

        await _move_page_subject(page_id, member_subject(member_id))
        fenced = await service.list_recent(frozenset({SHARED_SUBJECT}), 10)
    assert {match.text for match in fenced.rows} == {"the office moves in March"}


async def test_every_read_of_a_pages_rows_goes_through_an_index(db: None) -> None:
    """Settling and retiring both filter a page's rows once per replayed change: unindexed, each
    read every memory_item the workspace holds — 41ms at 140k rows against 0.04ms through this."""
    async with workspace_tx() as connection:
        indexes = await connection.run_sync(
            lambda sync: sa.inspect(sync).get_indexes("memory_item")
        )
    page_index = next(index for index in indexes if index["name"] == "memory_item_page")
    assert page_index["column_names"] == ["workspace_id", "created_from_page_uid"]
