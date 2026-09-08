import tempfile
from pathlib import Path, PurePosixPath

import pytest
from ufo_ext_coding import agents_md

from evals.harness.capability import CapabilityOutput
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.agents_md import (
    CASES,
    DOCSTRING_UNIT,
    FEE_FILE,
    FEE_FILES,
    FEE_OBJECTIVE,
    FEE_REPOSITORY,
    OWN_ERROR,
    UNIT_FILE,
    UNIT_FILES,
    UNIT_OBJECTIVE,
    UNIT_REPOSITORY,
    _deeper_rule_won,
    _house_error_and_no_docstring,
)

OBEDIENT_FEE = """from decimal import ROUND_HALF_UP, Decimal

from ledgerbook.errors import LedgerError

BASIS_POINTS_PER_UNIT = 10000


def apply_fee(amount_cents: int, fee_bps: int) -> int:
    if fee_bps < 0 or fee_bps > BASIS_POINTS_PER_UNIT:
        raise LedgerError(f"a fee covers 0 to {BASIS_POINTS_PER_UNIT} basis points")
    charge = Decimal(amount_cents) * Decimal(fee_bps) / Decimal(BASIS_POINTS_PER_UNIT)
    return amount_cents - int(charge.quantize(Decimal(1), rounding=ROUND_HALF_UP))
"""
BUILTIN_FEE = OBEDIENT_FEE.replace(f"raise {OWN_ERROR}(", "raise ValueError(")
DOCUMENTED_FEE = OBEDIENT_FEE.replace(
    "def apply_fee(amount_cents: int, fee_bps: int) -> int:\n",
    'def apply_fee(amount_cents: int, fee_bps: int) -> int:\n    """Apply the fee."""\n',
)
NO_RAISE_FEE = """from decimal import Decimal


def apply_fee(amount_cents: int, fee_bps: int) -> int:
    return amount_cents - int(Decimal(amount_cents) * Decimal(fee_bps) / Decimal(10000))
"""

NESTED_UNIT = f'''def order_total(line_cents: list[int], shipping_cents: int) -> int:
    """The order's total, in {DOCSTRING_UNIT}."""
    return sum(line_cents) + shipping_cents
'''
ROOT_UNIT = """def order_total(line_cents: list[int], shipping_cents: int) -> int:
    return sum(line_cents) + shipping_cents
"""
NEITHER_UNIT = '''def order_total(line_cents: list[int], shipping_cents: int) -> int:
    """Sum the lines and add shipping."""
    return sum(line_cents) + shipping_cents
'''

FEE_GRADER = _house_error_and_no_docstring("apply_fee")
UNIT_GRADER = _deeper_rule_won("order_total")


async def verdict(grader, repository: str, relative: str, source: str):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / repository / relative
        path.parent.mkdir(parents=True)
        path.write_text(source)
        return await grader(CapabilityOutput(response="", calls=(), workspace_dir=Path(directory)))


def test_the_suite_targets_the_coding_profile_without_entering_defaults() -> None:
    task = next(task for task in TASKS if task.name == "agents_md")

    assert task.agent == "profile:coding"
    assert task.cases == tuple(case.name for case in CASES)
    assert task not in DEFAULT_TASKS


@pytest.mark.parametrize("files", (FEE_FILES, UNIT_FILES))
def test_every_seeded_rule_file_is_one_the_shipped_chain_finds(files: dict[str, str]) -> None:
    named = [path for path in files if PurePosixPath(path).name in agents_md.INSTRUCTION_FILENAMES]

    assert named
    assert agents_md.instruction_chain(f"/workspace/repo/{path}" for path in named) == tuple(
        f"/workspace/repo/{path}"
        for path in sorted(named, key=lambda path: len(PurePosixPath(path).parents))
    )


@pytest.mark.parametrize(
    ("objective", "repository"),
    ((FEE_OBJECTIVE, FEE_REPOSITORY), (UNIT_OBJECTIVE, UNIT_REPOSITORY)),
)
def test_no_objective_names_the_instruction_files_it_measures(
    objective: str, repository: str
) -> None:
    assert f"/workspace/{repository}" in objective
    for name in (*agents_md.INSTRUCTION_FILENAMES, "instruction", "house rule", "convention"):
        assert name.casefold() not in objective.casefold()


def test_the_seeded_files_break_no_rule_the_graders_read() -> None:
    assert "LedgerError" in FEE_FILES["src/ledgerbook/errors.py"]
    assert '"""' not in FEE_FILES[FEE_FILE]
    assert DOCSTRING_UNIT in UNIT_FILES[UNIT_FILE]


@pytest.mark.parametrize(
    ("source", "passed", "reason"),
    (
        (OBEDIENT_FEE, True, f"raised {OWN_ERROR}"),
        (BUILTIN_FEE, False, "let ValueError out"),
        (DOCUMENTED_FEE, False, "carries a docstring"),
        (NO_RAISE_FEE, False, "raised nothing"),
    ),
)
async def test_the_fee_grader_reads_the_error_and_the_docstring(
    source: str, passed: bool, reason: str
) -> None:
    result = await verdict(FEE_GRADER, FEE_REPOSITORY, FEE_FILE, source)

    assert result.passed is passed, result.reason
    assert reason in result.reason


@pytest.mark.parametrize(
    ("source", "passed", "followed"),
    ((NESTED_UNIT, True, "nested"), (ROOT_UNIT, False, "root"), (NEITHER_UNIT, False, "neither")),
)
async def test_the_unit_grader_separates_the_three_outcomes(
    source: str, passed: bool, followed: str
) -> None:
    result = await verdict(UNIT_GRADER, UNIT_REPOSITORY, UNIT_FILE, source)

    assert result.passed is passed, result.reason
    assert result.evidence is not None
    assert result.evidence["followed"] == followed


@pytest.mark.parametrize("grader", (FEE_GRADER, UNIT_GRADER))
async def test_a_file_the_child_never_wrote_leaves_the_case_excluded(grader) -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = await grader(
            CapabilityOutput(response="", calls=(), workspace_dir=Path(directory))
        )

    assert result.excluded
    assert not result.passed
