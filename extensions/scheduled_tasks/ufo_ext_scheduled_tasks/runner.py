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
FINAL_FIRE_INSTRUCTION = (
    "This is the final permitted fire. Complete the scheduled task and settle its result; an "
    "external-tool failure is a result, not a reason to retry after the check-in. Then call "
    'ask_user as the final tool with choices "Continue same cadence", "Change cadence", and '
    '"Stop". After ask_user returns, call no more tools; close with the task result and '
    "continuation question."
)


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
        following_fire = (
            next_fire(task.schedule, tick_at) if task.schedule != ONE_TIME_SCHEDULE else None
        )
        runtime_instruction = (
            FINAL_FIRE_INSTRUCTION
            if following_fire is not None
            and task.expires_at is not None
            and following_fire >= task.expires_at
            else None
        )
        try:
            turn_id = await scheduler.invoke(task, runtime_instruction)
        except Exception as raised:
            failure = f"{task.name} ({type(raised).__name__})"
        else:
            if turn_id is None:
                return None
        if following_fire is not None and failure is None:
            await scheduler.reschedule(
                task,
                following_fire,
                tick_at,
                turn_id,
            )
        return failure
