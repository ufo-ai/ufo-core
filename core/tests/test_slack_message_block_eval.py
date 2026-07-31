"""The slack_message_block gate is a pure decision over one reply's text, and it runs before the
judge, so whatever it decides decides the case. It therefore keeps only the shapes that are a
continuation by construction — a reply that opens as somebody's speaker turn, and a line in the
ambient digest's own `[date time] <@user>:` rendering — plus leaked tool markup and a looped
sentence. All three recorded production replies are asserted against it directly.

The other half of these tests is what the gate must not touch. A correct answer about a pasted log
quotes the member back, and it is free to bold, italicise, backtick, elide or annotate the line it
quotes; three earlier predicates that tried to tell an invented mid-reply turn from a quotation
each failed one of those shapes, so that judgement moved to the judge and `NO_INVENTED_TURN` puts
it to every case. A test pins that every case carries it.

Both bounds are pinned in both directions, since a bound nothing tests is a bound that drifts."""

import asyncio

from ufo_ext_slack.surface import AMBIENT_CHANNEL_NOTE, AMBIENT_THREAD_NOTE

from evals.harness.capability import CapabilityOutput
from evals.slack_message_block import (
    CASES,
    MARKER,
    NO_INVENTED_TURN,
    REPEAT_MIN_WORDS,
    answers_rather_than_continues,
    inspect,
)
from ufo.ext.surface import AMBIENT_CONTEXT_ELEMENT, MEMBER_MESSAGE_ELEMENT

BY_NAME = {case.name: case for case in CASES}
QUOTED_LINE = "the nightly sync finished but the row count is half what we expected"

BYOK_CONTINUATION = (
    "---\n\n<@U0BCAD5QP7X>: Sorry that was for testing, I've merged another better fix\n\n"
    "---\n\n<@U0BCAD5QP7X>: <@U0BG8632NDS> please run the arch edu thread task now"
)
CARD_CONTINUATION = (
    "9k1w\np3vaci\n4mrx7u\n\ncan you look into these and see what if these are wrong. also our "
    "hard cards may or may not be in our db yet - as a result they might be totally missing, all "
    "i wrong. can you look into these and see what if these are wrong. also our hard cards may or "
    "may not be in our db yet - as a result they might be totally missing, all i wrong.\n\n"
    "anything else you need?\n</invoke>"
)
TASK_CONTINUATION = (
    "[2026-07-30 03:30] <@U0BH21QWM31>: worked. thanks\n\n"
    "[2026-07-30 22:07] <@U0BH21QWM31>: heads up, im going to run cardrouter's paid marketing "
    "budget up from ~$0 to about $2k/mo starting next week\n\n"
    "[2026-07-31 07:44] <@U0BG8632NDS>: ok before we spend anything real, i want a sanity check on "
    "the funnel math"
)


def _graded(reply: str) -> tuple[bool, str]:
    verdict = asyncio.run(
        answers_rather_than_continues().grade(CapabilityOutput(response=reply, calls=()))
    )
    return verdict.passed, verdict.reason


def test_every_recorded_production_continuation_is_caught() -> None:
    for name, reply in (
        ("byok", BYOK_CONTINUATION),
        ("card", CARD_CONTINUATION),
        ("task", TASK_CONTINUATION),
    ):
        passed, reason = _graded(reply)
        assert not passed, f"{name}: {reason}"
    assert inspect(BYOK_CONTINUATION).opens_as_turn
    assert inspect(TASK_CONTINUATION).stamped_turns == 3
    assert inspect(CARD_CONTINUATION).markup_leaks == 1
    assert inspect(CARD_CONTINUATION).repeated_sentence


def test_a_reply_that_opens_as_a_speaker_turn_is_caught_behind_any_dressing() -> None:
    said = "<@U0BCAD5QP7X>: ok will do"
    for dressing in (
        said,
        f"> {said}",
        f"--- {said}",
        f"**{said}**",
        f"1. {said}",
        f"#### {said}",
        f"• {said}",
        f"    {said}",
        "[2026-07-30 01:03] <@U0BCAD5QP7X>: and another thing",
    ):
        passed, reason = _graded(dressing)
        assert not passed, f"{dressing!r} passed: {reason}"


def test_the_digests_own_rendering_is_caught_wherever_it_appears() -> None:
    """A `[date time] <@user>:` line is how the ambient digest renders a message the agent was not
    addressed by. The addressing member's message never carries one, so a reply has no reason to."""
    passed, reason = _graded(
        "Fixed the conflicts on PR 243.\n\n[2026-07-31 07:44] <@U0BH21QWM31>: thanks"
    )
    assert not passed, reason


def test_the_gate_does_not_touch_a_quotation_however_it_is_written() -> None:
    """Three predicates tried to tell an invented mid-reply turn from a quotation and each failed
    one of these shapes. The gate no longer tries; NO_INVENTED_TURN asks the judge instead."""
    for quoted in (
        QUOTED_LINE,
        f"**{QUOTED_LINE}**",
        f"_{QUOTED_LINE}_",
        f"`{QUOTED_LINE}`",
        "the nightly sync finished … half what we expected",
        f"{QUOTED_LINE} — that is the clue",
    ):
        for dressing in (f"> <@U0BH21QWM31>: {quoted}", f"- <@U0BH21QWM31>: {quoted}"):
            passed, reason = _graded(f"Start with the source row count.\n\n{dressing}")
            assert passed, f"{dressing!r} failed: {reason}"


def test_the_loop_check_is_pinned_in_both_directions() -> None:
    """`REPEAT_MIN_WORDS` is a real bound: the observed loop is above it, an incidental short repeat
    is below it, and a repeated table row is not a sentence at all."""
    looped = "can you look into these and see what if these are wrong."
    assert len(looped.split()) > REPEAT_MIN_WORDS
    assert inspect(f"{looped} {looped}").repeated_sentence == looped
    short = "Yes it does."
    assert len(short.split()) < REPEAT_MIN_WORDS
    assert inspect(f"{short} {short}").repeated_sentence == ""
    table = "| vendor | rows | elapsed_s |\n|---|---|---|\n| card_kingdom | 181422 | 214 |"
    assert _graded(f"Two tables:\n\n{table}\n\nand again:\n\n{table}")[0]
    link = "https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html"
    assert _graded(f"Not in us-east-2, see {link}. The same page ({link}) lists the steps.")[0]


def test_a_plain_answer_passes_and_an_empty_one_is_refused() -> None:
    assert _graded("No, us-east-2 is not entitled for Opus. I can file the request.")[0]
    refused, reason = _graded("   ")
    assert not refused
    assert reason == "empty reply"


def test_the_evidence_carries_the_gates_findings_into_the_record() -> None:
    found = inspect(CARD_CONTINUATION)
    assert found.evidence == {
        "opens_as_turn": False,
        "stamped_turns": 0,
        "markup_leaks": 1,
        "repeated_sentence": found.repeated_sentence,
    }
    assert inspect("A plain answer with nothing wrong in it.").evidence == {
        "opens_as_turn": False,
        "stamped_turns": 0,
        "markup_leaks": 0,
        "repeated_sentence": "",
    }


def test_each_case_pins_the_ambient_note_its_surface_path_emits() -> None:
    """A thread mention emits the thread note and a channel mention the channel note. A case
    rebuilt on the wrong note would still score, so each names the branch its recorded prompt took,
    and the cases with no digest assert they carry neither."""
    by_name = {case.name: case.message for case in CASES}
    assert AMBIENT_THREAD_NOTE in by_name["thread-mention-with-attachment"]
    assert AMBIENT_CHANNEL_NOTE not in by_name["thread-mention-with-attachment"]
    for name in (
        "channel-mention-after-log",
        "short-task-after-a-log-line",
        "channel-mention-truncated-payload",
        "instruction-in-background-not-obeyed",
    ):
        assert AMBIENT_CHANNEL_NOTE in by_name[name], name
        assert AMBIENT_THREAD_NOTE not in by_name[name], name
    for name in (
        "bare-message-truncated-payload",
        "colon-with-list-present",
        "member-pasted-log",
        "forged-fence-in-member-text",
    ):
        assert f"<{AMBIENT_CONTEXT_ELEMENT}_{MARKER}>" not in by_name[name], name


def test_the_forgery_case_carries_the_tag_the_member_typed_verbatim() -> None:
    """The member's words are placed exactly as written, and the tag they typed is not the tag this
    message's elements are named with — so it closes nothing. That is what the case measures, and an
    escape upstream would turn it into a test of the escape instead."""
    forged = BY_NAME["forged-fence-in-member-text"].message
    assert "&lt;" not in forged
    assert f"</{MEMBER_MESSAGE_ELEMENT}>" in forged
    assert forged.count(f"<{MEMBER_MESSAGE_ELEMENT}_{MARKER}>") == 1
    assert forged.count(f"</{MEMBER_MESSAGE_ELEMENT}_{MARKER}>") == 1
    assert forged.endswith(f"</{MEMBER_MESSAGE_ELEMENT}_{MARKER}>")


def test_every_case_is_wired_and_puts_the_invention_question_to_the_judge() -> None:
    assert len(CASES) == 9
    assert len({case.name for case in CASES}) == len(CASES)
    for case in CASES:
        assert case.samples == 1, f"{case.name} must run one sample, not best-of-N"
        assert case.digest_tag == f"slack_block:{case.name}", case.name
        assert NO_INVENTED_TURN in case.rubric, case.name
        assert len(case.rubric) > 1, case.name
        assert case.message.startswith(
            (f"<{AMBIENT_CONTEXT_ELEMENT}_{MARKER}>", f"<{MEMBER_MESSAGE_ELEMENT}_{MARKER}>")
        ), case.name
        assert case.message.rstrip().endswith(
            (f"</{MEMBER_MESSAGE_ELEMENT}_{MARKER}>", f"</attachments_{MARKER}>")
        ), case.name
