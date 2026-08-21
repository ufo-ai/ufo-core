"""Adjacent artifact tasks that require correct routing and a successfully delivered artifact."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import (
    board_presentation_scorer,
    combine,
    forecast_workbook_scorer,
    required_tools_scorer,
    skill_scorer,
)

REVENUE = (120, 135, 142, 160)

CASES = (
    CapabilityCase(
        "house-style-reference",
        "State the exact house palette, typefaces, spacing steps, and corner radius that ufo uses. "
        "Read the source of truth before you answer.",
        skill_scorer("ufo-style", "website-building"),
        digest_tag="skill:house-style-reference",
    ),
    CapabilityCase(
        "forecast-assumption-model",
        "Build an editable assumption model for the finance team from Q1 revenue 120, Q2 135, "
        "Q3 142, and Q4 160. Separate Inputs and Forecast into named tabs, calculate "
        "quarter-over-quarter growth with formulas, add a revenue chart, recalculate the model, "
        "verify there are no formula errors, and share the finished artifact.",
        combine(
            skill_scorer("office-xlsx", "office-pptx"),
            required_tools_scorer(("xlsx_repl", "share_file"), (("xlsx_repl", "share_file"),)),
            forecast_workbook_scorer(REVENUE),
        ),
        digest_tag="skill:forecast-assumption-model",
    ),
    CapabilityCase(
        "board-visual-narrative",
        "Build a six-page visual narrative for the board from Q1 revenue 120, Q2 135, Q3 142, "
        "and Q4 160. Lead with an executive takeaway, include an editable revenue chart and "
        "speaker notes for every page, render and inspect the complete result, then share the "
        "finished artifact.",
        combine(
            skill_scorer("office-pptx", "office-xlsx"),
            required_tools_scorer(("bash", "share_file"), (("bash", "share_file"),)),
            board_presentation_scorer(6, REVENUE),
        ),
        digest_tag="skill:board-visual-narrative",
    ),
)
