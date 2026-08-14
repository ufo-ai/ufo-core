"""The scheduled-task runner: a batch-at-interval poll that retires expired tasks and fires every
due task once.

It runs as the extension's recurring job, so it fires on the clock — never on the schedule rows it
writes. Each tick claims the due tasks (a lease, so an overlapping tick never fires one twice),
retires any whose expiry passes before invocation, revalidates the claim, invokes the exact claimed
version back into its conversation, and advances the row to its next cron occurrence once that one
is accepted by the turn outbox. Every schedule is a cron schedule, so every accepted fire has a
following one to advance to. A failed fire keeps its leased occurrence for retry. A tick with
failures raises their names.

The fire body is composed here. It is a scheduled turn — `as_scheduled=True` gives it the meaning
core keys every scheduled behaviour on (its own turn, never folded, seat-gated on the creator) —
and its idempotency key is the task and the exact occurrence, so a redelivery across a deploy roll
settles on the turn already admitted rather than firing twice."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from ufo.sdk.context import ExtensionContext
from ufo_ext_scheduled_tasks.cron import next_fire
from ufo_ext_scheduled_tasks.schedules import ScheduledTask, ScheduleStore

CLAIM_LEASE_SECONDS = 300
FINAL_FIRE_INSTRUCTION = (
    "This is the final permitted fire. Complete the scheduled task and settle its result; an "
    "external-tool failure is a result, not a reason to retry after the check-in. Then call "
    'ask_user as the final tool with choices "Continue same cadence", "Change cadence", and '
    '"Stop". After ask_user returns, call no more tools; close with the task result and '
    "continuation question."
)


def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]:
    """The inbound a scheduled fire delivers, and the key it is admitted under.

    The two renderings of the occurrence differ on purpose and must not be collapsed: the key keeps
    `isoformat()`'s `+00:00`, because it is the dedupe contract a turn admitted before a deploy roll
    was keyed with, while the `scheduled_fire` line the model reads carries `Z`."""
    fire_at = task.next_run_at.astimezone(UTC).isoformat()
    inbound = (
        "<scheduled_task>\n"
        f"scheduled_fire: {fire_at.replace('+00:00', 'Z')}\n"
        "</scheduled_task>\n"
        f"{task.prompt}"
    )
    if runtime_instruction is not None:
        inbound += (
            f"\n<scheduled_task_instruction>\n{runtime_instruction}\n</scheduled_task_instruction>"
        )
    return inbound, f"{task.id}:{fire_at}"


@dataclass(frozen=True)
class ScheduledTaskRunner:
    ctx: ExtensionContext
    lease_seconds: int = CLAIM_LEASE_SECONDS

    async def run(self) -> None:
        store = ScheduleStore(self.ctx)
        now = datetime.now(UTC)
        failures: list[str] = []
        for task in await store.claim_due(now, self.lease_seconds):
            failure = await self._fire(store, task, now, datetime.now(UTC))
            if failure is not None:
                failures.append(failure)
        if failures:
            raise RuntimeError("scheduled task fires failed: " + ", ".join(failures))

    async def _fire(
        self,
        store: ScheduleStore,
        task: ScheduledTask,
        tick_at: datetime,
        expiry_checked_at: datetime,
    ) -> str | None:
        failure: str | None = None
        if await store.retire_if_expired(task, expiry_checked_at):
            return None
        following_fire = next_fire(task.schedule, tick_at)
        runtime_instruction = (
            FINAL_FIRE_INSTRUCTION
            if task.expires_at is not None and following_fire >= task.expires_at
            else None
        )
        if not await store.claim_holds(task):
            return None
        inbound, key = fire_body(task, runtime_instruction)
        turn_id: UUID | None = None
        try:
            turn_id = await self.ctx.invoke(
                task.conversation_id,
                task.agent_id,
                inbound,
                key,
                on_behalf_of_member_id=task.created_by_member_id,
                as_scheduled=True,
            )
        except Exception as raised:
            failure = f"{task.name} ({type(raised).__name__})"
        else:
            if turn_id is None:
                return None
            await store.reschedule(task, following_fire, tick_at, turn_id)
        return failure
