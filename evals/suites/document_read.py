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
from ufo.harness.sandbox.preview import PREVIEW_HOST

FIXTURES = Path(__file__).parents[2] / "servers" / "preview" / "tests" / "fixtures"
# The sandbox proxy's own line for a CONNECT it would not open (`client/src/egress.rs`). The read
# path prints the render URL in every failure text, including one the service answered with a
# status, so the host alone does not say the request never arrived — this line does.
RENDER_TUNNEL_REFUSED = f"refused the tunnel to {PREVIEW_HOST}"
# `xlsx_repl` runs `python3` inside the sandbox, and `openpyxl` reaches it from the sandbox image
# (`sandbox/build_template.py`). The local carrier runs host subprocesses instead, where that
# interpreter is the machine's own and carries no such package, so the import fails before the
# workbook is opened. That is an environment the case does not describe, the same as a deploy
# running no preview service.
REPL_PACKAGE_MISSING = "ModuleNotFoundError: No module named 'openpyxl'"
DOCX_PATH = "/workspace/fixture-layout.docx"
XLSX_PATH = "/workspace/fixture-print-layout.xlsx"
# The heading each fixture prints on its second page: the answer the case asks for, and the text a
# read has to carry back for the page to have come through the render service rather than a shell.
DOCX_PAGE_TWO = "DOCX layout page two"
XLSX_PAGE_TWO = "XLSX print layout page two"
DOCX = WorkspaceFile(
    path=DOCX_PATH.removeprefix("/workspace/"),
    content=(FIXTURES / "fixture-layout.docx").read_bytes(),
)
XLSX = WorkspaceFile(
    path=XLSX_PATH.removeprefix("/workspace/"),
    content=(FIXTURES / "fixture-print-layout.xlsx").read_bytes(),
)


def document_read_scorer(path: str, page_text: str) -> Grader:
    """Reaching the page through `read` is the model's act; answering it is the environment's. The
    page has to arrive from `read` — an agent that unzips the OOXML in bash reads the same words
    without exercising the capability, and the answer grader alone cannot tell the two apart, so
    this holds the trajectory. Which offset carried it is not the model's act: these fixtures are
    two pages and the default window is twenty, so one plain read returns the whole document and
    naming a page is work the model is right to skip.

    A read that broke for its own reasons fails — only one the proxy refused to tunnel to the
    preview service excludes the sample. Every document read routes through that service, and a
    deploy running none never admits its host, so the tunnel is refused and no render is reached:
    an environment the case does not describe. A render the service answered with an error is the
    fault this suite exists to catch, so it is scored; excluding it would file a real regression
    where nobody reads it."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        reads = tuple(
            call
            for call in output.calls
            if call.name == "read" and call.input.get("file_path") == path
        )
        if not reads:
            return CapabilityVerdict(False, f"did not read {path}")
        if any(call.succeeded and page_text.lower() in call.result.lower() for call in reads):
            return CapabilityVerdict(True, f"read {path} and it returned {page_text!r}")
        if any(call.succeeded for call in reads):
            return CapabilityVerdict(False, f"read {path} without returning {page_text!r}")
        unreachable = any(RENDER_TUNNEL_REFUSED in call.result for call in reads)
        reason = (
            f"the render service never answered {path}: {reads[0].result[:120]}"
            if unreachable
            else f"the read of {path} failed: {reads[0].result[:120]}"
        )
        return CapabilityVerdict(False, reason, excluded=unreachable)

    return DescribedGrader(f"read returns {page_text!r} from {path!r}", grade)


def repl_hosted_scorer() -> Grader:
    """The workbook question is answerable only where the REPL's interpreter carries `openpyxl`.
    A carrier that runs host subprocesses does not, so the import fails on the cell the model
    wrote correctly, and charging that to the model files a carrier gap as a capability
    regression. A REPL that ran and answered anything else is the model's, and is scored."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        missing = any(
            call.name == "xlsx_repl" and REPL_PACKAGE_MISSING in call.result
            for call in output.calls
        )
        if missing:
            return CapabilityVerdict(
                False,
                f"the REPL interpreter carries no openpyxl: {REPL_PACKAGE_MISSING}",
                excluded=True,
            )
        return CapabilityVerdict(True, "the REPL interpreter carries openpyxl")

    return DescribedGrader("the REPL's interpreter carries openpyxl", grade)


CASES = (
    CapabilityCase(
        "docx-second-page",
        f"What exact heading is printed on the second page of {DOCX_PATH}? Read that page, then "
        "reply with one line: 'ANSWER: <heading>'.",
        combine(exact_scorer(DOCX_PAGE_TWO), document_read_scorer(DOCX_PATH, DOCX_PAGE_TWO)),
        workspace_files=(DOCX,),
        digest_tag="document-read:docx-second-page",
    ),
    CapabilityCase(
        "xlsx-second-printed-page",
        f"What exact title is visible on the second printed page of {XLSX_PATH}? Read that page, "
        "then reply with one line: 'ANSWER: <title>'.",
        combine(exact_scorer(XLSX_PAGE_TWO), document_read_scorer(XLSX_PATH, XLSX_PAGE_TWO)),
        workspace_files=(XLSX,),
        digest_tag="document-read:xlsx-second-page",
    ),
    CapabilityCase(
        "xlsx-formula-structure",
        f"What exact formula is stored in Overview!D8 of {XLSX_PATH}? Inspect the workbook's "
        "cell structure, then reply with one line: 'ANSWER: <formula>'.",
        combine(
            repl_hosted_scorer(),
            exact_scorer("=SUM(D4:D7)"),
            skill_scorer("office-xlsx", "office-docx"),
            required_tools_scorer(("xlsx_repl",)),
            restraint_scorer(("read",)),
        ),
        workspace_files=(XLSX,),
        digest_tag="document-read:xlsx-formula-structure",
    ),
)
