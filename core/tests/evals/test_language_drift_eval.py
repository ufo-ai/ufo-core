from evals.harness.capability import CapabilityOutput
from evals.suites.language_drift import (
    CASES,
    DEGENERATE_LOOP,
    english_reply,
    mandarin_content_reply,
)

ENGLISH = english_reply()
MANDARIN_CONTENT = mandarin_content_reply()
ENGLISH_ANSWER = "Seven rows are still unsent, so the run stops at row 29 of 36 for now."


def _output(response: str) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=())


async def test_english_reply_passes_an_english_answer() -> None:
    assert (await ENGLISH(_output(ENGLISH_ANSWER))).passed


async def test_english_reply_fails_the_observed_degenerate_loop() -> None:
    verdict = await ENGLISH(_output(DEGENERATE_LOOP))
    assert not verdict.passed
    assert "Han characters" in verdict.reason


async def test_english_reply_fails_one_mandarin_clause_inside_english() -> None:
    verdict = await ENGLISH(_output(f"{ENGLISH_ANSWER} 还剩七行。"))
    assert not verdict.passed
    assert verdict.evidence["han_characters"] == 4


async def test_english_reply_admits_latin_punctuation_and_digits() -> None:
    assert (await ENGLISH(_output("Rows 30-36 remain — 7 of 36, per the sheet."))).passed


async def test_mandarin_content_reply_passes_a_translation_in_an_english_frame() -> None:
    verdict = await MANDARIN_CONTENT(
        _output('Here is that line in Mandarin, ready to paste: "定时任务已删除，不会再发邮件。"')
    )
    assert verdict.passed, verdict.reason


async def test_mandarin_content_reply_fails_an_english_only_reply() -> None:
    verdict = await MANDARIN_CONTENT(_output("I can't write that in Mandarin."))
    assert not verdict.passed
    assert verdict.reason == "no Mandarin in the reply"


async def test_mandarin_content_reply_passes_a_bare_translation() -> None:
    verdict = await MANDARIN_CONTENT(_output("定时任务已删除，不会再发邮件。"))
    assert verdict.passed, verdict.reason
    assert verdict.evidence["han_characters"] == 13


def test_every_seeded_transcript_ends_on_the_injected_mandarin() -> None:
    seeded = [case for case in CASES if case.prior_messages and case.name.startswith("drift-")]
    assert len(seeded) == 3
    for case in seeded:
        transcript = (
            (*case.prior_messages, case.undelivered[0].result)
            if case.undelivered
            else case.prior_messages
        )
        assert transcript[-1] == DEGENERATE_LOOP, case.name


def test_the_repetition_case_seeds_an_assistant_turn_last() -> None:
    case = next(case for case in CASES if case.name == "drift-under-repeated-fires")
    assert len(case.prior_messages) % 2 == 0
    assert case.prior_messages.count(case.message) == len(case.prior_messages) // 2
