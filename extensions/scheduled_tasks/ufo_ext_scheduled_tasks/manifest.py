"""What the scheduled-tasks extension declares: the `scheduled_task` object kind, the durable pause
tool, a batch-at-interval runner for each of the two waiting kinds of row, and the task-scheduling
skill the agent loads before scheduling.

Both runners are recurring jobs (they fire on the clock, never on the rows they write), and each
names only the workspaces its own table has due work in, so a workspace with nothing waiting costs
the dispatcher nothing. Two jobs rather than one because they are two failure domains: a wedged cron
fire must not hold back a due resume. Both tables are the extension's own, migrations included, and
both fire by invoking the conversation the row names.

Watching is not here. A monitor always probes, and that is a different question from waiting on
time, so it is the `monitors` extension with its own table, runner, and kind."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest, SkillSpec
from ufo_ext_scheduled_tasks.conversation_slot import AUTOMATIONS_SLOT
from ufo_ext_scheduled_tasks.pause_runner import PauseRunner
from ufo_ext_scheduled_tasks.pauses import due_pause_workspaces
from ufo_ext_scheduled_tasks.runner import ScheduledTaskRunner
from ufo_ext_scheduled_tasks.schedules import due_task_workspaces
from ufo_ext_scheduled_tasks.tools import PAUSE_AND_WAIT_TOOL, SCHEDULED_TASK_OBJECT

NAME = "scheduled_tasks"
VERSION = "0.1.0"
RUNNER_JOB = "scheduled_task_runner"
PAUSE_RUNNER_JOB = "pause_runner"
RUNNER_SCHEDULE = "0 * * * * *"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("task-scheduling",)


async def _run(ctx: ExtensionContext) -> None:
    await ScheduledTaskRunner(ctx=ctx).run()


async def _resume(ctx: ExtensionContext) -> None:
    await PauseRunner(ctx=ctx).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(PAUSE_AND_WAIT_TOOL,),
        objects=(SCHEDULED_TASK_OBJECT,),
        jobs=(
            JobSpec(
                name=RUNNER_JOB,
                schedule=RUNNER_SCHEDULE,
                handler=_run,
                candidates=due_task_workspaces(),
                spends_the_balance=True,
            ),
            JobSpec(
                name=PAUSE_RUNNER_JOB,
                schedule=RUNNER_SCHEDULE,
                handler=_resume,
                candidates=due_pause_workspaces(),
                spends_the_balance=True,
            ),
        ),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        requires=("memory_search",),
        conversation_slots=(AUTOMATIONS_SLOT,),
    )
