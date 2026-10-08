"""`index_pages` over the deploy index and the memory service's page mirror, and the page passages
served back from that index, fenced on the live page the sources service answers."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
from ufo_ext_memory.client import MemoryApi
from ufo_ext_memory.pages import PageIndex, PagePassages
from ufo_ext_memory.store import mem_page
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.index import StubEmbed, default_index, vec
from ufo_testsupport.memory_service import MemoryServiceStandIn
from ufo_testsupport.sources_service import SOURCES_WIRE, SourcesServiceStandIn

from ufo.db import workspace_tx
from ufo.runtime.billing.accounting import MEMORY_SERVICE, SOURCES_SERVICE
from ufo.runtime.cloud import CloudApis, CloudUnavailable
from ufo.runtime.ext.context import ExtensionContext, ScopedStore, context_for
from ufo.runtime.ext.manifest import PAGE_CHANGE_CURSOR_KEY, HookContext, PageChangeBatch
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    Chunk,
    IndexScope,
    TextChunker,
    chunk_digest,
)
from ufo.runtime.jobs import PageChangeRunner
from ufo.runtime.memory import MemoryMatch
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.pages import PageChange
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.sources_api import SourceLink, SourceLinks, SourcesFeed, SourcesService
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

GOLDEN = json.loads(SOURCES_WIRE.read_text(encoding="utf-8"))
LIVE = GOLDEN["sources.pages_read"]["answer"]["body"]["items"][0]
FED = GOLDEN["sources.changes"]["answer"]["body"]["items"][0]
SELECTED = frozenset({MEMORY_SERVICE, SOURCES_SERVICE})
BODY = "The release codename is polaris and it ships in the third quarter."
REDACTED = "The release plan has been redacted."
RENEWAL = "Northwind renews on 1 July 2026 and takes 30 days notice to cancel."
DIGEST = "sha256:live"
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)
UNAVAILABLE = {"error": {"code": "unavailable", "message": "The service is unavailable."}}


class _UnreachedEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("A page that does not reach memory is never embedded.")


async def _workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, owner_id, shared_id, private_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=owner_id,
                workspace_id=workspace_id,
                email=f"{owner_id.hex}@x.test",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="ufo",
                prompt="p",
                model="m",
                is_main=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.connection),
            [
                {
                    "id": connection_id,
                    "workspace_id": workspace_id,
                    "provider": "github",
                    "account_id": account_id,
                    "host": "",
                    "owner_member_id": owner,
                    "shared": owner is None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for connection_id, account_id, owner in (
                    (shared_id, "", None),
                    (private_id, "owned", owner_id),
                )
            ],
        )
    return workspace_id, agent_id, shared_id, private_id


def _apis(memory_service: MemoryServiceStandIn, sources: SourcesServiceStandIn) -> CloudApis:
    return cloud_apis_for(memory_service.app, sources.app, clients=SELECTED)


def _context(
    apis: CloudApis, links: dict[tuple[UUID, UUID], SourceLink | None]
) -> ExtensionContext:
    return context_for(
        memory.NAME,
        frozenset(),
        cloud_client=True,
        cloud=apis,
        sources_service=SourcesService(apis=apis, links=SourceLinks(entries=links)),
    )


def _page_index(
    apis: CloudApis,
    links: dict[tuple[UUID, UUID], SourceLink | None],
    embed: object = StubEmbed(vec((7, 1.0))),
) -> PageIndex:
    ctx = _context(apis, links)
    return PageIndex(
        index=default_index(), embed=embed, memory=MemoryApi(cloud=ctx.cloud_api()), ctx=ctx
    )


def _change(page_id: UUID, source_id: UUID, **fields: object) -> PageChange:
    change = PageChange(
        page_id=page_id,
        source_id=source_id,
        connection_id=uuid4(),
        provider="github",
        subject=SHARED_SUBJECT,
        stream="issues",
        title="Release plan",
        body=BODY,
        digest=DIGEST,
        revision=1,
        tombstone=False,
        indexed=True,
        created_at=NOW,
        as_of=NOW,
        changed_at=NOW,
    )
    return replace(change, **fields)


def _live(page_id: UUID, source_id: UUID, **fields: object) -> dict[str, object]:
    return {
        **LIVE,
        "id": str(page_id),
        "source_id": str(source_id),
        "subject": SHARED_SUBJECT,
        "digest": DIGEST,
        "revision": 1,
        **fields,
    }


def _mirrored(memory_service: MemoryServiceStandIn) -> list[object]:
    return [sent.body for sent in memory_service.sent if sent.operation == "memory.pages"]


def _reads(sources: SourcesServiceStandIn) -> list[object]:
    return [sent.body for sent in sources.sent if sent.operation == "sources.pages_read"]


def _mirror(change: PageChange) -> dict[str, object]:
    return {
        "page_id": str(change.page_id),
        "subject": change.subject,
        "revision": change.revision,
        "indexed": change.indexed,
        "tombstone": change.tombstone,
        "title": change.title,
        "provider": change.provider,
    }


async def _texts(query: str, subjects: frozenset[str]) -> list[str]:
    return [hit.text for hit in await default_index().lexical(query, subjects, OWNER_KIND_PAGE, 10)]


async def test_an_indexed_change_embeds_its_body_and_mirrors_its_state() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    sources.answer("sources.pages_read", 200, {"items": [_live(page_id, source_id, revision=3)]})
    change = _change(page_id, source_id, revision=3)

    with ws(workspace_id):
        await _page_index(_apis(memory_service, sources), links).apply((change,))
        hits = await default_index().lexical(
            "codename", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10
        )

    assert [(hit.owner_id, hit.chunk_digest) for hit in hits] == [
        (str(page_id), chunk_digest(OWNER_KIND_PAGE, str(page_id), DIGEST, 0, BODY))
    ]
    assert _reads(sources) == [{"ids": [str(page_id)]}]
    assert _mirrored(memory_service) == [{"pages": [_mirror(change)]}]


async def test_a_tombstone_drops_the_pages_chunks_and_mirrors_its_state() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    sources.answer("sources.pages_read", 200, {"items": [_live(page_id, source_id)]})
    live = _change(page_id, source_id)
    gone = replace(live, body="", digest="", revision=2, tombstone=True)

    with ws(workspace_id):
        indexer = _page_index(_apis(memory_service, sources), links)
        await default_index().upsert(
            TextChunker().chunk(
                "the retired source fact is polaris",
                OWNER_KIND_MEMORY_ITEM,
                str(uuid4()),
                SHARED_SUBJECT,
                "",
            )
        )
        await indexer.apply((live,))
        await indexer.apply((gone,))
        page_chunks = await default_index().has_chunks(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        facts = await default_index().lexical(
            "retired source fact", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 10
        )

    assert page_chunks is False
    assert len(facts) == 1
    assert len(_reads(sources)) == 1
    assert _mirrored(memory_service) == [{"pages": [_mirror(live)]}, {"pages": [_mirror(gone)]}]


async def test_an_unindexed_stream_embeds_nothing_drops_the_page_and_mirrors_it() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    sources.answer("sources.pages_read", 200, {"items": [_live(page_id, source_id)]})
    apis = _apis(memory_service, sources)
    live = _change(page_id, source_id, stream="workflow_runs")
    unreached = replace(live, indexed=False, revision=2)

    with ws(workspace_id):
        await _page_index(apis, links).apply((live,))
        before = await default_index().has_chunks(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        await _page_index(apis, links, _UnreachedEmbed()).apply((unreached,))
        after = await default_index().has_chunks(IndexScope(OWNER_KIND_PAGE, str(page_id)))

    assert (before, after) == (True, False)
    assert len(_reads(sources)) == 1
    assert _mirrored(memory_service)[-1] == {"pages": [_mirror(unreached)]}


async def test_a_new_revision_prunes_the_older_revisions_chunks() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    sources.queue(
        "sources.pages_read",
        [
            (200, {"items": [_live(page_id, source_id)]}),
            (200, {"items": [_live(page_id, source_id, digest="sha256:redacted", revision=2)]}),
        ],
    )
    older = _change(page_id, source_id)
    newer = replace(older, body=REDACTED, digest="sha256:redacted", revision=2)

    with ws(workspace_id):
        indexer = _page_index(_apis(memory_service, sources), links)
        await indexer.apply((older,))
        held = await _texts("polaris", frozenset({SHARED_SUBJECT}))
        await indexer.apply((newer,))
        pruned = await _texts("polaris", frozenset({SHARED_SUBJECT}))
        hits = await default_index().lexical(
            "redacted", frozenset({SHARED_SUBJECT}), OWNER_KIND_PAGE, 10
        )

    assert (held, pruned) == ([BODY], [])
    assert [hit.chunk_digest for hit in hits] == [
        chunk_digest(OWNER_KIND_PAGE, str(page_id), "sha256:redacted", 0, REDACTED)
    ]


async def test_a_subject_moved_during_the_embed_withdraws_the_fresh_chunks() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id, member_id = uuid4(), uuid4(), uuid4()
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    both = frozenset({SHARED_SUBJECT, member_subject(member_id)})
    sources.queue(
        "sources.pages_read",
        [
            (200, {"items": [_live(page_id, source_id, subject=member_subject(member_id))]}),
            (200, {"items": [_live(page_id, source_id, digest="sha256:redacted", revision=2)]}),
            (200, {"items": [_live(page_id, source_id, digest="sha256:redacted", revision=2)]}),
        ],
    )
    reclassified = _change(page_id, source_id)
    stale = _change(page_id, source_id, subject=member_subject(member_id), digest="sha256:private")
    sanitized = replace(reclassified, body=REDACTED, digest="sha256:redacted", revision=2)

    with ws(workspace_id):
        indexer = _page_index(_apis(memory_service, sources), links)
        await indexer.apply((reclassified,))
        withdrawn = await default_index().has_chunks(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        await indexer.apply((stale,))
        stale_held = await _texts("polaris", both)
        await indexer.apply((sanitized,))
        polaris = await _texts("polaris", both)
        redacted = await _texts("redacted", frozenset({SHARED_SUBJECT}))

    assert withdrawn is False
    assert stale_held == []
    assert (polaris, redacted) == ([], [REDACTED])
    assert _mirrored(memory_service) == [
        {"pages": [_mirror(change)]} for change in (reclassified, stale, sanitized)
    ]


async def test_a_memory_service_outage_fails_the_batch() -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = UUID(FED["page_id"]), UUID(FED["source_id"])
    memory_service.answer("memory.pages", 503, UNAVAILABLE)
    sources.queue(
        "sources.changes",
        [(200, {"items": [FED], "next_cursor": f"{FED['revision']}|{page_id}"})],
    )
    sources.answer(
        "sources.pages_read",
        200,
        {"items": [_live(page_id, source_id, digest=FED["digest"], revision=FED["revision"])]},
    )
    apis = _apis(memory_service, sources)
    service = SourcesService(
        apis=apis,
        links=SourceLinks(
            entries={
                (workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")
            }
        ),
    )
    runner = PageChangeRunner(
        manifests=(memory.manifest(),),
        pages=SourcesFeed(apis=apis, links=service.links),
        index=default_index(),
        embed=StubEmbed(vec((7, 1.0))),
        cloud=apis,
        sources_service=service,
    )
    (consumer,) = (
        consumer
        for consumer in runner.consumers()
        if consumer.discriminator == memory.index_pages.__name__
    )

    with ws(workspace_id):
        with pytest.raises(CloudUnavailable):
            await runner.drive(consumer)
        cursor = await ScopedStore(extension=memory.NAME).get(
            f"{PAGE_CHANGE_CURSOR_KEY}:{memory.index_pages.__name__}"
        )

    assert cursor is None
    assert [body["pages"][0]["page_id"] for body in _mirrored(memory_service)] == [str(page_id)]


@pytest.mark.parametrize("cloudless", [False, True])
async def test_index_pages_keeps_the_core_mirror_where_memory_is_not_selected(
    cloudless: bool,
) -> None:
    workspace_id, _, shared_id, _ = await _workspace()
    memory_service, sources = MemoryServiceStandIn(), SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend="github",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=shared_id,
                next_sync_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest=DIGEST,
                body_ref=f"pages/{page_id}",
                stream="issues",
                title="Release plan",
                subject=SHARED_SUBJECT,
                tombstone=False,
                indexed=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
    ctx = context_for(
        memory.NAME,
        frozenset(),
        default_index(),
        StubEmbed(vec((7, 1.0))),
        cloud_client=True,
        cloud=None if cloudless else cloud_apis_for(memory_service.app, sources.app),
    )

    with ws(workspace_id):
        await memory.index_pages(
            HookContext(
                ext=ctx,
                payload=PageChangeBatch(changes=(_change(page_id, source_id, revision=revision),)),
            )
        )
    async with workspace_tx() as connection:
        mirrored = (
            (
                await connection.execute(
                    sa.select(mem_page.c.page_uid).where(mem_page.c.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )

    assert mirrored == [page_id]
    assert memory_service.sent == []


async def _passage(
    page_id: UUID, digest: str, text: str, ordinal: int = 0, subject: str = SHARED_SUBJECT
) -> None:
    await default_index().upsert(
        (
            Chunk(
                chunk_digest=chunk_digest(OWNER_KIND_PAGE, str(page_id), digest, ordinal, text),
                owner_kind=OWNER_KIND_PAGE,
                owner_id=str(page_id),
                subject=subject,
                ordinal=ordinal,
                text=text,
            ),
        )
    )


def _passages(
    sources: SourcesServiceStandIn, links: dict[tuple[UUID, UUID], SourceLink | None]
) -> PagePassages:
    return PagePassages(
        index=default_index(),
        embed=None,
        ctx=_context(_apis(MemoryServiceStandIn(), sources), links),
    )


def _main_reader(agent_id: UUID) -> SourceReader:
    return SourceReader(
        agent_id=agent_id, requesting_member_id=None, subjects=frozenset({SHARED_SUBJECT})
    )


async def test_page_passages_fence_on_reach_subject_and_digest() -> None:
    workspace_id, agent_id, shared_id, private_id = await _workspace()
    sources = SourcesServiceStandIn()
    readable, unreached, moved, edited = (uuid4() for _ in range(4))
    shared_source, private_source = uuid4(), uuid4()
    sources.answer(
        "sources.pages_read",
        200,
        {
            "items": [
                _live(readable, shared_source, title="Order form"),
                _live(unreached, private_source),
                _live(moved, shared_source, subject=member_subject(uuid4())),
                _live(edited, shared_source, digest="sha256:edited", revision=2),
            ]
        },
    )
    links = {
        (workspace_id, shared_source): SourceLink(connection_id=shared_id, provider="github"),
        (workspace_id, private_source): SourceLink(connection_id=private_id, provider="github"),
    }

    with ws(workspace_id):
        for page_id in (readable, unreached, moved, edited):
            await _passage(page_id, DIGEST, RENEWAL)
        found = await _passages(sources, links).search(
            ("Northwind renews",), _main_reader(agent_id), 8
        )

    assert found == (
        MemoryMatch(
            kind="source",
            text=RENEWAL,
            ref=ObjectRef(kind="page", name=str(readable)),
            created_at=NOW,
            subject=SHARED_SUBJECT,
            page_provider=LIVE["provider"],
            page_title="Order form",
            created_from_page_id=readable,
        ),
    )
    assert len(_reads(sources)) == 1


async def test_page_passages_keep_start_and_end() -> None:
    workspace_id, agent_id, shared_id, _ = await _workspace()
    sources = SourcesServiceStandIn()
    september, october, source_id = uuid4(), uuid4(), uuid4()
    sources.answer(
        "sources.pages_read",
        200,
        {
            "items": [
                _live(september, source_id, created_at="2026-09-01T09:00:00Z"),
                _live(october, source_id, created_at="2026-10-01T09:00:00Z"),
            ]
        },
    )
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}
    cut = datetime(2026, 9, 15, tzinfo=UTC)

    with ws(workspace_id):
        for page_id in (september, october):
            await _passage(page_id, DIGEST, RENEWAL)
        passages = _passages(sources, links)
        reader = _main_reader(agent_id)
        after = await passages.search(("Northwind renews",), reader, 8, start=cut)
        before = await passages.search(("Northwind renews",), reader, 8, end=cut)

    assert [match.created_from_page_id for match in after] == [october]
    assert [match.created_from_page_id for match in before] == [september]


async def test_page_passages_keep_each_queries_passage_of_one_document() -> None:
    workspace_id, agent_id, shared_id, _ = await _workspace()
    sources = SourcesServiceStandIn()
    page_id, source_id = uuid4(), uuid4()
    putaway = "13.4 Putaway authorisation needs a supervisor."
    template = "Template 24 is the post format."
    sources.answer("sources.pages_read", 200, {"items": [_live(page_id, source_id)]})
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}

    with ws(workspace_id):
        await _passage(page_id, DIGEST, putaway)
        await _passage(page_id, DIGEST, template, ordinal=1)
        found = await _passages(sources, links).search(
            ("putaway", "template", "post"), _main_reader(agent_id), 8
        )

    assert [match.text for match in found] == [putaway, template]
