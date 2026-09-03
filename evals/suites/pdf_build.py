"""PDF-build case: build a one-page PDF, deliver it, and answer with the heading read back from
the produced file. The delivered bytes are the claim — the grader inspects the artifact the turn
shared rather than the names of the tools that made it."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import (
    combine,
    exact_scorer,
    pdf_document_scorer,
    required_tools_scorer,
)

NOTICE_PATH = "/workspace/notice.pdf"
NOTICE_HEADING = "Board Meeting Notice"

CASES = (
    CapabilityCase(
        "one-page-pdf",
        f"Create a one-page PDF at {NOTICE_PATH} whose only visible content is a heading "
        f"with the exact text '{NOTICE_HEADING}'; set the PDF title metadata to the same text. "
        "Read the PDF back to confirm the heading, send me the file, then reply with a single "
        "line 'ANSWER: <the exact heading text you read back from the PDF>'.",
        combine(
            exact_scorer(NOTICE_HEADING),
            required_tools_scorer(("bash", "share_file")),
            pdf_document_scorer(),
        ),
        digest_tag="deliverable:one-page-pdf",
    ),
)
