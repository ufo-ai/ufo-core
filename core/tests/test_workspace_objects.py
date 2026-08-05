"""The `workspace` object kind end to end: one instance named by the workspace id, its seat bounds
and member counts read through the verbs, every mutation refused, and the shape withheld from an
externally shared channel."""

import json
from datetime import UTC, datetime
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


async def _seed(members: int, seated: int) -> tuple[UUID, UUID]:
    workspace_id, speaker_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for index in range(members):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=speaker_id if index == 0 else uuid4(),
                    workspace_id=workspace_id,
                    email=f"member{index}@example.com",
                    is_admin=index == 0,
                    seated_at=sa.func.now() if index < seated else None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, speaker_id


async def _bound(workspace_id: UUID, seat_limit: int, included_seats: int) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=seat_limit, included_seats=included_seats, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )


def _context(workspace_id: UUID, speaker_id: UUID, audience: Audience | None = None) -> ToolContext:
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
            agent_id=uuid4(),
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
    workspace_id, speaker_id = await _seed(members=3, seated=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id)

        kinds = json.loads(await _text(_tool("object_list"), ctx))["kinds"]
        assert WORKSPACE_KIND in {row["kind"] for row in kinds}

        ungated = json.loads(await _text(_tool("object_list"), ctx, kind=WORKSPACE_KIND))["objects"]
        assert ungated == [
            {
                "name": str(workspace_id),
                "summary": "3 members, 2 seated, no seat limit",
                "seat_limit": None,
                "included_seats": None,
                "members": 3,
                "seated": 2,
            }
        ]

        await _bound(workspace_id, seat_limit=5, included_seats=2)
        gated = json.loads(await _text(_tool("object_list"), ctx, kind=WORKSPACE_KIND))["objects"]
        assert gated[0]["summary"] == "3 members, 2 seated, seat limit 5"

        read = yaml.safe_load(
            await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
        )
        assert read["spec"] == {}
        assert read["status"] == {
            "seat_limit": 5,
            "included_seats": 2,
            "members": 3,
            "seated": 2,
            "billed_overage_seats": 0,
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
    workspace_id, speaker_id = await _seed(members=4, seated=3)
    await _bound(workspace_id, seat_limit=6, included_seats=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id)
        listing = _tool("object_list")

        row = json.loads(await _text(listing, ctx, kind=WORKSPACE_KIND))["objects"][0]
        assert WORKSPACE_OBJECT.list_fields <= set(row)

        for field, value in (
            ("seat_limit", 6),
            ("included_seats", 2),
            ("members", 4),
            ("seated", 3),
        ):
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
    workspace_id, speaker_id = await _seed(members=2, seated=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id)
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
        with pytest.raises(ValueError, match="seat_limit"):
            await _text(
                _tool("object_apply"),
                ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": WORKSPACE_KIND,
                        "name": str(workspace_id),
                        "spec": {"seat_limit": 9},
                    }
                ),
            )
        async with workspace_tx() as connection:
            bounds = (
                await connection.execute(
                    sa.select(
                        tables.workspace.c.seat_limit, tables.workspace.c.included_seats
                    ).where(tables.workspace.c.id == workspace_id)
                )
            ).one()
        assert (bounds.seat_limit, bounds.included_seats) == (None, None)


async def test_any_member_reads_who_holds_a_seat_and_what_bills_as_overage(db: None) -> None:
    """The hosted corpus tells customers any member can list who holds a seat, so the roster
    answers a plain member on any agent — and the seats past the included allowance are named
    beside it, because that is what an admin approving one is told they cost."""
    workspace_id, _ = await _seed(members=3, seated=3)
    await _bound(workspace_id, seat_limit=5, included_seats=1)
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
        ctx = _context(workspace_id, plain.id)
        read = yaml.safe_load(
            await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
        )

    assert read["status"]["billed_overage_seats"] == 2
    assert read["status"]["roster"] == [
        {"email": "member0@example.com", "seated": True, "admin": True},
        {"email": "member1@example.com", "seated": True, "admin": False},
        {"email": "member2@example.com", "seated": True, "admin": False},
    ]


async def test_an_ungated_workspace_bills_no_overage(db: None) -> None:
    workspace_id, speaker_id = await _seed(members=2, seated=2)
    with ws(workspace_id):
        ctx = _context(workspace_id, speaker_id)
        read = yaml.safe_load(
            await _text(_tool("object_get"), ctx, kind=WORKSPACE_KIND, name=str(workspace_id))
        )
    assert read["status"]["billed_overage_seats"] == 0


async def test_an_externally_shared_channel_hears_no_seat_shape(db: None) -> None:
    """The seat shape is the roster in aggregate, so a channel another organization sits in gets
    the same nothing the `member` kind's roster gives it."""
    workspace_id, speaker_id = await _seed(members=3, seated=3)
    with ws(workspace_id):
        foreign = _context(
            workspace_id, speaker_id, audience=foreign_room_audience("slack", "C-SHARED")
        )
        listed = json.loads(await _text(_tool("object_list"), foreign, kind=WORKSPACE_KIND))
        assert listed["objects"] == []
        with pytest.raises(UnknownObject):
            await _text(_tool("object_get"), foreign, kind=WORKSPACE_KIND, name=str(workspace_id))
