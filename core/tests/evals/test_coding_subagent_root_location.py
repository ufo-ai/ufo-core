import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import root_location_scorer


def output_for(result: str) -> CapabilityOutput:
    return CapabilityOutput(result, ())


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Probe direct Add and Mul first.\n"
            "ROOT_LAYER: MatAdd postprocessor in matexpr.py\n"
            "REJECTED_LAYER: BlockMatrix._blockmul output normalization",
            True,
        ),
        (
            "Repair the first caller.\n"
            "ROOT_LAYER: BlockMatrix._blockmul\n"
            "REJECTED_LAYER: MatAdd postprocessor",
            False,
        ),
        ("ROOT_LAYER: MatAdd postprocessor", False),
    ),
)
async def test_root_location_scorer_requires_the_generic_add_layer(
    result: str, passed: bool
) -> None:
    verdict = await root_location_scorer(generic_add=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "The primitive contract already passes.\n"
            "ROOT_LAYER: BlockMatrix._blockmul explicit zero conversion\n"
            "REJECTED_LAYER: generic MatAdd postprocessor",
            True,
        ),
        (
            "Change the deeper operation anyway.\n"
            "ROOT_LAYER: generic MatAdd postprocessor\n"
            "REJECTED_LAYER: BlockMatrix._blockmul",
            False,
        ),
    ),
)
async def test_root_location_neighbor_keeps_a_proven_operator_local(
    result: str, passed: bool
) -> None:
    verdict = await root_location_scorer(generic_add=False)(output_for(result))

    assert verdict.passed is passed


async def test_root_location_scorer_requires_both_direct_response_markers() -> None:
    grader = root_location_scorer(generic_add=True)
    missing_root = await grader(output_for("REJECTED_LAYER: _blockmul"))
    missing_rejected = await grader(output_for("ROOT_LAYER: MatAdd"))

    assert not missing_root.passed
    assert not missing_rejected.passed


async def test_root_location_scorer_rejects_tool_calls() -> None:
    output = CapabilityOutput(
        "ROOT_LAYER: MatAdd postprocessor\nREJECTED_LAYER: BlockMatrix._blockmul",
        (ToolInvocation("bash", {}),),
    )

    verdict = await root_location_scorer(generic_add=True)(output)

    assert not verdict.passed
    assert verdict.reason == "used tools at the isolated decision step"
