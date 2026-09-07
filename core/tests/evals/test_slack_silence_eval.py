"""The slack_silence suite grades one classifier decision per case, so the runner is asserted
against a stand-in decision leg that books its calls through the real workspace accounting: a case
that decides as owed, one that decides the other way, one whose provider raises, and the per-case
cost each one reads back off the ledger.

The suite's shape is pinned too, because a silence suite is trivially gamed in either direction: a
gate that never replies passes every silence case, and one that always replies passes every answer
case. Both halves have to be present, the answer cases have to carry no mention of the agent (what
makes them controls rather than restatements of the admission rule), the stop cases have to hold
the stop and nothing naming the agent after it, every case has to carry the thread history its
decision is only decidable from, and every case runs one decision."""

import asyncio
import json
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from evals.harness.harness import EvalReport
from evals.harness.judge import ModelJudge
from evals.suites.slack_silence import (
    ALEX,
    ARXIV_SENTENCE,
    BOT_USER_ID,
    CASES,
    MARSHALL,
    RECORDED_THREAD,
    SlackSilenceSuite,
    slack_silence_task,
)
from ufo.config import DEFAULT_AMBIENT_REPLY_MODEL
from ufo.db import workspace_tx
from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.models.interface import ModelRequest
from ufo.runtime.billing.accounting import record_workspace_usage
from ufo.runtime.turns.ambient_reply import (
    AMBIENT_HISTORY_MESSAGES,
    AmbientDecision,
    AmbientMessage,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Usage

BY_NAME = {case.name: case for case in CASES}
SILENT_TO_ANOTHER_MEMBER = (
    "point-up-at-another-member",
    "asking-a-teammate-what-they-think",
    "two-members-settling-a-time",
    "thanking-the-other-member",
)
SILENT_BETWEEN_MEMBERS = (
    "two-members-drawing-their-own-conclusion",
    "a-remark-between-members-on-the-agents-answer",
    "recorded-a-member-doubts-the-change-to-another",
    "recorded-a-member-names-the-vision-model",
)
SILENT_AFTER_A_STOP = (
    "question-the-agent-could-answer-after-a-stop",
    "correction-of-the-agents-work-after-a-stop",
    "sideways-talk-after-a-stop",
    "recorded-a-correction-after-stop-ufo",
    "recorded-a-question-to-a-member-after-stop-ufo",
    "recorded-the-braille-thing-after-stop-ufo",
)
SILENT_CASES = (*SILENT_TO_ANOTHER_MEMBER, *SILENT_BETWEEN_MEMBERS, *SILENT_AFTER_A_STOP)
ANSWER_CASES = (
    "unmentioned-challenge-to-the-agents-own-sentence",
    "unmentioned-followup-only-the-agent-can-answer",
    "member-answers-the-question-the-agent-asked",
    "member-calls-off-work-the-agent-committed-to",
    "a-decision-word-inside-a-message-for-the-agent",
    "a-mention-after-a-stop-lifts-it",
    "new-ask-after-the-agent-said-it-paused-a-stream",
    "recorded-no-implement-it-after-a-mention-lifted-the-stop",
)
STOPS = ("actually nevermind, drop it", "stop ufo")
DECISION_USAGE = Usage(input_tokens=609, output_tokens=6)
DECISION_MICRO_USD = CORE_PRICING.micro_usd(DEFAULT_AMBIENT_REPLY_MODEL, DECISION_USAGE)


@dataclass
class DecisionLeg:
    """The decision's provider leg, answering per case and booking one workspace ledger row per call
    through the same `record_workspace_usage` a metered background call bills through — which is
    where the suite reads each case's cost from. The case is read out of the payload it was handed,
    so a runner that sent the wrong thread finds no case at all."""

    answers: dict[str, AmbientDecision]
    workspace_id: UUID
    raises: str | None = None
    asked: list[str] = field(default_factory=list)
    model: str = DEFAULT_AMBIENT_REPLY_MODEL

    async def complete(self, request: ModelRequest) -> str:
        payload = str(request.messages[-1].content)
        self.asked.append(payload)
        document = payload.split("\n", 1)[1].rsplit("\n", 1)[0]
        asked_about = json.loads(document)["message"]["text"]
        case = next(case for case in CASES if case.message.text == asked_about)
        if self.raises == case.name:
            raise RuntimeError("provider is down")
        async with workspace_tx() as connection:
            await record_workspace_usage(connection, self.workspace_id, self.model, DECISION_USAGE)
        return self.answers[case.name]


@dataclass(frozen=True)
class StubTarget:
    judge: ModelJudge | None


async def _report(leg: DecisionLeg | None, workspace_id: UUID) -> EvalReport:
    target = StubTarget(judge=None if leg is None else ModelJudge(leg))
    with ws(workspace_id):
        return await SlackSilenceSuite(cases=CASES, digest=slack_silence_task(CASES).digest).run(
            target, asyncio.Semaphore(1)
        )


def _expected_answers() -> dict[str, AmbientDecision]:
    return {case.name: case.expected for case in CASES}


async def test_every_case_passes_on_the_decision_it_is_owed_and_carries_its_cost(db: None) -> None:
    """The pass path end to end: each case decided from its own thread and graded against what it is
    owed, with the per-decision spend read back off the `turn_id IS NULL` ledger row the call booked
    — the number the recorded turn costs are compared against."""
    workspace_id = uuid4()
    leg = DecisionLeg(answers=_expected_answers(), workspace_id=workspace_id)

    report = await _report(leg, workspace_id)

    assert report.passed
    assert [case.name for case in report.cases] == [case.name for case in CASES]
    assert len(leg.asked) == len(CASES)
    for result in report.cases:
        assert result.evidence["decision"] == BY_NAME[result.name].expected, result.name
        assert result.evidence["microUsd"] == DECISION_MICRO_USD, result.name
        assert result.evidence["promptTokens"] == DECISION_USAGE.input_tokens, result.name
        assert result.evidence["history"], result.name


async def test_a_wrong_decision_fails_its_case_and_names_what_was_owed(db: None) -> None:
    workspace_id = uuid4()
    answers = _expected_answers()
    answers["point-up-at-another-member"] = "REPLY"
    answers["member-calls-off-work-the-agent-committed-to"] = "NO_REPLY"

    report = await _report(DecisionLeg(answers=answers, workspace_id=workspace_id), workspace_id)

    assert not report.passed
    assert {case.name: case.reason for case in report.cases if not case.passed} == {
        "point-up-at-another-member": "decided REPLY where NO_REPLY was owed",
        "member-calls-off-work-the-agent-committed-to": "decided NO_REPLY where REPLY was owed",
    }
    assert report.pass_rate == pytest.approx((len(CASES) - 2) / len(CASES))


async def test_a_provider_failure_fails_the_case_it_broke_and_leaves_the_rest(db: None) -> None:
    """A raise is a failure, never a pass: an undecided case is not silently scored, and only the
    harness's own transient-fault set excludes one from scoring."""
    workspace_id = uuid4()
    leg = DecisionLeg(
        answers=_expected_answers(),
        workspace_id=workspace_id,
        raises="thanking-the-other-member",
    )

    report = await _report(leg, workspace_id)

    broken = next(case for case in report.cases if case.name == "thanking-the-other-member")
    assert not broken.passed and not broken.excluded
    assert broken.reason == "the decision raised: RuntimeError: provider is down"
    assert broken.evidence["decision"] is None
    assert broken.evidence["microUsd"] == 0
    assert all(case.passed for case in report.cases if case.name != broken.name)


async def test_the_suite_refuses_to_run_without_its_classifier_leg(db: None) -> None:
    with pytest.raises(RuntimeError, match="classifier model leg"):
        await _report(None, uuid4())


def test_the_thread_history_is_what_every_case_hands_the_decision() -> None:
    """A recorded failure is six characters and a mention of someone else, so a case with no history
    is a case about nothing. Each history is the thread as Slack holds it: the agent's own messages
    marked as its own, and human-to-human traffic left standing whether the agent answered it."""
    for case in CASES:
        assert case.history, case.name
        assert any(entry.own for entry in case.history), case.name
        assert all(entry.text and entry.speaker for entry in case.history), case.name
        assert case.message.speaker in (ALEX, MARSHALL), case.name
        assert not case.message.own, case.name
    assert not BY_NAME["two-members-settling-a-time"].history[-1].own


def test_the_suite_holds_both_halves_and_cannot_be_gamed_by_either() -> None:
    assert len(CASES) == 22
    assert {case.name for case in CASES} == set(SILENT_CASES) | set(ANSWER_CASES)
    assert len({case.name for case in CASES}) == len(CASES)
    assert len({case.message.text for case in CASES}) == len(CASES)
    assert {BY_NAME[name].expected for name in SILENT_CASES} == {"NO_REPLY"}
    assert {BY_NAME[name].expected for name in ANSWER_CASES} == {"REPLY"}


def test_only_the_recorded_shape_of_silence_names_another_member() -> None:
    """The first silent group is the recorded shape — a mention of a different member, asking the
    agent nothing. Every other case names nobody, which is what makes each a probe of its own rule:
    a gate that learned "a message naming somebody else is not for me" still has to answer the
    controls, and still has to stay out of an exchange between members that names no one."""
    for name in SILENT_TO_ANOTHER_MEMBER:
        text = BY_NAME[name].message.text
        assert f"<@{ALEX}>" in text or f"<@{MARSHALL}>" in text, name
        assert f"<@{BOT_USER_ID}>" not in text, name
    for name in (*SILENT_BETWEEN_MEMBERS, *SILENT_AFTER_A_STOP, *ANSWER_CASES):
        assert "<@" not in BY_NAME[name].message.text, name


def test_a_stop_holds_until_a_message_names_the_agent() -> None:
    """Each stop case carries the stop and the agent's own acknowledgement of it, then only traffic
    naming somebody else — a question the agent could answer, a correction of its work, sideways
    talk — so the stop is the one thing that can keep the decision silent. The control lifts the
    stop the only way it lifts: a later message naming the agent, which the agent answered, so the
    member's next reply is once again an answer to the agent's own question."""
    for name in SILENT_AFTER_A_STOP:
        history = BY_NAME[name].history
        stopped = next(index for index, entry in enumerate(history) if entry.text in STOPS)
        assert history[stopped + 1].own, name
        assert all(f"<@{BOT_USER_ID}>" not in entry.text for entry in history[stopped:]), name
    for name in (
        "a-mention-after-a-stop-lifts-it",
        "recorded-no-implement-it-after-a-mention-lifted-the-stop",
    ):
        lifted = BY_NAME[name].history
        stopped = next(index for index, entry in enumerate(lifted) if entry.text in STOPS)
        assert any(f"<@{BOT_USER_ID}>" in entry.text for entry in lifted[stopped:]), name
        assert lifted[-1].own, name


def test_only_a_members_stop_can_silence_the_thread() -> None:
    """The control for whose stop counts: the agent's own message reports that it paused a stream,
    and nothing a member wrote calls the agent off. So the new ask that follows is owed a reply, and
    a gate that read the agent's own account of its act as a stop would drop it."""
    control = BY_NAME["new-ask-after-the-agent-said-it-paused-a-stream"]
    assert control.expected == "REPLY"
    paused = next(entry for entry in control.history if entry.text.startswith("Paused the nightly"))
    assert paused.own
    assert all(entry.own or entry.text not in STOPS for entry in control.history)
    assert control.history.index(paused) < len(control.history) - 1
    assert all(
        f"<@{BOT_USER_ID}>" not in entry.text
        for entry in control.history[control.history.index(paused) :]
    )


def test_the_recorded_thread_is_read_whole_and_its_stop_sits_inside_the_window() -> None:
    """Every recorded case carries the whole thread before its message, so what the decision saw is
    what the suite hands it. The third after-stop case has the stop eleven messages back, at the
    edge of a twelve-message window and inside the twenty the surface reads."""
    thread = [message for _, message in RECORDED_THREAD]
    for name in (case.name for case in CASES if case.name.startswith("recorded-")):
        case = BY_NAME[name]
        position = thread.index(case.message)
        assert case.history == tuple(thread[:position]), name
    braille = BY_NAME["recorded-the-braille-thing-after-stop-ufo"]
    stopped = next(index for index, entry in enumerate(braille.history) if entry.text == "stop ufo")
    assert braille.history[stopped + 1].text == "Stopped."
    assert 11 <= len(braille.history) - stopped <= AMBIENT_HISTORY_MESSAGES


def test_the_recorded_threads_and_the_two_new_controls_are_the_ones_asked_for() -> None:
    """Each recorded failure keeps its own thread: two Sydecar answers behind the `:point_up_2:`,
    and the delivered arXiv plan behind "not bad wdyt" — whose own thread carries the positive
    control, so the case that must be answered sits directly after a case that must not be. A member
    calling the work off is a reply, because the team's answer to "nevermind" is a short
    affirmation; a decision word inside a message is that member's text and never the answer."""
    sydecar = BY_NAME["point-up-at-another-member"]
    assert ":point_up_2:" in sydecar.message.text
    assert any("sydecar" in entry.text for entry in sydecar.history)
    assert any("No Apple Pay" in entry.text for entry in sydecar.history)
    assert "not bad wdyt" in BY_NAME["asking-a-teammate-what-they-think"].message.text
    challenge = BY_NAME["unmentioned-challenge-to-the-agents-own-sentence"]
    assert "not bad wdyt" in challenge.history[-1].text
    assert "This doesn't seem right?" in challenge.message.text
    assert ARXIV_SENTENCE in challenge.message.text
    assert "self-evolving-coding-agents-in-practice.md" in challenge.history[1].text
    assert challenge.history[1].own
    cancel = BY_NAME["member-calls-off-work-the-agent-committed-to"]
    assert cancel.message.text == "actually nevermind, drop it"
    assert cancel.history[-1].own
    assert "NO_REPLY" in BY_NAME["a-decision-word-inside-a-message-for-the-agent"].message.text


def test_the_task_pins_the_model_and_the_prompt_a_passing_run_was_measured_on() -> None:
    """A report is comparable only against a run of the identical suite, and here the prompt
    revision and the classifier model are part of it: a reworded case or a repointed model moves the
    digest rather than quietly changing what a passing run means."""
    task = slack_silence_task(CASES)
    assert task.name == "slack_silence" and task.suite == "slack_silence"
    assert task.cases == tuple(case.name for case in CASES)
    assert task.judge_model == DEFAULT_AMBIENT_REPLY_MODEL
    assert task.exclusive
    reworded = tuple(
        replace(case, message=AmbientMessage(speaker=case.message.speaker, text="thanks!"))
        if case.name == "thanking-the-other-member"
        else case
        for case in CASES
    )
    assert slack_silence_task(reworded).digest != task.digest


async def test_a_decision_is_billed_to_the_workspace_with_no_turn_behind_it(db: None) -> None:
    """What the cost figure rests on: the classifier's call books a `tokens` row against the
    workspace with `turn_id` NULL, because the whole point of deciding pre-admission is that no
    turn exists to bill it to."""
    workspace_id = uuid4()

    await _report(DecisionLeg(answers=_expected_answers(), workspace_id=workspace_id), workspace_id)

    with ws(workspace_id):
        async with workspace_tx() as connection:
            booked = (
                await connection.execute(
                    sa.select(
                        sa.func.count(),
                        sa.func.sum(tables.ledger.c.priced_micro_usd),
                    ).where(
                        tables.ledger.c.workspace_id == workspace_id,
                        tables.ledger.c.turn_id.is_(None),
                    )
                )
            ).one()
    assert tuple(booked) == (len(CASES), DECISION_MICRO_USD * len(CASES))
