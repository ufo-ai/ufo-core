"""The handle probe: the reply hands over the link the member acts on, and invents none."""

from evals.harness.capability import CapabilityOutput
from evals.registry import DEFAULT_TASKS
from evals.suites.proactive_handoff import (
    CASES,
    DESIGN_REVIEW_LINK,
    HIRING_LINK,
    ROADMAP_LINK,
    handle_scorer,
)

BY_NAME = {case.name: case for case in CASES}
SEEDED = (DESIGN_REVIEW_LINK, ROADMAP_LINK, HIRING_LINK)


def _output(response: str) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=())


def test_the_handle_suite_runs_by_default() -> None:
    assert "proactive_handoff" in {task.name for task in DEFAULT_TASKS}


async def test_the_meeting_answer_needs_the_join_link() -> None:
    case = BY_NAME["meeting-answer-carries-the-join-link"]

    handed = await case.grader(
        _output(f"The design review is at 15:00 on Wednesday. Join it here: {DESIGN_REVIEW_LINK}")
    )
    withheld = await case.grader(_output("The design review is at 15:00 on Wednesday."))

    assert handed.passed, handed.reason
    assert not withheld.passed
    assert "missing the handle" in withheld.reason


async def test_an_invented_link_fails_even_beside_the_real_one() -> None:
    grader = handle_scorer(required=(DESIGN_REVIEW_LINK,), seeded=SEEDED, max_words=60)

    verdict = await grader(
        _output(
            f"The design review is at 15:00 on Wednesday. Join: {DESIGN_REVIEW_LINK}. "
            "The notes are at https://docs.example.com/design-review."
        )
    )

    assert not verdict.passed
    assert "links the seeded material never carried" in verdict.reason


async def test_one_question_carries_one_handle() -> None:
    case = BY_NAME["one-handle-not-every-handle"]

    single = await case.grader(_output(f"The hiring sync is at 17:00 on Wednesday. {HIRING_LINK}"))
    dumped = await case.grader(
        _output(
            f"The hiring sync is at 17:00 on Wednesday. {HIRING_LINK}. The design review is at "
            f"15:00: {DESIGN_REVIEW_LINK}. Roadmap planning is on Tuesday: {ROADMAP_LINK}."
        )
    )

    assert single.passed, single.reason
    assert not dumped.passed
    assert "pastes handles the question did not ask about" in dumped.reason


async def test_material_without_a_handle_earns_a_reply_without_one() -> None:
    case = BY_NAME["no-handle-in-the-material-invents-none"]

    plain = await case.grader(
        _output(
            "The design review is at 15:00 on Wednesday in room 4A. The calendar lists no "
            "join link."
        )
    )
    guessed = await case.grader(
        _output("The design review is at 15:00 on Wednesday. Join: https://meet.example.com/design")
    )

    assert plain.passed, plain.reason
    assert not guessed.passed


async def test_a_long_reply_fails_its_word_budget() -> None:
    grader = handle_scorer(required=(HIRING_LINK,), seeded=SEEDED, max_words=10)

    verdict = await grader(_output(f"{HIRING_LINK} " + "word " * 20))

    assert not verdict.passed
    assert "over the 10 budget" in verdict.reason


def test_every_case_names_the_material_it_stages() -> None:
    """The suite grades what the reply carries once the turn has read the material, so a question
    that leaves the agent to find the staged file measures discovery instead. Unnamed, the agent
    searched memory and the connector catalogue and answered "no calendar account is connected":
    the suite scored 0/8 on the 2026-09-10 to 2026-09-12 sweeps."""
    for case in CASES:
        staged = tuple(item.path for item in case.workspace_files)
        assert staged, case.name
        assert any(path in case.message for path in staged), case.name
