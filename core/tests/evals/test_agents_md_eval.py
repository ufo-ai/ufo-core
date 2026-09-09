import tempfile
from pathlib import Path, PurePosixPath

import pytest
from ufo_ext_coding import agents_md

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import DEFAULT_TASKS, TASKS
from evals.suites.agents_md import (
    CARD_FILE,
    CARD_OBJECTIVE,
    CARD_REPOSITORY,
    CASES,
    DOCSTRING_UNIT,
    FEE_FILE,
    FEE_FILES,
    FEE_OBJECTIVE,
    FEE_REPOSITORY,
    HOUSE_RADIUS,
    INSTRUCTION_FILENAMES,
    OWN_ERROR,
    SKILL_RADIUS,
    UNIT_FILE,
    UNIT_FILES,
    UNIT_OBJECTIVE,
    UNIT_REPOSITORY,
    _deeper_rule_won,
    _house_error_and_no_docstring,
    _the_checkouts_rule_survived_the_skill,
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


def _looked_it_up(path: str) -> ToolInvocation:
    return ToolInvocation(name="read", input={"file_path": path}, result="rules", has_result=True)


READ_THE_RULES = _looked_it_up(f"/workspace/{FEE_REPOSITORY}/AGENTS.md")
CAT_THE_RULES = ToolInvocation(
    name="bash",
    input={"command": f"cat /workspace/{UNIT_REPOSITORY}/src/postbook/AGENTS.md"},
    result="rules",
    has_result=True,
)
READ_THE_CODE = _looked_it_up(f"/workspace/{FEE_REPOSITORY}/{FEE_FILE}")


async def verdict_with_calls(grader, repository, relative, source, calls):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / repository / relative
        path.parent.mkdir(parents=True)
        path.write_text(source)
        return await grader(
            CapabilityOutput(response="", calls=calls, workspace_dir=Path(directory))
        )


@pytest.mark.parametrize(
    ("calls", "passed"),
    (
        ((), True),
        ((READ_THE_CODE,), True),
        ((READ_THE_RULES,), False),
        ((CAT_THE_RULES,), False),
    ),
)
async def test_the_fee_grader_fails_a_child_that_looked_the_rules_up(calls, passed) -> None:
    result = await verdict_with_calls(FEE_GRADER, FEE_REPOSITORY, FEE_FILE, OBEDIENT_FEE, calls)

    assert result.passed is passed, result.reason
    if not passed:
        assert "opened 1 instruction file(s) itself" in result.reason


@pytest.mark.parametrize(
    ("calls", "passed"), (((), True), ((READ_THE_CODE,), True), ((CAT_THE_RULES,), False))
)
async def test_the_unit_grader_fails_a_child_that_looked_the_rules_up(calls, passed) -> None:
    result = await verdict_with_calls(UNIT_GRADER, UNIT_REPOSITORY, UNIT_FILE, NESTED_UNIT, calls)

    assert result.passed is passed, result.reason


def test_the_elision_check_reads_the_filenames_the_product_ships() -> None:
    assert INSTRUCTION_FILENAMES == agents_md.INSTRUCTION_FILENAMES


LOADED_STYLE = ToolInvocation(
    name="load_skill", input={"name": "ufo-style"}, result="loaded", has_result=True
)
HOUSE_CARD = f".card {{\n  border-radius: {HOUSE_RADIUS};\n}}\n"
SKILL_CARD = f".card {{\n  border-radius: {SKILL_RADIUS};\n}}\n"
NEITHER_CARD = ".card {\n  border-radius: 8px;\n}\n"
CARD_GRADER = _the_checkouts_rule_survived_the_skill()


@pytest.mark.parametrize(
    ("source", "calls", "passed", "reason"),
    (
        (HOUSE_CARD, (LOADED_STYLE,), True, f"took {HOUSE_RADIUS}"),
        (SKILL_CARD, (LOADED_STYLE,), False, f"took {SKILL_RADIUS}, the skill's value"),
        (NEITHER_CARD, (LOADED_STYLE,), False, "took neither radius"),
        (HOUSE_CARD, (), False, "loaded no skill"),
    ),
)
async def test_the_conflict_grader_names_the_authority_the_child_took(
    source: str, calls: tuple, passed: bool, reason: str
) -> None:
    result = await verdict_with_calls(CARD_GRADER, CARD_REPOSITORY, CARD_FILE, source, calls)

    assert result.passed is passed, result.reason
    assert reason in result.reason


async def test_a_turn_that_loaded_no_skill_is_excluded_rather_than_failed() -> None:
    result = await verdict_with_calls(CARD_GRADER, CARD_REPOSITORY, CARD_FILE, HOUSE_CARD, ())

    assert result.excluded
    assert not result.passed


async def test_the_conflict_grader_also_fails_a_child_that_read_the_rule_itself() -> None:
    opened = ToolInvocation(
        name="read",
        input={"file_path": f"/workspace/{CARD_REPOSITORY}/AGENTS.md"},
        result="rules",
        has_result=True,
    )

    result = await verdict_with_calls(
        CARD_GRADER, CARD_REPOSITORY, CARD_FILE, HOUSE_CARD, (LOADED_STYLE, opened)
    )

    assert not result.passed
    assert "opened 1 instruction file(s) itself" in result.reason


def test_the_conflict_case_contradicts_the_skill_it_makes_the_child_load() -> None:
    skill = (
        Path(__file__).resolve().parents[3] / "core/src/ufo/runtime/skills/ufo-style/SKILL.md"
    ).read_text()

    assert f"`{SKILL_RADIUS}`" in skill
    assert HOUSE_RADIUS not in skill
    assert "ufo-style" in CARD_OBJECTIVE
