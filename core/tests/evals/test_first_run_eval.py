import asyncio

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.first_run import ASK_TOOL, GOALS, _goal_plan_scorer


def _call(name: str, input: dict[str, object]) -> ToolInvocation:
    return ToolInvocation(name=name, input=input, result="ok", has_result=True)


def _output(reads: tuple[str, ...]) -> CapabilityOutput:
    calls = (
        _call("load_skill", {"name": "first-run"}),
        _call("memory_search", {"query": "workspace"}),
        _call("object_list", {"kind": "agent"}),
        *(_call("read", {"file_path": path}) for path in reads),
        _call(
            ASK_TOOL,
            {
                "questions": [
                    {"question": "Where is the delivery bottleneck?"},
                    {"question": "How do you measure cycle time?"},
                ]
            },
        ),
    )
    return CapabilityOutput("Plan.", calls)


def test_repeated_reads_of_the_selected_goal_reference_are_one_selection() -> None:
    goal = GOALS[0]
    output = _output(
        (
            f"$UFO_HOME/skills/first-run/references/{goal.reference}",
            f"/tmp/ufo/home/.ufo/skills/first-run/references/{goal.reference}",
        )
    )

    verdict = asyncio.run(_goal_plan_scorer(goal)(output))

    assert verdict.passed


def test_reading_a_second_goal_reference_still_fails() -> None:
    goal = GOALS[0]
    output = _output(
        tuple(f"$UFO_HOME/skills/first-run/references/{item.reference}" for item in GOALS[:2])
    )

    verdict = asyncio.run(_goal_plan_scorer(goal)(output))

    assert not verdict.passed
    assert verdict.reason == "did not read only the selected goal reference"
