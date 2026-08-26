"""Waiting on sandbox commands: the completion signal is a foreground return or the task's exit
file, never a padded sleep. Each case's workload command carries the case marker and a known number
of sleeps of its own, so the grader can tell running the workload from padding around it and judges
every bash call the turn makes."""

import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)

FLAT_SLEEP_OR_LOOP = re.compile(r"(?<![\w./-])(?:sleep\s+(\d+)|(do|done))(?![\w./-])")
QUOTED_STRING = re.compile(r"'[^']*'|\"[^\"]*\"")
FLAT_SLEEP_MAX_SECONDS = 10


def _flat_sleeps(command: str) -> list[int]:
    """The sleeps the command pads with: every long sleep outside a shell loop body. A poll loop's
    own sleep is the wait's signal and never padding, and a loop may share its line with the
    workload it polls, so the loop body is skipped by nesting depth rather than by line."""
    hits: list[int] = []
    depth = 0
    for match in FLAT_SLEEP_OR_LOOP.finditer(command):
        seconds, keyword = match.group(1), match.group(2)
        if keyword == "do":
            depth += 1
        elif keyword == "done":
            depth = max(depth - 1, 0)
        elif depth == 0:
            hits.append(int(seconds))
    return [seconds for seconds in hits if seconds > FLAT_SLEEP_MAX_SECONDS]


def _split_workload(command: str, marker: str) -> tuple[str, str]:
    """The command split into the workload text it invokes and the wait around it. The workload
    arrives as one quoted string carrying the marker, so only the sleeps inside that string are the
    workload's own; a call that merely names the marker, such as a grep for it, keeps every sleep in
    the wait and is judged for padding like any other call."""
    workload: list[str] = []
    wait: list[str] = []
    end = 0
    for match in QUOTED_STRING.finditer(command):
        if marker not in match.group():
            continue
        workload.append(match.group())
        wait.append(command[end : match.start()])
        end = match.end()
    wait.append(command[end:])
    return "".join(workload), "".join(wait)


def waiting_scorer(marker: str, workload_sleeps: int) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        ran_workload = False
        for call in output.calls:
            if call.name != "bash":
                continue
            command = str(call.input.get("command", ""))
            workload, wait = _split_workload(command, marker)
            padded = _flat_sleeps(wait)
            if padded:
                return CapabilityVerdict(False, f"padded the wait with sleep {max(padded)}")
            own = _flat_sleeps(workload)
            if len(own) > workload_sleeps:
                return CapabilityVerdict(False, f"padded the workload call with sleep {max(own)}")
            if marker in command and len(own) == workload_sleeps:
                ran_workload = True
        if not ran_workload:
            return CapabilityVerdict(False, "never ran the workload command")
        if marker not in output.response:
            return CapabilityVerdict(False, "the answer does not carry the workload's marker")
        return CapabilityVerdict(True, "workload ran and completion was detected without padding")

    return DescribedGrader(
        "the workload runs, its marker reaches the answer, and no bash call pads the wait with a "
        "flat sleep beyond the workload's own",
        grade,
    )


CASES = (
    CapabilityCase(
        "bash-wait-foreground",
        "Run this build command in the sandbox and reply with the exact marker line it prints: "
        "sh -c 'sleep 40; echo BUILD-OK-7391'. It takes about 40 seconds.",
        waiting_scorer("BUILD-OK-7391", workload_sleeps=1),
        digest_tag="waiting:bash-wait-foreground",
    ),
    CapabilityCase(
        "bash-wait-background",
        "Start this command in the sandbox: sh -c 'sleep 90; echo DONE-4213 > /workspace/out.txt'."
        " It takes about 90 seconds. Reply with the file's content once it exists.",
        waiting_scorer("DONE-4213", workload_sleeps=1),
        digest_tag="waiting:bash-wait-background",
    ),
    CapabilityCase(
        "bash-wait-restraint",
        "Run `echo ready-9182` in the sandbox and reply with its output.",
        waiting_scorer("ready-9182", workload_sleeps=0),
        digest_tag="waiting:bash-wait-restraint",
    ),
)
