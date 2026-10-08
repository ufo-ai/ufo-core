import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
from ufo_ext_memory.objects import (
    MEMORY_LIST_MAX,
    MEMORY_OBJECT,
    MEMORY_UNDELETABLE,
    MEMORY_UPDATE_REFUSAL,
    MemoryObjects,
    MemorySpec,
)
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.memory_service import MEMORY_WIRE, MemoryServiceStandIn
from ufo_testsupport.service_stand_in import SentRequest

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent
from ufo.runtime.billing.accounting import MEMORY_SERVICE
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.listings import ListingCursor
from ufo.runtime.objects import ObjectListQuery, VerbNotSupported
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.objects import ObjectLink, ObjectRef

GOLDEN: dict[str, dict] = json.loads(MEMORY_WIRE.read_text(encoding="utf-8"))
LISTED: dict = GOLDEN["memory.list"]["answer"]["body"]
ITEM: dict = LISTED["items"][0]
GOT: dict = GOLDEN["memory.get"]["answer"]["body"]
SELECTED = frozenset({MEMORY_SERVICE})
CREATED = datetime(2026, 10, 7, 12, tzinfo=UTC)
NEXT = "older|2026-10-07T12:00:00+00:00|0192f0c6-2b3c-7d4e-9f5a-6b7c8d9e0f1a"
PREV = "newer|2026-10-07T12:30:00+00:00|0192f0d0-4e5f-7a6b-8c7d-8e9f0a1b2c3d"


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the memory object tests")


def _ext(stand_in: MemoryServiceStandIn) -> ExtensionContext:
    return context_for(
        memory.NAME,
        frozenset(),
        cloud_client=True,
        cloud=cloud_apis_for(stand_in.app, clients=SELECTED),
    )


def _ctx(stand_in: MemoryServiceStandIn, tmp_path: Path, workspace_id: UUID) -> ToolContext:
    member = uuid4()
    audience = conversation_audience(member)
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member,
        audience=audience,
        artifact_token_secret="",
        ext=_ext(stand_in),
    )


def _item(
    body: str,
    *,
    kind: str = "fact",
    item_class: str = "fact",
    subject: str = SHARED_SUBJECT,
    minute: int = 0,
    sources: list[dict] | None = None,
) -> dict:
    return ITEM | {
        "id": str(uuid4()),
        "body": body,
        "kind": kind,
        "item_class": item_class,
        "subject": subject,
        "created_at": (CREATED - timedelta(minutes=minute)).isoformat(),
        "sources": [] if sources is None else sources,
    }


def _query(**filters: str) -> ObjectListQuery:
    return ObjectListQuery(
        filters=filters,
        order_by="written",
        order="desc",
        supported_fields=MEMORY_OBJECT.list_fields,
    )


def _sent(stand_in: MemoryServiceStandIn, operation: str) -> list[SentRequest]:
    return [sent for sent in stand_in.sent if sent.operation == operation]


def _reach(reader_agent: UUID, member: UUID | None) -> list[tuple[str, str]]:
    members = [] if member is None else [("reach.member_id", str(member))]
    return [("reach.agent_id", str(reader_agent)), *members]


def _subjects(subjects: frozenset[str]) -> list[tuple[str, str]]:
    return [("subject", subject) for subject in sorted(subjects)]


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _main_agent(workspace_id: UUID, agent_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=f"agent-{agent_id.hex[:8]}",
                prompt="p",
                model="m",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _shared_page(workspace_id: UUID) -> UUID:
    connection_id, source_id, page_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="notion",
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
                backend="notion",
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
                digest=f"sha256:{page_id.hex}",
                body_ref=f"pages/{page_id}",
                stream="docs",
                title="Q3 plan",
                subject=SHARED_SUBJECT,
                tombstone=False,
                indexed=True,
                created_at=now,
                updated_at=now,
            )
        )
    return page_id


async def test_list_sends_the_readers_subjects_reach_and_filters(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    stand_in = MemoryServiceStandIn()
    decision = _item("The importer ships in October.", kind="decision")
    stand_in.answer(
        "memory.list",
        200,
        {
            "items": [decision, _item("The office stocks green tea.", minute=1)],
            "next_cursor": None,
            "prev_cursor": None,
        },
    )
    ctx = _ctx(stand_in, tmp_path, workspace_id)
    reader = ctx.source_reader()

    with ws(workspace_id):
        page = await MEMORY_OBJECT.store.list(
            ctx, _query(item_class="fact", memory_kind="decision")
        )

    (sent,) = _sent(stand_in, "memory.list")
    assert sent.query == (
        *_subjects(reader.subjects),
        ("kind", "decision"),
        ("item_class", "fact"),
        ("limit", str(200)),
        *_reach(reader.agent_id, reader.requesting_member_id),
    )
    assert [(row.name, row.fields["memory_kind"]) for row in page.rows] == [
        (decision["id"], "decision")
    ]
    assert page.rows[0].fields["text"] == "The importer ships in October."
    assert page.rows[0].fields["written"] == CREATED.isoformat()


async def test_list_pages_to_five_hundred(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    stand_in = MemoryServiceStandIn()
    cursors = [f"older|2026-10-07T12:00:00+00:00|{uuid4()}" for _ in range(3)]
    stand_in.queue(
        "memory.list",
        [
            (
                200,
                {
                    "items": [_item(f"fact {page} {n}") for n in range(200)],
                    "next_cursor": cursor,
                    "prev_cursor": None,
                },
            )
            for page, cursor in enumerate(cursors)
        ],
    )
    ctx = _ctx(stand_in, tmp_path, workspace_id)

    with ws(workspace_id):
        listed = await MEMORY_OBJECT.store.list(ctx, _query())

    sent = [dict(call.query) for call in _sent(stand_in, "memory.list")]
    assert [call["limit"] for call in sent] == ["200", "200", "100"]
    assert [call.get("cursor") for call in sent] == [None, *cursors[:2]]
    assert MEMORY_LIST_MAX == 500
    assert listed.next_cursor is not None


async def test_get_answers_a_superseded_row_with_its_links(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    stand_in = MemoryServiceStandIn()
    head = uuid4()
    stand_in.answer(
        "memory.get",
        200,
        GOT | {"invalidated_by": str(head), "invalid_at": "2026-10-07T13:00:00Z"},
    )
    ctx = _ctx(stand_in, tmp_path, workspace_id)

    with ws(workspace_id):
        detail = await MEMORY_OBJECT.store.get(ctx, GOT["id"])

    assert detail is not None
    assert detail.spec == MemorySpec(
        body=GOT["body"],
        subject=GOT["subject"],
        item_class=GOT["item_class"],
        memory_kind=GOT["kind"],
        confidence=GOT["confidence"],
        source_ref=GOT["source_ref"],
        as_of=datetime.fromisoformat(GOT["as_of"]).isoformat(),
    )
    assert detail.created_at == datetime.fromisoformat(GOT["created_at"])
    assert detail.links == (
        ObjectLink(
            relation="created_from",
            target=ObjectRef(kind="page", name=GOT["sources"][0]["page_id"]),
        ),
        ObjectLink(
            relation="superseded_by", target=ObjectRef(kind="memory", name=GOT["superseded_by"])
        ),
        ObjectLink(relation="overtaken_by", target=ObjectRef(kind="memory", name=str(head))),
    )


async def test_get_sends_the_readers_subjects_and_reach(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.get", 404, {"error": {"code": "not_found", "message": "No memory."}})
    ctx = _ctx(stand_in, tmp_path, workspace_id)
    reader = ctx.source_reader()
    wanted = uuid4()

    with ws(workspace_id):
        detail = await MEMORY_OBJECT.store.get(ctx, str(wanted))
        unnamed = await MEMORY_OBJECT.store.get(ctx, "not-an-id")

    (sent,) = _sent(stand_in, "memory.get")
    assert sent.path == f"/v1/memory/memories/{wanted}"
    assert sent.query == (
        *_subjects(reader.subjects),
        *_reach(reader.agent_id, reader.requesting_member_id),
    )
    assert (detail, unnamed) == (None, None)


async def test_member_detail_reads_as_the_member_under_the_bound_agent(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.get", 200, GOT | {"sources": []})
    member, agent_id = uuid4(), uuid4()

    with ws(workspace_id), agent(agent_id):
        found = await MemoryObjects().member_detail(
            _ext(stand_in), GOT["id"], member_id=member, admin=False
        )

    (sent,) = _sent(stand_in, "memory.get")
    assert sent.query == (
        *_subjects(frozenset({SHARED_SUBJECT, member_subject(member)})),
        *_reach(agent_id, member),
    )
    assert found is not None
    assert found.row.name == GOT["id"]
    assert found.row.fields["created_from_page_id"] is None


async def test_list_recent_maps_cursors_both_ways() -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer(
        "memory.list",
        200,
        {
            "items": [_item("The office stocks green tea.")],
            "next_cursor": NEXT,
            "prev_cursor": PREV,
        },
    )
    cursor = ListingCursor(created_at=CREATED, item_id=str(uuid4()))
    subjects = frozenset({SHARED_SUBJECT})

    with ws(uuid4()):
        page = await memory.MemorySearchService(_ext(stand_in)).list_recent(
            subjects, 50, frozenset({"fact"}), cursor
        )

    (sent,) = _sent(stand_in, "memory.list")
    assert sent.query == (
        ("subject", SHARED_SUBJECT),
        ("item_class", "fact"),
        ("cursor", cursor.encode()),
        ("limit", "50"),
    )
    assert page.older == ListingCursor.decode(NEXT)
    assert page.newer == ListingCursor.decode(PREV)
    assert [row.text for row in page.rows] == ["The office stocks green tea."]


async def test_list_recent_asks_nothing_for_no_subject_or_no_kind() -> None:
    stand_in = MemoryServiceStandIn()
    service = memory.MemorySearchService(_ext(stand_in))

    with ws(uuid4()):
        unsubjected = await service.list_recent(frozenset(), 50)
        unkinded = await service.list_recent(frozenset({SHARED_SUBJECT}), 50, frozenset())

    assert (unsubjected.rows, unkinded.rows) == ((), ())
    assert stand_in.sent == []


async def test_list_recent_names_a_page_only_a_reader_may_read(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    page_id = await _shared_page(workspace_id)
    stand_in = MemoryServiceStandIn()
    stand_in.answer(
        "memory.list",
        200,
        {
            "items": [
                _item(
                    "The Q3 plan ships the importer.",
                    sources=[
                        {
                            "kind": "page",
                            "page_id": str(page_id),
                            "title": "Q3 plan",
                            "provider": "notion",
                        }
                    ],
                )
            ],
            "next_cursor": None,
            "prev_cursor": None,
        },
    )
    ctx = _ctx(stand_in, tmp_path, workspace_id)
    await _main_agent(workspace_id, ctx.turn.agent_id)
    reader = ctx.source_reader()
    stranger = replace(reader, agent_id=uuid4())
    service = memory.MemorySearchService(ctx.ext)
    subjects = frozenset({SHARED_SUBJECT})

    with ws(workspace_id):
        owned = await service.list_recent(subjects, 10, readers=(reader,))
        sealed = await service.list_recent(subjects, 10, readers=(stranger,))
        unread = await service.list_recent(subjects, 10)

    assert [
        (row.page_provider, row.page_title, row.created_from_page_id) for row in owned.rows
    ] == [("notion", "Q3 plan", page_id)]
    assert [(row.page_provider, row.created_from_page_id) for row in sealed.rows] == [(None, None)]
    assert [(row.page_provider, row.created_from_page_id) for row in unread.rows] == [(None, None)]
    assert all(("reach.agent_id" not in dict(sent.query)) for sent in stand_in.sent)


async def test_list_recent_quotes_the_head_of_an_overtaken_row() -> None:
    stand_in = MemoryServiceStandIn()
    head = uuid4()
    stand_in.answer(
        "memory.list",
        200,
        {
            "items": [
                _item("The importer ships in September.")
                | {"invalidated_by": str(head), "invalid_at": "2026-10-07T13:00:00Z"}
            ],
            "next_cursor": None,
            "prev_cursor": None,
        },
    )
    stand_in.answer(
        "memory.get",
        200,
        GOT | {"id": str(head), "body": "The importer ships in October.", "superseded_by": None},
    )

    with ws(uuid4()):
        page = await memory.MemorySearchService(_ext(stand_in)).list_recent(
            frozenset({SHARED_SUBJECT}), 10
        )

    assert [row.text for row in page.rows] == [
        "The importer ships in September. (now: The importer ships in October.)"
    ]
    (got,) = _sent(stand_in, "memory.get")
    assert got.query == (("subject", SHARED_SUBJECT),)


async def test_the_kind_still_refuses_apply_and_delete_with_the_pinned_sentences(
    tmp_path: Path,
) -> None:
    stand_in = MemoryServiceStandIn()
    ctx = _ctx(stand_in, tmp_path, uuid4())
    spec = MemorySpec(
        body="b",
        subject=SHARED_SUBJECT,
        item_class="fact",
        memory_kind="fact",
        confidence=5,
        source_ref=None,
        as_of=None,
    )

    with pytest.raises(VerbNotSupported) as applied:
        await MEMORY_OBJECT.store.apply(ctx, str(uuid4()), spec, None, expected_generation=None)
    with pytest.raises(VerbNotSupported) as deleted:
        await MEMORY_OBJECT.store.delete(ctx, str(uuid4()), expected_generation=None)

    assert (str(applied.value), str(deleted.value)) == (MEMORY_UPDATE_REFUSAL, MEMORY_UNDELETABLE)
    assert stand_in.sent == []
