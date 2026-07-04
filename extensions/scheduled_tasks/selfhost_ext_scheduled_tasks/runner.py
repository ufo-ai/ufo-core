"""The scheduled-task runner: a batch-at-interval poll that fires every due task once.

It runs as the extension's recurring job, so it fires on the clock — never on the schedule rows it
writes. Each tick claims the due tasks (a lease, so an overlapping tick never fires one twice),
invokes each back into its conversation with a per-fire idempotency key (a refired key collapses to
the turn already admitted), and reschedules it to its next cron fire — advancing past the fire
whether the invoke landed or not, so one poisoned task neither replays every tick nor blocks its
siblings. A tick that saw any fire fail ends by raising the names that could not fire, so the
failure surfaces rather than being swallowed."""

from dataclasses import dataclass
from datetime import UTC, datetime

from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.scheduling import ScheduledTask, ScheduleStore
from selfhost_ext_scheduled_tasks.cron import next_fire

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
            failure = await self._fire(scheduler, task, now)
            if failure is not None:
                failures.append(failure)
        if failures:
            raise RuntimeError("scheduled task fires failed: " + ", ".join(failures))

    async def _fire(
        self, scheduler: ScheduleStore, task: ScheduledTask, now: datetime
    ) -> str | None:
        firing_key = f"{task.id}:{task.next_run_at.isoformat()}"
        failure: str | None = None
        try:
            await self.ctx.invoke(task.conversation_id, task.agent_id, task.prompt, firing_key)
        except Exception as raised:
            failure = f"{task.name} ({type(raised).__name__})"
        await scheduler.reschedule(task.id, next_fire(task.schedule, now), now)
        return failure
