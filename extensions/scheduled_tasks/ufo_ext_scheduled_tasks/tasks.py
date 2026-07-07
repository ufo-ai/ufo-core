"""The chat-native scheduling tools: an agent schedules, cancels, and lists its recurring tasks.

Each runs inside a turn, so it reads the conversation and agent to re-enter from the turn's context
and writes through the workspace-scoped `ScheduleStore` its ExtensionContext carries — a scheduled
fire later re-enters this same conversation as this same agent. `schedule_task` derives a stable
name from the task so a later `cancel_scheduled_task` addresses it, and re-scheduling an existing
name updates it in place."""

import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from ufo.sdk.scheduling import ScheduleStore
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_scheduled_tasks.cron import next_fire, validate_cron

NAME_PREFIX = "scheduled"
MAX_NAME = 52


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
