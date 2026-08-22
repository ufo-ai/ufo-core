"""Seat rules proven against the real schema on both dialects: creation seats the member it
writes, the last seated admin is irrevocable, the gate answers a seated member and no one else,
the parked-resume sweep holds a revoked speaker's turn, and the migration seats every existing
member and takes the seat bounds off the workspace."""

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

from ufo.db import MIGRATIONS_DIR, owner_tx, workspace_tx
from ufo.ext.loader import migration_locations
from ufo.hub import Parked
from ufo.runtime.jobs import TurnDispatcher
from ufo.schema import tables
from ufo.schema.records import INTERNAL_ADMISSION, MEMBER_ADMISSION, SCHEDULED_ADMISSION
from ufo.seats import (
    SEAT_REVOKED_MESSAGE,
    LastAdminSeatRevocation,
    Seats,
    UnknownMember,
    create_member,
    gate_member,
    member_by_email,
    member_is_admin,
    workspace_by_domain,
    workspace_domain,
)
from ufo.surfaces.hub_tail import PARK_NOTICE, turn_status_frame
from ufo.workspace import ws

ADMIN_EMAIL = "owner@example.com"
TEAMMATE_EMAIL = "teammate@example.com"


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
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
                is_admin=email == ADMIN_EMAIL,
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


async def test_grant_restores_a_revoked_seat_and_is_idempotent(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    teammate = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, TEAMMATE_EMAIL)
    first = await _seated_at(teammate)
    assert first is not None
    async with workspace_tx() as connection:
        await Seats(workspace_id).grant(connection, "Teammate@Example.com")
    assert await _seated_at(teammate) == first


async def test_grant_unknown_email_raises(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL)
    async with workspace_tx() as connection:
        with pytest.raises(UnknownMember, match="stranger@example"):
            await Seats(workspace_id).grant(connection, "stranger@example.com")


async def test_revoke_unseats_and_is_idempotent(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    teammate = await _member(workspace_id, TEAMMATE_EMAIL, offset_seconds=1)
    for _ in range(2):
        async with workspace_tx() as connection:
            await Seats(workspace_id).revoke(connection, TEAMMATE_EMAIL)
        assert await _seated_at(teammate) is None


async def test_revoke_last_admin_refused(db: None) -> None:
    workspace_id = await _workspace()
    administrator = await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    await _member(workspace_id, TEAMMATE_EMAIL, offset_seconds=1)
    async with workspace_tx() as connection:
        with pytest.raises(LastAdminSeatRevocation):
            await Seats(workspace_id).revoke(connection, ADMIN_EMAIL)
    assert await _seated_at(administrator) is not None


async def test_create_member_seats_every_member_it_writes(db: None) -> None:
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        members = [
            await create_member(connection, workspace_id, f"member{index}@example.com")
            for index in range(3)
        ]
    for member_id in members:
        assert await _seated_at(member_id) is not None


async def test_admits_only_a_seated_member_of_this_workspace(db: None) -> None:
    workspace_id = await _workspace()
    seated = await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    unseated = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    elsewhere = await _member(await _workspace(), ADMIN_EMAIL)
    async with workspace_tx() as connection:
        assert await Seats(workspace_id).admits(connection, seated)
        assert not await Seats(workspace_id).admits(connection, unseated)
        assert not await Seats(workspace_id).admits(connection, elsewhere)
        assert not await Seats(workspace_id).admits(connection, uuid4())


async def test_snapshot_orders_members_and_flags_admin(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    async with workspace_tx() as connection:
        snapshot = await Seats(workspace_id).snapshot(connection)
    assert snapshot.seated == 1
    assert [(entry.email, entry.seated, entry.admin) for entry in snapshot.members] == [
        (ADMIN_EMAIL, True, True),
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
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
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


async def test_sweep_holds_a_parked_aggregate_for_every_pending_speaker(db: None) -> None:
    workspace_id = await _workspace()
    founder = await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
    pending = await _member(workspace_id, TEAMMATE_EMAIL, seated=False, offset_seconds=1)
    turn_id = await _parked_turn(workspace_id, founder)
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="pending speaker",
                admission_source=MEMBER_ADMISSION,
                speaker_member_id=pending,
                admitted_turn_id=turn_id,
                created_at=sa.func.now(),
            )
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


async def test_sweep_resumes_a_speakerless_parked_turn(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL)
    turn_id = await _parked_turn(workspace_id, None)
    dbos = _StubDbos()
    with ws(workspace_id):
        await TurnDispatcher(client=dbos).run()
    assert dbos.enqueued == [str(turn_id)]


def test_migration_seats_every_member_and_drops_the_seat_bounds(tmp_path: Path) -> None:
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
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
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
        connection.execute(
            sa.text(
                "insert into agent "
                "(id, workspace_id, name, prompt, model, created_at, updated_at) "
                "values (:id, :workspace_id, 'assistant', 'p', 'm', :created, :created)"
            ),
            {"id": agent_id.hex, "workspace_id": workspace_id.hex, "created": created},
        )
        connection.commit()
    command.upgrade(config, "heads")
    with engine.connect() as connection:
        row = connection.execute(sa.text("select seated_at, created_at from member")).one()
        columns = {
            column.name
            for column in connection.execute(sa.text("pragma table_info(workspace)")).all()
        }
    engine.dispose()
    assert row.seated_at == row.created_at
    assert "seat_limit" not in columns
    assert "included_seats" not in columns


def test_gate_member_is_the_speaker_else_the_acting_member() -> None:
    speaker, acting = uuid4(), uuid4()
    assert gate_member(speaker, acting) == speaker
    assert gate_member(speaker, None) == speaker
    assert gate_member(None, acting) == acting
    assert gate_member(None, None) is None


async def test_sweep_holds_a_scheduled_turn_for_an_unseated_creator(db: None) -> None:
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
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
    workspace_id = await _workspace()
    await _member(workspace_id, ADMIN_EMAIL, offset_seconds=0)
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
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        first = await create_member(connection, workspace_id, ADMIN_EMAIL)
        second = await create_member(connection, workspace_id, ADMIN_EMAIL)
    assert second == first
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count()).where(tables.member.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert rows == 1
    assert await _seated_at(first) is not None


async def test_create_member_lowercases_the_address_it_writes(db: None) -> None:
    """Member uniqueness compares bytes, so the address as typed and the address a sign-in resolves
    have to be one row: the second creation collapses onto the first instead of adding a twin no
    lookup can tell apart."""
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        first = await create_member(connection, workspace_id, "Owner@Example.COM")
        second = await create_member(connection, workspace_id, ADMIN_EMAIL)
    assert second == first
    async with workspace_tx() as connection:
        emails = (
            (
                await connection.execute(
                    sa.select(tables.member.c.email).where(
                        tables.member.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert emails == [ADMIN_EMAIL]


async def test_member_by_email_answers_the_row_in_the_workspace_asked_for(db: None) -> None:
    """One person, seated in two workspaces, holding one address. A session proves the address; it
    cannot prove which workspace's member it is. So the lookup is scoped in the query, and callers
    that read an admin flag off the answer depend on it: unscoped, the same address would carry the
    other workspace's flag here, and a guest would read what an admin reads."""
    here, elsewhere = await _workspace(), await _workspace()
    mine = await _member(here, TEAMMATE_EMAIL)
    theirs = await _member(elsewhere, TEAMMATE_EMAIL, offset_seconds=60)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == theirs).values(is_admin=True)
        )
        with ws(here):
            found = await member_by_email(connection, here, TEAMMATE_EMAIL)
            assert found == mine
            assert await member_is_admin(connection, here, found) is False
        with ws(elsewhere):
            assert await member_by_email(connection, elsewhere, TEAMMATE_EMAIL) == theirs


async def test_member_by_email_never_creates_a_member(db: None) -> None:
    """The billing page resolves whoever the session names. An address that was never seated must
    answer nothing, not become a member of the workspace it asked about."""
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        with ws(workspace_id):
            assert await member_by_email(connection, workspace_id, "nobody@example.com") is None
            seated = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.member))
            ).scalar_one()
    assert seated == 0


async def test_a_domain_resolves_to_the_workspace_its_first_member_holds(db: None) -> None:
    """The inverse of `workspace_domain`: what the derivation prints for a workspace is what
    resolves back to it. A workspace holds an id of its own, unrelated to its domain, so nothing
    but this lookup can turn an address an operator types into the workspace they meant."""
    mine, other = await _workspace(), await _workspace()
    await _member(mine, "owner@acme.com")
    await _member(other, "owner@beta.io")
    async with owner_tx() as connection:
        assert await workspace_by_domain(connection, "acme.com") == mine
        assert await workspace_by_domain(connection, "beta.io") == other
        assert await workspace_by_domain(connection, "nobody.example") is None
    async with workspace_tx() as connection:
        with ws(mine):
            assert await workspace_domain(connection, mine) == "acme.com"


async def test_only_the_first_member_names_the_workspace(db: None) -> None:
    """A later hire at another domain does not give the workspace a second address, and the
    workspace they joined is not reachable by theirs — the derivation reads one member, so the
    lookup must match on that same one."""
    workspace_id = await _workspace()
    await _member(workspace_id, "owner@acme.com")
    await _member(workspace_id, "contractor@other.com", offset_seconds=60)
    async with owner_tx() as connection:
        assert await workspace_by_domain(connection, "acme.com") == workspace_id
        assert await workspace_by_domain(connection, "other.com") is None


async def test_the_oldest_seating_wins_a_domain_two_workspaces_share(db: None) -> None:
    """Two workspaces seated at one domain is a fleet a sign-in refuses to choose between. An
    operator reading a directory must still reach a workspace, so the older seating answers and
    the answer does not move between requests."""
    older, newer = await _workspace(), await _workspace()
    await _member(older, "owner@acme.com")
    await _member(newer, "founder@acme.com", offset_seconds=60)
    async with owner_tx() as connection:
        assert await workspace_by_domain(connection, "acme.com") == older
        assert await workspace_by_domain(connection, "acme.com") == older


async def test_a_malformed_domain_matches_nothing(db: None) -> None:
    """`?ws=` carries whatever was typed. A wildcard, an address, or blank text must answer nothing
    rather than pattern-match its way into a workspace."""
    workspace_id = await _workspace()
    await _member(workspace_id, "owner@acme.com")
    async with owner_tx() as connection:
        for typed in ("%", "%.com", "acme_com", "owner@acme.com", " ", ""):
            assert await workspace_by_domain(connection, typed) is None
