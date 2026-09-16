"""The monitor table and every read and write over it.

A monitor is an armed watch: the conversation and agent a fire re-enters, the shell probe and its
interval, the required deadline, the baseline byte-compared against each probe's output, the streak
counters a fire reports, and the lease the per-minute runner claims it under. The table is the
extension's own — its own metadata, its own migration — so nothing here reaches core's schema, and
every statement filters `workspace_id` itself because `ExtensionContext.transaction` yields an
unscoped connection.

An armed monitor ends in exactly one fire, so the writes here are the tick updates (quiet, failed,
skipped) and the two removals: `retire` under the claim that fired it, `disarm` for the member who
says stop watching."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import JsonValue

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, agent_is_live, owner_candidates

MONITOR_KIND = "monitor"
ARMED_MAX = 5
DEADLINE_MAX_MINUTES = 10_080
PROBE_TIMEOUT_SECONDS = 60
PROBE_CAPTURE_MAX_BYTES = 16 * 1024
CAPTURE_HALF_BYTES = PROBE_CAPTURE_MAX_BYTES // 2
STDERR_TAIL_BYTES = 4 * 1024
CLAIM_BATCH_MAX_MONITORS = 50
OMISSION = "\n… {omitted} bytes omitted …\n"
NAME_PATTERN = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
OBJECT_NAME_MAX = 64
CONVERSATION_PREFIX_HEX = 8
NAME_MAX = OBJECT_NAME_MAX - CONVERSATION_PREFIX_HEX - 1

_metadata = sa.MetaData()
monitor = sa.Table(
    "monitor",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("command", sa.Text, nullable=False),
    sa.Column("interval_minutes", sa.Integer, nullable=False),
    sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("next_steps", sa.Text, nullable=False),
    sa.Column("metadata", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("user_description", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("requesting_message_ref", sa.Uuid, nullable=True),
    sa.Column("internet_access", sa.Boolean, nullable=False),
    sa.Column("baseline", sa.Text, nullable=False),
    sa.Column("probes_run", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("quiet_streak", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("failure_streak", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("skipped", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("next_probe_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "name", name="monitor_name"),
)

_COLUMNS = tuple(monitor.c)


def qualified_name(conversation_id: UUID, slug: str) -> str:
    """The object name for a monitor: the conversation's hex prefix and the slug the agent chose.

    A slug is the member's word for the watch and is theirs to reuse — two conversations may each
    hold a `ci-run`. The kind, though, resolves a name across the whole workspace, so the stored
    name has to be workspace-unique or a delete lands on whichever row sorted first. Qualifying by
    conversation is the same identity the artifact kind builds for the same reason (RFC 0017), and
    the unique index over `(workspace_id, name)` is what makes it hold rather than be hoped for.

    `NAME_MAX` is what the prefix leaves of the object-name limit, so the longest slug the tool
    accepts still qualifies to a name the object layer will take."""
    return f"{conversation_id.hex[:CONVERSATION_PREFIX_HEX]}-{slug}"


def capped(output: str) -> str:
    """Probe output bounded to what a fire may carry: under the cap it is returned whole, over it
    the head and tail survive around a marker naming how many bytes went. A monitor diffs this
    value, not the raw output, so an unbounded probe cannot make every tick a change by growing in
    the middle."""
    raw = output.encode()
    if len(raw) <= PROBE_CAPTURE_MAX_BYTES:
        return output
    head = raw[:CAPTURE_HALF_BYTES].decode(errors="ignore")
    tail = raw[-CAPTURE_HALF_BYTES:].decode(errors="ignore")
    return head + OMISSION.format(omitted=len(raw) - 2 * CAPTURE_HALF_BYTES) + tail


def stderr_tail(stderr: str) -> str:
    """The end of a failed probe's stderr — where a shell writes what went wrong."""
    raw = stderr.encode()
    if len(raw) <= STDERR_TAIL_BYTES:
        return stderr
    return raw[-STDERR_TAIL_BYTES:].decode(errors="ignore")


@dataclass(frozen=True)
class Monitor:
    """One armed monitor as a handler reads it. A value object — never leaves the process, so a
    live capability hands it out and no wire type mirrors it."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    name: str
    command: str
    interval_minutes: int
    deadline_at: datetime
    reason: str
    next_steps: str
    metadata: dict[str, JsonValue] | None
    created_by_member_id: UUID | None
    baseline: str
    probes_run: int
    quiet_streak: int
    failure_streak: int
    skipped: int
    last_probe_at: datetime | None
    next_probe_at: datetime
    claim_id: str | None
    created_at: datetime
    updated_at: datetime
    internet_access: Literal[False] | None = None
    requesting_message_ref: UUID | None = None


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _row(row: sa.RowMapping) -> Monitor:
    """The one builder every read funnels through — SQLite hands naive datetimes back, so every
    timing mark leaves here aware UTC and no consumer re-normalizes."""
    probed_at = row["last_probe_at"]
    return Monitor(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        command=row["command"],
        interval_minutes=row["interval_minutes"],
        deadline_at=_aware(row["deadline_at"]),
        reason=row["reason"],
        next_steps=row["next_steps"],
        metadata=row["metadata"],
        created_by_member_id=row["created_by_member_id"],
        requesting_message_ref=row["requesting_message_ref"],
        baseline=row["baseline"],
        probes_run=row["probes_run"],
        quiet_streak=row["quiet_streak"],
        failure_streak=row["failure_streak"],
        skipped=row["skipped"],
        last_probe_at=None if probed_at is None else _aware(probed_at),
        next_probe_at=_aware(row["next_probe_at"]),
        claim_id=row["claimed_by"],
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        internet_access=None if row["internet_access"] else False,
    )


def _claim_available(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(monitor.c.claimed_by.is_(None), monitor.c.claim_expires_at < now)


def _due(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(monitor.c.next_probe_at <= now, monitor.c.deadline_at <= now)


def due_monitor_workspaces() -> WorkspaceCandidates:
    """The monitor runner's candidate seam: one workspace holding a monitor whose next probe or
    whose deadline is due and whose lease is free. Claim availability matches `claim_due`, so a
    workspace whose due monitors are under live leases is not reopened."""

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        return (
            sa.select(monitor.c.workspace_id)
            .where(
                _claim_available(now),
                _due(now),
                agent_is_live(monitor.c.workspace_id, monitor.c.agent_id),
            )
            .distinct()
        )

    return owner_candidates(due)


@dataclass(frozen=True)
class MonitorStore:
    """The monitor rows of one workspace, reached through the extension's own transaction."""

    ctx: ExtensionContext

    async def armed(self, conversation_id: UUID | None = None) -> tuple[Monitor, ...]:
        query = sa.select(*_COLUMNS).where(monitor.c.workspace_id == self.ctx.workspace_id)
        if conversation_id is not None:
            query = query.where(monitor.c.conversation_id == conversation_id)
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query.order_by(monitor.c.name))).mappings().all()
        return tuple(_row(row) for row in rows)

    async def arm(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID,
        name: str,
        command: str,
        interval_minutes: int,
        deadline_at: datetime,
        reason: str,
        next_steps: str,
        metadata: dict[str, JsonValue] | None,
        created_by_member_id: UUID | None,
        baseline: str,
        next_probe_at: datetime,
        internet_access: Literal[False] | None,
        requesting_message_ref: UUID | None = None,
    ) -> Monitor:
        async with self.ctx.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.insert(monitor)
                        .values(
                            id=uuid4(),
                            workspace_id=self.ctx.workspace_id,
                            conversation_id=conversation_id,
                            agent_id=agent_id,
                            name=name,
                            command=command,
                            interval_minutes=interval_minutes,
                            deadline_at=deadline_at,
                            reason=reason,
                            next_steps=next_steps,
                            metadata=metadata,
                            user_description=reason,
                            created_by_member_id=created_by_member_id,
                            requesting_message_ref=requesting_message_ref,
                            internet_access=internet_access is not False,
                            baseline=baseline,
                            probes_run=0,
                            quiet_streak=0,
                            failure_streak=0,
                            skipped=0,
                            last_probe_at=None,
                            next_probe_at=next_probe_at,
                            claimed_by=None,
                            claim_expires_at=None,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one()
            )
        return _row(row)

    async def claim_due(
        self, now: datetime, lease_seconds: int, limit: int = CLAIM_BATCH_MAX_MONITORS
    ) -> tuple[Monitor, ...]:
        """Lease up to `limit` oldest-due monitors. The lease UPDATE stamps and returns its rows
        atomically, so overlapping ticks partition the due set rather than both probing it."""
        claim = uuid4().hex
        claim_available = _claim_available(now)
        due = _due(now)
        selected = (
            sa.select(monitor.c.id)
            .where(
                monitor.c.workspace_id == self.ctx.workspace_id,
                due,
                claim_available,
                agent_is_live(monitor.c.workspace_id, monitor.c.agent_id),
            )
            .order_by(monitor.c.next_probe_at, monitor.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due_monitor")
        )
        async with self.ctx.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.update(monitor)
                        .where(
                            monitor.c.id.in_(sa.select(selected.c.id)),
                            monitor.c.workspace_id == self.ctx.workspace_id,
                            due,
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

    async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None:
        """A probe that ran and matched the baseline: counted, rescheduled, nothing posted."""
        await self._tick(
            row,
            probes_run=row.probes_run + 1,
            quiet_streak=row.quiet_streak + 1,
            failure_streak=0,
            skipped=row.skipped,
            last_probe_at=probed_at,
            next_probe_at=next_probe_at,
        )

    async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None:
        """A probe that ran and exited nonzero, under the fire threshold: the failure streak
        advances and the quiet one breaks — both counters are streaks, so neither counts across an
        interruption by the other."""
        await self._tick(
            row,
            probes_run=row.probes_run + 1,
            quiet_streak=0,
            failure_streak=row.failure_streak + 1,
            skipped=row.skipped,
            last_probe_at=probed_at,
            next_probe_at=next_probe_at,
        )

    async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None:
        """A probe that could not run — an unreachable client sandbox. Counted so the eventual fire
        reports it, never a failure and never a probe."""
        await self._tick(
            row,
            probes_run=row.probes_run,
            quiet_streak=row.quiet_streak,
            failure_streak=row.failure_streak,
            skipped=row.skipped + 1,
            last_probe_at=row.last_probe_at,
            next_probe_at=next_probe_at,
        )

    async def _tick(
        self,
        row: Monitor,
        *,
        probes_run: int,
        quiet_streak: int,
        failure_streak: int,
        skipped: int,
        last_probe_at: datetime | None,
        next_probe_at: datetime,
    ) -> None:
        if row.claim_id is None:
            raise ValueError("an unclaimed monitor cannot record a tick")
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.update(monitor)
                .where(
                    monitor.c.id == row.id,
                    monitor.c.workspace_id == self.ctx.workspace_id,
                    monitor.c.claimed_by == row.claim_id,
                )
                .values(
                    probes_run=probes_run,
                    quiet_streak=quiet_streak,
                    failure_streak=failure_streak,
                    skipped=skipped,
                    last_probe_at=last_probe_at,
                    next_probe_at=next_probe_at,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )

    async def claim_holds(self, row: Monitor) -> bool:
        """Whether this claim still owns the row it leased — asked immediately before a fire, and
        the reason a member's stop lands ahead of a fire rather than beside it.

        A probe runs for up to its timeout inside a lease seconds wide, and `disarm` removes the row
        the moment the member says stop watching. Without this read the fire that followed would
        arrive after the watch was stopped, and would retire nothing on its way out. The lease alone
        cannot answer it: the row is gone, not re-claimed.

        What remains is inherent rather than a hole: a stop landing between this read committing and
        the invoke still fires, because two statements cannot be one. That window is as narrow as
        two round trips, where the probe it replaces holds the lease for as long as a command
        runs."""
        if row.claim_id is None:
            raise ValueError("an unclaimed monitor cannot fire")
        async with self.ctx.transaction() as connection:
            found = (
                await connection.execute(
                    sa.select(monitor.c.id)
                    .where(
                        monitor.c.id == row.id,
                        monitor.c.workspace_id == self.ctx.workspace_id,
                        monitor.c.claimed_by == row.claim_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        return found is not None

    async def retire(self, row: Monitor) -> None:
        """Remove the monitor whose fire this claim delivered. Guarded on the claim, so a lease that
        expired mid-fire retires nothing and the row the next tick holds is that tick's to fire."""
        if row.claim_id is None:
            raise ValueError("an unclaimed monitor cannot be retired")
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(monitor).where(
                    monitor.c.id == row.id,
                    monitor.c.workspace_id == self.ctx.workspace_id,
                    monitor.c.claimed_by == row.claim_id,
                )
            )

    async def disarm(self, row: Monitor) -> bool:
        """Stop watching, reporting whether this call removed the row."""
        async with self.ctx.transaction() as connection:
            deleted = await connection.execute(
                sa.delete(monitor).where(
                    monitor.c.id == row.id, monitor.c.workspace_id == self.ctx.workspace_id
                )
            )
        return deleted.rowcount == 1
