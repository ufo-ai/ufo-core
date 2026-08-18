"""The memory extension's MemoryStore: commit derives nothing, recall fuses the index legs.

MemoryStore is the memory extension's domain workflow, driven here exactly as the extension's tools
and hook drive it — over the deploy index/embed backends and the workspace-scoped transaction core
threads onto the context. The embed client and the DefaultIndex are real dependencies, never the
asserted thing: every assertion reads the Recalled/SourceMatch values back."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.store as memory_store
from sqlalchemy.exc import IntegrityError
from ufo_ext_embed_openai import EMBED_DIM
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
    decay_factor,
    drop_near_duplicates,
    enforce_type_diversity,
    fuse_hits,
    fuse_recall,
    inventory,
    mem_page,
    memory_item,
    memory_source,
    recall_subjects,
)

from ufo.db import workspace_tx
from ufo.ext.context import PageState, SourceReader, context_for
from ufo.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    Chunk,
    Hit,
    IndexScope,
    TextChunker,
)
from ufo.schema import tables
from ufo.sdk.audience import conversation_audience
from ufo.sources.sync import PageChange
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import ws

PAGE_DIGEST = "sha256:page"
PAGE_REVISION = 1


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """Deterministic stand-in EmbedClient: a dependency of recall's vector leg, never the asserted
    thing — the tests assert the Recalled items recall returns via public surfaces."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


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
                body_ref=f"pages/{self.page_id}",
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


async def _seed_page(workspace_id: UUID, page_id: UUID, source_id: UUID, subject: str) -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                subject=subject,
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest=PAGE_DIGEST,
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject=subject,
                tombstone=False,
                created_at=now,
                updated_at=now,
            )
        )


def _store(embed: object, workspace_id: UUID) -> MemoryStore:
    ext = context_for("memory", frozenset())

    async def readable(page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]:
        return await ext.page_states(page_ids)

    async def readable_ids(reader: SourceReader) -> frozenset[UUID]:
        async with workspace_tx() as connection:
            return frozenset(
                (
                    await connection.execute(
                        sa.select(tables.source.c.id).where(
                            tables.source.c.workspace_id == workspace_id,
                            tables.source.c.removed_at.is_(None),
                        )
                    )
                ).scalars()
            )

    return MemoryStore(
        index=DefaultIndex(transaction=workspace_tx),
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
    """An agent holding the grant for each named source: a page-derived fact reaches recall only
    through a reader granted the feed it came from."""
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
                sa.insert(tables.source_grant).values(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    agent_id=agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return SourceReader(agent_id=agent_id, requesting_member_id=None, subjects=frozenset({subject}))


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
                    sa.select(tables.page.c.revision, tables.page.c.source_id).where(
                        tables.page.c.id == created_from_page_id
                    )
                )
            ).one()
        )
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject=subject,
                body=body,
                item_class=FACT,
                source_ref=None,
                created_from_page_id=created_from_page_id,
                created_from_page_revision=page_authority[0],
                source_id=page_authority[1],
                as_of=as_of,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created_at if created_at is not None else sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if created_from_page_id is not None:
            await connection.execute(
                sa.insert(memory_source).values(
                    workspace_id=workspace_id,
                    memory_item_id=item_id,
                    source_id=page_authority[1],
                    page_id=created_from_page_id,
                    revision=page_authority[0],
                    created_at=sa.func.now(),
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
        await DefaultIndex(transaction=workspace_tx).upsert((chunk,))
    return item_id


async def _seed_page_chunk(
    workspace_id: UUID, subject: str, body: str, vector: tuple[float, ...]
) -> UUID:
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, subject)
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
        await DefaultIndex(transaction=workspace_tx).upsert((chunk,))
    async with workspace_tx() as connection:
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id,
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
    """A fact committed twice is content-addressed to one row: the id derives from
    (workspace, subject, item_class, body), so the re-commit upserts its decay inputs in place and
    one recallable item survives — not two hits for the same claim. The re-commit's confidence
    wins, and the identical body leaves the derived chunk untouched."""
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


async def test_same_fact_from_two_sources_is_one_row_granted_by_either(db: None) -> None:
    """The same fact learned from two feeds is one row, not two: its content id ignores the source,
    so the second commit upserts the first row rather than minting a rival. Both feeds are kept as
    additive `memory_source` links, so a reader granted either source reaches the one fact, and the
    row's own `source_id` tracks the last derivation for staleness."""
    workspace_id = await _workspace()
    page_a, page_b = uuid4(), uuid4()
    source_a, source_b = uuid4(), uuid4()
    await _seed_page(workspace_id, page_a, source_a, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_b, source_b, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    for page_id, source_id in ((page_a, source_a), (page_b, source_b)):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="the launch date is June 12",
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION,
                source_id=source_id,
            )
        )

    async with workspace_tx() as connection:
        items = (
            await connection.execute(
                sa.select(
                    memory_item.c.id,
                    memory_item.c.created_from_page_id,
                    memory_item.c.source_id,
                )
            )
        ).all()
        links = (
            await connection.execute(
                sa.select(memory_source.c.source_id, memory_source.c.page_id).where(
                    memory_source.c.memory_item_id == items[0].id
                )
            )
        ).all()
    assert len(items) == 1
    assert items[0].created_from_page_id == page_b
    assert items[0].source_id == source_b
    assert set(links) == {(source_a, page_a), (source_b, page_b)}

    (provenance,) = await inventory(workspace_tx, workspace_id)
    assert set(provenance.source_ids) == {source_a, source_b}


async def test_deleting_one_source_keeps_a_fact_its_other_source_still_provides(db: None) -> None:
    """A fact from two feeds is one row a reader reaches through either, so deleting one feed's page
    must keep it while the other feed is live. Retiring the gone page drops only its link and, since
    it was the row's primary origin, re-points the primary to the surviving feed and clears the
    digest so the index job re-checks the new binding — the fact stays recallable through it."""
    workspace_id = await _workspace()
    page_a, page_b = uuid4(), uuid4()
    source_a, source_b = uuid4(), uuid4()
    await _seed_page(workspace_id, page_a, source_a, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_b, source_b, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    for page_id, source_id in ((page_a, source_a), (page_b, source_b)):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="the launch date is June 12",
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION,
                source_id=source_id,
            )
        )
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.page).where(tables.page.c.id == page_b))
    with ws(workspace_id):
        await store.supersede_page_facts(page_b, None)

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.id,
                    memory_item.c.created_from_page_id,
                    memory_item.c.source_id,
                    memory_item.c.embedding_digest,
                )
            )
        ).one()
        links = (
            await connection.execute(sa.select(memory_source.c.source_id, memory_source.c.page_id))
        ).all()
    assert row.created_from_page_id == page_a
    assert row.source_id == source_a
    assert row.embedding_digest is None
    assert set(links) == {(source_a, page_a)}

    reader = _reader(frozenset({SHARED_SUBJECT}))
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=StubEmbed(vec((0, 1.0))),
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        recalled = await store.recall(
            "launch date", frozenset({SHARED_SUBJECT}), 10, source_reader=reader
        )
    assert [item.memory_id for item in recalled] == [row.id]


async def test_deleting_the_last_source_erases_the_fact(db: None) -> None:
    """A single-source fact loses its only link when its page is deleted, so the row is removed and
    its index scope with it — nothing else links it."""
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the retired mailbox code is helios",
            created_from_page_id=page_id,
            created_from_page_revision=PAGE_REVISION,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=StubEmbed(vec((0, 1.0))),
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.page).where(tables.page.c.id == page_id))
    with ws(workspace_id):
        await store.supersede_page_facts(page_id, None)

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(memory_item))
        ).scalar_one()
        chunks = (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()
    assert rows == 0
    assert chunks == 0


async def test_retiring_a_primary_repoints_to_a_live_feed_over_an_older_stale_one(
    db: None,
) -> None:
    """Re-pointing a retired primary prefers a surviving link the page mirror shows live at that
    link's revision over a merely older link whose page it does not — so a fact bound to a deleted
    feed lands on a feed a reader can actually recall it through, not the oldest one to hand."""
    workspace_id = await _workspace()
    stale_page, live_page, primary_page = uuid4(), uuid4(), uuid4()
    stale_src, live_src, primary_src = uuid4(), uuid4(), uuid4()
    await _seed_page(workspace_id, stale_page, stale_src, SHARED_SUBJECT)
    await _seed_page(workspace_id, live_page, live_src, SHARED_SUBJECT)
    await _seed_page(workspace_id, primary_page, primary_src, SHARED_SUBJECT)
    async with workspace_tx() as connection:
        revisions = {
            row.id: row.revision
            for row in (
                await connection.execute(
                    sa.select(tables.page.c.id, tables.page.c.revision).where(
                        tables.page.c.id.in_((stale_page, live_page, primary_page))
                    )
                )
            ).all()
        }
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    body = "the badge reader logs every swipe"
    for page_id, source_id in (
        (stale_page, stale_src),
        (live_page, live_src),
        (primary_page, primary_src),
    ):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=revisions[page_id],
                source_id=source_id,
            )
        )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_source)
            .where(memory_source.c.page_id == stale_page)
            .values(created_at=datetime(2024, 1, 1, tzinfo=UTC))
        )
        await connection.execute(
            sa.update(memory_source)
            .where(memory_source.c.page_id == live_page)
            .values(created_at=datetime(2025, 6, 1, tzinfo=UTC))
        )
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=live_page,
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                revision=revisions[live_page],
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        )
    with ws(workspace_id):
        await store.supersede_page_facts(primary_page, None)

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.created_from_page_id, memory_item.c.source_id)
            )
        ).one()
    assert row.created_from_page_id == live_page
    assert row.source_id == live_src


async def test_two_pages_of_one_source_each_keep_the_fact_they_share(db: None) -> None:
    """A fact one source derives from two of its pages carries a link per page, not one per source,
    so deleting one page drops only that page's link and re-points the primary to the page that
    still holds it — a per-source link would have collapsed both pages onto one row and lost the
    fact with the first delete."""
    workspace_id = await _workspace()
    source_id = uuid4()
    page_1, page_2 = uuid4(), uuid4()
    await _seed_page(workspace_id, page_1, source_id, SHARED_SUBJECT)
    now = datetime(2025, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_2,
                workspace_id=workspace_id,
                source_id=source_id,
                digest=PAGE_DIGEST,
                body_ref=f"pages/{page_2}",
                stream="notes",
                title="Page",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=now,
                updated_at=now,
            )
        )
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    body = "the vault combination is 4-19-77"
    for page_id in (page_2, page_1):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION,
                source_id=source_id,
            )
        )
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.page).where(tables.page.c.id == page_1))
    with ws(workspace_id):
        await store.supersede_page_facts(page_1, None)

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.created_from_page_id, memory_item.c.source_id)
            )
        ).one()
        links = (
            await connection.execute(sa.select(memory_source.c.source_id, memory_source.c.page_id))
        ).all()
    assert row.created_from_page_id == page_2
    assert row.source_id == source_id
    assert set(links) == {(source_id, page_2)}


async def test_retiring_a_non_primary_page_leaves_the_primary_binding_untouched(db: None) -> None:
    """Retiring a feed the fact only links to — not the one its row binds to — drops that link and
    stops: a surviving link still backs the primary, so the row keeps its page, source, and settled
    digest. Only losing the primary's own link forces the re-point that clears the digest, so this
    path must leave it alone."""
    workspace_id = await _workspace()
    page_secondary, page_primary = uuid4(), uuid4()
    source_secondary, source_primary = uuid4(), uuid4()
    await _seed_page(workspace_id, page_secondary, source_secondary, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_primary, source_primary, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    for page_id, source_id in (
        (page_secondary, source_secondary),
        (page_primary, source_primary),
    ):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="the wire transfer clears friday",
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
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    async with workspace_tx() as connection:
        settled = (await connection.execute(sa.select(memory_item.c.embedding_digest))).scalar_one()
    assert settled is not None
    async with workspace_tx() as connection:
        await connection.execute(sa.delete(tables.page).where(tables.page.c.id == page_secondary))
    with ws(workspace_id):
        await store.supersede_page_facts(page_secondary, None)

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.created_from_page_id,
                    memory_item.c.source_id,
                    memory_item.c.embedding_digest,
                )
            )
        ).one()
        links = (
            await connection.execute(sa.select(memory_source.c.source_id, memory_source.c.page_id))
        ).all()
    assert row.created_from_page_id == page_primary
    assert row.source_id == source_primary
    assert row.embedding_digest == settled
    assert set(links) == {(source_primary, page_primary)}


async def test_a_revision_bump_retires_only_the_stale_source_link(db: None) -> None:
    """A page whose new revision no longer derives a fact retires that page's stale link to it,
    never a second feed's link to the same fact. The shared fact survives on the other feed, and
    because the retired link was its primary origin the primary re-points to the surviving live
    feed; the page's new-revision fact keeps its own fresh link."""
    workspace_id = await _workspace()
    page_a, page_b = uuid4(), uuid4()
    source_a, source_b = uuid4(), uuid4()
    await _seed_page(workspace_id, page_a, source_a, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_b, source_b, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    shared = "the auditor is booked for thursday"
    for page_id, source_id in ((page_b, source_b), (page_a, source_a)):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=shared,
                created_from_page_id=page_id,
                created_from_page_revision=PAGE_REVISION,
                source_id=source_id,
            )
        )
    next_revision = PAGE_REVISION + 1
    await store.commit(
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="the ledger closes on friday",
            created_from_page_id=page_a,
            created_from_page_revision=next_revision,
            source_id=source_a,
        )
    )
    with ws(workspace_id):
        await store.supersede_page_facts(page_a, next_revision)

    async with workspace_tx() as connection:
        shared_row = (
            await connection.execute(
                sa.select(
                    memory_item.c.created_from_page_id,
                    memory_item.c.created_from_page_revision,
                    memory_item.c.source_id,
                ).where(memory_item.c.body == shared)
            )
        ).one()
        links = {
            (row.body, row.source_id, row.page_id, row.revision)
            for row in (
                await connection.execute(
                    sa.select(
                        memory_item.c.body,
                        memory_source.c.source_id,
                        memory_source.c.page_id,
                        memory_source.c.revision,
                    ).join(memory_source, memory_source.c.memory_item_id == memory_item.c.id)
                )
            ).all()
        }
    assert (shared_row.created_from_page_id, shared_row.created_from_page_revision) == (
        page_b,
        PAGE_REVISION,
    )
    assert shared_row.source_id == source_b
    assert links == {
        (shared, source_b, page_b, PAGE_REVISION),
        ("the ledger closes on friday", source_a, page_a, next_revision),
    }


async def test_concurrent_retirements_over_one_fact_leave_no_dangling_primary(db: None) -> None:
    """Two feeds' pages retiring at once must not race the shared fact's re-point. The affected rows
    are locked in id order, so one retirement fully commits before the other reads survivors; both
    pages gone, the fact is deleted outright. Without the lock the second reads the link the first
    is deleting, re-points the primary onto it, and leaves a row bound to a page no reader can
    resolve — so this drives many shared facts through the race at once and asserts none dangles."""
    workspace_id = await _workspace()
    page_a, page_b = uuid4(), uuid4()
    source_a, source_b = uuid4(), uuid4()
    await _seed_page(workspace_id, page_a, source_a, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_b, source_b, SHARED_SUBJECT)
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    for index in range(24):
        for page_id, source_id in ((page_a, source_a), (page_b, source_b)):
            await store.commit(
                MemoryWrite(
                    subject=SHARED_SUBJECT,
                    body=f"fact {index} the merger closes in march",
                    created_from_page_id=page_id,
                    created_from_page_revision=PAGE_REVISION,
                    source_id=source_id,
                )
            )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.page).where(tables.page.c.id.in_((page_a, page_b)))
        )
    with ws(workspace_id):
        await asyncio.gather(
            store.supersede_page_facts(page_a, None),
            store.supersede_page_facts(page_b, None),
        )
    async with workspace_tx() as connection:
        primaries = (
            await connection.execute(
                sa.select(memory_item.c.id, memory_item.c.created_from_page_id)
            )
        ).all()
        live_links = {
            (link.memory_item_id, link.page_id)
            for link in (
                await connection.execute(
                    sa.select(memory_source.c.memory_item_id, memory_source.c.page_id)
                )
            ).all()
        }
    assert primaries == []
    assert live_links == set()
    """A page-derived row carries page, revision, and source together or not at all — the database
    refuses a partial origin, so the page indexer's staleness sweep never has to ask whether a
    derivation is missing one: inequality against the live page state selects every stale fact."""
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
                        memory_kind="fact",
                        confidence=5,
                        item_class=FACT,
                        source_ref=None,
                        created_from_page_id=page_id,
                        created_from_page_revision=revision,
                        source_id=source,
                        as_of=None,
                        embedding_digest=None,
                        superseded_by=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )


def test_fuse_recall_blends_cosine_to_break_a_rrf_tie() -> None:
    """Two rows tied on fused rank (each leads one leg) split by the cosine term: pure RRF keeps the
    lexical-leader first, the recall blend promotes the row nearer in embedding space."""
    lexical = (
        Hit("a", OWNER_KIND_MEMORY_ITEM, "A", SHARED_SUBJECT, 0, "alpha", 5.0),
        Hit("b", OWNER_KIND_MEMORY_ITEM, "B", SHARED_SUBJECT, 0, "beta", 3.0),
    )
    vector = (
        Hit("b", OWNER_KIND_MEMORY_ITEM, "B", SHARED_SUBJECT, 0, "beta", 0.9),
        Hit("a", OWNER_KIND_MEMORY_ITEM, "A", SHARED_SUBJECT, 0, "alpha", 0.4),
    )
    assert [fused.owner_id for fused in fuse_hits(lexical, vector, 10)] == ["A", "B"]
    assert [fused.owner_id for fused in fuse_recall(lexical, vector, (), 10)] == ["B", "A"]


def test_fuse_hits_holds_a_source_page_to_the_same_floor() -> None:
    """Source-page search answers the same portal box and the same tool as recall, so a page the
    lexical leg never matched earns its place the same way. Without this a meaningless query still
    returns pages: the vector leg always answers, and pure fused rank is relative to whatever came
    back."""
    far = (Hit("f", OWNER_KIND_PAGE, "F", SHARED_SUBJECT, 0, "far", RECALL_COSINE_FLOOR / 2),)
    assert fuse_hits((), far, 10) == ()

    near = (Hit("n", OWNER_KIND_PAGE, "N", SHARED_SUBJECT, 0, "near", RECALL_COSINE_FLOOR),)
    assert [fused.owner_id for fused in fuse_hits((), near, 10)] == ["N"]

    # A page the lexical leg matched holds the member's own words, so no cosine bars it.
    worded = (Hit("w", OWNER_KIND_PAGE, "W", SHARED_SUBJECT, 0, "worded", 3.0),)
    barely = (Hit("w", OWNER_KIND_PAGE, "W", SHARED_SUBJECT, 0, "worded", 0.1),)
    assert [fused.owner_id for fused in fuse_hits(worded, barely, 10)] == ["W"]


def test_fuse_recall_drops_a_vector_only_row_no_nearer_than_the_floor() -> None:
    """A nearest-neighbour search always answers: ask it about a random string and it returns its
    closest chunks, however far away they are. A row the lexical legs never matched has to earn its
    place on closeness alone, so one under the floor is dropped rather than ranked. Measured
    against the live corpus, meaningless queries reach `RECALL_COSINE_FLOOR` and no further."""
    far = (
        Hit("f", OWNER_KIND_MEMORY_ITEM, "F", SHARED_SUBJECT, 0, "far", RECALL_COSINE_FLOOR / 2),
    )
    assert fuse_recall((), far, (), 10) == ()


def test_fuse_recall_keeps_a_lexically_matched_row_however_far_its_embedding() -> None:
    """The floor governs the vector leg alone. A row a lexical leg matched holds words the member
    typed, which a random string cannot fake, so it stands whatever its cosine — this is what keeps
    a one-word query like `pricing` answering."""
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


def test_fuse_recall_folds_in_the_un_embedded_tail_leg() -> None:
    """The un-embedded tail is a third, lexical-only leg: a row present only in the tail fuses into
    the ranking (no index hit needed), while an indexed row keeps its index-hit ranking."""
    lexical = (Hit("a", OWNER_KIND_MEMORY_ITEM, "A", SHARED_SUBJECT, 0, "alpha", 4.0),)
    vector = (Hit("a", OWNER_KIND_MEMORY_ITEM, "A", SHARED_SUBJECT, 0, "alpha", 0.9),)
    tail = (Hit("tail:T", OWNER_KIND_MEMORY_ITEM, "T", SHARED_SUBJECT, 0, "fresh", 2.0),)
    owners = [fused.owner_id for fused in fuse_recall(lexical, vector, tail, 10)]
    assert set(owners) == {"A", "T"}


async def test_recall_blend_promotes_the_semantically_closer_fact(db: None) -> None:
    """The cosine blend end to end: two distinctly-worded but equally query-relevant facts
    committed at the same time tie on the lexical leg and on decay, so recall's order is decided by
    the raw query-chunk cosine — the fact whose embedding is nearer the query ranks first. The
    trailing word differs only enough to keep the near-duplicate guard from collapsing the pair."""
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


async def test_fresh_fact_recalls_before_indexing_then_via_the_index(db: None) -> None:
    """Immediacy: a just-committed fact is recallable before the index job derives its chunks — the
    un-embedded tail's lexical leg surfaces it. After the indexer stamps its digest, the index path
    serves it and the tail (embedding_digest IS NULL) no longer holds it, so it counts once."""
    workspace_id = await _workspace()
    probe = vec((7, 1.0))
    embed = StubEmbed(probe)
    store = _store(embed, workspace_id)
    await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body="the safe combination is 1234"))

    before = await store.recall(
        "safe combination",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert len(before) == 1
    assert "1234" in before[0].body

    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    after = await store.recall(
        "safe combination",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert len(after) == 1
    assert "1234" in after[0].body


async def test_untail_leg_respects_subject_scoping(db: None) -> None:
    """The tail leg is workspace + subject scoped like the index legs: an un-embedded fact in one
    member's subject is invisible to another member's recall, and never leaks cross-member."""
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    store = _store(StubEmbed(vec((0, 1.0))), workspace_id)
    await store.commit(MemoryWrite(subject=member_subject(alice), body="alices locker code is 77"))
    assert (
        await store.recall(
            "locker code",
            recall_subjects(conversation_audience(bob)),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
        == ()
    )
    mine = await store.recall(
        "locker code",
        recall_subjects(conversation_audience(alice)),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert len(mine) == 1 and "77" in mine[0].body


async def test_recall_returns_items_scoped_to_subject(db: None) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    probe = vec((1, 1.0))
    await _seed_item(workspace_id, member_subject(member), "alice prefers a window seat", probe)
    await _seed_item(workspace_id, SHARED_SUBJECT, "the office wifi password is maple", probe)

    mine = await _store(StubEmbed(probe), workspace_id).recall(
        "seat and wifi",
        recall_subjects(conversation_audience(member)),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert {item.subject for item in mine} == {member_subject(member), SHARED_SUBJECT}

    theirs = await _store(StubEmbed(probe), workspace_id).recall(
        "seat and wifi",
        recall_subjects(conversation_audience(uuid4())),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert [item.subject for item in theirs] == [SHARED_SUBJECT]


async def test_page_derived_recall_rechecks_the_current_page_subject(db: None) -> None:
    workspace_id = await _workspace()
    page_id, source_id = uuid4(), uuid4()
    member = uuid4()
    probe = vec((2, 1.0))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "the private acquisition codename is polaris",
        probe,
        created_from_page_id=page_id,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(subject=member_subject(member))
            .where(tables.page.c.id == page_id)
        )
    with ws(workspace_id):
        recalled = await _store(StubEmbed(probe), workspace_id).recall(
            "acquisition codename",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
    assert recalled == ()


async def test_recall_skips_a_superseded_item(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((2, 1.0))
    stale = await _seed_item(workspace_id, SHARED_SUBJECT, "the release ship date is friday", probe)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(superseded_by=uuid4(), updated_at=sa.func.now())
            .where(memory_item.c.id == stale)
        )
    empty = await _store(StubEmbed(probe), workspace_id).recall(
        "release ship date",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert empty == ()


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


async def test_pages_and_facts_do_not_crowd_each_others_candidate_window(db: None) -> None:
    """Facts and pages share the chunk index; each retrieval must get a full limit of its own kind.
    With the limit equal to the fact count (and the page count), one shared candidate window could
    return at most `limit` rows across both kinds — so recall returning every fact AND search
    returning every page at that limit proves neither kind crowds the other out."""
    workspace_id = await _workspace()
    probe = vec((4, 1.0))
    fact_a = await _seed_item(workspace_id, SHARED_SUBJECT, "quarterly report figures", probe)
    fact_b = await _seed_item(workspace_id, SHARED_SUBJECT, "quarterly report summary", probe)
    await _seed_page_chunk(workspace_id, SHARED_SUBJECT, "quarterly report appendix", probe)
    await _seed_page_chunk(workspace_id, SHARED_SUBJECT, "quarterly report preface", probe)

    store = _store(StubEmbed(probe), workspace_id)
    subjects = frozenset({SHARED_SUBJECT})
    with ws(workspace_id):
        facts = await store.recall(
            "quarterly report", subjects, 2, source_reader=_reader(frozenset({SHARED_SUBJECT}))
        )
        pages = await store.search_sources(
            "quarterly report",
            subjects,
            2,
            source_reader=_reader(subjects),
        )

    assert {item.memory_id for item in facts} == {fact_a, fact_b}
    assert len(pages) == 2
    assert all("quarterly report" in page.text for page in pages)


async def test_source_grants_filter_before_recall_ranking(db: None) -> None:
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
                sa.select(tables.page.c.source_id).where(tables.page.c.id == granted_page)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=granted_source,
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    ext = context_for("memory", frozenset())
    store = MemoryStore(
        index=DefaultIndex(transaction=workspace_tx),
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


async def test_a_fact_from_two_feeds_is_recalled_through_a_non_primary_granted_source(
    db: None,
) -> None:
    """One fact derived from two feeds is one row with a link per source, primary bound to the feed
    that wrote it last. A reader granted only the *other* source still recalls it — the grant fence
    reads the link set, not the row's current binding. A binding-only fence would have denied it,
    the exact case spec.md says a grant on any one source must reach."""
    workspace_id = await _workspace()
    page_a, page_b = uuid4(), uuid4()
    source_a, source_b = uuid4(), uuid4()
    await _seed_page(workspace_id, page_a, source_a, SHARED_SUBJECT)
    await _seed_page(workspace_id, page_b, source_b, SHARED_SUBJECT)
    async with workspace_tx() as connection:
        revisions = {
            row.id: row.revision
            for row in (
                await connection.execute(
                    sa.select(tables.page.c.id, tables.page.c.revision).where(
                        tables.page.c.id.in_((page_a, page_b))
                    )
                )
            ).all()
        }
    ext = context_for("memory", frozenset())
    store = MemoryStore(
        index=DefaultIndex(transaction=workspace_tx),
        embed=StubEmbed(vec((0, 1.0))),
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )
    body = "the vault code is 8842"
    for page_id, source_id in ((page_a, source_a), (page_b, source_b)):
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=revisions[page_id],
                source_id=source_id,
            )
        )
    with ws(workspace_id):
        await MemoryIndexer(
            index=store.index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=ext.page_states,
        ).run()
    async with workspace_tx() as connection:
        primary = (
            await connection.execute(
                sa.select(memory_item.c.source_id).where(memory_item.c.body == body)
            )
        ).scalar_one()
    assert primary == source_b
    reader = await _granted_reader(workspace_id, SHARED_SUBJECT, source_a)
    with ws(workspace_id):
        recalled = await store.recall(
            "vault code", frozenset({SHARED_SUBJECT}), 10, source_reader=reader
        )
    assert [str(fact.body) for fact in recalled] == [body]


async def test_source_search_rechecks_the_current_page_subject(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((5, 1.0))
    page_id = await _seed_page_chunk(
        workspace_id, SHARED_SUBJECT, "private quarterly forecast", probe
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(subject=member_subject(uuid4()))
            .where(tables.page.c.id == page_id)
        )
    with ws(workspace_id):
        pages = await _store(StubEmbed(probe), workspace_id).search_sources(
            "quarterly forecast",
            frozenset({SHARED_SUBJECT}),
            10,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
    assert pages == ()


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
    assert decay_factor(replace(fresh, item_class="episodic"), now) == 1.0
    assert decay_factor(replace(fresh, item_class="semantic"), now) == 1.0


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
    can fill, and its cost is one trigram set per body it examines. Once it holds the distinct items
    the caller asked for it stops: the bodies below them are never shingled, so a pool carrying
    2,000-character rows costs the turn only what its slots need."""
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
    """Recall only ever injects a body's first RECALL_ITEM_MAX_CHARS (`recall_hook` truncates every
    line there), so prefix-equality already is injected-content-equality: two bodies sharing that
    prefix but diverging well past it inject as identical lines and should collapse to the
    higher-ranked copy."""
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
    """`drop_near_duplicates` proves its wiring inside `MemoryStore.recall`, not only as a
    standalone function: a near-duplicate restatement (word-trigram Jaccard >= 0.6, not identical
    text) ranks just behind the fact it restates and must not spend a second slot on it, so at
    limit=2 the
    freed slot backfills with the next distinct, lower-ranked fact rather than truncating early. If
    the wiring reverted to `enforce_type_diversity(ranked, limit)` this would return the duplicate
    pair and drop the distinct fact instead."""
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


async def test_recall_reorders_by_information_age(db: None) -> None:
    """Two equally-matching facts committed together rank by source information time: the current
    one first and the year-old page fact demoted, end to end over the real index. The trailing word
    differs only enough to keep the near-duplicate guard from collapsing the pair."""
    workspace_id = await _workspace()
    probe = vec((8, 1.0))
    old = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "budget review meeting stale",
        probe,
        as_of=datetime.now(UTC) - timedelta(days=365),
    )
    new = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "budget review meeting fresh",
        probe,
        as_of=datetime.now(UTC),
    )
    recalled = await _store(StubEmbed(probe), workspace_id).recall(
        "budget review",
        frozenset({SHARED_SUBJECT}),
        10,
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    assert [item.memory_id for item in recalled] == [new, old]


async def test_recall_filters_to_the_created_at_window(db: None) -> None:
    """The trailing word on each body differs only enough to keep the near-duplicate guard from
    collapsing the pair the `span` case expects both of."""
    workspace_id = await _workspace()
    probe = vec((0, 1.0))
    old = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "alpha budget review stale",
        probe,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    new = await _seed_item(
        workspace_id,
        SHARED_SUBJECT,
        "alpha budget review fresh",
        probe,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    store = _store(StubEmbed(probe), workspace_id)
    subjects = frozenset({SHARED_SUBJECT})

    since = await store.recall(
        "alpha",
        subjects,
        8,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    before = await store.recall(
        "alpha",
        subjects,
        8,
        end=datetime(2021, 1, 1, tzinfo=UTC),
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )
    span = await store.recall(
        "alpha",
        subjects,
        8,
        start=datetime(2019, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, tzinfo=UTC),
        source_reader=_reader(frozenset({SHARED_SUBJECT})),
    )

    assert {item.memory_id for item in since} == {new}
    assert {item.memory_id for item in before} == {old}
    assert {item.memory_id for item in span} == {old, new}


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
        subject=SHARED_SUBJECT,
        stream="notes",
        title="Merger timing",
        body="the merger closes in the third quarter",
        digest=PAGE_DIGEST,
        revision=PAGE_REVISION,
        tombstone=False,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        changed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await _seed_page(workspace_id, change.page_id, change.source_id, SHARED_SUBJECT)
    with ws(workspace_id):
        await PageIndexer(
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        ).apply((change,))

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(mem_page.c.workspace_id).where(mem_page.c.page_id == change.page_id)
            )
        ).one()
    assert row.workspace_id == workspace_id


async def test_page_index_write_is_deleted_when_the_subject_changes_during_embed(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id = uuid4()
    source_id = uuid4()
    index = DefaultIndex(transaction=workspace_tx)
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
                    subject=SHARED_SUBJECT,
                    stream="notes",
                    title="Stale page",
                    body="the stale page codename is polaris",
                    digest="sha256:stale",
                    revision=PAGE_REVISION,
                    tombstone=False,
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
                sa.select(mem_page.c.page_id).where(mem_page.c.page_id == page_id)
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
            .where(tables.page.c.id == page_id)
        )
    now = datetime(2025, 1, 2, tzinfo=UTC)
    index = DefaultIndex(transaction=workspace_tx)
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
        subject=member_subject(member_id),
        stream="notes",
        title="Private plan",
        body="the private acquisition codename is polaris",
        digest="sha256:private",
        revision=PAGE_REVISION,
        tombstone=False,
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
    """Redacting a page fences its facts out of recall the moment the revision moves — and that is
    the whole guarantee the read path needs. The page indexer, riding a cursor that derives no
    replacement, removes no row and deletes no chunk itself: it leaves the fact due for the index
    job, which is what withdraws the chunks. Retiring the row waits on the derivation that settles
    the new revision."""
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
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(digest="sha256:redacted")
                .where(tables.page.c.id == page_id)
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
                    subject=SHARED_SUBJECT,
                    stream="notes",
                    title="Redacted plan",
                    body="the acquisition plan has been redacted",
                    digest="sha256:redacted",
                    revision=PAGE_REVISION + 1,
                    tombstone=False,
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
                .where(memory_item.c.created_from_page_id == page_id)
            )
        ).scalar_one() == 1


async def test_retirement_requeues_a_fact_rebound_while_its_index_is_deleted(
    db: None,
) -> None:
    """Retirement deletes the superseded row and then its index scope. A derivation that rebinds the
    identical body to the new revision in between lands on the same content-addressed id, so the
    scope delete strips a live row's chunks — the re-created row is due again and the index job
    restores it, leaving nothing recallable-but-unindexed."""
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
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
    new_digest = "sha256:redacted"
    new_revision = PAGE_REVISION + 1
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(digest=new_digest).where(tables.page.c.id == page_id)
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
    with ws(workspace_id):
        await replace(store, index=index).supersede_page_facts(page_id, new_revision)
        await MemoryIndexer(
            index=index,
            embed=store.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
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
                ).where(memory_item.c.created_from_page_id == page_id)
            )
        ).one()
    assert row.created_from_page_revision == new_revision
    assert row.embedding_digest is not None


async def test_a_revision_rebind_with_an_unchanged_body_is_not_re_embedded(db: None) -> None:
    """A feed that re-emits identical content under a new revision rebinds the fact to that
    revision, making it due again — but the body is unchanged, and the item id is content-addressed
    over the body, so its chunks already sit in the index. The job re-checks publishability and
    settles the row without paying the embed a second time."""
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
            sa.update(tables.page).values(digest="sha256:rev2").where(tables.page.c.id == page_id)
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
                ).where(memory_item.c.created_from_page_id == page_id)
            )
        ).one()
    assert row.embedding_digest is not None
    assert row.created_from_page_revision == PAGE_REVISION + 1


async def test_a_stale_revision_is_withdrawn_even_though_its_chunks_still_exist(db: None) -> None:
    """The re-embed skip must never keep a stale body alive. A page that moves on without
    re-deriving a fact leaves the fact's chunks in the index, but the job withdraws them because
    the fact is no longer publishable — the publishability gate runs ahead of the has-chunks skip,
    so a row due for a stale revision loses its chunks rather than keeping them."""
    workspace_id, page_id, source_id = await _workspace(), uuid4(), uuid4()
    body = "the merger closes in march"
    embed = CountingEmbed(vec((8, 1.0)))
    await _seed_page(workspace_id, page_id, source_id, SHARED_SUBJECT)
    store = _store(embed, workspace_id)

    def _run() -> Awaitable[None]:
        return MemoryIndexer(
            index=store.index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
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
        assert (
            len(
                await store.recall(
                    "merger",
                    frozenset({SHARED_SUBJECT}),
                    10,
                    source_reader=_reader(frozenset({SHARED_SUBJECT})),
                )
            )
            == 1
        )
    async with workspace_tx() as connection:
        item_id = (
            await connection.execute(
                sa.select(memory_item.c.id).where(memory_item.c.created_from_page_id == page_id)
            )
        ).scalar_one()
    scope = IndexScope(OWNER_KIND_MEMORY_ITEM, str(item_id))
    with ws(workspace_id):
        assert await store.index.has_chunks(scope)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(digest="sha256:moved").where(tables.page.c.id == page_id)
        )
        await connection.execute(
            sa.update(memory_item)
            .values(embedding_digest=None, embedding_claimed_at=None)
            .where(memory_item.c.id == item_id)
        )
    with ws(workspace_id):
        await _run()
        assert (
            await store.recall(
                "merger",
                frozenset({SHARED_SUBJECT}),
                10,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
        assert not await store.index.has_chunks(scope)


async def test_a_narrowed_pages_wider_fact_is_never_published_and_never_deleted(
    db: None,
) -> None:
    """A page that narrows to one member strands the fact derived under the wider subject. The index
    job is what could publish it, and it will not: the item is left unindexed, unrecallable, and
    intact — every deployed row memory_0010 stripped of its digest reaches exactly this state, and
    reaching it must cost no data. Retiring it belongs to the derivation that replaces it."""
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
                sa.update(tables.page).values(subject=subject).where(tables.page.c.id == page_id)
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
                    subject=subject,
                    stream="messages",
                    title="Private mailbox",
                    body="the mailbox page is now private",
                    digest=PAGE_DIGEST,
                    revision=PAGE_REVISION + 1,
                    tombstone=False,
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
                        memory_item.c.created_from_page_id == page_id
                    )
                )
            )
            .mappings()
            .all()
        )
    assert {row["body"]: row["subject"] for row in rows} == {
        stale_body: SHARED_SUBJECT,
        kept_body: subject,
    }


async def test_recommitting_an_unchanged_body_at_the_same_binding_never_re_embeds(
    db: None,
) -> None:
    """The body, subject, and owner id are what a chunk is digested over, so re-committing an
    identical fact at the same page binding must leave the row's chunks and digest alone: the second
    commit changes nothing due, and the indexer finds nothing to embed. A re-commit that binds a new
    revision is a different case — it makes the row due again, since whether that revision may be
    published is the index job's question."""
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
                    memory_item.c.created_from_page_id == page_id
                )
            )
        ).scalar_one()
    await poll()
    async with workspace_tx() as connection:
        second = (
            await connection.execute(
                sa.select(
                    memory_item.c.embedding_digest, memory_item.c.created_from_page_revision
                ).where(memory_item.c.created_from_page_id == page_id)
            )
        ).one()
    assert after_first == 1
    assert first is not None
    assert second == (first, PAGE_REVISION)
    assert embed.calls == after_first


async def test_page_tombstone_drops_the_pages_own_chunks_and_mirror_only(db: None) -> None:
    """A tombstone retires what the page indexer owns — the page's chunks and its mirror row — and
    deletes nothing else. The facts derived from that page stop being recallable the instant it
    goes; they are the deriver's rows to retire and survive this pass, left due for the index job
    that withdraws their chunks."""
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
        subject=SHARED_SUBJECT,
        stream="notes",
        title="Retired page",
        body="the retired source page names polaris",
        digest=PAGE_DIGEST,
        revision=PAGE_REVISION,
        tombstone=False,
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
            page_states=context_for("memory", frozenset()).page_states,
        ).run()
        await indexer.apply((live,))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page).values(tombstone=True).where(tables.page.c.id == page_id)
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
                .where(mem_page.c.page_id == page_id)
            )
        ).scalar_one()
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(memory_item)
                .where(memory_item.c.created_from_page_id == page_id)
            )
        ).scalar_one()
    assert mirror == 0
    assert count == 1
