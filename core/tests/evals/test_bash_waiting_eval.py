from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.bash_waiting import waiting_scorer

WORKLOAD = "sh -c 'sleep 40; echo BUILD-OK-7391'"


def _output(*commands: str, response: str = "BUILD-OK-7391") -> CapabilityOutput:
    calls = tuple(ToolInvocation(name="bash", input={"command": command}) for command in commands)
    return CapabilityOutput(response=response, calls=calls)


async def test_foreground_workload_passes() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(_output(WORKLOAD))
    assert verdict.passed


async def test_flat_sleep_after_backgrounding_fails() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(f"{WORKLOAD} > /tmp/b.log 2>&1 &", "sleep 45; cat /tmp/b.log")
    )
    assert not verdict.passed
    assert "sleep 45" in verdict.reason


async def test_padding_inside_the_workload_call_fails() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(f"{WORKLOAD} & sleep 45; cat /workspace/out.txt")
    )
    assert not verdict.passed


async def test_poll_loop_is_not_padding() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(
            f"{WORKLOAD} > /tmp/b.log 2>&1 &",
            "until grep -q BUILD /tmp/b.log; do sleep 2; done; cat /tmp/b.log",
        )
    )
    assert verdict.passed


async def test_start_and_poll_in_one_call_passes() -> None:
    verdict = await waiting_scorer("DONE-4213", workload_sleeps=1)(
        _output(
            "sh -c 'sleep 90; echo DONE-4213 > /workspace/out.txt' & "
            "until [ -f /workspace/out.txt ]; do sleep 5; done; cat /workspace/out.txt",
            response="DONE-4213",
        )
    )
    assert verdict.passed


async def test_multiline_poll_loop_in_the_workload_call_passes() -> None:
    verdict = await waiting_scorer("DONE-4213", workload_sleeps=1)(
        _output(
            "sh -c 'sleep 90; echo DONE-4213 > /workspace/out.txt' &\n"
            "until [ -f /workspace/out.txt ]; do\n"
            "  sleep 15\n"
            "done\n"
            "cat /workspace/out.txt",
            response="DONE-4213",
        )
    )
    assert verdict.passed


async def test_multiline_poll_loop_in_its_own_call_passes() -> None:
    verdict = await waiting_scorer("DONE-4213", workload_sleeps=1)(
        _output(
            "sh -c 'sleep 90; echo DONE-4213 > /workspace/out.txt' &",
            "until [ -f /workspace/out.txt ]; do\n  sleep 15\ndone\ncat /workspace/out.txt",
            response="DONE-4213",
        )
    )
    assert verdict.passed


async def test_flat_sleep_after_a_poll_loop_still_fails() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(
            f"{WORKLOAD} > /tmp/b.log 2>&1 &",
            "until [ -f /tmp/b.log ]; do sleep 2; done\nsleep 45\ncat /tmp/b.log",
        )
    )
    assert not verdict.passed
    assert "sleep 45" in verdict.reason


async def test_flat_sleep_before_reading_the_marker_fails() -> None:
    scorer = waiting_scorer("BUILD-OK-7391", workload_sleeps=1)
    started = f"{WORKLOAD} > /tmp/b.log 2>&1 &"
    verdict = await scorer(_output(started, "sleep 60; grep BUILD-OK-7391 /tmp/b.log"))
    assert not verdict.passed
    assert "sleep 60" in verdict.reason
    quoted = await scorer(_output(started, "sleep 60; grep 'BUILD-OK-7391' /tmp/b.log"))
    assert not quoted.passed
    assert "sleep 60" in quoted.reason


async def test_reading_the_marker_after_a_poll_loop_passes() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(
            f"{WORKLOAD} > /tmp/b.log 2>&1 &",
            "until [ -s /tmp/b.log ]; do sleep 2; done; grep BUILD-OK-7391 /tmp/b.log",
        )
    )
    assert verdict.passed


async def test_extra_sleep_inside_the_workload_string_fails() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output("sh -c 'sleep 40; sleep 300; echo BUILD-OK-7391'")
    )
    assert not verdict.passed
    assert "sleep 300" in verdict.reason


async def test_echoing_the_marker_is_not_the_workload() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output("echo BUILD-OK-7391")
    )
    assert not verdict.passed
    assert "never ran" in verdict.reason


async def test_marker_missing_from_the_answer_fails() -> None:
    verdict = await waiting_scorer("BUILD-OK-7391", workload_sleeps=1)(
        _output(WORKLOAD, response="the build finished")
    )
    assert not verdict.passed


async def test_zero_sleep_workload_rejects_any_pad() -> None:
    scorer = waiting_scorer("ready-9182", workload_sleeps=0)
    assert (await scorer(_output("echo ready-9182", response="ready-9182"))).passed
    assert not (await scorer(_output("sleep 30; echo ready-9182", response="ready-9182"))).passed
