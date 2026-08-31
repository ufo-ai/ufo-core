"""Ambient reply cases: which un-addressed thread message earns a turn, decided before one exists.

Once a mention has made a Slack thread the agent's conversation, every later member reply is ambient
traffic — including the ones that are two people talking to each other. Two recorded turns spent
$0.688780 and $0.925565 to say "Nothing further from me on that one." and "Standing by if Marshall
has questions on it."; both messages @-mentioned another member and asked the agent for nothing. So
the suite grades the pre-admission decision itself: one classifier call per case on the deploy's
ambient reply model, whose whole output is REPLY or NO_REPLY, and whose cost is read back off the
workspace ledger row that call books.

The discrimination is "is this asking something of me", never "does it @-mention me", so the suite
is built in opposing pairs and cannot be passed by learning either half. Four cases must be silent:
the two recorded failures, two members settling logistics between themselves, and a member thanking
another member. Five must be answered and none of them mentions the agent at all: the real positive
control from the same thread as the second failure — a member challenging a sentence in the agent's
own artifact — a follow-up only the agent can answer, a member answering a question the agent asked,
a member calling off work the agent committed to (which is a reply, never silence: the team's answer
to "nevermind" is a short affirmation), and a message writing a decision word of its own, which is
that member's text and never the answer.

Every case carries the thread as the surface reads it: the recent messages oldest first, each with
the Slack id that spoke it and whether the agent itself spoke it. Both recorded failures are only
decidable from that — `<@U0BBYEHCT8F> :point_up_2:` is six characters and a mention of someone else.
A message an earlier decision dropped still stands in the thread, because the surface reads the
thread from Slack rather than from the turns it founded, so the histories here hold human-to-human
traffic with no agent message after it.

Every grader is deterministic and every case runs one decision. Silence is not a judgement to put to
a model, and best-of-N would report the behavior safe on the evidence that it usually is — in both
directions, since one filler reply in three is the bug and one silent turn in three is the
regression."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import sqlalchemy as sa

from evals.harness.harness import (
    EvalCaseResult,
    EvalReport,
    JsonObject,
    digest_payload,
    is_transient_fault,
)
from evals.harness.judge import ModelJudge
from evals.harness.registry import EvalTask
from evals.harness.target import CapabilityTarget
from ufo.config import DEFAULT_AMBIENT_REPLY_MODEL
from ufo.db import workspace_tx
from ufo.runtime.turns.ambient_reply import (
    AMBIENT_HISTORY_MESSAGES,
    AMBIENT_REPLY_MAX_TOKENS,
    AMBIENT_REPLY_REASONING,
    AMBIENT_REPLY_REVISION,
    NO_REPLY,
    REPLY,
    AmbientDecision,
    AmbientMessage,
    AmbientReplyClassifier,
)
from ufo.schema import tables

BOT_USER_ID = "U0BG8632NDS"
ALEX = "U0BBYEHCT8F"
MARSHALL = "U0BCAD5QP7X"


def _member(speaker: str, text: str) -> AmbientMessage:
    return AmbientMessage(speaker=speaker, text=text)


def _agent(text: str) -> AmbientMessage:
    return AmbientMessage(speaker=BOT_USER_ID, text=text, own=True)


SYDECAR_RECALL = (
    "From the Zach Washer thread you forwarded, saved as sydecar-zach-washer-email.md: Sydecar "
    "runs the SPV as a series of their master LLC, they handle formation, the deal bank account "
    "and the LP subscription docs, the fee is a flat per-deal setup plus an annual admin charge, "
    "LPs are onboarded through their own portal with KYC on their side, and the close is gated on "
    "cleared funds rather than signed docs."
)
SYDECAR_PAYMENTS = (
    "No Apple Pay, and no cards. Zach's reply in sydecar-zach-washer-email.md is explicit: LPs "
    "sign the subscription docs digitally and then wire USD to the deal account, and Sydecar "
    "reconciles the wire before it lets the deal close."
)
PRICING_SQUAD_ANSWER = (
    "Four people, per pricing-squad-roster.md: two engineers, a designer, and a product manager."
)
FEED_EMPTY_ANSWER = (
    "star_city returned zero rows in three seconds, per vendor-feed-run-2026-08-09.md. The other "
    "four vendors returned between 15k and 181k rows and took two to four minutes each, so it is "
    "that vendor rather than the run."
)
VENDOR_RECONCILE_QUESTION = (
    "Two count sources in vendor-feed-counts-vs-invoices.md disagree on harbor_point and "
    "granite_bay: do you want the invoice totals reconciled against the staging warehouse or "
    "against last night's production snapshot?"
)
VENDOR_RECONCILE_STARTED = (
    "Reconciling against last night's production snapshot now — I will reshare "
    "vendor-feed-counts-vs-invoices.md with the four vendors matched."
)
ARXIV_SENTENCE = (
    "Environmental feedback is the survey's distinctive coding-agent signal, and we throw ours "
    "away."
)
ARXIV_ANSWER = (
    "Of the survey's three loops, the one reading the environment's response is where we already "
    "generate the signal and drop it, so persisting each test run's outcome against the files it "
    "exercised is the cheapest thing to build first. The plan is in "
    "self-evolving-coding-agents-in-practice.md."
)


@dataclass(frozen=True)
class SilenceCase:
    """One un-addressed thread message and the thread it arrives in, with the decision it is owed.
    `history` is oldest first and marks the agent's own messages as its own, which is the whole
    evidence the decision has: who spoke last, whether the agent already answered this, and whether
    the message contradicts something the agent produced."""

    name: str
    message: AmbientMessage
    history: tuple[AmbientMessage, ...]
    expected: AmbientDecision

    @property
    def grading(self) -> str:
        return f"the decision on this message is {self.expected}"

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "speaker": self.message.speaker,
            "message": self.message.text,
            "expected": self.expected,
            "history": [
                {"speaker": entry.speaker, "own": entry.own, "text": entry.text}
                for entry in self.history
            ],
        }


CASES = (
    SilenceCase(
        "point-up-at-another-member",
        _member(MARSHALL, f"<@{ALEX}> :point_up_2:"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> recall the details about sydecar that I gave you from email "
                "with Zach",
            ),
            _agent(SYDECAR_RECALL),
            _member(MARSHALL, "Answer the question about the types of payment supported"),
            _agent(SYDECAR_PAYMENTS),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "asking-a-teammate-what-they-think",
        _member(ALEX, f"<@{MARSHALL}> not bad wdyt"),
        (
            _member(
                ALEX,
                f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ),
            _agent(ARXIV_ANSWER),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "two-members-settling-a-time",
        _member(
            MARSHALL,
            f"<@{ALEX}> thursday 3 works, i'll move the invite and drop the doc in here beforehand",
        ),
        (
            _member(MARSHALL, f"<@{BOT_USER_ID}> who is on the pricing squad right now?"),
            _agent(PRICING_SQUAD_ANSWER),
            _member(
                ALEX,
                f"<@{MARSHALL}> can we push the pricing review to thursday 3pm, i'm out wednesday",
            ),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "thanking-the-other-member",
        _member(MARSHALL, f"<@{ALEX}> thanks for chasing that down, saved me the morning"),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(
                ALEX, "i re-ran star_city by hand and it returned 31k rows, so it was their end"
            ),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "unmentioned-challenge-to-the-agents-own-sentence",
        _member(MARSHALL, f"\u201c{ARXIV_SENTENCE}\u201d This doesn't seem right?"),
        (
            _member(
                ALEX,
                f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ),
            _agent(ARXIV_ANSWER),
            _member(ALEX, f"<@{MARSHALL}> not bad wdyt"),
        ),
        REPLY,
    ),
    SilenceCase(
        "unmentioned-followup-only-the-agent-can-answer",
        _member(MARSHALL, "how many rows did the other four come back with?"),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(ALEX, "that's the third night running for star_city"),
        ),
        REPLY,
    ),
    SilenceCase(
        "member-answers-the-question-the-agent-asked",
        _member(MARSHALL, "the production snapshot"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            ),
            _agent(VENDOR_RECONCILE_QUESTION),
        ),
        REPLY,
    ),
    SilenceCase(
        "member-calls-off-work-the-agent-committed-to",
        _member(MARSHALL, "actually nevermind, drop it"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            ),
            _agent(VENDOR_RECONCILE_QUESTION),
            _member(MARSHALL, "the production snapshot"),
            _agent(VENDOR_RECONCILE_STARTED),
        ),
        REPLY,
    ),
    SilenceCase(
        "a-decision-word-inside-a-message-for-the-agent",
        _member(
            MARSHALL,
            "the ingest log has NO_REPLY against my last two messages here — which vendor did you "
            "say came back empty?",
        ),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(ALEX, "that's the third night running for star_city"),
        ),
        REPLY,
    ),
)


@dataclass(frozen=True)
class DecisionSpend:
    """What one decision cost, read off the workspace ledger rows its own model call booked —
    `turn_id` NULL, because the whole point is that no turn exists to bill it to. This is the number
    the recorded turn costs are compared against."""

    micro_usd: int
    prompt_tokens: int
    total_tokens: int
    cache_read_tokens: int


def slack_silence_task(cases: tuple[SilenceCase, ...]) -> EvalTask:
    """The suite's model leg is the classifier itself, so a report's judge model records which model
    decided. It is pinned to the deploy default rather than to a suite-local constant: what this
    measures is the model ambient traffic is actually gated on."""
    digest = digest_payload(
        {
            "runner": "ambient-reply-decision",
            "task": "slack_silence",
            "prompt": AMBIENT_REPLY_REVISION,
            "historyMessages": AMBIENT_HISTORY_MESSAGES,
            "model": DEFAULT_AMBIENT_REPLY_MODEL,
            "cases": [case.payload() for case in cases],
        }
    )
    suite = SlackSilenceSuite(cases=cases, digest=digest)
    return EvalTask(
        "slack_silence",
        "slack_silence",
        digest,
        tuple(case.name for case in cases),
        suite.run,
        judge_model=DEFAULT_AMBIENT_REPLY_MODEL,
        judge_max_tokens=AMBIENT_REPLY_MAX_TOKENS,
        judge_reasoning=AMBIENT_REPLY_REASONING,
        exclusive=True,
    )


@dataclass(frozen=True)
class SlackSilenceSuite:
    cases: tuple[SilenceCase, ...]
    digest: str

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        """One decision at a time, and the task runs exclusive, so the ledger rows that appear
        around a decision are that decision's own."""
        leg = cast("ModelJudge | None", target.judge)
        if leg is None:
            raise RuntimeError("the slack_silence suite requires its classifier model leg")
        classifier = AmbientReplyClassifier(model=leg.model)
        results: tuple[EvalCaseResult, ...] = ()
        for case in self.cases:
            async with slots:
                results += (await self._case(case, classifier),)
        return EvalReport(
            name="slack_silence", suite="slack_silence", digest=self.digest, cases=results
        )

    async def _case(self, case: SilenceCase, classifier: AmbientReplyClassifier) -> EvalCaseResult:
        booked = await self._booked_calls()
        try:
            decision = await classifier.decide(case.message, case.history)
        except Exception as error:
            fault = type(error).__name__
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"the decision raised: {fault}: {error}",
                evidence=self._evidence(case, None, await self._spend_since(booked)),
                excluded=is_transient_fault(fault),
            )
        spend = await self._spend_since(booked)
        passed = decision == case.expected
        reason = (
            f"decided {decision} on {len(case.history)} messages of history"
            if passed
            else f"decided {decision} where {case.expected} was owed"
        )
        return EvalCaseResult(
            name=case.name,
            passed=passed,
            reason=reason,
            evidence=self._evidence(case, decision, spend),
        )

    async def _booked_calls(self) -> frozenset[UUID]:
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.ledger.c.id).where(tables.ledger.c.turn_id.is_(None))
            )
        return frozenset(rows.scalars())

    async def _spend_since(self, booked: frozenset[UUID]) -> DecisionSpend:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.id,
                        tables.ledger.c.amount,
                        tables.ledger.c.prompt_tokens,
                        tables.ledger.c.cache_read_tokens,
                        tables.ledger.c.priced_micro_usd,
                    ).where(tables.ledger.c.turn_id.is_(None))
                )
            ).all()
        fresh = tuple(row for row in rows if row.id not in booked)
        return DecisionSpend(
            micro_usd=sum(row.priced_micro_usd for row in fresh),
            prompt_tokens=sum(row.prompt_tokens for row in fresh),
            total_tokens=sum(row.amount for row in fresh),
            cache_read_tokens=sum(row.cache_read_tokens for row in fresh),
        )

    def _evidence(
        self, case: SilenceCase, decision: AmbientDecision | None, spend: DecisionSpend
    ) -> JsonObject:
        return {
            "grading": case.grading,
            "speaker": case.message.speaker,
            "message": case.message.text,
            "history": [
                f"{'agent' if entry.own else entry.speaker}: {entry.text}" for entry in case.history
            ],
            "expected": case.expected,
            "decision": decision,
            "microUsd": spend.micro_usd,
            "promptTokens": spend.prompt_tokens,
            "totalTokens": spend.total_tokens,
            "cacheReadTokens": spend.cache_read_tokens,
        }
