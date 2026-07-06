"""The knowledge-graph derivation, driven end to end over real source pages and a real database.

`GraphExtractor` is the extension's producer; it rides the core `CorePageFeed` off its own
ScopedStore cursor exactly as core threads them onto the job's context, and every assertion reads
the resulting `graph_entity`/`graph_edge` rows back — never a fake. The pages are real `page` rows
with real blob bodies the feed inlines, so the cursor, digest gate, and tombstone signal are all
exercised as the sync driver produces them. Parsing is pure and asserted directly.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_knowledge_graph.store import (
    PAGE_CURSOR_KEY,
    GraphExtractor,
    GraphStore,
    UnknownEdgeType,
    graph_edge,
    graph_entity,
    parse_page,
    to_edge_type,
)

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ScopedStore
from selfhost.schema import tables
from selfhost.sources.sync import CorePageFeed
from selfhost.subjects import SHARED_SUBJECT

BASE = datetime(2026, 1, 1, tzinfo=UTC)
EXTENSION = "knowledge-graph"


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _source(workspace_id: UUID) -> UUID:
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                cursor=None,
                next_sync_at=datetime.now(UTC),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id


async def _seed_page(
    blob: FilesystemBlobStore,
    workspace_id: UUID,
    source_id: UUID,
    body: str,
    when: datetime,
    subject: str = SHARED_SUBJECT,
) -> UUID:
    page_id = uuid4()
    body_ref = f"pages/{page_id}"
    await blob.put(body_ref, body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                body_ref=body_ref,
                subject=subject,
                tombstone=False,
                created_at=when,
                updated_at=when,
            )
        )
    return page_id


async def _revise_page(
    blob: FilesystemBlobStore, page_id: UUID, body: str, when: datetime, tombstone: bool = False
) -> None:
    await blob.put(f"pages/{page_id}", body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                tombstone=tombstone,
                updated_at=when,
            )
            .where(tables.page.c.id == page_id)
        )


def _extractor(workspace_id: UUID, blob: FilesystemBlobStore) -> GraphExtractor:
    return GraphExtractor(
        pages=CorePageFeed(blob=blob),
        transaction=workspace_tx,
        cursor_store=ScopedStore(workspace_id=workspace_id, extension=EXTENSION),
        workspace_id=workspace_id,
    )


async def _entity(workspace_id: UUID, normalized_name: str, entity_type: str) -> sa.Row | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(graph_entity.c.id, graph_entity.c.name, graph_entity.c.is_stub).where(
                    graph_entity.c.workspace_id == workspace_id,
                    graph_entity.c.normalized_name == normalized_name,
                    graph_entity.c.entity_type == entity_type,
                )
            )
        ).one_or_none()


async def _edges(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        graph_edge.c.id,
                        graph_edge.c.edge_type,
                        graph_edge.c.from_entity,
                        graph_edge.c.to_entity,
                        graph_edge.c.source_page_id,
                        graph_edge.c.updated_at,
                    ).where(
                        graph_edge.c.workspace_id == workspace_id,
                        graph_edge.c.tombstone.is_(False),
                    )
                )
            ).all()
        )


async def test_reference_creates_a_stub_then_a_page_fills_it(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    await _seed_page(
        blob, workspace_id, source_id, "# Meeting Notes\nfollow up on [[Widget]]", BASE
    )
    await _extractor(workspace_id, blob).run()

    stub = await _entity(workspace_id, "widget", "topic")
    assert stub is not None and stub.is_stub
    anchor = await _entity(workspace_id, "meeting notes", "topic")
    assert anchor is not None and not anchor.is_stub

    await _seed_page(
        blob, workspace_id, source_id, "# Widget\nthe widget spec", BASE + timedelta(seconds=1)
    )
    await _extractor(workspace_id, blob).run()

    filled = await _entity(workspace_id, "widget", "topic")
    assert filled is not None and not filled.is_stub
    assert filled.id == stub.id


async def test_typed_links_produce_the_bounded_vocabulary_and_typed_nodes(
    db: None, tmp_path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    await _seed_page(
        blob,
        workspace_id,
        source_id,
        "# Sam Altman\n[[founded::OpenAI]] and [[works_at::Y Combinator]] with @greg on #ai",
        BASE,
    )
    await _extractor(workspace_id, blob).run()

    edge_types = {row.edge_type for row in await _edges(workspace_id)}
    assert {"founded", "works_at", "mentions"} <= edge_types

    openai = await _entity(workspace_id, "openai", "company")
    assert openai is not None and openai.is_stub
    assert await _entity(workspace_id, "y combinator", "company") is not None
    assert await _entity(workspace_id, "greg", "person") is not None
    assert await _entity(workspace_id, "ai", "topic") is not None


async def test_reextract_on_unchanged_digest_is_idempotent(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    await _seed_page(blob, workspace_id, source_id, "# Notes\nabout [[Acme]]", BASE)
    await _extractor(workspace_id, blob).run()

    first = await _edges(workspace_id)
    assert len(first) == 1
    stamped = first[0].updated_at

    await ScopedStore(workspace_id=workspace_id, extension=EXTENSION).put(PAGE_CURSOR_KEY, None)
    await _extractor(workspace_id, blob).run()

    second = await _edges(workspace_id)
    assert len(second) == 1
    assert second[0].id == first[0].id
    assert second[0].updated_at == stamped


async def test_digest_change_reextracts_and_prunes_removed_refs(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    page_id = await _seed_page(
        blob, workspace_id, source_id, "# Notes\n[[Acme]] and [[Beta]]", BASE
    )
    await _extractor(workspace_id, blob).run()
    assert len(await _edges(workspace_id)) == 2

    await _revise_page(blob, page_id, "# Notes\n[[Acme]]", BASE + timedelta(seconds=1))
    await _extractor(workspace_id, blob).run()

    remaining = await _edges(workspace_id)
    acme = await _entity(workspace_id, "acme", "topic")
    assert acme is not None
    assert [row.to_entity for row in remaining] == [acme.id]


async def test_page_tombstone_soft_deletes_its_edges(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    page_id = await _seed_page(blob, workspace_id, source_id, "# Notes\nabout [[Acme]]", BASE)
    await _extractor(workspace_id, blob).run()
    assert len(await _edges(workspace_id)) == 1

    await _revise_page(blob, page_id, "", BASE + timedelta(seconds=1), tombstone=True)
    await _extractor(workspace_id, blob).run()

    assert await _edges(workspace_id) == []
    async with workspace_tx() as connection:
        live = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(graph_edge)
                .where(
                    graph_edge.c.source_page_id == page_id,
                    graph_edge.c.tombstone.is_(True),
                )
            )
        ).scalar_one()
    assert live == 1
    subgraph = await GraphStore(workspace_tx, workspace_id).traverse(
        "Notes", frozenset({SHARED_SUBJECT}), 2, frozenset()
    )
    assert subgraph.edges == ()


async def test_traverse_follows_multiple_hops_with_citations(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    await _seed_page(blob, workspace_id, source_id, "# Alice\nworks with [[Project Apollo]]", BASE)
    await _seed_page(
        blob,
        workspace_id,
        source_id,
        "# Project Apollo\nsee also [[Beta Initiative]]",
        BASE + timedelta(seconds=1),
    )
    await _extractor(workspace_id, blob).run()
    store = GraphStore(workspace_tx, workspace_id)

    two = await store.traverse("Alice", frozenset({SHARED_SUBJECT}), 2, frozenset())
    reached = {node.name for node in two.nodes}
    assert {"Alice", "Project Apollo", "Beta Initiative"} <= reached
    assert len(two.edges) == 2
    assert all(edge.source_page_id is not None for edge in two.edges)

    one = await store.traverse("Alice", frozenset({SHARED_SUBJECT}), 1, frozenset())
    one_reach = {node.name for node in one.nodes}
    assert "Project Apollo" in one_reach
    assert "Beta Initiative" not in one_reach


async def test_member_page_graph_is_invisible_to_another_member(db: None, tmp_path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    workspace_id = await _workspace()
    source_id = await _source(workspace_id)
    alice, bob = uuid4(), uuid4()
    await _seed_page(
        blob,
        workspace_id,
        source_id,
        "# Alice Notes\nabout [[Locker]]",
        BASE,
        subject=f"member:{alice}",
    )
    await _extractor(workspace_id, blob).run()
    store = GraphStore(workspace_tx, workspace_id)

    mine = await store.traverse(
        "Alice Notes", frozenset({f"member:{alice}", SHARED_SUBJECT}), 2, frozenset()
    )
    assert len(mine.edges) == 1
    theirs = await store.traverse(
        "Alice Notes", frozenset({f"member:{bob}", SHARED_SUBJECT}), 2, frozenset()
    )
    assert theirs.edges == ()


def test_parse_page_extracts_title_typed_and_plain_refs() -> None:
    parsed = parse_page(
        "# Sam Altman\n[[founded::OpenAI]] met @greg about #ai, see [[Widget|the widget]] "
        "and https://example.com/x."
    )
    assert parsed.title == "Sam Altman"
    refs = {(ref.edge_type, ref.entity_type, ref.name) for ref in parsed.refs}
    assert ("founded", "company", "OpenAI") in refs
    assert ("mentions", "person", "greg") in refs
    assert ("mentions", "topic", "ai") in refs
    assert ("mentions", "topic", "Widget") in refs
    assert ("mentions", "topic", "https://example.com/x") in refs


def test_parse_page_treats_an_unknown_typed_link_as_a_plain_mention() -> None:
    parsed = parse_page("# Doc\n[[bogus::Target]]")
    assert [(ref.edge_type, ref.name) for ref in parsed.refs] == [("mentions", "bogus::Target")]


def test_to_edge_type_rejects_a_type_outside_the_vocabulary() -> None:
    assert to_edge_type("founded") == "founded"
    with pytest.raises(UnknownEdgeType):
        to_edge_type("acquired")
