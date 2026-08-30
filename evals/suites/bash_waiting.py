"""Waiting on sandbox commands: the completion signal is a foreground return, a poll loop, or the
task's exit file, never a padded sleep. The runtime refuses foreground flat sleeps with the same
detector these graders read (`ufo.tools.tasks.flat_sleeps`), so the suite grades exactly what the
guard enforces. Workload pauses are Python sleeps the shell detector cannot see, so a case's
workload runs under the guard while any shell-level padding around it still fails the case."""

from asyncio import create_subprocess_exec
from asyncio.subprocess import DEVNULL, Process
from pathlib import Path
from uuid import UUID

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject
from ufo.tools.tasks import flat_sleeps

FOREGROUND_WORKLOAD = "python3 -c \"import time; time.sleep(40); print('BUILD-OK-7391')\""
BACKGROUND_WORKLOAD = (
    "python3 -c \"import time; time.sleep(90); open('/workspace/out.txt', 'w').write('DONE-4213')\""
)


def waiting_scorer(marker: str, workload_token: str, async_ok: bool = False) -> Grader:
    """`async_ok` accepts the detached shape: the workload started with `background: true` and the
    turn answered without its content — delivery then rides the task journal and monitors, proven
    in their own suites. A blocking shape must carry the marker in the answer either way."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        ran_workload = False
        detached_workload = False
        for call in output.calls:
            if call.name != "bash":
                continue
            command = str(call.input.get("command", ""))
            padded = flat_sleeps(command)
            if padded:
                return CapabilityVerdict(False, f"padded the wait with sleep {max(padded)}")
            if marker in command and workload_token in command:
                ran_workload = True
                if call.input.get("background") is True:
                    detached_workload = True
        if not ran_workload:
            return CapabilityVerdict(False, "never ran the workload command")
        if marker in output.response:
            return CapabilityVerdict(True, "workload ran and the answer carries its marker")
        if async_ok and detached_workload:
            return CapabilityVerdict(True, "workload detached without padding; delivery is armed")
        return CapabilityVerdict(False, "the answer does not carry the workload's marker")

    return DescribedGrader(
        "the workload runs without any flat-sleep padding, and the answer carries its marker or "
        "the workload was detached with background: true",
        grade,
    )


WORKFLOW_WAIT_SECONDS = 1800.0

LONG_WORKLOAD = (
    'python3 -c "import time; time.sleep(300); '
    "open('/workspace/release.txt', 'w').write('SHIPPED-5507')\""
)
LONG_MARKER = "SHIPPED-5507"
MAX_UNPADDED_WAITS = 2


def long_wait_scorer() -> Grader:
    """A wait far longer than one foreground budget. What separates the shapes is how many times
    the turn paid a foreground budget for the same wait: one call whose budget covers the work
    passes, a queue of short hops does not. A foreground hop counts whether it succeeds or exits
    nonzero — a poll that finds the work unfinished times out or returns an error, and that is
    exactly the budget being paid. What a hop's command mentions decides nothing: a hop that waits
    on the detached task's own exit file is the same repeated wait as one that names the release
    file. A flat `sleep` the runtime refused never ran, so it is neither counted nor charged; the
    model that tried one and recovered is graded on what it then did."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        waits: list[int] = []
        ran_workload = False
        for call in output.calls:
            if call.name != "bash":
                continue
            command = str(call.input.get("command", ""))
            if flat_sleeps(command):
                continue
            if "time.sleep(300)" in command:
                ran_workload = True
            if call.input.get("background") is not True:
                budget = call.input.get("timeout")
                waits.append(budget if isinstance(budget, int) else 0)
        evidence: JsonObject = {"foreground_waits": len(waits), "wait_budgets_ms": list(waits)}
        if not ran_workload:
            return CapabilityVerdict(False, "never started the release job", evidence)
        if len(waits) > MAX_UNPADDED_WAITS:
            return CapabilityVerdict(
                False, f"paid a foreground budget {len(waits)} times for one wait", evidence
            )
        if LONG_MARKER not in output.response:
            return CapabilityVerdict(
                False, "the answer does not carry the release marker", evidence
            )
        return CapabilityVerdict(True, f"waited {len(waits)} time(s) and answered", evidence)

    return DescribedGrader(
        "the release marker reaches the answer without the turn paying a foreground budget more "
        f"than {MAX_UNPADDED_WAITS} times for the same wait",
        grade,
    )


RELEASE_MARKER = "SHIPPED-5507"
RELEASE_SECONDS = 900
RELEASE_CTL = "release-ctl"
MAX_RELEASE_POLLS = 4
RELEASE_COMPLETE = ".release-complete"
RELEASE_SCRIPT = f"""#!/bin/sh
done_file="$(dirname "$0")/{RELEASE_COMPLETE}"
case "$1" in
  start)
    echo "queued: production release run 33198513316"
    ;;
  status)
    if [ -f "$done_file" ]; then
      echo "status: completed"
      echo "marker: {RELEASE_MARKER}"
    else
      echo "status: in_progress"
    fi
    ;;
esac
"""

RELEASE_RUNS: list[Process] = []


async def stage_release_run(_workspace_id: UUID, workspace: Path) -> None:
    """The release's clock, held by a detached sleeper rather than by any file the agent can read:
    `release-ctl` only tests whether the run has landed, so nothing in the sandbox says how much
    longer it will be. Sizing a wait you cannot see the end of is the case's whole question."""
    marker = workspace / RELEASE_COMPLETE
    process = await create_subprocess_exec(
        "sh",
        "-c",
        f'sleep {RELEASE_SECONDS}; : > "{marker}"',
        start_new_session=True,
        stdout=DEVNULL,
        stderr=DEVNULL,
    )
    RELEASE_RUNS.append(process)


def release_poll_scorer() -> Grader:
    """The work runs somewhere this turn cannot reach, so `background` buys nothing — the only
    lever is how much of the wait one foreground budget is asked to cover. A turn that covers it
    answers in a couple of calls; one that re-blocks in short hops pays the budget over and over,
    and that is what this case fails. Every blocking call is a hop whatever its command reads, so a
    detached poll loop followed by foreground hops on that task's own files is charged for each of
    them; only the release's start pays no wait, and a refused flat sleep never ran."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        foreground_polls = 0
        engaged = False
        for call in output.calls:
            if call.name != "bash":
                continue
            command = str(call.input.get("command", ""))
            if flat_sleeps(command):
                continue
            if f"{RELEASE_CTL} status" in command:
                engaged = True
            elif f"{RELEASE_CTL} start" in command:
                continue
            if call.input.get("background") is not True:
                foreground_polls += 1
        evidence: JsonObject = {"status_polls": foreground_polls}
        if not engaged:
            return CapabilityVerdict(False, "never polled the release status", evidence)
        if foreground_polls > MAX_RELEASE_POLLS:
            return CapabilityVerdict(
                False, f"re-blocked on the same wait {foreground_polls} times", evidence
            )
        if RELEASE_MARKER not in output.response:
            return CapabilityVerdict(
                False, "the answer does not carry the release marker", evidence
            )
        return CapabilityVerdict(True, f"polled {foreground_polls} time(s) and answered", evidence)

    return DescribedGrader(
        "the release marker reaches the answer without the turn paying a foreground budget more "
        f"than {MAX_RELEASE_POLLS} times for the same wait",
        grade,
    )


CASES = (
    CapabilityCase(
        "bash-wait-foreground",
        "Run this build command in the sandbox and reply with the exact marker line it prints: "
        f"{FOREGROUND_WORKLOAD}. It takes about 40 seconds.",
        waiting_scorer("BUILD-OK-7391", "time.sleep(40)", async_ok=True),
        digest_tag="waiting:bash-wait-foreground:python-workload",
    ),
    CapabilityCase(
        "bash-wait-background",
        f"Start this command in the sandbox: {BACKGROUND_WORKLOAD}. It writes the file after "
        "about 90 seconds. Reply with the file's content once it exists.",
        waiting_scorer("DONE-4213", "time.sleep(90)", async_ok=True),
        digest_tag="waiting:bash-wait-background:python-workload",
    ),
    CapabilityCase(
        "bash-wait-restraint",
        "Run `echo ready-9182` in the sandbox and reply with its output.",
        waiting_scorer("ready-9182", "echo ready-9182"),
        digest_tag="waiting:bash-wait-restraint",
    ),
    CapabilityCase(
        "bash-wait-long",
        "Start this release job in the sandbox: "
        f"{LONG_WORKLOAD}. It takes about five minutes and writes the file when it finishes. "
        "Reply with the file's content once it exists.",
        long_wait_scorer(),
        digest_tag="waiting:bash-wait-long:python-workload",
    ),
    CapabilityCase(
        "bash-wait-remote",
        f"Trigger the production release with `sh /workspace/{RELEASE_CTL} start`. It runs on the "
        f"build server; `sh /workspace/{RELEASE_CTL} status` reports where it has got to. Reply "
        "with the release marker once the status reports completed.",
        release_poll_scorer(),
        digest_tag="waiting:bash-wait-remote:release-ctl",
        workspace_files=(WorkspaceFile(path=RELEASE_CTL, content=RELEASE_SCRIPT.encode()),),
        prepare=stage_release_run,
    ),
)
