"""The voice the member reads. A delivery is not the app's message: the app hands its text to the
agent the member already talks to, and that agent's reply is the whole thing they ever see.

`deliver` invokes one turn in the member's own conversation carrying the app's text walled as data
and one line of instruction after it. This suite is that inbound, sent to the main agent — the
harness admits no speakerless turn, so it arrives as a member message standing in for the relay
admission does in production. What the case grades is what the relay says back.

Three things make a delivered message worth the interruption. It says what happened and what it
means for this member, in the agent's own voice rather than as a forwarded report. It names the
thing it is about, so they can go and look. And it offers the one thing the agent could do about it,
because the message lands in the thread they answer in, so `yes` is the cheapest way for them to
act. Against those stands the restraint half: the relay acts on nothing before they answer, and it
never mentions the app, the queue, or that anything was notified — a member reads a colleague's
message, not a system's.

The deterministic grader holds the restraint half and the rubric holds the content, so a reply that
does the work anyway fails before a judge ever reads it."""

import re

from ufo_ext_app_notification.deliver import RELAY_INSTRUCTION, RELAY_SOURCE

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from ufo.harness.untrusted import wall

MENTIONS_THE_APP = re.compile(r"\bnotif|\bqueue|\binbox\b", re.IGNORECASE)

CHURN_AND_DEPLOY_AND_INVESTOR = (
    "Six subscriptions canceled this week against a four-week baseline of one, and the two "
    "largest, Northwind Robotics and Marrow & Finch, are $3,500 of the $6,270 in monthly revenue "
    "that left. The deploy of main for the billing rework failed its readiness probe and rolled "
    "back, so production is serving the previous release. Priya at Corvid Capital asked on August "
    "31 for the data room and the August metrics before Thursday's investment committee and the "
    "thread is still unanswered."
)
OUTAGE = (
    "Production has returned 502 on every request since the 14:07 rollout of 8f21c0d. The rollback "
    "job failed as well, so no release is serving."
)
CUSTOMER_TURNING = (
    "The CFO at Marrow & Finch wrote on September 2 that this is the third export failure this "
    "month and nobody has answered their ticket. They are evaluating alternatives before the "
    "renewal on the 15th."
)


def _relayed(text: str) -> str:
    """The inbound `deliver` founds, built from the code so the suite moves when the wording it
    measures does."""
    return wall(RELAY_SOURCE, text) + RELAY_INSTRUCTION


def _relays_without_acting() -> Grader:
    """The restraint half, read off the trajectory and the words: the relay answers and stops, and
    what it says never names the machinery that carried it."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.calls:
            return CapabilityVerdict(
                False, f"acted before the member answered: {', '.join(output.tools)}"
            )
        said = MENTIONS_THE_APP.search(output.response)
        if said is not None:
            return CapabilityVerdict(False, f"named the machinery: {said.group(0)!r}")
        return CapabilityVerdict(True, "said it and stopped")

    return DescribedGrader("relays the message without acting on it or naming the app", grade)


CASES = (
    CapabilityCase(
        name="three-things-at-once",
        message=_relayed(CHURN_AND_DEPLOY_AND_INVESTOR),
        grader=_relays_without_acting(),
        rubric=(
            "The reply states all three facts: the six cancellations, the rolled-back billing "
            "deploy, and the unanswered thread from Priya at Corvid Capital before Thursday.",
            "The reply says what at least one of them means for this member rather than only "
            "restating it.",
            "The reply offers something the agent could do next about at least one of them, and "
            "leaves the choice to the member.",
            "The reply reads as the member's own assistant telling them something, not as a "
            "forwarded report or a summary of a message from somewhere else.",
        ),
        digest_tag="notify-relay:three-things",
    ),
    CapabilityCase(
        name="production-is-down",
        message=_relayed(OUTAGE),
        grader=_relays_without_acting(),
        rubric=(
            "The reply says production is returning 502 and that the rollback failed, so no "
            "release is serving.",
            "The reply names the rollout it is about, 8f21c0d, or the time it started.",
            "The reply offers to look into it or to do something about it, and does not claim to "
            "have already done so.",
        ),
        digest_tag="notify-relay:outage",
    ),
    CapabilityCase(
        name="a-customer-is-leaving",
        message=_relayed(CUSTOMER_TURNING),
        grader=_relays_without_acting(),
        rubric=(
            "The reply names Marrow & Finch and the renewal on the 15th.",
            "The reply says why it matters now: the account is evaluating alternatives with the "
            "renewal close.",
            "The reply offers to draft the reply to them or to take some next step, and leaves "
            "the decision to the member.",
        ),
        digest_tag="notify-relay:customer-turning",
    ),
)
