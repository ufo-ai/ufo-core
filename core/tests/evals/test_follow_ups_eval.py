"""The countable half of the follow-ups suite, and the contract the ranking call is made under.

The judged half is the judge's; what is tested here is what no judge should ever be asked: whether
a slate has the shape the screen can draw, and whether a workspace without Slack can be handed a
`share` row at all.
"""

from dataclasses import dataclass
from typing import cast

import pytest
from ufo_ext_web.followups import (
    HOOK_CHARS,
    OFFERS_MAX,
    Offer,
    _reachable_schema,
    ranking_request,
)

from evals.harness.judge import JudgeLeg
from evals.registry import FOLLOW_UPS_JUDGE_MODEL, TASKS
from evals.suites.follow_ups import (
    CASES,
    GRADING,
    SAMPLES,
    SLATE,
    FollowUpCase,
    FollowUpSuite,
    _slate_fault,
    follow_ups_task,
)
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL
from ufo.sdk.context import ModelAccess

SPOKE = (("user", "Where did the Acme renewal land?"), ("assistant", "Net-45, at a 1% uplift."))

EVERY_KIND = frozenset({"ask", "keep", "share", "watch"})
NO_SLACK = frozenset({"ask", "keep", "watch"})


def _case(supports_rows: bool, kinds: frozenset[str] = EVERY_KIND) -> FollowUpCase:
    return FollowUpCase("acme", SPOKE, kinds=kinds, supports_rows=supports_rows)


def _row(kind: str = "ask", hook: str = "Draft the Acme reply.") -> Offer:
    return Offer(kind=kind, hook=hook, prompt=f"{hook} In full.")


def test_a_thread_that_earns_no_rows_fails_the_moment_it_is_given_any() -> None:
    assert _slate_fault(_case(supports_rows=False), ()) == ""
    assert "supports none" in _slate_fault(_case(supports_rows=False), (_row(),))


def test_a_thread_that_earns_rows_fails_when_it_is_given_none() -> None:
    assert "no rows" in _slate_fault(_case(supports_rows=True), ())
    assert _slate_fault(_case(supports_rows=True), (_row(),)) == ""


def test_a_kind_this_workspace_cannot_take_is_the_whole_verdict() -> None:
    drawn = (_row(), _row(kind="share", hook="Post the terms to #sales."))
    assert _slate_fault(_case(supports_rows=True), drawn) == ""
    assert "share" in _slate_fault(_case(supports_rows=True, kinds=NO_SLACK), drawn)


def test_two_rows_that_read_the_same_are_one_row_to_the_member() -> None:
    twice = (_row(), _row(kind="keep"))
    assert "read the same" in _slate_fault(_case(supports_rows=True), twice)


def test_a_slate_over_the_contract_is_refused_before_it_is_read() -> None:
    many = tuple(_row(hook=f"Draft reply {index}.") for index in range(OFFERS_MAX + 1))
    assert "over the" in _slate_fault(_case(supports_rows=True), many)


def test_the_call_offers_only_the_kinds_the_workspace_can_take() -> None:
    """Told in prose, the ranking spent rows on kinds the screen then dropped. The contract is what
    refuses them, so the schema the call carries names exactly the reach it was given."""
    kinds = _reachable_schema(NO_SLACK)["$defs"]["Offer"]["properties"]["kind"]["enum"]
    assert kinds == sorted(NO_SLACK)

    request = ranking_request(DEFAULT_BACKGROUND_JOBS_MODEL, SPOKE, NO_SLACK)
    offered = request.tools[0].input_schema["$defs"]["Offer"]["properties"]["kind"]["enum"]
    assert offered == sorted(NO_SLACK)
    assert request.tools[0].input_schema["$defs"]["Offer"]["properties"]["hook"]["maxLength"] == (
        HOOK_CHARS
    )


def test_a_hook_the_member_could_not_read_whole_never_reaches_a_slate() -> None:
    with pytest.raises(ValueError):
        _row(hook="x" * (HOOK_CHARS + 1))


def test_every_criterion_is_its_own_verdict_off_the_one_judge_call() -> None:
    """Folding six criteria into one verdict multiplied the judge's own flip rate — measured at one
    slate in six — into the score. Scored apart, the same call answers six verdicts, and a silent
    thread carries the countable one alone because there are no rows for a judge to read."""
    task = follow_ups_task(FOLLOW_UPS_JUDGE_MODEL)
    silent = tuple(case.name for case in CASES if not case.supports_rows)
    keys = tuple(key for key, _ in GRADING)

    assert len(silent) == 2
    for name in silent:
        assert f"{name}:{SLATE}:0" in task.cases
        assert not any(verdict.startswith(f"{name}:{keys[0]}") for verdict in task.cases)
    judged = len(CASES) - len(silent)
    assert len(task.cases) == SAMPLES * (len(CASES) + judged * len(keys))
    assert len(set(keys)) == len(keys)


def test_the_suite_ranks_on_the_shipped_model_and_grades_on_its_own_judge() -> None:
    task = next(task for task in TASKS if task.name == "follow_ups")
    assert task.simulator_model == DEFAULT_BACKGROUND_JOBS_MODEL
    assert task.judge_model == FOLLOW_UPS_JUDGE_MODEL


class RateLimitError(Exception):
    """The provider class by name: `is_transient_fault` matches the class name, never the type."""


def _suite() -> FollowUpSuite:
    return FollowUpSuite(cases=CASES, digest="d", samples=SAMPLES, selected=frozenset())


@dataclass
class _Raising:
    """A ranking leg that raises the way a provider does, reached through `_case` so the test covers
    the guard rather than the helper behind it."""

    error: Exception
    model: str = "gpt-5.6-luna"

    async def turn(self, request: object) -> object:
        raise self.error


async def test_a_provider_fault_costs_its_own_sample_and_not_the_report() -> None:
    """The raise used to leave `_case`, and `gather_cases` then re-raised it as a group: one rate
    limit on one of eighteen rankings ended the run holding none of the verdicts it had paid for."""
    case = next(case for case in CASES if case.supports_rows)

    faulted = await _suite()._case(
        case, 1, cast("ModelAccess", _Raising(RateLimitError("slow down"))), cast("JudgeLeg", None)
    )

    assert {result.name for result in faulted} == {
        f"{case.name}:{key}:1" for key in ("slate", *(key for key, _ in GRADING))
    }
    assert all(result.excluded and result.provider_fault for result in faulted)
    assert all("RateLimitError" in result.reason for result in faulted)


async def test_a_ranking_that_records_no_slate_is_scored_rather_than_excluded() -> None:
    """A provider's own uncertainty is not this: a reply carrying no `record_follow_ups` call is a
    ranking that failed, and a run that excused it would report a suite nobody was grading."""
    case = next(case for case in CASES if case.supports_rows)

    faulted = await _suite()._case(
        case,
        0,
        cast("ModelAccess", _Raising(ValueError("recorded no record_follow_ups call"))),
        cast("JudgeLeg", None),
    )

    assert faulted
    assert not any(result.excluded or result.provider_fault for result in faulted)
    assert all(not result.passed for result in faulted)
