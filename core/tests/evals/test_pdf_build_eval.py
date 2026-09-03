"""The PDF case grades the file the turn delivered, not the tools that produced it."""

from evals.harness.capability import CapabilityOutput, SharedArtifact, ToolInvocation
from evals.suites.pdf_build import CASES, NOTICE_HEADING

ANSWER = f"ANSWER: {NOTICE_HEADING}"
NOTICE_PDF = b"%PDF-1.4\n1 0 obj\n<< /Title (Board Meeting Notice) >>\nendobj\ntrailer\n%%EOF\n"
CASE = CASES[0]
BUILT = ToolInvocation("bash", {"command": "python3 build.py"}, "ok", True)


def _shared(name: str = "notice.pdf") -> ToolInvocation:
    return ToolInvocation(
        "share_file",
        {"files": [{"file_path": f"/workspace/{name}"}]},
        f'[{{"name":"{name}"}}]',
        True,
    )


def _output(
    *,
    answer: str = ANSWER,
    calls: tuple[ToolInvocation, ...] = (),
    artifacts: tuple[SharedArtifact, ...] = (),
) -> CapabilityOutput:
    return CapabilityOutput(answer, calls, artifacts=artifacts)


async def test_the_case_passes_on_a_delivered_well_formed_pdf() -> None:
    verdict = await CASE.grader(
        _output(
            calls=(BUILT, _shared()),
            artifacts=(SharedArtifact("notice.pdf", NOTICE_PDF),),
        )
    )

    assert verdict.passed


async def test_the_case_fails_when_nothing_was_delivered() -> None:
    verdict = await CASE.grader(_output(calls=(BUILT,)))

    assert not verdict.passed
    assert "share_file" in verdict.reason


async def test_the_case_fails_when_the_delivered_file_is_not_a_pdf() -> None:
    verdict = await CASE.grader(
        _output(
            calls=(BUILT, _shared()),
            artifacts=(SharedArtifact("notice.pdf", b"Board Meeting Notice"),),
        )
    )

    assert not verdict.passed
    assert "missing %PDF- header" in verdict.reason


async def test_the_case_fails_when_the_delivered_pdf_is_truncated() -> None:
    verdict = await CASE.grader(
        _output(
            calls=(BUILT, _shared()),
            artifacts=(SharedArtifact("notice.pdf", b"%PDF-1.4\n1 0 obj\n"),),
        )
    )

    assert not verdict.passed
    assert "no %%EOF trailer" in verdict.reason


async def test_the_case_fails_when_the_answer_does_not_match_the_heading() -> None:
    verdict = await CASE.grader(
        _output(
            answer="ANSWER: Board Meeting",
            calls=(BUILT, _shared()),
            artifacts=(SharedArtifact("notice.pdf", NOTICE_PDF),),
        )
    )

    assert not verdict.passed


def test_the_case_declares_no_artifact_probe() -> None:
    """A capability run wires a workspace probe only for the app-bench tasks, so a probe declared
    here would raise instead of grading."""
    assert CASE.artifact_probe is None
