"""Chart cases: a member delivery that rests on a series of numbers carries a picture of them.

The register sets a floor — one measure at five or more points — so the cases run in opposing
pairs: two asks whose answer clears the floor and must arrive with one charted PNG carried in an
artifact tag, two that sit under it and must arrive in words alone. A suite that only rewarded the
chart would score highest on a turn that plots a pair of numbers, which is the failure the floor
exists to stop; the four-quarter case sits one point under the floor, so a rule that fires on
"there are numbers here" fails it while the rule as written passes.

Every case's numbers arrive in the member's own message, so no case turns on workspace state, a
connected account, or a tool result: what is measured is which shape the delivery takes, never what
the agent could find out. Every ask is single-subject: an ask that invites parallel items earns the
register's bullet path, and the case then measures the bullet rule instead of the chart.

The reply is graded to that register beside the image, because a chart is not licence to abandon
the words: a member who reads only the message still gets the conclusion, and a reply that points
at the picture for the answer fails on the same 100-word budget the register gives a member
delivery. The chart must arrive as a `details` artifact — the tag's own carrier — since an
unrequested share_file is a share the ask never triggered, and the same PNG sent both ways puts the
picture in front of the member twice.

The vision judge reads the shared PNG on the two charted cases. Its criteria are the ones the
register names and the ones a matplotlib default breaks: an axis with no measure or unit on it, a
tick label too small or too rotated to read, a title that names no subject.

Each case carries `samples=2` because whether one reply draws a chart swings wider than the effect
a wording change produces.

The suite runs off the nightly sweep. A chart is drawn with the sandbox image's matplotlib, and the
sweep gives a non-app shard the local carrier, which runs host subprocesses with no plotting library
at all — so a nightly run would score the shard's environment rather than the model. The ablation
harness names `sandbox = { backend = "docker" }` and is where this suite measures."""

from evals.harness.artifact_checks import valid_image
from evals.harness.capability import (
    PAGE_IMAGE_SUFFIXES,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    SharedArtifact,
)
from evals.harness.harness import JsonObject
from evals.suites.response_register import conversational_scorer

WORKFLOW_WAIT_SECONDS = 600.0
CARRIED_ROLE = "details"
CHART_CRITERIA = (
    "Every axis carries a label naming what it measures and the unit it is measured in, and the "
    "chart has a title that names the subject rather than restating the axis.",
    "Every tick label and legend entry is fully readable: not clipped at an edge, not overlapping "
    "another label, and not shrunk past legibility.",
    "The plotted values are the member's own series, drawn in proportion — no invented points, no "
    "truncated axis that exaggerates the movement, and nothing cut off at a boundary.",
)


def _images(output: CapabilityOutput) -> tuple[SharedArtifact, ...]:
    return tuple(
        artifact
        for artifact in output.artifacts
        if artifact.name.lower().endswith(PAGE_IMAGE_SUFFIXES)
    )


def _drawn_images(output: CapabilityOutput) -> tuple[str, ...]:
    """Every image file the turn left in its own workspace, delivered or not. A chart drawn under
    the floor and then withheld is still the work the floor exists to stop, and the reply alone
    cannot show it."""
    if output.workspace_dir is None:
        return ()
    return tuple(
        sorted(
            path.name
            for path in output.workspace_dir.rglob("*")
            if path.is_file() and path.name.lower().endswith(PAGE_IMAGE_SUFFIXES)
        )
    )


def charted_answer_scorer(max_words: int, max_lines: int) -> Grader:
    """A chat-register reply carrying one chart, once, in an artifact tag.

    A chart is a file, not a row: the same PNG reaches a member twice when the turn calls
    share_file and carries the tag as well, and counting rows read that as two charts. So the count
    is over distinct names, and the double delivery is its own failure — the member sees the same
    picture twice, which is the fault, not that two rows exist."""
    conversational = conversational_scorer(max_words, max_lines)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await conversational(output)
        failures = [] if verdict.passed else [verdict.reason]
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        images = _images(output)
        names = {image.name for image in images}
        carried = {image.name for image in images if image.role == CARRIED_ROLE}
        sent = {image.name for image in images if image.role != CARRIED_ROLE}
        if not names:
            failures.append("carried no chart image")
        elif len(names) > 1:
            failures.append(f"carried {len(names)} charts for one series")
        else:
            corrupt = next(
                (image for image in images if not valid_image(image.content).passed), None
            )
            if corrupt is not None:
                failures.append(f"{corrupt.name}: {valid_image(corrupt.content).reason}")
            if carried & sent:
                failures.append(f"{', '.join(carried & sent)} was both sent and carried")
            elif not carried:
                failures.append(f"{', '.join(sent)} arrived as a file, not an artifact tag")
        evidence: JsonObject = verdict.evidence | {
            "carriedImages": [
                {"name": image.name, "role": image.role, "bytes": len(image.content)}
                for image in images
            ]
        }
        if failures:
            return CapabilityVerdict(False, "charted answer: " + ", ".join(failures), evidence)
        return CapabilityVerdict(
            True, f"charted answer: {verdict.reason}, {names.pop()} carried", evidence
        )

    return DescribedGrader(
        f"a chat-register reply — at most {max_words} words and {max_lines} lines, no section "
        "headers, no bullet list — carrying one valid chart, once, as a tagged artifact",
        grade,
    )


def unillustrated_answer_scorer(max_words: int, max_lines: int) -> Grader:
    """A chat-register reply to a series under the floor: no chart carried, and none drawn."""
    conversational = conversational_scorer(max_words, max_lines)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await conversational(output)
        failures = [] if verdict.passed else [verdict.reason]
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        carried = _images(output)
        drawn = _drawn_images(output)
        if carried:
            failures.append(
                "carried " + ", ".join(image.name for image in carried) + " under the chart floor"
            )
        if drawn:
            failures.append("drew " + ", ".join(drawn) + " under the chart floor")
        evidence: JsonObject = verdict.evidence | {
            "carriedImages": [image.name for image in carried],
            "drawnImages": list(drawn),
        }
        if failures:
            return CapabilityVerdict(
                False, "unillustrated answer: " + ", ".join(failures), evidence
            )
        return CapabilityVerdict(True, f"unillustrated answer: {verdict.reason}", evidence)

    return DescribedGrader(
        f"a chat-register reply — at most {max_words} words and {max_lines} lines, no section "
        "headers, no bullet list — that carries no image and leaves none in the workspace",
        grade,
    )


CASES = (
    CapabilityCase(
        "series-over-period",
        "Weekly signups for the last twelve weeks, oldest first: 118, 131, 127, 144, 139, 152, "
        "166, 158, 174, 191, 187, 205. Are we still growing, and how fast?",
        charted_answer_scorer(max_words=100, max_lines=6),
        digest_tag="chart:series-over-period",
        samples=2,
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "spend-across-categories",
        "Last month's software spend, in dollars: AWS 5400, Datadog 3900, Slack 1240, GitHub 640, "
        "Vercel 520, Figma 480, Zoom 300, Notion 210, Linear 180. How concentrated is that?",
        charted_answer_scorer(max_words=100, max_lines=6),
        digest_tag="chart:spend-across-categories",
        samples=2,
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "two-month-comparison",
        "We spent 4180 dollars on ads in June and 5090 in July. How much is that up?",
        unillustrated_answer_scorer(max_words=60, max_lines=4),
        digest_tag="chart:two-month-comparison",
        samples=2,
    ),
    CapabilityCase(
        "four-quarter-total",
        "Revenue by quarter last year was 210k, 244k, 231k, and 268k. Did we finish the year "
        "ahead of where we started?",
        unillustrated_answer_scorer(max_words=60, max_lines=4),
        digest_tag="chart:four-quarter-total",
        samples=2,
    ),
)
