"""The digest cases pin the entry's shape without binding its length to the report's bullets."""

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.report_digest import CASES, MAX_POINTS, REPLY_SHAPE, WEEKLY_REPORT

WEEKLY_CASE = next(case for case in CASES if case.name == "report-digest-attributed-findings")
LOADED = ToolInvocation("load_skill", {"name": "report-digest"}, "# Skill: report-digest", True)
RANKED_ENTRY = (
    "Title: Staging certificate renewal blocked before 28 August expiry\n"
    "Summary: Chase the unanswered vendor ticket today.\n"
    "Point: Certificate renewal waits on the vendor | Actor: Marco Ruiz\n"
    "Point: Deploys sped from 41 to 19 minutes | Actor: Dana Okafor"
)
MIRRORED_ENTRY = (
    "Title: Staging certificate renewal blocked before 28 August expiry\n"
    "Summary: Chase the unanswered vendor ticket today.\n"
    "Point: Certificate renewal waits on the vendor | Actor: Marco Ruiz\n"
    "Point: Deploys sped from 41 to 19 minutes | Actor: Dana Okafor\n"
    "Point: Reindex sped from 50 to 12 minutes | Actor: Ines Bertrand\n"
    "Point: Headcount unchanged | Actor: "
)


def _output(text: str) -> CapabilityOutput:
    return CapabilityOutput(text, (LOADED,))


def test_the_reply_shape_does_not_bind_the_point_count_to_the_report() -> None:
    assert "line per point" not in REPLY_SHAPE
    assert "your entry" in REPLY_SHAPE


def test_the_weekly_report_carries_more_bullets_than_the_grader_admits_points() -> None:
    assert WEEKLY_REPORT.count("\n- ") > MAX_POINTS


async def test_the_weekly_case_passes_a_ranked_entry_shorter_than_the_report() -> None:
    verdict = await WEEKLY_CASE.grader(_output(RANKED_ENTRY))

    assert verdict.passed


async def test_the_weekly_case_fails_an_entry_that_mirrors_every_bullet() -> None:
    verdict = await WEEKLY_CASE.grader(_output(MIRRORED_ENTRY))

    assert not verdict.passed
    assert f"at most {MAX_POINTS} points" in verdict.reason
