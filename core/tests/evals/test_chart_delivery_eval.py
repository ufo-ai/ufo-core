from pathlib import Path

from evals.harness.artifact_checks import PNG_MAGIC, PNG_TRAILER
from evals.harness.capability import CapabilityOutput, SharedArtifact
from evals.suites.chart_delivery import charted_answer_scorer, unillustrated_answer_scorer

CHART_PNG = PNG_MAGIC + b"weekly-signups" + PNG_TRAILER
CHARTED_REPLY = (
    "Signups are still growing, about 6 percent a week: the twelve weeks run 118 to 205, and the "
    "last four weeks alone add 47."
)
PLAIN_REPLY = "July ads spend is 910 dollars above June, about 22 percent up."


def _carried(response: str, *artifacts: SharedArtifact) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=(), artifacts=artifacts)


async def test_a_charted_answer_passes_with_one_tagged_chart() -> None:
    grader = charted_answer_scorer(100, 6)

    verdict = await grader(
        _carried(CHARTED_REPLY, SharedArtifact("weekly-signups.png", CHART_PNG, "details"))
    )

    assert verdict.passed, verdict.reason
    assert verdict.evidence["carriedImages"] == [
        {"name": "weekly-signups.png", "role": "details", "bytes": len(CHART_PNG)}
    ]


async def test_a_charted_answer_fails_without_a_chart() -> None:
    grader = charted_answer_scorer(100, 6)

    verdict = await grader(_carried(CHARTED_REPLY))

    assert not verdict.passed
    assert "carried no chart image" in verdict.reason


async def test_a_charted_answer_fails_on_a_shared_chart_and_on_a_truncated_one() -> None:
    grader = charted_answer_scorer(100, 6)

    shared = await grader(
        _carried(CHARTED_REPLY, SharedArtifact("weekly-signups.png", CHART_PNG, "file"))
    )
    truncated = await grader(
        _carried(
            CHARTED_REPLY,
            SharedArtifact("weekly-signups.png", PNG_MAGIC + b"cut", "details"),
        )
    )

    assert not shared.passed
    assert "not an artifact tag" in shared.reason
    assert not truncated.passed
    assert "truncated" in truncated.reason


async def test_a_charted_answer_fails_one_chart_sent_and_carried_both() -> None:
    """The same PNG under both carriers is one chart delivered twice, not two charts."""
    grader = charted_answer_scorer(100, 6)

    verdict = await grader(
        _carried(
            CHARTED_REPLY,
            SharedArtifact("weekly-signups.png", CHART_PNG, "file"),
            SharedArtifact("weekly-signups.png", CHART_PNG, "details"),
        )
    )

    assert not verdict.passed
    assert "weekly-signups.png was both sent and carried" in verdict.reason


async def test_a_charted_answer_fails_two_charts_for_one_series() -> None:
    grader = charted_answer_scorer(100, 6)

    verdict = await grader(
        _carried(
            CHARTED_REPLY,
            SharedArtifact("weekly-signups.png", CHART_PNG, "details"),
            SharedArtifact("weekly-growth-rate.png", CHART_PNG, "details"),
        )
    )

    assert not verdict.passed
    assert "carried 2 charts for one series" in verdict.reason


async def test_a_charted_answer_fails_a_reply_that_sends_the_member_to_the_picture() -> None:
    grader = charted_answer_scorer(100, 6)

    verdict = await grader(
        _carried(
            "## Signups\n\n- The chart has the numbers.\n- Open it for the growth rate.",
            SharedArtifact("weekly-signups.png", CHART_PNG, "details"),
        )
    )

    assert not verdict.passed
    assert "section headers" in verdict.reason


async def test_an_under_floor_answer_passes_in_words_alone() -> None:
    grader = unillustrated_answer_scorer(60, 4)

    verdict = await grader(_carried(PLAIN_REPLY))

    assert verdict.passed, verdict.reason


async def test_an_under_floor_answer_fails_a_chart_it_carried_or_only_drew(tmp_path: Path) -> None:
    grader = unillustrated_answer_scorer(60, 4)
    (tmp_path / "ads.png").write_bytes(CHART_PNG)

    carried = await grader(_carried(PLAIN_REPLY, SharedArtifact("ads.png", CHART_PNG, "details")))
    drawn = await grader(CapabilityOutput(response=PLAIN_REPLY, calls=(), workspace_dir=tmp_path))

    assert not carried.passed
    assert "carried ads.png under the chart floor" in carried.reason
    assert not drawn.passed
    assert drawn.evidence["drawnImages"] == ["ads.png"]
