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
from ufo_ext_lifecycle_email.sequences import SEQUENCES

LIVE = "live"
ENDED = "ended"

CLAIM_LEASE_SECONDS = 120
CLAIM_BATCH = 50

_metadata = sa.MetaData()

lifecycle_event = sa.Table(
    "lifecycle_event",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
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


@dataclass(frozen=True)
class Enrollments:
    """The event log and the enrollments over it, for the workspace the dispatcher bound."""

    ctx: ExtensionContext

    async def reached(self, name: str, member_id: UUID, occurred_at: datetime) -> bool:
        """Log the instant, and put the member in every sequence that measures from it. Answers
        whether this call was the one that logged it, so a caller that sweeps the same rows every
        minute can tell the first pass from the rest.

        Enrolling here rather than at each producer is what keeps a sequence's trigger in one
        place: a sequence names the event it measures from, and every producer of that event
        enrolls it without knowing the sequence exists.

        One transaction, because the event is the whole idempotency key: a second pass reads the
        event row and writes nothing, so a pass that committed the event and then stopped would
        leave a member logged, never enrolled, and beyond repair."""
        async with self.ctx.transaction() as connection:
            event_id = await self._record(connection, name, member_id, occurred_at)
            if event_id is None:
                return False
            for sequence in SEQUENCES:
                if sequence.event != name:
                    continue
                await self._enroll(
                    connection,
                    sequence.name,
                    member_id,
                    event_id,
                    occurred_at,
                    sequence.steps[0].after,
                )
            return True

    async def _record(
        self, connection: AsyncConnection, name: str, member_id: UUID, occurred_at: datetime
    ) -> UUID | None:
        """The event's id the first time it is logged, and None after. A member reaches one named
        state once, so the unique key is the whole idempotency."""
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

    async def _enroll(
        self,
        connection: AsyncConnection,
        sequence: str,
        member_id: UUID,
        event_id: UUID,
        occurred_at: datetime,
        first_step_after: timedelta,
    ) -> None:
        """Put this member in this sequence from that instant. A member is in a sequence once at a
        time — the partial unique index over the live rows says so — and re-entry needs the
        enrollment it has to have ended first."""
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
