import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    IMPLEMENTATION_INVENTORY_OBJECTIVE,
    implementation_inventory_scorer,
)


def output_for(result: str) -> CapabilityOutput:
    return CapabilityOutput(result, ())


def test_repository_wide_inventory_names_the_authoritative_sources() -> None:
    assert "sympy/core/assumptions.py" in IMPLEMENTATION_INVENTORY_OBJECTIVE
    assert "sympy/assumptions/ask.py" in IMPLEMENTATION_INVENTORY_OBJECTIVE
    assert "sympy/assumptions/ask_generated.py" in IMPLEMENTATION_INVENTORY_OBJECTIVE


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "SOURCE_FILES: sympy/core/assumptions.py, sympy/assumptions/ask.py\n"
            "GENERATED_FILES: sympy/assumptions/ask_generated.py\n"
            "REGENERATE_WITH: ./bin/ask_update.py",
            True,
        ),
        (
            "SOURCE_FILES: sympy/core/assumptions.py\nGENERATED_FILES: NONE\nREGENERATE_WITH: NONE",
            False,
        ),
        (
            "SOURCE_FILES: sympy/core/assumptions.py, sympy/assumptions/ask.py\n"
            "GENERATED_FILES: sympy/assumptions/ask_generated.py\n"
            "REGENERATE_WITH: NONE",
            False,
        ),
        (
            "SOURCE_FILES: sympy/core/assumptions.py, sympy/assumptions/ask.py, "
            "sympy/core/power.py\n"
            "GENERATED_FILES: sympy/assumptions/ask_generated.py\n"
            "REGENERATE_WITH: bin/ask_update.py",
            False,
        ),
    ),
)
async def test_repository_wide_inventory_requires_both_engines_and_generated_facts(
    result: str, passed: bool
) -> None:
    verdict = await implementation_inventory_scorer(repository_wide=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "SOURCE_FILES: sympy/core/power.py\nGENERATED_FILES: NONE\nREGENERATE_WITH: NONE",
            True,
        ),
        (
            "SOURCE_FILES: sympy/core/power.py, sympy/core/assumptions.py\n"
            "GENERATED_FILES: NONE\n"
            "REGENERATE_WITH: NONE",
            False,
        ),
        (
            "SOURCE_FILES: sympy/core/power.py\n"
            "GENERATED_FILES: sympy/assumptions/ask_generated.py\n"
            "REGENERATE_WITH: bin/ask_update.py",
            False,
        ),
    ),
)
async def test_proven_local_inventory_rejects_repository_wide_work(
    result: str, passed: bool
) -> None:
    verdict = await implementation_inventory_scorer(repository_wide=False)(output_for(result))

    assert verdict.passed is passed


async def test_inventory_requires_all_three_decision_markers() -> None:
    grader = implementation_inventory_scorer(repository_wide=True)

    assert not (
        await grader(
            output_for(
                "SOURCE_FILES: sympy/core/assumptions.py, sympy/assumptions/ask.py\n"
                "GENERATED_FILES: sympy/assumptions/ask_generated.py"
            )
        )
    ).passed


async def test_inventory_scorer_rejects_tool_calls() -> None:
    output = CapabilityOutput(
        "SOURCE_FILES: sympy/core/assumptions.py, sympy/assumptions/ask.py\n"
        "GENERATED_FILES: sympy/assumptions/ask_generated.py\n"
        "REGENERATE_WITH: bin/ask_update.py",
        (ToolInvocation("bash", {}),),
    )

    verdict = await implementation_inventory_scorer(repository_wide=True)(output)

    assert not verdict.passed
    assert verdict.reason == "used tools at the isolated decision step"
