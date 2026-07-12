"""Chat-native recurring tasks and durable workflow pauses.

Each runs inside a turn, so it reads the conversation and agent to re-enter from the turn's context
and writes through the workspace-scoped `ScheduleStore` its ExtensionContext carries — a scheduled
fire later re-enters this same conversation as this same agent. `schedule_task` derives a stable
name from the task so a later `cancel_scheduled_task` addresses it, and re-scheduling an existing
name updates it in place. `pause_and_wait` stores one hidden `@once` row that converges member
ingress and timer expiry on one resume turn, retained until that turn is claimed."""

import json
import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, JsonValue

from ufo.sdk.scheduling import ScheduleStore
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron

NAME_PREFIX = "scheduled"
MAX_NAME = 52
MAX_WAIT_MINUTES = 10_080
PAUSE_DIRECTIVE = (
    "Reply with `ai_response`, then end your turn. The workflow resumes when a new message arrives "
    "or the durable timer fires."
)
MEMBER_RESUME_DIRECTIVE = (
    "A newer member message has already been admitted, so no timer was armed. Reply with "
    "`ai_response`, then end your turn; that message resumes the workflow."
)


class ScheduleTaskInput(BaseModel):
    schedule: str = Field(
        description="A 5-field cron schedule (e.g. '0 9 * * 1' for 9am Mondays) on which the task "
        "runs."
    )
    prompt: str = Field(description="The task prompt the platform sends to you on each run.")
    name: str | None = Field(
        default=None, description="Optional name for the task; defaults to a slug of the prompt."
    )
    description: str | None = Field(
        default=None, description="Optional human-readable description shown in the task list."
    )


class CancelScheduledTaskInput(BaseModel):
    name: str = Field(description="Name of the scheduled task to cancel.")


class ListScheduledTasksInput(BaseModel):
    pass


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
        raise RuntimeError("schedule tools require the scheduled-tasks ExtensionContext and store")
    return ctx.ext.scheduler


def _slug(source: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    budget = MAX_NAME - len(NAME_PREFIX) - 1
    slug = slug[:budget].strip("-")
    return f"{NAME_PREFIX}-{slug}" if slug else NAME_PREFIX


async def schedule_task(ctx: ToolContext, args: ScheduleTaskInput) -> ToolResult:
    scheduler = _require_scheduler(ctx)
    schedule = validate_cron(args.schedule)
    task = await scheduler.create(
        conversation_id=ctx.turn.conversation_id,
        agent_id=ctx.turn.agent_id,
        name=_slug(args.name or args.prompt),
        schedule=schedule,
        prompt=args.prompt,
        description=args.description or args.prompt,
        next_run_at=next_fire(schedule, datetime.now(UTC)),
    )
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Scheduled {task.name!r} ({task.schedule}); next run at "
                    f"{task.next_run_at.isoformat()}. Cancel it with cancel_scheduled_task "
                    f"name={task.name!r}."
                )
            ),
        )
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


async def cancel_scheduled_task(ctx: ToolContext, args: CancelScheduledTaskInput) -> ToolResult:
    scheduler = _require_scheduler(ctx)
    cancelled = await scheduler.cancel(args.name)
    text = (
        f"Cancelled scheduled task {args.name!r}."
        if cancelled
        else f"No active scheduled task named {args.name!r}."
    )
    return ToolResult(content=(TextContent(text=text),))


async def list_scheduled_tasks(ctx: ToolContext, args: ListScheduledTasksInput) -> ToolResult:
    scheduler = _require_scheduler(ctx)
    tasks = await scheduler.list()
    if not tasks:
        return ToolResult(content=(TextContent(text="No scheduled tasks."),))
    lines = [
        f"- {task.name}: {task.schedule} — {task.description} "
        f"(last run {task.last_run_at.isoformat() if task.last_run_at else 'never'})"
        for task in tasks
    ]
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


SCHEDULED_TASK_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="schedule_task",
        description=(
            "Schedule a recurring task for yourself: give a 5-field cron schedule and the task "
            "prompt. The platform materializes a locked-down recurring job that searches memory, "
            "then invokes you on that schedule in this conversation."
        ),
        input_model=ScheduleTaskInput,
        handler=schedule_task,
    ),
    ToolDef(
        name="cancel_scheduled_task",
        description=(
            "Cancel a scheduled task in this workspace by name — any managed task, not only ones "
            "you created. The platform deletes the managed recurring task."
        ),
        input_model=CancelScheduledTaskInput,
        handler=cancel_scheduled_task,
    ),
    ToolDef(
        name="list_scheduled_tasks",
        description=(
            "List this workspace's recurring scheduled tasks — each with its name, cron schedule, "
            "description, and last run. Pass a returned name to cancel_scheduled_task."
        ),
        input_model=ListScheduledTasksInput,
        handler=list_scheduled_tasks,
    ),
    ToolDef(
        name="pause_and_wait",
        description=(
            "Pause this workflow until a new message arrives or a durable timer expires. Use for "
            "verification emails, manual approvals, and external cooldowns. The resumed turn "
            "receives `next_steps` and `metadata`."
        ),
        input_model=PauseAndWaitInput,
        handler=pause_and_wait,
        side_effecting=True,
    ),
)
