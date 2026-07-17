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

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ufo.sdk.objects import OBJECT_LIST_PAGE, ObjectKind, ObjectPage, ObjectRow
from ufo.sdk.scheduling import ScheduledTask, ScheduleStore
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron

SCHEDULED_TASK_KIND = "scheduled_task"
SUMMARY_MAX = 120
RESPONSE_EXCERPT_MAX = 400
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
class ScheduledTaskObjects:
    """The kind's handlers over `ScheduleStore`: apply validates the cron and upserts the row
    bound to the applying turn's conversation and agent; list pages the name-ordered rows by
    keyset; status renders the timing marks beside the spec. Any member may mutate — the kind
    declares no owner gate, matching the tools it replaces."""

    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage:
        tasks = [
            task
            for task in await _require_scheduler(ctx).list()
            if query in task.name or query in _summary(task)
        ]
        remaining = [task for task in tasks if task.name > cursor] if cursor else tasks
        page, rest = remaining[:OBJECT_LIST_PAGE], remaining[OBJECT_LIST_PAGE:]
        rows = tuple(ObjectRow(name=task.name, summary=_summary(task)) for task in page)
        return ObjectPage(rows=rows, next_cursor=page[-1].name if rest else None)

    async def get(self, ctx: ToolContext, name: str) -> ScheduledTaskSpec | None:
        task = await self._find(ctx, name)
        if task is None:
            return None
        return ScheduledTaskSpec(
            schedule=task.schedule, prompt=task.prompt, description=task.description
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
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
            "updated_at": inspection.updated_at.isoformat(),
            "last_run": last_run,
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ScheduledTaskSpec,
        old: ScheduledTaskSpec | None,
    ) -> None:
        schedule = validate_cron(spec.schedule)
        await _require_scheduler(ctx).create(
            conversation_id=ctx.turn.conversation_id,
            agent_id=ctx.turn.agent_id,
            name=name,
            schedule=schedule,
            prompt=spec.prompt,
            description=spec.description,
            next_run_at=next_fire(schedule, datetime.now(UTC)),
        )

    async def delete(self, ctx: ToolContext, name: str) -> None:
        await _require_scheduler(ctx).cancel(name)

    async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None:
        return next(
            (task for task in await _require_scheduler(ctx).list() if task.name == name), None
        )


SCHEDULED_TASK_OBJECT = ObjectKind(
    name=SCHEDULED_TASK_KIND,
    description=(
        "A durable recurring task: a 5-field UTC cron schedule that re-invokes the agent with "
        "the spec's prompt, reporting into the conversation that created it. Any member may "
        "create, update, or delete; one-shot scheduling is not supported."
    ),
    guidance=(
        "Apply a manifest to schedule a recurring task for yourself: give a 5-field cron "
        "schedule and the task prompt. The platform materializes a locked-down recurring task "
        "that searches memory, then invokes you on that schedule in the conversation the task "
        "was created from; re-applying an existing name updates the definition in place and "
        "never moves where it reports. Delete cancels any managed task in this workspace by "
        "name — not only ones you created. Listing returns each task's name, schedule, and "
        "description; get shows where it reports and the latest run's response. Load the "
        "task-scheduling skill before scheduling."
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
