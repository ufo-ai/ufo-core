"""The memory extension's MemoryStore: commit derives nothing, recall fuses the index legs.

MemoryStore is the memory extension's domain workflow, driven here exactly as the extension's tools
and hook drive it — over the deploy index/embed backends and the workspace-scoped transaction core
threads onto the context. The embed client and the DefaultIndex are real dependencies, never the
asserted thing: every assertion reads the Recalled/SourceMatch values back."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_embed_openai import EMBED_DIM
from selfhost_ext_index_default import DefaultIndex
from selfhost_ext_memory.store import (
    FACT,
    MemoryStore,
    MemoryWrite,
    mem_page,
    memory_item,
    recall_subjects,
)

from selfhost.db import workspace_tx
from selfhost.indexing import OWNER_KIND_MEMORY_ITEM, OWNER_KIND_PAGE, Chunk
from selfhost.schema import tables
from selfhost.subjects import SHARED_SUBJECT, member_subject


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


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        await connection.execute(sa.text("delete from mem_page"))
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


def _store(embed: object, workspace_id: UUID) -> MemoryStore:
    return MemoryStore(
        index=DefaultIndex(embed=embed, transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
    )


async def _seed_item(
    workspace_id: UUID,
    subject: str,
    body: str,
    vector: tuple[float, ...],
    created_at: datetime | None = None,
) -> UUID:
    """Insert a memory_item and its one already-derived chunk directly, so recall can be exercised
    without the derivation job in these unit tests."""
    item_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject=subject,
                body=body,
                item_class=FACT,
                source_ref=None,
                embedding_digest="sha256:seeded",
                superseded_by=None,
                created_at=created_at if created_at is not None else sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await DefaultIndex(embed=StubEmbed(vector), transaction=workspace_tx).upsert(
        (Chunk("d-" + item_id.hex, OWNER_KIND_MEMORY_ITEM, str(item_id), subject, 0, body, vector),)
    )
    return item_id


async def _seed_page_chunk(subject: str, body: str, vector: tuple[float, ...]) -> None:
    page_id = uuid4()
    await DefaultIndex(embed=StubEmbed(vector), transaction=workspace_tx).upsert(
        (Chunk("p-" + page_id.hex, OWNER_KIND_PAGE, str(page_id), subject, 0, body, vector),)
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id, subject=subject, created_at=sa.func.now()
            )
        )


async def test_commit_persists_item_and_derives_no_chunk(clean: None) -> None:
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


async def test_recall_returns_items_scoped_to_subject(clean: None) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    probe = vec((1, 1.0))
    await _seed_item(workspace_id, member_subject(member), "alice prefers a window seat", probe)
    await _seed_item(workspace_id, SHARED_SUBJECT, "the office wifi password is maple", probe)

    mine = await _store(StubEmbed(probe), workspace_id).recall(
        "seat and wifi", recall_subjects(member), 10
    )
    assert {item.subject for item in mine} == {member_subject(member), SHARED_SUBJECT}

    theirs = await _store(StubEmbed(probe), workspace_id).recall(
        "seat and wifi", recall_subjects(uuid4()), 10
    )
    assert [item.subject for item in theirs] == [SHARED_SUBJECT]


async def test_recall_skips_a_superseded_item(clean: None) -> None:
    workspace_id = await _workspace()
    probe = vec((2, 1.0))
    stale = await _seed_item(
        workspace_id, SHARED_SUBJECT, "the release ship date is friday", probe
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(superseded_by=uuid4(), updated_at=sa.func.now())
            .where(memory_item.c.id == stale)
        )
    empty = await _store(StubEmbed(probe), workspace_id).recall(
        "release ship date", frozenset({SHARED_SUBJECT}), 10
    )
    assert empty == ()


async def test_recall_degrades_to_lexical_when_embed_fails(clean: None) -> None:
    workspace_id = await _workspace()
    await _seed_item(workspace_id, SHARED_SUBJECT, "the mascot is named zoltar", vec((3, 1.0)))
    hits = await _store(BrokenEmbed(), workspace_id).recall(
        "zoltar", frozenset({SHARED_SUBJECT}), 10
    )
    assert len(hits) == 1
    assert "zoltar" in hits[0].body


async def test_pages_and_facts_do_not_crowd_each_others_candidate_window(clean: None) -> None:
    """Facts and pages share the chunk index; each retrieval must get a full limit of its own kind.
    With the limit equal to the fact count (and the page count), one shared candidate window could
    return at most `limit` rows across both kinds — so recall returning every fact AND search
    returning every page at that limit proves neither kind crowds the other out."""
    workspace_id = await _workspace()
    probe = vec((4, 1.0))
    fact_a = await _seed_item(workspace_id, SHARED_SUBJECT, "quarterly report figures", probe)
    fact_b = await _seed_item(workspace_id, SHARED_SUBJECT, "quarterly report summary", probe)
    await _seed_page_chunk(SHARED_SUBJECT, "quarterly report appendix", probe)
    await _seed_page_chunk(SHARED_SUBJECT, "quarterly report preface", probe)

    store = _store(StubEmbed(probe), workspace_id)
    subjects = frozenset({SHARED_SUBJECT})
    facts = await store.recall("quarterly report", subjects, 2)
    pages = await store.search_sources("quarterly report", subjects, 2)

    assert {item.memory_id for item in facts} == {fact_a, fact_b}
    assert len(pages) == 2
    assert all("quarterly report" in page.text for page in pages)


async def test_recall_filters_to_the_created_at_window(clean: None) -> None:
    workspace_id = await _workspace()
    probe = vec((0, 1.0))
    old = await _seed_item(
        workspace_id, SHARED_SUBJECT, "alpha budget review", probe,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    new = await _seed_item(
        workspace_id, SHARED_SUBJECT, "alpha budget review", probe,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    store = _store(StubEmbed(probe), workspace_id)
    subjects = frozenset({SHARED_SUBJECT})

    since = await store.recall("alpha", subjects, 8, start=datetime(2024, 1, 1, tzinfo=UTC))
    before = await store.recall("alpha", subjects, 8, end=datetime(2021, 1, 1, tzinfo=UTC))
    span = await store.recall(
        "alpha", subjects, 8,
        start=datetime(2019, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert {item.memory_id for item in since} == {new}
    assert {item.memory_id for item in before} == {old}
    assert {item.memory_id for item in span} == {old, new}
