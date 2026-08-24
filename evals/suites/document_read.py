"""Document-read cases: a visual question must use the paginated read tool, while a formula
question stays on the spreadsheet's structural tool."""

from pathlib import Path

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.scorers import (
    combine,
    exact_scorer,
    required_tools_scorer,
    restraint_scorer,
    skill_scorer,
)

FIXTURES = Path(__file__).parents[2] / "servers" / "preview" / "tests" / "fixtures"
DOCX_PATH = "/workspace/fixture-layout.docx"
XLSX_PATH = "/workspace/fixture-print-layout.xlsx"
DOCX = WorkspaceFile(
    path=DOCX_PATH.removeprefix("/workspace/"),
    content=(FIXTURES / "fixture-layout.docx").read_bytes(),
)
XLSX = WorkspaceFile(
    path=XLSX_PATH.removeprefix("/workspace/"),
    content=(FIXTURES / "fixture-print-layout.xlsx").read_bytes(),
)


def paginated_read_scorer(path: str, offset: int) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        call = next(
            (
                call
                for call in output.calls
                if call.name == "read"
                and call.input.get("file_path") == path
                and call.input.get("offset") == offset
            ),
            None,
        )
        if call is None:
            return CapabilityVerdict(False, f"did not read {path} from page {offset}")
        if not call.succeeded:
            return CapabilityVerdict(False, f"the paginated read failed: {call.result[:120]}")
        return CapabilityVerdict(True, f"read {path} from page {offset}")

    return DescribedGrader(f"read completes for {path!r} with offset={offset}", grade)


CASES = (
    CapabilityCase(
        "docx-second-page",
        f"What exact heading is printed on the second page of {DOCX_PATH}? Read that page, then "
        "reply with one line: 'ANSWER: <heading>'.",
        combine(exact_scorer("DOCX layout page two"), paginated_read_scorer(DOCX_PATH, 2)),
        workspace_files=(DOCX,),
        digest_tag="document-read:docx-second-page",
    ),
    CapabilityCase(
        "xlsx-second-printed-page",
        f"What exact title is visible on the second printed page of {XLSX_PATH}? Read that page, "
        "then reply with one line: 'ANSWER: <title>'.",
        combine(exact_scorer("XLSX print layout page two"), paginated_read_scorer(XLSX_PATH, 2)),
        workspace_files=(XLSX,),
        digest_tag="document-read:xlsx-second-page",
    ),
    CapabilityCase(
        "xlsx-formula-structure",
        f"What exact formula is stored in Overview!D8 of {XLSX_PATH}? Inspect the workbook's "
        "cell structure, then reply with one line: 'ANSWER: <formula>'.",
        combine(
            exact_scorer("=SUM(D4:D7)"),
            skill_scorer("office-xlsx", "office-docx"),
            required_tools_scorer(("xlsx_repl",)),
            restraint_scorer(("read",)),
        ),
        workspace_files=(XLSX,),
        digest_tag="document-read:xlsx-formula-structure",
    ),
)
