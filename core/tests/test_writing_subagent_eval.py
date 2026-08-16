import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation, grading_statement
from evals.registry import SEMANTIC_JUDGE_MODEL, TASKS
from evals.writing_subagent import CASES, LAUNCH_CASES, LaunchThreadGrader, TweetLanguageGrader

GOOD_TWEETS = (
    "1. CSV export shipped today for Pro workspaces. Pull up to 100,000 rows when you need them.\n"
    "2. Pro teams can export 100k CSV rows as of today. No launch theater, the button is live.\n"
    "3. Today's Pro release handles 100,000 rows per CSV export. Useful when the spreadsheet is "
    "already too big."
)


def _call(name: str, input: dict[str, object] | None = None) -> ToolInvocation:
    return ToolInvocation(name, input or {}, "done", True)


@pytest.mark.asyncio
async def test_tweet_language_grader_records_deterministic_nlp_evidence() -> None:
    grader = TweetLanguageGrader(
        (("csv",), ("100,000", "100k"), ("pro",), ("today",)), ("read", "edit")
    )
    output = CapabilityOutput(GOOD_TWEETS, (_call("read"), _call("edit")))

    verdict = await grader(output)

    assert verdict.passed
    assert verdict.evidence["contentDensity"] >= 0.45
    assert verdict.evidence["lexicalDiversity"] >= 0.4
    assert verdict.evidence["maxPairwiseOverlap"] <= 0.75
    assert verdict.evidence["openingDiversity"] == 1.0
    assert verdict.evidence["tweetCharacters"] == [88, 86, 104]


@pytest.mark.asyncio
async def test_tweet_language_grader_rejects_hype_repetition_and_missing_edit() -> None:
    grader = TweetLanguageGrader((("csv",), ("pro",)), ("read", "edit"))
    repeated = "\n".join(
        f"{index}. This isn't just a CSV export for Pro. This is a game changer!"
        for index in range(1, 4)
    )

    verdict = await grader(CapabilityOutput(repeated, (_call("read"),)))

    assert not verdict.passed
    assert "missing successful edit" in verdict.reason
    assert "game changer" in verdict.reason
    assert "pairwise overlap" in verdict.reason
    assert "tweet openings repeat" in verdict.reason


@pytest.mark.asyncio
async def test_tweet_language_grader_excludes_required_facts_from_overlap() -> None:
    grader = TweetLanguageGrader(
        (("csv",), ("100,000", "100k"), ("pro",), ("today",)), ("read", "edit")
    )
    options = (
        "1. Pro users can export up to 100,000 rows to CSV today.\n"
        "2. CSV export is available today for Pro, with up to 100,000 rows.\n"
        "3. Pro now has CSV export for up to 100,000 rows. It is available today."
    )

    verdict = await grader(CapabilityOutput(options, (_call("read"), _call("edit"))))

    assert verdict.passed
    assert verdict.evidence["maxPairwiseOverlap"] <= 0.75


@pytest.mark.asyncio
async def test_writing_cases_require_the_profile_and_semantic_judge() -> None:
    task = {task.name: task for task in TASKS}["writing_subagent"]
    assert task.judge_model == SEMANTIC_JUDGE_MODEL
    assert len(CASES) == 2
    assert len(LAUNCH_CASES) == 1
    assert all(len(case.rubric) == 3 for case in (*CASES, *LAUNCH_CASES))
    assert all(case.samples == 3 for case in CASES)

    scorer = CASES[0].grader
    no_delegation = await scorer(CapabilityOutput(GOOD_TWEETS, ()))
    delegated = await scorer(
        CapabilityOutput(GOOD_TWEETS, (_call("spawn", {"target": "writing"}),))
    )

    assert not no_delegation.passed
    assert delegated.passed
    assert "content-word density" in grading_statement(scorer)


@pytest.mark.asyncio
async def test_launch_thread_grader_requires_the_complete_thread_and_edit() -> None:
    grader = LaunchThreadGrader()
    thread = "\n\n".join(
        (
            "1/5 UFO.ai builds your most important work.",
            "[they will never see you coming embed]",
            "2/5 UFO Version 1 released today is a frontier hosted multiplayer AI harness on "
            "Slack, terminal, and web. It brings us to parity with ChatGPT Work, Claude Tag, and "
            "managed agents.",
            "3/5 UFO is self-aware and self-improving: a connective cognitive layer between "
            "systems that responds to events and makes decisions.",
            "4/5 The UFO behind UFO.ai handles recruiting, marketing, system monitoring, "
            "experiments, deploys, competitive analysis, and feature development.",
            "[some hud of parallel complexity]",
            "5/5 Our microteam raised $30M from YC and Garry Tan, Transpose Platform, Perplexity "
            "Fund, Michael Ovitz, and Blake Byers. Work with us: alex@ufo.ai",
        )
    )

    verdict = await grader(CapabilityOutput(thread, (_call("read"), _call("edit"))))

    assert verdict.passed
    assert verdict.evidence["postCharacters"] == [39, 175, 127, 140, 143]
    assert verdict.evidence["lexicalDiversity"] >= 0.55

    incomplete = await grader(CapabilityOutput(thread.replace("Claude Tag", ""), ()))
    assert not incomplete.passed
    assert "missing successful read" in incomplete.reason
    assert "missing successful edit or write" in incomplete.reason
    assert "claude tag" in incomplete.reason

    overwrite = await grader(CapabilityOutput(thread, (_call("read"), _call("write"))))
    assert overwrite.passed

    missing_product = await grader(
        CapabilityOutput(
            thread.replace("UFO.ai builds your most important work.", "UFO.ai released today."),
            (_call("read"), _call("edit")),
        )
    )
    assert not missing_product.passed
    assert "builds your most important work/builds important work" in missing_product.reason

    generic = await grader(
        CapabilityOutput(
            thread.replace(
                "UFO.ai builds your most important work.",
                "Every company, project, and person is unique. Today we release UFO.ai.",
            ),
            (_call("read"), _call("edit")),
        )
    )
    assert not generic.passed
    assert "every company, project, and person is unique" in generic.reason

    announcement = await grader(
        CapabilityOutput(
            thread.replace(
                "UFO.ai builds your most important work.",
                "Today, we're announcing UFO.ai, which builds your most important work.",
            ),
            (_call("read"), _call("edit")),
        )
    )
    assert not announcement.passed
    assert "today we're announcing" in announcement.reason

    announce = await grader(
        CapabilityOutput(
            thread.replace(
                "UFO.ai builds your most important work.",
                "Today we announce UFO.ai, which builds your most important work.",
            ),
            (_call("read"), _call("edit")),
        )
    )
    assert not announce.passed
    assert "today we announce ufo.ai" in announce.reason

    launch = await grader(
        CapabilityOutput(
            thread.replace(
                "UFO.ai builds your most important work.",
                "Today we launch UFO.ai to build your most important work.",
            ),
            (_call("read"), _call("edit")),
        )
    )
    assert not launch.passed
    assert "today we " in launch.reason

    moved_media = thread.replace(
        "4/5 The UFO behind UFO.ai handles recruiting, marketing, system monitoring, "
        "experiments, deploys, competitive analysis, and feature development.\n\n"
        "[some hud of parallel complexity]",
        "[some hud of parallel complexity]\n\n"
        "4/5 The UFO behind UFO.ai handles recruiting, marketing, system monitoring, "
        "experiments, deploys, competitive analysis, and feature development.",
    )
    misplaced = await grader(CapabilityOutput(moved_media, (_call("read"), _call("edit"))))
    assert not misplaced.passed
    assert "media placeholders moved from their intended posts" in misplaced.reason
