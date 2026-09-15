"""Detached sandbox commands followed to their end after their launching turn commits."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.db import owner_tx, workspace_tx
from ufo.harness.o11y import log, warn
from ufo.harness.sandbox.session import ExecResult, shell_path
from ufo.runtime.ext.context import AgentArchived, TurnInvoker, conversation_agent_id
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import Turn

BACKGROUND_TASKS_JOB = "background_tasks"
BACKGROUND_TASKS_SCHEDULE = "0 * * * * *"
DETACHED_FOLLOW = timedelta(hours=24)
FOLLOW_HOURS = int(DETACHED_FOLLOW.total_seconds() // 3600)
LOG_TAIL_BYTES = 4000
PROBE_TIMEOUT_SECONDS = 15
IDEMPOTENCY_PREFIX = "background-task:"
TASK_SCAN = (
    "base={base}; "
    'if [ -e "$base.exit" ]; then printf "exit %s\\n" "$(cat "$base.exit")"; '
    'elif [ -e "$base.pid" ] && kill -0 "$(cat "$base.pid")" 2>/dev/null; '
    'then printf "alive\\n"; else printf "gone\\n"; fi'
)
LOG_TAIL = "tail -c {tail} {log} 2>/dev/null || true"


class Prober(Protocol):
    """One bounded command in a conversation's sandbox, off every turn."""

    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult: ...


async def mark_detached(
    turn: Turn,
    task: str,
    runtime_base: str,
    capability_id: UUID | None = None,
) -> None:
    """Follow one detached task for the next `DETACHED_FOLLOW`."""
    if runtime_base.rsplit("/", 1)[-1] != task:
        raise ValueError(f"runtime base {runtime_base!r} does not name task {task!r}")
    now = datetime.now(UTC)
    until = now + DETACHED_FOLLOW
    async with workspace_tx() as connection:
        await connection.execute(
            sa.select(tables.turn.c.id)
            .where(tables.turn.c.id == turn.id, tables.turn.c.workspace_id == turn.workspace_id)
            .with_for_update()
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.detached_task)
            .values(
                workspace_id=turn.workspace_id,
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                sandbox_conversation_id=turn.sandbox_conversation_id or turn.conversation_id,
                task=task,
                runtime_base=runtime_base,
                capability_id=capability_id,
                follow_until=until,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=[tables.detached_task.c.turn_id, tables.detached_task.c.task],
                set_={
                    "runtime_base": runtime_base,
                    "capability_id": capability_id,
                    "follow_until": until,
                },
            )
        )
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn.id, tables.turn.c.workspace_id == turn.workspace_id)
            .values(
                detached_until=sa.select(sa.func.max(tables.detached_task.c.follow_until))
                .where(tables.detached_task.c.turn_id == turn.id)
                .scalar_subquery()
            )
        )


@dataclass(frozen=True)
class _Followed:
    conversation_id: UUID
    sandbox_conversation_id: UUID
    turn_id: UUID
    task: str
    runtime_base: str
    capability_id: UUID | None
    until: datetime


@dataclass(frozen=True)
class BackgroundTaskSweep:
    """Probe detached tasks, report terminal results once, and retire their authority."""

    probes: Prober
    invoker_for: Callable[[UUID], TurnInvoker]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    async def run(self) -> None:
        workspace_id = ws_current().workspace_id
        for followed in await self._followed(workspace_id):
            await self._follow(workspace_id, followed)

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        async with owner_tx() as connection:
            rows = (
                await connection.execute(sa.select(tables.detached_task.c.workspace_id).distinct())
            ).all()
        return tuple(row[0] for row in rows)

    async def _followed(self, workspace_id: UUID) -> tuple[_Followed, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.detached_task.c.conversation_id,
                        tables.detached_task.c.sandbox_conversation_id,
                        tables.detached_task.c.turn_id,
                        tables.detached_task.c.task,
                        tables.detached_task.c.runtime_base,
                        tables.detached_task.c.capability_id,
                        tables.detached_task.c.follow_until,
                    ).where(tables.detached_task.c.workspace_id == workspace_id)
                )
            ).all()
        return tuple(
            _Followed(
                conversation_id=row.conversation_id,
                sandbox_conversation_id=row.sandbox_conversation_id,
                turn_id=row.turn_id,
                task=row.task,
                runtime_base=row.runtime_base,
                capability_id=row.capability_id,
                until=(
                    row.follow_until
                    if row.follow_until.tzinfo is not None
                    else row.follow_until.replace(tzinfo=UTC)
                ),
            )
            for row in rows
        )

    async def _follow(self, workspace_id: UUID, followed: _Followed) -> None:
        try:
            scan = await self.probes.run(
                followed.sandbox_conversation_id,
                TASK_SCAN.format(base=shell_path(followed.runtime_base)),
                PROBE_TIMEOUT_SECONDS,
            )
        except Exception as error:
            warn(
                "background_tasks.probe_failed",
                conversation_id=str(followed.conversation_id),
                task=followed.task,
                error=repr(error),
            )
            return
        if scan.exit_code != 0:
            warn(
                "background_tasks.scan_refused",
                conversation_id=str(followed.conversation_id),
                task=followed.task,
                exit_code=scan.exit_code,
                stderr=scan.stderr[-500:],
            )
            return
        match scan.stdout.split():
            case ["exit", code]:
                headline = f"Background task {followed.task} ended with exit code {code}."
            case ["gone"]:
                headline = (
                    f"Background task {followed.task} is no longer running and wrote no exit code."
                )
            case ["alive"] if self.clock() >= followed.until:
                headline = (
                    f"Background task {followed.task} has run for {FOLLOW_HOURS} hours and is no "
                    "longer followed."
                )
            case ["alive"]:
                return
            case answer:
                raise RuntimeError(f"background task scan answered {answer!r}")
        await self._report(workspace_id, followed, headline)
        await self._settle(workspace_id, followed)

    async def _report(self, workspace_id: UUID, followed: _Followed, headline: str) -> None:
        agent_id = await conversation_agent_id(workspace_id, followed.conversation_id)
        if agent_id is None:
            raise RuntimeError(f"conversation {followed.conversation_id} has no agent")
        tail = await self.probes.run(
            followed.sandbox_conversation_id,
            LOG_TAIL.format(
                tail=LOG_TAIL_BYTES,
                log=shell_path(f"{followed.runtime_base}.log"),
            ),
            PROBE_TIMEOUT_SECONDS,
        )
        body = f"{headline} Log: {followed.runtime_base}.log"
        if tail.stdout.strip():
            body = f"{body}\n{tail.stdout.rstrip()}"
        try:
            await self.invoker_for(workspace_id).invoke(
                followed.conversation_id,
                agent_id,
                body,
                f"{IDEMPOTENCY_PREFIX}{followed.turn_id.hex}:{followed.task}",
                holds_work_already_done=True,
            )
        except AgentArchived:
            return
        log(
            "background_tasks.reported",
            conversation_id=str(followed.conversation_id),
            task=followed.task,
        )

    async def _settle(self, workspace_id: UUID, followed: _Followed) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.turn.c.id)
                .where(
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.id == followed.turn_id,
                )
                .with_for_update()
            )
            deleted = (
                await connection.execute(
                    sa.delete(tables.detached_task)
                    .where(
                        tables.detached_task.c.workspace_id == workspace_id,
                        tables.detached_task.c.turn_id == followed.turn_id,
                        tables.detached_task.c.task == followed.task,
                        tables.detached_task.c.follow_until == followed.until,
                    )
                    .returning(tables.detached_task.c.turn_id)
                )
            ).scalar_one_or_none()
            if deleted is None:
                return
            remaining = (
                sa.select(sa.func.max(tables.detached_task.c.follow_until))
                .where(tables.detached_task.c.turn_id == followed.turn_id)
                .scalar_subquery()
            )
            await connection.execute(
                sa.update(tables.turn)
                .where(
                    tables.turn.c.workspace_id == workspace_id,
                    tables.turn.c.id == followed.turn_id,
                )
                .values(detached_until=remaining)
            )
            if followed.capability_id is not None:
                still_used = sa.exists(
                    sa.select(tables.detached_task.c.turn_id).where(
                        tables.detached_task.c.capability_id == followed.capability_id
                    )
                )
                await connection.execute(
                    sa.delete(tables.sandbox_call_capability).where(
                        tables.sandbox_call_capability.c.id == followed.capability_id,
                        tables.sandbox_call_capability.c.workspace_id == workspace_id,
                        ~still_used,
                    )
                )
        log(
            "background_tasks.cleared",
            conversation_id=str(followed.conversation_id),
            task=followed.task,
        )
