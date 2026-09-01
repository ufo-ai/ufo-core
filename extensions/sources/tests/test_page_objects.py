"""The `page` object kind end to end: synced pages read through the object verbs, forgotten by
the owner.

Rows are landed through the sync driver for the producer-consumer proof and seeded directly for
row-specific cases. The tests drive list/get/status through the real tool dispatch, prove create
and update are refused naming the sync driver, and prove delete tombstones the row and is
admin-gated.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import PAGE_BODY_MAX_BYTES, PAGE_KIND, PageObjects, _page_timestamp
from ufo_ext_sources.registry import CONNECTORS

from ufo.blob import BlobStore, FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import context_for
from ufo.runtime.sources.sync import SyncDriver
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import Audience, conversation_audience, foreign_room_audience, room_audience
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.objects import AdminRequired, VerbNotSupported
from ufo.sdk.sources import ConnectorSourceConfig, Page, SourceAuth, SyncResult
from ufo.sdk.tools import ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "looking through the synced pages"

OWNER_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
MEMBER_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)
DECLARED_PROVIDERS = frozenset(CONNECTORS)


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class _PageSource:
    page: Page
    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        return SyncResult(pages=(self.page,))


@dataclass(frozen=True)
class _MutatingBlob:
    inner: FilesystemBlobStore
    mutate: Callable[[], Awaitable[None]]

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        async for chunk in self.inner.get_stream(key):
            yield chunk
        await self.mutate()


async def _workspace() -> _Workspace:
    workspace_id = uuid4()
    owner_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=OWNER_CREATED_AT, updated_at=OWNER_CREATED_AT
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": owner_id,
                    "workspace_id": workspace_id,
                    "email": f"{owner_id.hex}@x.test",
                    "is_admin": True,
                    "created_at": OWNER_CREATED_AT,
                    "updated_at": OWNER_CREATED_AT,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "is_admin": False,
                    "created_at": MEMBER_CREATED_AT,
                    "updated_at": MEMBER_CREATED_AT,
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=OWNER_CREATED_AT,
                updated_at=OWNER_CREATED_AT,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=owner_id,
                created_at=OWNER_CREATED_AT,
                updated_at=OWNER_CREATED_AT,
            )
        )
    return _Workspace(workspace_id, owner_id, member_id, agent_id, conversation_id)


_TOOLS: dict[str, ToolDef] = {
    tool.name: tool
    for tool in turn_tools(
        (manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )[0]
}


def _context(
    state: _Workspace,
    blob: BlobStore,
    *,
    speaker_id: UUID | None = None,
    audience: Audience | None = None,
) -> ToolContext:
    exact_audience = (
        conversation_audience(speaker_id or state.owner_id) if audience is None else audience
    )
    ext = context_for(NAME, DECLARED_PROVIDERS, audience=exact_audience)
    return ToolContext(
        sandbox=None,
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=state.agent_id,
            seq=1,
            status="running",
            inbound="read pages",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=speaker_id or state.owner_id,
        audience=exact_audience,
        artifact_token_secret="",
        grants=None,
        connectors=ConnectorRegistry(entries={}, fallback=None),
        ext=ext,
    )


async def _seed_source(
    state: _Workspace, backend: str, *, source_id: UUID | None = None, granted: bool = True
) -> UUID:
    source_id = uuid4() if source_id is None else source_id
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=state.workspace_id,
                backend=backend,
                config={"account": "acct-one", "stream": "tickets", "base_url": None},
                next_sync_at=datetime(2026, 7, 9, tzinfo=UTC),
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
                updated_at=datetime(2026, 7, 9, tzinfo=UTC),
            )
        )
        if granted:
            await connection.execute(
                sa.insert(tables.source_grant).values(
                    workspace_id=state.workspace_id,
                    source_id=source_id,
                    agent_id=state.agent_id,
                    created_at=datetime(2026, 7, 9, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 9, tzinfo=UTC),
                )
            )
    return source_id


async def _seed_page(
    state: _Workspace,
    source_id: UUID,
    blob: FilesystemBlobStore,
    *,
    stream: str = "issues",
    title: str = "Fix the flux capacitor",
    record_created_at: str | None = "2026-07-09T00:00:00Z",
    body: str | None = None,
    subject: str = "shared",
    tombstone: bool = False,
    page_id: UUID | None = None,
) -> UUID:
    page_id = uuid4() if page_id is None else page_id
    body_ref = f"pages/{page_id}"
    content = f"# {title}\n\nIssue body" if body is None else body
    await blob.put(body_ref, content.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=state.workspace_id,
                source_id=source_id,
                digest="sha256:abc",
                body_ref=body_ref,
                stream=stream,
                title=title,
                record_created_at=record_created_at,
                record_updated_at=record_created_at,
                subject=subject,
                tombstone=tombstone,
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
                updated_at=datetime(2026, 7, 9, tzinfo=UTC),
            )
        )
    return page_id


async def _tombstone(state: _Workspace, page_id: UUID) -> bool:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.page.c.tombstone).where(tables.page.c.id == page_id)
                )
            ).scalar_one()


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


def test_manifest_declares_the_page_kind() -> None:
    declared = manifest()
    assert {kind.name for kind in declared.objects} >= {PAGE_KIND}


async def test_sync_driver_page_metadata_round_trips_through_object_verbs(
    db: None, database_url: str, tmp_path: Path
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    source_id = await _seed_source(state, "probe")
    page = Page(
        source_ref="issues/ENG-42",
        body="Issue body",
        stream="issues",
        title="Fix launch sequencing",
        created_at="2026-07-01T12:00:00Z",
        updated_at="2026-07-23T18:30:00Z",
    )
    driver = SyncDriver(
        backends={"probe": _PageSource(page)},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )

    with ws(state.workspace_id):
        await driver.run()
        ctx = _context(state, blob)
        listing = json.loads(
            await _text(
                _TOOLS["object_list"],
                ctx,
                kind=PAGE_KIND,
                filters={"source_id": str(source_id), "stream": page.stream},
                order_by="updated_at",
                order="desc",
            )
        )
        assert len(listing["objects"]) == 1
        listed = listing["objects"][0]
        assert listed["source_id"] == str(source_id)
        assert listed["source"] == "probe"
        assert listed["stream"] == page.stream
        assert listed["title"] == page.title
        assert listed["created_at"] == "2026-07-01T12:00:00.000000+00:00"
        assert listed["updated_at"] == "2026-07-23T18:30:00.000000+00:00"

        fetched = yaml.safe_load(
            await _text(_TOOLS["object_get"], ctx, kind=PAGE_KIND, name=listed["name"])
        )
        assert fetched["spec"]["source_id"] == str(source_id)
        assert fetched["spec"]["stream"] == page.stream
        assert fetched["spec"]["title"] == page.title
        assert fetched["spec"]["created_at"] == "2026-07-01T12:00:00.000000+00:00"
        assert fetched["spec"]["updated_at"] == "2026-07-23T18:30:00.000000+00:00"
        assert fetched["spec"]["body"] == page.body


async def test_page_get_rechecks_source_authority_after_streaming(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    stored = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id, stored)

        async def revoke() -> None:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.delete(tables.source_grant).where(
                        tables.source_grant.c.workspace_id == state.workspace_id,
                        tables.source_grant.c.source_id == source_id,
                        tables.source_grant.c.agent_id == state.agent_id,
                    )
                )

        assert (
            await PageObjects().get(
                _context(state, _MutatingBlob(stored, revoke)),
                str(page_id),
            )
            is None
        )


async def test_pages_list_hides_a_source_this_agent_holds_no_grant_for(
    db: None, tmp_path: Path
) -> None:
    """The page listing reads through `source_pages`, so an ungranted feed's pages must not appear
    even when they carry the exact subject the agent reads, in its own workspace, un-tombstoned —
    the same audience the granted feed's page passes on. Only the grant separates them, and `get`
    refuses the ungranted page too, so the listing is not merely hiding a readable row."""
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        granted_source = await _seed_source(state, "asana")
        ungranted_source = await _seed_source(state, "linear", granted=False)
        granted = await _seed_page(state, granted_source, blob, title="Granted")
        ungranted = await _seed_page(state, ungranted_source, blob, title="Ungranted")
        ctx = _context(state, blob)

        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))
        fetched = await PageObjects().get(ctx, str(ungranted))
        async with workspace_tx() as connection:
            subjects = {
                row.id: (row.subject, row.tombstone)
                for row in (
                    await connection.execute(
                        sa.select(tables.page.c.id, tables.page.c.subject, tables.page.c.tombstone)
                    )
                ).all()
            }

    assert {row["name"] for row in listing["objects"]} == {str(granted)}
    assert fetched is None
    assert subjects == {granted: ("shared", False), ungranted: ("shared", False)}


async def test_foreign_room_cannot_read_shared_source_pages(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        await _seed_page(state, source_id, blob)
        room = replace(
            _context(state, blob, audience=room_audience("slack", "CPRIVATE")),
            speaker_member_id=None,
        )
        foreign = replace(
            _context(state, blob, audience=foreign_room_audience("slack", "CCONNECT")),
            speaker_member_id=None,
        )

        speaking = _context(state, blob, audience=foreign_room_audience("slack", "CCONNECT"))

        room_listing = json.loads(await _text(_TOOLS["object_list"], room, kind=PAGE_KIND))
        foreign_listing = json.loads(await _text(_TOOLS["object_list"], foreign, kind=PAGE_KIND))
        speaking_listing = json.loads(await _text(_TOOLS["object_list"], speaking, kind=PAGE_KIND))

    assert len(room_listing["objects"]) == 1
    assert foreign_listing["objects"] == []
    assert speaking_listing["objects"] == []


async def test_explicit_room_request_reads_shared_and_requester_private_pages(
    db: None, tmp_path: Path
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    room = room_audience("slack", "CPRIVATE")
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        shared = await _seed_page(state, source_id, blob, title="Shared")
        private = await _seed_page(
            state,
            source_id,
            blob,
            title="Mine",
            subject=member_subject(state.member_id),
        )
        hidden = await _seed_page(
            state,
            source_id,
            blob,
            title="Other member",
            subject=member_subject(state.owner_id),
        )
        ctx = _context(state, blob, speaker_id=state.member_id, audience=room)

        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))

    names = {row["name"] for row in listing["objects"]}
    assert names == {str(shared), str(private)}
    assert str(hidden) not in names


async def test_pages_without_provider_timestamps_use_row_timestamps(
    db: None, tmp_path: Path
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "probe")
        page_id = await _seed_page(state, source_id, blob, record_created_at=None)
        ctx = _context(state, blob)

        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))
        listed = next(row for row in listing["objects"] if row["name"] == str(page_id))
        assert listed["created_at"] == "2026-07-09T00:00:00.000000+00:00"
        assert listed["updated_at"] == "2026-07-09T00:00:00.000000+00:00"

        fetched = yaml.safe_load(
            await _text(_TOOLS["object_get"], ctx, kind=PAGE_KIND, name=str(page_id))
        )
        assert fetched["spec"]["created_at"] == "2026-07-09T00:00:00.000000+00:00"
        assert fetched["spec"]["updated_at"] == "2026-07-09T00:00:00.000000+00:00"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-time", "invalid page timestamp"),
        ("2026-07-23T18:30:00", "lacks a timezone"),
    ],
)
def test_page_timestamp_rejects_invalid_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _page_timestamp(value, datetime(2026, 7, 9, tzinfo=UTC))


async def test_page_get_bounds_an_oversized_body(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(
            state,
            source_id,
            blob,
            body="x" * (PAGE_BODY_MAX_BYTES - 1) + "🙂",
        )
        fetched = yaml.safe_load(
            await _text(
                _TOOLS["object_get"],
                _context(state, blob),
                kind=PAGE_KIND,
                name=str(page_id),
            )
        )
        assert fetched["spec"]["body"] == "x" * (PAGE_BODY_MAX_BYTES - 1)
        assert "�" not in fetched["spec"]["body"]
        assert fetched["spec"]["body_truncated"] is True


async def test_page_get_rejects_corrupted_utf8(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id, blob)
        await blob.put(f"pages/{page_id}", b"corrupted\xff")
        with pytest.raises(UnicodeDecodeError):
            await PageObjects().get(_context(state, blob), str(page_id))


async def test_page_get_rejects_corruption_at_the_truncation_boundary(
    db: None, tmp_path: Path
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id, blob)
        content = b"x" * (PAGE_BODY_MAX_BYTES - 1) + b"\xff" + b"tail"
        await blob.put(f"pages/{page_id}", content)
        with pytest.raises(UnicodeDecodeError):
            await PageObjects().get(_context(state, blob), str(page_id))


async def test_page_get_closes_an_oversized_body_stream(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    closed = False
    original_get_stream = FilesystemBlobStore.get_stream

    async def observed_get_stream(store: FilesystemBlobStore, key: str) -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            async for chunk in original_get_stream(store, key):
                yield chunk
        finally:
            closed = True

    monkeypatch.setattr(FilesystemBlobStore, "get_stream", observed_get_stream)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(
            state,
            source_id,
            blob,
            body="x" * (PAGE_BODY_MAX_BYTES + 1),
        )
        await _text(
            _TOOLS["object_get"],
            _context(state, blob),
            kind=PAGE_KIND,
            name=str(page_id),
        )
        assert closed is True


async def test_tombstoned_pages_never_list(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        await _seed_page(state, source_id, blob, tombstone=True)
        ctx = _context(state, blob)
        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))
        assert listing["objects"] == []


async def test_create_and_update_are_refused_naming_the_sync_driver(
    db: None, tmp_path: Path
) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    apply_tool = _TOOLS["object_apply"]
    manifest_text = yaml.safe_dump(
        {
            "kind": PAGE_KIND,
            "name": str(uuid4()),
            "spec": {
                "source_id": str(uuid4()),
                "source": "asana",
                "stream": "issues",
                "title": "Issue",
                "created_at": "2026-07-09T00:00:00Z",
                "updated_at": "2026-07-09T00:00:00Z",
                "subject": "shared",
                "digest": "sha256:abc",
                "body_ref": "pages/abc",
                "body": "body",
                "body_truncated": False,
            },
        }
    )
    with ws(state.workspace_id), pytest.raises(VerbNotSupported, match="sync"):
        await apply_tool.handler(
            _context(state, blob),
            apply_tool.input_model.model_validate({"manifest": manifest_text}),
        )


async def test_delete_tombstones_and_is_owner_gated(db: None, tmp_path: Path) -> None:
    state = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id, blob)
        args = delete_tool.input_model.model_validate({"kind": PAGE_KIND, "name": str(page_id)})
        with pytest.raises(AdminRequired):
            await delete_tool.handler(_context(state, blob, speaker_id=state.member_id), args)
        assert await _tombstone(state, page_id) in (False, 0)

        deleted = json.loads(
            await _text(delete_tool, _context(state, blob), kind=PAGE_KIND, name=str(page_id))
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["source"] == "asana"
        assert await _tombstone(state, page_id) in (True, 1)
