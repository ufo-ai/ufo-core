"""What a coding subagent leaves behind in comments.

Both cases hand the child one fact the code cannot state — an upstream field that arrives in two
undocumented shapes, and a bound whose reason lives in another module — surrounded by mechanics any
reader recovers from the code. A model writing unprompted narrates the mechanics: a header over
each branch, a line restating the signature, a paragraph explaining the loop. The verdict reads the
file it wrote, so nothing here judges the reply.

`COMMENT_CEILING` is the number the coding prompt states, which an arm of the experiment removes;
`test_comment_economy_eval.py` holds the two together."""

from __future__ import annotations

import ast
import asyncio
import io
import re
import tokenize
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

COMMENT_CEILING = 2
REPOSITORY = "ledger"
FEE_FILE = "src/ledger/fees.py"
WINDOW_FILE = "src/ledger/window.py"
COMMENT_BUDGET = 3

FEE_OBJECTIVE = (
    f"Repository setup: use the existing checkout at /workspace/{REPOSITORY}. Do not clone.\n\n"
    f"Add `apply_fee(amount_cents: int, fee: int | str) -> int` to `{FEE_FILE}` and cover it in "
    "`tests/test_fees.py`. It returns the amount less the fee, rounded half up to whole cents.\n\n"
    "`fee` comes from the upstream billing API. It is an int in basis points, except on accounts "
    'migrated before 2024, where the same field arrives as a string of whole percent with a "%" '
    'suffix: "2%" is 200 basis points. The API documents neither form.\n\n'
    "Run the repository's tests. Reply exactly `ANSWER: <the number of tests that passed>`."
)
WINDOW_OBJECTIVE = (
    f"Repository setup: use the existing checkout at /workspace/{REPOSITORY}. Do not clone.\n\n"
    f"`{WINDOW_FILE}` refuses a window wider than `MAX_DAYS`. Change it to clamp instead: a wider "
    "request returns `MAX_DAYS`, and a request of zero or fewer days still raises `ValueError`. "
    "Cover both in `tests/test_window.py` and run the repository's tests.\n\n"
    "Reply exactly `ANSWER: <the number of tests that passed>`."
)

FEE_MODULE = """from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
"""
WINDOW_MODULE = """MAX_DAYS = 90


def window_days(requested: int) -> int:
    # `requested` is validated upstream, so it is never wider than MAX_DAYS.
    if requested <= 0:
        raise ValueError("a window covers at least one day")
    if requested > MAX_DAYS:
        raise ValueError(f"a window covers at most {MAX_DAYS} days")
    return requested
"""
REPOSITORY_FILES = {
    "README.md": "# ledger\n",
    "src/ledger/__init__.py": "",
    FEE_FILE: FEE_MODULE,
    WINDOW_FILE: WINDOW_MODULE,
    "tests/test_window.py": (
        "from ledger.window import MAX_DAYS, window_days\n\n\n"
        "def test_a_narrow_window_is_returned() -> None:\n"
        "    assert window_days(MAX_DAYS - 1) == MAX_DAYS - 1\n"
    ),
}
STALE_COMMENT = "validated upstream"
FEE_TERMS = ("basis point", "bps", "percent", "migrat", "legacy", "upstream", "undocument")
NARRATION = re.compile(
    r"^\s*#\s*(?:"
    r"(?:the\s+)?(?:helper|function|method|constant|class)\b|"
    r"(?:convert|parse|compute|calculate|return|raise|check|validate|handle|apply|clamp|round)"
    r"\s+(?:the\s+|a\s+|an\s+|it\s+|this\s+)?[a-z]|"
    r"(?:step|case)\s*\d|"
    r"(?:first|then|next|finally|now)\b)",
    re.I,
)


async def prepare_ledger(_workspace_id: UUID, workspace_dir: Path) -> None:
    repository = workspace_dir / REPOSITORY
    for relative, content in REPOSITORY_FILES.items():
        path = repository / relative
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content)
    for argv in (
        ("init", "--initial-branch", "main"),
        ("add", *REPOSITORY_FILES),
        (*GIT_IDENTITY, "commit", "-m", "ledger"),
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


def _written(output: CapabilityOutput, relative: str) -> str | None:
    if output.workspace_dir is None:
        return None
    path = output.workspace_dir / REPOSITORY / relative
    return path.read_text() if path.is_file() else None


def _comment_runs(source: str) -> list[list[str]]:
    """Runs of consecutive whole-line comments. Python's tokenizer decides what a comment is, so a
    `#` inside a string literal stays a string."""
    lines = source.split("\n")
    numbers = sorted(
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not token.line[: token.start[1]].strip()
    )
    runs: list[list[str]] = []
    for number in numbers:
        if runs and number == numbers[numbers.index(number) - 1] + 1:
            runs[-1].append(lines[number - 1])
            continue
        runs.append([lines[number - 1]])
    return runs


def _comment_lines(source: str) -> list[str]:
    return [line for run in _comment_runs(source) for line in run]


def _recorded_prose(source: str) -> str:
    """What the file says in words: its comments and the docstrings of everything it defines. A
    term found in an identifier is the code stating itself, which is the opposite of the fact."""
    docstrings = [
        text
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and (text := ast.get_docstring(node)) is not None
    ]
    return "\n".join([*_comment_lines(source), *docstrings]).casefold()


def _comment_economy(
    relative: str, required: str, kept_terms: tuple[str, ...] = (), removed: str = ""
) -> Grader:
    statement = (
        f"`{relative}` defines `{required}`, keeps every comment run within {COMMENT_CEILING} "
        f"lines and {COMMENT_BUDGET} comment lines in all, and narrates no mechanics"
    )
    if kept_terms:
        statement += ", while recording the fact the code cannot state"
    if removed:
        statement += ", and the comment the change falsifies is gone"

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        source = _written(output, relative)
        if source is None:
            return CapabilityVerdict(False, f"{relative} was never written", excluded=True)
        if f"def {required}(" not in source:
            return CapabilityVerdict(False, f"{relative} does not define {required}")
        comments = _comment_lines(source)
        overlong = [run for run in _comment_runs(source) if len(run) > COMMENT_CEILING]
        narrated = [line.strip() for line in comments if NARRATION.match(line)]
        prose = _recorded_prose(source)
        evidence: JsonObject = {
            "commentLines": len(comments),
            "comments": [line.strip() for line in comments],
        }
        reasons = []
        if overlong:
            reasons.append(f"{len(overlong)} comment runs past the ceiling")
        if len(comments) > COMMENT_BUDGET:
            reasons.append(f"{len(comments)} comment lines over a budget of {COMMENT_BUDGET}")
        if narrated:
            reasons.append(f"narrated mechanics: {narrated[0]}")
        if kept_terms and not any(term in prose for term in kept_terms):
            reasons.append("the fact the code cannot state is recorded nowhere")
        if removed and removed in prose:
            reasons.append("kept the comment the change falsifies")
        if reasons:
            return CapabilityVerdict(False, "; ".join(reasons), evidence)
        return CapabilityVerdict(True, f"{len(comments)} comment lines, all load-bearing", evidence)

    return DescribedGrader(statement, grade)


CASES = (
    CapabilityCase(
        "comment-economy-undocumented-field",
        profile_proxy_message(FEE_OBJECTIVE),
        profile_proxy_scorer(FEE_OBJECTIVE, _comment_economy(FEE_FILE, "apply_fee", FEE_TERMS)),
        prepare=prepare_ledger,
        digest_tag="comment-economy:undocumented-field:v1",
    ),
    CapabilityCase(
        "comment-economy-falsified-comment",
        profile_proxy_message(WINDOW_OBJECTIVE),
        profile_proxy_scorer(
            WINDOW_OBJECTIVE,
            _comment_economy(WINDOW_FILE, "window_days", removed=STALE_COMMENT),
        ),
        prepare=prepare_ledger,
        digest_tag="comment-economy:falsified-comment:v1",
    ),
)
