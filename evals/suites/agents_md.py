"""Whether a coding child follows the house rules its checkout carries.

Both cases seed a checkout whose `AGENTS.md` states a rule a model writing unprompted breaks: the
package raises its own error rather than a built-in, and it carries no docstrings. Neither
objective mentions `AGENTS.md`, so the only route to the rule is the block the pack injects.

The second case puts the root and a nested file in conflict and reads which one won. Its nested
rule names an arbitrary unit, so the three outcomes separate: the deeper rule followed, the root
rule followed, or neither read and the model's own habit showing.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from uuid import UUID

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.harness import JsonObject
from evals.suites.coding_subagent import GIT_IDENTITY, profile_proxy_message, profile_proxy_scorer

OWN_ERROR = "LedgerError"
BUILTIN_ERRORS = ("ValueError", "TypeError", "RuntimeError", "AssertionError")
FEE_REPOSITORY = "ledgerbook"
FEE_FILE = "src/ledgerbook/fees.py"
UNIT_REPOSITORY = "postbook"
UNIT_FILE = "src/postbook/totals.py"
DOCSTRING_UNIT = "cents"

FEE_RULES = """# ledgerbook

## Errors

Raise `LedgerError` from `ledgerbook.errors`. A built-in exception never leaves this package.

## Code style

No docstrings. Names and structure carry intent.
"""
FEE_MODULE = """from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
"""
FEE_FILES = {
    "AGENTS.md": FEE_RULES,
    "README.md": "# ledgerbook\n",
    "src/ledgerbook/__init__.py": "",
    "src/ledgerbook/errors.py": "class LedgerError(Exception):\n    pass\n",
    FEE_FILE: FEE_MODULE,
    "tests/test_fees.py": (
        "from decimal import Decimal\n\n"
        "from ledgerbook.fees import to_cents\n\n\n"
        "def test_to_cents_rounds_half_up() -> None:\n"
        '    assert to_cents(Decimal("1.5")) == 2\n'
    ),
}
FEE_OBJECTIVE = (
    f"Repository setup: use the existing checkout at /workspace/{FEE_REPOSITORY}. Do not clone.\n\n"
    f"Add `apply_fee(amount_cents: int, fee_bps: int) -> int` to `{FEE_FILE}` and cover it in "
    "`tests/test_fees.py`. It returns the amount less the fee, rounded half up to whole cents. A "
    "negative `fee_bps`, or one above 10000, is rejected with an exception.\n\n"
    "Run the repository's tests. Reply exactly `ANSWER: <the number of tests that passed>`."
)

UNIT_ROOT_RULES = """# postbook

## Code style

No docstrings anywhere in this repository. Names and structure carry intent.
"""
UNIT_PACKAGE_RULES = f"""# postbook/src

This directory overrides the repository's code style.

Every public function here carries a one-line docstring whose final word is the unit it returns, so
a caller reading the signature alone cannot mistake the scale. The unit here is `{DOCSTRING_UNIT}`.
"""
UNIT_MODULE = """def line_total(unit_price_cents: int, quantity: int) -> int:
    \"\"\"The price of one line, in cents.\"\"\"
    return unit_price_cents * quantity
"""
UNIT_FILES = {
    "AGENTS.md": UNIT_ROOT_RULES,
    "README.md": "# postbook\n",
    "src/postbook/AGENTS.md": UNIT_PACKAGE_RULES,
    "src/postbook/__init__.py": "",
    UNIT_FILE: UNIT_MODULE,
    "tests/test_totals.py": (
        "from postbook.totals import line_total\n\n\n"
        "def test_line_total_multiplies() -> None:\n"
        "    assert line_total(250, 3) == 750\n"
    ),
}
UNIT_OBJECTIVE = (
    f"Repository setup: use the existing checkout at /workspace/{UNIT_REPOSITORY}. Do not "
    "clone.\n\n"
    f"Add `order_total(line_cents: list[int], shipping_cents: int) -> int` to `{UNIT_FILE}` and "
    "cover it in `tests/test_totals.py`. It sums the lines and adds shipping.\n\n"
    "Run the repository's tests. Reply exactly `ANSWER: <the number of tests that passed>`."
)


async def _seeded(workspace_dir: Path, name: str, files: dict[str, str]) -> None:
    repository = workspace_dir / name
    for relative, content in files.items():
        path = repository / relative
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content)
    for argv in (
        ("init", "--initial-branch", "main"),
        ("add", *files),
        (*GIT_IDENTITY, "commit", "-m", name),
    ):
        process = await asyncio.create_subprocess_exec(
            "git",
            *argv,
            cwd=repository,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, error = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)} failed: {error.decode().strip()}")


async def prepare_ledgerbook(_workspace_id: UUID, workspace_dir: Path) -> None:
    await _seeded(workspace_dir, FEE_REPOSITORY, FEE_FILES)


async def prepare_postbook(_workspace_id: UUID, workspace_dir: Path) -> None:
    await _seeded(workspace_dir, UNIT_REPOSITORY, UNIT_FILES)


def _written(output: CapabilityOutput, repository: str, relative: str) -> str | None:
    if output.workspace_dir is None:
        return None
    path = output.workspace_dir / repository / relative
    return path.read_text() if path.is_file() else None


def _defined(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
        ),
        None,
    )


def _house_error_and_no_docstring(required: str) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        source = _written(output, FEE_REPOSITORY, FEE_FILE)
        if source is None:
            return CapabilityVerdict(False, f"{FEE_FILE} was never written", excluded=True)
        node = _defined(source, required)
        if node is None:
            return CapabilityVerdict(False, f"{FEE_FILE} does not define {required}")
        raised = tuple(
            inner.func.id
            for outer in ast.walk(node)
            if isinstance(outer, ast.Raise) and isinstance(inner := outer.exc, ast.Call)
            if isinstance(inner.func, ast.Name)
        )
        docstring = ast.get_docstring(node)
        evidence: JsonObject = {"raised": list(raised), "docstring": docstring}
        reasons = []
        if OWN_ERROR not in raised:
            reasons.append(f"raised {list(raised) or 'nothing'} rather than {OWN_ERROR}")
        if builtin := [name for name in raised if name in BUILTIN_ERRORS]:
            reasons.append(f"let {builtin[0]} out of the package")
        if docstring is not None:
            reasons.append("carries a docstring")
        if reasons:
            return CapabilityVerdict(False, "; ".join(reasons), evidence)
        return CapabilityVerdict(True, f"raised {OWN_ERROR}, no docstring", evidence)

    return DescribedGrader(
        f"`{required}` raises {OWN_ERROR} rather than a built-in and carries no docstring, as the "
        "checkout's AGENTS.md requires",
        grade,
    )


def _deeper_rule_won(required: str) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        source = _written(output, UNIT_REPOSITORY, UNIT_FILE)
        if source is None:
            return CapabilityVerdict(False, f"{UNIT_FILE} was never written", excluded=True)
        node = _defined(source, required)
        if node is None:
            return CapabilityVerdict(False, f"{UNIT_FILE} does not define {required}")
        docstring = ast.get_docstring(node)
        evidence: JsonObject = {"docstring": docstring, "followed": _followed(docstring)}
        if docstring is None:
            return CapabilityVerdict(False, "no docstring — the root rule won", evidence)
        if not docstring.strip().rstrip(".").casefold().endswith(DOCSTRING_UNIT):
            return CapabilityVerdict(False, "a docstring naming no unit — neither rule", evidence)
        return CapabilityVerdict(True, f"a docstring ending in {DOCSTRING_UNIT}", evidence)

    return DescribedGrader(
        f"`{required}` carries a one-line docstring ending in `{DOCSTRING_UNIT}`, the nested "
        "AGENTS.md rule that overrides the repository's own",
        grade,
    )


def _followed(docstring: str | None) -> str:
    if docstring is None:
        return "root"
    if docstring.strip().rstrip(".").casefold().endswith(DOCSTRING_UNIT):
        return "nested"
    return "neither"


CASES = (
    CapabilityCase(
        "agents-md-house-error-and-no-docstring",
        profile_proxy_message(FEE_OBJECTIVE),
        profile_proxy_scorer(FEE_OBJECTIVE, _house_error_and_no_docstring("apply_fee")),
        prepare=prepare_ledgerbook,
        digest_tag="agents-md:house-error-and-no-docstring:v1",
    ),
    CapabilityCase(
        "agents-md-deeper-rule-wins",
        profile_proxy_message(UNIT_OBJECTIVE),
        profile_proxy_scorer(UNIT_OBJECTIVE, _deeper_rule_won("order_total")),
        prepare=prepare_postbook,
        digest_tag="agents-md:deeper-rule-wins:v1",
    ),
)
