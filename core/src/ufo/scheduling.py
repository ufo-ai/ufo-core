"""Durable scheduled tasks: the schedule row and the workspace-scoped store that owns it.

A scheduled task is a durable row — the conversation and agent a fire re-enters, the cron `schedule`
string, the `prompt` to deliver, and a `next_run_at` due marker. `ScheduleStore` is the one path a
workspace reaches those rows: `create` upserts by name so re-scheduling an existing name updates it,
`cancel` deletes, `list` reports, and `claim_due` + `reschedule` are the batch-at-interval runner's
grip — `claim_due` leases every due task in one atomic statement so an overlapping poll never fires
one twice, and `reschedule` advances a fired task to its next run. The store is cron-agnostic: it
stores the schedule string opaquely and orders on the `next_run_at` a caller computes, so the cron
dialect lives with the extension that owns it, never in core."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws_current


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

    async def claim_due(self, now: datetime, lease_seconds: int) -> tuple[ScheduledTask, ...]:
        """Lease every task due at `now` in one atomic UPDATE: a due, unclaimed-or-expired row is
        stamped with a fresh claim and returned. Because the claim and the read are the same
        statement, two overlapping polls partition the due set rather than both firing it — the
        loser's WHERE no longer matches the rows the winner claimed."""
        claim = uuid4().hex
        expires = now + timedelta(seconds=lease_seconds)
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.update(tables.scheduled_task)
                        .where(
                            tables.scheduled_task.c.workspace_id == self.workspace_id,
                            tables.scheduled_task.c.next_run_at <= now,
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
