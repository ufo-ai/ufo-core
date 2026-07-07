"""PDF-build case: build a one-page PDF, then answer with the heading read back from the produced
file — grading the artifact, not just the trajectory. `documents` is pure `load_skill` +
`bash`/`write`/`read`, so the case asserts the builtin tools the workflow actually requires."""

from ufo_ext_eval_harness.capability import CapabilityCase, Grader
from ufo_ext_eval_harness.scorers import combine, exact_scorer, required_tools_scorer

SPECS: list[tuple[str, str, Grader]] = [
    (
        "one-page-pdf",
        "Create a one-page PDF at /workspace/notice.pdf whose only visible content is a heading "
        "with the exact text 'Board Meeting Notice'; set the PDF title metadata to the same text. "
        "Read the PDF back to confirm the heading before replying. Reply with a single line "
        "'ANSWER: <the exact heading text you read back from the PDF>'.",
        combine(
            exact_scorer("Board Meeting Notice"),
            required_tools_scorer(("load_skill", "write", "bash", "read")),
        ),
    ),
]

CASES = tuple(
    CapabilityCase(name, brief, grader, digest_tag=f"deliverable:{name}")
    for name, brief, grader in SPECS
)
