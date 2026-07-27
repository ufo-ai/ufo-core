"""Seat rules proven against the real schema on both dialects: grants count and stop at the
limit, the owner's seat is irrevocable, creation always succeeds and seats while a seat is open,
an unlimited workspace admits everyone (and primes the per-round fast-path), the parked-resume
sweep holds a revoked speaker's turn, and the migration backfills every existing member as
seated."""

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from dbos import EnqueueOptions

from ufo.db import MIGRATIONS_DIR, workspace_tx
from ufo.ext.loader import migration_locations
from ufo.hub import Parked
from ufo.jobs import TurnDispatcher
from ufo.schema import tables
from ufo.schema.records import INTERNAL_ADMISSION, MEMBER_ADMISSION, SCHEDULED_ADMISSION
from ufo.seats import (
    SEAT_REVOKED_MESSAGE,
    OwnerSeatRevocation,
    SeatLimitReached,
    Seats,
    UnknownMember,
    create_member,
    gate_member,
    owner_conversation,
    seat_gate_absent,
)
from ufo.surfaces.hub_tail import PARK_NOTICE, turn_status_frame
from ufo.workspace import ws

OWNER_EMAIL = "owner@example.com"
TEAMMATE_EMAIL = "teammate@example.com"


async def _workspace(limit: int | None) -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                seat_limit=limit,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _member(
    workspace_id: UUID, email: str, *, seated: bool = True, offset_seconds: int = 0
) -> UUID:
    member_id = uuid4()
    created = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                seated_at=created if seated else None,
                created_at=created,
                updated_at=created,
            )
        )
    return member_id


async def _seated_at(member_id: UUID) -> datetime | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.seated_at).where(tables.member.c.id == member_id)
            )
        ).scalar_one()


async def test_grant_seats_under_limit_and_is_idempotent(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    teammate = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    first = await _seated_at(teammate)
    assert first is not None
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, "Teammate@Example.com")
    assert await _seated_at(teammate) == first


async def test_grant_at_limit_raises(db: None) -> None:
    workspace_id = await _workspace(limit=1)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    teammate = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        with pytest.raises(SeatLimitReached, match="all 1 seats"):
            await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    assert await _seated_at(teammate) is None


async def test_grant_unknown_email_raises(db: None) -> None:
    workspace_id = await _workspace(limit=5)
    await _member(workspace_id, OWNER_EMAIL)
    async with workspace_tx() as connection:
        with pytest.raises(UnknownMember, match="stranger@example"):
            await Seats(workspace_id).grant(connection, "stranger@example.com")


async def test_revoke_unseats_and_is_idempotent(db: None) -> None:
    workspace_id = await _workspace(limit=5)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    teammate = await _member(workspace_id, TEAMMATE_EMAIL, offset_seconds=1)
    for _ in range(2):
        async with workspace_tx() as connection:
            await Seats(workspace_id).revoke(connection, TEAMMATE_EMAIL)
        assert await _seated_at(teammate) is None


async def test_revoke_owner_refused(db: None) -> None:
    workspace_id = await _workspace(limit=5)
    owner = await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    await _member(workspace_id, TEAMMATE_EMAIL, offset_seconds=1)
    async with workspace_tx() as connection:
        with pytest.raises(OwnerSeatRevocation):
            await Seats(workspace_id).revoke(connection, OWNER_EMAIL)
    assert await _seated_at(owner) is not None


async def test_ensure_limit_writes_only_when_null(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    async with workspace_tx() as connection:
        await Seats(workspace_id).ensure_limit(connection, 5)
        await Seats(workspace_id).ensure_limit(connection, 3)
        limit = (
            await connection.execute(
                sa.select(tables.workspace.c.seat_limit).where(
                    tables.workspace.c.id == workspace_id
                )
            )
        ).scalar_one()
    assert limit == 5


async def test_ensure_limit_rejects_nonpositive(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    async with workspace_tx() as connection:
        with pytest.raises(ValueError, match="positive"):
            await Seats(workspace_id).ensure_limit(connection, 0)


async def test_create_member_seats_while_open_and_leaves_unseated_at_limit(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    async with workspace_tx() as connection:
        owner = await create_member(connection, workspace_id, OWNER_EMAIL)
        second = await create_member(connection, workspace_id, TEAMMATE_EMAIL)
        third = await create_member(connection, workspace_id, "third@example.com")
    assert await _seated_at(owner) is not None
    assert await _seated_at(second) is not None
    assert await _seated_at(third) is None


async def test_create_member_seats_everyone_without_a_limit(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    async with workspace_tx() as connection:
        members = [
            await create_member(connection, workspace_id, f"member{index}@example.com")
            for index in range(3)
        ]
    for member_id in members:
        assert await _seated_at(member_id) is not None


async def test_admits_everyone_without_a_limit_and_primes_the_fast_path(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    unseated = await _member(workspace_id, OWNER_EMAIL, seated=False)
    assert not seat_gate_absent(workspace_id)
    async with workspace_tx() as connection:
        assert await Seats(workspace_id).admits(connection, unseated)
    assert seat_gate_absent(workspace_id)


async def test_admits_only_seated_members_under_a_limit(db: None) -> None:
    workspace_id = await _workspace(limit=1)
    seated = await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    unseated = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        assert await Seats(workspace_id).admits(connection, seated)
        assert not await Seats(workspace_id).admits(connection, unseated)
    assert not seat_gate_absent(workspace_id)


async def test_snapshot_orders_members_and_flags_owner(db: None) -> None:
    workspace_id = await _workspace(limit=5)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        snapshot = await Seats(workspace_id).snapshot(connection)
    assert snapshot.limit == 5
    assert snapshot.seated == 1
    assert [(entry.email, entry.seated, entry.owner) for entry in snapshot.members] == [
        (OWNER_EMAIL, True, True),
        (TEAMMATE_EMAIL, False, False),
    ]


@dataclass
class _StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _parked_turn(
    workspace_id: UUID,
    speaker_member_id: UUID | None,
    conversation_member_id: UUID | None = None,
    admission_source: str = INTERNAL_ADMISSION,
    on_behalf_of_member_id: UUID | None = None,
) -> UUID:
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=(
                    conversation_member_id
                    if conversation_member_id is not None
                    else speaker_member_id
                ),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="parked",
                inbound="hello",
                speaker_member_id=speaker_member_id,
                on_behalf_of_member_id=on_behalf_of_member_id,
                admission_source=admission_source,
                created_at=sa.func.now() - timedelta(hours=1),
                updated_at=sa.func.now() - timedelta(hours=1),
            )
        )
    return turn_id


async def test_sweep_holds_a_revoked_speakers_parked_turn_until_regranted(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    speaker = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    turn_id = await _parked_turn(workspace_id, speaker)
    dbos = _StubDbos()
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == [str(turn_id)]


async def test_sweep_resumes_a_speakerless_parked_turn(db: None) -> None:
    workspace_id = await _workspace(limit=1)
    await _member(workspace_id, OWNER_EMAIL)
    turn_id = await _parked_turn(workspace_id, None)
    dbos = _StubDbos()
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == [str(turn_id)]


def test_migration_backfills_existing_members_as_seated(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}"
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0038")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    workspace_id, member_id = uuid4(), uuid4()
    created = datetime(2026, 7, 1, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :created, :created)"
            ),
            {"id": workspace_id.hex, "created": created},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :workspace_id, 'owner@example.com', :created, :created)"
            ),
            {"id": member_id.hex, "workspace_id": workspace_id.hex, "created": created},
        )
        connection.commit()
    command.upgrade(config, "heads")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "select member.seated_at, member.created_at, workspace.seat_limit from member "
                "join workspace on workspace.id = member.workspace_id"
            )
        ).one()
    engine.dispose()
    assert row.seated_at == row.created_at
    assert row.seat_limit is None


def test_gate_member_is_the_speaker_else_the_scheduled_acting_member() -> None:
    speaker, acting = uuid4(), uuid4()
    assert gate_member(speaker, MEMBER_ADMISSION, acting) == speaker
    assert gate_member(speaker, SCHEDULED_ADMISSION, acting) == speaker
    assert gate_member(None, SCHEDULED_ADMISSION, acting) == acting
    assert gate_member(None, SCHEDULED_ADMISSION, None) is None
    assert gate_member(None, INTERNAL_ADMISSION, acting) is None


async def test_sweep_holds_a_scheduled_turn_for_an_unseated_creator(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    member = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    turn_id = await _parked_turn(
        workspace_id,
        None,
        conversation_member_id=None,
        admission_source=SCHEDULED_ADMISSION,
        on_behalf_of_member_id=member,
    )
    dbos = _StubDbos()
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == [str(turn_id)]


async def test_park_notice_names_the_seat_when_the_gate_member_lost_it(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    speaker = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    turn_id = await _parked_turn(workspace_id, speaker)
    with ws(workspace_id):
        frame = await turn_status_frame(turn_id)
    assert frame == Parked(message=SEAT_REVOKED_MESSAGE)
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    with ws(workspace_id):
        frame = await turn_status_frame(turn_id)
    assert frame == Parked(message=PARK_NOTICE)


async def test_create_member_collapses_a_lost_race_onto_the_surviving_row(db: None) -> None:
    workspace_id = await _workspace(limit=2)
    async with workspace_tx() as connection:
        first = await create_member(connection, workspace_id, OWNER_EMAIL)
        second = await create_member(connection, workspace_id, OWNER_EMAIL)
    assert second == first
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count()).where(tables.member.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert rows == 1
    assert await _seated_at(first) is not None


async def _set_included(workspace_id: UUID, included: int) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=included, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )


async def test_auto_seat_stops_at_the_included_allowance(db: None) -> None:
    workspace_id = await _workspace(limit=25)
    await _set_included(workspace_id, 1)
    async with workspace_tx() as connection:
        first = await create_member(connection, workspace_id, OWNER_EMAIL)
        second = await create_member(connection, workspace_id, TEAMMATE_EMAIL)
    assert await _seated_at(first) is not None
    assert await _seated_at(second) is None
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    assert await _seated_at(second) is not None


async def test_ensure_included_writes_only_when_null(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    async with workspace_tx() as connection:
        await Seats(workspace_id).ensure_included(connection, 5)
        await Seats(workspace_id).ensure_included(connection, 3)
        with pytest.raises(ValueError, match="positive"):
            await Seats(workspace_id).ensure_included(connection, 0)
        included = (
            await connection.execute(
                sa.select(tables.workspace.c.included_seats).where(
                    tables.workspace.c.id == workspace_id
                )
            )
        ).scalar_one()
    assert included == 5


async def test_admits_gates_on_included_even_without_a_hard_limit(db: None) -> None:
    workspace_id = await _workspace(limit=None)
    await _set_included(workspace_id, 1)
    seated = await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    unseated = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        assert await Seats(workspace_id).admits(connection, seated)
        assert not await Seats(workspace_id).admits(connection, unseated)
        assert await Seats(workspace_id).gated(connection)


async def test_owner_conversation_answers_with_the_conversations_bound_agent(db: None) -> None:
    workspace_id = await _workspace(limit=25)
    owner = await _member(workspace_id, OWNER_EMAIL, offset_seconds=0)
    async with workspace_tx() as connection:
        assert await owner_conversation(connection, workspace_id) is None
    earliest_agent, bound_agent, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for agent_id, name, created_at in (
            (earliest_agent, "assistant", datetime(2026, 7, 1, tzinfo=UTC)),
            (bound_agent, "exec", datetime(2026, 7, 2, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="claude-opus-4-8",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=bound_agent,
                surface="slack",
                queue_key="dm",
                member_id=owner,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    async with workspace_tx() as connection:
        venue = await owner_conversation(connection, workspace_id)
    assert venue == (conversation_id, bound_agent)
