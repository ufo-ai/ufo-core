"""What the scheduled-tasks extension declares: the chat tools, the batch-at-interval runner, and
the task-scheduling skill the agent loads before scheduling.

The runner is a recurring job (fires on the clock, never on the schedule rows it writes); the tools
let an agent schedule, cancel, and list its recurring tasks in chat. Both reach the durable schedule
rows through the workspace-scoped `ScheduleStore` their ExtensionContext carries, and a fire drives
the admit-turn seam through `ExtensionContext.invoke`."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.scheduling import due_task_workspaces
from ufo.sdk.tools import ToolDef
from ufo_ext_scheduled_tasks.runner import ScheduledTaskRunner
from ufo_ext_scheduled_tasks.tasks import (
    CancelScheduledTaskInput,
    ListScheduledTasksInput,
    ScheduleTaskInput,
    cancel_scheduled_task,
    list_scheduled_tasks,
    schedule_task,
)

NAME = "scheduled_tasks"
VERSION = "0.1.0"
RUNNER_JOB = "scheduled_task_runner"
RUNNER_SCHEDULE = "0 * * * * *"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("task-scheduling",)


async def _run(ctx: ExtensionContext) -> None:
    await ScheduledTaskRunner(ctx=ctx).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="schedule_task",
                description=(
                    "Schedule a recurring task for yourself: give a 5-field cron schedule and the "
                    "task prompt. The platform materializes a locked-down recurring job that "
                    "invokes you on that schedule, in this conversation."
                ),
                input_model=ScheduleTaskInput,
                handler=schedule_task,
            ),
            ToolDef(
                name="cancel_scheduled_task",
                description=(
                    "Cancel a scheduled task in this workspace by name — any managed task, not "
                    "only ones you created. The platform deletes the managed recurring task."
                ),
                input_model=CancelScheduledTaskInput,
                handler=cancel_scheduled_task,
            ),
            ToolDef(
                name="list_scheduled_tasks",
                description=(
                    "List this workspace's recurring scheduled tasks — each with its name, cron "
                    "schedule, description, and last run. Pass a returned name to "
                    "cancel_scheduled_task."
                ),
                input_model=ListScheduledTasksInput,
                handler=list_scheduled_tasks,
            ),
        ),
        jobs=(
            JobSpec(
                name=RUNNER_JOB,
                schedule=RUNNER_SCHEDULE,
                handler=_run,
                candidates=due_task_workspaces(),
            ),
        ),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
