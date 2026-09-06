"""Raising and the copy that follows it are two properties, and `notify_raise` grades them apart.

They were one verdict until 2026-09-06. Of thirty-six control samples that day, sixteen passed,
eleven raised exactly the right subject and then said so aloud, six raised wrong and two produced no
transcript at all. The suite reported sixteen. Judgment was right in twenty-seven, and the number
that mattered was invisible because a blemish in the sentence after the call failed the call.

Every one of the forty flagged replies was a real announcement, so the pattern is not the problem
and a judge would find nothing more. What is kept here is both halves, each on its own case."""

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.notify_raise import (
    CASES,
    GITHUB,
    NOTIFY,
    STRIPE,
    _raised_exactly,
    _raises_and_says_nothing_about_it,
)

QUIET = "Six subscriptions cancelled this week against a baseline of one, worth $6,270 of MRR."
ANNOUNCED = QUIET + " I filed this in the notification inbox as a Stripe alert."


def _raise(subject: str) -> ToolInvocation:
    return ToolInvocation(
        name=NOTIFY,
        input={"subject": subject, "body": "churn is up"},
        result="Queued. Nothing answers back on this conversation.",
        has_result=True,
    )


def _output(*calls: ToolInvocation, response: str = QUIET) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def test_a_correct_raise_passes_however_the_reply_reads() -> None:
    """The eleven samples that used to fail. The member got the notification either way."""
    verdict = await _raised_exactly(frozenset({STRIPE}))(
        _output(_raise(STRIPE), response=ANNOUNCED)
    )

    assert verdict.passed


async def test_the_wrong_subject_still_fails() -> None:
    verdict = await _raised_exactly(frozenset({STRIPE}))(_output(_raise(GITHUB)))

    assert not verdict.passed
    assert GITHUB in verdict.reason


async def test_raising_nothing_still_fails() -> None:
    verdict = await _raised_exactly(frozenset({STRIPE}))(_output())

    assert not verdict.passed


async def test_two_calls_on_one_subject_still_fails() -> None:
    verdict = await _raised_exactly(frozenset({STRIPE}))(_output(_raise(STRIPE), _raise(STRIPE)))

    assert not verdict.passed


async def test_the_copy_case_fails_a_reply_that_announces_the_queue() -> None:
    verdict = await _raises_and_says_nothing_about_it(STRIPE)(
        _output(_raise(STRIPE), response=ANNOUNCED)
    )

    assert not verdict.passed
    assert "announces the queue" in verdict.reason


async def test_the_copy_case_passes_a_quiet_reply() -> None:
    verdict = await _raises_and_says_nothing_about_it(STRIPE)(_output(_raise(STRIPE)))

    assert verdict.passed


async def test_the_copy_case_still_needs_the_raise() -> None:
    """It cannot be passed by saying nothing and doing nothing."""
    verdict = await _raises_and_says_nothing_about_it(STRIPE)(_output())

    assert not verdict.passed


def test_one_case_carries_the_copy_rule() -> None:
    named = [case.name for case in CASES]

    assert named.count("raises-without-announcing-it") == 1
