import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml

from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.members import MEMBER_KIND
from ufo.objects import AdminRequired, VerbNotSupported
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

LOCK_OBSERVE_TIMEOUT_SECONDS = 5
TOOL_NARRATION = "managing workspace members"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
) -> SpawnResult:
    raise AssertionError


async def _seed() -> tuple[UUID, UUID, UUID, UUID, UUID]:
    workspace_id, main_agent, child_agent, admin_id, member_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for agent_id, name, is_main in (
            (main_agent, "ufo", True),
            (child_agent, "research", False),
        ):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="m",
                    is_main=is_main,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for row_id, email, is_admin in (
            (admin_id, "admin@example.com", True),
            (member_id, "member@example.com", False),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=row_id,
                    workspace_id=workspace_id,
                    email=email,
                    is_admin=is_admin,
                    seated_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, main_agent, child_agent, admin_id, member_id


def _context(
    workspace_id: UUID,
    agent_id: UUID,
    speaker_id: UUID,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=Path()),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="manage members",
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="m"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_id,
        audience=conversation_audience(speaker_id),
        artifact_token_secret="",
    )


def _tool(name: str) -> ToolDef:
    tools, _ = turn_tools((), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )
    assert result.is_error is False
    return result.content[0].text


def _manifest(member_id: UUID, admin: bool) -> str:
    return yaml.safe_dump(
        {
            "kind": MEMBER_KIND,
            "name": str(member_id),
            "spec": {"admin": admin},
        }
    )


async def test_main_agent_admin_manages_roles_through_member_objects(db: None) -> None:
    workspace_id, main_agent, child_agent, admin_id, member_id = await _seed()
    with ws(workspace_id):
        member_listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, child_agent, member_id),
                kind=MEMBER_KIND,
            )
        )
        assert [row["name"] for row in member_listing["objects"]] == [str(member_id)]

        admin = _context(workspace_id, main_agent, admin_id)
        admin_listing = json.loads(await _text(_tool("object_list"), admin, kind=MEMBER_KIND))
        assert {row["name"] for row in admin_listing["objects"]} == {
            str(admin_id),
            str(member_id),
        }
        await _text(
            _tool("object_apply"),
            admin,
            manifest=_manifest(member_id, True),
        )
        await _text(
            _tool("object_apply"),
            _context(workspace_id, main_agent, member_id),
            manifest=_manifest(admin_id, False),
        )
    async with workspace_tx() as connection:
        roles = {
            row.id: row.is_admin
            for row in (
                await connection.execute(
                    sa.select(tables.member.c.id, tables.member.c.is_admin).where(
                        tables.member.c.id.in_((admin_id, member_id))
                    )
                )
            )
        }
    assert roles == {admin_id: False, member_id: True}


async def test_member_get_reports_role_and_seat_and_delete_is_refused(db: None) -> None:
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    with ws(workspace_id):
        admin = _context(workspace_id, main_agent, admin_id)
        fetched = yaml.safe_load(
            await _text(
                _tool("object_get"),
                admin,
                kind=MEMBER_KIND,
                name=str(member_id),
            )
        )
        assert fetched["kind"] == MEMBER_KIND
        assert fetched["name"] == str(member_id)
        assert fetched["spec"] == {"admin": False}
        assert fetched["status"] == {
            "email": "member@example.com",
            "seated": True,
        }
        assert fetched["links"] == []
        assert datetime.fromisoformat(fetched["created_at"])
        assert datetime.fromisoformat(fetched["updated_at"])

        delete_tool = _tool("object_delete")
        with pytest.raises(VerbNotSupported, match="cannot be deleted"):
            await delete_tool.handler(
                admin,
                delete_tool.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": MEMBER_KIND,
                        "name": str(member_id),
                    }
                ),
            )


async def test_child_agent_cannot_change_another_members_role(db: None) -> None:
    workspace_id, _, child_agent, admin_id, member_id = await _seed()
    with ws(workspace_id), pytest.raises(AdminRequired, match="main agent"):
        await _tool("object_apply").handler(
            _context(workspace_id, child_agent, admin_id),
            _tool("object_apply").input_model.model_validate(
                {
                    "user_description": TOOL_NARRATION,
                    "manifest": _manifest(member_id, True),
                }
            ),
        )


async def test_last_admin_cannot_be_removed(db: None) -> None:
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id), pytest.raises(ValueError, match="at least one admin"):
        await _tool("object_apply").handler(
            _context(workspace_id, main_agent, admin_id),
            _tool("object_apply").input_model.model_validate(
                {
                    "user_description": TOOL_NARRATION,
                    "manifest": _manifest(admin_id, False),
                }
            ),
        )


async def test_demoted_admin_cannot_finish_a_role_change(
    db: None,
    database_url: str,
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("row-lock interleaving requires PostgreSQL")
    workspace_id, main_agent, _, _admin_id, second_admin = await _seed()
    target = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == second_admin).values(is_admin=True)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=target,
                workspace_id=workspace_id,
                email="target@example.com",
                is_admin=False,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    tool = _tool("object_apply")
    args = tool.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _manifest(target, True),
        }
    )
    with ws(workspace_id):
        async with workspace_tx() as demotion:
            holder_pid = (await demotion.execute(sa.text("select pg_backend_pid()"))).scalar_one()
            await demotion.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == workspace_id)
                .with_for_update()
            )
            pending = asyncio.create_task(
                tool.handler(_context(workspace_id, main_agent, second_admin), args)
            )
            async with asyncio.timeout(LOCK_OBSERVE_TIMEOUT_SECONDS):
                while True:
                    async with workspace_tx() as observer:
                        blocked = (
                            await observer.execute(
                                sa.text(
                                    "select exists ("
                                    "select 1 from pg_stat_activity "
                                    "where cast(:holder as integer) = any(pg_blocking_pids(pid))"
                                    ")"
                                ),
                                {"holder": holder_pid},
                            )
                        ).scalar_one()
                    if blocked:
                        break
            await demotion.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == second_admin)
                .values(is_admin=False)
            )
        with pytest.raises(AdminRequired, match="workspace admin"):
            await pending
        async with workspace_tx() as connection:
            assert not (
                await connection.execute(
                    sa.select(tables.member.c.is_admin).where(tables.member.c.id == target)
                )
            ).scalar_one()
