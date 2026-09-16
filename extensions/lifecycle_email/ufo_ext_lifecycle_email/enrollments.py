"""The two tables a sequence runs on, and the reads and writes over them.

`lifecycle_event` is the instant a delay is measured from, and nothing else. Core stores no event
stream on purpose — every stage is already a row — so this keeps only what it needs a *time* for,
written by a sweep that reads the rows core already has.

`lifecycle_enrollment` is one member's place in one sequence. The lease is the `scheduled_tasks`
shape: a claim stamped in the same statement that selects the due row, so two sweeps partition the
due set instead of both firing it.

Every statement filters `workspace_id` itself — `ExtensionContext.transaction` yields an unscoped
connection — and the dispatcher has bound the workspace before any of this runs."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo_ext_lifecycle_email.sequences import Sequence

LIVE = "live"
ENDED = "ended"

CLAIM_LEASE_SECONDS = 120
CLAIM_BATCH = 50
RECONCILE_BATCH = 200

_metadata = sa.MetaData()

lifecycle_event = sa.Table(
    "lifecycle_event",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

lifecycle_enrollment = sa.Table(
    "lifecycle_enrollment",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("sequence", sa.Text, nullable=False),
    sa.Column("event_id", sa.Uuid, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("step", sa.Integer, nullable=False),
    sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class Logged:
    """One event nobody has offered to a sequence yet."""

    id: UUID
    member_id: UUID
    name: str
    occurred_at: datetime


@dataclass(frozen=True)
class Enrollment:
    """One member's place in one sequence, as a claim holds it."""

    id: UUID
    member_id: UUID
    sequence: str
    occurred_at: datetime
    step: int
    next_due_at: datetime


def _claimable(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(
        lifecycle_enrollment.c.claimed_by.is_(None),
        lifecycle_enrollment.c.claim_expires_at < now,
    )


def due_enrollment_workspaces() -> WorkspaceCandidates:
    """Where the runner has work: a workspace holding a live enrollment whose next step is due and
    whose row is not under a live lease. Claim availability matches the claim itself, so a
    workspace whose due rows are all being fired right now is not reopened."""

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        return (
            sa.select(lifecycle_enrollment.c.workspace_id)
            .where(
                lifecycle_enrollment.c.state == LIVE,
                lifecycle_enrollment.c.next_due_at <= now,
                _claimable(now),
            )
            .distinct()
        )

    return owner_candidates(due)


def unread_event_workspaces() -> WorkspaceCandidates:
    """Where the reconciler has work: a workspace holding an event no pass has read yet. An event
    read once leaves the set whether or not a sequence measured from it, so a state nothing
    measures from costs one pass and never another."""

    def unread() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(lifecycle_event.c.workspace_id)
            .where(lifecycle_event.c.matched_at.is_(None))
            .distinct()
        )

    return owner_candidates(unread)


@dataclass(frozen=True)
class Enrollments:
    """The event log and the enrollments over it, for the workspace the dispatcher bound."""

    ctx: ExtensionContext

    async def record(self, name: str, member_id: UUID, occurred_at: datetime) -> UUID | None:
        """Log the instant this member reached this state, and answer the event's id the first
        time it is logged. A member reaches one named state once, so the unique key is the whole
        idempotency: a sweep that reads the same row every minute writes one event.

        Nothing is enrolled here. `reconcile` reads the events nobody has read yet, so a pass that
        logs an event and then stops leaves it unread rather than logged and orphaned."""
        async with self.ctx.transaction() as connection:
            return await self._record(connection, name, member_id, occurred_at)

    async def _record(
        self, connection: AsyncConnection, name: str, member_id: UUID, occurred_at: datetime
    ) -> UUID | None:
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        return (
            await connection.execute(
                insert(lifecycle_event)
                .values(
                    id=uuid4(),
                    workspace_id=self.ctx.workspace_id,
                    member_id=member_id,
                    name=name,
                    occurred_at=occurred_at,
                    created_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=(
                        lifecycle_event.c.workspace_id,
                        lifecycle_event.c.member_id,
                        lifecycle_event.c.name,
                    )
                )
                .returning(lifecycle_event.c.id)
            )
        ).scalar_one_or_none()

    async def reconcile(self, sequences: tuple[Sequence, ...]) -> int:
        """Offer every event nobody has read yet to every sequence that measures from it, and mark
        it read. Answers how many events were reconciled.

        Enrolling here rather than at each producer is what keeps a producer ignorant of the
        sequences: a hook and a sweep each log an instant, and one pass decides what measures from
        it — which is also how a sequence an operator wrote today reaches the events logged since
        the last pass without reaching a year of them.

        One event is one transaction: its enrollments and the mark that says it was read commit
        together, so a pass that stops mid-event leaves the event unread and the next pass offers
        it again."""
        read = 0
        for event in await self._unread():
            async with self.ctx.transaction() as connection:
                for sequence in sequences:
                    if sequence.event != event.name:
                        continue
                    await self._enroll(
                        connection,
                        sequence.identity,
                        event.member_id,
                        event.id,
                        event.occurred_at,
                        sequence.steps[0].after,
                    )
                await self._mark_read(connection, event.id)
            read += 1
        return read

    async def _unread(self) -> tuple[Logged, ...]:
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        lifecycle_event.c.id,
                        lifecycle_event.c.member_id,
                        lifecycle_event.c.name,
                        lifecycle_event.c.occurred_at,
                    )
                    .where(
                        lifecycle_event.c.workspace_id == self.ctx.workspace_id,
                        lifecycle_event.c.matched_at.is_(None),
                    )
                    .order_by(lifecycle_event.c.occurred_at)
                    .limit(RECONCILE_BATCH)
                )
            ).all()
        return tuple(
            Logged(
                id=row.id,
                member_id=row.member_id,
                name=row.name,
                occurred_at=_utc(row.occurred_at),
            )
            for row in rows
        )

    async def _mark_read(self, connection: AsyncConnection, event_id: UUID) -> None:
        await connection.execute(
            sa.update(lifecycle_event)
            .where(lifecycle_event.c.id == event_id)
            .values(matched_at=sa.func.now())
        )

    async def _enroll(
        self,
        connection: AsyncConnection,
        sequence: str,
        member_id: UUID,
        event_id: UUID,
        occurred_at: datetime,
        first_step_after: timedelta,
    ) -> None:
        """Put this member in this sequence from that instant. One event enrolls a member in one
        sequence once and no more — `lifecycle_enrollment_event` is unique over the event and the
        sequence whatever state the enrollment reached, and the partial index over the live rows
        holds the one-at-a-time rule beside it — so re-entry needs a further event.

        The event is what the refusal keys on because a roll writes the first enrollment somewhere
        else: the image this one replaces enrolls a member where it logs the event, and this pass
        reads that same event unread."""
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(lifecycle_enrollment)
            .values(
                id=uuid4(),
                workspace_id=self.ctx.workspace_id,
                member_id=member_id,
                sequence=sequence,
                event_id=event_id,
                occurred_at=occurred_at,
                step=0,
                next_due_at=occurred_at + first_step_after,
                state=LIVE,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing()
        )

    async def claim_due(self, now: datetime) -> tuple[Enrollment, ...]:
        """Lease the due enrollments this pass will fire. The lease is stamped by the same UPDATE
        that selects them, so two overlapping passes partition the due set rather than both firing
        it, and the cap bounds one pass to what its lease can cover."""
        expires = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        claim = uuid4().hex
        due = (
            sa.select(lifecycle_enrollment.c.id)
            .where(
                lifecycle_enrollment.c.workspace_id == self.ctx.workspace_id,
                lifecycle_enrollment.c.state == LIVE,
                lifecycle_enrollment.c.next_due_at <= now,
                _claimable(now),
            )
            .order_by(lifecycle_enrollment.c.next_due_at, lifecycle_enrollment.c.id)
            .limit(CLAIM_BATCH)
            .with_for_update(skip_locked=True)
            .cte("due_enrollment")
        )
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.update(lifecycle_enrollment)
                        .where(
                            lifecycle_enrollment.c.id.in_(sa.select(due.c.id)),
                            lifecycle_enrollment.c.workspace_id == self.ctx.workspace_id,
                            lifecycle_enrollment.c.state == LIVE,
                            lifecycle_enrollment.c.next_due_at <= now,
                            _claimable(now),
                        )
                        .values(
                            claimed_by=claim,
                            claim_expires_at=expires,
                            updated_at=sa.func.now(),
                        )
                        .returning(
                            lifecycle_enrollment.c.id,
                            lifecycle_enrollment.c.member_id,
                            lifecycle_enrollment.c.sequence,
                            lifecycle_enrollment.c.occurred_at,
                            lifecycle_enrollment.c.step,
                            lifecycle_enrollment.c.next_due_at,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            Enrollment(
                id=row["id"],
                member_id=row["member_id"],
                sequence=row["sequence"],
                occurred_at=_utc(row["occurred_at"]),
                step=row["step"],
                next_due_at=_utc(row["next_due_at"]),
            )
            for row in rows
        )

    async def advance(self, enrollment: Enrollment, next_due_at: datetime) -> None:
        await self._settle(
            enrollment, step=enrollment.step + 1, next_due_at=next_due_at, state=LIVE
        )

    async def release(self, enrollment: Enrollment) -> None:
        """Drop the lease and leave the row exactly as it was, so the next pass claims it again.

        What a pass does with work it cannot do: a rolling deploy runs both images over one set of
        rows, and the outgoing one holds neither the sequences the new one ships nor the steps they
        name. Ending the enrollment there would settle it unsent, and the unique key over the event
        refuses to write it again — so the member would never be enrolled and never told. Core
        answers the same cross-version question the same way, in `JobTick.tick`: a key it holds no
        binding for is skipped, never deleted, because an older peer must not drop what a newer peer
        owns."""
        await self._settle(
            enrollment,
            step=enrollment.step,
            next_due_at=enrollment.next_due_at,
            state=LIVE,
        )

    async def end(self, enrollment: Enrollment) -> None:
        await self._settle(
            enrollment, step=enrollment.step, next_due_at=enrollment.occurred_at, state=ENDED
        )

    async def _settle(
        self, enrollment: Enrollment, step: int, next_due_at: datetime, state: str
    ) -> None:
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(lifecycle_enrollment)
                .where(
                    lifecycle_enrollment.c.id == enrollment.id,
                    lifecycle_enrollment.c.workspace_id == self.ctx.workspace_id,
                )
                .values(
                    step=step,
                    next_due_at=next_due_at,
                    state=state,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )


def _utc(value: datetime) -> datetime:
    """SQLite hands naive datetimes back, so every instant leaves a read aware and no caller
    re-normalizes."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
