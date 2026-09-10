"""The scheduled-task runner: a batch-at-interval poll that retires expired tasks and fires every
due task once.

It runs as the extension's recurring job, so it fires on the clock — never on the schedule rows it
writes. Each tick claims the due tasks (a lease, so an overlapping tick never fires one twice),
retires any whose expiry passes before invocation, revalidates the claim, invokes the exact claimed
version back into its conversation, and advances the row to its next cron occurrence once that one
is accepted by the turn outbox. Every schedule is a cron schedule, so every accepted fire has a
following one to advance to. A failed fire keeps its leased occurrence for retry. A tick with
failures raises their names. An archived app is neither a fire nor a failure: it admits no turn, so
its tasks keep the occurrence they hold and run again when it is restored.

The fire body is composed here. It is a scheduled turn — `as_scheduled=True` gives it the meaning
core keys every scheduled behaviour on (its own turn, never folded, seat-gated on the creator) —
and its idempotency key is the task and the exact occurrence, so a redelivery across a deploy roll
settles on the turn already admitted rather than firing twice."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from ufo.sdk.authority import authority_from_member_id
from ufo.sdk.context import AgentArchived, ExtensionContext, FiredBy
from ufo.sdk.scheduled_fire import scheduled_fire_key
from ufo_ext_scheduled_tasks.cron import next_fire
from ufo_ext_scheduled_tasks.schedules import ScheduledTask, ScheduleStore
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_KIND

CLAIM_LEASE_SECONDS = 300
REPORT_INSTRUCTION = (
    "A run that found nothing new posts nothing. Use share_file to broadcast a result worth "
    "sharing with the workspace. Otherwise follow the delivery register and use Markdown links "
    "as usual."
)
FINAL_FIRE_INSTRUCTION = (
    f"{REPORT_INSTRUCTION}\n\nThis is the final permitted fire. Complete the scheduled task and "
    "settle its result; an "
    "external-tool failure is a result, not a reason to retry after the check-in. Then call "
    'ask_user as the final tool with choices "Continue same cadence", "Change cadence", and '
    '"Stop". After ask_user returns, call no more tools; close with the task result and '
    "continuation question."
)


def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]:
    """The inbound a scheduled fire delivers, and the key it is admitted under.

    The two renderings of the occurrence differ on purpose and must not be collapsed: the key keeps
    `isoformat()`'s `+00:00` inside the sdk key shape — the dedupe contract a turn admitted before
    a deploy roll was keyed with, and the link the portal's runs feed resolves a run's task by —
    while the `scheduled_fire` line the model reads carries `Z`."""
    fire_at = task.next_run_at.astimezone(UTC)
    inbound = (
        "<scheduled_task>\n"
        f"scheduled_fire: {fire_at.isoformat().replace('+00:00', 'Z')}\n"
        "</scheduled_task>\n"
        f"{task.prompt}"
    )
    if runtime_instruction is not None:
        inbound += (
            f"\n<scheduled_task_instruction>\n{runtime_instruction}\n</scheduled_task_instruction>"
        )
    return inbound, scheduled_fire_key(task.id, fire_at)


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
            else REPORT_INSTRUCTION
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
                authority=authority_from_member_id(task.created_by_member_id),
                as_scheduled=True,
                fired_by=FiredBy(kind=SCHEDULED_TASK_KIND, name=task.name, title=task.name),
            )
        except AgentArchived:
            return None
        except Exception as raised:
            failure = f"{task.name} ({type(raised).__name__})"
        else:
            if turn_id is None:
                return None
            await store.reschedule(task, following_fire, tick_at, turn_id)
        return failure
