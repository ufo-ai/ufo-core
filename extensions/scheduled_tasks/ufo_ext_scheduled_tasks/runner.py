"""The scheduled-task runner: a batch-at-interval poll that retires expired tasks and fires every
due task once.

It runs as the extension's recurring job, so it fires on the clock — never on the schedule rows it
writes. Each tick claims the due tasks (a lease, so an overlapping tick never fires one twice),
retires any whose expiry passes before invocation, invokes each remaining exact claimed version
back into its conversation (a refire collapses to the turn already admitted), and advances a cron
row once that occurrence is accepted by the turn outbox. Member admission and a claimed one-time
pause arbitrate under the conversation lock; the pause remains recovery state until the turn
worker claims it. A failed fire keeps its leased occurrence for retry. A tick with failures raises
their names.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from ufo.sdk.context import ExtensionContext
from ufo.sdk.scheduling import ONE_TIME_SCHEDULE, ScheduledTask, ScheduleStore
from ufo_ext_scheduled_tasks.cron import next_fire

CLAIM_LEASE_SECONDS = 300


@dataclass(frozen=True)
class ScheduledTaskRunner:
    ctx: ExtensionContext
    lease_seconds: int = CLAIM_LEASE_SECONDS

    async def run(self) -> None:
        if self.ctx.scheduler is None:
            raise RuntimeError("scheduled-task runner requires a scheduler; none is wired")
        scheduler = self.ctx.scheduler
        now = datetime.now(UTC)
        failures: list[str] = []
        for task in await scheduler.claim_due(now, self.lease_seconds):
            failure = await self._fire(scheduler, task, now, datetime.now(UTC))
            if failure is not None:
                failures.append(failure)
        if failures:
            raise RuntimeError("scheduled task fires failed: " + ", ".join(failures))

    async def _fire(
        self,
        scheduler: ScheduleStore,
        task: ScheduledTask,
        tick_at: datetime,
        expiry_checked_at: datetime,
    ) -> str | None:
        failure: str | None = None
        if await scheduler.retire_if_expired(task, expiry_checked_at):
            return None
        try:
            turn_id = await scheduler.invoke(task)
        except Exception as raised:
            failure = f"{task.name} ({type(raised).__name__})"
        else:
            if turn_id is None:
                return None
        if task.schedule != ONE_TIME_SCHEDULE and failure is None:
            await scheduler.reschedule(
                task,
                next_fire(task.schedule, tick_at),
                tick_at,
                turn_id,
            )
        return failure
