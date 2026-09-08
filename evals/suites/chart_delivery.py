"""Chart cases: what a member actually receives when an answer rests on a series of numbers.

The register's rule has a floor — five or more numbers in one measure — so the cases run in opposing
pairs: four asks above the floor that must put a chart in front of the member, two below it that
must stay in words. The four-quarter case sits one point under the floor, so a rule firing on
"there are numbers here" fails it while the rule as written passes.

The four above the floor are four shapes, because the shape decides whether the turn ever draws. Two
carry their numbers in the member's own message and are answered from a standing start. One is a
thread follow-up: the numbers arrived a turn ago and an answer is already in the transcript, so
nothing forces the agent into the sandbox and drawing costs a round it can skip — the shape both
live misses took (2026-09-07, #ufo-eng). One holds its numbers in a workspace file, so the figures
reach the answer through a tool rather than from the words the member typed.

**A chart the member never receives is the failure this suite exists to name.** Three states are
distinguished, because they are three faults and the earlier grader collapsed them into one "carried
no chart image":

- nothing drawn and nothing delivered — the turn never took the round;
- a PNG in the workspace with nothing on the turn — the turn drew it and the delivery dropped it,
  which the engine logs as `turn.carried_file_unavailable` and no member can see;
- a reply that speaks of a chart with nothing on the turn — the turn claimed a drawing that does not
  exist, which is worse than silence and is what shipped to Slack in turn
  `a09b64a6-6250-52cd-93f1-7adcbd1ced10`.

Either carrier counts as delivered, and the role is recorded rather than graded. The role decides
what a surface draws — `details` renders as a link on web and Slack, `file` as a picture — but that
is a rendering fault of its own, not one to fail a turn for.

The reply is graded for its budget, its headers and any /workspace path, and not for bullets: the
register gives parallel items a bullet path, both live replies took it legitimately, and failing
them for it made the pass counts stop tracking the behaviour under test.

Each above-floor case carries `samples=3` and each under-floor case `samples=2`: whether one reply
draws a chart swings wider than the effect a wording change produces, and the above-floor side is
where that swing lives.

The suite runs off the nightly sweep. A chart is drawn with the sandbox image's matplotlib, and the
sweep gives a non-app shard the local carrier, which runs host subprocesses with no plotting library
at all — so a nightly run would score the shard's environment rather than the model. The ablation
harness names `sandbox = { backend = "docker" }` and is where this suite measures."""

import re

from evals.harness.artifact_checks import valid_image
from evals.harness.capability import (
    PAGE_IMAGE_SUFFIXES,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    SharedArtifact,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject
from evals.suites.response_register import HEADER_RE, WORKSPACE_PATH_RE, measure

WORKFLOW_WAIT_SECONDS = 600.0
CHART_CLAIM_RE = re.compile(r"\bchart(?:ed|s)?\b|\bplot(?:ted|s)?\b|\bgraph(?:ed|s)?\b", re.I)
CHART_CRITERIA = (
    "A reader can tell what is plotted and in what units from the title, the axis labels and the "
    "tick labels together, and the title names the subject rather than restating an axis. A "
    "categorical axis whose ticks name the categories needs no further label; a value axis whose "
    "numbers could stand for anything must say what they count.",
    "Every tick label and legend entry is fully readable: not clipped at an edge, not overlapping "
    "another label, and not shrunk past legibility.",
    "The plotted values are the member's own series, drawn in proportion — no invented points, no "
    "truncated axis that exaggerates the movement, and nothing cut off at a boundary.",
)
DAILY_TURNS = (
    "daily production turns last week: Mon 5, Tue 3, Wed 7, Thu 182, Fri 369, Sat 86, Sun 77",
    "Got it — 729 for the week, about 104 a day.",
)
TURNS_CSV = WorkspaceFile(
    "production-turns.csv",
    b"day,turns\nMon,5\nTue,3\nWed,7\nThu,182\nFri,369\nSat,86\nSun,77\n",
)
DAILY_CUT = (
    "how many turns did production run last week? the daily numbers are in "
    "/workspace/production-turns.csv",
    "729 for the week, about 104 a day.",
)
SIGNUP_CSV = WorkspaceFile(
    "weekly-signups.csv",
    b"week,signups\n1,118\n2,131\n3,127\n4,144\n5,139\n6,152\n7,166\n8,158\n9,174\n10,191\n"
    b"11,187\n12,205\n",
)


def _delivered(output: CapabilityOutput) -> tuple[SharedArtifact, ...]:
    """Every image the turn attached to itself, by either carrier. `file` is a share_file share and
    `details` a closing-message artifact tag; both put the file on the turn, and which one decides
    whether a surface draws a picture or a link."""
    return tuple(
        artifact
        for artifact in output.artifacts
        if artifact.name.lower().endswith(PAGE_IMAGE_SUFFIXES)
    )


def _drawn(output: CapabilityOutput) -> tuple[str, ...]:
    """Every image the turn left in its own workspace, delivered or not. Read against the delivered
    set, it separates a turn that never drew from one whose chart was dropped on the way out."""
    if output.workspace_dir is None:
        return ()
    return tuple(
        sorted(
            path.name
            for path in output.workspace_dir.rglob("*")
            if path.is_file() and path.name.lower().endswith(PAGE_IMAGE_SUFFIXES)
        )
    )


def _reply_faults(response: str, max_words: int) -> list[str]:
    """The reply's own shape: its budget, its headers, and any workspace path it exposes. Bullets
    are not a fault — the register gives parallel items a bullet path."""
    faults = []
    words = measure(response).words
    headers = HEADER_RE.findall(response)
    paths = WORKSPACE_PATH_RE.findall(response)
    if words > max_words:
        faults.append(f"{words} words over the {max_words} budget")
    if headers:
        faults.append(f"{len(headers)} section headers")
    if paths:
        faults.append("member-inaccessible workspace paths: " + ", ".join(paths))
    return faults


def charted_answer_scorer(max_words: int) -> Grader:
    """A reply inside its budget that puts one chart in front of the member."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        response = output.response.strip()
        failures = _reply_faults(response, max_words)
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        delivered = _delivered(output)
        drawn = _drawn(output)
        names = {artifact.name for artifact in delivered}
        claimed = bool(CHART_CLAIM_RE.search(response))
        if not names and drawn:
            failures.append(f"drew {', '.join(drawn)} and delivered none of it")
        elif not names and claimed:
            failures.append("spoke of a chart the member never received")
        elif not names:
            failures.append("drew no chart")
        elif len(names) > 1:
            failures.append(f"delivered {len(names)} charts for one series")
        else:
            corrupt = next(
                (item for item in delivered if not valid_image(item.content).passed), None
            )
            if corrupt is not None:
                failures.append(f"{corrupt.name}: {valid_image(corrupt.content).reason}")
        evidence: JsonObject = {
            "words": measure(response).words,
            "deliveredCharts": [
                {"name": item.name, "role": item.role, "bytes": len(item.content)}
                for item in delivered
            ],
            "drawnInWorkspace": list(drawn),
            "spokeOfAChart": claimed,
        }
        if failures:
            return CapabilityVerdict(False, "charted answer: " + ", ".join(failures), evidence)
        return CapabilityVerdict(
            True,
            f"charted answer: {delivered[0].name} delivered as {delivered[0].role}",
            evidence,
        )

    return DescribedGrader(
        f"a reply inside {max_words} words, with no section header and no /workspace path, "
        "delivering exactly one valid chart to the member by either carrier",
        grade,
    )


def unillustrated_answer_scorer(max_words: int) -> Grader:
    """A reply under the floor: no chart delivered, none drawn, and none spoken of."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        response = output.response.strip()
        failures = _reply_faults(response, max_words)
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        delivered = _delivered(output)
        drawn = _drawn(output)
        claimed = bool(CHART_CLAIM_RE.search(response))
        if delivered:
            failures.append(
                "delivered " + ", ".join(item.name for item in delivered) + " under the floor"
            )
        if drawn:
            failures.append("drew " + ", ".join(drawn) + " under the floor")
        if claimed:
            failures.append("spoke of a chart under the floor")
        evidence: JsonObject = {
            "words": measure(response).words,
            "deliveredCharts": [item.name for item in delivered],
            "drawnInWorkspace": list(drawn),
            "spokeOfAChart": claimed,
        }
        if failures:
            return CapabilityVerdict(
                False, "unillustrated answer: " + ", ".join(failures), evidence
            )
        return CapabilityVerdict(True, f"unillustrated answer: {evidence['words']} words", evidence)

    return DescribedGrader(
        f"a reply inside {max_words} words that delivers no chart, draws none, and claims none",
        grade,
    )


CASES = (
    CapabilityCase(
        "series-over-period",
        "Weekly signups for the last twelve weeks, oldest first: 118, 131, 127, 144, 139, 152, "
        "166, 158, 174, 191, 187, 205. Are we still growing, and how fast?",
        charted_answer_scorer(max_words=120),
        digest_tag="chart:series-over-period",
        samples=3,
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "spend-across-categories",
        "Last month's software spend, in dollars: AWS 5400, Datadog 3900, Slack 1240, GitHub 640, "
        "Vercel 520, Figma 480, Zoom 300, Notion 210, Linear 180. How concentrated is that?",
        charted_answer_scorer(max_words=120),
        digest_tag="chart:spend-across-categories",
        samples=3,
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "thread-followup-week",
        "what does that week actually look like?",
        charted_answer_scorer(max_words=120),
        digest_tag="chart:thread-followup-week",
        samples=3,
        prior_messages=DAILY_TURNS,
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "file-sourced-series",
        "/workspace/weekly-signups.csv has our signups by week. What is the trend?",
        charted_answer_scorer(max_words=120),
        digest_tag="chart:file-sourced-series",
        samples=3,
        workspace_files=(SIGNUP_CSV,),
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "thread-followup-daily-cut",
        "what about each day?",
        charted_answer_scorer(max_words=120),
        digest_tag="chart:thread-followup-daily-cut",
        samples=3,
        prior_messages=DAILY_CUT,
        workspace_files=(TURNS_CSV,),
        visual_rubric=CHART_CRITERIA,
    ),
    CapabilityCase(
        "two-month-comparison",
        "We spent 4180 dollars on ads in June and 5090 in July. How much is that up?",
        unillustrated_answer_scorer(max_words=60),
        digest_tag="chart:two-month-comparison",
        samples=2,
    ),
    CapabilityCase(
        "four-quarter-total",
        "Revenue by quarter last year was 210k, 244k, 231k, and 268k. Did we finish the year "
        "ahead of where we started?",
        unillustrated_answer_scorer(max_words=60),
        digest_tag="chart:four-quarter-total",
        samples=2,
    ),
)
