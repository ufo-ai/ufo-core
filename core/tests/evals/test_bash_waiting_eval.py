from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.bash_waiting import BACKGROUND_WORKLOAD, FOREGROUND_WORKLOAD, waiting_scorer

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
