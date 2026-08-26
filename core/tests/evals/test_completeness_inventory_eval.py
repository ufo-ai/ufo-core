"""The completeness probe rewards bounded evidence gathering, not speculative breadth."""

from evals.harness.capability import CapabilityOutput
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.completeness_inventory import CASES, LI2_FAMILY, inventory_scorer

BY_NAME = {case.name: case for case in CASES}


def test_completeness_probe_is_explicit_not_part_of_the_default_suite() -> None:
    assert "completeness_inventory" in {task.name for task in TASKS}
    assert "completeness_inventory" not in {task.name for task in DEFAULT_TASKS}


async def test_polylog_family_requires_the_complete_supported_inventory() -> None:
    case = BY_NAME["polylog-family-from-evidence"]
    answer = "ANSWER: " + ", ".join(sorted(LI2_FAMILY))

    complete = await case.grader(CapabilityOutput(answer, ()))
    showcased_only = await case.grader(CapabilityOutput("ANSWER: LI2_HALF", ()))

    assert complete.passed
    assert not showcased_only.passed
    assert "li2_two" in showcased_only.reason


async def test_polylog_family_rejects_an_unsupported_value() -> None:
    case = BY_NAME["polylog-family-from-evidence"]
    answer = "ANSWER: " + ", ".join(sorted((*LI2_FAMILY, "li3_half")))

    verdict = await case.grader(CapabilityOutput(answer, ()))

    assert not verdict.passed
    assert "unsupported=['li3_half']" in verdict.reason


async def test_explicit_single_value_neighbor_rejects_family_expansion() -> None:
    case = BY_NAME["polylog-explicit-single-value"]

    narrow = await case.grader(CapabilityOutput("ANSWER: LI2_HALF", ()))
    expanded = await case.grader(CapabilityOutput("ANSWER: LI2_HALF, LI2_TWO", ()))

    assert narrow.passed
    assert not expanded.passed
    assert "unsupported=['li2_two']" in expanded.reason


async def test_inventory_scorer_reports_missing_and_unsupported_keys() -> None:
    verdict = await inventory_scorer(frozenset({"alpha", "beta"}))(
        CapabilityOutput("ANSWER: ALPHA, GAMMA", ())
    )

    assert not verdict.passed
    assert verdict.reason == "missing=['beta'], unsupported=['gamma']"
