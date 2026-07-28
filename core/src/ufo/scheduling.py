"""Durable scheduled tasks: the schedule row and the scoped store that owns it.

A scheduled task is a durable row — the conversation and agent a fire re-enters, its schedule,
prompt, and due marker. Object-scoped calls create, update, cancel, and read recurring tasks.
Workspace jobs lease due records and advance their exact versions. A one-time pause also records
its originating conversation sequence and accepted resume turn, so member ingress and timer
recovery converge on one durable turn. Claims are bounded and atomic, so overlapping polls
partition due work. Cron parsing stays in the scheduled-tasks extension."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.candidates import WorkspaceCandidates
from ufo.db import owner_tx, workspace_tx
from ufo.object_scope import object_agent_id
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION
from ufo.workspace import ws_current

CLAIM_BATCH_MAX_TASKS = 50
ONE_TIME_SCHEDULE = "@once"
PAUSE_NAME_PREFIX = "@pause:"


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
    origin_seq: int | None
    resume_turn_id: UUID | None
    claim_id: str | None
    created_at: datetime
    updated_at: datetime
    created_by_member_id: UUID | None = None


class ScheduleInvoker(Protocol):
    async def invoke_scheduled(
        self, task: ScheduledTask, runtime_instruction: str | None = None
    ) -> UUID | None: ...


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
    tables.scheduled_task.c.id,
    tables.scheduled_task.c.conversation_id,
    tables.scheduled_task.c.agent_id,
    tables.scheduled_task.c.name,
    tables.scheduled_task.c.created_by_member_id,
    tables.scheduled_task.c.schedule,
    tables.scheduled_task.c.prompt,
    tables.scheduled_task.c.description,
    tables.scheduled_task.c.next_run_at,
    tables.scheduled_task.c.last_run_at,
    tables.scheduled_task.c.expires_at,
    tables.scheduled_task.c.origin_seq,
    tables.scheduled_task.c.resume_turn_id,
    tables.scheduled_task.c.claimed_by,
    tables.scheduled_task.c.created_at,
    tables.scheduled_task.c.updated_at,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _claim_available(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(
        tables.scheduled_task.c.claimed_by.is_(None),
        tables.scheduled_task.c.claim_expires_at < now,
    )


def _expired(now: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(
        tables.scheduled_task.c.expires_at.is_not(None),
        tables.scheduled_task.c.expires_at <= now,
    )


def _task(row: sa.RowMapping) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        created_by_member_id=row["created_by_member_id"],
        schedule=row["schedule"],
        prompt=row["prompt"],
        description=row["description"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        expires_at=_utc(row["expires_at"]),
        origin_seq=row["origin_seq"],
        resume_turn_id=row["resume_turn_id"],
        claim_id=row["claimed_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def due_task_workspaces() -> WorkspaceCandidates:
    """The scheduled-task runner's candidate seam: one workspace holding either a due task or an
    expired task ready for removal. The expiry sweep does not wait for `next_run_at`. One
    `owner_tx` read crosses workspace RLS only to return workspace ids; the runner binds each
    workspace before touching its tasks. Claim availability matches `claim_due`, so a workspace
    whose matching tasks are under live leases is not reopened."""

    async def candidates() -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        claim_available = _claim_available(now)
        expired = _expired(now)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.scheduled_task.c.workspace_id)
                    .where(
                        claim_available,
                        sa.or_(
                            expired,
                            tables.scheduled_task.c.next_run_at <= now,
                        ),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    return candidates


@dataclass(frozen=True)
class ScheduleStore:
    """Scheduled-task rows behind ambient workspace and object-agent boundaries.

    Member-facing reads and mutations use the selected object namespace, which defaults to the turn
    agent. Due-job methods operate workspace-wide on claimed records because each record carries
    the agent it re-enters."""

    _invoker: ScheduleInvoker | None = None

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def invoke(
        self, task: ScheduledTask, runtime_instruction: str | None = None
    ) -> UUID | None:
        if self._invoker is None:
            raise RuntimeError("scheduled invoke requires an invoker; none is wired")
        return await self._invoker.invoke_scheduled(task, runtime_instruction)

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
    ) -> ScheduledTask:
        """Create one recurring task with immutable executor, reporter, and creator."""
        if schedule == ONE_TIME_SCHEDULE:
            raise ValueError("one-time workflow pauses must use ScheduleStore.pause")
        if name.startswith(PAUSE_NAME_PREFIX):
            raise ValueError(f"scheduled task names cannot start with {PAUSE_NAME_PREFIX!r}")
        agent_id = object_agent_id()
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(tables.conversation.c.id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.agent_id == agent_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ValueError(
                    "a scheduled task must report to a conversation bound to its executing agent"
                )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            row = (
                (
                    await connection.execute(
                        insert(tables.scheduled_task)
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
                            origin_seq=None,
                            resume_turn_id=None,
                            claimed_by=None,
                            claim_expires_at=None,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                tables.scheduled_task.c.workspace_id,
                                tables.scheduled_task.c.agent_id,
                                tables.scheduled_task.c.name,
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
    ) -> ScheduledTask:
        """Update one exact recurring task without changing its immutable identity."""
        if schedule == ONE_TIME_SCHEDULE:
            raise ValueError("one-time workflow pauses cannot be updated as recurring tasks")
        if expected.name.startswith(PAUSE_NAME_PREFIX):
            raise ValueError("one-time workflow pauses cannot be updated as recurring tasks")
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("scheduled task executor changed while editing")
        creator_matches = (
            tables.scheduled_task.c.created_by_member_id.is_(None)
            if expected.created_by_member_id is None
            else tables.scheduled_task.c.created_by_member_id == expected.created_by_member_id
        )
        async with workspace_tx() as connection:
            row = (
                (
                    await connection.execute(
                        sa.update(tables.scheduled_task)
                        .where(
                            tables.scheduled_task.c.workspace_id == self.workspace_id,
                            tables.scheduled_task.c.id == expected.id,
                            tables.scheduled_task.c.agent_id == expected.agent_id,
                            tables.scheduled_task.c.conversation_id == expected.conversation_id,
                            tables.scheduled_task.c.name == expected.name,
                            creator_matches,
                        )
                        .values(
                            schedule=schedule,
                            prompt=prompt,
                            description=description,
                            next_run_at=next_run_at,
                            last_run_at=None,
                            last_turn_id=None,
                            expires_at=expires_at,
                            origin_seq=None,
                            resume_turn_id=None,
                            claimed_by=None,
                            claim_expires_at=None,
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

    async def pause(
        self,
        conversation_id: UUID,
        prompt: str,
        description: str,
        next_run_at: datetime,
        origin_seq: int,
        created_by_member_id: UUID | None = None,
    ) -> ScheduledTask | None:
        """Arm the conversation's one-time pause from the turn sequence that requested it."""
        return await self._upsert_pause(
            conversation_id,
            prompt,
            description,
            next_run_at,
            origin_seq,
            created_by_member_id,
        )

    async def _upsert_pause(
        self,
        conversation_id: UUID,
        prompt: str,
        description: str,
        next_run_at: datetime,
        origin_seq: int,
        created_by_member_id: UUID | None,
    ) -> ScheduledTask | None:
        agent_id = object_agent_id()
        name = f"{PAUSE_NAME_PREFIX}{conversation_id}"
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(tables.conversation.c.id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.agent_id == agent_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ValueError(
                    "a scheduled task must report to a conversation bound to its executing agent"
                )
            resume_turn_id: UUID | None = None
            effective_next_run_at = next_run_at
            reply_pending = (
                await connection.execute(
                    sa.select(
                        sa.exists(
                            sa.select(tables.inbound_message.c.id).where(
                                tables.inbound_message.c.workspace_id == self.workspace_id,
                                tables.inbound_message.c.conversation_id == conversation_id,
                                tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                                tables.inbound_message.c.consumed_turn_id.is_(None),
                            )
                        )
                    )
                )
            ).scalar_one()
            if reply_pending:
                return None
            newer_member = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.status,
                    )
                    .where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.agent_id == agent_id,
                        tables.turn.c.admission_source == MEMBER_ADMISSION,
                        tables.turn.c.seq > origin_seq,
                    )
                    .order_by(tables.turn.c.seq.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).one_or_none()
            if newer_member is not None:
                if newer_member.status != "queued":
                    return None
                resume_turn_id = newer_member.id
                effective_next_run_at = datetime.now(UTC)
            task_id = uuid4()
            values = {
                "schedule": ONE_TIME_SCHEDULE,
                "prompt": prompt,
                "description": description,
                "next_run_at": effective_next_run_at,
                "last_run_at": None,
                "last_turn_id": None,
                "expires_at": None,
                "origin_seq": origin_seq,
                "resume_turn_id": resume_turn_id,
                "claimed_by": None,
                "claim_expires_at": None,
                "updated_at": sa.func.now(),
            }
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            row = (
                await connection.execute(
                    insert(tables.scheduled_task)
                    .values(
                        id=task_id,
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        name=name,
                        created_by_member_id=created_by_member_id,
                        schedule=ONE_TIME_SCHEDULE,
                        prompt=prompt,
                        description=description,
                        next_run_at=effective_next_run_at,
                        last_run_at=None,
                        last_turn_id=None,
                        expires_at=None,
                        origin_seq=origin_seq,
                        resume_turn_id=resume_turn_id,
                        claimed_by=None,
                        claim_expires_at=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_update(
                        index_elements=(
                            tables.scheduled_task.c.workspace_id,
                            tables.scheduled_task.c.agent_id,
                            tables.scheduled_task.c.name,
                        ),
                        set_=values,
                    )
                    .returning(
                        tables.scheduled_task.c.id,
                        tables.scheduled_task.c.conversation_id,
                        tables.scheduled_task.c.created_by_member_id,
                        tables.scheduled_task.c.created_at,
                        tables.scheduled_task.c.updated_at,
                    )
                )
            ).one()
        return ScheduledTask(
            id=row.id,
            conversation_id=row.conversation_id,
            agent_id=agent_id,
            name=name,
            created_by_member_id=row.created_by_member_id,
            schedule=ONE_TIME_SCHEDULE,
            prompt=prompt,
            description=description,
            next_run_at=effective_next_run_at,
            last_run_at=None,
            expires_at=None,
            origin_seq=origin_seq,
            resume_turn_id=resume_turn_id,
            claim_id=None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def cancel(self, expected: ScheduledTask) -> None:
        if expected.schedule == ONE_TIME_SCHEDULE:
            raise ValueError("one-time workflow pauses cannot be cancelled as recurring tasks")
        agent_id = object_agent_id()
        if expected.agent_id != agent_id:
            raise ValueError("scheduled task executor changed while cancelling")
        creator_matches = (
            tables.scheduled_task.c.created_by_member_id.is_(None)
            if expected.created_by_member_id is None
            else tables.scheduled_task.c.created_by_member_id == expected.created_by_member_id
        )
        async with workspace_tx() as connection:
            deleted = await connection.execute(
                sa.delete(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    tables.scheduled_task.c.id == expected.id,
                    tables.scheduled_task.c.agent_id == expected.agent_id,
                    tables.scheduled_task.c.conversation_id == expected.conversation_id,
                    tables.scheduled_task.c.name == expected.name,
                    creator_matches,
                )
            )
        if deleted.rowcount == 0:
            raise ValueError(f"scheduled task {expected.name!r} changed while cancelling")

    async def list(self) -> tuple[ScheduledTask, ...]:
        agent_id = object_agent_id()
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(*_COLUMNS)
                        .where(
                            tables.scheduled_task.c.workspace_id == self.workspace_id,
                            tables.scheduled_task.c.agent_id == agent_id,
                            tables.scheduled_task.c.schedule != ONE_TIME_SCHEDULE,
                        )
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
        """Remove claim-available expired tasks, then lease up to `limit` oldest-due tasks in the
        same transaction. The lease UPDATE stamps and returns its rows atomically, so overlapping
        polls partition the due set rather than both firing it. The cap bounds one sweep's fires to
        what its lease can cover; the remainder stays due for the next sweep."""
        claim = uuid4().hex
        expires = now + timedelta(seconds=lease_seconds)
        expired = _expired(now)
        claim_available = _claim_available(now)
        due = (
            sa.select(tables.scheduled_task.c.id)
            .where(
                tables.scheduled_task.c.workspace_id == self.workspace_id,
                tables.scheduled_task.c.next_run_at <= now,
                sa.not_(expired),
                claim_available,
            )
            .order_by(tables.scheduled_task.c.next_run_at, tables.scheduled_task.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due_scheduled_task")
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    expired,
                    claim_available,
                )
            )
            rows = (
                (
                    await connection.execute(
                        sa.update(tables.scheduled_task)
                        .where(
                            tables.scheduled_task.c.id.in_(sa.select(due.c.id)),
                            tables.scheduled_task.c.workspace_id == self.workspace_id,
                            tables.scheduled_task.c.next_run_at <= now,
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

    async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool:
        """Cancel an exact claimed task whose expiry passed before invocation."""
        if task.claim_id is None:
            raise ValueError("an unclaimed scheduled task cannot be retired")
        if task.expires_at is None or task.expires_at > now:
            return False
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    tables.scheduled_task.c.id == task.id,
                    tables.scheduled_task.c.claimed_by == task.claim_id,
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
        if task.schedule == ONE_TIME_SCHEDULE:
            raise ValueError("a one-time workflow pause cannot be rescheduled")
        values: dict[str, object] = {
            "next_run_at": next_run_at,
            "last_run_at": last_run_at,
            "resume_turn_id": None,
            "claimed_by": None,
            "claim_expires_at": None,
            "updated_at": sa.func.now(),
        }
        if last_turn_id is not None:
            values["last_turn_id"] = last_turn_id
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.scheduled_task)
                .values(**values)
                .where(
                    tables.scheduled_task.c.workspace_id == self.workspace_id,
                    tables.scheduled_task.c.id == task.id,
                    tables.scheduled_task.c.claimed_by == task.claim_id,
                )
            )
        return updated.rowcount > 0

    async def inspect(self, expected: ScheduledTask) -> TaskInspection | None:
        """One task's live picture beyond its definition: its timing marks and the latest fire's
        turn with its terminal outcome — the read the `scheduled_task` object kind renders as
        status."""
        agent_id = object_agent_id()
        query = (
            sa.select(
                tables.scheduled_task.c.next_run_at,
                tables.scheduled_task.c.last_run_at,
                tables.scheduled_task.c.expires_at,
                tables.scheduled_task.c.last_turn_id,
                tables.turn.c.status.label("turn_status"),
                tables.turn.c.terminal,
            )
            .select_from(
                tables.scheduled_task.outerjoin(
                    tables.turn, tables.scheduled_task.c.last_turn_id == tables.turn.c.id
                )
            )
            .where(
                tables.scheduled_task.c.workspace_id == self.workspace_id,
                tables.scheduled_task.c.id == expected.id,
                tables.scheduled_task.c.agent_id == agent_id,
                tables.scheduled_task.c.name == expected.name,
                tables.scheduled_task.c.schedule != ONE_TIME_SCHEDULE,
            )
        )
        async with workspace_tx() as connection:
            row = (await connection.execute(query)).mappings().one_or_none()
        if row is None:
            return None
        terminal = row["terminal"]
        return TaskInspection(
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            expires_at=_utc(row["expires_at"]),
            last_turn_id=row["last_turn_id"],
            last_turn_status=row["turn_status"],
            last_response=(terminal or {}).get("text") if terminal else None,
        )
