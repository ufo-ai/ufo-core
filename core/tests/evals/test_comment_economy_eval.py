import tempfile
from pathlib import Path

import pytest
from ufo_ext_coding.manifest import CODING_PROMPT

from evals.harness.capability import CapabilityOutput
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.comment_economy import (
    CASES,
    COMMENT_BUDGET,
    COMMENT_CEILING,
    FEE_FILE,
    FEE_TERMS,
    REPOSITORY,
    STALE_COMMENT,
    WINDOW_FILE,
    WINDOW_MODULE,
    _comment_economy,
)

QUIRK = (
    "    # The upstream billing API documents neither form: an account migrated before\n"
    '    # 2024 sends whole percent as a "%"-suffixed string, every other one an int.\n'
)
ECONOMICAL = f"""from decimal import ROUND_HALF_UP, Decimal

BASIS_POINTS_PER_PERCENT = 100
BASIS_POINTS_PER_UNIT = 10000


def apply_fee(amount_cents: int, fee: int | str) -> int:
{QUIRK}    basis_points = (
        int(fee.rstrip("%")) * BASIS_POINTS_PER_PERCENT if isinstance(fee, str) else fee
    )
    charge = Decimal(amount_cents) * Decimal(basis_points) / Decimal(BASIS_POINTS_PER_UNIT)
    return amount_cents - int(charge.quantize(Decimal(1), rounding=ROUND_HALF_UP))
"""
NARRATED = '''from decimal import ROUND_HALF_UP, Decimal


def apply_fee(amount_cents: int, fee: int | str) -> int:
    """Apply the fee to the amount."""
    # Parse the fee. It can be an int in basis points or a string of whole percent.
    # Legacy accounts migrated before 2024 send a string with a "%" suffix, so we
    # strip the suffix and multiply by 100 to get basis points.
    if isinstance(fee, str):
        basis_points = int(fee.rstrip("%")) * 100
    else:
        basis_points = fee
    charge = Decimal(amount_cents) * Decimal(basis_points) / Decimal(10000)
    return amount_cents - int(charge.quantize(Decimal(1), rounding=ROUND_HALF_UP))
'''
IN_A_DOCSTRING = ECONOMICAL.replace(
    QUIRK,
    '    """Accounts migrated before 2024 send whole percent as a "%"-suffixed string; the API\n'
    '    documents neither form."""\n',
)
FACT_NOWHERE = ECONOMICAL.replace(QUIRK, "")
SCATTERED = ECONOMICAL.replace(
    "BASIS_POINTS_PER_PERCENT = 100",
    "# A basis point is a hundredth of a percent.\nBASIS_POINTS_PER_PERCENT = 100",
).replace(
    "BASIS_POINTS_PER_UNIT = 10000",
    "# Ten thousand basis points make the whole.\nBASIS_POINTS_PER_UNIT = 10000",
)
REFUSAL = 'raise ValueError(f"a window covers at most {MAX_DAYS} days")'
CLAMPED = WINDOW_MODULE.replace(REFUSAL, "return MAX_DAYS")
FALSIFIED = f"    # `requested` is {STALE_COMMENT}, so it is never wider than MAX_DAYS.\n"
FEE_GRADER = _comment_economy(FEE_FILE, "apply_fee", FEE_TERMS)
WINDOW_GRADER = _comment_economy(WINDOW_FILE, "window_days", removed=STALE_COMMENT)


async def verdict(grader, relative: str, source: str):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / REPOSITORY / relative
        path.parent.mkdir(parents=True)
        path.write_text(source)
        return await grader(CapabilityOutput(response="", calls=(), workspace_dir=Path(directory)))


def test_the_suite_grades_the_ceiling_the_prompt_states() -> None:
    assert COMMENT_CEILING == 2
    assert "two lines is the ceiling for what survives" in CODING_PROMPT
    assert COMMENT_BUDGET > COMMENT_CEILING
    assert FALSIFIED in WINDOW_MODULE


def test_the_suite_targets_the_coding_profile_without_entering_defaults() -> None:
    task = next(task for task in TASKS if task.name == "comment_economy")

    assert task.agent == "profile:coding"
    assert task.cases == tuple(case.name for case in CASES)
    assert task not in DEFAULT_TASKS


@pytest.mark.parametrize(
    ("source", "passed", "reason"),
    (
        (ECONOMICAL, True, "all load-bearing"),
        (IN_A_DOCSTRING, True, "all load-bearing"),
        (NARRATED, False, "past the ceiling"),
        (FACT_NOWHERE, False, "recorded nowhere"),
        (SCATTERED, False, f"over a budget of {COMMENT_BUDGET}"),
    ),
)
async def test_the_fee_grader_reads_prose_and_not_identifiers(
    source: str, passed: bool, reason: str
) -> None:
    result = await verdict(FEE_GRADER, FEE_FILE, source)

    assert result.passed is passed, result.reason
    assert reason in result.reason


@pytest.mark.parametrize(
    ("source", "passed", "reason"),
    (
        (CLAMPED.replace(FALSIFIED, ""), True, "all load-bearing"),
        (CLAMPED, False, "kept the comment the change falsifies"),
        (CLAMPED.replace(FALSIFIED, "    # Clamp the request to MAX_DAYS.\n"), False, "narrated"),
    ),
)
async def test_the_window_grader_requires_the_falsified_comment_to_go(
    source: str, passed: bool, reason: str
) -> None:
    result = await verdict(WINDOW_GRADER, WINDOW_FILE, source)

    assert result.passed is passed, result.reason
    assert reason in result.reason


async def test_a_file_the_child_never_wrote_leaves_the_case_excluded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = await FEE_GRADER(
            CapabilityOutput(response="", calls=(), workspace_dir=Path(directory))
        )

    assert result.excluded
    assert not result.passed
