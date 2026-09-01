from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.bash_waiting import (
    BACKGROUND_WORKLOAD,
    FOREGROUND_WORKLOAD,
    LONG_WORKLOAD,
    RELEASE_CTL,
    long_wait_scorer,
    release_poll_scorer,
    waiting_scorer,
)

FOREGROUND = waiting_scorer("BUILD-OK-7391", "time.sleep(40)", async_ok=True)
BACKGROUND = waiting_scorer("DONE-4213", "time.sleep(90)", async_ok=True)


def _output(*commands: str, response: str = "BUILD-OK-7391") -> CapabilityOutput:
    calls = tuple(ToolInvocation(name="bash", input={"command": command}) for command in commands)
    return CapabilityOutput(response=response, calls=calls)


async def test_foreground_workload_passes() -> None:
    assert (await FOREGROUND(_output(FOREGROUND_WORKLOAD))).passed


async def test_flat_sleep_after_backgrounding_fails() -> None:
    verdict = await FOREGROUND(
        _output(f"{FOREGROUND_WORKLOAD} > /tmp/b.log 2>&1 &", "sleep 45; cat /tmp/b.log")
    )
    assert not verdict.passed
    assert "sleep 45" in verdict.reason


async def test_refused_flat_sleep_after_backgrounding_does_not_fail() -> None:
    refused = ToolInvocation(
        name="bash",
        input={"command": "sleep 45; cat /tmp/b.log"},
        is_error=True,
        has_result=True,
    )
    poll = ToolInvocation(
        name="bash",
        input={
            "command": "timeout 90 bash -c 'until [ -s /tmp/b.log ]; do sleep 2; done; "
            "cat /tmp/b.log'"
        },
    )
    workload = ToolInvocation(
        name="bash",
        input={"command": FOREGROUND_WORKLOAD, "background": True},
    )
    verdict = await FOREGROUND(
        CapabilityOutput(response="BUILD-OK-7391", calls=(workload, refused, poll))
    )
    assert verdict.passed, verdict.reason


async def test_padding_beside_the_workload_in_one_call_fails() -> None:
    verdict = await FOREGROUND(_output(f"{FOREGROUND_WORKLOAD} & sleep 45; cat /tmp/b.log"))
    assert not verdict.passed


async def test_start_and_poll_in_one_call_passes() -> None:
    verdict = await BACKGROUND(
        _output(
            f"{BACKGROUND_WORKLOAD} & "
            "until [ -f /workspace/out.txt ]; do sleep 5; done; cat /workspace/out.txt",
            response="DONE-4213",
        )
    )
    assert verdict.passed


async def test_multiline_poll_loop_in_its_own_call_passes() -> None:
    verdict = await BACKGROUND(
        _output(
            f"{BACKGROUND_WORKLOAD} &",
            "until [ -f /workspace/out.txt ]; do\n  sleep 15\ndone\ncat /workspace/out.txt",
            response="DONE-4213",
        )
    )
    assert verdict.passed


async def test_flat_sleep_after_a_poll_loop_still_fails() -> None:
    verdict = await FOREGROUND(
        _output(
            f"{FOREGROUND_WORKLOAD} > /tmp/b.log 2>&1 &",
            "until [ -f /tmp/b.log ]; do sleep 2; done\nsleep 45\ncat /tmp/b.log",
        )
    )
    assert not verdict.passed
    assert "sleep 45" in verdict.reason


async def test_flat_sleep_before_reading_the_marker_fails() -> None:
    started = f"{FOREGROUND_WORKLOAD} > /tmp/b.log 2>&1 &"
    verdict = await FOREGROUND(_output(started, "sleep 60; grep BUILD-OK-7391 /tmp/b.log"))
    assert not verdict.passed
    assert "sleep 60" in verdict.reason


async def test_reading_the_marker_after_a_poll_loop_passes() -> None:
    verdict = await FOREGROUND(
        _output(
            f"{FOREGROUND_WORKLOAD} > /tmp/b.log 2>&1 &",
            "until [ -s /tmp/b.log ]; do sleep 2; done; grep BUILD-OK-7391 /tmp/b.log",
        )
    )
    assert verdict.passed


async def test_echoing_the_marker_is_not_the_workload() -> None:
    verdict = await FOREGROUND(_output("echo BUILD-OK-7391"))
    assert not verdict.passed
    assert "never ran" in verdict.reason


async def test_marker_missing_from_the_answer_fails() -> None:
    verdict = await FOREGROUND(_output(FOREGROUND_WORKLOAD, response="the build finished"))
    assert not verdict.passed


async def test_detached_workload_passes_without_the_marker() -> None:
    call = ToolInvocation(
        "bash", {"command": BACKGROUND_WORKLOAD, "background": True}, "detached", True
    )
    verdict = await BACKGROUND(
        CapabilityOutput(response="I will reply once the file exists.", calls=(call,))
    )
    assert verdict.passed
    assert "detached" in verdict.reason


async def test_a_foreground_workload_still_owes_the_marker() -> None:
    verdict = await BACKGROUND(
        _output(BACKGROUND_WORKLOAD, response="started it in the foreground")
    )
    assert not verdict.passed


async def test_restraint_never_accepts_the_detached_excuse() -> None:
    call = ToolInvocation("bash", {"command": "echo ready-9182", "background": True}, "ok", True)
    scorer = waiting_scorer("ready-9182", "echo ready-9182")
    verdict = await scorer(CapabilityOutput(response="running it in the background", calls=(call,)))
    assert not verdict.passed


async def test_restraint_rejects_any_pad() -> None:
    scorer = waiting_scorer("ready-9182", "echo ready-9182")
    assert (await scorer(_output("echo ready-9182", response="ready-9182"))).passed
    assert not (await scorer(_output("sleep 30; echo ready-9182", response="ready-9182"))).passed


LONG = long_wait_scorer()


def _long_output(*calls: dict[str, object], response: str) -> CapabilityOutput:
    return CapabilityOutput(
        response=response,
        calls=tuple(ToolInvocation(name="bash", input=call) for call in calls),
    )


async def test_one_covering_wait_passes() -> None:
    verdict = await LONG(
        _long_output(
            {"command": LONG_WORKLOAD, "timeout": 360_000},
            {"command": "cat /workspace/release.txt", "timeout": None},
            response="SHIPPED-5507",
        )
    )
    assert verdict.passed
    assert verdict.evidence["foreground_waits"] == 2


async def test_repeated_short_waits_fail() -> None:
    """The incident: every wait unpadded, the budget paid over and over for one wait."""
    poll = {"command": "timeout 115 sh -c 'until [ -f /workspace/release.txt ]; do :; done'"}
    verdict = await LONG(
        _long_output({"command": LONG_WORKLOAD}, poll, poll, poll, response="SHIPPED-5507")
    )
    assert not verdict.passed
    assert "4 times" in verdict.reason
    assert verdict.evidence["foreground_waits"] == 4


async def test_detached_workload_polled_once_passes() -> None:
    verdict = await LONG(
        _long_output(
            {"command": LONG_WORKLOAD, "background": True},
            {"command": "cat /workspace/release.txt", "timeout": 400_000},
            response="SHIPPED-5507",
        )
    )
    assert verdict.passed
    assert verdict.evidence["foreground_waits"] == 1


async def test_erroring_foreground_waits_are_counted() -> None:
    """A poll that finds the file not there yet times out or exits nonzero, so the harness marks
    the hop is_error. Those hops are the budget being paid over and over — they must be counted,
    or the exact over-poll shape the case grades reads as a pass."""
    hop = ToolInvocation(
        name="bash",
        input={"command": "timeout 115 sh -c 'until [ -f /workspace/release.txt ]; do :; done'"},
        is_error=True,
        has_result=True,
    )
    verdict = await LONG(
        CapabilityOutput(
            response="SHIPPED-5507",
            calls=(
                ToolInvocation(name="bash", input={"command": LONG_WORKLOAD, "background": True}),
                hop,
                hop,
                hop,
            ),
        )
    )
    assert not verdict.passed, verdict.reason
    assert verdict.evidence["foreground_waits"] == 3


async def test_erroring_foreground_polls_are_counted() -> None:
    """Same for the release poll: a status hop that finds it in progress exits nonzero, and those
    errored hops are the over-poll the case fails."""
    hop = ToolInvocation(
        name="bash",
        input={"command": f"{POLL} | grep -q completed"},
        is_error=True,
        has_result=True,
    )
    verdict = await REMOTE(
        CapabilityOutput(
            response="SHIPPED-5507",
            calls=(
                ToolInvocation(
                    name="bash", input={"command": f"sh /workspace/{RELEASE_CTL} start"}
                ),
                hop,
                hop,
                hop,
                hop,
                hop,
            ),
        )
    )
    assert not verdict.passed, verdict.reason
    assert verdict.evidence["status_polls"] == 5


async def test_foreground_hops_on_the_tasks_exit_file_are_counted() -> None:
    """The detached start hands back the task's exit file as the completion signal, so the hops that
    wait for one wait name that file and neither the workload nor the release file. They are the
    same repeated short wait, and they are charged as such."""
    hop = {
        "command": 'timeout 115 sh -c \'until [ -e "$UFO_HOME/runs/1f9/tasks/bash-1.exit" ]; '
        "do sleep 5; done'"
    }
    verdict = await LONG(
        _long_output(
            {"command": LONG_WORKLOAD, "background": True},
            hop,
            hop,
            hop,
            hop,
            {"command": "cat /workspace/release.txt"},
            response="SHIPPED-5507",
        )
    )
    assert not verdict.passed, verdict.reason
    assert verdict.evidence["foreground_waits"] == 5


async def test_never_started_the_job_fails() -> None:
    verdict = await LONG(_long_output({"command": "ls /workspace"}, response="SHIPPED-5507"))
    assert not verdict.passed
    assert "never started" in verdict.reason


REMOTE = release_poll_scorer()
POLL = f"sh /workspace/{RELEASE_CTL} status"


async def test_one_covering_poll_passes() -> None:
    verdict = await REMOTE(
        _long_output(
            {"command": f"sh /workspace/{RELEASE_CTL} start"},
            {
                "command": f"until {POLL} | grep -q completed; do sleep 10; done; {POLL}",
                "timeout": 600_000,
            },
            response="SHIPPED-5507",
        )
    )
    assert verdict.passed
    assert verdict.evidence["status_polls"] == 1


async def test_repeated_short_polls_fail() -> None:
    """The incident: many unpadded foreground hops at one wait."""
    hop = {"command": f"timeout 115 sh -c 'until {POLL} | grep -q completed; do sleep 5; done'"}
    verdict = await REMOTE(
        _long_output(
            {"command": f"sh /workspace/{RELEASE_CTL} start"},
            hop,
            hop,
            hop,
            hop,
            hop,
            response="SHIPPED-5507",
        )
    )
    assert not verdict.passed
    assert "5 times" in verdict.reason


async def test_never_polled_fails() -> None:
    verdict = await REMOTE(
        _long_output({"command": f"sh /workspace/{RELEASE_CTL} start"}, response="SHIPPED-5507")
    )
    assert not verdict.passed
    assert "never polled" in verdict.reason


async def test_polled_but_no_marker_fails() -> None:
    verdict = await REMOTE(_long_output({"command": POLL}, response="it is still going"))
    assert not verdict.passed
    assert "does not carry" in verdict.reason


async def test_refused_flat_sleep_is_not_charged_to_the_model() -> None:
    """The guard already refused it, so the command never ran; only what ran is graded."""
    refused = ToolInvocation(
        name="bash", input={"command": "sleep 240; " + POLL}, is_error=True, has_result=True
    )
    ran = ToolInvocation(
        name="bash",
        input={
            "command": f"until {POLL} | grep -q completed; do sleep 10; done",
            "timeout": 600_000,
        },
    )
    verdict = await REMOTE(CapabilityOutput(response="SHIPPED-5507", calls=(refused, ran)))
    assert verdict.passed, verdict.reason
    assert verdict.evidence["status_polls"] == 1


async def test_long_wait_does_not_charge_a_refused_flat_sleep() -> None:
    """The guard refuses a foreground flat sleep before it runs; the model then covers the workload
    and answers. The refused call padded nothing — the sandbox never ran it — so it must not fail
    the case."""
    refused = ToolInvocation(
        name="bash",
        input={"command": "sleep 300; cat /workspace/release.txt"},
        is_error=True,
        has_result=True,
    )
    covered = ToolInvocation(name="bash", input={"command": LONG_WORKLOAD, "timeout": 360_000})
    read = ToolInvocation(name="bash", input={"command": "cat /workspace/release.txt"})
    verdict = await LONG(CapabilityOutput(response="SHIPPED-5507", calls=(refused, covered, read)))
    assert verdict.passed, verdict.reason


async def test_a_detached_poll_loop_is_not_scored_as_never_polling() -> None:
    """Detaching the poll loop with background: true is one of the remedies the flat-sleep refusal
    names. It polls the release and delivers the marker, so counting zero foreground polls must not
    read as never polling."""
    verdict = await REMOTE(
        _long_output(
            {"command": f"sh /workspace/{RELEASE_CTL} start"},
            {
                "command": f"until {POLL} | grep -q completed; do sleep 20; done; {POLL}",
                "background": True,
            },
            response="SHIPPED-5507",
        )
    )
    assert verdict.passed, verdict.reason
    assert verdict.evidence["status_polls"] == 0


async def test_foreground_hops_on_a_detached_poll_loop_are_counted() -> None:
    """Detaching the poll loop and then blocking on that task's own files pays the foreground budget
    for every hop, so the turn re-blocks on one wait however its commands read."""
    hop = {
        "command": 'timeout 115 sh -c \'until [ -e "$UFO_HOME/runs/1f9/tasks/bash-2.exit" ]; '
        "do sleep 5; done'"
    }
    verdict = await REMOTE(
        _long_output(
            {"command": f"sh /workspace/{RELEASE_CTL} start"},
            {
                "command": f"until {POLL} | grep -q completed; do sleep 20; done; {POLL}",
                "background": True,
            },
            hop,
            hop,
            hop,
            hop,
            hop,
            hop,
            {"command": "cat $UFO_HOME/runs/1f9/tasks/bash-2.log"},
            response="SHIPPED-5507",
        )
    )
    assert not verdict.passed, verdict.reason
    assert verdict.evidence["status_polls"] == 7
