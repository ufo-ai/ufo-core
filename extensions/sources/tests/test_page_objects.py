"""The `page` object kind end to end: synced pages read through the object verbs, forgotten by
the owner.

Rows are seeded where the sync driver lands them; the tests drive list/get/status through the real
tool dispatch, prove create and update are refused naming the sync driver, and prove delete
tombstones the row and is owner-gated.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sources.manifest import NAME, manifest
from ufo_ext_sources.pages import PAGE_KIND
from ufo_ext_sources.registry import CONNECTORS

from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import turn_tools
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.connectors import ConnectorRegistry
from ufo.sdk.objects import OwnerRequired, VerbNotSupported
from ufo.sdk.tools import ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

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
                    "created_at": OWNER_CREATED_AT,
                    "updated_at": OWNER_CREATED_AT,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": f"{member_id.hex}@x.test",
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
                created_at=OWNER_CREATED_AT,
                updated_at=OWNER_CREATED_AT,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
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
    for tool in turn_tools((manifest(),), CredentialStore(fernet=Fernet(Fernet.generate_key())))[0]
}


def _context(state: _Workspace, *, speaker_id: UUID | None = None) -> ToolContext:
    ext = context_for(NAME, DECLARED_PROVIDERS)
    return ToolContext(
        sandbox=None,
        blob=None,
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
        audience_member_id=speaker_id or state.owner_id,
        artifact_token_secret="",
        grants=None,
        connectors=ConnectorRegistry(entries={}, fallback=None),
        ext=ext,
    )


async def _seed_source(state: _Workspace, backend: str) -> UUID:
    source_id = uuid4()
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
    return source_id


async def _seed_page(
    state: _Workspace, source_id: UUID, subject: str = "shared", tombstone: bool = False
) -> UUID:
    page_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=state.workspace_id,
                source_id=source_id,
                digest="sha256:abc",
                body_ref="pages/abc",
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
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
    assert result.is_error is False
    return result.content[0].text


def test_manifest_declares_the_page_kind() -> None:
    declared = manifest()
    assert {kind.name for kind in declared.objects} >= {PAGE_KIND}


async def test_pages_list_and_read_through_the_verbs(db: None) -> None:
    state = await _workspace()
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id)
        ctx = _context(state)

        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))
        assert [row["name"] for row in listing["objects"]] == [str(page_id)]
        assert "asana" in listing["objects"][0]["summary"]

        fetched = yaml.safe_load(
            await _text(_TOOLS["object_get"], ctx, kind=PAGE_KIND, name=str(page_id))
        )
        assert fetched["spec"] == {
            "source": "asana",
            "subject": "shared",
            "digest": "sha256:abc",
            "body_ref": "pages/abc",
        }
        assert fetched["status"]["backend"] == "asana"
        assert fetched["status"]["source_id"] == str(source_id)


async def test_tombstoned_pages_never_list(db: None) -> None:
    state = await _workspace()
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        await _seed_page(state, source_id, tombstone=True)
        ctx = _context(state)
        listing = json.loads(await _text(_TOOLS["object_list"], ctx, kind=PAGE_KIND))
        assert listing["objects"] == []


async def test_create_and_update_are_refused_naming_the_sync_driver(db: None) -> None:
    state = await _workspace()
    apply_tool = _TOOLS["object_apply"]
    manifest_text = yaml.safe_dump(
        {
            "kind": PAGE_KIND,
            "name": str(uuid4()),
            "spec": {
                "source": "asana",
                "subject": "shared",
                "digest": "sha256:abc",
                "body_ref": "pages/abc",
            },
        }
    )
    with ws(state.workspace_id), pytest.raises(VerbNotSupported, match="sync"):
        await apply_tool.handler(
            _context(state), apply_tool.input_model.model_validate({"manifest": manifest_text})
        )


async def test_delete_tombstones_and_is_owner_gated(db: None) -> None:
    state = await _workspace()
    delete_tool = _TOOLS["object_delete"]
    with ws(state.workspace_id):
        source_id = await _seed_source(state, "asana")
        page_id = await _seed_page(state, source_id)
        args = delete_tool.input_model.model_validate({"kind": PAGE_KIND, "name": str(page_id)})
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(_context(state, speaker_id=state.member_id), args)
        assert await _tombstone(state, page_id) in (False, 0)

        deleted = json.loads(
            await _text(delete_tool, _context(state), kind=PAGE_KIND, name=str(page_id))
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["source"] == "asana"
        assert await _tombstone(state, page_id) in (True, 1)
