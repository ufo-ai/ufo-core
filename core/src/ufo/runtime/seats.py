"""Seats: who the agent answers. A member holds a seat from creation and the agent answers them;
an admin revokes it to remove that person's access, which is the only way to remove it — the
`member` kind refuses delete, because the row is an identity and a memory subject that outlives
the access. Nothing bounds how many members hold one: the workspace pays one flat fee and the
count is an outreach figure, never a gate. Core owns seating, the last seated admin's irrevocable
seat, member creation, and liveness enforcement at each speaking boundary. An unseated member still
exists: the gate answers their turn with the refusal, and seating them again simply lets them
speak."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.schema import tables

SEAT_REFUSAL_MESSAGE = (
    "A workspace admin removed your seat, so I can't answer you. Ask them to restore it."
)
UNRESOLVED_SPEAKER_MESSAGE = (
    "I can only answer workspace members, and I couldn't verify who you are. Make sure your "
    "email is confirmed and visible on your profile, then try again — or ask a workspace admin "
    "to add you."
)
SEAT_REVOKED_MESSAGE = (
    "This turn is parked: the speaker's seat was revoked. It resumes if the seat is granted again."
)


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

    async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool:
        """Whether every one of these members still holds a seat. One indexed read for the whole
        set, so a turn that absorbed six speakers costs the same round-trip as one — this is the
        question the per-round check, the parked-fold check, and the dispatch sweep all ask, and
        each of them asks it about a set. An id that is not a member of this workspace is not
        seated."""
        wanted = set(member_ids)
        if not wanted:
            return True
        seated = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.member)
                .where(
                    tables.member.c.workspace_id == self.workspace_id,
                    tables.member.c.id.in_(wanted),
                    tables.member.c.seated_at.is_not(None),
                )
            )
        ).scalar_one()
        return seated == len(wanted)

    async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot:
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
        """Seat the member with this email, restoring access an admin revoked. Idempotent for a
        member who already holds one."""
        member_id, seated_at, _ = await self._member_by_email(connection, email)
        if seated_at is not None:
            return
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )

    async def revoke(self, connection: AsyncConnection, email: str) -> None:
        """Unseat the member with this email, removing their access; idempotent. Refuses to unseat
        the last seated admin, who is the only one left who could seat anyone again. The
        workspace-row lock serializes two concurrent revokes against that count, so the last two
        admins cannot each read the other as the second one.

        In-flight turns are never touched here — admission refuses the member's next message
        immediately, and the per-round enforcement parks any running turn before its next model
        call."""
        await connection.execute(
            sa.select(tables.workspace.c.id)
            .where(tables.workspace.c.id == self.workspace_id)
            .with_for_update()
        )
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


def email_domain(email: str) -> str:
    """The address's domain, lowercased — empty for anything that is not one `local@domain` with no
    whitespace, so a malformed value can never satisfy a domain match and never reaches a member
    row. `create_member` calls it too, so this is the shape gate for every creation path and not
    only for the two that match a domain: an address no sign-in could normalize to and no
    channel-verified join could equal would otherwise become a seated member the `member` kind
    cannot delete."""
    candidate = email.strip().lower()
    local, _, domain = candidate.partition("@")
    if not local or not domain or "@" in domain:
        return ""
    if any(character.isspace() for character in candidate):
        return ""
    return domain


def signup_workspace_id(subject: str) -> UUID:
    """The hosted workspace one verified signup subject names."""
    return uuid5(NAMESPACE_DNS, subject.lower())


def workspace_subject(first_email: str, workspace_id: UUID) -> str:
    """The signup subject a seated workspace belongs to, derived from its first member: that
    member's exact address when the address names the workspace, and their domain otherwise.

    The one derivation every consumer reads, so the label `choices` offers, the label an invitation
    prints, and the subject a seat is checked against cannot diverge. A personal-mail workspace
    answers its founder's address, so a shared provider domain matches nothing here."""
    if signup_workspace_id(first_email) == workspace_id:
        return first_email
    return email_domain(first_email)


async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None:
    """The workspace's own email domain when that domain is its signup subject.

    A hosted personal-mail workspace is keyed by its founder's exact address, so its provider
    domain grants no auto-join authority and this answers None."""
    email = (
        await connection.execute(
            sa.select(tables.member.c.email)
            .where(tables.member.c.workspace_id == workspace_id)
            .order_by(tables.member.c.created_at.asc(), tables.member.c.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if email is None:
        return None
    domain = email_domain(email)
    if not domain or signup_workspace_id(email) == workspace_id:
        return None
    return domain


@dataclass(frozen=True, slots=True)
class InvitedMember:
    """One member an admin added, and what has become of them. `spoken` is stamped off the turn a
    member addressed the agent with, whatever surface carried it — unlike the writeback registry,
    which a live surface's members never enter — so a member who has not spoken has not shown up:
    they were added, the invitation reached them, and nothing came of it. `inviter` is None where
    the admin who added them has since been removed."""

    member_id: UUID
    email: str
    invited_at: datetime
    inviter: str | None
    seated: bool


async def invited_members(
    connection: AsyncConnection, workspace_id: UUID, within: timedelta
) -> tuple[InvitedMember, ...]:
    """Every member of this workspace an admin added inside `within`, oldest invitation first.

    A founder is absent: `invited_at` is stamped only where `invited_by` is, so the workspace's own
    first member — who invited nobody and was invited by nobody — is not an invitation.

    The window bounds the rows, not only the workspaces a sweep runs in. Without it a workspace
    that holds one recent invitation hands back every invitation it ever made, and a job measuring
    a delay from each of them fires the whole backlog at once.

    The bound is a statement predicate rather than a filter on the answer, and it is the same
    expression `recently_invited_workspaces` names its candidates by."""
    inviter = tables.member.alias("inviter")
    rows = (
        await connection.execute(
            sa.select(
                tables.member.c.id,
                tables.member.c.email,
                tables.member.c.invited_at,
                tables.member.c.seated_at,
                inviter.c.email.label("inviter_email"),
            )
            .select_from(
                tables.member.outerjoin(inviter, inviter.c.id == tables.member.c.invited_by)
            )
            .where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.invited_at.is_not(None),
                tables.member.c.invited_at > datetime.now(UTC) - within,
            )
            .order_by(tables.member.c.invited_at.asc(), tables.member.c.id.asc())
        )
    ).all()
    return tuple(
        InvitedMember(
            member_id=row.id,
            email=row.email,
            invited_at=row.invited_at,
            inviter=row.inviter_email,
            seated=row.seated_at is not None,
        )
        for row in rows
    )


async def invited_member(
    connection: AsyncConnection, workspace_id: UUID, member_id: UUID
) -> InvitedMember | None:
    """One invitation, of any age. A step measured from an invitation reads the member it is about
    when it comes due, which is after the window that found them has closed."""
    inviter = tables.member.alias("inviter")
    row = (
        await connection.execute(
            sa.select(
                tables.member.c.id,
                tables.member.c.email,
                tables.member.c.invited_at,
                tables.member.c.seated_at,
                inviter.c.email.label("inviter_email"),
            )
            .select_from(
                tables.member.outerjoin(inviter, inviter.c.id == tables.member.c.invited_by)
            )
            .where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.id == member_id,
                tables.member.c.invited_at.is_not(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return InvitedMember(
        member_id=row.id,
        email=row.email,
        invited_at=row.invited_at,
        inviter=row.inviter_email,
        seated=row.seated_at is not None,
    )


async def has_spoken(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool:
    """Whether this member has ever taken a turn.

    Asked of one member rather than carried on every invitation: `turn_spoken` leads with the
    conversation, so the speaker is an in-index filter and a member who never spoke is answered by
    walking the workspace's turns. On an invitation row that cost is paid per member per pass by a
    per-minute sweep that discards the answer."""
    return bool(
        (
            await connection.execute(
                sa.select(sa.literal(1))
                .where(
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.speaker_member_id == member_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    )


def recently_invited_workspaces(within: timedelta) -> WorkspaceCandidates:
    """Candidates for a job that reacts to an invitation: the workspaces where an admin added
    someone inside `within`.

    The window is what keeps this off the whole fleet — an invitation is rare and a workspace
    leaves the set once its newest one ages out — and it is also the product rule. A sequence
    measured from an invitation has nothing to say about one from last quarter, so a job that fell
    behind by longer than its window has missed those members rather than owing them a late
    message, and the window is chosen to state that."""

    def invited() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(tables.member.c.workspace_id)
            .where(tables.member.c.invited_at > datetime.now(UTC) - within)
            .distinct()
        )

    return owner_candidates(invited)


async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None:
    """The workspace a domain addresses through its first member.

    A personal-mail workspace is instead addressed by its founder's exact email signup subject and
    is skipped, so a provider domain never resolves to one of its customers."""
    wanted = email_domain(f"anyone@{domain.strip()}")
    if not wanted:
        return None
    escaped = wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    ranked = sa.select(
        tables.member.c.workspace_id,
        tables.member.c.email,
        tables.member.c.created_at,
        sa.func.row_number()
        .over(
            partition_by=tables.member.c.workspace_id,
            order_by=(tables.member.c.created_at.asc(), tables.member.c.id.asc()),
        )
        .label("seniority"),
    ).subquery()
    rows = (
        await connection.execute(
            sa.select(ranked.c.workspace_id, ranked.c.email)
            .where(
                ranked.c.seniority == 1,
                sa.func.lower(ranked.c.email).like(f"%@{escaped}", escape="\\"),
            )
            .order_by(ranked.c.created_at.asc(), ranked.c.workspace_id.asc())
        )
    ).all()
    return next(
        (row.workspace_id for row in rows if signup_workspace_id(row.email) != row.workspace_id),
        None,
    )


async def member_by_email(
    connection: AsyncConnection, workspace_id: UUID, email: str
) -> UUID | None:
    """The member of this workspace holding this address, or None.

    Lookup only, and scoped to the workspace in the query rather than by the caller: a session
    proves an address, and an address is not a member anywhere in particular. A route that resolved
    the address first and checked the workspace after would read a member of another workspace on
    the way. Nothing here creates a member — an address that has never been seated is not one."""
    return (
        await connection.execute(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                sa.func.lower(tables.member.c.email) == email.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool:
    return bool(
        (
            await connection.execute(
                sa.select(tables.member.c.is_admin).where(
                    tables.member.c.id == member_id,
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.seated_at.is_not(None),
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
    invited_by: UUID | None = None,
) -> UUID:
    """The one member-creation write: every surface that mints a member — onboarding's admin, a
    channel-verified teammate join, hosted onboarding, whatever joins next — inserts through
    here, so the shape rule below is applied structurally rather than remembered per call site.
    Seating is structural in the column itself: `member.seated_at` defaults to now, so a member is
    seated by the row that creates them whichever statement writes it, and only an admin's revoke
    ever clears it. A lost creation race collapses on the member's (workspace_id, email) uniqueness
    and answers the surviving row, which the racing winner already seated.

    The workspace row is locked before the insert so that every creation path takes one lock
    order. Two creations of one address — a teammate's first channel message and an admin adding
    them in the same moment — then queue instead of forming a cycle with whichever caller already
    holds the row.

    The address crosses `email_domain` here, so the shape rule holds for every caller rather than
    for the two that match a domain: a value no sign-in normalizes to and no verified join equals
    cannot become a seated row the `member` kind refuses to delete.

    It is lowercased here for the same reason: (workspace_id, email) uniqueness compares bytes, so a
    caller passing the address as typed would mint a second row for one person, and every read that
    resolves a member by lowercased address would then find two.

    `invited_by` names the admin who added them, and stamping it here stamps `invited_at` with the
    insert that mints the row: the invitation is the creation, and the two facts an invitation
    email states cannot then disagree with the member it names. A creation that loses the race
    writes neither — the surviving row belongs to whoever got there first, and re-stamping it would
    invite a member who is already here."""
    if not email_domain(email):
        raise ValueError(f"{email!r} is not one local@domain address")
    email = email.lower()
    await connection.execute(
        sa.select(tables.workspace.c.id)
        .where(tables.workspace.c.id == workspace_id)
        .with_for_update()
    )
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    created = (
        await connection.execute(
            insert(tables.member)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                email=email,
                is_admin=is_admin,
                invited_at=sa.func.now() if invited_by is not None else None,
                invited_by=invited_by,
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
        return created
    return (
        await connection.execute(
            sa.select(tables.member.c.id).where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.email == email,
            )
        )
    ).scalar_one()


def member_workspaces() -> WorkspaceCandidates:
    """Candidates for a seat-reporting job: every workspace with a member — one indexed distinct
    read, coarse on purpose. Core owns the `member` table, so it owns this query; an extension
    declares `candidates=member_workspaces()` without reaching `owner_tx`."""

    def with_a_member() -> sa.Select[tuple[UUID]]:
        return sa.select(tables.member.c.workspace_id).distinct()

    return owner_candidates(with_a_member)
