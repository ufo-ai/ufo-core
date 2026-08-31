import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from dbos import EnqueueOptions

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import turn_tools
from ufo.runtime.kinds.members import ADD_MEMBER_TOOL_DEF, MEMBER_KIND
from ufo.runtime.objects import AdminRequired, UnknownObject, VerbNotSupported
from ufo.runtime.seats import SEAT_REFUSAL_MESSAGE, create_member
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import (
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import CANCELLED, Agent, TerminalFrame, Turn

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
    audience: Audience | None = None,
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
        audience=audience or conversation_audience(speaker_id),
        artifact_token_secret="",
    )


def _tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


def _manifest(member_id: UUID, admin: bool, seated: bool = True) -> str:
    return yaml.safe_dump(
        {
            "kind": MEMBER_KIND,
            "name": str(member_id),
            "spec": {"admin": admin, "seated": seated},
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
        assert fetched["spec"] == {"admin": False, "seated": True}
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


async def _member_row(workspace_id: UUID, email: str) -> sa.Row | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.member.c.id,
                    tables.member.c.is_admin,
                    tables.member.c.seated_at,
                    tables.member.c.invited_at,
                    tables.member.c.invited_by,
                ).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == email,
                )
            )
        ).one_or_none()


async def _add(ctx: ToolContext, **args: object) -> str:
    return await _text(ADD_MEMBER_TOOL_DEF, ctx, **args)


async def test_an_admin_adds_a_member_who_has_never_spoken(db: None) -> None:
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(
            _context(workspace_id, main_agent, admin_id), email="New.Person@Example.com"
        )
    row = await _member_row(workspace_id, "new.person@example.com")
    assert row is not None and not row.is_admin
    assert row.seated_at is not None
    assert "new.person@example.com" in answer


async def test_an_admin_adds_a_member_as_an_admin(db: None) -> None:
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        await _add(
            _context(workspace_id, main_agent, admin_id),
            email="second@example.com",
            admin=True,
        )
    row = await _member_row(workspace_id, "second@example.com")
    assert row is not None and row.is_admin


async def test_an_added_member_carries_the_admin_who_added_them_and_when(db: None) -> None:
    """The stamp is the invitation. Nothing else on the row separates somebody an admin added from
    somebody who arrived by themselves, and the stamp is what the gateway enumerates to send them
    the message naming who added them and where to sign in."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(_context(workspace_id, main_agent, admin_id), email="told@example.com")
    row = await _member_row(workspace_id, "told@example.com")
    assert row is not None
    assert row.invited_by == admin_id
    assert row.invited_at is not None
    assert "email" in answer
    seated_themselves = await _member_row(workspace_id, "member@example.com")
    assert seated_themselves is not None
    assert seated_themselves.invited_at is None
    assert seated_themselves.invited_by is None


async def test_a_member_added_unannounced_carries_no_stamp(db: None) -> None:
    """An admin who will tell the person themselves adds them with `notify` false. The stamp is the
    whole event the delivery reads, so a row without one is a member nothing writes to."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(
            _context(workspace_id, main_agent, admin_id),
            email="quiet@example.com",
            notify=False,
        )
    row = await _member_row(workspace_id, "quiet@example.com")
    assert row is not None
    assert row.invited_at is None
    assert row.invited_by is None
    assert "email" not in answer


async def test_a_non_admin_cannot_add_a_member(db: None) -> None:
    workspace_id, main_agent, _, _, member_id = await _seed()
    with ws(workspace_id), pytest.raises(AdminRequired, match="workspace admin"):
        await _add(_context(workspace_id, main_agent, member_id), email="new@example.com")
    assert await _member_row(workspace_id, "new@example.com") is None


async def test_a_child_agent_cannot_add_a_member(db: None) -> None:
    workspace_id, _, child_agent, admin_id, _ = await _seed()
    with ws(workspace_id), pytest.raises(AdminRequired, match="main agent"):
        await _add(_context(workspace_id, child_agent, admin_id), email="new@example.com")
    assert await _member_row(workspace_id, "new@example.com") is None


async def test_an_email_outside_the_workspace_domain_is_added(db: None) -> None:
    """A contractor, an advisor, or a colleague at a sister company is a member an admin can staff
    the workspace with: the speaking admin is the vetting, so no domain is compared."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(
            _context(workspace_id, main_agent, admin_id), email="contractor@other.com"
        )
    row = await _member_row(workspace_id, "contractor@other.com")
    assert row is not None and not row.is_admin
    assert "contractor@other.com is a workspace member" in answer


async def test_an_email_outside_the_workspace_domain_is_added_as_an_admin(db: None) -> None:
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        await _add(
            _context(workspace_id, main_agent, admin_id),
            email="advisor@other.com",
            admin=True,
        )
    row = await _member_row(workspace_id, "advisor@other.com")
    assert row is not None and row.is_admin


async def test_a_malformed_address_is_refused(db: None) -> None:
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id), pytest.raises(ValueError, match="not an email address"):
        await _add(_context(workspace_id, main_agent, admin_id), email="not-an-address")


async def test_adding_an_existing_member_refuses_and_leaves_their_role(db: None) -> None:
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    with ws(workspace_id), pytest.raises(ValueError, match="already a member"):
        await _add(
            _context(workspace_id, main_agent, admin_id),
            email="member@example.com",
            admin=True,
        )
    row = await _member_row(workspace_id, "member@example.com")
    assert row is not None and row.id == member_id and not row.is_admin


async def test_a_member_an_admin_adds_can_speak_at_once(db: None) -> None:
    """Nothing bounds the members a workspace has, so adding one seats them and the answer says so.
    A member added unseated would be a person the admin just invited and the agent then refuses."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(_context(workspace_id, main_agent, admin_id), email="third@example.com")
    row = await _member_row(workspace_id, "third@example.com")
    assert row is not None and row.seated_at is not None
    assert answer == (
        "third@example.com is a workspace member. They can speak to the agent now. "
        "They will get an email with a link to sign in."
    )


async def test_an_admins_unseat_stops_the_agent_answering_that_member(db: None) -> None:
    """The whole of removing someone's access, end to end: an admin applies `seated: false` on the
    member object, and the member's next message through the real admission path is refused instead
    of answered. The `member` kind refuses delete, so this apply is the only act that does it — if
    admission still answered them, no supported action would remove that person's access at all."""
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=main_agent,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    admission = Admission(dbos=_SeatStubDbos(), durable_surfaces=frozenset())
    before = await admission.admit_member(workspace_id, conversation_id, "still here?", member_id)
    assert await _turn_terminal(before.turn_id) == ("queued", None)

    with ws(workspace_id):
        await _text(
            _tool("object_apply"),
            _context(workspace_id, main_agent, admin_id),
            manifest=_manifest(member_id, admin=False, seated=False),
        )

    after = await admission.admit_member(workspace_id, conversation_id, "hello?", member_id)
    assert await _turn_terminal(after.turn_id) == (CANCELLED, SEAT_REFUSAL_MESSAGE)


@dataclass
class _SeatStubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _turn_terminal(turn_id: UUID) -> tuple[str, str | None]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    text = None if row.terminal is None else TerminalFrame.model_validate(row.terminal).text
    return row.status, text


async def test_every_member_lists_the_roster_from_the_main_agent(db: None) -> None:
    workspace_id, main_agent, child_agent, admin_id, member_id = await _seed()
    with ws(workspace_id):
        listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, main_agent, member_id),
                kind=MEMBER_KIND,
            )
        )
        assert {row["name"] for row in listing["objects"]} == {str(admin_id), str(member_id)}
        assert {row["summary"] for row in listing["objects"]} == {
            "admin@example.com, workspace admin, seated",
            "member@example.com, workspace member, seated",
        }
        opened = yaml.safe_load(
            await _text(
                _tool("object_get"),
                _context(workspace_id, main_agent, member_id),
                kind=MEMBER_KIND,
                name=str(admin_id),
            )
        )
        assert opened["spec"] == {"admin": True, "seated": True}
        assert opened["status"]["email"] == "admin@example.com"
        walled = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, child_agent, member_id),
                kind=MEMBER_KIND,
            )
        )
        assert [row["name"] for row in walled["objects"]] == [str(member_id)]


async def test_a_speaking_admin_reads_the_roster_from_a_child_agent(db: None) -> None:
    workspace_id, _, child_agent, admin_id, member_id = await _seed()
    with ws(workspace_id):
        listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, child_agent, admin_id),
                kind=MEMBER_KIND,
            )
        )
        assert {row["name"] for row in listing["objects"]} == {str(admin_id), str(member_id)}
        opened = yaml.safe_load(
            await _text(
                _tool("object_get"),
                _context(workspace_id, child_agent, admin_id),
                kind=MEMBER_KIND,
                name=str(member_id),
            )
        )
        assert opened["status"]["email"] == "member@example.com"
        room = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, child_agent, admin_id, room_audience("slack", "C7")),
                kind=MEMBER_KIND,
            )
        )
        assert {row["name"] for row in room["objects"]} == {str(admin_id), str(member_id)}
        with pytest.raises(UnknownObject):
            await _text(
                _tool("object_get"),
                _context(workspace_id, child_agent, member_id),
                kind=MEMBER_KIND,
                name=str(admin_id),
            )
        foreign = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, child_agent, admin_id, foreign_room_audience("slack", "C9")),
                kind=MEMBER_KIND,
            )
        )
        assert [row["name"] for row in foreign["objects"]] == [str(admin_id)]


async def test_a_listing_member_still_cannot_change_a_role(db: None) -> None:
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    with ws(workspace_id), pytest.raises(AdminRequired, match="workspace admin"):
        await _tool("object_apply").handler(
            _context(workspace_id, main_agent, member_id),
            _tool("object_apply").input_model.model_validate(
                {"manifest": _manifest(admin_id, False)}
            ),
        )


async def test_an_externally_shared_room_reads_only_the_speaker(db: None) -> None:
    """The roster is internal. In a channel another organization sits in, a non-admin listing
    members reads their own row alone — and cannot open a colleague's — so a Slack Connect channel
    never hears who works here, who administers the workspace, or who is unseated."""
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    foreign = foreign_room_audience("slack", "C123")
    with ws(workspace_id):
        listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, main_agent, member_id, foreign),
                kind=MEMBER_KIND,
            )
        )
        assert [row["name"] for row in listing["objects"]] == [str(member_id)]
        assert "admin@example.com" not in json.dumps(listing)
        with pytest.raises(UnknownObject):
            await _text(
                _tool("object_get"),
                _context(workspace_id, main_agent, member_id, foreign),
                kind=MEMBER_KIND,
                name=str(admin_id),
            )


async def test_the_foreign_room_narrows_an_admin_too(db: None) -> None:
    """The room is the boundary, not the role: the staff list reaching another organization is no
    less a disclosure when an admin is the one who asked, so an admin in an externally shared
    channel reads their own row alone and reaches the roster from an internal conversation."""
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    with ws(workspace_id):
        listing = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, main_agent, admin_id, foreign_room_audience("slack", "C1")),
                kind=MEMBER_KIND,
            )
        )
        assert [row["name"] for row in listing["objects"]] == [str(admin_id)]
        assert "member@example.com" not in json.dumps(listing)
        internal = json.loads(
            await _text(
                _tool("object_list"),
                _context(workspace_id, main_agent, admin_id),
                kind=MEMBER_KIND,
            )
        )
    assert {row["name"] for row in internal["objects"]} == {str(admin_id), str(member_id)}


async def test_an_address_differing_only_in_case_is_already_a_member(db: None) -> None:
    """A mixed-case row is real — onboarding stores `--email` verbatim — so the duplicate check
    folds case. Otherwise the exact-index conflict would not fire and one person would hold two
    member rows and two seats."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="Mixed.Case@Example.com",
                is_admin=False,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id), pytest.raises(ValueError, match="already a member"):
        await _add(_context(workspace_id, main_agent, admin_id), email="mixed.case@example.com")
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count()).where(
                    tables.member.c.workspace_id == workspace_id,
                    sa.func.lower(tables.member.c.email) == "mixed.case@example.com",
                )
            )
        ).scalar_one()
    assert rows == 1


async def test_the_success_report_names_the_role_it_wrote(db: None) -> None:
    """The report is what an admin acts on, so it states the role that landed and that the member
    can be answered — a member added as an admin is not described as an ordinary member."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        as_admin = await _add(
            _context(workspace_id, main_agent, admin_id), email="chief@example.com", admin=True
        )
        as_member = await _add(
            _context(workspace_id, main_agent, admin_id), email="hand@example.com", notify=False
        )
    assert as_admin == (
        "chief@example.com is a workspace admin. They can speak to the agent now. "
        "They will get an email with a link to sign in."
    )
    assert as_member == "hand@example.com is a workspace member. They can speak to the agent now."


async def test_a_dotless_domain_is_admitted(db: None) -> None:
    """A self-hosted deploy's internal addresses (`ufoctl init --email admin@internal`) parse as one
    `local@domain`, so the shape gate admits them."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id):
        answer = await _add(_context(workspace_id, main_agent, admin_id), email="bob@internal")
    assert "bob@internal is a workspace member" in answer
    assert await _member_row(workspace_id, "bob@internal") is not None


async def test_object_apply_names_the_verb_that_creates_a_member(db: None) -> None:
    """The kind refuses create and points at the verb that does it, so an agent told "add
    jane@acme.com" is never left with a dead end."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id), pytest.raises(VerbNotSupported, match="add_member action"):
        await _tool("object_apply").handler(
            _context(workspace_id, main_agent, admin_id),
            _tool("object_apply").input_model.model_validate(
                {"manifest": _manifest(uuid4(), False)}
            ),
        )


async def test_create_member_holds_the_workspace_row_before_it_inserts(
    db: None,
    database_url: str,
) -> None:
    """The lock order every creation path shares, asserted where it can regress: `create_member`
    takes the workspace row before its insert, so a caller holding nothing (`join_member`) and a
    caller already holding the row (`add_member`, hosted onboarding) queue behind one another
    instead of forming a cycle — one taking the row then the address, the other the address then
    the row.

    The discriminator is *which* statement blocks while another transaction holds the row. With the
    lock first, `create_member` waits on its `SELECT … workspace … FOR UPDATE` and has inserted
    nothing. With the lock below the insert, it waits inside `INSERT INTO member` instead — the
    insert takes a `KEY SHARE` lock on the referenced workspace row — having already taken the
    address's index key, which is the second edge of the deadlock."""
    if not database_url.startswith("postgresql"):
        pytest.skip("row-lock interleaving requires PostgreSQL")
    workspace_id, _main, _child, _admin, _member = await _seed()

    async def create() -> None:
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await create_member(connection, workspace_id, "queued@example.com")

    with ws(workspace_id):
        async with workspace_tx() as holder:
            holder_pid = (await holder.execute(sa.text("select pg_backend_pid()"))).scalar_one()
            await holder.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == workspace_id)
                .with_for_update()
            )
            creating = asyncio.create_task(create())
            blocked_query = ""
            async with asyncio.timeout(LOCK_OBSERVE_TIMEOUT_SECONDS):
                while not blocked_query:
                    async with workspace_tx() as observer:
                        blocked_query = (
                            await observer.execute(
                                sa.text(
                                    "select query from pg_stat_activity "
                                    "where cast(:holder as integer) = any(pg_blocking_pids(pid))"
                                ),
                                {"holder": holder_pid},
                            )
                        ).scalar_one_or_none() or ""
            assert "for update" in blocked_query.lower(), blocked_query
            assert "insert into member" not in blocked_query.lower(), blocked_query
        await creating
    assert await _member_row(workspace_id, "queued@example.com") is not None


@pytest.mark.parametrize(
    "address",
    [
        "jane doe@example.com",
        "a@b@example.com",
        "@@example.com",
        "  spaced out @example.com",
        "no-at-sign",
        "@example.com",
        "trailing@",
    ],
)
async def test_a_malformed_address_never_becomes_a_member(db: None, address: str) -> None:
    """`create_member` crosses the shape gate, so nothing a sign-in could never normalize to and no
    channel-verified join could ever equal reaches a member row. (An unusual-but-structural local
    part like `<script>@example.com` is admitted — the gate rules out whitespace and stray
    separators, not local-part taste.) Such a row would be seated against the allowance,
    answerable by nobody, and undeletable — the `member` kind refuses delete."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    with ws(workspace_id), pytest.raises(ValueError):
        await _add(_context(workspace_id, main_agent, admin_id), email=address)
    async with workspace_tx() as connection:
        added = (
            await connection.execute(
                sa.select(sa.func.count()).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email.not_in(("admin@example.com", "member@example.com")),
                )
            )
        ).scalar_one()
    assert added == 0
    # The gate lives in the write, not only in the verb that calls it, so the two creation paths
    # that match no domain — onboarding's owner and hosted signup — cannot mint the row either.
    with ws(workspace_id), pytest.raises(ValueError, match="local@domain"):
        async with workspace_tx() as connection:
            await create_member(connection, workspace_id, address)


async def test_membership_is_not_managed_in_an_externally_shared_channel(db: None) -> None:
    """`add_member` is refused in a channel another organization sits in, whoever asks: its refusal
    would confirm a colleague's membership and hand out their member id, and its success would
    mint a member there. The reads already narrow in that room; the write matches them."""
    workspace_id, main_agent, _, admin_id, member_id = await _seed()
    foreign = foreign_room_audience("slack", "C900")
    with ws(workspace_id):
        with pytest.raises(AdminRequired, match="internal conversation"):
            await _add(
                _context(workspace_id, main_agent, admin_id, foreign), email="newhire@example.com"
            )
        with pytest.raises(AdminRequired, match="internal conversation"):
            await _add(
                _context(workspace_id, main_agent, admin_id, foreign),
                email="member@example.com",
            )
    assert await _member_row(workspace_id, "newhire@example.com") is None
    async with workspace_tx() as connection:
        untouched = (
            await connection.execute(
                sa.select(tables.member.c.is_admin).where(tables.member.c.id == member_id)
            )
        ).scalar_one()
    assert untouched is False


async def test_a_workspace_whose_own_address_has_no_domain_still_adds_a_member(db: None) -> None:
    """`ufoctl init --email root` mints a workspace with no domain of its own, which nothing is
    compared against: the adding admin is the authority, not the workspace's own address."""
    workspace_id, main_agent, _, admin_id, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.workspace_id == workspace_id)
            .values(email=sa.func.replace(tables.member.c.email, "@example.com", ""))
        )
    with ws(workspace_id):
        await _add(_context(workspace_id, main_agent, admin_id), email="bob@example.com")
    assert await _member_row(workspace_id, "bob@example.com") is not None
