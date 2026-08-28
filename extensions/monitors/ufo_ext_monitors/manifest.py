"""What the monitors extension declares: the `monitor` object kind, its `monitor` action, and the
per-minute runner that probes every due watch.

A monitor is a durable watch — a shell probe run in a conversation's sandbox on an interval, under
a required deadline — and it owns its own table, its own migration, and its own runner. The runner
is a recurring job, so it fires on the clock and never on the rows it writes; it names only the
workspaces its table has due work in, so a workspace with nothing armed costs the dispatcher
nothing.

The extension is separate from `scheduled_tasks` because the two answer different questions. A
scheduled task and a pause both wait on time; a monitor always probes, and its state is a baseline
and a set of streak counters that nothing else reads."""

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest
from ufo_ext_monitors.monitor_kind import MONITOR_OBJECT
from ufo_ext_monitors.monitor_runner import MonitorRunner
from ufo_ext_monitors.monitor_tool import MONITOR_TOOL
from ufo_ext_monitors.monitors import due_monitor_workspaces

NAME = "monitors"
VERSION = "0.1.0"
RUNNER_JOB = "monitor_runner"
RUNNER_SCHEDULE = "0 * * * * *"


async def _probe(ctx: ExtensionContext) -> None:
    await MonitorRunner(ctx=ctx).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(MONITOR_TOOL,),
        objects=(MONITOR_OBJECT,),
        jobs=(
            JobSpec(
                name=RUNNER_JOB,
                schedule=RUNNER_SCHEDULE,
                handler=_probe,
                candidates=due_monitor_workspaces(),
            ),
        ),
    )
