"""What the scheduled-tasks extension declares: the chat tools, the batch-at-interval runner, and
the task-scheduling skill the agent loads before scheduling.

The runner is a recurring job (fires on the clock, never on the schedule rows it writes); the tools
let an agent schedule, cancel, and list its recurring tasks in chat. Both reach the durable schedule
rows through the workspace-scoped `ScheduleStore` their ExtensionContext carries, and a fire drives
the exact claimed schedule version through that store's invocation capability."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo.sdk.scheduling import due_task_workspaces
from ufo_ext_scheduled_tasks.runner import ScheduledTaskRunner
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_TOOLS

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
        tools=SCHEDULED_TASK_TOOLS,
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
