"""Arming a durable watch versus waiting on the member. Both cases are authored — the monitor
action has no production invocation yet — and neither brief names the action. The watch case
stages the file its probe reads, so the arming probe succeeds inside the sandbox and the grade is
the choice of act, not the fixture's plumbing. The monitors extension is outside the hosted pack,
so the suite runs only on the packs that activate it."""

from evals.harness.capability import CapabilityCase, WorkspaceFile
from evals.harness.scorers import (
    attempted_tools_scorer,
    combine,
    required_tools_scorer,
    restraint_scorer,
)

MONITOR_ACTION = "action:monitor:monitor"
WAIT_TOOL = "pause_and_wait"
MONITOR_PACKS = ("assistant", "assistant_eval")
DEPLOY_LOG = WorkspaceFile("deploy.log", b"12:04 building image\n12:06 pushing image\n")

CASES = (
    CapabilityCase(
        "watch-deploy-log",
        "The deploy writes its progress to deploy.log in my workspace, one line per step, and the "
        "last line is the current step. Keep watching that file and tell me the moment its last "
        "line changes. If nothing has changed after two hours, stop watching and say so.",
        combine(required_tools_scorer((MONITOR_ACTION,)), restraint_scorer((WAIT_TOOL,))),
        workspace_files=(DEPLOY_LOG,),
        digest_tag="monitor-arm:watch-deploy-log:authored",
    ),
    CapabilityCase(
        "wait-for-my-numbers",
        "I'm pulling this quarter's numbers together right now — hold on and I'll paste them here "
        "in a few minutes. Don't do anything until I do.",
        attempted_tools_scorer(((WAIT_TOOL, {}),), (MONITOR_ACTION,), ()),
        digest_tag="monitor-arm:wait-for-my-numbers:authored",
    ),
)
