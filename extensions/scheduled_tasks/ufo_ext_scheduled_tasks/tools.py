"""The `scheduled_task` object kind and the durable workflow pause.

A scheduled task is a workspace object (RFC 0017): the agent creates, updates, lists, and deletes
recurring tasks through the generic object verbs, and this module supplies the kind — spec model,
store handlers over the workspace-scoped `ScheduleStore`, cron validation on every apply. Each
apply runs inside a turn, so the row binds the applying turn's conversation and agent: a fire
later re-enters that conversation as that agent. Pause rows (`@once`) are workflow internals,
never objects — `ScheduleStore.list` excludes them, and `pause_and_wait` stays a plain tool that
converges member ingress and timer expiry on one resume turn."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ufo.sdk.objects import MemberOwnedObjects, ObjectKind, ObjectOwner, OwnedRow, OwnerRequired
from ufo.sdk.scheduling import ScheduledTask, ScheduleStore
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron

SCHEDULED_TASK_KIND = "scheduled_task"
SUMMARY_MAX = 120
RESPONSE_EXCERPT_MAX = 400
SCHEDULE_GATE = "only the task's creator may change a scheduled task"
DELETE_GATE = "only the task's creator or the workspace owner may delete a scheduled task"
MAX_WAIT_MINUTES = 10_080
PAUSE_DIRECTIVE = (
    "Reply with `ai_response`, then end your turn. The workflow resumes when a new message arrives "
    "or the durable timer fires."
)
MEMBER_RESUME_DIRECTIVE = (
    "A newer member message has already been admitted, so no timer was armed. Reply with "
    "`ai_response`, then end your turn; that message resumes the workflow."
)


class ScheduledTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: str = Field(
        description="A single 5-field cron schedule in UTC (e.g. '0 9 * * 1-5' for 9am weekdays)."
    )
    prompt: str = Field(description="The task prompt delivered to the agent on each fire.")
    description: str = Field(
        default="", description="One line shown in listings; the prompt stands in when empty."
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Absolute UTC timestamp after which the task is cancelled before firing.",
    )

    @field_validator("expires_at")
    @classmethod
    def validate_utc_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("expires_at must be a UTC timestamp")
        return value


class PauseAndWaitInput(BaseModel):
    ai_response: str = Field(description="Message to show while the workflow waits.")
    wait_minutes: int = Field(
        ge=1,
        le=MAX_WAIT_MINUTES,
        description="Minutes before the workflow resumes automatically.",
    )
    next_steps: str = Field(description="Instructions for the resumed turn.")
    reason: str = Field(description="Why the workflow is waiting.")
    metadata: dict[str, JsonValue] | None = Field(
        default=None, description="State the resumed turn needs."
    )


def _require_scheduler(ctx: ToolContext) -> ScheduleStore:
    if ctx.ext is None or ctx.ext.scheduler is None:
        raise RuntimeError("scheduled tasks require the scheduled-tasks ExtensionContext and store")
    return ctx.ext.scheduler


def _summary(task: ScheduledTask) -> str:
    return f"{task.schedule} — {task.description or task.prompt}"[:SUMMARY_MAX]


@dataclass(frozen=True)
class ScheduledTaskObjects(MemberOwnedObjects[ScheduledTaskSpec]):
    """The kind's handlers over `ScheduleStore`: a task is private to the member who created it, so
    only that member or the workspace owner sees and mutates it — the per-member visibility and
    ownership gate is the base's. This kind supplies the task rows, their specs and status, and the
    upsert/cancel domain acts. Each apply binds the applying turn's conversation and agent, so a
    later fire re-enters that conversation as that agent, acting on behalf of the creator."""

    kind_name: ClassVar[str] = SCHEDULED_TASK_KIND
    mutate_gate: ClassVar[str] = SCHEDULE_GATE
    delete_gate: ClassVar[str] = DELETE_GATE

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]:
        return tuple(
            OwnedRow(
                name=task.name,
                summary=_summary(task),
                owner=ObjectOwner(member_id=task.created_by_member_id, shared=False),
            )
            for task in await _require_scheduler(ctx).list()
        )

    async def _spec(self, ctx: ToolContext, name: str) -> ScheduledTaskSpec | None:
        task = await self._find(ctx, name)
        if task is None:
            return None
        return ScheduledTaskSpec(
            schedule=task.schedule,
            prompt=task.prompt,
            description=task.description,
            expires_at=task.expires_at,
        )

    async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        inspection = await _require_scheduler(ctx).inspect(name)
        if inspection is None:
            return None
        last_run: dict[str, JsonValue] | None = None
        if inspection.last_turn_id is not None:
            last_run = {
                "turn_id": str(inspection.last_turn_id),
                "turn_status": inspection.last_turn_status,
                "response": (
                    None
                    if inspection.last_response is None
                    else inspection.last_response[:RESPONSE_EXCERPT_MAX]
                ),
            }
        return {
            "reports_to": {
                "conversation_id": str(inspection.conversation_id),
                "surface": inspection.surface,
            },
            "next_run_at": inspection.next_run_at.isoformat(),
            "last_run_at": (
                None if inspection.last_run_at is None else inspection.last_run_at.isoformat()
            ),
            "expires_at": (
                None if inspection.expires_at is None else inspection.expires_at.isoformat()
            ),
            "updated_at": inspection.updated_at.isoformat(),
            "last_run": last_run,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ScheduledTaskSpec,
        old: ScheduledTaskSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        if owner is not None and owner.member_id != ctx.acting_member_id:
            raise OwnerRequired(SCHEDULE_GATE)
        schedule = validate_cron(spec.schedule)
        await _require_scheduler(ctx).create(
            conversation_id=ctx.turn.conversation_id,
            agent_id=ctx.turn.agent_id,
            name=name,
            schedule=schedule,
            prompt=spec.prompt,
            description=spec.description,
            next_run_at=next_fire(schedule, datetime.now(UTC)),
            created_by_member_id=ctx.acting_member_id,
            expires_at=spec.expires_at,
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        await _require_scheduler(ctx).cancel(name)

    async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None:
        return next(
            (task for task in await _require_scheduler(ctx).list() if task.name == name), None
        )


SCHEDULED_TASK_OBJECT = ObjectKind(
    name=SCHEDULED_TASK_KIND,
    description=(
        "A durable recurring task: a 5-field UTC cron schedule that re-invokes the agent with "
        "the spec's prompt, reporting into the conversation that created it. Private to its "
        "creator — only the creator or the workspace owner sees, updates, or deletes it; a fire "
        "acts on the creator's behalf. One-shot scheduling is not supported."
    ),
    guidance=(
        "Apply a manifest to schedule a recurring task for yourself: give a 5-field cron "
        "schedule and the task prompt. The platform materializes a locked-down recurring task "
        "that searches memory, then invokes you on that schedule in the conversation the task "
        "was created from; re-applying an existing name updates the definition in place and "
        "never moves where it reports. A task is private to its creator: reads, updates, and "
        "delete are the creator's or the workspace owner's — another member's tasks are not "
        "visible. A fire acts as the creator and uses the creator's private connections, but "
        "recalls only the memory its reporting conversation can see (shared-only in a channel). "
        "Listing returns each task's name, schedule, and description; get shows where it reports "
        "and the latest run's response. A run's per-run output is not durable memory — it belongs "
        "in the reply the run posts, not in a saved fact; keep in-task state in files or todo "
        "items. Load the task-scheduling skill before scheduling."
    ),
    spec_model=ScheduledTaskSpec,
    store=ScheduledTaskObjects(),
)


async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult:
    scheduler = _require_scheduler(ctx)
    resume_at = datetime.now(UTC) + timedelta(minutes=args.wait_minutes)
    wakeup = {
        "resuming": "timer",
        "reason": args.reason,
        "next_steps": args.next_steps,
        "metadata": args.metadata,
    }
    pause = await scheduler.pause(
        conversation_id=ctx.turn.conversation_id,
        agent_id=ctx.turn.agent_id,
        prompt="Resume the paused workflow.\n" + json.dumps(wakeup),
        description=args.reason,
        next_run_at=resume_at,
        origin_seq=ctx.turn.seq,
        created_by_member_id=ctx.acting_member_id,
    )
    if pause is None or pause.resume_turn_id is not None:
        payload = {
            "awaiting": "member",
            "ai_response": args.ai_response,
            "next_steps": args.next_steps,
            "reason": args.reason,
            "metadata": args.metadata,
        }
        return ToolResult(
            content=(TextContent(text=f"{MEMBER_RESUME_DIRECTIVE}\n{json.dumps(payload)}"),)
        )
    payload = {
        "awaiting": "timer",
        "ai_response": args.ai_response,
        "resume_at": resume_at.isoformat(),
        "next_steps": args.next_steps,
        "reason": args.reason,
        "metadata": args.metadata,
    }
    return ToolResult(content=(TextContent(text=f"{PAUSE_DIRECTIVE}\n{json.dumps(payload)}"),))


PAUSE_AND_WAIT_TOOL = ToolDef(
    name="pause_and_wait",
    description=(
        "Pause this workflow until a new message arrives or a durable timer expires. Use for "
        "verification emails, manual approvals, and external cooldowns. The resumed turn "
        "receives `next_steps` and `metadata`."
    ),
    input_model=PauseAndWaitInput,
    handler=pause_and_wait,
    side_effecting=True,
)
