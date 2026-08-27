"""Waiting on sandbox commands: the completion signal is a foreground return, a poll loop, or the
task's exit file, never a padded sleep. The runtime refuses foreground flat sleeps with the same
detector these graders read (`ufo.tools.tasks.flat_sleeps`), so the suite grades exactly what the
guard enforces. Workload pauses are Python sleeps the shell detector cannot see, so a case's
workload runs under the guard while any shell-level padding around it still fails the case."""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
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
)
