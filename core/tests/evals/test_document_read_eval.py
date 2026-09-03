"""The paginated document cases grade the page the agent asked for, not the render behind it."""

from pathlib import Path

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.document_read import CASES, DOCX_PATH, RENDER_TUNNEL_REFUSED
from ufo.harness.sandbox.preview import PREVIEW_HOST

EGRESS_SOURCE = Path(__file__).parents[3] / "client" / "src" / "egress.rs"
ANSWER = "ANSWER: DOCX layout page two"
PAGE_TWO = "DOCX layout page two"
TUNNEL_502 = (
    "ValueError: document render failed: https://preview.ufo.internal/render failed: "
    "127.0.0.1 refused the tunnel to preview.ufo.internal: HTTP/1.1 502 Bad Gateway"
)
RENDER_ANSWERED_401 = (
    "ValueError: document render failed: https://preview.ufo.internal/render -> 401: "
    "preview token rejected"
)
DOCX_CASE = next(case for case in CASES if case.name == "docx-second-page")


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(ANSWER, calls)


async def test_the_docx_case_excludes_the_sample_when_the_render_never_answers() -> None:
    """A deploy running no preview service is not the environment the case describes, so the
    sample is neither charged to the model nor counted as a capability the suite proved."""
    verdict = await DOCX_CASE.grader(
        _output(
            ToolInvocation("read", {"file_path": DOCX_PATH, "offset": 2}, TUNNEL_502, True, True),
            ToolInvocation("bash", {"command": "unzip -p fixture.docx"}, PAGE_TWO, True),
        )
    )

    assert verdict.excluded
    assert not verdict.passed


async def test_the_docx_case_passes_when_the_paginated_read_answers() -> None:
    verdict = await DOCX_CASE.grader(
        _output(ToolInvocation("read", {"file_path": DOCX_PATH, "offset": 2}, PAGE_TWO, True))
    )

    assert verdict.passed


async def test_the_docx_case_fails_when_no_page_was_asked_for() -> None:
    verdict = await DOCX_CASE.grader(
        _output(ToolInvocation("bash", {"command": "unzip -p fixture.docx"}, PAGE_TWO, True))
    )

    assert not verdict.passed
    assert f"did not read {DOCX_PATH} from page 2" in verdict.reason


async def test_the_docx_case_fails_when_another_page_was_asked_for() -> None:
    verdict = await DOCX_CASE.grader(
        _output(
            ToolInvocation("read", {"file_path": DOCX_PATH, "offset": 1}, TUNNEL_502, True, True)
        )
    )

    assert not verdict.passed
    assert not verdict.excluded
    assert f"did not read {DOCX_PATH} from page 2" in verdict.reason


async def test_the_docx_case_fails_rather_than_excludes_when_no_page_was_asked_for() -> None:
    """Only the environment excludes. A model that never asked for the page is graded on it."""
    verdict = await DOCX_CASE.grader(
        _output(ToolInvocation("bash", {"command": "unzip -p fixture.docx"}, PAGE_TWO, True))
    )

    assert not verdict.excluded


async def test_the_docx_case_fails_when_the_answer_is_wrong() -> None:
    verdict = await DOCX_CASE.grader(
        CapabilityOutput(
            "ANSWER: DOCX layout page one",
            (ToolInvocation("read", {"file_path": DOCX_PATH, "offset": 2}, PAGE_TWO, True),),
        )
    )

    assert not verdict.passed


async def test_the_docx_case_fails_when_the_read_broke_for_its_own_reasons() -> None:
    """Only an unreachable render excludes. A paginated read that broke on its own is the fault
    this suite exists to catch, so it is scored, not filed away as an environment gap."""
    verdict = await DOCX_CASE.grader(
        _output(
            ToolInvocation(
                "read",
                {"file_path": DOCX_PATH, "offset": 2},
                "ValueError: page 2 is out of range for a 1 page document",
                True,
                True,
            )
        )
    )

    assert not verdict.passed
    assert not verdict.excluded
    assert "failed" in verdict.reason


async def test_the_docx_case_fails_when_the_render_answered_with_an_error() -> None:
    """A render the service answered is not an environment gap, whatever its status. Both failure
    texts carry the render URL, so the preview host alone cannot tell the two apart."""
    verdict = await DOCX_CASE.grader(
        _output(
            ToolInvocation(
                "read", {"file_path": DOCX_PATH, "offset": 2}, RENDER_ANSWERED_401, True, True
            )
        )
    )

    assert PREVIEW_HOST in RENDER_ANSWERED_401
    assert not verdict.passed
    assert not verdict.excluded
    assert "failed" in verdict.reason


def test_the_exclusion_marker_is_the_refusal_line_the_client_prints() -> None:
    """The suite excludes on the proxy's own refusal line, so a rewording in the client fails here
    instead of turning every answered render back into an exclusion."""
    assert RENDER_TUNNEL_REFUSED in TUNNEL_502
    assert RENDER_TUNNEL_REFUSED.replace(PREVIEW_HOST, "{host}") in EGRESS_SOURCE.read_text()
