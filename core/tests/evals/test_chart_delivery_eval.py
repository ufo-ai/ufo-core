from pathlib import Path

from evals.harness.artifact_checks import PNG_MAGIC, PNG_TRAILER
from evals.harness.capability import CapabilityOutput, SharedArtifact
from evals.suites.chart_delivery import charted_answer_scorer, unillustrated_answer_scorer

CHART_PNG = PNG_MAGIC + b"weekly-signups" + PNG_TRAILER
CHARTED_REPLY = (
    "Signups are still growing, about 6 percent a week: the twelve weeks run 118 to 205, and the "
    "last four weeks alone add 47."
)
CLAIMING_REPLY = (
    "Seated workspaces went 54 to 113 over the week, which is what I have charted; the growth all "
    "lands Thursday through Saturday."
)
PLAIN_REPLY = "July ads spend is 910 dollars above June, about 22 percent up."


def _delivered(response: str, *artifacts: SharedArtifact) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=(), artifacts=artifacts)


async def test_a_chart_delivered_by_either_carrier_passes() -> None:
    """The role decides what a surface draws, and that is a rendering fault, not this one."""
    grader = charted_answer_scorer(120)

    tagged = await grader(
        _delivered(CHARTED_REPLY, SharedArtifact("signups.png", CHART_PNG, "details"))
    )
    shared = await grader(
        _delivered(CHARTED_REPLY, SharedArtifact("signups.png", CHART_PNG, "file"))
    )

    assert tagged.passed, tagged.reason
    assert shared.passed, shared.reason
    assert tagged.evidence["deliveredCharts"][0]["role"] == "details"
    assert shared.evidence["deliveredCharts"][0]["role"] == "file"


async def test_a_turn_that_never_drew_is_named_as_such() -> None:
    grader = charted_answer_scorer(120)

    verdict = await grader(_delivered(CHARTED_REPLY))

    assert not verdict.passed
    assert "drew no chart" in verdict.reason


async def test_a_drawn_chart_that_was_never_delivered_is_its_own_failure(tmp_path: Path) -> None:
    """The engine logs this one as turn.carried_file_unavailable and ships the words regardless."""
    grader = charted_answer_scorer(120)
    (tmp_path / "signups.png").write_bytes(CHART_PNG)

    verdict = await grader(
        CapabilityOutput(response=CHARTED_REPLY, calls=(), workspace_dir=tmp_path)
    )

    assert not verdict.passed
    assert "drew signups.png and delivered none of it" in verdict.reason
    assert verdict.evidence["drawnInWorkspace"] == ["signups.png"]


async def test_a_claimed_chart_with_nothing_behind_it_is_its_own_failure() -> None:
    """The Slack fault: prose says it charted, no file exists, and the member sees the claim."""
    grader = charted_answer_scorer(120)

    verdict = await grader(_delivered(CLAIMING_REPLY))

    assert not verdict.passed
    assert "spoke of a chart the member never received" in verdict.reason
    assert verdict.evidence["spokeOfAChart"] is True


async def test_two_charts_for_one_series_fail() -> None:
    grader = charted_answer_scorer(120)

    verdict = await grader(
        _delivered(
            CHARTED_REPLY,
            SharedArtifact("signups.png", CHART_PNG, "details"),
            SharedArtifact("growth-rate.png", CHART_PNG, "details"),
        )
    )

    assert not verdict.passed
    assert "delivered 2 charts for one series" in verdict.reason


async def test_a_truncated_chart_fails() -> None:
    grader = charted_answer_scorer(120)

    verdict = await grader(
        _delivered(CHARTED_REPLY, SharedArtifact("signups.png", PNG_MAGIC + b"cut", "details"))
    )

    assert not verdict.passed
    assert "truncated" in verdict.reason


async def test_bullets_are_not_a_fault_but_headers_and_paths_are() -> None:
    """The register gives parallel items a bullet path; failing them for it hid the real signal."""
    grader = charted_answer_scorer(120)
    chart = SharedArtifact("signups.png", CHART_PNG, "details")

    bulleted = await grader(
        _delivered("Growth held all quarter.\n- Q1 was 118.\n- Q4 was 205.", chart)
    )
    headed = await grader(_delivered("## Signups\n\nGrowth held all quarter.", chart))
    pathed = await grader(_delivered("The picture is at /workspace/signups.png.", chart))

    assert bulleted.passed, bulleted.reason
    assert not headed.passed
    assert "1 section headers" in headed.reason
    assert not pathed.passed
    assert "/workspace/signups.png" in pathed.reason


async def test_an_under_floor_answer_passes_in_words_alone() -> None:
    grader = unillustrated_answer_scorer(60)

    verdict = await grader(_delivered(PLAIN_REPLY))

    assert verdict.passed, verdict.reason


async def test_an_under_floor_answer_fails_a_chart_delivered_drawn_or_claimed(
    tmp_path: Path,
) -> None:
    grader = unillustrated_answer_scorer(60)
    (tmp_path / "ads.png").write_bytes(CHART_PNG)

    delivered = await grader(
        _delivered(PLAIN_REPLY, SharedArtifact("ads.png", CHART_PNG, "details"))
    )
    drawn = await grader(CapabilityOutput(response=PLAIN_REPLY, calls=(), workspace_dir=tmp_path))
    claimed = await grader(_delivered(f"{PLAIN_REPLY} I charted it for you."))

    assert not delivered.passed
    assert "delivered ads.png under the floor" in delivered.reason
    assert not drawn.passed
    assert "drew ads.png under the floor" in drawn.reason
    assert not claimed.passed
    assert "spoke of a chart under the floor" in claimed.reason
