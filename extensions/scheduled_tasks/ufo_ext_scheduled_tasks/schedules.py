"""The scheduled-task table and the scoped store that owns it.

A scheduled task is a durable row — the conversation and agent a fire re-enters, its cron schedule,
prompt, and due marker. Object-scoped calls create, update, cancel, and read recurring tasks; the
runner leases due records workspace-wide and advances their exact versions, because each record
carries the agent it re-enters.

Core migrations up to and including `0085` created this table, and the extension owns it from here:
the declaration below adopts it in place rather than copying it, so no row moves and no migration
runs. A future shape change rides this extension's own migration branch.

Every statement filters `workspace_id` itself — `ExtensionContext.transaction` yields an unscoped
connection — and every member-facing read and mutation also filters the selected object namespace,
which defaults to the turn's agent."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.objects import object_agent_id

CLAIM_BATCH_MAX_TASKS = 50

_metadata = sa.MetaData()
scheduled_task = sa.Table(
    "scheduled_task",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_by_member_id", sa.Uuid, nullable=True),
    sa.Column("schedule", sa.Text, nullable=False),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_turn_id", sa.Uuid, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("paused", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "agent_id", "name", name="scheduled_task_name"),
)


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
    expires_at: datetime | None
    claim_id: str | None
    paused: bool
    created_at: datetime
    updated_at: datetime
    created_by_member_id: UUID | None = None


@dataclass(frozen=True)
class ListedTask:
    """One scheduled task beside the disclosure audience of the conversation it reports into — the
    pair every member-facing read decides visibility from, since who may see a task is a fact of
    where it reports and not of the task row. A fire never asks, so the second read is paid only
    where the answer is needed."""

    task: ScheduledTask
    audience: str
    surface_label: str | None


@dataclass(frozen=True)
class TaskInspection:
    """One scheduled task's live picture for status rendering: its timing marks and the latest
    fire's turn and terminal text — where it reports is the task's `reports_to` link."""

    next_run_at: datetime
    last_run_at: datetime | None
    expires_at: datetime | None
    last_turn_id: UUID | None
    last_turn_status: str | None
    last_response: str | None


_COLUMNS = (
    scheduled_task.c.id,
    scheduled_task.c.conversation_id,
    scheduled_task.c.agent_id,
    scheduled_task.c.name,
    scheduled_task.c.created_by_member_id,
    scheduled_task.c.schedule,
    scheduled_task.c.prompt,
    scheduled_task.c.description,
    scheduled_task.c.next_run_at,
    scheduled_task.c.last_run_at,
    scheduled_task.c.expires_at,
    scheduled_task.c.claimed_by,
    scheduled_task.c.paused,
    scheduled_task.c.created_at,
    scheduled_task.c.updated_at,
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc_opt(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)


def _claim_available(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(
        scheduled_task.c.claimed_by.is_(None),
        scheduled_task.c.claim_expires_at < now,
    )


def _expired(now: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(
        scheduled_task.c.expires_at.is_not(None),
        scheduled_task.c.expires_at <= now,
    )


def _task(row: sa.RowMapping) -> ScheduledTask:
    """The one builder every `ScheduleStore` read funnels through — SQLite hands naive datetimes
    back, so every timing mark leaves here aware UTC and no consumer re-normalizes."""
    return ScheduledTask(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        created_by_member_id=row["created_by_member_id"],
        schedule=row["schedule"],
        prompt=row["prompt"],
        description=row["description"],
        next_run_at=_utc(row["next_run_at"]),
        last_run_at=_utc_opt(row["last_run_at"]),
        expires_at=_utc_opt(row["expires_at"]),
        claim_id=row["claimed_by"],
        paused=row["paused"],
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


def due_task_workspaces() -> WorkspaceCandidates:
    """The scheduled-task runner's candidate seam: one workspace holding either a due task or an
    expired task ready for removal. The expiry sweep does not wait for `next_run_at`. Claim
    availability matches `claim_due`, so a workspace whose matching tasks are under live leases is
    not reopened."""

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        return (
            sa.select(scheduled_task.c.workspace_id)
            .where(
                _claim_available(now),
                sa.or_(
                    _expired(now),
                    sa.and_(
                        sa.not_(scheduled_task.c.paused),
                        scheduled_task.c.next_run_at <= now,
                    ),
                ),
            )
            .distinct()
        )

    return owner_candidates(due)


@dataclass(frozen=True)
class ScheduleStore:
    """Scheduled-task rows behind ambient workspace and object-agent boundaries.

    Member-facing reads and mutations use the selected object namespace, which defaults to the turn
    agent. Due-job methods operate workspace-wide on claimed records because each record carries the
    agent it re-enters."""

    ctx: ExtensionContext

    @property
    def workspace_id(self) -> UUID:
        return self.ctx.workspace_id

    async def create(
        self,
        conversation_id: UUID,
        name: str,
        schedule: str,
        prompt: str,
        description: str,
        next_run_at: datetime,
        created_by_member_id: UUID | None = None,
        expires_at: datetime | None = None,
        paused: bool = False,
    ) -> ScheduledTask:
        """Create one recurring task with immutable executor, reporter, and creator."""
        agent_id = object_agent_id()
        if await self.ctx.conversation_agent(conversation_id) != agent_id:
            raise ValueError(
                "a scheduled task must report to a conversation bound to its executing agent"
            )
        async with self.ctx.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            row = (
                (
                    await connection.execute(
                        insert(scheduled_task)
                        .values(
                            id=uuid4(),
                            workspace_id=self.workspace_id,
                            conversation_id=conversation_id,
                            agent_id=agent_id,
                            name=name,
                            created_by_member_id=created_by_member_id,
                            schedule=schedule,
                            prompt=prompt,
                            description=description,
                            next_run_at=next_run_at,
                            last_run_at=None,
                            last_turn_id=None,
                            expires_at=expires_at,
                            claimed_by=None,
                            claim_expires_at=None,
                            paused=paused,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                scheduled_task.c.workspace_id,
                                scheduled_task.c.agent_id,
                                scheduled_task.c.name,
                            )
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"scheduled task {name!r} already exists")
        return _task(row)

    async def update(
        self,
        expected: ScheduledTask,
        schedule: str,
        prompt: str,
        description: str,
        next_run_at: datetime,
        expires_at: datetime | None = None,
        *,
        paused: bool,
    ) -> ScheduledTask:
        """Update one exact recurring task without changing its immutable identity — `paused` has
        no preserving default, so every caller states whether the task keeps firing."""
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("scheduled task executor changed while editing")
        async with self.ctx.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.update(scheduled_task)
                        .where(
                            scheduled_task.c.workspace_id == self.workspace_id,
                            scheduled_task.c.id == expected.id,
                            scheduled_task.c.agent_id == expected.agent_id,
                            scheduled_task.c.conversation_id == expected.conversation_id,
                            scheduled_task.c.name == expected.name,
                            self._creator_matches(expected),
                        )
                        .values(
                            schedule=schedule,
                            prompt=prompt,
                            description=description,
                            next_run_at=next_run_at,
                            last_run_at=None,
                            last_turn_id=None,
                            expires_at=expires_at,
                            claimed_by=None,
                            claim_expires_at=None,
                            paused=paused,
                            updated_at=sa.func.now(),
                        )
                        .returning(*_COLUMNS)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"scheduled task {expected.name!r} changed while editing")
        return _task(row)

    async def cancel(self, expected: ScheduledTask) -> None:
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("scheduled task executor changed while cancelling")
        async with self.ctx.transaction() as connection:
            deleted = await connection.execute(
                sa.delete(scheduled_task).where(
                    scheduled_task.c.workspace_id == self.workspace_id,
                    scheduled_task.c.id == expected.id,
                    scheduled_task.c.agent_id == expected.agent_id,
                    scheduled_task.c.conversation_id == expected.conversation_id,
                    scheduled_task.c.name == expected.name,
                    self._creator_matches(expected),
                )
            )
        if deleted.rowcount == 0:
            raise ValueError(f"scheduled task {expected.name!r} changed while cancelling")

    def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]:
        """A creatorless task is matched by IS NULL, never by equality — a mutation authorized
        against one member's task must not land on a task nobody created."""
        if expected.created_by_member_id is None:
            return scheduled_task.c.created_by_member_id.is_(None)
        return scheduled_task.c.created_by_member_id == expected.created_by_member_id

    def _listing(
        self,
        selected: tuple[sa.ColumnElement[Any], ...],
        *,
        conversation_id: UUID | None,
        names: tuple[str, ...] | None,
        visible_to_member_id: UUID | None,
        include_all_owners: bool,
        limit: int | None,
    ) -> sa.Select[Any]:
        query = sa.select(*selected).where(
            scheduled_task.c.workspace_id == self.workspace_id,
            scheduled_task.c.agent_id == object_agent_id(),
        )
        if conversation_id is not None:
            query = query.where(scheduled_task.c.conversation_id == conversation_id)
        if names is not None:
            query = query.where(scheduled_task.c.name.in_(names))
        if not include_all_owners:
            query = query.where(scheduled_task.c.created_by_member_id == visible_to_member_id)
        query = query.order_by(scheduled_task.c.name)
        return query if limit is None else query.limit(limit)

    async def list(
        self,
        *,
        conversation_id: UUID | None = None,
        names: tuple[str, ...] | None = None,
        visible_to_member_id: UUID | None = None,
        include_all_owners: bool = True,
        limit: int | None = None,
    ) -> tuple[ScheduledTask, ...]:
        query = self._listing(
            _COLUMNS,
            conversation_id=conversation_id,
            names=names,
            visible_to_member_id=visible_to_member_id,
            include_all_owners=include_all_owners,
            limit=limit,
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return tuple(_task(row) for row in rows)

    async def list_reported(
        self,
        *,
        conversation_id: UUID | None = None,
        names: tuple[str, ...] | None = None,
        visible_to_member_id: UUID | None = None,
        include_all_owners: bool = True,
        limit: int | None = None,
    ) -> tuple[ListedTask, ...]:
        """The same page as `list`, each task beside the audience and surface label of the
        conversation it reports into — the read a member-facing surface answers visibility from.

        The conversation facts are read live rather than snapshotted onto the row: a conversation's
        audience never changes, but its surface label does when a channel is renamed, and a listing
        showing a channel's old name is a listing that lies. A task whose conversation is gone is
        absent from this page, as it was when this read was one inner join."""
        tasks = await self.list(
            conversation_id=conversation_id,
            names=names,
            visible_to_member_id=visible_to_member_id,
            include_all_owners=include_all_owners,
            limit=limit,
        )
        if not tasks:
            return ()
        facts = await self.ctx.conversation_facts(tuple({task.conversation_id for task in tasks}))
        return tuple(
            ListedTask(
                task=task,
                audience=facts[task.conversation_id].audience,
                surface_label=facts[task.conversation_id].surface_label,
            )
            for task in tasks
            if task.conversation_id in facts
        )

    async def claim_due(
        self, now: datetime, lease_seconds: int, limit: int = CLAIM_BATCH_MAX_TASKS
    ) -> tuple[ScheduledTask, ...]:
        """Remove claim-available expired tasks, then lease up to `limit` oldest-due tasks in the
        same transaction. The lease UPDATE stamps and returns its rows atomically, so overlapping
        polls partition the due set rather than both firing it. The cap bounds one sweep's fires to
        what its lease can cover; the remainder stays due for the next sweep."""
        claim = uuid4().hex
        expires = now + timedelta(seconds=lease_seconds)
        expired = _expired(now)
        claim_available = _claim_available(now)
        due = (
            sa.select(scheduled_task.c.id)
            .where(
                scheduled_task.c.workspace_id == self.workspace_id,
                scheduled_task.c.next_run_at <= now,
                sa.not_(scheduled_task.c.paused),
                sa.not_(expired),
                claim_available,
            )
            .order_by(scheduled_task.c.next_run_at, scheduled_task.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due_scheduled_task")
        )
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(scheduled_task).where(
                    scheduled_task.c.workspace_id == self.workspace_id,
                    expired,
                    claim_available,
                )
            )
            rows = (
                (
                    await connection.execute(
                        sa.update(scheduled_task)
                        .where(
                            scheduled_task.c.id.in_(sa.select(due.c.id)),
                            scheduled_task.c.workspace_id == self.workspace_id,
                            scheduled_task.c.next_run_at <= now,
                            sa.not_(scheduled_task.c.paused),
                            sa.not_(expired),
                            claim_available,
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

    async def claim_holds(self, task: ScheduledTask) -> bool:
        """Whether this claim still owns the exact task version it leased — asked immediately before
        a fire, and the reason a cancel inside the lease window does not fire.

        A lease is seconds wide, and within it a member can cancel the task, edit it, or another
        sweep can re-claim an expired lease. Idempotency does not cover any of that: the fire key
        collapses repeat deliveries of one fire, it never asks whether the task still exists. So the
        row is re-read under `FOR UPDATE` against its whole identity — id, claim, conversation,
        agent, name, schedule — and a fire whose row moved is skipped.

        What remains is inherent rather than a hole: a cancel landing between this read committing
        and the invoke still fires, because two statements cannot be one. That window is as narrow
        as two round trips, where the lease window it replaces was seconds wide."""
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be invoked")
        async with self.ctx.transaction() as connection:
            found = (
                await connection.execute(
                    sa.select(scheduled_task.c.id)
                    .where(
                        scheduled_task.c.workspace_id == self.workspace_id,
                        scheduled_task.c.id == task.id,
                        scheduled_task.c.claimed_by == task.claim_id,
                        scheduled_task.c.conversation_id == task.conversation_id,
                        scheduled_task.c.agent_id == task.agent_id,
                        scheduled_task.c.name == task.name,
                        scheduled_task.c.schedule == task.schedule,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        return found is not None

    async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool:
        """Cancel an exact claimed task whose expiry passed before invocation."""
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be retired")
        if task.expires_at is None or task.expires_at > now:
            return False
        async with self.ctx.transaction() as connection:
            await connection.execute(
                sa.delete(scheduled_task).where(
                    scheduled_task.c.workspace_id == self.workspace_id,
                    scheduled_task.c.id == task.id,
                    scheduled_task.c.claimed_by == task.claim_id,
                )
            )
        return True

    async def reschedule(
        self,
        task: ScheduledTask,
        next_run_at: datetime,
        last_run_at: datetime,
        last_turn_id: UUID | None = None,
    ) -> bool:
        """Advance the exact claimed task version and clear its claim, recording when it ran and —
        when the fire admitted a turn — which turn, so `inspect` can surface the latest response."""
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be rescheduled")
        values: dict[str, object] = {
            "next_run_at": next_run_at,
            "last_run_at": last_run_at,
            "claimed_by": None,
            "claim_expires_at": None,
            "updated_at": sa.func.now(),
        }
        if last_turn_id is not None:
            values["last_turn_id"] = last_turn_id
        async with self.ctx.transaction() as connection:
            updated = await connection.execute(
                sa.update(scheduled_task)
                .values(**values)
                .where(
                    scheduled_task.c.workspace_id == self.workspace_id,
                    scheduled_task.c.id == task.id,
                    scheduled_task.c.claimed_by == task.claim_id,
                )
            )
        return updated.rowcount > 0

    async def inspect(self, expected: ScheduledTask) -> TaskInspection | None:
        """One task's live picture beyond its definition: its timing marks and the latest fire's
        turn with its terminal outcome — the read the `scheduled_task` object kind renders as
        status."""
        return (await self.inspect_many((expected,))).get(expected.id)

    async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]:
        if not expected:
            return {}
        agent_id = object_agent_id()
        by_id = {task.id: task for task in expected}
        query = sa.select(
            scheduled_task.c.id,
            scheduled_task.c.name,
            scheduled_task.c.conversation_id,
            scheduled_task.c.next_run_at,
            scheduled_task.c.last_run_at,
            scheduled_task.c.expires_at,
            scheduled_task.c.last_turn_id,
        ).where(
            scheduled_task.c.workspace_id == self.workspace_id,
            scheduled_task.c.id.in_(by_id),
            scheduled_task.c.agent_id == agent_id,
        )
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
        fired = tuple({row["last_turn_id"] for row in rows if row["last_turn_id"] is not None})
        outcomes = await self.ctx.turn_outcomes(fired) if fired else {}
        inspections: dict[UUID, TaskInspection] = {}
        for row in rows:
            task = by_id[row["id"]]
            if row["name"] != task.name or row["conversation_id"] != task.conversation_id:
                continue
            outcome = outcomes.get(row["last_turn_id"])
            inspections[task.id] = TaskInspection(
                next_run_at=_utc(row["next_run_at"]),
                last_run_at=_utc_opt(row["last_run_at"]),
                expires_at=_utc_opt(row["expires_at"]),
                last_turn_id=row["last_turn_id"],
                last_turn_status=None if outcome is None else outcome.status,
                last_response=None if outcome is None else outcome.text,
            )
        return inspections
