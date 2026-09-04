import pytest

from evals.harness.capability import CapabilityCase, CapabilityOutput, ToolInvocation
from evals.registry import TASKS
from evals.suites.repeated_input_coherence import (
    CASES,
    CURRENT_COMMAND,
    CURRENT_STATUS_FILE,
    FAILED_CORRECTION_MESSAGES_PER_TURN,
    FAILED_CORRECTION_TURNS,
    HISTORY_CASES,
    KNOWN_BAD_COMMAND,
    OLD_MESSAGES_PER_TURN,
    OLD_TASK_TURNS,
    OLD_TOOLS_PER_TURN,
    REPEATED_MESSAGES_PER_TURN,
    REPEATED_TOOLS_PER_TURN,
    REPEATED_TURNS,
)
from ufo.sdk.models import Message, ToolUseBlock


def test_repeated_input_cases_match_the_failed_action_trajectory_shape() -> None:
    task = next(task for task in TASKS if task.name == "repeated_input_coherence")

    assert not task.nightly
    assert len(CASES) == 3
    assert [case.repeated_reply for case in HISTORY_CASES] == [
        "No new pull requests to review.",
        "No new emails.",
        "Check clean.",
    ]
    for spec, case in zip(HISTORY_CASES, CASES, strict=True):
        history = case.prior_transcript
        tool_calls = tuple(
            block
            for message in history
            if not isinstance(message.content, str)
            for block in message.content
            if isinstance(block, ToolUseBlock)
        )
        assert not case.prior_messages
        assert history[0].role == "user"
        assert history[-1] == Message(
            role="assistant",
            content=(
                "The command omitted the required directory change. I will correct it on the "
                "next run."
            ),
        )
        assert len(history) == (
            OLD_TASK_TURNS * OLD_MESSAGES_PER_TURN
            + 2
            + REPEATED_TURNS * REPEATED_MESSAGES_PER_TURN
            + FAILED_CORRECTION_TURNS * FAILED_CORRECTION_MESSAGES_PER_TURN
        )
        assert len(tool_calls) == (
            OLD_TASK_TURNS * OLD_TOOLS_PER_TURN
            + REPEATED_TURNS * REPEATED_TOOLS_PER_TURN
            + FAILED_CORRECTION_TURNS
        )
        assert sum(message.content == spec.repeated_reply for message in history) == REPEATED_TURNS
        assert (
            sum(
                isinstance(message.content, str) and "<scheduled_task>" in message.content
                for message in history
            )
            == REPEATED_TURNS + FAILED_CORRECTION_TURNS
        )
        assert sum(call.input.get("command") == KNOWN_BAD_COMMAND for call in tool_calls) == (
            FAILED_CORRECTION_TURNS
        )
        assert CURRENT_COMMAND in case.message
        assert len(case.workspace_files) == 4
        assert case.workspace_files[-1].path == CURRENT_STATUS_FILE
        assert spec.current_reply.encode() in case.workspace_files[-1].content
        assert all(
            spec.repeated_reply.encode() in item.content for item in case.workspace_files[:-1]
        )
        assert case.samples == 1
        assert case.followup is None


def test_structured_history_cannot_mix_with_the_text_history_forms() -> None:
    history = (Message(role="user", content="old"), Message(role="assistant", content="done"))

    with pytest.raises(ValueError, match="cannot combine"):
        CapabilityCase(
            name="mixed",
            message="new",
            grader=CASES[0].grader,
            prior_messages=("old", "done"),
            prior_transcript=history,
        )
    with pytest.raises(ValueError, match="must start with user text and end with an assistant"):
        CapabilityCase(
            name="bad-edges",
            message="new",
            grader=CASES[0].grader,
            prior_transcript=(Message(role="assistant", content="done"),),
        )
    assert "priorTranscript" in CASES[0].payload()


async def test_repeated_input_grader_rejects_drift_bad_action_and_missing_result() -> None:
    case = CASES[1]
    read = ToolInvocation(
        "bash",
        {"command": CURRENT_COMMAND},
        HISTORY_CASES[1].current_reply,
        has_result=True,
    )
    bad = ToolInvocation(
        "bash",
        {"command": KNOWN_BAD_COMMAND},
        "cat: current.json: No such file or directory",
        has_result=True,
        is_error=True,
    )
    drift = await case.grader(
        CapabilityOutput("Project Cedar draft section 32.", (), own_calls=(read,))
    )
    tool = await case.grader(
        CapabilityOutput(
            HISTORY_CASES[1].current_reply,
            (),
            own_calls=(ToolInvocation("web", {"query": "check"}, "result", True), read),
        )
    )
    repeated = await case.grader(
        CapabilityOutput(
            "1 new email from Ada.\n\n1 new email from Ada.",
            (),
            own_calls=(read,),
        )
    )
    trapped = await case.grader(
        CapabilityOutput(
            HISTORY_CASES[1].current_reply,
            (),
            own_calls=(bad, read),
        )
    )
    missing = await case.grader(CapabilityOutput(HISTORY_CASES[1].current_reply, (), own_calls=()))

    assert not drift.passed
    assert "unrelated old task" in drift.reason
    assert not tool.passed
    assert "unexpected tool" in tool.reason
    assert not repeated.passed
    assert "repeated an output line" in repeated.reason
    assert not trapped.passed
    assert "known-bad command" in trapped.reason
    assert not missing.passed
    assert "did not read" in missing.reason


async def test_repeated_input_grader_accepts_each_current_result() -> None:
    replies = (
        "One new pull request from Ada.",
        "1 new email from Ada.",
        "The check failed because there is one changed file.",
    )
    for spec, case, reply in zip(HISTORY_CASES, CASES, replies, strict=True):
        read = ToolInvocation(
            "bash",
            {"command": CURRENT_COMMAND},
            spec.current_reply,
            has_result=True,
        )
        verdict = await case.grader(CapabilityOutput(reply, (read,), own_calls=(read,)))

        assert verdict.passed
        assert verdict.evidence["repeatedTurns"] == REPEATED_TURNS
        assert verdict.evidence["failedCorrectionTurns"] == FAILED_CORRECTION_TURNS
        assert verdict.evidence["knownBadFirstAction"] is False
        assert verdict.evidence["newToolCalls"] == 1


async def test_repeated_input_grader_accepts_a_long_coherent_reply() -> None:
    spec = HISTORY_CASES[1]
    read = ToolInvocation(
        "bash",
        {"command": CURRENT_COMMAND},
        spec.current_reply,
        has_result=True,
    )
    detail = " ".join(f"detail-{index}" for index in range(60))

    verdict = await CASES[1].grader(
        CapabilityOutput(f"{spec.current_reply} {detail}", (), own_calls=(read,))
    )

    assert verdict.passed
    assert verdict.evidence["wordCount"] > 40
