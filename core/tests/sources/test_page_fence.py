"""Live page state through the sources stand-in: what `page_states` sends and answers, and how
`readable_page_states` fences a page on its subject and its core connection's reach."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_rag.pages import PageStore
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.index import default_index
from ufo_testsupport.sources_service import SOURCES_WIRE, SourcesServiceStandIn

from ufo.db import workspace_tx
from ufo.runtime.ext.context import CORE_EXTENSION, ExtensionContext, PageState, context_for
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.indexing import OWNER_KIND_PAGE, TextChunker
from ufo.runtime.sources_api import PAGES_READ_MAX, SourceLink, SourceLinks, SourcesService
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

GOLDEN = json.loads(SOURCES_WIRE.read_text(encoding="utf-8"))
PAGE = GOLDEN["sources.pages_read"]["answer"]["body"]["items"][0]
SOURCE = GOLDEN["sources.get"]["answer"]["body"]
RENEWAL = "Northwind renews on 1 July 2026 and takes 30 days notice to cancel."


async def _workspace() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, owner_id, shared_id, private_id = (uuid4() for _ in range(5))
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=owner_id,
                workspace_id=workspace_id,
                email=f"{owner_id.hex}@x.test",
                created_at=now,
                updated_at=now,
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
                created_at=now,
                updated_at=now,
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
                    "created_at": now,
                    "updated_at": now,
                }
                for connection_id, account_id, owner in (
                    (shared_id, "", None),
                    (private_id, "owned", owner_id),
                )
            ],
        )
    return workspace_id, agent_id, shared_id, private_id


def _context(
    stand_in: SourcesServiceStandIn, links: dict[tuple[UUID, UUID], SourceLink | None]
) -> ExtensionContext:
    return context_for(
        CORE_EXTENSION,
        frozenset(),
        sources_service=SourcesService(
            apis=cloud_apis_for(stand_in.app), links=SourceLinks(entries=links)
        ),
    )


def _page(page_id: UUID, source_id: UUID, **fields: object) -> dict[str, object]:
    return {**PAGE, "id": str(page_id), "source_id": str(source_id), **fields}


def _reader(agent_id: UUID, subjects: frozenset[str] = frozenset({SHARED_SUBJECT})) -> SourceReader:
    return SourceReader(agent_id=agent_id, requesting_member_id=None, subjects=subjects)


async def test_page_states_read_live_pages_from_the_sources_service() -> None:
    workspace_id, *_ = await _workspace()
    stand_in = SourcesServiceStandIn()
    page_id, gone_id = UUID(PAGE["id"]), uuid4()

    with ws(workspace_id):
        states = await _context(stand_in, {}).page_states((page_id, gone_id))

    read = [sent for sent in stand_in.sent if sent.operation == "sources.pages_read"]
    assert [sent.body for sent in read] == [{"ids": [str(page_id), str(gone_id)]}]
    assert states == {
        page_id: PageState(
            subject=PAGE["subject"],
            revision=PAGE["revision"],
            digest=PAGE["digest"],
            title=PAGE["title"],
            stream=PAGE["stream"],
            indexed=PAGE["indexed"],
            as_of="2026-10-01",
            backend=PAGE["provider"],
            source_id=UUID(PAGE["source_id"]),
            connection_id=UUID(SOURCE["labels"]["connection"]),
            created_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        )
    }


async def test_two_hundred_and_one_ids_read_in_two_calls() -> None:
    workspace_id, *_ = await _workspace()
    stand_in = SourcesServiceStandIn()
    stand_in.answer("sources.pages_read", 200, {"items": []})
    page_ids = tuple(uuid4() for _ in range(PAGES_READ_MAX + 1))

    with ws(workspace_id):
        assert await _context(stand_in, {}).page_states(page_ids) == {}

    bodies = [sent.body for sent in stand_in.sent if sent.operation == "sources.pages_read"]
    assert sorted(len(body["ids"]) for body in bodies) == [1, PAGES_READ_MAX]
    assert sorted(i for body in bodies for i in body["ids"]) == sorted(map(str, page_ids))


async def test_a_page_of_an_unreachable_connection_is_dropped() -> None:
    workspace_id, agent_id, shared_id, private_id = await _workspace()
    stand_in = SourcesServiceStandIn()
    reached, unreached = uuid4(), uuid4()
    shared_source, private_source = uuid4(), uuid4()
    stand_in.answer(
        "sources.pages_read",
        200,
        {"items": [_page(reached, shared_source), _page(unreached, private_source)]},
    )
    links = {
        (workspace_id, shared_source): SourceLink(connection_id=shared_id, provider="github"),
        (workspace_id, private_source): SourceLink(connection_id=private_id, provider="github"),
    }

    with ws(workspace_id):
        states = await _context(stand_in, links).readable_page_states(
            (reached, unreached), _reader(agent_id)
        )

    assert set(states) == {reached}
    assert states[reached].connection_id == shared_id


async def test_a_page_outside_the_readers_subjects_is_dropped() -> None:
    workspace_id, agent_id, shared_id, _ = await _workspace()
    stand_in = SourcesServiceStandIn()
    shared_page, member_page, source_id = uuid4(), uuid4(), uuid4()
    stand_in.answer(
        "sources.pages_read",
        200,
        {
            "items": [
                _page(shared_page, source_id),
                _page(member_page, source_id, subject=member_subject(uuid4())),
            ]
        },
    )
    links = {(workspace_id, source_id): SourceLink(connection_id=shared_id, provider="github")}

    with ws(workspace_id):
        states = await _context(stand_in, links).readable_page_states(
            (shared_page, member_page), _reader(agent_id)
        )

    assert set(states) == {shared_page}


async def test_a_source_without_a_connection_label_reads_as_unreadable() -> None:
    workspace_id, agent_id, *_ = await _workspace()
    stand_in = SourcesServiceStandIn()
    stand_in.answer("sources.get", 200, {**SOURCE, "labels": {}})
    page_id = UUID(PAGE["id"])

    with ws(workspace_id):
        context = _context(stand_in, {})
        assert await context.page_states((page_id,)) == {}
        assert await context.readable_page_states((page_id,), _reader(agent_id)) == {}


async def test_rag_drops_an_unreadable_page_before_its_limit() -> None:
    workspace_id, agent_id, shared_id, private_id = await _workspace()
    stand_in = SourcesServiceStandIn()
    readable, unreadable = uuid4(), uuid4()
    shared_source, private_source = uuid4(), uuid4()
    digest = "sha256:renewal"
    stand_in.answer(
        "sources.pages_read",
        200,
        {
            "items": [
                _page(readable, shared_source, digest=digest, title="Order form"),
                _page(unreadable, private_source, digest=digest, title="Board pack"),
            ]
        },
    )
    links = {
        (workspace_id, shared_source): SourceLink(connection_id=shared_id, provider="github"),
        (workspace_id, private_source): SourceLink(connection_id=private_id, provider="github"),
    }
    index = default_index()
    reader = _reader(agent_id)

    with ws(workspace_id):
        for page_id in (readable, unreadable):
            await index.upsert(
                TextChunker().chunk(RENEWAL, OWNER_KIND_PAGE, str(page_id), SHARED_SUBJECT, digest)
            )
        store = PageStore(
            index=index,
            embed=None,
            readable=_context(stand_in, links).readable_page_states,
        )
        hits = await store.hits("Northwind renews", reader, 2)

    assert [hit.page_id for hit in hits] == [readable]
