"""Durable scheduled tasks: the schedule row and the workspace-scoped store that owns it.

A scheduled task is a durable row — the conversation and agent a fire re-enters, the cron `schedule`
string, the `prompt` to deliver, and a `next_run_at` due marker. `ScheduleStore` is the one path a
workspace reaches those rows: `create` upserts by name so re-scheduling an existing name updates it,
`cancel` deletes, `list` reports, and `claim_due` + `reschedule` are the batch-at-interval runner's
grip — `claim_due` leases a bounded batch of the oldest-due tasks in one atomic statement so an
overlapping poll never fires one twice and one sweep never claims more than its lease can cover,
and `reschedule` advances a fired task to its next run. The store is cron-agnostic: it
stores the schedule string opaquely and orders on the `next_run_at` a caller computes, so the cron
dialect lives with the extension that owns it, never in core."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.candidates import WorkspaceCandidates
from ufo.db import owner_tx, workspace_tx
from ufo.schema import tables
from ufo.workspace import ws_current

CLAIM_BATCH_MAX_TASKS = 50


@dataclass(frozen=True)
class ScheduledTask:
    """One scheduled task as a handler reads it: the conversation and agent a fire re-enters, the
    cron schedule, the prompt to deliver, and its timing marks. A value object — never leaves the
    process, so a live capability hands it out and a wire type never mirrors it."""

    id: UUID
    conversation_id: UUID
    agent_id: UUID
    name: str
    schedule: str
    prompt: str
    description: str
    next_run_at: datetime
    last_run_at: datetime | None


_COLUMNS = (
    tables.scheduled_task.c.id,
    tables.scheduled_task.c.conversation_id,
    tables.scheduled_task.c.agent_id,
    tables.scheduled_task.c.name,
    tables.scheduled_task.c.schedule,
    tables.scheduled_task.c.prompt,
    tables.scheduled_task.c.description,
    tables.scheduled_task.c.next_run_at,
    tables.scheduled_task.c.last_run_at,
)


def _task(row: sa.RowMapping) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        schedule=row["schedule"],
        prompt=row["prompt"],
        description=row["description"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
    )


def due_task_workspaces() -> WorkspaceCandidates:
    """The candidate seam the scheduled-task runner declares: the workspaces holding a task due to
    fire — one distinct `workspace_id` per workspace with a due, unclaimed-or-expired row, read in
    one `owner_tx` (the RLS-bypass path) so the dispatcher binds only those. Core owns the
    `scheduled_task` table, so it owns this query and hands the runner a ready selector — the
    extension never reaches `owner_tx`. The claim lease is folded in, matching the runner's own
    `claim_due`, so a workspace whose only due task is mid-fire under a lease is not reopened."""

    async def candidates() -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.scheduled_task.c.workspace_id)
                    .where(
                        tables.scheduled_task.c.next_run_at <= now,
                        sa.or_(
                            tables.scheduled_task.c.claimed_by.is_(None),
                            tables.scheduled_task.c.claim_expires_at < now,
                        ),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    return candidates


@dataclass(frozen=True)
class ScheduleStore:
    """One workspace's scheduled-task rows, reached only through workspace_tx. Every query is scoped
    to the ambient workspace the turn or job bound, so a handler holding the store can never see or
    advance another's tasks."""

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def create(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        name: str,
        schedule: str,
        prompt: str,
        description: str,
        next_run_at: datetime,
    ) -> ScheduledTask:
        """Upsert a schedule row by name: an existing name is re-pointed at the new cadence, prompt,
        and conversation and its claim cleared; a new name inserts. One row per (workspace, name),
        so the name a caller keeps addresses exactly one task at cancel time."""
        async with workspace_tx() as connection:
            existing = (
                await connection.execute(
                    sa.select(tables.scheduled_task.c.id).where(
                        tables.scheduled_task.c.workspace_id == self.workspace_id,
                        tables.scheduled_task.c.name == name,
                    )
                )
            ).one_or_none()
            task_id = uuid4() if existing is None else existing.id
            if existing is None:
                await connection.execute(
                    sa.insert(tables.scheduled_task).values(
                        id=task_id,
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        name=name,
                        schedule=schedule,
                        prompt=prompt,
                        description=description,
                        next_run_at=next_run_at,
                        last_run_at=None,
                        claimed_by=None,
                        claim_expires_at=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                await connection.execute(
                    sa.update(tables.scheduled_task)
                    .values(
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        schedule=schedule,
                        prompt=prompt,
                        description=description,
                        next_run_at=next_run_at,
                        last_run_at=None,
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.scheduled_task.c.id == task_id)
                )
        return ScheduledTask(
            id=task_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            name=name,
            schedule=schedule,
            prompt=prompt,
            description=description,
            next_run_at=next_run_at,
            last_run_at=None,
        )

    async def cancel(self, name: str) -> bool:
        async with workspace_tx() as connection:
            deleted = await connection.execute(
                sa.delete(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    tables.scheduled_task.c.name == name,
                )
            )
        return deleted.rowcount > 0

    async def list(self) -> tuple[ScheduledTask, ...]:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(*_COLUMNS)
                        .where(tables.scheduled_task.c.workspace_id == self.workspace_id)
                        .order_by(tables.scheduled_task.c.name)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_task(row) for row in rows)

    async def claim_due(
        self, now: datetime, lease_seconds: int, limit: int = CLAIM_BATCH_MAX_TASKS
    ) -> tuple[ScheduledTask, ...]:
        """Lease up to `limit` of the oldest-due tasks at `now` in one atomic UPDATE: a due,
        unclaimed-or-expired row is stamped with a fresh claim and returned. Because the claim and
        the read are the same statement, two overlapping polls partition the due set rather than
        both firing it — the loser's WHERE no longer matches the rows the winner claimed. The cap
        bounds one sweep's fires to what its lease can cover; the remainder stays due and the next
        sweep claims it."""
        claim = uuid4().hex
        expires = now + timedelta(seconds=lease_seconds)
        due = (
            sa.select(tables.scheduled_task.c.id)
            .where(
                tables.scheduled_task.c.workspace_id == self.workspace_id,
                tables.scheduled_task.c.next_run_at <= now,
                sa.or_(
                    tables.scheduled_task.c.claimed_by.is_(None),
                    tables.scheduled_task.c.claim_expires_at < now,
                ),
            )
            .order_by(tables.scheduled_task.c.next_run_at, tables.scheduled_task.c.id)
            .limit(limit)
            .scalar_subquery()
        )
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.update(tables.scheduled_task)
                        .where(
                            tables.scheduled_task.c.id.in_(due),
                            sa.or_(
                                tables.scheduled_task.c.claimed_by.is_(None),
                                tables.scheduled_task.c.claim_expires_at < now,
                            ),
                        )
                        .values(
                            claimed_by=claim,
                            claim_expires_at=expires,
                            updated_at=sa.func.now(),
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_task(row) for row in rows)

    async def reschedule(self, task_id: UUID, next_run_at: datetime, last_run_at: datetime) -> None:
        """Advance a fired task to its next run and clear its claim, recording when it last ran."""
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.scheduled_task)
                .values(
                    next_run_at=next_run_at,
                    last_run_at=last_run_at,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    tables.scheduled_task.c.id == task_id,
                )
            )
