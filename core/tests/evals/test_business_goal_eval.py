import asyncio
import re
from pathlib import Path

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.business_goal import ASK_TOOL, GOALS, _goal_thread_scorer

FIRST_RUN_VIEW = (
    Path(__file__).resolve().parents[3] / "extensions/web/frontend/src/views/FirstRun.tsx"
)


def _call(name: str, input: dict[str, object]) -> ToolInvocation:
    return ToolInvocation(name=name, input=input, result="ok", has_result=True)


def _output(
    *,
    worked: bool = True,
    questions: list[dict[str, object]] | None = None,
    applied: bool = False,
    asked_first: bool = False,
) -> CapabilityOutput:
    ask = (_call(ASK_TOOL, {"questions": questions}),) if questions is not None else ()
    work = (_call("web_search", {"query": "pricing"}),) if worked else ()
    calls = (
        *(ask if asked_first else ()),
        *work,
        *((_call("object_apply", {"kind": "scheduled_task"}),) if applied else ()),
        *(() if asked_first else ask),
    )
    return CapabilityOutput("Found the baseline.", calls)


def test_working_then_asking_one_bare_question_passes() -> None:
    verdict = asyncio.run(
        _goal_thread_scorer()(_output(questions=[{"question": "What is the revenue target?"}]))
    )

    assert verdict.passed


def test_a_thread_that_did_nothing_fails() -> None:
    verdict = asyncio.run(_goal_thread_scorer()(_output(worked=False)))

    assert not verdict.passed
    assert verdict.reason == "took no first step"


def test_asking_before_working_fails() -> None:
    verdict = asyncio.run(
        _goal_thread_scorer()(_output(questions=[{"question": "The target?"}], asked_first=True))
    )

    assert not verdict.passed
    assert verdict.reason == "asked before taking the first step"


def test_two_questions_fail() -> None:
    verdict = asyncio.run(
        _goal_thread_scorer()(
            _output(questions=[{"question": "The target?"}, {"question": "The window?"}])
        )
    )

    assert not verdict.passed
    assert verdict.reason == "asked 2 questions, expected at most one"


def test_a_question_carrying_options_is_a_menu() -> None:
    verdict = asyncio.run(
        _goal_thread_scorer()(
            _output(questions=[{"question": "Which stage?", "options": ["Leads", "Conversion"]}])
        )
    )

    assert not verdict.passed
    assert verdict.reason == "offered the member a menu"


def test_applying_an_object_on_an_unread_thread_fails() -> None:
    verdict = asyncio.run(_goal_thread_scorer()(_output(applied=True)))

    assert not verdict.passed
    assert verdict.reason == "applied an object on a thread nobody is reading"


def test_every_case_says_the_words_the_first_run_actually_sends() -> None:
    """The suite and the run hold the same sentences, and only the run sends them. A case measuring
    words no thread opens on measures nothing, and an ablation arm editing the run would then move
    no verdict. There is one run and one copy of these words — both shells draw this component —
    so this holds the suite against it and nothing else needs holding."""
    joined = re.sub(r'"\s*\+\s*"', "", FIRST_RUN_VIEW.read_text())
    for goal in GOALS:
        assert goal.said in joined, goal.name
        assert goal.asks in joined, goal.name
