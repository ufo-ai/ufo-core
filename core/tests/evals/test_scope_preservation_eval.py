"""The scope probe isolates the choice that lost Pylint 8898 without teaching under-scoping."""

from evals.harness.capability import CapabilityOutput
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.scope_preservation import CASES, scope_choice_scorer

BY_NAME = {case.name: case for case in CASES}


def test_scope_probe_is_explicit_not_part_of_the_default_suite() -> None:
    assert "scope_preservation" in {task.name for task in TASKS}
    assert "scope_preservation" not in {task.name for task in DEFAULT_TASKS}


async def test_8898_quantifier_case_requires_the_narrow_compatible_fix() -> None:
    case = BY_NAME["regex-csv-quantifier"]

    narrow = await case.grader(CapabilityOutput("ANSWER: NARROW", ()))
    inflated = await case.grader(CapabilityOutput("ANSWER: GENERALIZE", ()))

    assert narrow.passed
    assert not inflated.passed
    assert case.digest_tag == "scope-preservation:regex-csv-quantifier:v2"


async def test_explicit_generalization_neighbor_rejects_an_under_scoped_fix() -> None:
    case = BY_NAME["regex-csv-explicit-generalization"]

    generalized = await case.grader(CapabilityOutput("ANSWER: GENERALIZE", ()))
    under_scoped = await case.grader(CapabilityOutput("ANSWER: NARROW", ()))

    assert generalized.passed
    assert not under_scoped.passed
    assert case.digest_tag == "scope-preservation:regex-csv-explicit-generalization:v2"


async def test_scope_choice_scorer_fails_closed_on_an_unrecognized_choice() -> None:
    verdict = await scope_choice_scorer("narrow")(CapabilityOutput("ANSWER: BOTH", ()))

    assert not verdict.passed
    assert verdict.reason == "selected 'both', expected 'narrow'"
