from hashlib import sha256

import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.coding_caveat_completeness import (
    CASE_OBJECTIVES,
    CASES,
    CDS_DIVISION_NEIGHBOR_SCORER,
    DECISION_PREFIX,
    OBJECTIVES,
    REFRACTION_ACCEPTANCE_SCORER,
    SLICED_WCS_NEIGHBOR_SCORER,
)


def output_for(result: str, calls: tuple[ToolInvocation, ...] = ()) -> CapabilityOutput:
    return CapabilityOutput(result, calls)


def test_caveat_cases_preserve_the_captured_objectives() -> None:
    expected_sizes = (5606, 5979, 3394)

    assert tuple(case.name for case in CASES) == tuple(name for name, _ in OBJECTIVES)
    for case, objective, (_, expected_digest), expected_size in zip(
        CASES, CASE_OBJECTIVES, OBJECTIVES, expected_sizes, strict=True
    ):
        body = objective.removeprefix(DECISION_PREFIX).encode()
        assert len(body) == expected_size
        assert sha256(body).hexdigest() == expected_digest
        assert objective in case.message


def test_caveat_suite_targets_the_coding_profile_without_entering_defaults() -> None:
    task = next(task for task in TASKS if task.name == "coding_caveat_completeness")

    assert task.agent == "profile:coding"
    assert task.cases == tuple(case.name for case in CASES)
    assert task not in DEFAULT_TASKS


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Implement refraction support with erfa.refco and the observed frame's "
            "relative_humidity and obswl. Apply and remove refraction for direct AltAz and "
            "HADec transforms. Add regression tests for both directions.",
            True,
        ),
        (
            "Include the existing AltAz refraction behavior in the direct path, with a matching "
            "inverse calculation. Add regression tests for direct AltAz and HADec transforms.",
            True,
        ),
        (
            "Do not implement refraction. Use erfa.refco, relative_humidity, and obswl only as "
            "documentation for AltAz and HADec. Add a regression test for the geometric path.",
            False,
        ),
        (
            "Implement the direct AltAz and HADec transforms and add regression tests.",
            False,
        ),
        (
            "Remove the refraction correction from AltAz and HADec. Add a regression test.",
            False,
        ),
    ),
)
async def test_refraction_acceptance_scorer_requires_implementation(
    result: str, passed: bool
) -> None:
    verdict = await REFRACTION_ACCEPTANCE_SCORER(output_for(result))

    assert verdict.passed is passed


async def test_refraction_acceptance_scorer_rejects_tool_use() -> None:
    output = output_for(
        "Implement refraction with erfa.refco, relative_humidity, and obswl. Test AltAz and HADec.",
        (ToolInvocation("grep", {}, "", True),),
    )

    verdict = await REFRACTION_ACCEPTANCE_SCORER(output)

    assert not verdict.passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "In SlicedLowLevelWCS, compute each dropped world coordinate from its fixed pixel "
            "with the full WCS pixel_to_world_values conversion. Reconstruct the full world "
            "input before world_to_pixel_values. Add a coupled PC matrix regression test.",
            True,
        ),
        (
            "In SlicedLowLevelWCS.world_to_pixel_values, correct the handling of fixed pixel "
            "axes so coupled world axes use the sliced pixel value during inverse "
            "transformation. Add a non-diagonal PC matrix regression test.",
            True,
        ),
        (
            "In SlicedLowLevelWCS, compute each dropped world coordinate from its fixed pixel "
            "with pixel_to_world_values, reconstruct the full world input, and call "
            "world_to_pixel_values. Also add refraction. Test the coupled PC matrix.",
            False,
        ),
        (
            "Change SlicedLowLevelWCS.world_to_pixel_values and add a coupled PC matrix test.",
            False,
        ),
    ),
)
async def test_sliced_wcs_neighbor_stays_on_the_dropped_axis(result: str, passed: bool) -> None:
    verdict = await SLICED_WCS_NEIGHBOR_SCORER(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Fix the ascii.cds unit parser so chained division stays left-associative. Add "
            "regression tests for 10+3J/m/s/kpc2 and 10-7J/s/kpc2.",
            True,
        ),
        (
            "Fix the CDS unit parser so every factor after `/` remains in the denominator. Add "
            "regression tests for 10+3J/m/s/kpc2 and 10-7J/s/kpc2.",
            True,
        ),
        (
            "Fix the CDS unit parser but move factors after `/` into the numerator. Add "
            "regression tests for 10+3J/m/s/kpc2 and 10-7J/s/kpc2.",
            False,
        ),
        (
            "Fix the CDS parser's division order and test 10+3J/m/s/kpc2.",
            False,
        ),
        (
            "Fix the ascii.cds grammar for chained division. Add regression tests for "
            "10+3J/m/s/kpc2 and 10-7J/s/kpc2, plus ITRS refraction coverage.",
            False,
        ),
    ),
)
async def test_cds_neighbor_stays_on_chained_division(result: str, passed: bool) -> None:
    verdict = await CDS_DIVISION_NEIGHBOR_SCORER(output_for(result))

    assert verdict.passed is passed
