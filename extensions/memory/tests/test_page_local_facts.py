"""One fact from one page is one row.

A page-derived `memory_item` is keyed by `(workspace, subject, item_class, body_digest, page)` and
a conversation-written one by the same key without the page, each under a partial unique index, so
two pages stating identical bytes are two rows, retiring a page touches only its own, and every
read serves one row per statement. Driven over the real DefaultIndex and the workspace-scoped
transaction under both dialects: the conflict target is a partial index each dialect infers its own
way."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import (
    FACT,
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    body_digest,
    memory_item,
)

from ufo.db import workspace_tx
from ufo.runtime.ext.context import SourceReader, context_for
from ufo.runtime.indexing import OWNER_KIND_MEMORY_ITEM, IndexScope, TextChunker
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite", "postgresql"], indirect=True),
]

WHEN = datetime(2025, 1, 1, tzinfo=UTC)
LATER = datetime(2025, 2, 1, tzinfo=UTC)
BODY = "the launch date is June 12"
PROBE = tuple(1.0 if axis == 0 else 0.0 for axis in range(EMBED_DIM))


class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(PROBE for _ in texts)


async def _workspace() -> UUID:
    workspace_id = uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_page(workspace_id: UUID, page_id: UUID, source_id: UUID) -> None:
    connection_id = uuid7()
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
                created_at=WHEN,
                updated_at=WHEN,
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
                next_sync_at=WHEN,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest="sha256:page",
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )


async def _revision(page_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()


def _store(workspace_id: UUID) -> MemoryStore:
    ext = context_for("memory", frozenset())
    return MemoryStore(
        index=DefaultIndex(transaction=workspace_tx),
        embed=StubEmbed(),
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )


async def _granted_reader(workspace_id: UUID, *source_ids: UUID) -> SourceReader:
    agent_id = uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=f"reader-{agent_id.hex}",
                prompt="p",
                model="m",
                created_at=WHEN,
                updated_at=WHEN,
            )
        )
        for source_id in source_ids:
            connection_id = (
                await connection.execute(
                    sa.select(tables.source.c.connection_id).where(tables.source.c.uid == source_id)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    created_at=WHEN,
                    updated_at=WHEN,
                )
            )
    return SourceReader(
        agent_id=agent_id, requesting_member_id=None, subjects=frozenset({SHARED_SUBJECT})
    )


async def _index(store: MemoryStore) -> None:
    await MemoryIndexer(
        index=store.index,
        embed=store.embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        page_states=context_for("memory", frozenset()).page_states,
    ).run()


async def _rows(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.body_digest,
                        memory_item.c.confidence,
                        memory_item.c.created_from_page_uid,
                        memory_item.c.created_from_page_revision,
                        memory_item.c.source_uid,
                        memory_item.c.embedding_digest,
                    )
                    .where(memory_item.c.workspace_id == workspace_id)
                    .order_by(memory_item.c.created_at, memory_item.c.id)
                )
            ).all()
        )


async def _chunks_held(store: MemoryStore, memory_id: UUID) -> bool:
    return await store.index.has_chunks(IndexScope(OWNER_KIND_MEMORY_ITEM, str(memory_id)))


async def _derived(
    page_id: UUID, source_id: UUID, body: str = BODY, as_of: datetime = WHEN
) -> MemoryWrite:
    return MemoryWrite(
        subject=SHARED_SUBJECT,
        body=body,
        created_from_page_id=page_id,
        created_from_page_revision=await _revision(page_id),
        source_id=source_id,
        as_of=as_of,
    )


async def _insert(
    workspace_id: UUID, body: str, page_id: UUID | None, source_id: UUID | None
) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(memory_item).values(
                id=uuid7(),
                workspace_id=workspace_id,
                subject=SHARED_SUBJECT,
                body=body,
                body_digest=body_digest(body),
                item_class=FACT,
                created_from_page_uid=page_id,
                created_from_page_revision=None if page_id is None else 1,
                source_uid=source_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_the_same_fact_from_two_pages_is_two_rows(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a, page_b, source_b = uuid7(), uuid7(), uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    await _seed_page(workspace_id, page_b, source_b)
    store = _store(workspace_id)
    with ws(workspace_id):
        landed_a = await store.commit(await _derived(page_a, source_a))
        landed_b = await store.commit(await _derived(page_b, source_b))
        rows = await _rows(workspace_id)

    assert landed_a != landed_b
    assert {(row.id, row.created_from_page_uid, row.source_uid) for row in rows} == {
        (landed_a, page_a, source_a),
        (landed_b, page_b, source_b),
    }
    assert {row.body_digest for row in rows} == {body_digest(BODY)}


async def test_retiring_one_page_keeps_the_other_pages_row_and_chunks(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a, page_b, source_b = uuid7(), uuid7(), uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    await _seed_page(workspace_id, page_b, source_b)
    store = _store(workspace_id)
    reader = await _granted_reader(workspace_id, source_a)
    with ws(workspace_id):
        landed_a = await store.commit(await _derived(page_a, source_a))
        landed_b = await store.commit(await _derived(page_b, source_b))
        await _index(store)
        async with workspace_tx() as connection:
            await connection.execute(sa.delete(tables.page).where(tables.page.c.uid == page_b))
        await store.supersede_page_facts(page_b, None)
        rows = await _rows(workspace_id)
        held = (await _chunks_held(store, landed_a), await _chunks_held(store, landed_b))
        recalled = await store.recall(
            "launch date", frozenset({SHARED_SUBJECT}), 10, source_reader=reader
        )

    assert [(row.id, row.created_from_page_uid) for row in rows] == [(landed_a, page_a)]
    assert rows[0].embedding_digest is not None
    assert held == (True, False)
    assert [item.memory_id for item in recalled] == [landed_a]


async def test_a_restatement_from_the_same_page_at_a_new_revision_rebinds_the_row(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_a, source_a = uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    store = _store(workspace_id)
    with ws(workspace_id):
        first = await store.commit(await _derived(page_a, source_a))
        await _index(store)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(digest="sha256:edited")
                .where(tables.page.c.uid == page_a)
            )
        moved = await _revision(page_a)
        second = await store.commit(await _derived(page_a, source_a))
        rows = await _rows(workspace_id)

    assert first == second
    assert [(row.id, row.created_from_page_revision, row.embedding_digest) for row in rows] == [
        (first, moved, None)
    ]


async def test_a_conversation_restatement_lands_on_its_existing_row(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a = uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    store = _store(workspace_id)
    with ws(workspace_id):
        first = await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=BODY, confidence=3))
        second = await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=BODY, confidence=9))
        derived = await store.commit(await _derived(page_a, source_a))
        rows = await _rows(workspace_id)

    assert first == second != derived
    assert {(row.id, row.confidence, row.created_from_page_uid) for row in rows} == {
        (first, 9, None),
        (derived, 5, page_a),
    }


async def test_recall_returns_one_of_two_identical_bodies_from_two_pages(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a, page_b, source_b = uuid7(), uuid7(), uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    await _seed_page(workspace_id, page_b, source_b)
    store = _store(workspace_id)
    both = await _granted_reader(workspace_id, source_a, source_b)
    only_a = await _granted_reader(workspace_id, source_a)
    with ws(workspace_id):
        landed_a = await store.commit(await _derived(page_a, source_a, as_of=WHEN))
        landed_b = await store.commit(await _derived(page_b, source_b, as_of=LATER))
        await _index(store)
        through_both = await store.recall(
            "launch date", frozenset({SHARED_SUBJECT}), 10, source_reader=both
        )
        through_a = await store.recall(
            "launch date", frozenset({SHARED_SUBJECT}), 10, source_reader=only_a
        )

    assert [item.memory_id for item in through_both] == [landed_b]
    assert [item.memory_id for item in through_a] == [landed_a]


async def test_supersede_page_facts_with_kept_deletes_exactly_the_others(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a, page_b, source_b = uuid7(), uuid7(), uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    await _seed_page(workspace_id, page_b, source_b)
    store = _store(workspace_id)
    with ws(workspace_id):
        kept = await store.commit(await _derived(page_a, source_a, body="the vault code is 4821"))
        dropped = tuple(
            [
                await store.commit(await _derived(page_a, source_a, body="the auditor is booked")),
                await store.commit(
                    await _derived(page_a, source_a, body="the ledger closes friday")
                ),
            ]
        )
        other = await store.commit(await _derived(page_b, source_b, body="the vault code is 4821"))
        await _index(store)
        await store.supersede_page_facts(page_a, frozenset({kept}))
        rows = await _rows(workspace_id)
        held = {
            memory_id: await _chunks_held(store, memory_id) for memory_id in (kept, *dropped, other)
        }
        with pytest.raises(ValueError, match="retires nothing"):
            await store.supersede_page_facts(page_a, frozenset())

    assert {row.id for row in rows} == {kept, other}
    assert held == {kept: True, dropped[0]: False, dropped[1]: False, other: True}


async def test_the_database_holds_one_row_per_statement_per_page(db: None) -> None:
    workspace_id = await _workspace()
    page_a, source_a = uuid7(), uuid7()
    await _seed_page(workspace_id, page_a, source_a)
    await _insert(workspace_id, BODY, page_a, source_a)
    await _insert(workspace_id, BODY, None, None)
    with pytest.raises(IntegrityError):
        await _insert(workspace_id, BODY, page_a, source_a)
    with pytest.raises(IntegrityError):
        await _insert(workspace_id, BODY, None, None)
    assert len(await _rows(workspace_id)) == 2
