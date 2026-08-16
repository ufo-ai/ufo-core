"""Seats: who the agent answers. A member holds a seat from creation and the agent answers them;
an admin revokes it to remove that person's access, which is the only way to remove it — the
`member` kind refuses delete, because the row is an identity and a memory subject that outlives
the access. Nothing bounds how many members hold one: the workspace pays one flat fee and the
count is an outreach figure, never a gate. Core owns seating, the last seated admin's irrevocable
seat, and member creation, so the admission gate, per-round enforcement, resume sweep, and an
extension's tools apply the same rules; an extension only decides when to call them. An unseated
member still exists: the gate answers their turn with the refusal, and seating them again simply
lets them speak."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.schema import tables

SEAT_REFUSAL_MESSAGE = (
    "A workspace admin removed your seat, so I can't answer you. Ask them to restore it."
)
UNRESOLVED_SPEAKER_MESSAGE = (
    "I can only answer workspace members, and I couldn't verify who you are. Make sure your work "
    "email is confirmed and visible on your profile, then try again — or ask a workspace admin "
    "to add you."
)
SEAT_REVOKED_MESSAGE = (
    "This turn is parked: the speaker's seat was revoked. It resumes if the seat is granted again."
)


def gate_member(
    speaker_member_id: UUID | None,
    on_behalf_of_member_id: UUID | None,
) -> UUID | None:
    """The member a turn is seat-gated on: its speaker, else the member it acts on behalf of — a
    scheduled fire's creator, a subagent's requester, a monitor's armer — so an unseated member's
    work is refused however it was admitted; a turn acting for nobody gates on nobody. The one
    derivation admission, the fold resume, the dispatch sweep, and the per-round check all share —
    the gate cannot fork on who it means."""
    if speaker_member_id is not None:
        return speaker_member_id
    return on_behalf_of_member_id


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

    async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool:
        """Whether the gate answers this member: only while they hold a seat, and never for an id
        that is not a member of this workspace. One indexed read of the member's own row, paid on
        every admission, on every round of a running turn, and on every resume — which is what
        makes an admin's revoke stop the agent answering that person everywhere at once, rather
        than only at the next thing that happens to re-read the workspace."""
        row = (
            await connection.execute(
                sa.select(tables.member.c.seated_at).where(
                    tables.member.c.id == member_id,
                    tables.member.c.workspace_id == self.workspace_id,
                )
            )
        ).one_or_none()
        return row is not None and row.seated_at is not None

    async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool:
        """Whether every one of these members still holds a seat. One indexed read for the whole
        set, so a turn that absorbed six speakers costs the same round-trip as one — this is the
        question the per-round check, the parked-fold check, and the dispatch sweep all ask, and
        each of them asks it about a set. An id that is not a member of this workspace is not
        seated, exactly as `admits` answers it."""
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


async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None:
    """The workspace's own email domain: its first member's, the vetted domain a sign-in resolves
    a workspace by and a chat-surface join matches against. The one derivation every consumer
    reads — what `join_member` matches and what the operator check compares cannot diverge. None
    only when the workspace has no member yet, the state a chat-surface join meets before anyone has
    onboarded: every stored address carries a domain, because `create_member` admits none that does
    not."""
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
    return email_domain(email) or None


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
    resolves a member by lowercased address would then find two."""
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
