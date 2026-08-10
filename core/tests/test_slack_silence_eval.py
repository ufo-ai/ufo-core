"""The slack_silence graders decide their cases alone — the suite has no judge — so both are
asserted directly against replies, including the two recorded production fillers the suite exists to
catch and the sentinel forms the model actually produces.

The suite's shape is pinned too, because a silence suite is trivially gamed in either direction: an
agent that never replies passes every silence case, and one that always replies passes every answer
case. Both halves have to be present, the answer cases have to carry no mention of the agent (that
is what makes them controls rather than restatements of the admission rule), and every case has to
run one sample. So is the grounding of the seeded threads: a seeded answer asserting what no seeded
file carries turns the case into a question about unsourced claims, which is how the Sydecar case
failed its first live run."""

import asyncio

from evals.harness.capability import CapabilityOutput
from evals.slack_silence import (
    ALEX,
    BOT_USER_ID,
    CASES,
    MARKER,
    MARSHALL,
    answered_scorer,
    silent_scorer,
)
from ufo.ext.surface import MEMBER_MESSAGE_ELEMENT, SILENCE_SENTINEL

BY_NAME = {case.name: case for case in CASES}
SILENT_CASES = (
    "point-up-at-another-member",
    "asking-a-teammate-what-they-think",
    "two-members-settling-a-time",
    "thanking-the-other-member",
)
ANSWER_CASES = (
    "unmentioned-challenge-to-the-agents-own-sentence",
    "unmentioned-followup-only-the-agent-can-answer",
    "member-answers-the-question-the-agent-asked",
)
CORRECTION = (
    "You're right and the sentence was wrong. Environmental feedback is not what the survey "
    "singles out for coding agents, and we do keep the tool results within the turn — what we "
    "drop is the outcome across turns. I have corrected that section and reshared the plan."
)


def _silent(reply: str) -> tuple[bool, str]:
    verdict = asyncio.run(silent_scorer().grade(CapabilityOutput(response=reply, calls=())))
    return verdict.passed, verdict.reason


def _answered(reply: str, min_words: int = 15) -> tuple[bool, str]:
    verdict = asyncio.run(
        answered_scorer(min_words).grade(CapabilityOutput(response=reply, calls=()))
    )
    return verdict.passed, verdict.reason


def test_both_recorded_fillers_fail_the_silence_gate() -> None:
    for reply in (
        "Nothing further from me on that one.",
        "Standing by if Marshall has questions on it.",
    ):
        passed, reason = _silent(reply)
        assert not passed, f"{reply!r} passed: {reason}"


def test_the_silence_gate_takes_every_form_the_model_writes_the_token_in() -> None:
    for reply in (SILENCE_SENTINEL, f"\n{SILENCE_SENTINEL}\n", "<response/>", "<response />"):
        passed, reason = _silent(reply)
        assert passed, f"{reply!r} failed: {reason}"
    for reply in (
        "",
        f"On it. {SILENCE_SENTINEL}",
        f"{SILENCE_SENTINEL} \u2014 nothing for me here",
    ):
        assert not _silent(reply)[0], reply


def test_the_answer_gate_refuses_silence_and_takes_a_real_answer() -> None:
    assert _answered(CORRECTION, min_words=20)[0]
    for reply in (SILENCE_SENTINEL, "<response/>", f"{SILENCE_SENTINEL}", ""):
        assert not _answered(reply)[0], reply
    hedged, reason = _answered(f"Happy to help. {SILENCE_SENTINEL}", min_words=2)
    assert not hedged
    assert "carries the silence sentinel" in reason
    short, reason = _answered("Noted.", min_words=15)
    assert not short
    assert "under the 15 floor" in reason


def test_the_word_floor_is_the_register_the_case_earns() -> None:
    """An acknowledgement of a member's answer is a short sentence and must still pass; the floor is
    per case so a low one never excuses a silent reply on a case that owes an explanation."""
    acknowledgement = "Got it \u2014 reconciling against the production snapshot."
    assert _answered(acknowledgement, min_words=5)[0]
    assert not _answered(acknowledgement, min_words=20)[0]


def test_a_case_whose_answer_is_numbers_is_graded_on_the_numbers() -> None:
    """The recorded answer to the row-count follow-up is nine words, so a floor tall enough to
    exclude filler on its own would exclude the right answer too. What it must carry does the work
    instead: filler passes no substring check."""
    counts = "harbor_point 181,402, lakeshore 96,318, granite_bay 41,905, and tidewater 15,247."
    carried = ("181,402", "96,318", "41,905", "15,247")
    verdict = asyncio.run(
        answered_scorer(6, carried).grade(CapabilityOutput(response=counts, calls=()))
    )
    assert verdict.passed, verdict.reason
    for reply in ("Nothing further from me on that one.", "harbor_point 181,402 and the rest."):
        refused = asyncio.run(
            answered_scorer(6, carried).grade(CapabilityOutput(response=reply, calls=()))
        )
        assert not refused.passed
        assert "answers without" in refused.reason


def test_the_suite_holds_both_halves_and_cannot_be_gamed_by_either() -> None:
    assert len(CASES) == 7
    assert len({case.name for case in CASES}) == len(CASES)
    assert tuple(name for name in SILENT_CASES if name in BY_NAME) == SILENT_CASES
    assert tuple(name for name in ANSWER_CASES if name in BY_NAME) == ANSWER_CASES
    for case in CASES:
        assert case.samples == 1, f"{case.name} must run one sample, not best-of-N"
        assert case.digest_tag == f"silence:{case.name}", case.name
        assert not case.rubric and not case.artifact_rubric, case.name
        assert case.message.startswith(f"<{MEMBER_MESSAGE_ELEMENT}_{MARKER}>"), case.name
        assert case.message.endswith(f"</{MEMBER_MESSAGE_ELEMENT}_{MARKER}>"), case.name
        assert case.prior_messages, case.name


def test_the_silence_cases_address_another_member_and_the_controls_address_nobody() -> None:
    """The negative cases are the recorded shape — a mention of a different member, asking the agent
    nothing. The controls carry no mention at all, which is what makes them controls: an agent that
    learned "a message naming somebody else is not for me" still has to answer these."""
    for name in SILENT_CASES:
        message = BY_NAME[name].message
        assert f"<@{ALEX}>" in message or f"<@{MARSHALL}>" in message, name
        assert f"<@{BOT_USER_ID}>" not in message, name
    for name in ANSWER_CASES:
        message = BY_NAME[name].message
        assert "<@" not in message, name


def test_the_seeded_threads_are_the_recorded_ones() -> None:
    """Each recorded failure keeps its own thread: two Sydecar answers behind the `:point_up_2:`,
    and the delivered arXiv plan behind "not bad wdyt" — whose own thread carries the positive
    control, so the case that must be answered sits directly after a case that must not be."""
    sydecar = BY_NAME["point-up-at-another-member"]
    assert ":point_up_2:" in sydecar.message
    assert any("sydecar" in message for message in sydecar.prior_messages)
    assert any("No Apple Pay" in message for message in sydecar.prior_messages)
    arxiv = BY_NAME["asking-a-teammate-what-they-think"]
    assert "not bad wdyt" in arxiv.message
    challenge = BY_NAME["unmentioned-challenge-to-the-agents-own-sentence"]
    assert "not bad wdyt" in challenge.prior_messages[2]
    assert challenge.prior_messages[3] == SILENCE_SENTINEL
    assert "This doesn't seem right?" in challenge.message
    quoted = next(
        file for file in challenge.workspace_files if file.path.endswith("in-practice.md")
    )
    assert (
        b"Environmental feedback is the survey's distinctive coding-agent signal" in quoted.content
    )


def test_every_seeded_answer_cites_a_file_the_case_seeds() -> None:
    """A seeded assistant turn that recalls specifics from nowhere invites the live turn to check
    and retract them, which is a reply on a case that must produce none."""
    for case in CASES:
        for answer in case.prior_messages[1::2]:
            if answer == SILENCE_SENTINEL:
                continue
            assert any(file.path in answer for file in case.workspace_files), (
                f"{case.name}: {answer!r} cites no seeded file"
            )
    sydecar = BY_NAME["point-up-at-another-member"]
    email = next(file for file in sydecar.workspace_files if "sydecar" in file.path)
    assert b"No Apple Pay" in email.content
    assert b"cleared funds" in email.content
