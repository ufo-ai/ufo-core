"""Seats: who the agent answers. A seat is a member the agent responds to;
`workspace.seat_limit` bounds how many can hold one, and `workspace.included_seats` bounds how
many are handed out silently — auto-seat fills the included allowance and stops, so every seat
beyond it is an explicit admin grant (the billed-overage consent). Both NULL means unlimited, the
shape every deploy without a billing extension keeps, where the gate admits everyone and costs
nothing. Core owns the count, the last seated admin's irrevocable seat, and member creation, so
the admission gate, per-round enforcement, resume sweep, and a billing extension's tools apply the
same ones; an extension only decides when to call them. A refused member still exists (identity,
memory subject): the gate answers their turn with the refusal, and a granted seat simply lets them
speak again."""

import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.schema import tables
from ufo.schema.records import SCHEDULED_ADMISSION, TurnAdmissionSource

SEAT_REFUSAL_MESSAGE = (
    "This workspace has no open seat for you yet — a workspace admin has been asked to grant "
    "one; you'll be answered once they do."
)
UNRESOLVED_SPEAKER_MESSAGE = (
    "I can only answer workspace members, and I couldn't verify who you are. Make sure your work "
    "email is confirmed and visible on your profile, then try again — or ask a workspace admin "
    "to add you."
)
SEAT_REVOKED_MESSAGE = (
    "This turn is parked: the speaker's seat was revoked. It resumes if the seat is granted again."
)

SEAT_PRESENCE_TTL_SECONDS = 5.0
SEAT_PRESENCE_CACHE_MAX = 4096
_no_seat_limit: dict[UUID, float] = {}


def gate_member(
    speaker_member_id: UUID | None,
    admission_source: TurnAdmissionSource,
    on_behalf_of_member_id: UUID | None,
) -> UUID | None:
    """The member a turn is seat-gated on: its speaker; for a scheduled fire, the member it acts on
    behalf of (its creator), so an unseated member's scheduled job is refused even when it fires
    into a shared channel that has no conversation member; an internal turn none. The one
    derivation admission, the fold resume, the dispatch sweep, and the per-round check all share —
    the gate cannot fork on who it means."""
    if speaker_member_id is not None:
        return speaker_member_id
    if admission_source == SCHEDULED_ADMISSION:
        return on_behalf_of_member_id
    return None


def seat_gate_absent(workspace_id: UUID) -> bool:
    """Connectionless fast-path mirroring `applicable_caps_absent`: True only when a recent read
    found no seat limit on this workspace, within a short TTL. The per-round enforcement then
    skips its DB round-trip — the common unlimited deploy pays nothing per round, and a
    newly-established limit takes effect within the TTL."""
    expiry = _no_seat_limit.get(workspace_id)
    return expiry is not None and expiry > time.monotonic()


def _note_absent_limit(workspace_id: UUID) -> None:
    now = time.monotonic()
    if len(_no_seat_limit) >= SEAT_PRESENCE_CACHE_MAX:
        for expired in [key for key, expiry in _no_seat_limit.items() if expiry <= now]:
            del _no_seat_limit[expired]
    _no_seat_limit[workspace_id] = now + SEAT_PRESENCE_TTL_SECONDS


class SeatLimitReached(RuntimeError):
    """Every seat is taken: granting another needs a revoke first or a raised limit."""


class UnknownMember(LookupError):
    """The email names no member of this workspace."""


class LastAdminSeatRevocation(RuntimeError):
    """The last seated admin cannot be unseated because seat management happens in chat."""


@dataclass(frozen=True, slots=True)
class SeatEntry:
    id: UUID
    email: str
    seated: bool
    admin: bool


@dataclass(frozen=True, slots=True)
class SeatSnapshot:
    limit: int | None
    included: int | None
    members: tuple[SeatEntry, ...]

    @property
    def seated(self) -> int:
        return sum(1 for entry in self.members if entry.seated)


@dataclass(frozen=True)
class Seats:
    """One workspace's seat state and the validated writes over it. Every method takes the
    caller's connection, so the same rule runs inside admission's transaction, the engine's
    per-round check, and an extension tool's `ctx.transaction()` — the rules cannot fork."""

    workspace_id: UUID

    async def gated(self, connection: AsyncConnection) -> bool:
        """Whether this workspace enforces seats at all — a limit or an included allowance is
        set. Ungated workspaces (every deploy without a billing extension) answer everyone,
        resolvable or not."""
        row = (
            await connection.execute(
                sa.select(tables.workspace.c.seat_limit, tables.workspace.c.included_seats).where(
                    tables.workspace.c.id == self.workspace_id
                )
            )
        ).one()
        if row.seat_limit is None and row.included_seats is None:
            _note_absent_limit(self.workspace_id)
            return False
        return True

    async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool:
        """Whether the gate answers this member: an ungated workspace (no limit, no included
        allowance) admits everyone and primes the fast-path; a gated one, only a seated member."""
        row = (
            await connection.execute(
                sa.select(
                    tables.member.c.seated_at,
                    tables.workspace.c.seat_limit,
                    tables.workspace.c.included_seats,
                )
                .select_from(
                    tables.member.join(
                        tables.workspace,
                        tables.member.c.workspace_id == tables.workspace.c.id,
                    )
                )
                .where(
                    tables.member.c.id == member_id,
                    tables.member.c.workspace_id == self.workspace_id,
                )
            )
        ).one_or_none()
        if row is None:
            return False
        if row.seat_limit is None and row.included_seats is None:
            _note_absent_limit(self.workspace_id)
            return True
        return row.seated_at is not None

    async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot:
        bounds = (
            await connection.execute(
                sa.select(tables.workspace.c.seat_limit, tables.workspace.c.included_seats).where(
                    tables.workspace.c.id == self.workspace_id
                )
            )
        ).one()
        rows = (
            await connection.execute(
                sa.select(
                    tables.member.c.id,
                    tables.member.c.email,
                    tables.member.c.seated_at,
                    tables.member.c.is_admin,
                )
                .where(tables.member.c.workspace_id == self.workspace_id)
                .order_by(tables.member.c.created_at.asc(), tables.member.c.id.asc())
            )
        ).all()
        return SeatSnapshot(
            limit=bounds.seat_limit,
            included=bounds.included_seats,
            members=tuple(
                SeatEntry(
                    id=row.id,
                    email=row.email,
                    seated=row.seated_at is not None,
                    admin=row.is_admin,
                )
                for row in rows
            ),
        )

    async def grant(self, connection: AsyncConnection, email: str) -> None:
        """Seat the member with this email. Idempotent for an already-seated member; raises
        `SeatLimitReached` when every seat is taken. The workspace-row lock serializes concurrent
        counts."""
        limit, _ = await self._locked_limits(connection)
        member_id, seated_at, _ = await self._member_by_email(connection, email)
        if seated_at is not None:
            return
        if limit is not None and await self._seated_count(connection) >= limit:
            raise SeatLimitReached(
                f"all {limit} seats are taken — revoke one, or contact us to raise the limit"
            )
        await self._seat(connection, member_id)

    async def revoke(self, connection: AsyncConnection, email: str) -> None:
        """Unseat the member with this email; idempotent. Refuses to unseat the last seated admin.
        In-flight turns are never touched here — admission refuses the member's next message
        immediately, and the per-round enforcement parks any running turn before its next model
        call."""
        await self._locked_limits(connection)
        member_id, seated_at, is_admin = await self._member_by_email(connection, email)
        if seated_at is None:
            return
        if is_admin and await self._seated_admin_count(connection) == 1:
            raise LastAdminSeatRevocation("the last seated workspace admin cannot be unseated")
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=None, updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )

    async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None:
        """Establish the seat limit once: writes only while it is NULL, so a re-fire or an
        operator-tuned value is never overwritten."""
        if limit < 1:
            raise ValueError(f"seat limit must be positive, got {limit}")
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=limit, updated_at=sa.func.now())
            .where(
                tables.workspace.c.id == self.workspace_id,
                tables.workspace.c.seat_limit.is_(None),
            )
        )

    async def ensure_included(self, connection: AsyncConnection, included: int) -> None:
        """Establish the silent-seat allowance once — the plan's included quantity; writes only
        while it is NULL, the same establishment rule as the limit."""
        if included < 1:
            raise ValueError(f"included seats must be positive, got {included}")
        await connection.execute(
            sa.update(tables.workspace)
            .values(included_seats=included, updated_at=sa.func.now())
            .where(
                tables.workspace.c.id == self.workspace_id,
                tables.workspace.c.included_seats.is_(None),
            )
        )

    async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None:
        """Seat a just-created member while a silent seat is open — the included allowance when
        one is set, else the hard limit. Beyond it, leave them unseated and return: creation
        always succeeds, the gate answers them with the refusal, and only an explicit admin grant
        (the billed-overage consent) seats them."""
        limit, included = await self._locked_limits(connection)
        bound = included if included is not None else limit
        if bound is not None and await self._seated_count(connection) >= bound:
            return
        await self._seat(connection, member_id)

    async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]:
        row = (
            await connection.execute(
                sa.select(tables.workspace.c.seat_limit, tables.workspace.c.included_seats)
                .where(tables.workspace.c.id == self.workspace_id)
                .with_for_update()
            )
        ).one()
        return row.seat_limit, row.included_seats

    async def _member_by_email(
        self, connection: AsyncConnection, email: str
    ) -> tuple[UUID, datetime | None, bool]:
        row = (
            await connection.execute(
                sa.select(
                    tables.member.c.id,
                    tables.member.c.seated_at,
                    tables.member.c.is_admin,
                ).where(
                    tables.member.c.workspace_id == self.workspace_id,
                    sa.func.lower(tables.member.c.email) == email.strip().lower(),
                )
            )
        ).one_or_none()
        if row is None:
            raise UnknownMember(f"no member with email {email!r} in this workspace")
        return row.id, row.seated_at, row.is_admin

    async def _seated_count(self, connection: AsyncConnection) -> int:
        return (
            await connection.execute(
                sa.select(sa.func.count()).where(
                    tables.member.c.workspace_id == self.workspace_id,
                    tables.member.c.seated_at.is_not(None),
                )
            )
        ).scalar_one()

    async def _seated_admin_count(self, connection: AsyncConnection) -> int:
        return (
            await connection.execute(
                sa.select(sa.func.count()).where(
                    tables.member.c.workspace_id == self.workspace_id,
                    tables.member.c.seated_at.is_not(None),
                    tables.member.c.is_admin,
                )
            )
        ).scalar_one()

    async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )


async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool:
    return bool(
        (
            await connection.execute(
                sa.select(tables.member.c.is_admin).where(
                    tables.member.c.id == member_id,
                    tables.member.c.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
    )


async def create_member(
    connection: AsyncConnection,
    workspace_id: UUID,
    email: str,
    *,
    is_admin: bool = False,
) -> UUID:
    """The one member-creation write: every surface that mints a member — onboarding's admin, a
    channel-verified teammate join, hosted onboarding, whatever joins next — inserts through
    here, so the seat rule is applied structurally rather than remembered per call site. A lost
    creation race collapses on the member's (workspace_id, email) uniqueness and answers the
    surviving row, which the racing winner already seated."""
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    created = (
        await connection.execute(
            insert(tables.member)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                email=email,
                is_admin=is_admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[tables.member.c.workspace_id, tables.member.c.email]
            )
            .returning(tables.member.c.id)
        )
    ).scalar_one_or_none()
    if created is not None:
        await Seats(workspace_id).auto_seat(connection, created)
        return created
    return (
        await connection.execute(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.email == email,
            )
        )
    ).scalar_one()


async def admin_conversation(
    connection: AsyncConnection, workspace_id: UUID
) -> tuple[UUID, UUID] | None:
    """Where a workspace-level ask reaches an admin: the most recently active private conversation
    of a seated admin on the main agent. None before any seated admin's first private conversation
    with the main agent."""
    conversation = (
        await connection.execute(
            sa.select(tables.conversation.c.id, tables.conversation.c.agent_id)
            .select_from(
                tables.conversation.join(
                    tables.member,
                    tables.conversation.c.member_id == tables.member.c.id,
                ).join(
                    tables.agent,
                    tables.conversation.c.agent_id == tables.agent.c.id,
                ),
            )
            .where(
                tables.conversation.c.workspace_id == workspace_id,
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.is_admin,
                tables.member.c.seated_at.is_not(None),
                tables.agent.c.is_main,
            )
            .order_by(tables.conversation.c.updated_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if conversation is None:
        return None
    return conversation.id, conversation.agent_id


def member_workspaces() -> WorkspaceCandidates:
    """Candidates for a seat-reporting job: every workspace with a member — one indexed distinct
    read, coarse on purpose. Core owns the `member` table, so it owns this query; an extension
    declares `candidates=member_workspaces()` without reaching `owner_tx`."""

    def with_a_member() -> sa.Select[tuple[UUID]]:
        return sa.select(tables.member.c.workspace_id).distinct()

    return owner_candidates(with_a_member)
