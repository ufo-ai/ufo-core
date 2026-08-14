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


def _output(*findings: CodeReviewFinding, checked_out: bool = True) -> CapabilityOutput:
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
            ToolInvocation(
                name="checkout_code_review",
                input={"pull_number": 1539},
                result="prepared" if checked_out else "ValueError: comparison does not match",
                has_result=True,
                is_error=not checked_out,
            ),
        ),
    )


def _markdown() -> CodeReviewFinding:
    return _finding(
        "client/src/ui/markdown.rs",
        365,
        "A hard break in a link panics",
        "Markdown link text contains a hard break.",
        "HardBreak flushes the spans, and close_link panics.",
    )


def _history() -> CodeReviewFinding:
    return _finding(
        "extensions/ufo/ufo_ext_ufo/surface.py",
        136,
        "History shows internal text as member text",
        "Resume calls history_directives after compaction.",
        "member_message_text emits internal compaction text as a you line.",
    )


def _invented() -> CodeReviewFinding:
    return _finding(
        "extensions/web/frontend/src/components/ui/button.tsx",
        17,
        "The send variant drops the last bg-primary use",
        "CI runs the theme test.",
        "Tailwind emits no bg-primary rule, and the assertion fails.",
    )


@pytest.mark.asyncio
async def test_code_review_eval_requires_both_independent_defects() -> None:
    grader = CASES[0].grader

    complete = await grader(_output(_markdown(), _history()))
    incomplete = await grader(_output(_markdown()))

    assert complete.passed
    assert not incomplete.passed
    assert "history replay" in incomplete.reason


@pytest.mark.asyncio
async def test_code_review_eval_fails_a_finding_the_comparison_never_earned() -> None:
    grader = CASES[0].grader

    padded = await grader(_output(_markdown(), _history(), _invented()))
    only_invented = await grader(_output(_invented()))

    assert not padded.passed
    assert "button.tsx:17" in padded.reason
    assert not only_invented.passed
    assert "missing" in only_invented.reason
    assert "invented" in only_invented.reason


@pytest.mark.asyncio
async def test_code_review_eval_requires_silence_on_a_clean_comparison() -> None:
    grader = CASES[1].grader

    silent = await grader(_output())
    noisy = await grader(_output(_invented()))

    assert silent.passed
    assert not noisy.passed
    assert "button.tsx:17" in noisy.reason


@pytest.mark.asyncio
async def test_code_review_eval_refuses_silence_from_a_review_that_took_no_checkout() -> None:
    silent = await CASES[1].grader(_output(checked_out=False))
    established = await CASES[0].grader(_output(_markdown(), _history(), checked_out=False))

    assert not silent.passed
    assert "took no checkout" in silent.reason
    assert not established.passed
    assert "took no checkout" in established.reason


@pytest.mark.asyncio
async def test_code_review_eval_refuses_the_claim_this_comparison_disproved() -> None:
    grader = CASES[2].grader

    republished = await grader(_output(_invented()))
    other = await grader(
        _output(
            _finding(
                "extensions/web/frontend/src/kernel/table.tsx",
                88,
                "The fixed track drops the act column",
                "A table renders inside the record drawer.",
                "The act cell gets zero width and the row cannot be acted on.",
            )
        )
    )
    silent = await grader(_output())

    assert not republished.passed
    assert "republished the refuted claim" in republished.reason
    assert other.passed
    assert silent.passed


def test_code_review_eval_is_an_opt_in_live_comparison() -> None:
    task = next(task for task in TASKS if task.name == "code_review")

    assert task.name not in {default.name for default in selected_run_tasks()}
    assert task.cases == (
        "code-review-all-severe-defects",
        "code-review-clean-comparison",
        "code-review-refuted-claim",
    )
    assert all(case.web_dependent for case in CASES)
    assert "a41aece5c9fd2edc18a6871959cb60c4e73ebe44" in CASES[0].message
    assert "e883462be12b26079f34a7691bf89e27845c2105" in CASES[1].message
    assert "7de7fe8d4950f6f97f528955281e13ef6bf78037" in CASES[2].message
