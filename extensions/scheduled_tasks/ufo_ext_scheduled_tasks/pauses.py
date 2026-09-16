"""The pause table and every read and write over it.

A pause is a workflow waiting to be resumed: the conversation and agent the resume re-enters, the
instant the timer is due, the body that resume turn receives, and the turn sequence the wait was
armed from. One row per conversation — arming again overwrites it, because a workflow waits for one
thing at a time.

The row answers nothing about whether a member has spoken since; the fire asks admission that
directly, under the conversation lock, by handing back the two watermarks the arm recorded. That is
why this table has no resume-turn column and no arm-time member detection: the one question the old
name-protocol row existed to answer race-free is now answered where the race actually is.

The watermarks are two because they count different things. `origin_seq` is a turn sequence and
`origin_arrival_seq` an arrival sequence, and a member message folding into the arming turn
advances only the second — so turn sequence alone cannot tell a fold the arming turn had already
absorbed from one that landed after the arm, and those two must end opposite ways.

The table is the extension's own — its own metadata, its own migration — so nothing here reaches
core's schema, and every statement filters `workspace_id` itself because
`ExtensionContext.transaction` yields an unscoped connection."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates

CLAIM_BATCH_MAX_PAUSES = 50
PAUSE_LOCK_MODULUS = 1 << 63
PAUSE_CLAIM_GUC = "app.scope_preserving_pause_claim"

_metadata = sa.MetaData()
pause = sa.Table(
    "pause",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("resume_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("origin_seq", sa.Integer, nullable=False),
    sa.Column("origin_arrival_seq", sa.Integer, nullable=False),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("user_description", sa.Text, nullable=False),
    sa.Column("internet_access", sa.Boolean, nullable=False),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "conversation_id", name="pause_conversation"),
)

_COLUMNS = tuple(pause.c)


@dataclass(frozen=True)
class Pause:
    """One armed pause as a handler reads it. A value object — never leaves the process."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    resume_at: datetime
    origin_seq: int
    origin_arrival_seq: int
    prompt: str
    created_by_member_id: UUID | None
    claim_id: str | None
    created_at: datetime
    updated_at: datetime
    internet_access: Literal[False] | None = None


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _row(row: sa.RowMapping) -> Pause:
    """The one builder every read funnels through — SQLite hands naive datetimes back, so every
    timing mark leaves here aware UTC and no consumer re-normalizes."""
    return Pause(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        resume_at=_aware(row["resume_at"]),
        origin_seq=row["origin_seq"],
        origin_arrival_seq=row["origin_arrival_seq"],
        prompt=row["prompt"],
        created_by_member_id=row["created_by_member_id"],
        claim_id=row["claimed_by"],
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        internet_access=None if row["internet_access"] else False,
    )


def _claim_available(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(pause.c.claimed_by.is_(None), pause.c.claim_expires_at < now)


def due_pause_workspaces() -> WorkspaceCandidates:
    """The pause runner's candidate seam: one workspace holding a pause whose timer is due and whose
    lease is free. Claim availability matches `claim_due`, so a workspace whose due pauses are under
    live leases is not reopened."""

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        return (
            sa.select(pause.c.workspace_id)
            .where(
                _claim_available(now),
                pause.c.resume_at <= now,
            )
            .distinct()
        )

    return owner_candidates(due)


@dataclass(frozen=True)
class PauseStore:
    """The pause rows of one workspace, reached through the extension's own transaction."""

    ctx: ExtensionContext

    async def arm(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID,
        resume_at: datetime,
        origin_seq: int,
        origin_arrival_seq: int,
        prompt: str,
        created_by_member_id: UUID | None,
        internet_access: Literal[False] | None,
    ) -> Pause:
        """Arm the conversation's pause, replacing whatever it was waiting on before. A workflow
        waits for one thing at a time, and replacement releases any live claim: the new wait is not
        the one an in-flight tick leased.

        Every arm mints a fresh id, including the one that overwrites. The fire key names the row,
        so a re-armed wait that kept its predecessor's id would present a key that predecessor's
        fire already used — and after a crash between an invoke and its retire, that key belongs to
        a turn already admitted. Admission would settle on it, admit nothing, and the runner would
        retire the row: the second wait would never resume, with nothing raised and nothing logged.
        The identity the key must name is the wait, not the row it happens to occupy."""
        armed = {
            "id": uuid4(),
            "agent_id": agent_id,
            "resume_at": resume_at,
            "origin_seq": origin_seq,
            "origin_arrival_seq": origin_arrival_seq,
            "prompt": prompt,
            "user_description": prompt,
            "created_by_member_id": created_by_member_id,
            "internet_access": internet_access is not False,
            "claimed_by": None,
            "claim_expires_at": None,
            "updated_at": sa.func.now(),
        }
        async with self.ctx.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    sa.select(
                        sa.func.pg_advisory_xact_lock(
                            sa.cast(conversation_id.int % PAUSE_LOCK_MODULUS, sa.BigInteger)
                        )
                    )
                )
            await connection.execute(
                sa.delete(pause).where(
                    pause.c.workspace_id == self.ctx.workspace_id,
                    pause.c.conversation_id == conversation_id,
                )
            )
            row = (
                (
                    await connection.execute(
                        sa.insert(pause)
                        .values(
                            workspace_id=self.ctx.workspace_id,
                            conversation_id=conversation_id,
                            created_at=sa.func.now(),
                            **armed,
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one()
            )
        return _row(row)

    async def armed(self, conversation_id: UUID | None = None) -> tuple[Pause, ...]:
        query = sa.select(*_COLUMNS).where(pause.c.workspace_id == self.ctx.workspace_id)
        if conversation_id is not None:
            query = query.where(pause.c.conversation_id == conversation_id)
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query.order_by(pause.c.resume_at))).mappings().all()
        return tuple(_row(row) for row in rows)

    async def claim_due(
        self, now: datetime, lease_seconds: int, limit: int = CLAIM_BATCH_MAX_PAUSES
    ) -> tuple[Pause, ...]:
        """Lease up to `limit` oldest-due pauses. The lease UPDATE stamps and returns its rows
        atomically, so overlapping ticks partition the due set rather than both firing it."""
        claim = uuid4().hex
        claim_available = _claim_available(now)
        selected = (
            sa.select(pause.c.id)
            .where(
                pause.c.workspace_id == self.ctx.workspace_id,
                pause.c.resume_at <= now,
                claim_available,
            )
            .order_by(pause.c.resume_at, pause.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due_pause")
        )
        async with self.ctx.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    sa.text("select set_config(:guc, 'true', true)"),
                    {"guc": PAUSE_CLAIM_GUC},
                )
            rows = (
                (
                    await connection.execute(
                        sa.update(pause)
                        .where(
                            pause.c.id.in_(sa.select(selected.c.id)),
                            pause.c.workspace_id == self.ctx.workspace_id,
                            pause.c.resume_at <= now,
                            claim_available,
                        )
                        .values(
                            claimed_by=claim,
                            claim_expires_at=now + timedelta(seconds=lease_seconds),
                            updated_at=sa.func.now(),
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_row(row) for row in rows)

    async def claim_holds(self, row: Pause) -> bool:
        """Whether this claim still owns the wait it leased — asked immediately before a fire.

        `retire` already refuses to remove a row that moved, but a fire that has already been sent
        cannot be taken back: an agent re-arming inside the lease window replaces the row with a new
        id and clears the claim, and the tick holding the predecessor would otherwise resume the
        workflow against an instruction it had abandoned, leaving its replacement armed to fire
        again. A re-arm needs no member message, so the supersession guard never sees it.

        What remains is inherent rather than a hole: a re-arm landing between this read committing
        and the invoke still fires, because two statements cannot be one."""
        if row.claim_id is None:
            raise ValueError("an unclaimed pause cannot fire")
        async with self.ctx.transaction() as connection:
            found = (
                await connection.execute(
                    sa.select(pause.c.id)
                    .where(
                        pause.c.id == row.id,
                        pause.c.workspace_id == self.ctx.workspace_id,
                        pause.c.claimed_by == row.claim_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        return found is not None

    async def retire(self, row: Pause) -> None:
        """Remove the pause this claim settled — fired or superseded, the wait is over either way.
        Guarded on the claim, so a lease that expired mid-fire retires nothing and a re-arm that
        replaced the row is never removed by the tick that leased its predecessor."""
        if row.claim_id is None:
            raise ValueError("an unclaimed pause cannot be retired")
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(pause).where(
                    pause.c.id == row.id,
                    pause.c.workspace_id == self.ctx.workspace_id,
                    pause.c.claimed_by == row.claim_id,
                )
            )
