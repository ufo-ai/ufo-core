from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import TASKS
from evals.suites.slack_ladder_coherence import (
    CASES,
    CURRENT_ACTION,
    HISTORY_MESSAGES,
    HISTORY_TOOL_CALLS,
    LADDER_CASES,
    OLD_ACTION,
    SCHEDULED_TURNS,
)
from ufo.sdk.models import ToolUseBlock


def test_slack_ladder_cases_hold_the_live_failure_shape() -> None:
    task = next(task for task in TASKS if task.name == "slack_ladder_coherence")

    assert not task.nightly
    assert len(CASES) == 3
    for spec, case in zip(LADDER_CASES, CASES, strict=True):
        history = case.prior_transcript
        calls = tuple(
            block
            for message in history
            if not isinstance(message.content, str)
            for block in message.content
            if isinstance(block, ToolUseBlock)
        )
        user_text = "\n".join(
            message.content
            for message in history
            if message.role == "user" and isinstance(message.content, str)
        )
        all_input = user_text + "\n" + case.message

        assert len(history) == HISTORY_MESSAGES
        assert len(calls) == HISTORY_TOOL_CALLS
        assert sum(call.input.get("command") == OLD_ACTION for call in calls) == (
            SCHEDULED_TURNS + 1
        )
        assert history[-1].role == "assistant"
        assert CURRENT_ACTION in case.message
        assert ("<member_message_" in all_input) is spec.envelopes
        assert ("<channel_context_" in all_input) is spec.channel_context
        assert sum("<scheduled_task>" in line for line in user_text.splitlines()) == SCHEDULED_TURNS


async def test_slack_ladder_grader_accepts_the_new_action() -> None:
    current = ToolInvocation(
        "bash",
        {"command": CURRENT_ACTION},
        "",
        has_result=True,
    )

    for case in CASES:
        verdict = await case.grader(
            CapabilityOutput(
                "Deleted the synthetic hourly task.",
                (current,),
                own_calls=(current,),
            )
        )

        assert verdict.passed
        assert verdict.evidence["staleFirstAction"] is False


async def test_slack_ladder_grader_rejects_stale_action_and_language_drift() -> None:
    stale = ToolInvocation(
        "bash",
        {"command": OLD_ACTION},
        '{"row":"synthetic-row-085"}',
        has_result=True,
    )
    current = ToolInvocation(
        "bash",
        {"command": CURRENT_ACTION},
        "",
        has_result=True,
    )

    stale_verdict = await CASES[0].grader(
        CapabilityOutput(
            "Deleted the synthetic hourly task.",
            (stale, current),
            own_calls=(stale, current),
        )
    )
    drift_verdict = await CASES[0].grader(
        CapabilityOutput("任务已删除。", (current,), own_calls=(current,))
    )

    assert not stale_verdict.passed
    assert "stale scheduled action" in stale_verdict.reason
    assert not drift_verdict.passed
    assert "CJK" in drift_verdict.reason
