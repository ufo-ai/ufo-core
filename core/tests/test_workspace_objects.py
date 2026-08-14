"""The `workspace` object kind end to end: one instance named by the workspace id, its seat bounds
and member counts read through the verbs, every mutation refused, and the shape withheld from an
externally shared channel."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml

from ufo.audience import Audience, conversation_audience, foreign_room_audience
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.objects import UnknownObject, VerbNotSupported
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws
from ufo.workspace_kind import WORKSPACE_KIND, WORKSPACE_OBJECT

TOOL_NARRATION = "checking how many seats this workspace has"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError


SEEDED_AT = datetime(2026, 8, 1, tzinfo=UTC)


async def _agent(workspace_id: UUID, *, is_main: bool) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=agent_id.hex[:8],
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=is_main,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _member(
    workspace_id: UUID, email: str, *, seated: bool, admin: bool, created_at: datetime
) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=admin,
                seated_at=created_at if seated else None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return member_id


async def _seed(members: int, seated: int) -> tuple[UUID, UUID, UUID, UUID]:
    """A workspace whose members are created in index order, so the roster's `created_at` ordering
    is observable, beside the main agent every roster read is answered on and a child agent."""
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    speaker_id = None
    for index in range(members):
        member_id = await _member(
            workspace_id,
            f"member{index}@example.com",
            seated=index < seated,
            admin=index == 0,
            created_at=SEEDED_AT + timedelta(minutes=index),
        )
        speaker_id = speaker_id or member_id
    assert speaker_id is not None
    return (
        workspace_id,
        speaker_id,
        await _agent(workspace_id, is_main=True),
        await _agent(workspace_id, is_main=False),
    )


def _context(
    workspace_id: UUID,
    speaker_id: UUID | None,
    agent_id: UUID,
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
            inbound="how many seats do we have",
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="m"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_id,
        audience=audience or conversation_audience(speaker_id),
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


async def test_the_workspace_reads_as_one_object_carrying_its_seat_shape(db: None) -> None:
    workspace_id, speaker_id, main, _ = await _seed(members=3, seated=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id, main)

        kinds = json.loads(await _text(_tool("object_list"), ctx))["kinds"]
        assert WORKSPACE_KIND in {row["kind"] for row in kinds}

        listed = json.loads(await _text(_tool("object_list"), ctx, kind=WORKSPACE_KIND))["objects"]
        assert listed == [
            {
                "name": str(workspace_id),
                "summary": "3 members, 2 seated",
                "members": 3,
                "seated": 2,
            }
        ]

        read = yaml.safe_load(
            await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
        )
        assert read["spec"] == {}
        assert read["status"] == {
            "members": 3,
            "seated": 2,
            "roster": [
                {"email": "member0@example.com", "seated": True, "admin": True},
                {"email": "member1@example.com", "seated": True, "admin": False},
                {"email": "member2@example.com", "seated": False, "admin": False},
            ],
        }
        assert read["links"] == []
        assert isinstance(read["created_at"], str)
        assert isinstance(read["updated_at"], str)

        explained = json.loads(await _text(_tool("object_explain"), ctx, kind=WORKSPACE_KIND))
        assert explained["spec_schema"]["properties"] == {}
        assert explained["spec_schema"]["additionalProperties"] is False

        with pytest.raises(UnknownObject):
            await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(uuid4()))


async def test_the_workspace_kind_filters_and_orders_on_its_declared_fields(db: None) -> None:
    """Every field the kind declares rides its one row, so a caller filters and orders on the seat
    shape without opening the object."""
    workspace_id, speaker_id, main, _ = await _seed(members=4, seated=3)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id, main)
        listing = _tool("object_list")

        row = json.loads(await _text(listing, ctx, kind=WORKSPACE_KIND))["objects"][0]
        assert WORKSPACE_OBJECT.list_fields <= set(row)

        for field, value in (("members", 4), ("seated", 3)):
            matched = json.loads(
                await _text(listing, ctx, kind=WORKSPACE_KIND, filters={field: value})
            )
            assert [entry["name"] for entry in matched["objects"]] == [str(workspace_id)]
            missed = json.loads(
                await _text(listing, ctx, kind=WORKSPACE_KIND, filters={field: value + 1})
            )
            assert missed["objects"] == []

        for order in ("asc", "desc"):
            ordered = json.loads(
                await _text(listing, ctx, kind=WORKSPACE_KIND, order_by="seated", order=order)
            )
            assert [entry["name"] for entry in ordered["objects"]] == [str(workspace_id)]

        with pytest.raises(ValueError, match="unknown object list filters"):
            await _text(listing, ctx, kind=WORKSPACE_KIND, filters={"page_revision": 1})
        with pytest.raises(ValueError, match="unknown object list order field"):
            await _text(listing, ctx, kind=WORKSPACE_KIND, order_by="page_revision")


async def test_every_workspace_mutation_refuses_with_the_path_that_owns_it(db: None) -> None:
    workspace_id, speaker_id, main, _ = await _seed(members=2, seated=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id, main)
        with pytest.raises(VerbNotSupported, match="member object"):
            await _text(
                _tool("object_apply"),
                ctx,
                manifest=yaml.safe_dump(
                    {"kind": WORKSPACE_KIND, "name": str(workspace_id), "spec": {}}
                ),
            )
        with pytest.raises(VerbNotSupported, match="never deleted"):
            await _text(_tool("object_delete"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            await _text(
                _tool("object_apply"),
                ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": WORKSPACE_KIND,
                        "name": str(workspace_id),
                        "spec": {"seated": 9},
                    }
                ),
            )


async def _member_id(workspace_id: UUID, email: str) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == email,
                )
            )
        ).scalar_one()


async def _status(ctx: ToolContext, workspace_id: UUID) -> dict[str, object]:
    read = yaml.safe_load(
        await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
    )
    return read["status"]


async def test_a_member_asking_the_main_agent_reads_who_holds_a_seat(db: None) -> None:
    """The hosted corpus tells customers any member can list who currently holds a seat, so a
    plain member — not just an admin — reads the whole roster on the main agent."""
    workspace_id, _, main, _child = await _seed(members=3, seated=3)
    async with workspace_tx() as connection:
        plain = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.is_admin.is_(False),
                )
            )
        ).first()
    assert plain is not None
    with ws(workspace_id):
        status = await _status(_context(workspace_id, plain.id, main), workspace_id)

    assert status["roster"] == [
        {"email": "member0@example.com", "seated": True, "admin": True},
        {"email": "member1@example.com", "seated": True, "admin": False},
        {"email": "member2@example.com", "seated": True, "admin": False},
    ]


async def test_a_child_agent_and_a_speakerless_turn_never_read_the_staff_list(db: None) -> None:
    """The roster is internal: a child agent answers the speaker's own row alone and a turn with
    no speaker names nobody, exactly as the `member` kind answers them, so no colleague's email or
    admin flag travels to either. A scheduled fire is the speakerless case that still runs on the
    main agent, so it is driven here too. The speaker is neither the admin nor the first row, so a
    gate returning either of those instead of the speaker's own row fails. The counts stay,
    because they name no one."""
    workspace_id, _admin, main, child = await _seed(members=3, seated=2)
    speaker_id = await _member_id(workspace_id, "member2@example.com")
    with ws(workspace_id):
        scoped = await _status(_context(workspace_id, speaker_id, child), workspace_id)
        timer = await _status(_context(workspace_id, None, child), workspace_id)
        scheduled = await _status(_context(workspace_id, None, main), workspace_id)

    assert scoped["roster"] == [{"email": "member2@example.com", "seated": False, "admin": False}]
    assert timer["roster"] == []
    assert scheduled["roster"] == []
    assert (scoped["members"], scoped["seated"]) == (3, 2)


async def test_the_roster_holds_the_order_every_other_seat_read_uses(db: None) -> None:
    """The roster is `Seats.snapshot`'s own `created_at` order — the order `grant_seat` reports —
    not the alphabetical order the emails would otherwise fall into."""
    workspace_id, speaker_id, main, _child = await _seed(members=1, seated=1)
    await _member(
        workspace_id,
        "zoe@example.com",
        seated=False,
        admin=False,
        created_at=SEEDED_AT - timedelta(days=1),
    )
    with ws(workspace_id):
        status = await _status(_context(workspace_id, speaker_id, main), workspace_id)

    assert [entry["email"] for entry in status["roster"]] == [
        "zoe@example.com",
        "member0@example.com",
    ]


async def test_an_externally_shared_channel_hears_no_seat_shape(db: None) -> None:
    """A channel another organization sits in gets the same nothing the `member` kind's roster
    gives it."""
    workspace_id, speaker_id, main, _ = await _seed(members=3, seated=3)
    with ws(workspace_id):
        foreign = _context(
            workspace_id, speaker_id, main, audience=foreign_room_audience("slack", "C-SHARED")
        )
        listed = json.loads(await _text(_tool("object_list"), foreign, kind=WORKSPACE_KIND))
        assert listed["objects"] == []
        with pytest.raises(UnknownObject):
            await _text(_tool("object_get"), foreign, kind=WORKSPACE_KIND, name=str(workspace_id))
