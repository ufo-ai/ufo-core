import pytest
from ufo_ext_coding.review_checkout import CodeReviewFinding, CodeReviewOutput

from evals.code_review import CASES
from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import TASKS, selected_run_tasks
from ufo.untrusted import wall


def _finding(path: str, line: int, title: str, trigger: str, failure: str) -> CodeReviewFinding:
    return CodeReviewFinding(
        path=path,
        line=line,
        title=title,
        trigger=trigger,
        failure=failure,
        impact="a supported operation fails or cannot complete for valid input",
    )


def _output(*findings: CodeReviewFinding) -> CapabilityOutput:
    return CapabilityOutput(
        "Done.",
        (
            ToolInvocation(
                name="spawn_subagent",
                input={"profile": "code_review"},
                result=wall(
                    "spawn_subagent", CodeReviewOutput(findings=findings).model_dump_json()
                ),
                has_result=True,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_code_review_eval_requires_both_independent_defects() -> None:
    grader = CASES[0].grader
    markdown = _finding(
        "client/src/ui/markdown.rs",
        365,
        "A hard break in a link panics",
        "Markdown link text contains a hard break.",
        "HardBreak flushes the spans, and close_link panics.",
    )
    history = _finding(
        "extensions/ufo/ufo_ext_ufo/surface.py",
        136,
        "History shows internal text as member text",
        "Resume calls history_directives after compaction.",
        "member_message_text emits internal compaction text as a you line.",
    )

    complete = await grader(_output(markdown, history))
    incomplete = await grader(_output(markdown))

    assert complete.passed
    assert not incomplete.passed
    assert "history replay" in incomplete.reason


def test_code_review_eval_is_an_opt_in_live_comparison() -> None:
    task = next(task for task in TASKS if task.name == "code_review")

    assert task.name not in {default.name for default in selected_run_tasks()}
    assert task.cases == ("code-review-all-severe-defects",)
    assert CASES[0].web_dependent
    assert "a41aece5c9fd2edc18a6871959cb60c4e73ebe44" in CASES[0].message
