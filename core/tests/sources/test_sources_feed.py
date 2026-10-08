"""The page-change runner over the sources service's feed, through the sources stand-in: what the
runner sends, how the answer parses into page changes, and where the cursor stands after."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sample.spend import SampleGate, allow
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.sources_service import SOURCES_WIRE, SourcesServiceStandIn

from ufo.db import workspace_tx
from ufo.runtime.billing.spend import NO_SPEND_GATES, GateDeploy, SpendGates
from ufo.runtime.cloud import CloudUnavailable
from ufo.runtime.ext.context import CORE_EXTENSION, ScopedStore
from ufo.runtime.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY,
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
    PageChangeBatch,
)
from ufo.runtime.jobs import PageChangeConsumer, PageChangeRunner
from ufo.runtime.knowledge_import import IMPORT_SOURCES_KEY, ImportCursor
from ufo.runtime.pages import PageChange
from ufo.runtime.sources_api import SourceLinks, SourcesFeed
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

GOLDEN = json.loads(SOURCES_WIRE.read_text(encoding="utf-8"))
CHANGES = GOLDEN["sources.changes"]
SOURCE = GOLDEN["sources.get"]["answer"]["body"]
CURSOR_KEY = f"{PAGE_CHANGE_CURSOR_KEY}:index_pages"
CORE_CONNECTION = UUID(SOURCE["labels"]["connection"])
SAMPLE_SPEND = SpendGates(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))
DELIVERED: list[tuple[PageChange, ...]] = []
SEEN_CURSORS: list[object] = []


async def index_pages(ctx: HookContext) -> HookOutcome:
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            SEEN_CURSORS.append(await ctx.ext.store.get(CURSOR_KEY))
            DELIVERED.append(changes)
    return None


INDEXER = Manifest(
    name="indexer_ext",
    version="0",
    hooks=(HookSpec(event="page_change", handler=index_pages),),
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


async def _connect(workspace_id: UUID) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    provider="github",
                    account_id="",
                    host="",
                    owner_member_id=None,
                    shared=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


async def _imported(workspace_id: UUID, done: bool = True) -> None:
    with ws(workspace_id):
        await ScopedStore(extension=CORE_EXTENSION).put(
            IMPORT_SOURCES_KEY, ImportCursor(done=done).model_dump(mode="json")
        )


def _rig(
    spend: SpendGates = NO_SPEND_GATES,
) -> tuple[SourcesServiceStandIn, PageChangeRunner, PageChangeConsumer]:
    DELIVERED.clear()
    SEEN_CURSORS.clear()
    stand_in = SourcesServiceStandIn()
    feed = SourcesFeed(apis=cloud_apis_for(stand_in.app), links=SourceLinks(entries={}))
    runner = PageChangeRunner(manifests=(INDEXER,), pages=feed, spend=spend)
    (consumer,) = runner.consumers()
    return stand_in, runner, consumer


def _item(revision: int, source_id: str = SOURCE["id"]) -> dict[str, object]:
    page_id = UUID(int=revision)
    return {
        **CHANGES["answer"]["body"]["items"][0],
        "page_id": str(page_id),
        "source_id": source_id,
        "revision": revision,
    }


def _answer(*items: dict[str, object]) -> tuple[int, object]:
    cursor = None if not items else f"{items[-1]['revision']}|{items[-1]['page_id']}"
    return 200, {"items": list(items), "next_cursor": cursor}


def _sent_queries(stand_in: SourcesServiceStandIn) -> list[tuple[tuple[str, str], ...]]:
    return [sent.query for sent in stand_in.sent if sent.operation == "sources.changes"]


async def _drive(
    workspace_id: UUID, runner: PageChangeRunner, consumer: PageChangeConsumer
) -> None:
    with ws(workspace_id):
        await runner.drive(consumer)


async def _stored(workspace_id: UUID) -> object:
    with ws(workspace_id):
        return await ScopedStore(extension=INDEXER.name).get(CURSOR_KEY)


async def test_a_stored_cursor_is_sent_unchanged() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()
    stored = CHANGES["query"][0][1]
    with ws(workspace_id):
        await ScopedStore(extension=INDEXER.name).put(CURSOR_KEY, stored)

    await _drive(workspace_id, runner, consumer)

    assert _sent_queries(stand_in) == [tuple(tuple(pair) for pair in CHANGES["query"])]
    assert _sent_queries(stand_in)[0] == (("cursor", stored), ("limit", "50"))


async def test_a_first_drive_sends_no_cursor() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()

    await _drive(workspace_id, runner, consumer)

    assert _sent_queries(stand_in) == [(("limit", "50"),)]


async def test_changes_parse_into_page_changes() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()

    await _drive(workspace_id, runner, consumer)

    (live, gone), *_ = DELIVERED
    assert live == PageChange(
        page_id=UUID("6f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f"),
        source_id=UUID(SOURCE["id"]),
        connection_id=CORE_CONNECTION,
        provider="github",
        subject="shared",
        stream="issues",
        title="Importer drops the last page",
        body="The importer drops the last page of a 200-row export.",
        digest="sha256:9c0e36118fb8e1962476591d02f0bfd2a6648807cd82d25bcdcdacbff26d969f",
        revision=413,
        tombstone=False,
        indexed=True,
        created_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        as_of=datetime(2026, 10, 1, 9, tzinfo=UTC),
        changed_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
    )
    assert (gone.page_id, gone.tombstone, gone.body, gone.revision, gone.connection_id) == (
        UUID("4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f7a"),
        True,
        "",
        414,
        CORE_CONNECTION,
    )
    assert all(
        moment.tzinfo is not None
        for change in (live, gone)
        for moment in (change.created_at, change.as_of, change.changed_at)
    )
    assert [sent.path for sent in stand_in.sent if sent.operation == "sources.get"] == [
        f"/v1/sources/{SOURCE['id']}"
    ]


async def test_a_sources_link_is_fetched_once() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()
    stand_in.queue("sources.changes", [_answer(_item(1)), _answer(_item(2))])

    await _drive(workspace_id, runner, consumer)
    await _drive(workspace_id, runner, consumer)

    assert [[change.revision for change in batch] for batch in DELIVERED] == [[1], [2]]
    assert [sent.operation for sent in stand_in.sent].count("sources.get") == 1


async def test_a_batch_of_unlinked_items_advances_the_cursor() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()
    stand_in.answer("sources.get", 200, {**SOURCE, "labels": {}})
    stand_in.queue(
        "sources.changes",
        [_answer(_item(1)), _answer(_item(2)), _answer(_item(3)), _answer()],
    )

    await _drive(workspace_id, runner, consumer)

    assert DELIVERED == []
    assert await _stored(workspace_id) == f"3|{UUID(int=3)}"
    assert [query[0] for query in _sent_queries(stand_in)] == [
        ("limit", "50"),
        ("cursor", f"1|{UUID(int=1)}"),
        ("cursor", f"2|{UUID(int=2)}"),
        ("cursor", f"3|{UUID(int=3)}"),
    ]
    assert [sent.operation for sent in stand_in.sent].count("sources.get") == 1


async def test_the_cursor_advances_to_next_cursor_after_the_handler() -> None:
    workspace_id = await _workspace()
    _stand_in, runner, consumer = _rig()

    await _drive(workspace_id, runner, consumer)

    assert SEEN_CURSORS == [None]
    assert await _stored(workspace_id) == CHANGES["answer"]["body"]["next_cursor"]


async def test_a_null_next_cursor_holds_the_cursor() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()
    stored = f"7|{UUID(int=7)}"
    with ws(workspace_id):
        await ScopedStore(extension=INDEXER.name).put(CURSOR_KEY, stored)
    stand_in.queue("sources.changes", [_answer()])

    await _drive(workspace_id, runner, consumer)

    assert DELIVERED == []
    assert await _stored(workspace_id) == stored


async def test_a_5xx_holds_the_cursor() -> None:
    workspace_id = await _workspace()
    stand_in, runner, consumer = _rig()
    stored = f"7|{UUID(int=7)}"
    with ws(workspace_id):
        await ScopedStore(extension=INDEXER.name).put(CURSOR_KEY, stored)
    stand_in.queue(
        "sources.changes",
        [(503, {"error": {"code": "unavailable", "message": "The store is unavailable."}})],
    )

    with pytest.raises(CloudUnavailable):
        await _drive(workspace_id, runner, consumer)

    assert DELIVERED == []
    assert await _stored(workspace_id) == stored


async def test_candidates_are_the_connected_admitted_imported_workspaces() -> None:
    connected, unconnected, held, importing = [await _workspace() for _ in range(4)]
    for workspace_id in (connected, held, importing):
        await _connect(workspace_id)
    for workspace_id in (connected, unconnected, held):
        await _imported(workspace_id)
    await _imported(importing, done=False)
    with ws(held):
        async with workspace_tx() as connection:
            await allow(connection, held, 0, "reject")
    for workspace_id in (connected, unconnected, importing):
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await allow(connection, workspace_id, 5_000_000, "reject")
    _stand_in, runner, consumer = _rig(spend=SAMPLE_SPEND)

    candidates = await runner.workspaces_with_changes(consumer)

    assert set(candidates) & {connected, unconnected, held, importing} == {connected}
